"""Account-scoped calendar entries and the home page's saved learning activity."""

from datetime import UTC, date, datetime, time
from uuid import uuid4

from .domain import DomainConflict, DomainNotFound, DomainService
from .schemas import CalendarEntryCreate
from .storage import Store


def _courses(store: Store, user_id: str) -> dict:
    return {
        course["course_id"]: course
        for course in store.scan("course", {"user_id": user_id})
        if course.get("status") not in {"deleted", "purged"}
    }


def _entries(store: Store, user_id: str, courses: dict) -> list[dict]:
    items = []
    for entry in store.scan("calendar_entry", {"user_id": user_id}):
        course_id = entry["course_id"]
        if course_id and course_id not in courses:
            continue
        items.append({**entry, "course_name": courses[course_id]["name"] if course_id else None})
    return sorted(
        items,
        key=lambda row: (
            row["scheduled_on"] or "9999-12-31",
            row["start_time"] or "",
            row["created_at"],
        ),
    )


def _upcoming(entries: list[dict], today: date, at: time) -> list[dict]:
    day = today.isoformat()
    clock = at.replace(tzinfo=None)
    return [
        item
        for item in entries
        if item["scheduled_on"]
        and not item["completed_at"]
        and (
            item["scheduled_on"] > day
            or (
                item["scheduled_on"] == day
                and (item["all_day"] or time.fromisoformat(item["end_time"]) > clock)
            )
        )
    ]


def read_calendar(store: Store, user_id: str, start: date, end: date, today: date, at: time):
    if not 0 < (end - start).days <= 62:
        raise ValueError("日历查询范围须为 1 至 62 天")
    entries = _entries(store, user_id, _courses(store, user_id))
    upcoming = _upcoming(entries, today, at)
    return {
        "items": [
            item
            for item in entries
            if item["scheduled_on"] and start.isoformat() <= item["scheduled_on"] < end.isoformat()
        ],
        "upcoming": upcoming[:4],
        "next_exam": next((item for item in upcoming if item["kind"] == "exam"), None),
    }


def create_calendar_entry(store: Store, user_id: str, request: CalendarEntryCreate):
    course = None
    if request.course_id:
        course = DomainService(store, user_id).course(request.course_id, writable=True)
    entry_id = f"calendar-{uuid4().hex}"
    now = datetime.now(UTC).isoformat()
    item = {
        **request.model_dump(mode="json"),
        "entry_id": entry_id,
        "user_id": user_id,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    store.put("calendar_entry", entry_id, item)
    return {**item, "course_name": course["name"] if course else None}


def set_calendar_completion(store: Store, user_id: str, entry_id: str, completed: bool):
    domain = DomainService(store, user_id)
    with domain._transaction():
        read = getattr(store, "get_for_update", store.get)
        item = read("calendar_entry", entry_id)
        if item is None or item["user_id"] != user_id:
            raise DomainNotFound(entry_id)
        if item["kind"] != "todo":
            raise DomainConflict("只有待办可以标记完成")
        course = None
        if item["course_id"]:
            course = domain.course(item["course_id"], writable=True)
        if bool(item["completed_at"]) != completed:
            now = datetime.now(UTC).isoformat()
            item.update(completed_at=now if completed else None, updated_at=now)
            store.put("calendar_entry", entry_id, item)
    return {**item, "course_name": course["name"] if course else None}


def _stamp(row: dict) -> str:
    return row.get("updated_at") or row.get("created_at") or ""


def _activity(store: Store, user_id: str, courses: dict):
    owner = {"user_id": user_id}
    domain = DomainService(store, user_id)
    notes, saved_tests, running_tests = [], [], []
    course_activity = {}
    quiz_titles = {}
    for asset in store.scan("learning_asset", owner):
        course_id = asset["course_id"]
        if course_id not in courses or asset.get("status") in {"archived", "deleted", "purged"}:
            continue
        asset_type = asset.get("asset_type")
        if asset_type not in {"note", "quiz"}:
            continue
        revision_id = asset.get("latest_revision_id")
        if not revision_id and asset_type == "note":
            try:
                revision_id = domain._latest_note(asset)["revision_id"]
            except DomainNotFound:
                continue
        if not revision_id:
            continue
        entry = {
            "course_id": course_id,
            "course_name": courses[course_id]["name"],
            "asset_id": asset["asset_id"],
            "revision_id": revision_id,
            "title": asset["title"],
            "updated_at": _stamp(asset),
        }
        if asset_type == "note":
            revision = store.get("asset_revision", revision_id)
            if not revision or revision.get("user_id") != user_id:
                continue
            entry["title"] = revision["title"]
            notes.append(entry)
        else:
            # Older generic quiz assets do not have a saved paper to open.
            revision = store.get("quiz_revision_payload", revision_id)
            if not revision or revision.get("user_id") != user_id:
                continue
            quiz_titles[revision_id] = revision["title"]
            saved_tests.append({**entry, "kind": "paper", "title": revision["title"]})
        course_activity[course_id] = max(course_activity.get(course_id, ""), entry["updated_at"])

    now = datetime.now(UTC)
    for attempt in store.scan("attempt", owner):
        course_id = attempt["course_id"]
        revision_id = attempt.get("quiz_revision_id")
        if course_id not in courses or not revision_id:
            continue
        status = attempt.get("status")
        deadline = attempt.get("deadline_at")
        if status == "in_progress" and deadline and datetime.fromisoformat(deadline) <= now:
            continue
        if status not in {"in_progress", "submitted"}:
            continue
        if revision_id not in quiz_titles:
            revision = store.get("quiz_revision_payload", revision_id)
            if not revision or revision.get("user_id") != user_id:
                continue
            quiz_titles[revision_id] = revision["title"]
        updated_at = _stamp(attempt) or attempt["started_at"]
        entry = {
            "kind": "attempt",
            "course_id": course_id,
            "course_name": courses[course_id]["name"],
            "attempt_id": attempt["attempt_id"],
            "title": quiz_titles[revision_id],
            "status": status,
            "updated_at": updated_at,
        }
        (running_tests if status == "in_progress" else saved_tests).append(entry)
        course_activity[course_id] = max(course_activity.get(course_id, ""), updated_at)

    recent_courses = [
        {
            "course_id": course_id,
            "name": course["name"],
            "last_activity_at": course_activity.get(course_id),
            "updated_at": _stamp(course),
        }
        for course_id, course in courses.items()
    ]
    recent_courses.sort(key=lambda row: row["last_activity_at"] or row["updated_at"], reverse=True)
    return (
        max(notes, key=_stamp, default=None),
        max(running_tests or saved_tests, key=_stamp, default=None),
        recent_courses[:3],
    )


def read_home(store: Store, user_id: str, today: date, at: time):
    courses = _courses(store, user_id)
    note, test, recent_courses = _activity(
        store,
        user_id,
        {
            key: course
            for key, course in courses.items()
            if course.get("status", "active") == "active"
        },
    )
    entries = _entries(store, user_id, courses)
    return {
        "recent_note": note,
        "recent_test": test,
        "recent_courses": recent_courses,
        "next_exam": next(
            (item for item in _upcoming(entries, today, at) if item["kind"] == "exam"), None
        ),
        "pending_todos": [
            item for item in entries if item["kind"] == "todo" and not item["completed_at"]
        ],
    }
