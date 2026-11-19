"""Pure scheduling policies with a controllable clock; no provider calls."""

from datetime import UTC, datetime, timedelta

import pytest

from final_review.config import Settings
from final_review.knowledge_catalog_scheduler import (
    CatalogService,
    SchedulerConfig,
    changed_input,
    window,
)


@pytest.mark.parametrize("count", [0, 1, 5])
def test_initial_needs_only_one_visible_point(count):
    result = changed_input({"version": None}, {"hash": "h", "points": [{}] * count})
    assert result == (("initial", None) if count else None)


@pytest.mark.parametrize("count", [0, 1])
def test_incremental_includes_removal_of_every_point(count):
    current = {"version": {"id": "v1", "knowledge_snapshot_hash": "old"}}
    assert changed_input(current, {"hash": "new", "points": [{}] * count}) == ("incremental", "v1")


def test_identical_snapshot_skips_task():
    current = {"version": {"id": "v1", "knowledge_snapshot_hash": "same"}}
    assert changed_input(current, {"hash": "same"}) is None


def test_same_success_does_not_extend_and_five_uploads_merge():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cp = window(now, "s0", now)
    assert window(now + timedelta(seconds=30), "s0", now, cp) == cp
    for index in range(1, 5):
        when = now + timedelta(seconds=index * 40)
        cp = window(when, f"s{index}", when, cp)
    assert datetime.fromisoformat(cp["due_at"]) == now + timedelta(seconds=220)
    assert cp["first_pending_at"] == now.isoformat()


def test_max_wait_never_restarts_even_for_deletion_without_success():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cp = window(now, "s0", None)
    for index in range(1, 15):
        cp = window(now + timedelta(seconds=index * 30), str(index), None, cp)
    assert datetime.fromisoformat(cp["due_at"]) == now + timedelta(seconds=300)


def test_delayed_success_scan_uses_committed_signal_time():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cp = window(now, "s0", now)
    cp = window(now + timedelta(seconds=40), "s1", now + timedelta(seconds=35), cp)
    assert datetime.fromisoformat(cp["due_at"]) == now + timedelta(seconds=95)


@pytest.mark.parametrize("kwargs", [{"quiet_seconds": 0}, {"page_size": -1}])
def test_invalid_scheduler_limits(kwargs):
    with pytest.raises(ValueError):
        SchedulerConfig(**kwargs)


def test_service_disabled_never_connects_or_creates_model():
    settings = Settings(_env_file=None, database_url="postgresql://unreachable.invalid/no")
    assert settings.catalog_scheduler_enabled is False
    service = CatalogService(settings, lambda: pytest.fail("model while disabled"))
    try:
        assert service.claim() is None
        service.run(once=True)
    finally:
        service.close()


def test_service_scan_cadence_and_restart_full_reconciliation(monkeypatch):
    settings = Settings(_env_file=None)
    clock, calls = [0], []
    service = CatalogService(settings, None, monotonic=lambda: clock[0])
    monkeypatch.setattr(service.dispatcher.scheduler, "scan", lambda **kw: calls.append(kw))
    try:
        service.scan_if_due()
        clock[0] = 1
        service.scan_if_due()
        assert len(calls) == 1 and calls[-1]["successes_only"] is False
        clock[0] = 10
        service.scan_if_due()
        assert calls[-1]["successes_only"] is True and calls[-1]["since"] is not None
        clock[0] = 60
        service.scan_if_due()
        assert len(calls) == 3 and calls[-1]["successes_only"] is False
    finally:
        service.close()


def test_stop_and_runtime_disable_prevent_new_claims():
    settings = Settings(_env_file=None, catalog_scheduler_enabled=True)
    service = CatalogService(settings, None)
    try:
        service.stop_event.set()
        assert service.claim() is None
        service.stop_event.clear()
        settings.catalog_scheduler_enabled = False
        assert service.claim() is None
    finally:
        service.close()
