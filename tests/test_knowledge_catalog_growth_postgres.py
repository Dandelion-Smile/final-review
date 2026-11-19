"""Real growth publication/fencing proofs on the existing disposable-DB fixture."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from test_course_knowledge_postgres import knowledge_system, parsed, process  # noqa: F401
from test_knowledge_catalog_incremental import GrowthModel
from test_knowledge_catalog_postgres import COURSE, catalog, proposal  # noqa: F401
from test_postgres_migrations import USER, database_url  # noqa: F401

from final_review.knowledge_catalog_incremental import IncrementalCatalogWorker, IncrementalConfig
from final_review.knowledge_catalog_incremental_contracts import ExistingMatches, TopicReview
from final_review.knowledge_catalog_store import (
    CatalogConflict,
    CatalogInvalid,
    CatalogLeaseLost,
    CatalogRepository,
)
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration


def seed_v1(repo):
    args = proposal(repo)
    args["chapters"][0] = args["chapters"][0].model_copy(update={"title": "Docker 基础"})
    result = repo.publish(COURSE, **args)
    return repo.version(COURSE, result["version_id"])


def add_virtualization(system, *, title="virtualization.pptx"):
    facts = ["Hypervisor管理虚拟机。", "KVM提供虚拟化支持。", "全虚拟化模拟完整硬件。"]
    parsed(system, "\n".join(facts), title)
    process(system, [(f"KVM-{i}", f"虚拟化原理{i}", [fact]) for i, fact in enumerate(facts)])


def claim_incremental(repo):
    current = repo.current(COURSE)
    created = repo.create_job(
        COURSE,
        mode="incremental",
        base_version_id=current["version"]["id"],
        snapshot_hash=repo.snapshot(COURSE)["hash"],
    )
    job = repo.claim()
    # claim is user-scoped, never assume it targeted our newly created task.
    assert job is not None and job["id"] == created["id"]
    assert job["course_id"] == COURSE and job["mode"] == "incremental"
    return job


def version_count(repo):
    return repo.connection.execute(
        "SELECT count(*) AS n FROM course_knowledge_catalog_versions"
    ).fetchone()["n"]


def test_growth_v1_v2_preserves_history_uuids_and_complete_coverage(catalog):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)
    job = claim_incremental(repo)
    worker = IncrementalCatalogWorker(repo, GrowthModel())
    assert worker.run(job)["status"] == "succeeded"
    v2 = repo.current(COURSE)["version"]
    assert v2["parent_version_id"] == v1["id"] and v2["build_mode"] == "incremental"
    assert len(v2["chapters"]) == 2 and len(v2["assignments"]) == 4
    for key in ("chapter_id", "title", "description", "sort_order"):
        assert v2["chapters"][0][key] == v1["chapters"][0][key]
    assert v2["chapters"][1]["sort_order"] == 1
    assert v2["chapters"][0]["chapter_id"] != v2["chapters"][1]["chapter_id"]
    assert {str(a["point_id"]) for a in v2["assignments"]} == {
        p["point_id"] for p in repo.snapshot(COURSE)["points"]
    }
    assert sum(a["assignment_method"] == "carried" for a in v2["assignments"]) == 1
    assert repo.version(COURSE, v1["id"]) == v1
    assert worker.calls == 4
    assert worker.run(job)["status"] == "succeeded" and version_count(repo) == 2
    repeated = repo.create_job(
        COURSE, mode="incremental", base_version_id=v1["id"], snapshot_hash=job["snapshot_hash"]
    )
    assert repeated["id"] == job["id"] and repeated["status"] == "succeeded"


def test_source_only_change_uses_external_sidecar_without_model_calls(catalog):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)
    first = IncrementalCatalogWorker(repo, GrowthModel())
    assert first.run(claim_incremental(repo))["status"] == "succeeded"
    baseline = first.semantic_baseline
    previous_hash = repo.snapshot(COURSE)["hash"]
    # Source title is part of the complete input, but not the semantic projection.
    repo.connection.execute("UPDATE documents SET data=jsonb_set(data,'{title}','\"renamed\"')")
    assert repo.snapshot(COURSE)["hash"] != previous_hash
    model = GrowthModel()
    second = IncrementalCatalogWorker(repo, model)
    assert second.run(claim_incremental(repo), semantic_baseline=baseline)["status"] == "succeeded"
    assert not model.calls
    assert all(
        a["assignment_method"] == "carried" for a in repo.current(COURSE)["version"]["assignments"]
    )
    assert repo.version(COURSE, v1["id"]) == v1


@pytest.mark.parametrize("empty", [False, True])
def test_inactive_points_are_removed_even_when_snapshot_becomes_empty(catalog, empty):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    if not empty:
        add_virtualization(system)
    repo.connection.execute(
        "UPDATE knowledge_points SET status='inactive' WHERE id=%s",
        (v1["assignments"][0]["point_id"],),
    )
    worker = IncrementalCatalogWorker(repo, GrowthModel())
    assert worker.run(claim_incremental(repo))["status"] == "succeeded"
    current = repo.current(COURSE)["version"]
    assert current["point_count"] == (0 if empty else 3)
    assert v1["assignments"][0]["point_id"] not in {a["point_id"] for a in current["assignments"]}
    assert repo.version(COURSE, v1["id"]) == v1


def test_snapshot_mutated_during_model_never_publishes(catalog):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)

    def mutate(schema, data):
        if schema is ExistingMatches:
            repo.connection.execute("UPDATE knowledge_points SET description='changed'")

    result = IncrementalCatalogWorker(repo, GrowthModel(hook=mutate)).run(claim_incremental(repo))
    assert result["status"] == "failed" and result["error_code"] == "CatalogSnapshotChanged"
    assert version_count(repo) == 1 and repo.current(COURSE)["version"] == v1


def test_base_changed_during_model_is_terminal_conflict(catalog):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)
    switched = []

    def change_base(schema, data):
        if not switched:
            args = proposal(
                repo,
                base=v1["id"],
                revision=1,
                mode="incremental",
                chapter_id=v1["chapters"][0]["chapter_id"],
            )
            args["chapters"][0] = args["chapters"][0].model_copy(update={"title": "Docker 基础"})
            switched.append(repo.publish(COURSE, **args))

    result = IncrementalCatalogWorker(repo, GrowthModel(hook=change_base)).run(
        claim_incremental(repo)
    )
    assert result["status"] == "failed" and result["error_code"] == "CatalogConflict"
    assert repo.current(COURSE)["version"]["id"] == switched[0]["version_id"]
    assert repo.version(COURSE, v1["id"]) == v1


def test_lease_loss_reclaim_and_checkpoint_recovery(catalog):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)
    job = claim_incremental(repo)

    def expire(schema, data):
        repo.connection.execute(
            "UPDATE course_knowledge_catalog_jobs "
            "SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (job["id"],),
        )

    with pytest.raises(CatalogLeaseLost):
        IncrementalCatalogWorker(repo, GrowthModel(hook=expire)).run(job)
    assert repo.current(COURSE)["version"] == v1
    reclaimed = repo.claim()
    assert reclaimed["id"] == job["id"] and reclaimed["lease_token"] != job["lease_token"]
    with pytest.raises(CatalogLeaseLost):
        IncrementalCatalogWorker(repo, GrowthModel()).run(job)
    worker = IncrementalCatalogWorker(repo, GrowthModel())
    assert worker.run(reclaimed)["status"] == "succeeded" and worker.calls == 5


def test_retry_wait_preserves_call_budget_and_completed_model_results(catalog):  # noqa: F811
    system, repo = catalog
    seed_v1(repo)
    add_virtualization(system)
    job = claim_incremental(repo)

    def interrupt(schema, data):
        if schema is TopicReview:
            raise httpx.ConnectError("transient")

    config = IncrementalConfig(call_attempts=1)
    first = IncrementalCatalogWorker(repo, GrowthModel(hook=interrupt), config=config).run(job)
    assert first["status"] == "retry_wait" and first["checkpoint"]["logical_calls"] == 3
    repo.connection.execute(
        "UPDATE course_knowledge_catalog_jobs SET next_run_at=clock_timestamp() WHERE id=%s",
        (job["id"],),
    )
    model = GrowthModel()
    worker = IncrementalCatalogWorker(repo, model, config=config)
    assert worker.run(repo.claim())["status"] == "succeeded"
    assert worker.calls == 5 and [s for s, _ in model.calls] == ["TopicReview", "ExistingMatches"]


def test_foreign_key_failure_after_inserts_rolls_back_entire_v2(catalog, monkeypatch):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)
    publish = repo.publish

    def bad_reference(course, **kwargs):
        kwargs["assignments"][0] = kwargs["assignments"][0].model_copy(
            update={"chapter_id": uuid4()}
        )
        # Exercise the real DB FK after inserts, beyond worker + Repository checks.
        monkeypatch.setattr(repo, "_validate", lambda *args: None)
        return publish(course, **kwargs)

    monkeypatch.setattr(repo, "publish", bad_reference)
    result = IncrementalCatalogWorker(repo, GrowthModel()).run(claim_incremental(repo))
    assert result["status"] == "failed" and result["error_code"] == "ForeignKeyViolation"
    assert version_count(repo) == 1 and repo.current(COURSE)["version"] == v1
    assert (
        repo.connection.execute(
            "SELECT count(*) AS n FROM course_knowledge_catalog_chapters"
        ).fetchone()["n"]
        == 1
    )
    assert (
        repo.connection.execute(
            "SELECT count(*) AS n FROM course_knowledge_catalog_assignments"
        ).fetchone()["n"]
        == 1
    )


def test_lease_expires_inside_publish_rolls_back_v2(catalog, monkeypatch):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)
    job = claim_incremental(repo)
    repo.connection.execute("""
        CREATE FUNCTION delay_growth_switch() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_sleep(1.1); RETURN NEW; END $$;
        CREATE TRIGGER delay_growth BEFORE UPDATE ON course_knowledge_catalog_heads
          FOR EACH ROW EXECUTE FUNCTION delay_growth_switch();
    """)
    publish = repo.publish

    def shorten_lease(course, **kwargs):
        repo.connection.execute(
            "UPDATE course_knowledge_catalog_jobs "
            "SET lease_until=clock_timestamp()+interval '1 second' WHERE id=%s",
            (job["id"],),
        )
        return publish(course, **kwargs)

    monkeypatch.setattr(repo, "publish", shorten_lease)
    with pytest.raises(CatalogLeaseLost):
        IncrementalCatalogWorker(repo, GrowthModel()).run(job)
    assert version_count(repo) == 1 and repo.current(COURSE)["version"] == v1
    assert repo.job(COURSE, job["id"])["status"] == "running"


def test_competing_publishers_from_same_claim_cannot_overwrite_v2(catalog, monkeypatch):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)
    job = claim_incremental(repo)
    captured = {}

    class Pause(BaseException):
        pass

    def capture(course, **kwargs):
        captured.update(kwargs)
        raise Pause()

    monkeypatch.setattr(repo, "publish", capture)
    with pytest.raises(Pause):
        IncrementalCatalogWorker(repo, GrowthModel()).run(job)
    barrier = Barrier(2)

    def publish(rules):
        store = PostgresStore(system.store.database_url)
        store.bind_user(str(USER))
        try:
            barrier.wait(timeout=10)
            return CatalogRepository(store).publish(COURSE, **dict(captured, rules_version=rules))
        except CatalogConflict:
            return "conflict"
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ["growth-a", "growth-b"]))
    assert results.count("conflict") == 1 and version_count(repo) == 2
    assert repo.version(COURSE, v1["id"]) == v1


def test_repeated_exact_publication_acknowledges_completed_task(catalog, monkeypatch):  # noqa: F811
    system, repo = catalog
    seed_v1(repo)
    add_virtualization(system)
    captured = {}
    publish = repo.publish

    def capture(course, **kwargs):
        captured.update(deepcopy(kwargs))
        return publish(course, **kwargs)

    monkeypatch.setattr(repo, "publish", capture)
    assert (
        IncrementalCatalogWorker(repo, GrowthModel()).run(claim_incremental(repo))["status"]
        == "succeeded"
    )
    assert publish(COURSE, **captured)["created"] is False and version_count(repo) == 2


def test_noop_task_does_not_publish_meaningless_version(catalog):  # noqa: F811
    _, repo = catalog
    v1 = seed_v1(repo)
    model = GrowthModel()
    result = IncrementalCatalogWorker(repo, model).run(claim_incremental(repo))
    assert result["error_code"] == "CatalogNoChanges" and result["status"] == "failed"
    assert repo.current(COURSE)["version"] == v1 and not model.calls


def test_run_refuses_outer_transaction_before_any_model_call(catalog):  # noqa: F811
    system, repo = catalog
    v1 = seed_v1(repo)
    add_virtualization(system)
    job = claim_incremental(repo)
    model = GrowthModel()
    with repo.store.transaction(), pytest.raises(CatalogInvalid, match="outside"):
        IncrementalCatalogWorker(repo, model).run(job)
    assert not model.calls and repo.current(COURSE)["version"] == v1
    assert repo.job(COURSE, job["id"])["status"] == "running"
