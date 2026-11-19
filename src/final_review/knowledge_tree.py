"""Read-only course tree assembled from published knowledge and verified sources.

A published course catalog replaces source-PPT chapter grouping. Source locators
stay on each point. Courses without a published version keep the legacy tree.
"""

import re

from .knowledge_catalog_store import CatalogRepository


def chapter_key(title):
    """Sort chapter 2 before 10, including Chinese chapter numbers."""
    match = re.match(r"^第?\s*(\d+|[零〇一二两三四五六七八九十百]+)", title)
    number = None
    if match:
        token = match[1]
        if token.isdecimal():
            number = int(token)
        else:
            digits = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
            number, current = 0, 0
            for char in token:
                if char in "十百":
                    number += (current or 1) * (10 if char == "十" else 100)
                    current = 0
                else:
                    current = digits[char]
            number += current
    return (title == "未分章", number is None, number or 0, title)


def assemble_tree(rows):
    points = {}
    updated = []
    for row in rows:
        point_id = str(row["id"])
        point = points.setdefault(
            point_id,
            {
                "id": point_id,
                "name": row["name"],
                "description": row["description"],
                "importance_score": row["importance_score"],
                "sources": {},
                "facts": set(),
            },
        )
        point["facts"].add(str(row["fact_id"]))
        source = {
            "document_id": row["document_id"],
            "title": row["title"],
            "chapter": (row["chapter"] or "").strip() or "未分章",
            "locator_id": row["locator_id"],
            "position_kind": row["position_kind"],
            "position": row["position"],
            "ordinal": row["ordinal"],
        }
        point["sources"][(source["document_id"], source["locator_id"])] = source
        for stamp in (row["updated_at"], row["document_updated_at"]):
            if stamp:
                updated.append(stamp.isoformat() if hasattr(stamp, "isoformat") else stamp)
    chapters = {}
    for point in points.values():
        # A merged knowledge point appears once; all its source links remain available.
        sources = sorted(
            point["sources"].values(),
            key=lambda source: (
                chapter_key(source["chapter"]),
                source["document_id"],
                source["ordinal"],
            ),
        )
        chapter = sources[0]["chapter"]
        point["sources"] = sources
        point["fact_ids"] = sorted(point.pop("facts"))
        point["fact_count"] = len(point["fact_ids"])
        chapters.setdefault(chapter, {"id": chapter, "title": chapter, "points": []})[
            "points"
        ].append(point)
    for chapter in chapters.values():
        chapter["points"].sort(
            key=lambda point: (
                point["sources"][0]["document_id"],
                point["sources"][0]["ordinal"],
                point["name"],
                point["id"],
            )
        )
    return {
        "chapters": sorted(chapters.values(), key=lambda chapter: chapter_key(chapter["title"])),
        "point_count": len(points),
        "updated_at": max(updated, default=None),
    }


UNASSIGNED_TITLE = "未分章"
_ACTIVE_JOBS = ("queued", "running", "retry_wait")
_JOB_COLUMNS = "id,mode,status,stage,attempt,next_run_at,result_version_id,error_code"
_TREE_SQL = """SELECT p.id,p.name,p.description,p.importance_score,p.updated_at,
   f.id AS fact_id,v.document_id,
   coalesce(d.data->>'title',d.data->>'file_name',v.document_id) AS title,
   d.data->>'chapter' AS chapter,d.data->>'updated_at' AS document_updated_at,
   e.locator_id,l.data->>'position_kind' AS position_kind,
   l.data->'position' AS position,l.ordinal
   FROM knowledge_points p
   JOIN knowledge_facts f ON f.user_id=p.user_id AND f.course_id=p.course_id
     AND f.point_id=p.id AND f.status='supported'
   JOIN fact_evidence e ON e.user_id=f.user_id AND e.course_id=f.course_id
     AND e.fact_id=f.id AND e.support_status='supported' AND e.quote_verified
   JOIN material_versions v ON v.user_id=e.user_id AND v.course_id=e.course_id
     AND v.material_version_id=e.material_version_id
   JOIN documents d ON d.user_id=v.user_id AND d.course_id=v.course_id
     AND d.document_id=v.document_id AND d.data->>'parse_status'='ready'
   JOIN material_locators l ON l.user_id=e.user_id AND l.course_id=e.course_id
     AND l.material_version_id=e.material_version_id AND l.locator_id=e.locator_id
   WHERE p.user_id=%s AND p.course_id=%s AND p.status='active'"""
_BUILD_SQL = """SELECT j.status,count(*) AS count FROM knowledge_build_jobs j
   JOIN documents d ON d.user_id=j.user_id AND d.course_id=j.course_id
     AND d.document_id=j.document_id AND d.data->>'parse_status'='ready'
   WHERE j.user_id=%s AND j.course_id=%s GROUP BY j.status"""


def apply_published_catalog(legacy, version):
    """Group the current visible points by one immutable catalog version.

    ``chapter_id`` NULL was explicitly left unclassified. A visible point with
    no row in this version has not been catalogued yet; that is not a failure.
    """
    formal = []
    index = {}
    for row in sorted(
        version["chapters"], key=lambda item: (item["sort_order"], str(item["chapter_id"]))
    ):
        chapter = {
            "id": str(row["chapter_id"]),
            "title": row["title"],
            "description": row.get("description") or "",
            "points": [],
        }
        formal.append(chapter)
        index[chapter["id"]] = chapter
    assignments = {str(row["point_id"]): row for row in version["assignments"]}
    loose = []
    for point in (point for chapter in legacy["chapters"] for point in chapter["points"]):
        assignment = assignments.get(point["id"])
        target = None
        if assignment is not None and assignment["chapter_id"] is not None:
            target = index.get(str(assignment["chapter_id"]))
        if target is None:
            # NULL chapter_id is an explicit decision. Anything else is not in this version.
            placement = (
                "unclassified"
                if assignment is not None and assignment["chapter_id"] is None
                else "pending_catalog"
            )
            loose.append(
                (
                    0 if placement == "unclassified" else 1,
                    assignment["sort_order"] if assignment else 0,
                    point["name"],
                    point["id"],
                    {**point, "catalog_placement": placement},
                )
            )
            continue
        target["points"].append((assignment["sort_order"], point["name"], point["id"], point))
    for chapter in formal:
        chapter["points"] = [item[-1] for item in sorted(chapter["points"])]
    chapters = list(formal)
    if loose:
        chapters.append(
            {
                "id": UNASSIGNED_TITLE,
                "title": UNASSIGNED_TITLE,
                "description": "",
                "points": [item[-1] for item in sorted(loose)],
            }
        )
    assigned = sum(len(chapter["points"]) for chapter in formal)
    unassigned = len(loose)
    return (
        {
            "chapters": chapters,
            "point_count": assigned + unassigned,
            "updated_at": legacy["updated_at"],
        },
        {
            "chapter_count": len(formal),
            "assigned_count": assigned,
            "unassigned_count": unassigned,
        },
    )


def catalog_metadata(version, current_hash, counts, *, revision, updating):
    """``ready`` follows the published snapshot hash, not the latest job status."""
    if not version:
        return {
            "status": "uninitialized",
            "version_id": None,
            "revision": None,
            "knowledge_snapshot_hash": None,
            "chapter_count": 0,
            "assigned_count": 0,
            "unassigned_count": 0,
            "is_updating": updating,
        }
    return {
        "status": "ready" if version["knowledge_snapshot_hash"] == current_hash else "stale",
        "version_id": str(version["id"]),
        "revision": int(revision),
        "knowledge_snapshot_hash": version["knowledge_snapshot_hash"],
        "chapter_count": counts["chapter_count"],
        "assigned_count": counts["assigned_count"],
        "unassigned_count": counts["unassigned_count"],
        "is_updating": updating,
    }


def _stamp(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def public_catalog_job(row):
    """Status only. Checkpoints, leases, prompts and model output stay in the database."""
    if row is None:
        return None
    return {
        "job_id": str(row["id"]),
        "mode": row["mode"],
        "status": row["status"],
        "stage": row["stage"],
        "attempt": row["attempt"],
        "next_run_at": _stamp(row["next_run_at"]),
        "result_version_id": None
        if row["result_version_id"] is None
        else str(row["result_version_id"]),
        "error_code": row["error_code"],
    }


def _latest_catalog_job(connection, user_id, course_id):
    """Prefer the single live task. A superseded attempt yields to any later task."""
    return connection.execute(
        f"""SELECT {_JOB_COLUMNS} FROM course_knowledge_catalog_jobs j
            WHERE j.user_id=%s AND j.course_id=%s
              AND NOT (
                j.status='failed' AND j.error_code='SupersededBeforeExecution'
                AND EXISTS (
                  SELECT 1 FROM course_knowledge_catalog_jobs newer
                  WHERE newer.user_id=j.user_id AND newer.course_id=j.course_id
                    AND (newer.created_at, newer.id) > (j.created_at, j.id)
                )
              )
            ORDER BY (j.status IN ('queued','running','retry_wait')) DESC,
                     j.created_at DESC, j.id DESC
            LIMIT 1""",
        (user_id, course_id),
    ).fetchone()


def read_catalog_job(repo, course_id, job_id):
    row = repo.connection.execute(
        f"""SELECT {_JOB_COLUMNS} FROM course_knowledge_catalog_jobs
            WHERE user_id=%s AND course_id=%s AND id=%s""",
        (repo.user, course_id, job_id),
    ).fetchone()
    return public_catalog_job(row)


def read_tree(repo, course_id):
    catalog = CatalogRepository(repo.store)
    with repo.store.transaction():
        rows = repo.connection.execute(_TREE_SQL, (repo.user, course_id)).fetchall()
        legacy = assemble_tree(rows)
        statuses = repo.connection.execute(_BUILD_SQL, (repo.user, course_id)).fetchall()
        current = catalog.current(course_id)
        version = current["version"]
        snapshot_hash = catalog.snapshot(course_id)["hash"] if version else None
        job = _latest_catalog_job(repo.connection, repo.user, course_id)
    updating = bool(job and job["status"] in _ACTIVE_JOBS)
    if version:
        result, counts = apply_published_catalog(legacy, version)
        result["catalog"] = catalog_metadata(
            version,
            snapshot_hash,
            counts,
            revision=current["head"]["revision"],
            updating=updating,
        )
    else:
        result = legacy
        result["catalog"] = catalog_metadata(None, None, {}, revision=None, updating=updating)
    result["build_status"] = {row["status"]: row["count"] for row in statuses}
    result["catalog_job"] = public_catalog_job(job)
    return result
