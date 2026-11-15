"""Small, version-bound summaries for the course quiz workspace."""

from .domain import DomainService
from .quiz_jobs import public_quiz_job


def list_quiz_summaries(store, user_id: str, course_id: str):
    DomainService(store, user_id).course(course_id)
    items = []
    for asset in store.scan("learning_asset", {"course_id": course_id}):
        if asset.get("user_id") != user_id or asset.get("asset_type") != "quiz":
            continue
        revision_id = asset.get("latest_revision_id")
        payload = (store.get("quiz_revision_payload", revision_id) or {}) if revision_id else {}
        if payload.get("user_id") != user_id or payload.get("course_id") != course_id:
            payload = {}
        config = payload.get("config", {})
        items.append(
            {
                **asset,
                "title": payload.get("title", asset.get("title", "未命名试卷")),
                "latest_revision_state": payload.get("state"),
                "question_count": payload.get("question_count"),
                "total_score": payload.get("total_score"),
                "duration_minutes": payload.get("duration_minutes"),
                "difficulty": config.get("difficulty"),
                "blueprint": config.get("blueprint", []),
            }
        )
    return {"items": sorted(items, key=lambda item: item["created_at"], reverse=True)}


def list_quiz_jobs(store, user_id: str, course_id: str):
    DomainService(store, user_id).course(course_id)
    return {
        "items": sorted(
            [
                public_quiz_job(job)
                for job in store.scan("quiz_job", {"course_id": course_id})
                if job.get("user_id") == user_id
            ],
            key=lambda job: job["created_at"],
            reverse=True,
        )
    }
