"""Real PG, isolated TEST_DATABASE_URL only; deterministic catalog models."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import httpx
import psycopg
import pytest
from openai import RateLimitError
from test_course_knowledge_postgres import knowledge_system, parsed, process  # noqa: F401
from test_knowledge_catalog_worker import CatalogModel
from test_postgres_migrations import USER, database_url  # noqa: F401

from final_review.knowledge_catalog_dispatch import (
    CatalogDispatcher,
    DispatchConfig,
    DispatchRepository,
)
from final_review.knowledge_catalog_scheduler import CatalogScheduler, CatalogService
from final_review.knowledge_catalog_store import CatalogLeaseLost, CatalogRepository
from final_review.knowledge_catalog_worker import WorkerConfig
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration


def test_discovery_success_history_empty_and_no_model(knowledge_system):  # noqa: F811
    s = knowledge_system
    scheduler = CatalogScheduler(s.store, page_size=1)
    assert list(scheduler.discover()) == []
    parsed(s)
    # Knowledge still queued / not committed: no visible points.
    assert list(scheduler.discover()) == []
    process(s, [("HTTP", "HTTP无状态性", ["HTTP是无状态协议。"])])
    changes = list(scheduler.discover(successes_only=True))
    assert len(changes) == 1 and changes[0].mode == "initial"
    s.store.connection.execute("UPDATE knowledge_build_jobs SET status='failed'")
    assert list(scheduler.discover(successes_only=True)) == []
    assert len(list(scheduler.discover())) == 1
    assert (
        s.store.connection.execute(
            "SELECT count(*) AS n FROM course_knowledge_catalog_jobs"
        ).fetchone()["n"]
        == 0
    )


def test_discovery_skips_deleting_owner(knowledge_system):  # noqa: F811
    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", ["HTTP是无状态协议。"])])
    s.store.connection.execute("UPDATE app_users SET deletion_due_at=now()")
    assert list(CatalogScheduler(s.store).discover()) == []


@pytest.fixture
def pending(knowledge_system):  # noqa: F811
    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", ["HTTP是无状态协议。"])])
    now = [datetime.now(UTC) - timedelta(hours=1)]
    scheduler = CatalogScheduler(s.store, clock=lambda: now[0])
    return s, scheduler, now


def mutate(s):
    s.store.connection.execute("UPDATE knowledge_points SET description='changed'")


def test_window_persists_restarts_and_claim_respects_due(pending):
    s, _, _ = pending
    scheduler = CatalogScheduler(s.store)
    job = scheduler.reconcile("course-1")
    assert job["stage"] == "collecting"
    assert CatalogRepository(s.store).claim() is None
    again = CatalogScheduler(s.store).reconcile("course-1")
    assert again["id"] == job["id"]
    assert again["next_run_at"] == job["next_run_at"]


def test_concurrent_scanners_one_live_task(pending):
    s, _, _ = pending
    barrier = Barrier(2)

    def scan():
        store = PostgresStore(s.store.database_url)
        token = store.bind_user(str(USER))
        try:
            barrier.wait()
            return CatalogScheduler(store).reconcile("course-1")["id"]
        finally:
            store.reset_user(token)
            store.close()

    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: scan(), range(2)))
    assert ids[0] == ids[1]


def test_snapshot_replacement_is_atomic_and_inherits_elapsed_window(pending):
    s, scheduler, now = pending
    old = scheduler.reconcile("course-1")
    mutate(s)
    now[0] += timedelta(seconds=20)
    updated = scheduler.reconcile("course-1")
    assert updated["snapshot_hash"] == old["snapshot_hash"]
    now[0] += timedelta(seconds=61)
    claimed = scheduler.repo.claim()
    assert scheduler.prepare_claim(claimed) is None
    old = scheduler.repo.job("course-1", old["id"])
    assert old["error_code"] == "SupersededBeforeExecution"
    successor = scheduler.repo.claim()
    assert successor["snapshot_hash"] != old["snapshot_hash"]
    assert (
        successor["checkpoint"]["schedule"]["first_pending_at"]
        == (old["checkpoint"]["schedule"]["first_pending_at"])
    )
    assert scheduler.prepare_claim(successor)["id"] == successor["id"]


def test_replacement_rollback_keeps_original_lease(pending, monkeypatch):
    s, scheduler, now = pending
    original = scheduler.reconcile("course-1")
    mutate(s)
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    claimed = scheduler.repo.claim()

    def crash(*args, **kwargs):
        raise RuntimeError("crash before successor insert")

    monkeypatch.setattr(scheduler.repo, "create_job", crash)
    with pytest.raises(RuntimeError):
        scheduler.prepare_claim(claimed)
    stored = scheduler.repo.job("course-1", original["id"])
    assert stored["status"] == "running" and stored["lease_token"] == claimed["lease_token"]


def test_only_pristine_superseded_identity_can_revive(pending):
    s, scheduler, now = pending
    original = scheduler.reconcile("course-1")
    before = s.store.connection.execute("SELECT description FROM knowledge_points").fetchone()
    mutate(s)
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    scheduler.prepare_claim(scheduler.repo.claim())
    newer = scheduler.repo.claim()
    scheduler.repo.fail(
        "course-1", newer["id"], newer["lease_token"], error_code="Permanent", error_message="test"
    )
    s.store.connection.execute(
        "UPDATE knowledge_points SET description=%s", (before["description"],)
    )
    revived = scheduler.reconcile("course-1")
    assert revived["id"] == original["id"] and revived["status"] == "queued"
    assert revived["attempt"] == 0
    # An ordinary failed input is never reactivated.
    mutate(s)
    now[0] += timedelta(seconds=61)
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    scheduler.prepare_claim(scheduler.repo.claim())
    assert scheduler.repo.claim() is None


class AutomaticModel(CatalogModel):
    def structured(self, schema, task, data, **kwargs):
        if schema.__name__ == "ExistingMatches":
            self.calls.append((schema.__name__, data))
            return {
                "matches": [
                    dict(
                        point_id=p["point_id"],
                        status="matched",
                        chapter_key=data["chapters"][0]["key"],
                        reason="same topic",
                    )
                    for p in data["points"]
                ]
            }
        return super().structured(schema, task, data, **kwargs)


def run_pending(s, scheduler, now, models):
    runner = CatalogDispatcher(s.store, lambda: models.append(AutomaticModel()) or models[-1])
    runner.scheduler = scheduler
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    for _ in range(3):
        claimed = runner.claim()
        if claimed:
            result = runner.run(claimed)
            if result:
                return result
    raise AssertionError("no executed job")


def test_real_workers_publish_initial_then_incremental_with_envelopes(pending):
    s, scheduler, now = pending
    models = []
    initial = run_pending(s, scheduler, now, models)
    assert initial["status"] == "succeeded"
    assert initial["checkpoint"]["worker"]["schema_version"] == "catalog-checkpoint-v1"
    mutate(s)
    incremental = run_pending(s, scheduler, now, models)
    assert incremental["status"] == "succeeded", incremental
    assert incremental["checkpoint"]["worker"]["schema_version"] == (
        "catalog-incremental-checkpoint-v1"
    )
    assert scheduler.repo.current("course-1")["head"]["revision"] == 2
    assert len(models) == 2 and models[0] is not models[1]


def test_baseline_atomic_restart_and_duplicate_evidence_no_model(pending):
    s, scheduler, now = pending
    models = []
    initial = run_pending(s, scheduler, now, models)
    version = scheduler.repo.current("course-1")["version"]
    adapter = DispatchRepository(s.store)
    baseline = adapter.baseline("course-1", version)
    assert baseline.version_id == str(initial["result_version_id"])
    # Source metadata changes full snapshot identity but not semantic input.
    s.store.connection.execute('UPDATE documents SET data=data || \'{"title":"duplicate.pptx"}\'')
    incremental = run_pending(s, scheduler, now, models)
    assert incremental["status"] == "succeeded"
    assert models[-1].calls == []
    assert incremental["checkpoint"]["semantic_baseline"]["version_id"] == str(
        incremental["result_version_id"]
    )
    store = PostgresStore(s.store.database_url)
    token = store.bind_user(str(USER))
    try:
        repo = DispatchRepository(store)
        restored = repo.baseline("course-1", repo.current("course-1")["version"])
        assert restored.point_hashes == baseline.point_hashes
    finally:
        store.reset_user(token)
        store.close()


@pytest.mark.parametrize("corrupt", ["missing", "rules", "version", "snapshot", "points"])
def test_baseline_missing_or_incompatible_is_conservative(pending, corrupt):
    s, scheduler, now = pending
    models = []
    result = run_pending(s, scheduler, now, models)
    from psycopg.types.json import Jsonb

    cp = result["checkpoint"]
    if corrupt == "missing":
        cp.pop("semantic_baseline")
    elif corrupt == "rules":
        cp["semantic_rules_version"] = "other-policy"
    elif corrupt == "version":
        cp["semantic_baseline"]["version_id"] = "wrong"
    elif corrupt == "snapshot":
        cp["semantic_baseline"]["snapshot_hash"] = "wrong"
    else:
        cp["semantic_baseline"]["point_hashes"] = {}
    s.store.connection.execute(
        "UPDATE course_knowledge_catalog_jobs SET checkpoint=%s", (Jsonb(cp),)
    )
    version = scheduler.repo.current("course-1")["version"]
    assert DispatchRepository(s.store).baseline("course-1", version) is None
    s.store.connection.execute('UPDATE documents SET data=data || \'{"title":"more evidence"}\'')
    result = run_pending(s, scheduler, now, models)
    assert result["status"] == "succeeded"
    assert len(models[-1].calls) == 1


def test_publication_and_baseline_roll_back_together(pending, monkeypatch):
    s, scheduler, now = pending
    original = DispatchRepository.publish

    def crash(self, *args, **kwargs):
        with self.store.transaction():
            original(self, *args, **kwargs)
            raise RuntimeError("crash after baseline write before commit")

    monkeypatch.setattr(DispatchRepository, "publish", crash)
    result = run_pending(s, scheduler, now, [])
    assert result["status"] == "failed"
    assert scheduler.repo.current("course-1")["version"] is None
    assert "semantic_baseline" not in result["checkpoint"]


def test_prepublication_snapshot_conflict_then_latest_initial_compensation(pending):
    s, scheduler, now = pending
    fired = []

    def change(schema, data):
        if schema.__name__ == "BatchAssignments" and not fired:
            fired.append(True)
            mutate(s)

    runner = CatalogDispatcher(s.store, lambda: CatalogModel(hook=change))
    runner.scheduler = scheduler
    first = scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    failed = runner.run(runner.claim())
    assert failed["error_code"] == "CatalogSnapshotChanged"
    assert scheduler.repo.current("course-1")["version"] is None
    new = scheduler.reconcile("course-1")
    assert new["id"] != first["id"] and new["mode"] == "initial"
    assert run_pending(s, scheduler, now, [])["status"] == "succeeded"


def test_running_input_change_stays_serial_then_compensates(pending):
    s, scheduler, now = pending
    original = run_pending(s, scheduler, now, [])
    mutate(s)
    first = scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    claimed = scheduler.repo.claim()
    s.store.connection.execute("UPDATE knowledge_points SET description='newer'")
    assert scheduler.reconcile("course-1")["id"] == first["id"]
    runner = CatalogDispatcher(s.store, AutomaticModel)
    runner.scheduler = scheduler
    # It is still collecting: safely replace before any model calls.
    runner.run(claimed)
    now[0] += timedelta(seconds=61)
    runner.run(scheduler.repo.claim())
    next_job = scheduler.repo.claim()
    assert next_job["id"] != first["id"]
    assert runner.run(next_job)["status"] == "succeeded"
    assert scheduler.repo.current("course-1")["head"]["revision"] == 2
    assert original["result_version_id"] != scheduler.repo.current("course-1")["version"]["id"]


def test_expired_owner_cannot_checkpoint_publish_or_fail_and_new_owner_recovers(pending):
    s, scheduler, now = pending
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    old = scheduler.repo.claim()
    adapter = DispatchRepository(s.store)
    adapter.checkpoint("course-1", old["id"], old["lease_token"], stage="starting", checkpoint={})
    s.store.connection.execute(
        "UPDATE course_knowledge_catalog_jobs SET lease_until=now()-interval '1 second'"
    )
    fresh = scheduler.repo.claim()
    assert fresh["lease_token"] != old["lease_token"]
    for action in (
        lambda: adapter.checkpoint(
            "course-1", old["id"], old["lease_token"], stage="stale", checkpoint={}
        ),
        lambda: adapter.fail(
            "course-1", old["id"], old["lease_token"], error_code="Stale", error_message="stale"
        ),
    ):
        with pytest.raises(CatalogLeaseLost):
            action()
    from test_knowledge_catalog_postgres import proposal

    with pytest.raises(CatalogLeaseLost):
        adapter.publish(
            "course-1", **proposal(scheduler.repo), job_id=old["id"], lease_token=old["lease_token"]
        )
    runner = CatalogDispatcher(s.store, AutomaticModel)
    assert runner.run(fresh)["status"] == "succeeded"


def rate_limit(message):
    return RateLimitError(
        message,
        response=httpx.Response(429, request=httpx.Request("POST", "https://test")),
        body=None,
    )


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, psycopg.errors.SerializationFailure, rate_limit]
)
def test_transient_errors_bounded_backoff_and_terminal_idempotency(pending, error):
    s, scheduler, now = pending

    def fail(schema, data):
        raise error("temporary")

    runner = CatalogDispatcher(
        s.store,
        lambda: CatalogModel(hook=fail),
        config=DispatchConfig(initial=WorkerConfig(call_attempts=1)),
    )
    runner.scheduler = scheduler
    first = scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    for attempt in range(1, 4):
        result = runner.run(runner.claim())
        assert result["status"] == ("retry_wait" if attempt < 3 else "failed")
        if attempt < 3:
            assert 20 * attempt <= (result["next_run_at"] - datetime.now(UTC)).total_seconds()
            s.store.connection.execute("UPDATE course_knowledge_catalog_jobs SET next_run_at=now()")
    assert result["checkpoint"]["initial_calls_reserved"] == 3
    assert scheduler.reconcile("course-1")["id"] == first["id"]
    assert runner.claim() is None


def test_budget_and_invalid_output_are_terminal(pending):
    s, scheduler, now = pending
    runner = CatalogDispatcher(
        s.store, AutomaticModel, config=DispatchConfig(initial=WorkerConfig(max_calls=1))
    )
    runner.scheduler = scheduler
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    result = runner.run(runner.claim())
    assert result["status"] == "failed" and result["error_code"] == "CatalogBudgetExceeded"
    assert result["checkpoint"]["initial_calls_reserved"] == 1
    mutate(s)
    runner.model_factory = lambda: CatalogModel(hook=lambda schema, data: {})
    runner.config = DispatchConfig()
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    result = runner.run(runner.claim())
    assert result["error_code"] == "CatalogOutputInvalid"


def test_no_changes_releases_active_slot_without_fake_publication(pending):
    s, scheduler, now = pending
    result = run_pending(s, scheduler, now, [])
    version = scheduler.repo.current("course-1")["version"]
    scheduler.repo.create_job(
        "course-1",
        mode="incremental",
        base_version_id=version["id"],
        snapshot_hash=version["knowledge_snapshot_hash"],
    )
    unchanged = CatalogDispatcher(s.store, AutomaticModel).run(scheduler.repo.claim())
    assert unchanged["status"] == "failed" and unchanged["stage"] == "no_changes"
    assert unchanged["error_code"] == "CatalogNoChanges" and unchanged["result_version_id"] is None
    assert scheduler.repo.current("course-1")["version"]["id"] == result["result_version_id"]
    assert scheduler.repo.claim() is None


def test_deleting_source_removes_all_assignments_without_model(pending):
    s, scheduler, now = pending
    run_pending(s, scheduler, now, [])
    s.store.connection.execute('UPDATE documents SET data=data || \'{"parse_status":"deleted"}\'')
    models = []
    result = run_pending(s, scheduler, now, models)
    assert result["status"] == "succeeded" and models[-1].calls == []
    assert scheduler.repo.current("course-1")["version"]["assignments"] == []


@pytest.mark.parametrize("invalid", ["owner", "course"])
def test_invalidation_during_model_never_publishes(pending, invalid):
    s, scheduler, now = pending

    def invalidate(schema, data):
        if invalid == "owner":
            s.store.connection.execute("UPDATE app_users SET deletion_due_at=now()")
        else:
            s.store.connection.execute("UPDATE courses SET status='deleted'")

    runner = CatalogDispatcher(s.store, lambda: CatalogModel(hook=invalidate))
    runner.scheduler = scheduler
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    result = runner.run(runner.claim())
    assert result["status"] == "failed" and result["error_code"] == "CatalogInvalid"
    assert scheduler.repo.current("course-1")["version"] is None


def test_concurrent_claimers_cannot_execute_same_lease(pending):
    s, scheduler, now = pending
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    barrier = Barrier(2)

    def claim():
        store = PostgresStore(s.store.database_url)
        try:
            barrier.wait()
            return CatalogDispatcher(store, AutomaticModel).claim()
        finally:
            store.close()

    with ThreadPoolExecutor(2) as pool:
        jobs = list(pool.map(lambda _: claim(), range(2)))
    assert sum(j is not None for j in jobs) == 1
    runner = CatalogDispatcher(s.store, AutomaticModel)
    runner.scheduler = scheduler
    assert runner.run(next(j for j in jobs if j))["status"] == "succeeded"


def test_real_multiuser_dispatch_isolation(pending):
    s, scheduler, now = pending
    from test_knowledge_catalog_postgres import other_scopes
    from test_postgres_migrations import SECOND_USER

    other_scopes(s.store)
    token = s.store.bind_user(str(SECOND_USER))
    try:
        parsed(s, title="second.pptx")
        process(s, [("HTTP", "HTTP无状态性", ["HTTP是无状态协议。"])])
    finally:
        s.store.reset_user(token)
    assert scheduler.scan() == 2
    now[0] += timedelta(seconds=61)
    runner = CatalogDispatcher(s.store, AutomaticModel)
    runner.scheduler = scheduler
    users = set()
    for _ in range(2):
        job = runner.claim()
        users.add(str(job["user_id"]))
        result = runner.run(job)
        assert result["status"] == "succeeded" and result["user_id"] == job["user_id"]
        assert s.store._user() == str(USER)
    assert users == {str(USER), str(SECOND_USER)}


@pytest.mark.parametrize("invalid", ["owner", "course"])
def test_invalidated_before_claim_ends_without_model(pending, invalid):
    s, scheduler, now = pending
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    s.store.connection.execute(
        "UPDATE app_users SET deletion_due_at=now()"
        if invalid == "owner"
        else "UPDATE courses SET status='deleted'"
    )

    def forbidden_model():
        raise AssertionError("unavailable scope must not construct a model")

    runner = CatalogDispatcher(s.store, forbidden_model)
    result = runner.run(runner.claim())
    assert result["status"] == "failed" and result["error_code"] == "CatalogInvalid"


def test_automatic_growth_publishes_new_virtualization_chapter(pending):
    from test_knowledge_catalog_growth_postgres import add_virtualization, seed_v1
    from test_knowledge_catalog_incremental import GrowthModel

    s, scheduler, now = pending
    original = seed_v1(scheduler.repo)
    add_virtualization(s)
    runner = CatalogDispatcher(s.store, GrowthModel)
    runner.scheduler = scheduler
    queued = scheduler.reconcile("course-1")
    assert queued["mode"] == "incremental" and queued["base_version_id"] == original["id"]
    now[0] += timedelta(seconds=61)
    assert runner.run(runner.claim())["status"] == "succeeded"
    version = scheduler.repo.current("course-1")["version"]
    assert len(version["chapters"]) == 2 and len(version["assignments"]) == 4
    assert version["chapters"][0]["chapter_id"] == original["chapters"][0]["chapter_id"]


def test_database_transient_and_checkpoint_restart_with_no_inference_transaction(
    pending, monkeypatch
):
    from psycopg.pq import TransactionStatus

    s, scheduler, now = pending
    checkpoint = DispatchRepository.checkpoint
    fired = []

    def transient(self, *args, **kwargs):
        if kwargs["stage"] == "model_returned" and not fired:
            fired.append(True)
            self.connection.execute(
                "DO $$ BEGIN RAISE EXCEPTION 'test serialization' USING ERRCODE='40001'; END $$"
            )
        return checkpoint(self, *args, **kwargs)

    def idle(schema, data):
        assert s.store.connection.info.transaction_status == TransactionStatus.IDLE

    monkeypatch.setattr(DispatchRepository, "checkpoint", transient)
    runner = CatalogDispatcher(s.store, lambda: CatalogModel(hook=idle))
    runner.scheduler = scheduler
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    result = runner.run(runner.claim())
    assert result["status"] == "retry_wait" and result["error_code"] == "SerializationFailure"
    s.store.connection.execute("UPDATE course_knowledge_catalog_jobs SET next_run_at=now()")
    # A new dispatcher instance consumes persisted envelope and reservations.
    restarted = CatalogDispatcher(s.store, lambda: CatalogModel(hook=idle))
    assert restarted.run(restarted.claim())["status"] == "succeeded"


def test_running_knowledge_does_not_start_catalog(pending):
    s, scheduler, now = pending
    job = scheduler.reconcile("course-1")
    s.store.connection.execute("UPDATE knowledge_build_jobs SET status='running'")
    assert list(scheduler.discover()) == []
    now[0] += timedelta(seconds=61)
    assert scheduler.prepare_claim(scheduler.repo.claim()) is None
    stored = scheduler.repo.job("course-1", job["id"])
    assert stored["status"] == "queued" and stored["checkpoint"]["worker"] == {}


def service_for(s, **changes):
    return CatalogService(
        s.settings.model_copy(
            update={"catalog_scheduler_enabled": True, "catalog_max_concurrency": 1, **changes}
        ),
        AutomaticModel,
    )


def test_service_thread_queue_initial_incremental_and_restart(pending):
    s, scheduler, now = pending
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    service = service_for(s)
    try:
        service.run(once=True)
    finally:
        service.close()
    assert scheduler.repo.current("course-1")["head"]["revision"] == 1
    mutate(s)
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    # New service, new thread, persisted windows and semantic baseline.
    restarted = service_for(s)
    try:
        restarted.run(once=True)
    finally:
        restarted.close()
    assert scheduler.repo.current("course-1")["head"]["revision"] == 2


def test_service_global_concurrency_slots_across_instances(pending):
    s, scheduler, now = pending
    scheduler.reconcile("course-1")
    now[0] += timedelta(seconds=61)
    first, second = service_for(s), service_for(s)
    try:
        work = first.claim()
        assert work is not None
        assert second.claim() is None
        assert first.process(work)["status"] == "succeeded"
        # Session close releases capacity for another instance.
        slot = second.try_slot()
        assert slot is not None
        slot.close()
    finally:
        first.close()
        second.close()


def test_recent_success_filter_never_replaces_historical_reconciliation(pending):
    s, scheduler, now = pending
    assert (
        list(scheduler.courses(successes_only=True, since=datetime.now(UTC) + timedelta(days=1)))
        == []
    )
    assert len(list(scheduler.courses())) == 1


def test_enabled_service_checks_schema_without_applying_migration(knowledge_system):  # noqa: F811
    s = knowledge_system
    # Disposable test schema only: simulate business environment missing 022.
    s.store.connection.execute(
        "ALTER TABLE course_knowledge_catalog_jobs RENAME TO catalog_jobs_not_deployed"
    )
    service = service_for(s)
    try:
        with pytest.raises(RuntimeError, match="目录表尚未部署"):
            service.run(once=True)
        assert (
            s.store.connection.execute(
                "SELECT to_regclass('public.course_knowledge_catalog_jobs') AS t"
            ).fetchone()["t"]
            is None
        )
    finally:
        service.close()
