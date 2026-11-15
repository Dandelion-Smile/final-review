"""Real SQL proofs for the independently leased text/image pipeline."""
# ruff: noqa: F811 -- fixtures imported for pytest discovery.

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_course_knowledge import KnowledgeModel
from test_course_knowledge_postgres import knowledge_system  # noqa: F401
from test_material_hybrid import deck
from test_material_text_images import screenshots
from test_postgres_migrations import USER, database_url  # noqa: F401

from final_review.api import create_app
from final_review.auth import CurrentUser
from final_review.knowledge_jobs import process_knowledge_job
from final_review.knowledge_store import KnowledgeRepository
from final_review.material_jobs import process_material_job
from final_review.material_notes import STRATEGY
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration


def submit(s, directory, content=None):
    content = content or deck()
    document_id = uuid4().hex
    path = directory / f"{document_id}.pptx"
    path.write_bytes(content)
    document = dict(
        document_id=document_id,
        course_id="course-1",
        user_id=str(USER),
        title="资料笔记测试",
        source_type="teacher_ppt",
        source_origin="user_upload",
        file_name=path.name,
        file_path=str(path),
        content_sha256=sha256(content).hexdigest(),
        uploaded_at=datetime.now(UTC).isoformat(),
        chunk_count=0,
        parse_strategy=STRATEGY,
        parse_status="queued",
        preview_status="queued",
    )
    s.store.create_material_job(
        document,
        dict(
            job_id=uuid4().hex,
            document_id=document_id,
            course_id="course-1",
            idempotency_key=None,
            fingerprint=uuid4().hex,
            phase="preview",
        ),
    )
    return document


def run(s, phase):
    job = s.store.claim_material_job(phase)
    assert job
    process_material_job(s.store, s.kb, job, 50 * 1024 * 1024)
    return job


@pytest.fixture
def no_vision(monkeypatch):
    from final_review import material_conversion, material_jobs, slide_understanding

    def forbidden(*args, **kwargs):
        pytest.fail("text-images pipeline called a forbidden vision/OCR/legacy path")

    monkeypatch.setattr(material_jobs, "SlideInterpreter", forbidden)
    monkeypatch.setattr(slide_understanding, "SlideInterpreter", forbidden)
    monkeypatch.setattr(material_conversion, "_ocr_image", forbidden)
    monkeypatch.setattr(material_jobs, "convert_material", forbidden)


@pytest.fixture
def fake_images(monkeypatch):
    from final_review import material_text_images_worker as worker

    calls = []

    def render(pdf, cache, source_hash, number):
        calls.append(number)
        path = cache / f"slide-{number:03d}.png"
        Image.new("RGB", (30, 30), "white").save(path)
        path.with_suffix(".source").write_text(source_hash)
        return path

    monkeypatch.setattr(worker, "ensure_pdf", lambda *args: None)
    monkeypatch.setattr(worker, "render_page", render)
    return calls


def test_text_needs_no_pdf_and_initial_knowledge_is_enqueued(
    knowledge_system, tmp_path, no_vision, monkeypatch
):
    from final_review import material_text_images_worker as worker

    def forbidden(*args):
        pytest.fail("pure text must not convert PDF or render")

    monkeypatch.setattr(worker, "ensure_pdf", forbidden)
    monkeypatch.setattr(worker, "render_page", forbidden)
    s = knowledge_system
    document = submit(s, tmp_path)
    preview = run(s, "preview")
    assert s.store.get_material_job(preview["job_id"])["status"] == "succeeded"
    note = s.store.read_material_note("course-1", document["document_id"])
    assert note and not note["indexed_revision_id"]
    assert s.store.claim_material_job("images") is None
    job = run(s, "parse")
    current = s.store.get("document", document["document_id"])
    assert current["parse_status"] == "ready", current
    assert s.store.get_material_job(job["job_id"])["status"] == "succeeded"
    assert (
        s.store.read_material_note("course-1", document["document_id"])["indexed_revision_id"]
        == note["latest_saved_revision_id"]
    )
    chunks = s.store.list_material_chunks(document["document_id"])
    assert {c["content_origin"] for c in chunks} == {"native", "teaching_note"}
    assert all(c["note_revision_id"] == note["latest_saved_revision_id"] for c in chunks)
    assert KnowledgeRepository(s.store).claim() is not None
    assert not s.store.publish_text_images(job, current, chunks)


def test_images_can_finish_before_text_without_setting_parse_ready(
    knowledge_system, tmp_path, no_vision, fake_images
):
    s = knowledge_system
    document = submit(s, tmp_path, deck(("text", "picture", "table")))
    run(s, "preview")
    text = s.store.claim_material_job("parse")
    image = run(s, "images")
    current = s.store.get("document", document["document_id"])
    assert current["parse_status"] == "running"
    assert current["images_status"] == "ready"
    assert fake_images == [2, 3]
    assert not s.store.list_material_chunks(document["document_id"])
    process_material_job(s.store, s.kb, text, 50 * 1024 * 1024)
    assert s.store.get("document", document["document_id"])["images_status"] == "ready"
    assert s.store.get_material_job(image["job_id"])["status"] == "succeeded"


def test_text_and_knowledge_do_not_wait_for_images(
    knowledge_system, tmp_path, no_vision, fake_images
):
    s = knowledge_system
    document = submit(s, tmp_path, deck(("picture",)))
    run(s, "preview")
    image = s.store.claim_material_job("images")
    assert s.store.get("document", document["document_id"])["parse_status"] == "queued"
    run(s, "parse")
    ready = s.store.get("document", document["document_id"])
    assert ready["parse_status"] == "ready" and ready["images_status"] == "running"
    assert KnowledgeRepository(s.store).claim()
    process_material_job(s.store, s.kb, image, 50 * 1024 * 1024)
    assert s.store.get("document", document["document_id"])["parse_status"] == "ready"


def test_first_revision_completes_existing_knowledge_algorithm_before_images(
    knowledge_system, tmp_path, no_vision
):
    s = knowledge_system
    document = submit(s, tmp_path, deck(("picture",)))
    run(s, "preview")
    run(s, "parse")
    job = KnowledgeRepository(s.store).claim()
    model = KnowledgeModel([("DNS", "DNS传输协议", ["DNS 并非永远只使用 UDP。"])])
    assert process_knowledge_job(s.store, model, s.embeddings, job, s.settings)
    head = s.store.note_head("course-1", document["document_id"])
    assert head["knowledge_revision_id"] == head["initial_revision_id"]
    assert (
        s.store.connection.execute("SELECT count(*) AS n FROM knowledge_facts").fetchone()["n"] == 1
    )
    assert s.store.get("document", document["document_id"])["images_status"] == "queued"


def test_screenshots_publish_empty_index_and_all_enqueue_paths_skip(
    knowledge_system, tmp_path, no_vision, fake_images
):
    s = knowledge_system
    document = submit(s, tmp_path, screenshots())
    run(s, "preview")
    before = deepcopy(s.embeddings.__dict__)
    run(s, "parse")
    current = s.store.get("document", document["document_id"])
    assert current["parse_status"] == "ready"
    assert current["knowledge_status"] == "no_reliable_text"
    assert s.embeddings.__dict__ == before
    assert not s.store.list_material_chunks(document["document_id"])
    assert (
        KnowledgeRepository(s.store).enqueue_ready("course-1", document["document_id"], s.settings)[
            "status"
        ]
        == "skipped"
    )
    assert KnowledgeRepository(s.store).claim() is None
    run(s, "images")
    assert fake_images == [1]


def test_partial_image_retry_only_renders_failed_pages_and_preserves_saved_note(
    knowledge_system, tmp_path, no_vision, fake_images, monkeypatch
):
    from final_review import material_text_images_worker as worker

    s = knowledge_system
    document = submit(s, tmp_path, deck(("picture", "picture")))
    run(s, "preview")
    note = s.store.read_material_note("course-1", document["document_id"])
    body = deepcopy(note["revision"]["body"])
    body["content"][0]["content"][0]["content"][0]["text"] = "用户已保存的新文字"
    body["content"][-1]["attrs"]["hidden"] = True
    saved = s.store.save_material_note(document, note["latest_saved_revision_id"], "save", body)
    assert saved["sync_status"] == "pending_integration"
    before = s.store.read_material_note("course-1", document["document_id"])
    render = worker.render_page

    def partial(pdf, cache, source_hash, number):
        if number == 2:
            raise RuntimeError("intentional image failure")
        return render(pdf, cache, source_hash, number)

    monkeypatch.setattr(worker, "render_page", partial)
    run(s, "images")
    assert s.store.get("document", document["document_id"])["images_status"] == "partial"
    run(s, "parse")
    indexed = s.store.read_material_note("course-1", document["document_id"])
    assert indexed["indexed_revision_id"] == note["latest_saved_revision_id"]
    assert indexed["latest_saved_revision_id"] == saved["revision_id"]
    assert "用户已保存" not in s.store.get("document", document["document_id"])["cleaned_markdown"]
    jobs_before = s.store.connection.execute(
        "SELECT count(*) AS n FROM knowledge_build_jobs"
    ).fetchone()
    embeddings_before = deepcopy(s.embeddings.__dict__)
    first = s.store.retry_page_images(document)
    assert s.store.retry_page_images(document) == first
    monkeypatch.setattr(worker, "render_page", render)
    run(s, "images")
    assert fake_images == [1, 2]
    assert (
        s.store.read_material_note("course-1", document["document_id"])["revision"]
        == before["revision"]
    )
    assert s.embeddings.__dict__ == embeddings_before
    assert s.store.connection.execute(
        "SELECT count(*) AS n FROM knowledge_build_jobs"
    ).fetchone() == (jobs_before)


def test_expired_image_lease_cannot_checkpoint_or_complete(knowledge_system, tmp_path, no_vision):
    s = knowledge_system
    document = submit(s, tmp_path, screenshots())
    run(s, "preview")
    old = s.store.claim_material_job("images")
    s.store.connection.execute(
        "UPDATE material_jobs SET lease_until=now()-interval '1 second' WHERE job_id=%s",
        (old["job_id"],),
    )
    current = s.store.claim_material_job("images")
    assert current["lease_token"] != old["lease_token"]
    assert not s.store.checkpoint_page_image(old, 1, cache_key="stale")
    assert not s.store.finish_text_images(old)
    assert s.store.get("document", document["document_id"])["parse_status"] == "queued"


def test_api_upload_strategy_notes_and_images_before_text_ready(
    knowledge_system, tmp_path, no_vision, fake_images
):
    s = knowledge_system
    s.settings.material_pptx_parse_mode = "text_images"
    s.settings.material_note_edit_enabled = True
    s.settings.uploads_dir = str(tmp_path)
    app, principal = app_for(s)
    with TestClient(app) as client:
        response = client.post(
            "/knowledge/upload",
            data=dict(course_id="course-1", title="测试课件", source_type="teacher_ppt"),
            files={"file": ("mixed.pptx", deck(("picture",)))},
        )
        assert response.status_code == 202, response.text
        document_id = response.json()["document_id"]
        prefix = f"/api/courses/course-1/documents/{document_id}"
        assert client.get(prefix + "/reading-note").status_code == 409
        run(s, "preview")
        note = client.get(prefix + "/reading-note").json()
        assert note["indexed_revision_id"] is None
        assert client.get(prefix + "/page-images").json()["items"][0]["url"] is None
        run(s, "images")
        image = client.get(prefix + "/page-images/1")
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"
        assert client.get(prefix + "/page-images/99").status_code == 404
        saved = client.put(
            prefix + "/reading-note",
            json=dict(
                base_revision_id=note["latest_saved_revision_id"],
                idempotency_key="empty-save",
                body=dict(type="doc", content=[]),
            ),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["sync_status"] == "pending_integration"
        assert (
            client.get(
                prefix + "/reading-note/revisions/" + note["latest_saved_revision_id"]
            ).status_code
            == 200
        )
        s.store.connection.execute(
            "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,'hash')",
            ("00000000-0000-0000-0000-000000000002", "other@example.test"),
        )
        principal["id"] = "00000000-0000-0000-0000-000000000002"
        assert client.get(prefix + "/reading-note").status_code == 404
        assert client.get(prefix + "/page-images/1").status_code == 404


def app_for(s):
    """Override authentication only; real routes, authorization and SQL are exercised."""
    app = create_app(s.settings, SimpleNamespace(store=s.store, kb=s.kb))
    principal = dict(id=str(USER))

    def identity():
        return CurrentUser(id=principal["id"], is_local=True)

    def visit(dependant):
        if getattr(dependant.call, "__name__", "") == "identify":
            app.dependency_overrides[dependant.call] = identity
        for child in dependant.dependencies:
            visit(child)

    for route in app.routes:
        if hasattr(route, "dependant"):
            visit(route.dependant)

    @app.middleware("http")
    async def bind_fixture_user(request, call_next):
        token = s.store.bind_user(principal["id"])
        try:
            return await call_next(request)
        finally:
            s.store.reset_user(token)

    return app, principal


def test_preview_handoff_rolls_back_all_outputs_and_retry_is_idempotent(
    knowledge_system, tmp_path, monkeypatch
):
    s = knowledge_system
    document = submit(s, tmp_path, deck(("picture",)))
    original = s.store._insert_child_material_job

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("transaction rollback probe")

    monkeypatch.setattr(s.store, "_insert_child_material_job", fail)
    job = run(s, "preview")
    assert s.store.read_material_note("course-1", document["document_id"]) is None
    assert s.store.material_pages(document) == []
    assert (
        s.store.connection.execute("SELECT count(*) AS n FROM material_jobs").fetchone()["n"] == 1
    )
    monkeypatch.setattr(s.store, "_insert_child_material_job", original)
    s.store.connection.execute(
        "UPDATE material_jobs SET available_at=now() WHERE job_id=%s", (job["job_id"],)
    )
    run(s, "preview")
    assert (
        s.store.connection.execute("SELECT count(*) AS n FROM material_jobs").fetchone()["n"] == 3
    )
    assert (
        s.store.connection.execute("SELECT count(*) AS n FROM material_note_revisions").fetchone()[
            "n"
        ]
        == 1
    )
    assert not s.store.handoff_text_images_preview(job, [], {})


def test_real_concurrent_text_and_image_commits_retain_both_states(
    knowledge_system, tmp_path, no_vision, fake_images, monkeypatch
):
    from final_review import material_text_images_worker as worker

    s = knowledge_system
    document = submit(s, tmp_path, deck(("picture",)))
    run(s, "preview")
    jobs = [s.store.claim_material_job("parse"), s.store.claim_material_job("images")]
    barrier = Barrier(2)
    prepare, render = s.kb.prepare, worker.render_page

    def prepare_wait(*args, **kwargs):
        barrier.wait(timeout=15)
        return prepare(*args, **kwargs)

    def render_wait(*args, **kwargs):
        barrier.wait(timeout=15)
        return render(*args, **kwargs)

    monkeypatch.setattr(s.kb, "prepare", prepare_wait)
    monkeypatch.setattr(worker, "render_page", render_wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda job: process_material_job(s.store, s.kb, job, 50 * 1024 * 1024), jobs))
    current = s.store.get("document", document["document_id"])
    assert current["parse_status"] == "ready"
    assert current["images_status"] == "ready"
    assert all(s.store.get_material_job(job["job_id"])["status"] == "succeeded" for job in jobs)


def test_five_parallel_files_one_failure_is_isolated(knowledge_system, tmp_path, no_vision):
    s = knowledge_system
    documents = [
        submit(s, tmp_path, b"invalid archive" if index == 2 else deck()) for index in range(5)
    ]
    jobs = [s.store.claim_material_job("preview") for _ in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda job: process_material_job(s.store, s.kb, job, 50 * 1024 * 1024), jobs))
    for index, document in enumerate(documents):
        current = s.store.get("document", document["document_id"])
        assert current["preview_status"] == ("failed" if index == 2 else "ready")
        assert bool(s.store.read_material_note("course-1", document["document_id"])) == (index != 2)
    for _ in range(4):
        run(s, "parse")
    assert (
        sum(s.store.get("document", d["document_id"])["parse_status"] == "ready" for d in documents)
        == 4
    )


def test_generic_image_job_retry_does_not_set_parse_ready(knowledge_system, tmp_path):
    s = knowledge_system
    document = submit(s, tmp_path, screenshots())
    run(s, "preview")
    image = s.store.claim_material_job("images")
    assert s.store.fail_material_job(
        image["job_id"],
        image["attempts"],
        code="render_failed",
        message="expected",
        retry=False,
        lease_token=image["lease_token"],
    )
    assert s.store.retry_material_job(image["job_id"])
    assert s.store.get("document", document["document_id"])["parse_status"] == "queued"


def test_checkpoints_survive_a_new_store_and_old_lease_is_fenced(
    knowledge_system, tmp_path, fake_images
):
    from final_review.material_text_images_worker import image_cache_dir
    from final_review.rag import KnowledgeBase

    s = knowledge_system
    document = submit(s, tmp_path, screenshots(2))
    run(s, "preview")
    old = s.store.claim_material_job("images")
    cache = image_cache_dir(Path(document["file_path"]), document["content_sha256"])
    Image.new("RGB", (20, 20), "white").save(cache / "slide-001.png")
    (cache / "slide-001.source").write_text(document["content_sha256"])
    assert s.store.checkpoint_page_image(old, 1, cache_key=f"{document['content_sha256']}:1:v1")
    s.store.connection.execute(
        "UPDATE material_jobs SET lease_until=now()-interval '1 second' WHERE job_id=%s",
        (old["job_id"],),
    )
    fresh = PostgresStore(s.store.database_url)
    fresh.bind_user(str(USER))
    try:
        claimed = fresh.claim_material_job("images")
        process_material_job(
            fresh, KnowledgeBase(fresh, s.embeddings, s.settings), claimed, 50 * 1024 * 1024
        )
        assert fake_images == [2]
        assert fresh.get("document", document["document_id"])["images_status"] == "ready"
        assert not s.store.finish_text_images(old)
    finally:
        fresh.close()
