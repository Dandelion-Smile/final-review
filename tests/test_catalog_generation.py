"""Generation must stay inside verified facts even when several share one chunk."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from final_review import catalog_generation as catalog
from final_review.agent import FinalReviewAgent


def test_scoped_chunk_excludes_neighbouring_topic_and_rejects_changed_version():
    doc = dict(
        document_id="d",
        material_version_id="v1",
        course_id="c",
        user_id="u",
        title="Lecture",
        source_type="teacher_ppt",
    )
    store = SimpleNamespace(
        list_material_chunks=lambda _: [
            dict(chunk_id="z", content="TCP is reliable. UDP is connectionless.")
        ]
    )
    ref = dict(
        document_id="d",
        material_version_id="v1",
        chunk_id="z",
        quote="TCP is reliable.",
        name="TCP",
        point_id="p",
        fact_id="f",
    )
    scope = {"refs": [ref]}
    assert catalog.scoped_chunks(store, doc, scope)[0]["content"] == ref["quote"]
    assert catalog.citation_links(scope, "z", "UDP", "TCP") == []
    assert catalog.citation_links(scope, "z", "TCP", "UDP") == []
    with pytest.raises(ValueError, match="版本"):
        catalog.scoped_chunks(store, {**doc, "material_version_id": "v2"}, scope)


def test_merged_excerpt_fallback_cites_one_verified_fact():
    evidence = [
        {
            "chunk_id": "z",
            "document_id": "d",
            "file_name": "lecture",
            "source_type": "teacher_ppt",
            "ordinal": 0,
            "content": "first\nsecond",
            "verified_excerpts": [
                {"name": "Topic", "quote": "first"},
                {"name": "Topic", "quote": "second"},
            ],
        }
    ]
    result = FinalReviewAgent._extractive_note_batch(evidence, "chapter", "test", 1)
    point = result["points"][0]
    assert point["heading"] == "Topic"
    assert point["content"] == point["references"][0]["quote"] == "second"


def test_unpublished_or_conflicting_chapter_never_widens_scope(monkeypatch):
    store = SimpleNamespace(transaction=nullcontext)
    version = {
        "id": "published",
        "chapters": [{"title": "TCP", "chapter_id": "a"}, {"title": "UDP", "chapter_id": "b"}],
        "assignments": [],
    }
    repo = SimpleNamespace(
        current=lambda _: {"version": version}, snapshot=lambda _: {"points": []}
    )
    monkeypatch.setattr(catalog, "CatalogRepository", lambda _: repo)
    with pytest.raises(ValueError, match="确认"):
        catalog.resolve_scope(store, "c", "第二章 TCP", [])
    repo.current = lambda _: {"version": None}
    with pytest.raises(ValueError, match="已发布"):
        catalog.resolve_scope(store, "c", "TCP", [])
