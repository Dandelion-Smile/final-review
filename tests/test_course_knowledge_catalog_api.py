"""Catalog tree grouping and job redaction without a database."""

from uuid import uuid4

from final_review.knowledge_tree import (
    apply_published_catalog,
    catalog_metadata,
    public_catalog_job,
)


def point(point_id, name, chapter="第2章"):
    return {
        "id": point_id,
        "name": name,
        "description": f"{name} 的说明",
        "importance_score": 0.5,
        "fact_count": 1,
        "fact_ids": [f"fact-{point_id}"],
        "sources": [
            {
                "document_id": "doc-1",
                "title": "课件",
                "chapter": chapter,
                "locator_id": "loc-1",
                "position_kind": "slide",
                "position": 3,
                "ordinal": 1,
            }
        ],
    }


def legacy(*points):
    return {
        "chapters": [{"id": "第2章", "title": "第2章", "points": list(points)}],
        "point_count": len(points),
        "updated_at": "2026-11-16T00:00:00+00:00",
    }


def test_published_catalog_groups_orders_and_distinguishes_unassigned_points():
    first, second, third = point("p1", "乙"), point("p2", "甲"), point("p3", "丙")
    kept, empty = uuid4(), uuid4()
    version = {
        "chapters": [
            {"chapter_id": empty, "title": "空章", "description": "", "sort_order": 0},
            {"chapter_id": kept, "title": "应用层", "description": "协议", "sort_order": 1},
        ],
        "assignments": [
            {"point_id": "p1", "chapter_id": kept, "sort_order": 1},
            {"point_id": "p2", "chapter_id": None, "sort_order": 5},
        ],
    }
    tree, counts = apply_published_catalog(legacy(first, second, third), version)
    assert [chapter["title"] for chapter in tree["chapters"]] == ["空章", "应用层", "未分章"]
    assert tree["chapters"][0]["points"] == []
    assert tree["chapters"][1]["id"] == str(kept)
    assert tree["chapters"][1]["description"] == "协议"
    assert tree["chapters"][1]["points"] == [first]
    assert "catalog_placement" not in first
    unassigned = tree["chapters"][2]["points"]
    assert [item["id"] for item in unassigned] == ["p2", "p3"]
    assert [item["catalog_placement"] for item in unassigned] == ["unclassified", "pending_catalog"]
    assert unassigned[0]["sources"][0]["chapter"] == "第2章"
    assert counts == {"chapter_count": 2, "assigned_count": 1, "unassigned_count": 2}
    assert tree["point_count"] == 3
    assert tree["updated_at"] == "2026-11-16T00:00:00+00:00"
    assert {item["id"] for chapter in tree["chapters"] for item in chapter["points"]} == {
        "p1",
        "p2",
        "p3",
    }


def test_catalog_status_follows_snapshot_hash_and_hides_job_internals():
    version = {"id": uuid4(), "knowledge_snapshot_hash": "published"}
    ready = catalog_metadata(
        version,
        "published",
        {"chapter_count": 1, "assigned_count": 2, "unassigned_count": 0},
        revision=2,
        updating=False,
    )
    stale = catalog_metadata(
        version,
        "current",
        {"chapter_count": 1, "assigned_count": 1, "unassigned_count": 1},
        revision=2,
        updating=True,
    )
    empty = catalog_metadata(
        None,
        "ignored",
        {"chapter_count": 9, "assigned_count": 9, "unassigned_count": 9},
        revision=4,
        updating=False,
    )
    assert ready["status"] == "ready" and ready["revision"] == 2 and ready["is_updating"] is False
    assert stale["status"] == "stale" and stale["is_updating"] is True
    assert stale["knowledge_snapshot_hash"] == "published"
    assert empty == {
        "status": "uninitialized",
        "version_id": None,
        "revision": None,
        "knowledge_snapshot_hash": None,
        "chapter_count": 0,
        "assigned_count": 0,
        "unassigned_count": 0,
        "is_updating": False,
    }
    hidden = public_catalog_job(
        {
            "id": uuid4(),
            "mode": "incremental",
            "status": "failed",
            "stage": "no_changes",
            "attempt": 1,
            "next_run_at": None,
            "result_version_id": None,
            "error_code": "CatalogNoChanges",
            "error_message": "do-not-leak",
            "checkpoint": {"prompt": "do-not-leak", "semantic_baseline": {"raw": "do-not-leak"}},
            "lease_token": uuid4(),
            "snapshot_hash": "do-not-leak",
        }
    )
    assert hidden["stage"] == "no_changes" and hidden["error_code"] == "CatalogNoChanges"
    assert "do-not-leak" not in str(hidden)
    assert set(hidden) == {
        "job_id",
        "mode",
        "status",
        "stage",
        "attempt",
        "next_run_at",
        "result_version_id",
        "error_code",
    }
