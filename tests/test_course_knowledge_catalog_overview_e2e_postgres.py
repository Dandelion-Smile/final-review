"""Isolated overview chain: ingest, schedule, publish, knowledge-tree, UI contract.

The fixture rebuilds public only after proving TEST_DATABASE_URL is a different
database from the business DATABASE_URL. It never enables the business scheduler
and never calls a paid model. Clocks are injected so the quiet window does not
sleep for the real 60s or 300s bounds.
"""

import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from test_course_knowledge_postgres import knowledge_system, parsed, process  # noqa: F401
from test_knowledge_catalog_growth_postgres import add_virtualization
from test_knowledge_catalog_incremental import ExistingMatches, GrowthModel
from test_knowledge_catalog_worker import CatalogModel
from test_postgres_migrations import USER

from final_review.api import create_app
from final_review.auth import CurrentUser, DatabaseAuth
from final_review.config import Settings
from final_review.knowledge_catalog_dispatch import (
    CatalogDispatcher,
    DispatchConfig,
)
from final_review.knowledge_catalog_incremental import IncrementalConfig
from final_review.knowledge_catalog_scheduler import (
    CatalogScheduler,
    CatalogService,
    SchedulerConfig,
)
from final_review.knowledge_store import KnowledgeRepository
from final_review.knowledge_tree import read_tree

pytestmark = pytest.mark.integration
COURSE = "course-1"
ROOT = Path(__file__).parents[1]


def _dotenv(key):
    path = ROOT / ".env"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def _endpoint(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "local"
    name = parsed.path.lstrip("/").split("?")[0]
    return host, parsed.port or 5432, name


@pytest.fixture
def database_url():
    test = os.environ.get("TEST_DATABASE_URL") or _dotenv("TEST_DATABASE_URL")
    business = os.environ.get("DATABASE_URL") or _dotenv("DATABASE_URL")
    if not test:
        pytest.fail("TEST_DATABASE_URL is unset; refusing to guess a database")
    if not business:
        pytest.fail("DATABASE_URL is unavailable; cannot prove the test database is isolated")
    test_id, business_id = _endpoint(test), _endpoint(business)
    if test_id == business_id or test_id[2] in {"", "final_review"}:
        pytest.fail(
            "refusing to rebuild "
            f"{test_id[0]}:{test_id[1]}/{test_id[2] or '(empty)'}"
        )
    with psycopg.connect(test, autocommit=True) as connection:
        current = connection.execute("SELECT current_database()").fetchone()[0]
        if current != test_id[2] or current == business_id[2]:
            pytest.fail(f"connected database {current!r} is not an isolated test database")
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    return test


def _chapter(connection, title, chapter):
    connection.execute(
        "UPDATE documents SET data=data || %s WHERE data->>'title'=%s",
        (Jsonb({"chapter": chapter}), title),
    )


def _seed_http(system):
    parsed(system)
    process(system, [("HTTP", "HTTP无状态性", ["HTTP是无状态协议。"])])
    _chapter(system.store.connection, "HTTP.pptx", "第2章 HTTP")


def _scheduler(system):
    now = [datetime.now(UTC) - timedelta(hours=2)]
    scheduler = CatalogScheduler(
        system.store,
        clock=lambda: now[0],
        config=SchedulerConfig(quiet_seconds=60, max_wait_seconds=300),
    )
    return now, scheduler


def _advance_due(now):
    now[0] += timedelta(seconds=61)


def _tree(system):
    return read_tree(KnowledgeRepository(system.store), COURSE)


def _http_tree(system):
    system.settings.auth_mode = "database"
    system.settings.auth_cookie_secure = False
    system.settings.api_token = SecretStr("")
    auth = DatabaseAuth(system.settings)
    auth.setup()
    try:
        with auth.transaction() as cursor:
            session = auth._new_session(cursor, CurrentUser(str(USER)))
        with TestClient(create_app(system.settings)) as client:
            client.cookies.set(system.settings.auth_cookie_name, session)
            response = client.get(f"/api/courses/{COURSE}/knowledge-tree")
            assert response.status_code == 200, response.text
            return response.json()
    finally:
        auth.close()


def _placements(tree):
    found = {}
    for chapter in tree["chapters"]:
        for point in chapter["points"]:
            found[point["id"]] = point.get("catalog_placement")
    return found


def _assert_no_secret_fields(value):
    banned = {"checkpoint", "lease_token", "prompt", "model_output", "error_message"}
    if isinstance(value, dict):
        assert not (banned & set(value))
        for item in value.values():
            _assert_no_secret_fields(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_secret_fields(item)


def _run_due(system, scheduler, now, model_factory, **config):
    _advance_due(now)
    runner = CatalogDispatcher(
        system.store, model_factory, config=DispatchConfig(**config) if config else None
    )
    runner.scheduler = scheduler
    claimed = runner.claim()
    assert claimed is not None and claimed["course_id"] == COURSE
    result = runner.run(claimed)
    assert result is not None
    return runner, result


def test_upload_publishes_v1_then_v2_and_tree_matches_overview_contract(knowledge_system):  # noqa: F811
    system = knowledge_system
    assert system.settings.catalog_scheduler_enabled is False
    _seed_http(system)
    now, scheduler = _scheduler(system)

    discovered = list(scheduler.discover())
    assert len(discovered) == 1
    assert discovered[0].course_id == COURSE and discovered[0].mode == "initial"
    source = _tree(system)
    assert source["catalog"]["status"] == "uninitialized"
    assert source["catalog"]["is_updating"] is False
    assert source["chapters"][0]["title"] == "第2章 HTTP"
    assert "catalog_placement" not in source["chapters"][0]["points"][0]
    assert source["catalog"]["version_id"] is None
    assert source["catalog"]["chapter_count"] == 0
    assert source["catalog_job"] is None
    assert len(source["chapters"]) == 1 and source["point_count"] == 1

    collecting = scheduler.reconcile(COURSE)
    assert collecting["stage"] == "collecting" and collecting["mode"] == "initial"
    early = scheduler.repo.claim()
    assert scheduler.prepare_claim(early) is None
    waiting = _http_tree(system)
    assert waiting["catalog"]["status"] == "uninitialized"
    assert waiting["catalog"]["is_updating"] is True
    assert waiting["catalog_job"]["status"] == "queued"
    assert waiting["catalog_job"]["stage"] == "collecting"
    assert waiting["chapters"][0]["points"][0]["name"] == "HTTP"
    assert waiting["catalog_job"]["stage"] == "collecting"
    assert waiting["catalog_job"]["mode"] == "initial"
    _assert_no_secret_fields(waiting)

    seen = []

    def observe(schema, data):
        if not seen:
            seen.append(_tree(system))

    _, published = _run_due(system, scheduler, now, lambda: CatalogModel(hook=observe))
    assert seen and seen[0]["catalog_job"]["status"] == "running"
    assert seen[0]["catalog"]["status"] == "uninitialized"
    assert seen[0]["catalog"]["is_updating"] is True
    assert published["status"] == "succeeded" and published["mode"] == "initial"
    current = scheduler.repo.current(COURSE)
    v1 = deepcopy(scheduler.repo.version(COURSE, current["version"]["id"]))
    assert current["head"]["revision"] == 1
    ready = _http_tree(system)
    chapter = ready["chapters"][0]
    point = chapter["points"][0]
    UUID(chapter["id"])
    assert chapter["id"] != chapter["title"]
    assert chapter["title"] == "网络基础"
    assert chapter["description"] == "网络协议与通信机制"
    assert ready["catalog"]["status"] == "ready"
    assert ready["catalog"]["revision"] == 1
    assert ready["catalog"]["is_updating"] is False
    assert ready["catalog"]["chapter_count"] == 1
    assert len(ready["chapters"]) == ready["catalog"]["chapter_count"]
    assert all(item["id"] != "未分章" for item in ready["chapters"])
    assert point["name"] == "HTTP" and "catalog_placement" not in point
    assert point["fact_count"] >= 1 and point["fact_ids"]
    source_row = point["sources"][0]
    assert source_row["locator_id"] and source_row["title"]
    assert source_row["chapter"] == "第2章 HTTP"
    assert ready["catalog_job"]["result_version_id"] == str(v1["id"])
    assert ready["catalog_job"]["status"] == "succeeded"
    assert scheduler.repo.claim() is None

    add_virtualization(system)
    stale = _http_tree(system)
    assert stale["catalog"]["status"] == "stale"
    assert stale["catalog"]["is_updating"] is False
    assert stale["catalog"]["version_id"] == str(v1["id"])
    assert stale["catalog"]["chapter_count"] == 1
    assert len(stale["chapters"]) == 2
    pending = [
        point
        for item in stale["chapters"]
        if item["id"] == "未分章"
        for point in item["points"]
    ]
    assert [point["catalog_placement"] for point in pending] == ["pending_catalog"] * 3
    assert stale["catalog_job"]["status"] == "succeeded"
    assert _placements(stale)[point["id"]] is None

    queued = scheduler.reconcile(COURSE)
    assert queued["mode"] == "incremental" and queued["base_version_id"] == v1["id"]
    merging = _tree(system)
    assert merging["catalog"]["is_updating"] is True
    assert merging["chapters"][0]["title"] == "网络基础"
    assert merging["catalog"]["version_id"] == str(v1["id"])
    assert merging["catalog_job"]["status"] == "queued"
    assert merging["catalog_job"]["mode"] == "incremental"
    _, grown = _run_due(system, scheduler, now, GrowthModel)
    assert grown["status"] == "succeeded" and grown["mode"] == "incremental"
    v2 = scheduler.repo.current(COURSE)["version"]
    assert v2["parent_version_id"] == v1["id"] and v2["build_mode"] == "incremental"
    assert scheduler.repo.current(COURSE)["head"]["revision"] == 2
    assert scheduler.repo.version(COURSE, v1["id"]) == v1
    titles = [item["title"] for item in v2["chapters"]]
    assert titles[0] == "网络基础"
    assert titles[1].startswith("虚拟化")
    assert v2["chapters"][0]["chapter_id"] == v1["chapters"][0]["chapter_id"]
    latest = _http_tree(system)
    assert latest["catalog"]["status"] == "ready"
    assert latest["catalog"]["revision"] == 2
    assert latest["catalog"]["is_updating"] is False
    assert latest["catalog"]["chapter_count"] == 2
    assert [item["title"] for item in latest["chapters"]] == titles
    assert all(item["id"] != "未分章" for item in latest["chapters"])
    names = [point["name"] for item in latest["chapters"] for point in item["points"]]
    assert names.count("HTTP") == 1
    assert sorted(name for name in names if name.startswith("KVM-")) == ["KVM-0", "KVM-1", "KVM-2"]
    assert all(value is None for value in _placements(latest).values())
    assert latest["catalog_job"]["status"] == "succeeded"
    assert latest["chapters"][0]["points"][0]["sources"][0]["chapter"] == "第2章 HTTP"


def test_quiet_window_collapses_changes_into_one_publication(knowledge_system):  # noqa: F811
    system = knowledge_system
    _seed_http(system)
    now, scheduler = _scheduler(system)
    first = scheduler.reconcile(COURSE)
    claimed = scheduler.repo.claim()
    assert scheduler.prepare_claim(claimed) is None
    assert scheduler.repo.job(COURSE, first["id"])["status"] == "queued"
    for index in range(8):
        system.store.connection.execute(
            "UPDATE knowledge_points SET description = coalesce(description,'') || %s",
            (f" change-{index}",),
        )
        now[0] += timedelta(seconds=50)
        again = scheduler.reconcile(COURSE)
        assert again["id"] == first["id"] and again["status"] == "queued"
        live = system.store.connection.execute(
            "SELECT count(*) AS n FROM course_knowledge_catalog_jobs "
            "WHERE status IN ('queued','running','retry_wait')"
        ).fetchone()["n"]
        assert live == 1
    schedule = again["checkpoint"]["schedule"]
    first_at = datetime.fromisoformat(schedule["first_pending_at"])
    due = datetime.fromisoformat(schedule["due_at"])
    assert due - first_at == timedelta(seconds=300)
    assert due <= now[0]

    models = []
    runner = CatalogDispatcher(system.store, lambda: models.append(CatalogModel()) or models[-1])
    runner.scheduler = scheduler
    assert runner.run(runner.claim()) is None
    superseded = scheduler.repo.job(COURSE, first["id"])
    assert superseded["status"] == "failed"
    assert superseded["error_code"] == "SupersededBeforeExecution"
    hidden = _tree(system)
    assert hidden["catalog_job"]["error_code"] != "SupersededBeforeExecution"
    assert hidden["catalog_job"]["status"] == "queued"
    assert hidden["catalog_job"]["stage"] == "collecting"
    assert hidden["catalog"]["is_updating"] is True
    successor = runner.claim()
    assert successor is not None and successor["id"] != first["id"]
    published = runner.run(successor)
    assert published["status"] == "succeeded"
    assert len(models) == 1 and models[0].calls
    assert scheduler.repo.current(COURSE)["head"]["revision"] == 1
    assert (
        system.store.connection.execute(
            "SELECT count(*) AS n FROM course_knowledge_catalog_versions"
        ).fetchone()["n"]
        == 1
    )
    assert runner.claim() is None
    ready = _tree(system)
    assert ready["catalog"]["status"] == "ready"
    assert ready["chapters"][0]["title"] == "网络基础"
    assert ready["catalog_job"]["status"] == "succeeded"


def test_failures_keep_the_tree_and_recover_without_a_business_scheduler(knowledge_system):  # noqa: F811
    system = knowledge_system
    _seed_http(system)
    now, scheduler = _scheduler(system)
    scheduler.reconcile(COURSE)
    _advance_due(now)
    expired = {}

    def expire(schema, data):
        system.store.connection.execute(
            "UPDATE course_knowledge_catalog_jobs SET lease_until=%s WHERE id=%s",
            (datetime.now(UTC) - timedelta(seconds=5), expired["id"]),
        )

    lease_runner = CatalogDispatcher(system.store, lambda: CatalogModel(hook=expire))
    lease_runner.scheduler = scheduler
    leased = lease_runner.claim()
    assert leased is not None
    expired["id"] = leased["id"]
    lost = lease_runner.run(leased)
    assert lost is None, (
        None if lost is None else (lost["status"], lost["stage"], lost["error_code"])
    )
    assert scheduler.repo.current(COURSE)["version"] is None
    readable = _tree(system)
    assert readable["chapters"][0]["title"] == "第2章 HTTP"
    assert readable["chapters"][0]["points"][0]["sources"][0]["locator_id"]
    recovered = CatalogDispatcher(system.store, CatalogModel)
    recovered.scheduler = scheduler
    ready_job = recovered.run(recovered.claim())
    assert ready_job["status"] == "succeeded"
    v1 = deepcopy(scheduler.repo.version(COURSE, ready_job["result_version_id"]))

    unchanged_hash = v1["knowledge_snapshot_hash"]
    scheduler.repo.create_job(
        COURSE, mode="incremental", base_version_id=v1["id"], snapshot_hash=unchanged_hash
    )
    noop = CatalogDispatcher(system.store, CatalogModel).run(scheduler.repo.claim())
    assert noop["status"] == "failed" and noop["error_code"] == "CatalogNoChanges"
    stored = scheduler.repo.job(COURSE, noop["id"])
    assert stored["stage"] == "no_changes" and stored["result_version_id"] is None
    same = _http_tree(system)
    assert same["catalog"]["status"] == "ready" and same["catalog"]["is_updating"] is False
    assert same["catalog"]["version_id"] == str(v1["id"])
    assert same["catalog_job"]["stage"] == "no_changes"
    assert same["catalog_job"]["status"] == "failed"
    assert same["catalog_job"]["error_code"] == "CatalogNoChanges"
    assert scheduler.repo.claim() is None

    add_virtualization(system)
    stale = _tree(system)
    assert stale["catalog"]["status"] == "stale"
    assert any(
        point.get("catalog_placement") == "pending_catalog"
        for chapter in stale["chapters"]
        for point in chapter["points"]
    )
    assert stale["catalog_job"]["error_code"] == "CatalogNoChanges"
    queued = scheduler.reconcile(COURSE)
    assert queued["mode"] == "incremental"
    _advance_due(now)

    def fail(schema, data):
        raise httpx.ConnectError("temporary")

    paused_runner = CatalogDispatcher(
        system.store,
        lambda: GrowthModel(hook=fail),
        config=DispatchConfig(incremental=IncrementalConfig(call_attempts=1)),
    )
    paused_runner.scheduler = scheduler
    interrupted = paused_runner.run(paused_runner.claim())
    assert interrupted["status"] == "retry_wait"
    paused = _http_tree(system)
    assert paused["catalog"]["status"] == "stale"
    assert paused["catalog"]["is_updating"] is True
    assert paused["catalog"]["version_id"] == str(v1["id"])
    assert any(chapter["title"] == "网络基础" for chapter in paused["chapters"])
    assert paused["build_status"].get("succeeded", 0) >= 1
    assert paused["catalog_job"]["status"] == "retry_wait"
    assert paused["catalog_job"]["next_run_at"] is not None
    system.store.connection.execute(
        "UPDATE course_knowledge_catalog_jobs SET next_run_at=clock_timestamp() WHERE id=%s",
        (interrupted["id"],),
    )

    system.store.connection.execute(
        "UPDATE knowledge_points SET description='changed-before-resume' WHERE name='HTTP'"
    )
    conflict_runner = CatalogDispatcher(system.store, GrowthModel)
    conflict_runner.scheduler = scheduler
    conflict = conflict_runner.run(conflict_runner.claim())
    assert conflict["status"] == "failed" and conflict["error_code"] == "CatalogSnapshotChanged"
    assert scheduler.repo.version(COURSE, v1["id"]) == v1

    def mutate(schema, data):
        if schema is ExistingMatches:
            system.store.connection.execute(
                "UPDATE knowledge_points SET description='changed-during-model' WHERE name='HTTP'"
            )

    queued_again = scheduler.reconcile(COURSE)
    assert queued_again["id"] != conflict["id"] and queued_again["mode"] == "incremental"
    _advance_due(now)
    during_runner = CatalogDispatcher(system.store, lambda: GrowthModel(hook=mutate))
    during_runner.scheduler = scheduler
    during = during_runner.run(during_runner.claim())
    assert during["status"] == "failed" and during["error_code"] == "CatalogSnapshotChanged"
    assert scheduler.repo.version(COURSE, v1["id"]) == v1
    kept = _tree(system)
    assert kept["chapters"] and kept["catalog"]["version_id"] == str(v1["id"])
    assert kept["catalog"]["is_updating"] is False
    assert kept["catalog"]["status"] == "stale"
    assert kept["catalog_job"]["status"] == "failed"
    assert kept["catalog_job"]["error_code"] == "CatalogSnapshotChanged"

    compensated = scheduler.reconcile(COURSE)
    assert compensated["id"] not in {conflict["id"], during["id"]}
    assert compensated["mode"] == "incremental"
    _advance_due(now)
    final_runner = CatalogDispatcher(system.store, GrowthModel)
    final_runner.scheduler = scheduler
    finished = final_runner.run(final_runner.claim())
    assert finished["status"] == "succeeded"
    assert scheduler.repo.version(COURSE, v1["id"]) == v1
    assert scheduler.repo.current(COURSE)["head"]["revision"] == 2
    latest = _tree(system)
    assert latest["catalog"]["status"] == "ready"
    assert any(chapter["title"].startswith("虚拟化") for chapter in latest["chapters"])
    assert latest["catalog_job"]["status"] == "succeeded"


def test_stopped_catalog_service_claims_only_after_resume(knowledge_system):  # noqa: F811
    system = knowledge_system
    assert isinstance(system.settings, Settings)
    assert system.settings.catalog_scheduler_enabled is False
    _seed_http(system)
    now, scheduler = _scheduler(system)
    scheduler.reconcile(COURSE)
    _advance_due(now)
    stop = Event()
    stop.set()
    service = CatalogService(
        system.settings.model_copy(
            update={"catalog_scheduler_enabled": True, "catalog_max_concurrency": 1}
        ),
        CatalogModel,
        stop_event=stop,
    )
    try:
        assert service.claim() is None
        assert scheduler.repo.current(COURSE)["version"] is None
        stop.clear()
        work = service.claim()
        assert work is not None and work[0]["course_id"] == COURSE
        assert service.process(work)["status"] == "succeeded"
        assert service.claim() is None
    finally:
        service.close()
        stop.set()
    assert system.settings.catalog_scheduler_enabled is False
    visible = _tree(system)
    assert visible["catalog"]["status"] == "ready"
    assert visible["catalog"]["chapter_count"] == 1
    assert visible["chapters"][0]["title"] == "网络基础"
    assert visible["catalog_job"]["status"] == "succeeded"
