"""Dispatch boundary tests: modes, fresh instances and explicit user contexts."""

from contextlib import nullcontext
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

import final_review.knowledge_catalog_dispatch as dispatch
from final_review.knowledge_catalog_dispatch import CatalogDispatcher, unpack
from final_review.knowledge_catalog_scheduler import ENVELOPE


@pytest.mark.parametrize("checkpoint", [{}, {"schema_version": "catalog-checkpoint-v1"}])
def test_legacy_checkpoint_compatibility(checkpoint):
    envelope, worker = unpack(checkpoint)
    assert envelope["schema_version"] == ENVELOPE and worker == checkpoint
    assert unpack({**envelope, "schedule": {"due_at": "fixed"}})[1] == checkpoint


def setup_boundary(monkeypatch, mode):
    owner = ContextVar("owner", default="caller")
    store = SimpleNamespace(
        bind_user=owner.set,
        reset_user=owner.reset,
        account_file_access=lambda user: nullcontext(True),
    )
    scheduler = SimpleNamespace(
        prepare_claim=lambda job: job, repo=SimpleNamespace(job=lambda *args: {"status": "done"})
    )
    monkeypatch.setattr(dispatch, "CatalogScheduler", lambda *a, **k: scheduler)
    base = "v1" if mode == "incremental" else None
    monkeypatch.setattr(
        dispatch,
        "DispatchRepository",
        lambda s, **k: SimpleNamespace(
            current=lambda course: {"version": {"id": base} if base else None},
            checkpoint=lambda *a, **k: None,
            baseline=lambda *a: None,
            fail=lambda *a, **k: {"error_code": k["error_code"], "status": "failed"},
        ),
    )
    instances, models = [], []

    class Worker:
        def __init__(self, repo, model, *, config):
            self.calls = 0
            instances.append((self, owner.get(), model))

        def run(self, job, **kwargs):
            assert owner.get() == job["user_id"]
            self.calls += 1

    monkeypatch.setattr(dispatch, "InitialCatalogWorker", Worker if mode == "initial" else None)
    monkeypatch.setattr(
        dispatch, "IncrementalCatalogWorker", Worker if mode == "incremental" else None
    )

    def factory():
        model = object()
        models.append(model)
        return model

    runner = CatalogDispatcher(store, factory)
    monkeypatch.setattr(runner, "scope_allowed", lambda course: True)
    job = dict(
        user_id="user-a",
        course_id="course",
        id="j",
        mode=mode,
        base_version_id=base,
        checkpoint={},
        attempt=1,
        lease_token="lease",
    )
    return runner, job, owner, instances, models


@pytest.mark.parametrize("mode", ["initial", "incremental"])
def test_modes_users_and_fresh_workers(monkeypatch, mode):
    runner, job, owner, workers, models = setup_boundary(monkeypatch, mode)
    runner.run(job)
    runner.run({**job, "user_id": "user-b"})
    assert owner.get() == "caller"
    assert [w[1] for w in workers] == ["user-a", "user-b"]
    assert workers[0][0] is not workers[1][0] and models[0] is not models[1]
    assert [w[0].calls for w in workers] == [1, 1]


def test_unknown_mode_rejected_and_context_cleared(monkeypatch):
    runner, job, owner, _, models = setup_boundary(monkeypatch, "initial")
    result = runner.run({**job, "mode": "unknown"})
    assert result["status"] == "failed" and result["error_code"] == "CatalogInvalid"
    assert owner.get() == "caller" and not models


def test_discovery_generator_restores_context_before_yield(monkeypatch):
    from final_review.knowledge_catalog_scheduler import CatalogScheduler

    owner = ContextVar("discovery_owner", default="caller")
    store = SimpleNamespace(bind_user=owner.set, reset_user=owner.reset)
    scheduler = CatalogScheduler(store)
    monkeypatch.setattr(
        scheduler,
        "courses",
        lambda **kwargs: iter([{"user_id": "owner", "course_id": "course", "success_at": None}]),
    )
    monkeypatch.setattr(scheduler, "eligible", lambda course: True)
    scheduler.repo = SimpleNamespace(
        snapshot=lambda course: {"hash": "h", "points": [{}]},
        current=lambda course: {"version": None},
    )
    changes = scheduler.discover()
    assert next(changes).user_id == "owner"
    assert owner.get() == "caller"
    changes.close()
