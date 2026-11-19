"""Isolated SQL proofs for learning-note generations, caching and publication."""
# ruff: noqa: F811 -- imported pytest fixtures.

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest
from test_course_knowledge_postgres import knowledge_system  # noqa: F401
from test_postgres_migrations import USER, database_url  # noqa: F401

from final_review.material_jobs import process_material_job
from final_review.material_note_polish import assemble_body, polish_model_config
from final_review.material_note_polish_jobs import (
    MAX_JOB_MODEL_CALLS,
    PolishBudgetExhausted,
    PolishRequestBudget,
)
from final_review.material_notes import NoteConflict, node_text
from final_review.material_text_source import source_units

pytestmark = pytest.mark.integration


def setup_source(s, tmp_path):
    content = "# 服务配置\n\n默认端口为 8080。\n\n配置时需要指定服务端口。"
    path = tmp_path / "source.md"
    path.write_text(content, encoding="utf-8")
    document = dict(
        document_id=uuid4().hex,
        course_id="course-1",
        user_id=str(USER),
        title="服务课件",
        source_type="teacher_ppt",
        source_origin="user_upload",
        file_name=path.name,
        file_path=str(path),
        content_sha256=sha256(path.read_bytes()).hexdigest(),
        uploaded_at=datetime.now(UTC).isoformat(),
        chunk_count=0,
        parse_strategy="text-polish-v1",
        parse_status="queued",
        preview_status="queued",
    )
    s.store.create_material_job(
        document,
        dict(
            job_id=uuid4().hex,
            document_id=document["document_id"],
            course_id="course-1",
            phase="preview",
            idempotency_key=None,
            fingerprint=uuid4().hex,
        ),
    )
    preview = s.store.claim_material_job("preview")
    process_material_job(s.store, s.kb, preview, 1024 * 1024)
    assert s.store.get_material_job(preview["job_id"])["status"] == "succeeded"
    return document, s.store.claim_polish_job()


def body_for(s, document):
    units = source_units(s.store.material_pages(document), document["content_sha256"])
    return assemble_body(
        document["document_id"],
        [
            dict(
                section_title="服务配置",
                result=dict(
                    elements=[
                        dict(
                            type="paragraph",
                            text="默认端口为 **8080**。",
                            source_refs=[u["id"] for u in units],
                            content_role="source",
                        )
                    ],
                    excluded=[],
                ),
            )
        ],
        units,
        "服务配置学习笔记",
    )


def regenerate(s, document):
    s.store.regenerate_polish(
        document,
        model_config=polish_model_config(s.settings),
        prompt_version=s.settings.material_note_polish_prompt_version,
    )
    return s.store.claim_polish_job()


def test_exhausted_polish_continues_once_without_resetting_usage(knowledge_system, tmp_path):
    store = knowledge_system.store
    document, job = setup_source(knowledge_system, tmp_path)
    ledger = dict(
        version=1,
        identity={key: job[key] for key in ("job_id", "generation", "input_hash")},
        logical_calls=MAX_JOB_MODEL_CALLS,
        model_requests=MAX_JOB_MODEL_CALLS + 1,
    )
    assert store.update_polish_job(
        job["job_id"],
        job["attempts"],
        lease_token=job["lease_token"],
        metrics={"request_budget": ledger},
    )
    assert store.fail_polish_job(job, code="budget_exhausted", message="额度用完", retry=False)
    with pytest.raises(ValueError, match="追加额度"):
        store.retry_polish(document)
    assert store.retry_polish(document, extra_model_calls=1)["status"] == "queued"
    claimed = store.claim_polish_job(job["job_id"])
    assert claimed["metrics"]["request_budget"] == ledger

    class Model:
        def structured(self, schema, task, data, *, before_request):
            before_request()
            before_request()
            return {}

    budget = PolishRequestBudget(claimed, Model(), lambda **_kwargs: None)
    assert budget.structured(None, "", {}) == {}
    with pytest.raises(PolishBudgetExhausted):
        budget.structured(None, "", {})
    assert budget.ledger["model_requests"] == ledger["model_requests"] + 2
    assert store.fail_polish_job(claimed, code="budget_exhausted", message="额度用完", retry=False)
    with pytest.raises(ValueError, match="已追加过"):
        store.retry_polish(document, extra_model_calls=1)


def test_new_note_publishes_without_affecting_original_index(knowledge_system, tmp_path):
    s = knowledge_system
    document, job = setup_source(s, tmp_path)
    original = s.store.read_material_note("course-1", document["document_id"])
    outcome = s.store.publish_polished_note(job, body_for(s, document), {})
    assert outcome["published_as"] == "ready"
    parse = s.store.claim_material_job("parse")
    process_material_job(s.store, s.kb, parse, 1024 * 1024)
    head = s.store.note_head("course-1", document["document_id"])
    assert head["latest_saved_revision_id"] == outcome["revision_id"]
    assert head["indexed_revision_id"] == original["initial_revision_id"]
    assert "**" not in s.store.get("document", document["document_id"])["cleaned_markdown"]


def test_candidates_use_unique_revision_numbers_and_do_not_overwrite_saved_note(
    knowledge_system, tmp_path
):
    s = knowledge_system
    document, job = setup_source(s, tmp_path)
    first = s.store.publish_polished_note(job, body_for(s, document), {})
    candidate = s.store.publish_polished_note(regenerate(s, document), body_for(s, document), {})
    assert candidate["published_as"] == "candidate"
    assert (
        s.store.note_head("course-1", document["document_id"])["latest_saved_revision_id"]
        == first["revision_id"]
    )
    current = s.store.read_material_note("course-1", document["document_id"])
    edited = deepcopy(current["revision"]["body"])
    edited["content"][-1]["content"][0]["content"][0]["text"] = "我补充的理解。"
    saved = s.store.save_material_note(document, first["revision_id"], "edit", edited)
    with pytest.raises(NoteConflict):
        s.store.apply_polish_candidate(
            "course-1",
            document["document_id"],
            base_revision_id=first["revision_id"],
            candidate_revision_id=candidate["revision_id"],
        )
    next_candidate = s.store.publish_polished_note(
        regenerate(s, document), body_for(s, document), {}
    )
    assert next_candidate["published_as"] == "candidate"
    current = s.store.read_material_note("course-1", document["document_id"])
    assert current["latest_saved_revision_id"] == saved["revision_id"]
    assert "我补充的理解" in node_text(current["revision"]["body"])
    applied = s.store.apply_polish_candidate(
        "course-1",
        document["document_id"],
        base_revision_id=saved["revision_id"],
        candidate_revision_id=next_candidate["revision_id"],
    )
    assert applied["revision_id"] == next_candidate["revision_id"]


def test_cache_is_materialized_per_generation_and_failed_rows_do_not_block_regeneration(
    knowledge_system, tmp_path
):
    s = knowledge_system
    document, job = setup_source(s, tmp_path)
    batches = [
        dict(cache_key="same-context", units=[dict(id="a")]),
        dict(cache_key="failed-context", units=[dict(id="b")]),
    ]
    assert s.store.prepare_polish_batches(job, batches)[0]["status"] == "pending"
    accepted = dict(elements=[], excluded=[])
    assert s.store.checkpoint_polish_batch(
        job, 0, result=accepted, review=dict(supported=True, issues=[])
    )
    assert s.store.checkpoint_polish_batch(job, 1, error="output_limit")
    s.store.fail_polish_job(job, code="test", message="retry", retry=False)
    next_job = regenerate(s, document)
    rows = s.store.prepare_polish_batches(next_job, batches)
    assert [row["status"] for row in rows] == ["done", "pending"]
    assert len(s.store.load_polish_batches(next_job)) == 2
    assert len(s.store.load_polish_batches(job)) == 2
    assert s.store.checkpoint_polish_batch(
        next_job, 0, result=accepted, review=dict(supported=True, issues=[])
    )


def test_failed_exclusion_checkpoint_preserves_user_note_and_attempt_counters(
    knowledge_system, tmp_path
):
    """A failed polish batch must not replace a saved note, its sources, or attempt totals."""
    s = knowledge_system
    document, job = setup_source(s, tmp_path)
    published = s.store.publish_polished_note(job, body_for(s, document), {})
    current = s.store.read_material_note("course-1", document["document_id"])
    edited = deepcopy(current["revision"]["body"])
    edited["content"][-1]["content"][0]["content"][0]["text"] = "我补充的理解。"
    saved = s.store.save_material_note(document, published["revision_id"], "edit", edited)
    saved_body = s.store.read_material_note("course-1", document["document_id"])["revision"]["body"]
    saved_refs = [
        node["attrs"].get("source_refs", [])
        for node in saved_body["content"]
        if node.get("attrs", {}).get("source_refs")
    ]
    knowledge_before = s.store.connection.execute(
        "SELECT count(*) AS n FROM knowledge_points"
    ).fetchone()["n"]
    next_job = regenerate(s, document)
    assert next_job["generation"] == job["generation"] + 1
    assert next_job["attempts"] >= 1
    rows = s.store.prepare_polish_batches(
        next_job,
        [
            dict(cache_key="kept-context", units=[dict(id="kept")]),
            dict(cache_key="failed-context", units=[dict(id="failed")]),
        ],
    )
    assert [row["status"] for row in rows] == ["pending", "pending"]
    assert s.store.checkpoint_polish_batch(
        next_job,
        0,
        result=dict(elements=[], excluded=[]),
        review=dict(supported=True, issues=[]),
    )
    assert s.store.checkpoint_polish_batch(
        next_job,
        1,
        error="exclusion_failed",
        review=dict(
            supported=True,
            issues=[],
            program_errors=["块 failed 包含正文，不能按导航包装排除"],
            program_error_classes=["exclusion_navigation"],
        ),
    )
    assert s.store.fail_polish_job(next_job, code="exclusion_failed", message="stop", retry=False)
    stored = s.store.connection.execute(
        "SELECT status,attempts,phase_attempts,generation FROM material_note_polish_jobs "
        "WHERE job_id=%s",
        (next_job["job_id"],),
    ).fetchone()
    assert stored["status"] == "failed"
    assert stored["attempts"] == next_job["attempts"]
    assert stored["phase_attempts"] == next_job["phase_attempts"]
    assert stored["generation"] == next_job["generation"]
    failed_row = s.store.load_polish_batches(next_job)[1]
    assert failed_row["error"] == "exclusion_failed"
    assert failed_row["review"]["program_errors"]
    head = s.store.note_head("course-1", document["document_id"])
    assert head["latest_saved_revision_id"] == saved["revision_id"]
    assert head["candidate_revision_id"] is None
    current = s.store.read_material_note("course-1", document["document_id"])
    assert "我补充的理解" in node_text(current["revision"]["body"])
    assert [
        node["attrs"].get("source_refs", [])
        for node in current["revision"]["body"]["content"]
        if node.get("attrs", {}).get("source_refs")
    ] == saved_refs
    assert (
        s.store.connection.execute("SELECT count(*) AS n FROM knowledge_points").fetchone()["n"]
        == knowledge_before
    )
    assert s.store.publish_polished_note(job, body_for(s, document), {}) is False
    assert s.store.publish_polished_note(next_job, body_for(s, document), {}) is False


def test_stale_token_or_generation_cannot_publish(knowledge_system, tmp_path):
    s = knowledge_system
    document, job = setup_source(s, tmp_path)
    stale = {**job, "lease_token": "wrong-token"}
    assert s.store.polish_job_context(stale) is None
    assert s.store.prepare_polish_batches(stale, []) is None
    assert s.store.publish_polished_note(stale, body_for(s, document), {}) is False
    regenerate(s, document)
    assert s.store.polish_job_context(job) is None
    assert s.store.publish_polished_note(job, body_for(s, document), {}) is False
