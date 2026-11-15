"""Real database proofs for phase fencing and atomic publication."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest
from test_course_knowledge_postgres import knowledge_system  # noqa: F401
from test_material_hybrid import deck
from test_postgres_migrations import database_url  # noqa: F401

from final_review.material_jobs import process_material_job
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration


def submit(s, directory, *, phase="preview", content=None):
    content = content or deck()
    document_id = uuid4().hex
    path = directory / f"{document_id}.pptx"
    path.write_bytes(content)
    document = dict(
        document_id=document_id,
        course_id="course-1",
        user_id=s.store._user(),
        title="原文测试",
        file_name=f"{document_id}.pptx",
        file_path=str(path),
        content_sha256=sha256(content).hexdigest(),
        source_type="teacher_ppt",
        parse_status="queued",
        preview_status="queued",
        chunk_count=0,
        uploaded_at=datetime.now(UTC).isoformat(),
    )
    job = s.store.create_material_job(
        document,
        dict(
            job_id=uuid4().hex,
            course_id="course-1",
            document_id=document_id,
            fingerprint=uuid4().hex,
            idempotency_key=None,
            phase=phase,
        ),
    )
    return document, job


def process(s, phase):
    claimed = s.store.claim_material_job(phase)
    assert claimed
    process_material_job(s.store, s.kb, claimed, 50 * 1024 * 1024)
    return claimed


def test_preview_handoff_fences_old_lease_and_only_formal_publish_enqueues_knowledge(
    knowledge_system,
    tmp_path,
):
    s = knowledge_system
    document, submitted = submit(s, tmp_path)
    preview = process(s, "preview")
    doc = s.store.get("document", document["document_id"])
    pages = s.store.material_pages(doc)
    assert doc["preview_status"] == "ready" and len(pages) == 1
    assert not s.store.connection.execute(
        "SELECT 1 FROM knowledge_build_jobs WHERE document_id=%s", (doc["document_id"],)
    ).fetchone()
    assert not s.store.connection.execute(
        "SELECT 1 FROM material_versions WHERE document_id=%s", (doc["document_id"],)
    ).fetchone()
    assert not s.store.list_material_chunks(doc["document_id"])
    assert not s.store.renew_material_job(
        preview["job_id"], preview["attempts"], preview["lease_token"]
    )
    assert not s.store.checkpoint_material_page(preview, pages[0])
    assert not s.store.handoff_material_preview(preview, pages, {}, {})
    formal = process(s, "parse")
    ready = s.store.get("document", doc["document_id"])
    assert ready["parse_status"] == "ready"
    assert ready["quality_status"] == "native_checked"
    assert s.store.get_material_job(submitted["job_id"])["phase"] == "images"
    assert (
        s.store.connection.execute(
            "SELECT count(*) AS n FROM knowledge_build_jobs WHERE document_id=%s",
            (doc["document_id"],),
        ).fetchone()["n"]
        == 1
    )
    assert s.store.list_material_chunks(doc["document_id"])
    assert not s.store.publish_material_job(
        formal["job_id"], formal["attempts"], ready, [], lease_token=formal["lease_token"]
    )
    assert (
        s.store.get("document", doc["document_id"])["material_version_id"]
        == ready["material_version_id"]
    )


def test_lease_reclaim_and_manual_retry_never_reuse_attempt_or_token(knowledge_system, tmp_path):
    s = knowledge_system
    document, _ = submit(s, tmp_path)
    first = s.store.claim_material_job("preview")
    s.store.connection.execute(
        "UPDATE material_jobs SET lease_until=now()-interval '1 second' WHERE job_id=%s",
        (first["job_id"],),
    )
    assert not s.store.checkpoint_material_page(first, dict(position=1))
    second = s.store.claim_material_job("preview")
    assert second["lease_token"] != first["lease_token"]
    assert second["attempts"] > first["attempts"]
    assert not s.store.update_material_job(
        first["job_id"], first["attempts"], stage="wrong", lease_token=first["lease_token"]
    )
    assert not s.store.fail_material_job(
        first["job_id"],
        first["attempts"],
        code="wrong",
        message="wrong",
        retry=False,
        lease_token=first["lease_token"],
    )
    assert s.store.fail_material_job(
        second["job_id"],
        second["attempts"],
        code="test",
        message="temporary",
        retry=False,
        lease_token=second["lease_token"],
    )
    assert s.store.get("document", document["document_id"])["preview_status"] == "failed"
    s.store.retry_material_job(second["job_id"])
    third = s.store.claim_material_job("preview")
    assert third["attempts"] > second["attempts"] and third["phase_attempts"] == 1
    assert not s.store.renew_material_job(third["job_id"], third["attempts"], second["lease_token"])
    process_material_job(s.store, s.kb, third, 50 * 1024 * 1024)
    assert s.store.get("document", document["document_id"])["preview_status"] == "ready"


def test_five_formal_leases_do_not_prevent_preview_and_tenant_cache_isolation(
    knowledge_system,
    tmp_path,
):
    s = knowledge_system
    for _ in range(5):
        submit(s, tmp_path, phase="parse")
        assert s.store.claim_material_job("parse")
    document, _ = submit(s, tmp_path)
    claimed = process(s, "preview")
    doc = s.store.get("document", document["document_id"])
    assert doc["preview_status"] == "ready"
    assert len(s.store.material_pages(doc)) == 1
    assert not s.store.material_pages({**doc, "content_sha256": "different-source"})
    assert not s.store.material_pages({**doc, "preview_strategy": "different-policy"})
    other = PostgresStore(s.store.database_url)
    other.bind_user("00000000-0000-0000-0000-000000000002")
    try:
        assert not other.material_pages(doc)
        assert not other.checkpoint_material_page(claimed, dict(position=1))
    finally:
        other.close()


def test_background_images_failure_never_downgrades_searchable_document(knowledge_system, tmp_path):
    s = knowledge_system
    document, submitted = submit(s, tmp_path)
    process(s, "preview")
    process(s, "parse")
    ready = s.store.get("document", document["document_id"])
    images = s.store.claim_material_job("images")
    assert s.store.fail_material_job(
        images["job_id"],
        images["attempts"],
        code="renderer",
        message="renderer unavailable",
        retry=False,
        lease_token=images["lease_token"],
    )
    assert s.store.get("document", document["document_id"])["parse_status"] == "ready"
    s.store.retry_material_job(submitted["job_id"])
    images = s.store.claim_material_job("images")
    assert s.store.finish_material_images(images)
    current = s.store.get("document", document["document_id"])
    assert current["material_version_id"] == ready["material_version_id"]
    assert current["images_status"] == "ready"
    assert s.store.get_material_job(submitted["job_id"])["status"] == "succeeded"
