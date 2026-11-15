"""Read-only course tree assembled from published knowledge and verified sources."""

import re


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


def read_tree(repo, course_id):
    rows = repo.connection.execute(
        """SELECT p.id,p.name,p.description,p.importance_score,p.updated_at,
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
           WHERE p.user_id=%s AND p.course_id=%s AND p.status='active'""",
        (repo.user, course_id),
    ).fetchall()
    result = assemble_tree(rows)
    statuses = repo.connection.execute(
        """SELECT j.status,count(*) AS count FROM knowledge_build_jobs j
           JOIN documents d ON d.user_id=j.user_id AND d.course_id=j.course_id
             AND d.document_id=j.document_id AND d.data->>'parse_status'='ready'
           WHERE j.user_id=%s AND j.course_id=%s GROUP BY j.status""",
        (repo.user, course_id),
    ).fetchall()
    result["build_status"] = {row["status"]: row["count"] for row in statuses}
    return result
