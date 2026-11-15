"""End-to-end knowledge proofs; only an explicitly isolated TEST_DATABASE_URL is used."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from test_course_knowledge import KnowledgeEmbeddings, KnowledgeModel
from test_postgres_migrations import USER, _seed_course, database_url  # noqa: F401

from final_review.api import create_app
from final_review.config import Settings
from final_review.knowledge import Support
from final_review.knowledge_jobs import extract_candidates, process_knowledge_job
from final_review.knowledge_store import KnowledgeRepository, LeaseLost, enqueue
from final_review.material_jobs import process_material_job
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore
from final_review.rag import KnowledgeBase
from final_review.schemas import MaterialInput

pytestmark = pytest.mark.integration
STATELESS = "HTTP是无状态协议。"
PARAPHRASE = "HTTP协议不保存请求间状态。"
PORT = "HTTP默认端口为80。"


@pytest.fixture
def knowledge_system(database_url):  # noqa: F811
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
        connection.execute("UPDATE courses SET record_key='course-1'")
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        embedding_dimensions=3,
        llm_api_key="test",
        embedding_api_key="test",
        auth_mode="token",
        api_token="test",
        knowledge_block_pages=1,
    )
    store = PostgresStore(database_url)
    store.bind_user(str(USER))
    store.knowledge_settings = settings
    embeddings = KnowledgeEmbeddings()
    system = SimpleNamespace(
        store=store,
        settings=settings,
        embeddings=embeddings,
        kb=KnowledgeBase(store, embeddings, settings),
    )
    yield system
    store.close()


def parsed(system, text=STATELESS, title="HTTP.pptx", sections=None):
    material = MaterialInput(
        course_id="course-1", title=title, source_type="teacher_ppt", markdown=text
    )
    document, chunks = system.kb.prepare(material, sections=sections)
    system.store.ingest(document, chunks)
    return document["document_id"]


def counts(system):
    return tuple(
        system.store.connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        for table in ("knowledge_points", "knowledge_facts", "fact_evidence")
    )


def process(system, specs, job=None):
    job = job or KnowledgeRepository(system.store).claim()
    assert job is not None
    assert process_knowledge_job(
        system.store, KnowledgeModel(specs), system.embeddings, job, system.settings
    )
    return job


def test_candidates_checkpoint_before_any_formal_knowledge_write(knowledge_system):
    s = knowledge_system
    parsed(s)
    repo = KnowledgeRepository(s.store)
    job = repo.claim()
    candidates = extract_candidates(
        repo, job, KnowledgeModel([("HTTP", "HTTP无状态性", [STATELESS])]), s.embeddings
    )
    assert len(candidates) == 1
    assert candidates[0]["facts_json"]["facts"][0]["status"] == "supported"
    assert counts(s) == (0, 0, 0)
    process(s, [], job)
    assert counts(s) == (1, 1, 1)


def test_repeat_upload_build_and_worker_are_idempotent(knowledge_system):
    s = knowledge_system
    document = parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    with s.store.connection.cursor() as cursor:
        first = enqueue(cursor, s.store.get("document", document), s.settings)
        second = enqueue(cursor, s.store.get("document", document), s.settings)
    assert first["job_id"] == second["job_id"]
    assert first["status"] == "succeeded"
    assert KnowledgeRepository(s.store).claim() is None
    assert counts(s) == (1, 1, 1)


def test_new_ppt_paraphrase_new_fact_and_multiple_evidence(knowledge_system):
    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    parsed(s, PARAPHRASE + "\n" + PORT, "第二份课件.pptx")
    job = process(s, [("HTTP请求状态", "HTTP无状态性", [PARAPHRASE, PORT])])
    assert counts(s) == (1, 2, 3)
    candidate = KnowledgeRepository(s.store).candidates(job)[0]
    assert candidate["status"] == "merged"
    assert candidate["matched_point_id"] is not None


def test_same_name_different_granularity_remains_independent(knowledge_system):
    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    parsed(s, PORT, "应用.pptx")
    job = process(s, [("HTTP", "HTTP连接端口配置", [PORT])])
    assert counts(s) == (2, 2, 2)
    candidate = KnowledgeRepository(s.store).candidates(job)[0]
    assert candidate["match_relation"] == "narrower"


def test_same_target_in_batch_deduplicates_new_paraphrased_facts(knowledge_system, monkeypatch):
    from final_review import knowledge_jobs

    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    paraphrase = "HTTP的默认服务端口是80。"
    parsed(s, PORT + paraphrase, "batch.pptx")
    # Exercise the second-level guard even if two candidates survived first-level recall.
    monkeypatch.setattr(knowledge_jobs, "dedup_internal", lambda _m, candidates, *_: candidates)
    process(
        s,
        [("HTTP默认端口", "HTTP无状态性", [PORT]), ("HTTP服务端口", "HTTP无状态性", [paraphrase])],
    )
    assert counts(s) == (1, 2, 3)


def test_existing_legacy_points_are_preserved_and_not_silently_activated(knowledge_system):
    s = knowledge_system
    legacy = dict(course_id="course-1", name="HTTP", summary="历史记录")
    s.store.put("knowledge_point", "old-point", legacy)
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    row = s.store.connection.execute(
        "SELECT status,data,id FROM knowledge_points WHERE record_key='old-point'"
    ).fetchone()
    assert row["data"] == legacy
    assert row["status"] == "legacy"
    assert row["id"] is not None
    assert counts(s) == (2, 1, 1)


def test_internal_dedup_unions_evidence_from_multiple_blocks(knowledge_system):
    s = knowledge_system
    sections = [
        dict(text=text, position_kind="slide", position=index)
        for index, text in enumerate([STATELESS, PARAPHRASE], 1)
    ]
    parsed(s, STATELESS + PARAPHRASE, sections=sections)

    def specs(content):
        return [("HTTP", "HTTP无状态性", [STATELESS if STATELESS in content else PARAPHRASE])]

    job = process(s, specs)
    assert counts(s) == (1, 1, 2)
    assert [c["status"] for c in KnowledgeRepository(s.store).candidates(job)] == [
        "new",
        "internal_merged",
    ]


def test_failed_block_resumes_without_reextracting_completed_block(knowledge_system):
    s = knowledge_system
    sections = [
        dict(text=text, position_kind="slide", position=index)
        for index, text in enumerate([STATELESS, PARAPHRASE], 1)
    ]
    parsed(s, STATELESS + PARAPHRASE, sections=sections)
    model = KnowledgeModel(
        lambda content: [
            ("HTTP", "HTTP无状态性", [STATELESS if STATELESS in content else PARAPHRASE])
        ]
    )
    model.fail_at = 2
    repo = KnowledgeRepository(s.store)
    job = repo.claim()
    assert not process_knowledge_job(s.store, model, s.embeddings, job, s.settings)
    assert counts(s) == (0, 0, 0)
    assert len(repo.candidates(job)) == 1
    s.store.connection.execute("UPDATE knowledge_build_jobs SET available_at=now()")
    resumed = repo.claim()
    model.extractions = 0
    model.fail_at = None
    assert process_knowledge_job(s.store, model, s.embeddings, resumed, s.settings)
    assert model.extractions == 1
    assert counts(s) == (1, 1, 2)


def test_verification_timeout_preserves_raw_extraction_for_retry(knowledge_system):
    s = knowledge_system
    parsed(s, STATELESS + "\nHTTP请求相互独立。")

    class Model(KnowledgeModel):
        fail = True

        def structured(self, schema, task, data, **kwargs):
            if schema is Support and self.fail:
                raise TimeoutError("simulated verification timeout")
            return super().structured(schema, task, data, **kwargs)

    model = Model([("HTTP", "HTTP无状态性", [STATELESS])])
    repo = KnowledgeRepository(s.store)
    job = repo.claim()
    assert not process_knowledge_job(s.store, model, s.embeddings, job, s.settings)
    pending = repo.candidates(job)
    assert len(pending) == 1
    assert pending[0]["embedding"] is None
    assert pending[0]["facts_json"]["facts"][0]["status"] == "pending"
    assert counts(s) == (0, 0, 0)
    block = s.store.connection.execute(
        "SELECT error_message FROM material_extraction_blocks WHERE build_job_id=%s",
        (job["job_id"],),
    ).fetchone()
    assert "超时" in block["error_message"]
    model.fail = False
    s.store.connection.execute("UPDATE knowledge_build_jobs SET available_at=now()")
    assert process_knowledge_job(s.store, model, s.embeddings, repo.claim(), s.settings)
    assert model.extractions == 1
    assert counts(s) == (1, 1, 1)


def test_embedding_batches_resume_only_missing_vectors(knowledge_system):
    s = knowledge_system
    parsed(s, STATELESS + "\n补充说明")

    class Embeddings:
        calls = []

        def embed_documents(self, texts):
            self.calls.append(len(texts))
            assert len(texts) <= 10
            if len(self.calls) == 2:
                raise TimeoutError("second batch unavailable")
            return [[1.0, 0.1, 0.2] for _ in texts]

    model = KnowledgeModel([(f"概念{i}", f"范围{i}", [STATELESS]) for i in range(11)])
    repo = KnowledgeRepository(s.store)
    job = repo.claim()
    vectors = Embeddings()
    with pytest.raises(TimeoutError):
        extract_candidates(repo, job, model, vectors)
    first = repo.candidates(job)
    assert len(first) == 11
    assert sum(c["embedding"] is not None for c in first) == 10
    resumed = extract_candidates(repo, job, model, vectors)
    assert vectors.calls == [10, 1, 1]
    assert all(c["embedding"] is not None for c in resumed)
    assert model.extractions == 1
    assert counts(s) == (0, 0, 0)


def test_resource_budget_is_shared_across_database_connections(knowledge_system):
    from threading import Event

    from final_review.pipeline_runtime import resource_slot

    s = knowledge_system
    entered = Event()
    blocker = psycopg.connect(s.store.database_url, autocommit=True)
    for index in range(2):
        blocker.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s,0))",
            (f"pipeline-resource:embedding:{index}",),
        )

    def waiting_request():
        with resource_slot("embedding", s.settings):
            entered.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(waiting_request)
            try:
                assert not entered.wait(0.2)
            finally:
                # Closing a crashed worker's connection must release its slots.
                blocker.close()
            assert entered.wait(5)
            future.result(timeout=5)
    finally:
        blocker.close()


def test_real_queue_processes_five_files_and_preserves_other_results(
    knowledge_system, tmp_path, monkeypatch
):
    from threading import Event, Lock
    from uuid import uuid4

    from final_review import material_jobs
    from final_review.material_conversion import ConvertedMaterial
    from final_review.pipeline_runtime import run_queue

    s = knowledge_system
    barrier = Barrier(5)
    lock = Lock()
    stop = Event()
    finished = []

    def convert(content, filename, max_bytes, **kwargs):
        barrier.wait(timeout=10)
        if filename == "file-2.md":
            raise ValueError("invalid file")
        return ConvertedMaterial(content.decode())

    monkeypatch.setattr(material_jobs, "convert_material", convert)
    for index in range(5):
        path = tmp_path / f"file-{index}.md"
        path.write_text(STATELESS, encoding="utf-8")
        doc_id, job_id = uuid4().hex, uuid4().hex
        s.store.create_material_job(
            dict(
                document_id=doc_id,
                course_id="course-1",
                user_id=str(USER),
                file_name=path.name,
                file_path=str(path),
                title=path.name,
                source_type="teacher_ppt",
                chapter="",
                parse_status="queued",
            ),
            dict(
                job_id=job_id,
                course_id="course-1",
                document_id=doc_id,
                idempotency_key=job_id,
                fingerprint=job_id,
            ),
        )

    def process_file(job):
        store = PostgresStore(s.store.database_url)
        try:
            kb = KnowledgeBase(store, s.embeddings, s.settings)
            process_material_job(store, kb, job, 10 * 1024 * 1024)
        finally:
            store.close()
            with lock:
                finished.append(job["job_id"])
                if len(finished) == 5:
                    stop.set()

    run_queue(s.store.claim_material_job, process_file, 5, stop_event=stop, poll_seconds=0.01)
    rows = s.store.connection.execute(
        "SELECT status,count(*) AS n FROM material_jobs GROUP BY status"
    )
    assert {r["status"]: r["n"] for r in rows} == {"succeeded": 4, "failed": 1}
    assert (
        s.store.connection.execute("SELECT count(*) AS n FROM knowledge_build_jobs").fetchone()["n"]
        == 4
    )


def test_concurrent_same_course_builds_recheck_after_lock(knowledge_system, monkeypatch):
    s = knowledge_system
    parsed(s)
    parsed(s, PARAPHRASE, "second.pptx")
    repo = KnowledgeRepository(s.store)
    jobs = [repo.claim(), repo.claim()]
    barrier = Barrier(2)
    original = KnowledgeRepository.snapshot
    calls = set()

    def synchronized_snapshot(repository, course):
        snapshot = original(repository, course)
        key = id(repository)
        if key not in calls:
            calls.add(key)
            barrier.wait(timeout=15)
        return snapshot

    monkeypatch.setattr(KnowledgeRepository, "snapshot", synchronized_snapshot)

    def run(job, text):
        store = PostgresStore(s.store.database_url)
        try:
            return process_knowledge_job(
                store,
                KnowledgeModel([("HTTP", "HTTP无状态性", [text])]),
                s.embeddings,
                job,
                s.settings,
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(run, job, text) for job, text in zip(jobs, [STATELESS, PARAPHRASE])]
        assert all(f.result(timeout=30) for f in results)
    assert counts(s) == (1, 1, 2)


def test_atomic_publication_rolls_back_every_formal_write(knowledge_system, monkeypatch):
    s = knowledge_system
    parsed(s)
    repo = KnowledgeRepository(s.store)
    job = repo.claim()
    original = KnowledgeRepository._publish_candidate

    def crash_after_write(repository, job, entry):
        original(repository, job, entry)
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(KnowledgeRepository, "_publish_candidate", crash_after_write)
    assert not process_knowledge_job(
        s.store,
        KnowledgeModel([("HTTP", "HTTP无状态性", [STATELESS])]),
        s.embeddings,
        job,
        s.settings,
    )
    assert counts(s) == (0, 0, 0)
    assert repo.candidates(job)[0]["status"] == "pending"
    monkeypatch.setattr(KnowledgeRepository, "_publish_candidate", original)
    s.store.connection.execute("UPDATE knowledge_build_jobs SET available_at=now()")
    process(s, [], repo.claim())
    assert counts(s) == (1, 1, 1)


def test_expired_worker_cannot_publish_and_manual_retry(knowledge_system):
    s = knowledge_system
    parsed(s)
    repo = KnowledgeRepository(s.store)
    job = repo.claim()
    s.store.connection.execute(
        "UPDATE knowledge_build_jobs SET lease_until=now()-interval '1 second',max_attempts=1"
    )
    with pytest.raises(LeaseLost):
        repo.fence(job)
    assert repo.claim() is None
    assert repo.retry("course-1", job["job_id"])["status"] == "queued"
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    assert counts(s) == (1, 1, 1)


def test_deleted_material_stops_build_without_formal_writes(knowledge_system):
    s = knowledge_system
    document = parsed(s)
    s.store.connection.execute(
        "UPDATE documents SET data=data || %s WHERE document_id=%s",
        (Jsonb({"parse_status": "deleted"}), document),
    )
    repo = KnowledgeRepository(s.store)
    job = repo.claim()
    assert not process_knowledge_job(s.store, KnowledgeModel(), s.embeddings, job, s.settings)
    assert counts(s) == (0, 0, 0)
    row = s.store.connection.execute("SELECT status FROM knowledge_build_jobs").fetchone()
    assert row["status"] == "failed"


def test_conflicting_facts_wait_for_review(knowledge_system):
    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    opposite = "HTTP是有状态协议。"
    parsed(s, opposite, "conflict.pptx")
    job = process(s, [("HTTP", "HTTP无状态性", [opposite])])
    assert counts(s) == (1, 1, 1)
    assert KnowledgeRepository(s.store).candidates(job)[0]["status"] == "review_needed"


def test_foreign_course_fact_relationship_is_rejected(knowledge_system):
    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    s.store.put("course", "other", dict(course_id="other", user_id=str(USER), status="active"))
    point_id = s.store.connection.execute("SELECT id FROM knowledge_points").fetchone()["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        s.store.connection.execute(
            """INSERT INTO knowledge_facts(user_id,course_id,point_id,fact_text,normalized_text,
               fact_hash,status) VALUES (%s,'other',%s,'fact','fact','hash','pending')""",
            (USER, point_id),
        )


def test_user_account_purge_cascades_new_tables(knowledge_system):
    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    s.store.connection.execute("DELETE FROM app_users WHERE id=%s", (USER,))
    assert counts(s) == (0, 0, 0)


def test_upload_parse_build_full_chain_and_repeat_upload(knowledge_system, tmp_path):
    s = knowledge_system
    s.settings.auth_mode = "database"
    s.settings.auth_cookie_secure = False
    s.settings.api_token = SecretStr("")
    s.settings.uploads_dir = str(tmp_path)
    with TestClient(create_app(s.settings)) as client:
        # Existing seeded account uses a hash placeholder: mint its normal session directly.
        from final_review.auth import CurrentUser, DatabaseAuth

        auth = DatabaseAuth(s.settings)
        auth.setup()
        with auth.transaction() as cursor:
            session = auth._new_session(cursor, CurrentUser(str(USER)))
        client.cookies.set(s.settings.auth_cookie_name, session)
        try:
            payload = dict(course_id="course-1", title="HTTP", source_type="teacher_ppt")
            response = client.post(
                "/knowledge/upload",
                data=payload,
                files={"file": ("HTTP.md", STATELESS.encode())},
                headers={"Idempotency-Key": "knowledge-upload-proof"},
            )
            assert response.status_code == 202, response.text
            material_job = s.store.claim_material_job()
            process_material_job(s.store, s.kb, material_job, 10 * 1024 * 1024)
            assert s.store.get_material_job(material_job["job_id"])["status"] == "succeeded"
            # Simulate a file parsed before this feature: repeated upload must enqueue its build.
            s.store.connection.execute("DELETE FROM knowledge_build_jobs")
            reused = client.post(
                "/knowledge/upload", data=payload, files={"file": ("HTTP.md", STATELESS.encode())}
            )
            assert reused.json()["reused"] is True
            process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
            repeated = client.post(
                "/knowledge/upload",
                data=payload,
                files={"file": ("HTTP.md", STATELESS.encode())},
                headers={"Idempotency-Key": "knowledge-upload-proof"},
            )
            assert repeated.status_code == 202, repeated.text
            assert repeated.json()["document_id"] == response.json()["document_id"]
            assert counts(s) == (1, 1, 1)
            jobs = client.get("/api/courses/course-1/knowledge-build-jobs")
            assert jobs.status_code == 200
            assert len(jobs.json()["items"]) == 1
            status = client.get("/api/courses/course-1/knowledge-build-status")
            assert status.status_code == 200
            assert status.json()["items"][0]["status"] == "succeeded"
            assert status.json()["items"][0]["completed_blocks"] == 1
            ident = jobs.json()["items"][0]["job_id"]
            assert (
                client.get(
                    f"/api/courses/course-1/knowledge-build-jobs/{ident}/decisions"
                ).status_code
                == 200
            )
            assert client.get("/api/courses/course-1/knowledge-points").status_code == 200
            assert client.get("/api/courses/foreign/knowledge-points").status_code == 404
        finally:
            auth.close()
