"""Published catalog reads, stale compatibility and read-only job status.

Uses the isolated TEST_DATABASE_URL fixture. It rebuilds public and must not
point at the business database.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from test_course_knowledge_postgres import (  # noqa: F401
    knowledge_system,
    parsed,
    process,
)
from test_knowledge_catalog_postgres import (  # noqa: F401
    COURSE,
    catalog,
    other_scopes,
    proposal,
)
from test_knowledge_catalog_worker import CatalogModel
from test_postgres_migrations import SECOND_USER, USER, database_url  # noqa: F401

from final_review.api import create_app
from final_review.auth import CurrentUser, DatabaseAuth
from final_review.knowledge_catalog_dispatch import DispatchRepository
from final_review.knowledge_catalog_store import (
    CatalogAssignment,
    CatalogChapter,
    CatalogRepository,
)
from final_review.knowledge_catalog_worker import InitialCatalogWorker
from final_review.knowledge_store import KnowledgeRepository
from final_review.knowledge_tree import read_catalog_job, read_tree
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration
SECRET = "do-not-leak-catalog-secret"


def _chapter(connection, title):
    document = connection.execute("SELECT document_id FROM documents LIMIT 1").fetchone()
    connection.execute(
        "UPDATE documents SET data=data || %s WHERE document_id=%s",
        (Jsonb({"chapter": title}), document["document_id"]),
    )


def _by_name(repo):
    found = {}
    for item in repo.snapshot(COURSE)["points"]:
        found[item["input"]["rows"][0]["name"]] = item
    return found


def _owner_app(system):
    system.settings.auth_mode = "database"
    system.settings.auth_cookie_secure = False
    system.settings.api_token = SecretStr("")
    return TestClient(create_app(system.settings))


def test_missing_catalog_keeps_source_chapters(knowledge_system):  # noqa: F811
    system = knowledge_system
    repo = KnowledgeRepository(system.store)
    empty = read_tree(repo, COURSE)
    assert empty["chapters"] == [] and empty["point_count"] == 0
    assert empty["catalog_job"] is None
    assert empty["catalog"]["status"] == "uninitialized"
    assert empty["catalog"]["chapter_count"] == empty["catalog"]["assigned_count"] == 0
    parsed(system)
    process(system, [("HTTP", "HTTP无状态性", ["HTTP是无状态协议。"])])
    _chapter(system.store.connection, "第2章 HTTP")
    tree = read_tree(repo, COURSE)
    assert tree["catalog"]["status"] == "uninitialized" and tree["catalog"]["is_updating"] is False
    assert tree["chapters"][0]["title"] == "第2章 HTTP"
    assert "description" not in tree["chapters"][0]
    point = tree["chapters"][0]["points"][0]
    assert point["name"] == "HTTP" and point["fact_count"] == 1
    assert point["sources"][0]["chapter"] == "第2章 HTTP"
    assert "catalog_placement" not in point
    assert read_tree(repo, "foreign")["catalog"]["status"] == "uninitialized"


def test_current_version_replaces_source_chapters_and_keeps_sources(catalog):  # noqa: F811
    system, repo = catalog
    _chapter(system.store.connection, "第2章 HTTP")
    args = proposal(repo)
    args["chapters"][0] = args["chapters"][0].model_copy(update={"description": "协议分层"})
    created = repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash=args["snapshot_hash"]
    )
    lease = repo.claim()
    published = repo.publish(COURSE, **args, job_id=lease["id"], lease_token=lease["lease_token"])
    tree = read_tree(KnowledgeRepository(system.store), COURSE)
    chapter = tree["chapters"][0]
    point = chapter["points"][0]
    assert tree["catalog"]["status"] == "ready"
    assert tree["catalog"]["version_id"] == str(published["version_id"])
    assert tree["catalog"]["revision"] == 1
    assert tree["catalog"]["knowledge_snapshot_hash"] == args["snapshot_hash"]
    assert tree["catalog"]["chapter_count"] == tree["catalog"]["assigned_count"] == 1
    assert tree["catalog"]["unassigned_count"] == 0 and tree["catalog"]["is_updating"] is False
    assert chapter["id"] == str(args["chapters"][0].chapter_id)
    assert chapter["title"] == "应用层" and chapter["description"] == "协议分层"
    assert point["fact_count"] == 1 and point["sources"][0]["chapter"] == "第2章 HTTP"
    assert "catalog_placement" not in point
    assert tree["catalog_job"]["job_id"] == str(created["id"])
    assert (
        tree["catalog_job"]["status"] == "succeeded" and tree["catalog_job"]["stage"] == "published"
    )
    assert tree["catalog_job"]["result_version_id"] == str(published["version_id"])
    assert tree["catalog_job"]["error_code"] is None
    parsed(system, "HTTP默认端口为80。", "ports.pptx")
    process(system, [("端口", "HTTP默认端口配置", ["HTTP默认端口为80。"])])
    names = _by_name(repo)
    added = uuid4()
    grown = repo.publish(
        COURSE,
        base_version_id=published["version_id"],
        base_revision=1,
        snapshot_hash=repo.snapshot(COURSE)["hash"],
        chapters=[
            CatalogChapter(chapter_id=added, title="传输层", description="新增", sort_order=0),
            CatalogChapter(
                chapter_id=args["chapters"][0].chapter_id,
                title="应用层",
                description="协议分层",
                sort_order=1,
            ),
        ],
        assignments=[
            CatalogAssignment(
                point_id=names["端口"]["point_id"],
                chapter_id=added,
                classification_input_hash=names["端口"]["classification_input_hash"],
                assignment_method="llm",
                sort_order=0,
            ),
            CatalogAssignment(
                point_id=names["HTTP"]["point_id"],
                chapter_id=args["chapters"][0].chapter_id,
                classification_input_hash=names["HTTP"]["classification_input_hash"],
                assignment_method="carried",
                sort_order=0,
            ),
        ],
        build_mode="incremental",
        rules_version="catalog-v1",
        model_config={"model": "test"},
    )
    current = read_tree(KnowledgeRepository(system.store), COURSE)
    assert current["catalog"]["status"] == "ready"
    assert current["catalog"]["version_id"] == str(grown["version_id"])
    assert current["catalog"]["revision"] == 2
    assert [(item["title"], item["id"]) for item in current["chapters"]] == [
        ("传输层", str(added)),
        ("应用层", str(args["chapters"][0].chapter_id)),
    ]
    assert [item["points"][0]["name"] for item in current["chapters"]] == ["端口", "HTTP"]
    assert current["catalog"]["chapter_count"] == 2 and current["catalog"]["unassigned_count"] == 0


def test_stale_catalog_keeps_assignments_and_marks_only_new_points_pending(catalog):  # noqa: F811
    system, repo = catalog
    _chapter(system.store.connection, "第2章 HTTP")
    published = repo.publish(COURSE, **proposal(repo))
    knowledge = KnowledgeRepository(system.store)
    connection = system.store.connection
    connection.execute("UPDATE knowledge_points SET description='新的描述' WHERE name='HTTP'")
    described = read_tree(knowledge, COURSE)
    assert described["catalog"]["status"] == "stale"
    assert described["catalog"]["version_id"] == str(published["version_id"])
    assert described["chapters"][0]["title"] == "应用层"
    assert described["chapters"][0]["points"][0]["description"] == "新的描述"
    fact_id = described["chapters"][0]["points"][0]["fact_ids"][0]
    connection.execute(
        "UPDATE knowledge_facts SET fact_text='修订后的事实' WHERE id=%s", (fact_id,)
    )
    revised = read_tree(knowledge, COURSE)
    assert revised["catalog"]["status"] == "stale"
    assert fact_id in revised["chapters"][0]["points"][0]["fact_ids"]
    assert revised["chapters"][0]["points"][0]["sources"][0]["document_id"]
    parsed(system, "HTTP默认端口为80。", "ports.pptx")
    process(system, [("端口", "HTTP默认端口配置", ["HTTP默认端口为80。"])])
    stale = read_tree(knowledge, COURSE)
    placed = {item["name"]: item for item in stale["chapters"][0]["points"]}
    pending = {item["name"]: item for item in stale["chapters"][1]["points"]}
    assert stale["catalog"]["status"] == "stale"
    assert set(placed) == {"HTTP"} and "catalog_placement" not in placed["HTTP"]
    assert pending["端口"]["catalog_placement"] == "pending_catalog"
    assert stale["catalog"]["assigned_count"] == 1 and stale["catalog"]["unassigned_count"] == 1
    assert stale["point_count"] == 2
    connection.execute("UPDATE knowledge_points SET status='inactive' WHERE name='HTTP'")
    hidden = read_tree(knowledge, COURSE)
    names = [point["name"] for chapter in hidden["chapters"] for point in chapter["points"]]
    assert names == ["端口"]
    assert hidden["chapters"][0]["points"] == []
    assert hidden["chapters"][1]["points"][0]["catalog_placement"] == "pending_catalog"
    assert hidden["catalog"]["chapter_count"] == 1
    assert hidden["catalog"]["assigned_count"] == 0 and hidden["catalog"]["unassigned_count"] == 1
    assert hidden["point_count"] == 1


def test_explicit_unclassified_points_stay_distinct_from_pending_points(catalog):  # noqa: F811
    system, repo = catalog
    snap = repo.snapshot(COURSE)["points"][0]
    chapter_id = uuid4()
    repo.publish(
        COURSE,
        base_version_id=None,
        base_revision=0,
        snapshot_hash=repo.snapshot(COURSE)["hash"],
        chapters=[CatalogChapter(chapter_id=chapter_id, title="应用层", sort_order=0)],
        assignments=[
            CatalogAssignment(
                point_id=snap["point_id"],
                chapter_id=None,
                classification_input_hash=snap["classification_input_hash"],
                assignment_method="unclassified",
                sort_order=3,
            )
        ],
        build_mode="initial",
        rules_version="catalog-v1",
        model_config={"model": "test"},
    )
    parsed(system, "HTTP默认端口为80。", "ports.pptx")
    process(system, [("端口", "HTTP默认端口配置", ["HTTP默认端口为80。"])])
    tree = read_tree(KnowledgeRepository(system.store), COURSE)
    assert [chapter["title"] for chapter in tree["chapters"]] == ["应用层", "未分章"]
    assert tree["chapters"][0]["id"] == str(chapter_id) and tree["chapters"][0]["points"] == []
    assert [point["catalog_placement"] for point in tree["chapters"][1]["points"]] == [
        "unclassified",
        "pending_catalog",
    ]
    assert [point["name"] for point in tree["chapters"][1]["points"]] == ["HTTP", "端口"]
    assert len({point["id"] for chapter in tree["chapters"] for point in chapter["points"]}) == 2
    assert tree["catalog"]["chapter_count"] == 1
    assert tree["catalog"]["assigned_count"] == 0 and tree["catalog"]["unassigned_count"] == 2


def _view(system):
    return read_tree(KnowledgeRepository(system.store), COURSE)


def test_catalog_job_summary_prefers_live_work_and_hides_checkpoint(catalog):  # noqa: F811
    system, repo = catalog
    assert _view(system)["catalog_job"] is None
    args = proposal(repo)
    job = repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash=args["snapshot_hash"]
    )
    queued = _view(system)
    assert queued["catalog"]["status"] == "uninitialized" and queued["catalog"]["is_updating"]
    assert queued["chapters"][0]["title"] != "应用层"
    assert queued["catalog_job"]["status"] == "queued" and queued["catalog_job"]["attempt"] == 0
    lease = repo.claim()
    repo.checkpoint(
        COURSE,
        lease["id"],
        lease["lease_token"],
        stage="assigning",
        checkpoint={"prompt": SECRET, "semantic_baseline": {"raw": SECRET}},
    )
    running = _view(system)
    assert running["catalog_job"]["status"] == "running"
    assert running["catalog_job"]["stage"] == "assigning" and running["catalog_job"]["attempt"] == 1
    assert SECRET not in str(running) and "checkpoint" not in running["catalog_job"]
    assert str(lease["lease_token"]) not in str(running)
    direct = read_catalog_job(KnowledgeRepository(system.store), COURSE, job["id"])
    assert direct == running["catalog_job"]
    repo.fail(
        COURSE,
        lease["id"],
        lease["lease_token"],
        error_code="ModelTimeout",
        error_message=SECRET,
        retry_at=datetime.now(UTC) + timedelta(hours=1),
    )
    waiting = _view(system)["catalog_job"]
    assert waiting["status"] == "retry_wait" and waiting["error_code"] == "ModelTimeout"
    assert _view(system)["catalog"]["is_updating"] is True and SECRET not in str(waiting)
    system.store.connection.execute(
        "UPDATE course_knowledge_catalog_jobs SET next_run_at=clock_timestamp() WHERE id=%s",
        (job["id"],),
    )
    again = repo.claim()
    repo.fail(
        COURSE,
        again["id"],
        again["lease_token"],
        error_code="ModelOutputInvalid",
        error_message=SECRET,
    )
    failed = _view(system)
    assert failed["catalog_job"]["status"] == "failed"
    assert failed["catalog_job"]["error_code"] == "ModelOutputInvalid"
    assert failed["catalog"]["is_updating"] is False and SECRET not in str(failed)
    system.store.connection.execute(
        """UPDATE course_knowledge_catalog_jobs SET stage='no_changes',error_code='CatalogNoChanges'
           WHERE id=%s""",
        (job["id"],),
    )
    unchanged = _view(system)["catalog_job"]
    assert unchanged["status"] == "failed" and unchanged["stage"] == "no_changes"
    assert unchanged["error_code"] == "CatalogNoChanges"
    system.store.connection.execute(
        """UPDATE course_knowledge_catalog_jobs
           SET error_code='SupersededBeforeExecution',stage=NULL,
               created_at=clock_timestamp()+interval '1 day' WHERE id=%s""",
        (job["id"],),
    )
    successor = repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash="replacement-snapshot"
    )
    summary = _view(system)
    assert summary["catalog_job"]["job_id"] == str(successor["id"])
    assert summary["catalog_job"]["status"] == "queued" and summary["catalog"]["is_updating"]
    superseded = read_catalog_job(KnowledgeRepository(system.store), COURSE, job["id"])
    assert superseded["error_code"] == "SupersededBeforeExecution"
    assert superseded["status"] == "failed" and SECRET not in str(superseded)
    assert read_catalog_job(KnowledgeRepository(system.store), COURSE, uuid4()) is None
    assert read_catalog_job(KnowledgeRepository(system.store), "other", job["id"]) is None


def test_catalog_routes_reject_other_owners_deleted_courses_and_closed_accounts(catalog):  # noqa: F811
    system, repo = catalog
    args = proposal(repo)
    published = repo.publish(COURSE, **args)
    job = repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash=args["snapshot_hash"]
    )
    other_scopes(system.store)
    foreign = PostgresStore(system.store.database_url)
    token = foreign.bind_user(str(SECOND_USER))
    try:
        foreign_repo = CatalogRepository(foreign)
        foreign_job = foreign_repo.create_job(
            COURSE, mode="initial", base_version_id=None, snapshot_hash="foreign-snapshot"
        )
        isolated = read_tree(KnowledgeRepository(foreign), COURSE)
        assert isolated["point_count"] == 0
        assert isolated["catalog"]["status"] == "uninitialized"
        assert isolated["catalog"]["version_id"] is None
        assert read_catalog_job(KnowledgeRepository(foreign), COURSE, job["id"]) is None
        assert read_catalog_job(KnowledgeRepository(foreign), COURSE, foreign_job["id"])[
            "job_id"
        ] == str(foreign_job["id"])
    finally:
        foreign.reset_user(token)
        foreign.close()
    system.settings.auth_mode = "database"
    system.settings.auth_cookie_secure = False
    system.settings.api_token = SecretStr("")
    with _owner_app(system) as client:
        assert client.get(f"/api/courses/{COURSE}/knowledge-tree").status_code == 401
        assert (
            client.get(f"/api/courses/{COURSE}/knowledge-catalog/jobs/{job['id']}").status_code
            == 401
        )
        retry_url = f"/api/courses/{COURSE}/knowledge-catalog/jobs/{job['id']}/retry"
        assert client.post(retry_url).status_code == 401
        auth = DatabaseAuth(system.settings)
        auth.setup()
        try:
            with auth.transaction() as cursor:
                session = auth._new_session(cursor, CurrentUser(str(USER)))
                other_session = auth._new_session(cursor, CurrentUser(str(SECOND_USER)))
                stranger = str(uuid4())
                cursor.execute(
                    "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,'hash')",
                    (stranger, f"{stranger}@example.test"),
                )
                stranger_session = auth._new_session(cursor, CurrentUser(stranger))
            client.cookies.set(system.settings.auth_cookie_name, session)
            tree = client.get(f"/api/courses/{COURSE}/knowledge-tree")
            body = tree.json()
            assert tree.status_code == 200, tree.text
            assert body["catalog"]["status"] == "ready"
            assert body["catalog"]["version_id"] == str(published["version_id"])
            assert body["chapters"][0]["points"][0]["fact_count"] == 1
            assert body["catalog_job"]["job_id"] == str(job["id"])
            assert set(body) == {
                "chapters",
                "point_count",
                "updated_at",
                "build_status",
                "catalog",
                "catalog_job",
            }
            owned = client.get(f"/api/courses/{COURSE}/knowledge-catalog/jobs/{job['id']}")
            assert owned.status_code == 200, owned.text
            assert set(owned.json()) == {
                "job_id",
                "mode",
                "status",
                "stage",
                "attempt",
                "next_run_at",
                "result_version_id",
                "error_code",
            }
            missing = client.get(f"/api/courses/{COURSE}/knowledge-catalog/jobs/{uuid4()}")
            assert missing.status_code == 404
            leaked = client.get(f"/api/courses/{COURSE}/knowledge-catalog/jobs/{foreign_job['id']}")
            assert leaked.status_code == 404
            assert client.get("/api/courses/foreign/knowledge-tree").status_code == 404
            client.cookies.set(system.settings.auth_cookie_name, other_session)
            assert client.post(retry_url).status_code == 404
            assert client.get(f"/api/courses/{COURSE}/knowledge-tree").status_code == 404
            assert (
                client.get(f"/api/courses/{COURSE}/knowledge-catalog/jobs/{job['id']}").status_code
                == 404
            )
            client.cookies.set(system.settings.auth_cookie_name, stranger_session)
            assert client.get(f"/api/courses/{COURSE}/knowledge-tree").status_code == 404
            assert (
                client.get(f"/api/courses/{COURSE}/knowledge-catalog/jobs/{job['id']}").status_code
                == 404
            )
            client.cookies.set(system.settings.auth_cookie_name, session)
            system.store.connection.execute(
                "UPDATE courses SET data=data || %s WHERE user_id=%s AND course_id=%s",
                (Jsonb({"status": "deleted"}), USER, COURSE),
            )
            assert client.post(retry_url).status_code == 409
            assert client.get(f"/api/courses/{COURSE}/knowledge-tree").status_code == 409
            assert (
                client.get(f"/api/courses/{COURSE}/knowledge-catalog/jobs/{job['id']}").status_code
                == 409
            )
            system.store.connection.execute(
                "UPDATE courses SET data=data - 'status' WHERE user_id=%s AND course_id=%s",
                (USER, COURSE),
            )
            system.store.connection.execute(
                "UPDATE app_users SET deletion_due_at=clock_timestamp()+interval '1 day' "
                "WHERE id=%s",
                (USER,),
            )
            assert client.post(retry_url).status_code == 403
            assert client.get(f"/api/courses/{COURSE}/knowledge-tree").status_code == 403
            assert (
                client.get(f"/api/courses/{COURSE}/knowledge-catalog/jobs/{job['id']}").status_code
                == 403
            )
        finally:
            auth.close()


@pytest.mark.parametrize("condition", ["current", "snapshot", "version", "active", "budget"])
def test_manual_catalog_retry(catalog, condition):  # noqa: F811
    system, repo = catalog
    args = proposal(repo)
    job = repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash=args["snapshot_hash"]
    )
    url = f"/api/courses/{COURSE}/knowledge-catalog/jobs/{job['id']}/retry"
    with _owner_app(system) as client:
        auth = DatabaseAuth(system.settings)
        auth.setup()
        try:
            with auth.transaction() as cursor:
                session = auth._new_session(cursor, CurrentUser(str(USER)))
            client.cookies.set(system.settings.auth_cookie_name, session)
            assert client.post(url).status_code == 409
            lease = repo.claim()
            dispatch = DispatchRepository(system.store)
            result = InitialCatalogWorker(dispatch, CatalogModel(lambda *_: {})).run(lease)
            assert result["status"] == "failed"
            before = repo.job(COURSE, job["id"])["checkpoint"]
            assert before["initial_calls_reserved"] == 2
            if condition == "snapshot":
                parsed(system, "HTTP默认端口为80。", "ports.pptx")
                process(system, [("端口", "HTTP默认端口配置", ["HTTP默认端口为80。"])])
            elif condition == "version":
                repo.publish(COURSE, **args)
            elif condition == "active":
                repo.create_job(
                    COURSE, mode="initial", base_version_id=None, snapshot_hash="successor"
                )
            elif condition == "budget":
                before["initial_calls_reserved"] = dispatch.config.initial.max_calls
                repo.connection.execute(
                    "UPDATE course_knowledge_catalog_jobs SET checkpoint=%s WHERE id=%s",
                    (Jsonb(before), job["id"]),
                )
            if condition != "current":
                assert client.post(url).status_code == 409
                row = repo.job(COURSE, job["id"])
                assert row["status"] == "failed" and row["checkpoint"] == before
                return
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: client.post(url), range(2)))
            assert all(
                r.status_code == 200 and r.json()["job_id"] == str(job["id"]) for r in responses
            )
            row = repo.job(COURSE, job["id"])
            assert row["checkpoint"] == {**before, "manual_retry": True}
            assert row["attempt"] == 1
            claimed = repo.claim()
            assert claimed["attempt"] == 2 and repo.claim() is None
            assert (
                InitialCatalogWorker(dispatch, CatalogModel()).run(claimed)["status"] == "succeeded"
            )
            assert client.post(url).json()["status"] == "succeeded"
            assert repo.job(COURSE, job["id"])["checkpoint"]["initial_calls_reserved"] == 4
            assert repo.head(COURSE)["revision"] == 1
        finally:
            auth.close()
