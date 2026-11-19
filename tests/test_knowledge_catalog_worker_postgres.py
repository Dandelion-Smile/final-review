"""Worker + real repository proofs, exclusively on the disposable test database."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from test_course_knowledge_postgres import counts, knowledge_system  # noqa: F401
from test_knowledge_catalog_postgres import catalog  # noqa: F401
from test_knowledge_catalog_worker import CatalogModel
from test_postgres_migrations import database_url  # noqa: F401

from final_review.knowledge_catalog_contracts import BatchAssignments, TopicOverview
from final_review.knowledge_catalog_dispatch import DispatchConfig, DispatchRepository
from final_review.knowledge_catalog_store import CatalogLeaseLost
from final_review.knowledge_catalog_worker import InitialCatalogWorker, WorkerConfig, compact_points

pytestmark = pytest.mark.integration
COURSE = "course-1"


def claim_initial(repo):
    created = repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash=repo.snapshot(COURSE)["hash"]
    )
    job = repo.claim()
    assert job["id"] == created["id"] and job["course_id"] == COURSE
    return job


def add_synthetic_points(repo, count):
    # Build a large synthetic course using the existing verified source evidence.
    # This SQL executes only inside the disposable PostgreSQL fixture.
    source = repo.snapshot(COURSE)["points"][0]["point_id"]
    repo.connection.execute(
        """WITH points AS (
          INSERT INTO knowledge_points(record_key,user_id,course_id,data,name,
            normalized_name,description,embedding,importance_score,status)
          SELECT 'worker-point-'||i,p.user_id,p.course_id,'{}',p.name||i,
            p.normalized_name||i,p.description,p.embedding,p.importance_score,'active'
          FROM knowledge_points p CROSS JOIN generate_series(1,%s) i WHERE p.id=%s
          RETURNING id,user_id,course_id
        ), facts AS (
          INSERT INTO knowledge_facts(user_id,course_id,point_id,fact_text,
            normalized_text,fact_hash,status)
          SELECT p.user_id,p.course_id,p.id,f.fact_text,f.normalized_text,f.fact_hash,f.status
          FROM points p CROSS JOIN knowledge_facts f WHERE f.point_id=%s
          RETURNING id,user_id,course_id
        )
        INSERT INTO fact_evidence(user_id,course_id,fact_id,material_version_id,locator_id,
          quote,quote_hash,quote_verified,support_status,verification_method)
        SELECT f.user_id,f.course_id,f.id,e.material_version_id,e.locator_id,e.quote,
          e.quote_hash,e.quote_verified,e.support_status,e.verification_method
        FROM facts f CROSS JOIN fact_evidence e JOIN knowledge_facts original
          ON original.id=e.fact_id WHERE original.point_id=%s""",
        (count, source, source, source),
    )


def test_worker_publishes_v1_and_redelivery_is_idempotent(catalog):  # noqa: F811
    system, repo = catalog
    add_synthetic_points(repo, 581)
    before = counts(system)
    assert before == (582, 582, 582)
    job = claim_initial(repo)
    instance = InitialCatalogWorker(repo, CatalogModel())
    result = instance.run(job)
    assert result["status"] == "succeeded"
    current = repo.current(COURSE)
    assert current["head"]["revision"] == 1
    assert current["head"]["current_version_id"] == result["result_version_id"]
    version = repo.version(COURSE, result["result_version_id"])
    assert len(version["chapters"]) == 1 and len(version["assignments"]) == 582
    assert instance.calls == 31
    assert version["build_mode"] == "initial"
    assert version["assignments"][0]["assignment_method"] == "llm"
    assert counts(system) == before
    assert instance.run(job)["status"] == "succeeded"
    assert repo.current(COURSE)["head"]["revision"] == 1
    with pytest.raises(psycopg.errors.CheckViolation):
        system.store.connection.execute(
            "UPDATE course_knowledge_catalog_chapters SET title='changed' WHERE version_id=%s",
            (result["result_version_id"],),
        )


def test_worker_retry_wait_resumes_without_replanning(catalog):  # noqa: F811
    _, repo = catalog
    job = claim_initial(repo)

    def interrupted(schema, data):
        if schema is BatchAssignments:
            raise httpx.ConnectError("temporary")

    settings = WorkerConfig(call_attempts=1)
    result = InitialCatalogWorker(repo, CatalogModel(interrupted), config=settings).run(job)
    assert result["status"] == "retry_wait"
    assert repo.current(COURSE) == {"head": None, "version": None}
    # Re-schedule only this isolated test task; production scheduler is out of scope.
    repo.connection.execute(
        "UPDATE course_knowledge_catalog_jobs SET next_run_at=clock_timestamp() WHERE id=%s",
        (job["id"],),
    )
    reclaimed = repo.claim()
    assert reclaimed["attempt"] == 2
    model = CatalogModel()
    assert (
        InitialCatalogWorker(repo, model, config=settings).run(reclaimed)["status"] == "succeeded"
    )
    assert [name for name, _ in model.calls] == ["BatchAssignments"]


def test_expired_worker_cannot_write_or_publish_and_new_owner_recovers(catalog):  # noqa: F811
    system, repo = catalog
    job = claim_initial(repo)

    def expire(schema, data):
        repo.connection.execute(
            "UPDATE course_knowledge_catalog_jobs SET lease_until=%s WHERE id=%s",
            (datetime.now(UTC) - timedelta(seconds=1), job["id"]),
        )

    with pytest.raises(CatalogLeaseLost):
        InitialCatalogWorker(repo, CatalogModel(expire)).run(job)
    assert repo.current(COURSE) == {"head": None, "version": None}
    reclaimed = repo.claim()
    assert reclaimed["lease_token"] != job["lease_token"]
    with pytest.raises(CatalogLeaseLost):
        InitialCatalogWorker(repo, CatalogModel()).run(job)
    assert InitialCatalogWorker(repo, CatalogModel()).run(reclaimed)["status"] == "succeeded"
    assert counts(system) == (1, 1, 1)


def test_snapshot_mutated_during_model_call_cannot_publish(catalog):  # noqa: F811
    _, repo = catalog
    job = claim_initial(repo)

    def mutate(schema, data):
        if schema is BatchAssignments:
            repo.connection.execute("UPDATE knowledge_points SET description='changed'")

    result = InitialCatalogWorker(repo, CatalogModel(mutate)).run(job)
    assert result["status"] == "failed" and result["error_code"] == "CatalogSnapshotChanged"
    assert repo.current(COURSE) == {"head": None, "version": None}


def test_worker_publication_is_rolled_back_on_database_constraint_failure(catalog, monkeypatch):  # noqa: F811
    system, repo = catalog
    before = counts(system)
    publish = repo.publish
    job = claim_initial(repo)

    def invalid_publication(course, **kwargs):
        # Corrupt the DB reference after worker validation, exercising real DB rollback.
        kwargs["assignments"][0] = kwargs["assignments"][0].model_copy(update={"point_id": uuid4()})
        # Bypass Python validation only to force a DB foreign key error after inserts.
        monkeypatch.setattr(repo, "_validate", lambda *args: None)
        return publish(course, **kwargs)

    monkeypatch.setattr(repo, "publish", invalid_publication)
    result = InitialCatalogWorker(repo, CatalogModel()).run(job)
    assert result["status"] == "failed" and result["error_code"] == "ForeignKeyViolation"
    assert repo.current(COURSE) == {"head": None, "version": None}
    for table in ("versions", "chapters", "assignments"):
        assert (
            repo.connection.execute(
                f"SELECT count(*) AS n FROM course_knowledge_catalog_{table}"
            ).fetchone()["n"]
            == 0
        )
    assert counts(system) == before


def test_foreign_token_cannot_checkpoint(catalog):  # noqa: F811
    _, repo = catalog
    job = claim_initial(repo)
    stale = deepcopy(job)
    stale["lease_token"] = uuid4()
    with pytest.raises(CatalogLeaseLost):
        InitialCatalogWorker(repo, CatalogModel()).run(stale)
    assert repo.job(COURSE, job["id"])["checkpoint"] == {}


def test_108_points_corrected_overview_publishes_once(catalog):  # noqa: F811
    system, repo = catalog
    add_synthetic_points(repo, 107)
    repo.connection.execute(
        "UPDATE knowledge_points SET description=repeat('主题', 30) WHERE id IN "
        "(SELECT id FROM knowledge_points ORDER BY id LIMIT 37)"
    )
    points = compact_points(repo.snapshot(COURSE))
    first = points[:37]
    config = WorkerConfig(input_chars=2 * len(json.dumps(first)), call_attempts=1)
    dispatch = DispatchRepository(system.store, config=DispatchConfig(initial=config))

    def omit_once(schema, data):
        if schema is TopicOverview and data["points"] == first:
            if "coverage_errors" not in data:
                return {
                    "topics": [{"title": "主题", "point_ids": [p["point_id"] for p in first[:-1]]}]
                }
            assert data["coverage_errors"]["missing"] == {
                "count": 1,
                "ids": [first[-1]["point_id"]],
            }

    model = CatalogModel(omit_once)
    job = claim_initial(repo)
    result = InitialCatalogWorker(dispatch, model, config=config).run(job)
    assert result["status"] == "succeeded"
    assert [len(data["points"]) for name, data in model.calls if name == "TopicOverview"] == [
        37,
        37,
        40,
        31,
    ]
    cp = repo.job(COURSE, job["id"])["checkpoint"]
    assert cp["initial_calls_reserved"] == cp["worker"]["calls_reserved"] == len(model.calls) == 8
    assert repo.head(COURSE)["revision"] == 1
    version = repo.version(COURSE, result["result_version_id"])
    assert {str(a["point_id"]) for a in version["assignments"]} == {p["point_id"] for p in points}


@pytest.mark.parametrize("legacy_reservations, code", [(1, None), (50, "CatalogBudgetExceeded")])
def test_dispatch_resumes_legacy_budget_conservatively(catalog, legacy_reservations, code):  # noqa: F811
    from psycopg.types.json import Jsonb

    system, repo = catalog
    job = claim_initial(repo)
    dispatch = DispatchRepository(system.store)
    failed = InitialCatalogWorker(dispatch, CatalogModel(lambda *_: {})).run(job)
    assert failed["status"] == "failed"
    cp = repo.job(COURSE, job["id"])["checkpoint"]
    cp["worker"].pop("calls_reserved")
    cp["initial_calls_reserved"] = legacy_reservations
    repo.connection.execute(
        "UPDATE course_knowledge_catalog_jobs SET status='queued',checkpoint=%s WHERE id=%s",
        (Jsonb(cp), job["id"]),
    )
    model = CatalogModel()
    result = InitialCatalogWorker(dispatch, model).run(repo.claim())
    assert result["error_code"] == code
    assert len(model.calls) == (0 if code else 2)
    reserved = repo.job(COURSE, job["id"])["checkpoint"]["initial_calls_reserved"]
    assert reserved == 2 * legacy_reservations + len(model.calls)
