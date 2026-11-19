"""Real PostgreSQL catalog constraints, publication, leases and compatibility.

Uses the project's destructive public-schema fixture ONLY on TEST_DATABASE_URL.
No PostgreSQL constraint, lock or rollback is mocked.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import psycopg
import pytest
from test_course_knowledge_postgres import (
    counts,
    knowledge_system,  # noqa: F401
    parsed,
    process,
)
from test_postgres_migrations import SECOND_USER, USER, database_url  # noqa: F401

from final_review.knowledge_catalog_store import (
    CatalogAssignment,
    CatalogChapter,
    CatalogConflict,
    CatalogInvalid,
    CatalogLeaseLost,
    CatalogRepository,
    CatalogSnapshotChanged,
)
from final_review.knowledge_store import KnowledgeRepository
from final_review.knowledge_tree import read_tree
from final_review.migrations import apply_migrations, migrations
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration
COURSE = "course-1"
PREFIX = "course_knowledge_catalog_"


@pytest.fixture
def catalog(knowledge_system):  # noqa: F811
    system = knowledge_system
    parsed(system)
    process(system, [("HTTP", "HTTP无状态性", ["HTTP是无状态协议。"])])
    return system, CatalogRepository(system.store)


def proposal(repo, *, base=None, revision=0, mode="initial", chapter_id=None):
    snapshot = repo.snapshot(COURSE)
    chapter_id = chapter_id or uuid4()
    return dict(
        base_version_id=base,
        base_revision=revision,
        snapshot_hash=snapshot["hash"],
        chapters=[CatalogChapter(chapter_id=chapter_id, title="应用层", sort_order=0)],
        assignments=[
            CatalogAssignment(
                point_id=p["point_id"],
                chapter_id=chapter_id,
                classification_input_hash=p["classification_input_hash"],
                assignment_method="llm",
                sort_order=i,
            )
            for i, p in enumerate(snapshot["points"])
        ],
        build_mode=mode,
        rules_version="catalog-v1",
        model_config={"model": "test"},
    )


def catalog_counts(connection):
    return tuple(
        connection.execute(f"SELECT count(*) AS n FROM {PREFIX}{table}").fetchone()["n"]
        for table in ("heads", "versions", "chapters", "assignments")
    )


def raw_version(connection, *, user=USER, course=COURSE, parent=None):
    return connection.execute(
        f"""INSERT INTO {PREFIX}versions(user_id,course_id,parent_version_id,build_mode,
            knowledge_snapshot_hash,publication_hash,rules_version,model_config,
            chapter_count,point_count)
            VALUES (%s,%s,%s,%s,'snapshot','publication','rules','{{}}',0,0)
            RETURNING id""",
        (user, course, parent, "incremental" if parent else "initial"),
    ).fetchone()["id"]


def raw_chapter(
    connection, version, *, user=USER, course=COURSE, chapter=None, name="chapter", order=0
):
    chapter = chapter or uuid4()
    connection.execute(
        f"""INSERT INTO {PREFIX}chapters(version_id,chapter_id,user_id,course_id,title,
            normalized_title,sort_order) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (version, chapter, user, course, name, name, order),
    )
    return chapter


def raw_assignment(connection, version, point, *, user=USER, course=COURSE, chapter=None):
    connection.execute(
        f"""INSERT INTO {PREFIX}assignments(version_id,user_id,course_id,point_id,chapter_id,
            classification_input_hash,assignment_method,sort_order)
            VALUES (%s,%s,%s,%s,%s,'input',%s,0)""",
        (version, user, course, point, chapter, "llm" if chapter else "unclassified"),
    )


def other_scopes(store):
    c = store.connection
    c.execute(
        "INSERT INTO app_users(id,email,password_hash) VALUES (%s,'other@test','hash')",
        (SECOND_USER,),
    )
    for key, user, course in (("other", USER, "other"), ("second", SECOND_USER, COURSE)):
        c.execute(
            "INSERT INTO courses(record_key,user_id,course_id,data) VALUES (%s,%s,%s,'{}')",
            (key, user, course),
        )


def test_migrations_create_five_tables_and_repeat_cleanly(catalog):
    s, repo = catalog
    versions = s.store.connection.execute("SELECT version FROM schema_migrations ORDER BY version")
    assert [v["version"] for v in versions] == [p.name for p in migrations()]
    assert apply_migrations(s.store.database_url) == []
    rows = s.store.connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE %s",
        (PREFIX + "%",),
    ).fetchall()
    assert {r["tablename"] for r in rows} == {
        PREFIX + t for t in ("heads", "versions", "chapters", "assignments", "jobs")
    }
    assert repo.head(COURSE) is None
    assert repo.current(COURSE) == {"head": None, "version": None}
    assert repo.version(COURSE, uuid4()) is None


@pytest.mark.parametrize("user,course", [(SECOND_USER, COURSE), (USER, "other")])
def test_database_rejects_cross_scope_chapter(catalog, user, course):
    s, _ = catalog
    other_scopes(s.store)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), s.store.transaction():
        version = raw_version(s.store.connection)
        raw_chapter(s.store.connection, version, user=user, course=course)


@pytest.mark.parametrize("kind", ["head", "parent", "job_base", "job_result", "point"])
@pytest.mark.parametrize("user,course", [(SECOND_USER, COURSE), (USER, "other")])
def test_database_rejects_cross_scope_references(catalog, kind, user, course):
    s, repo = catalog
    c = s.store.connection
    other_scopes(s.store)
    point = repo.snapshot(COURSE)["points"][0]["point_id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation), s.store.transaction():
        raw_version(c)
        foreign = raw_version(c, user=user, course=course)
        if kind == "head":
            c.execute(
                f"INSERT INTO {PREFIX}heads(user_id,course_id,current_version_id) "
                "VALUES (%s,%s,%s)",
                (USER, COURSE, foreign),
            )
        elif kind == "parent":
            raw_version(c, parent=foreign)
        elif kind == "job_base":
            c.execute(
                f"INSERT INTO {PREFIX}jobs(user_id,course_id,mode,base_version_id,"
                "snapshot_hash) VALUES (%s,%s,'incremental',%s,'hash')",
                (USER, COURSE, foreign),
            )
        elif kind == "job_result":
            c.execute(
                f"INSERT INTO {PREFIX}jobs(user_id,course_id,mode,status,result_version_id,"
                "snapshot_hash) VALUES (%s,%s,'initial','succeeded',%s,'hash')",
                (USER, COURSE, foreign),
            )
        else:
            raw_assignment(c, foreign, point, user=user, course=course)
        # Also prove deferred constraints, rather than only immediate ones.
        c.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert repo.head(COURSE) is None


def test_database_rejects_chapter_from_another_version(catalog):
    s, repo = catalog
    c = s.store.connection
    point = repo.snapshot(COURSE)["points"][0]["point_id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation), s.store.transaction():
        v1, v2 = raw_version(c), raw_version(c)
        chapter = raw_chapter(c, v1)
        raw_assignment(c, v2, point, chapter=chapter)


@pytest.mark.parametrize("kind", ["point", "normalized_title", "chapter_order"])
def test_database_rejects_duplicate_catalog_rows(catalog, kind):
    s, repo = catalog
    c = s.store.connection
    point = repo.snapshot(COURSE)["points"][0]["point_id"]
    with pytest.raises(psycopg.errors.UniqueViolation), s.store.transaction():
        version = raw_version(c)
        if kind == "point":
            raw_assignment(c, version, point)
            raw_assignment(c, version, point)
        else:
            raw_chapter(c, version)
            raw_chapter(
                c,
                version,
                name="chapter" if kind == "normalized_title" else "second",
                order=1 if kind == "normalized_title" else 0,
            )


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("heads", "revision", -1),
        ("jobs", "attempt", -1),
        ("jobs", "mode", "invalid"),
        ("jobs", "status", "invalid"),
    ],
)
def test_database_check_constraints(catalog, table, column, value):
    s, repo = catalog
    if table == "heads":
        repo.publish(COURSE, **proposal(repo))
    else:
        repo.create_job(COURSE, mode="initial", base_version_id=None, snapshot_hash="hash")
    with pytest.raises(psycopg.errors.CheckViolation):
        s.store.connection.execute(f"UPDATE {PREFIX}{table} SET {column}=%s", (value,))


def test_database_one_nonterminal_job_and_repository_idempotency(catalog):
    s, repo = catalog
    args = dict(mode="initial", base_version_id=None, snapshot_hash="hash")
    first = repo.create_job(COURSE, **args)
    assert first["id"] == repo.create_job(COURSE, **args)["id"]
    with pytest.raises(CatalogConflict):
        repo.create_job(COURSE, **dict(args, snapshot_hash="different"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        s.store.connection.execute(
            f"INSERT INTO {PREFIX}jobs(user_id,course_id,mode,"
            "snapshot_hash) VALUES (%s,%s,'initial','second')",
            (USER, COURSE),
        )


def test_initial_incremental_history_and_stable_chapter_ids(catalog):
    s, repo = catalog
    initial = proposal(repo)
    v1 = repo.publish(COURSE, **initial)
    assert v1["revision"] == 1 and v1["created"]
    assert repo.current(COURSE)["version"]["id"] == v1["version_id"]
    incremental = proposal(
        repo,
        base=v1["version_id"],
        revision=1,
        mode="incremental",
        chapter_id=initial["chapters"][0].chapter_id,
    )
    v2 = repo.publish(COURSE, **incremental)
    assert v2["revision"] == 2 and v2["created"]
    assert repo.head(COURSE)["current_version_id"] == v2["version_id"]
    old = repo.version(COURSE, v1["version_id"])
    new = repo.version(COURSE, v2["version_id"])
    assert new["parent_version_id"] == old["id"]
    assert new["chapters"][0]["chapter_id"] == old["chapters"][0]["chapter_id"]
    assert old["point_count"] == new["point_count"] == 1
    assert catalog_counts(s.store.connection) == (1, 2, 2, 2)


def test_same_input_reuses_version_but_changed_output_is_conflict(catalog):
    s, repo = catalog
    args = proposal(repo)
    first = repo.publish(COURSE, **args)
    assert repo.publish(COURSE, **args) == dict(first, created=False)
    assert catalog_counts(s.store.connection) == (1, 1, 1, 1)
    with pytest.raises(CatalogConflict):
        repo.publish(COURSE, **dict(args, rules_version="different"))
    v2 = repo.publish(
        COURSE, **proposal(repo, base=first["version_id"], revision=1, mode="incremental")
    )
    with pytest.raises(CatalogConflict):
        repo.publish(COURSE, **args)
    assert repo.head(COURSE)["current_version_id"] == v2["version_id"]


@pytest.mark.parametrize("bad_base,bad_revision", [(True, 1), (False, 0)])
def test_stale_base_or_revision_is_rejected(catalog, bad_base, bad_revision):
    _, repo = catalog
    v1 = repo.publish(COURSE, **proposal(repo))
    with pytest.raises(CatalogConflict):
        repo.publish(
            COURSE,
            **proposal(
                repo,
                base=uuid4() if bad_base else v1["version_id"],
                revision=bad_revision,
                mode="incremental",
            ),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE knowledge_points SET name='changed'",
        "UPDATE knowledge_points SET description='changed'",
        "UPDATE knowledge_points SET status='inactive'",
        "UPDATE knowledge_facts SET fact_text='changed'",
        "UPDATE knowledge_facts SET status='inactive'",
        "UPDATE fact_evidence SET quote='changed'",
        "UPDATE fact_evidence SET support_status='uncertain'",
        "UPDATE documents SET data=jsonb_set(data,'{chapter}','\"changed\"')",
        "UPDATE documents SET data=jsonb_set(data,'{parse_status}','\"deleted\"')",
        "UPDATE material_locators SET data='{}'::jsonb",
    ],
)
def test_snapshot_input_changes_reject_publication(catalog, mutation):
    s, repo = catalog
    args = proposal(repo)
    s.store.connection.execute(mutation)
    with pytest.raises(CatalogSnapshotChanged):
        repo.publish(COURSE, **args)
    assert catalog_counts(s.store.connection) == (0, 0, 0, 0)


def test_new_visible_point_invalidates_snapshot(catalog):
    s, repo = catalog
    args = proposal(repo)
    parsed(s, "HTTP默认端口为80。", "port.pptx")
    process(s, [("端口", "HTTP默认端口配置", ["HTTP默认端口为80。"])])
    assert len(repo.snapshot(COURSE)["points"]) == 2
    with pytest.raises(CatalogSnapshotChanged):
        repo.publish(COURSE, **args)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "invalid",
        "foreign",
        "duplicate",
        "bad_hash",
        "bad_chapter",
        "duplicate_title",
        "duplicate_order",
    ],
)
def test_invalid_or_incomplete_proposals_never_publish(catalog, kind):
    s, repo = catalog
    args = proposal(repo)
    assignment = args["assignments"][0]
    if kind == "missing":
        args["assignments"] = []
    elif kind in {"invalid", "foreign"}:
        point = uuid4()
        if kind == "foreign":
            other_scopes(s.store)
            point = s.store.connection.execute(
                "INSERT INTO knowledge_points(record_key,user_id,course_id,data) "
                "VALUES ('foreign',%s,'other','{}') RETURNING id",
                (USER,),
            ).fetchone()["id"]
        args["assignments"] = [assignment.model_copy(update={"point_id": point})]
    elif kind == "duplicate":
        args["assignments"].append(assignment)
    elif kind == "bad_hash":
        args["assignments"] = [assignment.model_copy(update={"classification_input_hash": "bad"})]
    elif kind == "bad_chapter":
        args["assignments"] = [assignment.model_copy(update={"chapter_id": uuid4()})]
    else:
        args["chapters"].append(
            CatalogChapter(
                chapter_id=uuid4(),
                title="应用层" if kind == "duplicate_title" else "另一章",
                sort_order=1 if kind == "duplicate_title" else 0,
            )
        )
    with pytest.raises(CatalogSnapshotChanged if kind == "bad_hash" else CatalogInvalid):
        repo.publish(COURSE, **args)
    assert catalog_counts(s.store.connection) == (0, 0, 0, 0)


def test_empty_visible_set_and_unclassified_assignment(catalog):
    s, repo = catalog
    args = proposal(repo)
    args["chapters"] = []
    args["assignments"] = [
        args["assignments"][0].model_copy(
            update={
                "chapter_id": None,
                "assignment_method": "unclassified",
            }
        )
    ]
    first = repo.publish(COURSE, **args)
    assert repo.current(COURSE)["version"]["assignments"][0]["chapter_id"] is None
    s.store.connection.execute(
        "UPDATE documents SET data=jsonb_set(data,'{parse_status}','\"deleted\"')"
    )
    empty = proposal(repo, base=first["version_id"], revision=1, mode="incremental")
    empty["chapters"] = []
    second = repo.publish(COURSE, **empty)
    assert repo.version(COURSE, second["version_id"])["point_count"] == 0


@pytest.mark.parametrize("already_published", [False, True])
def test_database_exception_after_head_switch_rolls_back_everything(catalog, already_published):
    s, repo = catalog
    first = repo.publish(COURSE, **proposal(repo)) if already_published else None
    before = catalog_counts(s.store.connection)
    head_before = repo.head(COURSE)
    args = proposal(
        repo,
        base=first["version_id"] if first else None,
        revision=1 if first else 0,
        mode="incremental" if first else "initial",
    )
    job = repo.create_job(
        COURSE,
        mode=args["build_mode"],
        base_version_id=args["base_version_id"],
        snapshot_hash=args["snapshot_hash"],
    )
    lease = repo.claim()
    s.store.connection.execute(f"""
        CREATE FUNCTION reject_catalog_success() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.status='succeeded' THEN
          RAISE EXCEPTION 'injected failure after pointer switch'; END IF; RETURN NEW; END $$;
        CREATE TRIGGER reject_success BEFORE UPDATE ON {PREFIX}jobs FOR EACH ROW
          EXECUTE FUNCTION reject_catalog_success();
    """)
    with pytest.raises(psycopg.errors.RaiseException, match="injected failure"):
        repo.publish(COURSE, **args, job_id=job["id"], lease_token=lease["lease_token"])
    assert catalog_counts(s.store.connection) == before
    assert repo.head(COURSE) == head_before
    assert repo.job(COURSE, job["id"])["status"] == "running"


@pytest.mark.parametrize("table", ["versions", "chapters", "assignments"])
@pytest.mark.parametrize("operation", ["update", "delete", "append"])
def test_committed_catalog_history_cannot_mutate(catalog, table, operation):
    s, repo = catalog
    args = proposal(repo)
    result = repo.publish(COURSE, **args)
    c = s.store.connection
    with pytest.raises(psycopg.errors.CheckViolation):
        if operation == "update":
            column = {"versions": "rules_version", "chapters": "title", "assignments": "reason"}
            c.execute(f"UPDATE {PREFIX}{table} SET {column[table]}='changed'")
        elif operation == "delete":
            c.execute(f"DELETE FROM {PREFIX}{table}")
        elif table == "chapters":
            raw_chapter(c, result["version_id"], name="extra", order=1)
        elif table == "assignments":
            raw_assignment(c, result["version_id"], args["assignments"][0].point_id)
        else:
            # A changed transaction seal cannot reopen an existing version.
            c.execute(f"UPDATE {PREFIX}versions SET publication_xid=pg_current_xact_id()")
    assert catalog_counts(c) == (1, 1, 1, 1)


def test_account_purge_cascades_catalog_history_and_jobs(catalog):
    s, repo = catalog
    first = repo.publish(COURSE, **proposal(repo))
    repo.publish(COURSE, **proposal(repo, base=first["version_id"], revision=1, mode="incremental"))
    repo.create_job(COURSE, mode="initial", base_version_id=None, snapshot_hash="hash")
    s.store.connection.execute("DELETE FROM app_users WHERE id=%s", (USER,))
    assert catalog_counts(s.store.connection) == (0, 0, 0, 0)
    assert (
        s.store.connection.execute(f"SELECT count(*) AS n FROM {PREFIX}jobs").fetchone()["n"] == 0
    )
    assert counts(s) == (0, 0, 0)


def test_lease_checkpoint_retry_reclaim_and_success(catalog):
    s, repo = catalog
    args = proposal(repo)
    created = repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash=args["snapshot_hash"]
    )
    lease = repo.claim()
    assert lease["id"] == created["id"] and lease["attempt"] == 1
    checkpoint = repo.checkpoint(
        COURSE, lease["id"], lease["lease_token"], stage="planning", checkpoint={"chapters": []}
    )
    assert checkpoint["checkpoint"] == {"chapters": []}
    waiting = repo.fail(
        COURSE,
        lease["id"],
        lease["lease_token"],
        error_code="timeout",
        error_message="test",
        retry_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert waiting["status"] == "retry_wait" and repo.claim() is None
    s.store.connection.execute(f"UPDATE {PREFIX}jobs SET next_run_at=now()")
    second = repo.claim()
    assert second["attempt"] == 2 and second["lease_token"] != lease["lease_token"]
    assert second["checkpoint"] == {"chapters": []}
    with pytest.raises(CatalogLeaseLost):
        repo.checkpoint(COURSE, lease["id"], lease["lease_token"], stage="stale", checkpoint={})
    result = repo.publish(COURSE, **args, job_id=second["id"], lease_token=second["lease_token"])
    done = repo.job(COURSE, second["id"])
    assert done["status"] == "succeeded" and done["result_version_id"] == result["version_id"]
    assert done["lease_token"] is None and done["lease_until"] is None
    assert (
        repo.publish(COURSE, **args, job_id=second["id"], lease_token=second["lease_token"])[
            "created"
        ]
        is False
    )


@pytest.mark.parametrize("action", ["publish", "checkpoint", "fail"])
def test_expired_lease_cannot_write(catalog, action):
    s, repo = catalog
    args = proposal(repo)
    repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash=args["snapshot_hash"]
    )
    lease = repo.claim()
    s.store.connection.execute(f"UPDATE {PREFIX}jobs SET lease_until=now()-interval '1 second'")
    with pytest.raises(CatalogLeaseLost):
        if action == "publish":
            repo.publish(COURSE, **args, job_id=lease["id"], lease_token=lease["lease_token"])
        elif action == "checkpoint":
            repo.checkpoint(COURSE, lease["id"], lease["lease_token"], stage="test", checkpoint={})
        else:
            repo.fail(COURSE, lease["id"], lease["lease_token"], error_code="e", error_message="e")
    assert catalog_counts(s.store.connection) == (0, 0, 0, 0)
    replacement = repo.claim()
    assert replacement["attempt"] == 2 and replacement["lease_token"] != lease["lease_token"]
    failed = repo.fail(
        COURSE,
        replacement["id"],
        replacement["lease_token"],
        error_code="permanent",
        error_message="test",
    )
    assert failed["status"] == "failed" and repo.claim() is None


def test_concurrent_publishers_cannot_overwrite_current(catalog):
    s, repo = catalog
    barrier = Barrier(2)
    args = proposal(repo)

    def publish(rules):
        store = PostgresStore(s.store.database_url)
        store.bind_user(str(USER))
        try:
            barrier.wait(timeout=10)
            return CatalogRepository(store).publish(COURSE, **dict(args, rules_version=rules))
        except CatalogConflict:
            return "conflict"
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, rules) for rules in ("first", "second")]
        results = [future.result(timeout=15) for future in futures]
    assert sum(r == "conflict" for r in results) == 1
    assert catalog_counts(s.store.connection) == (1, 1, 1, 1)


def test_publication_waits_for_ingestion_advisory_lock(catalog):
    s, repo = catalog
    args = proposal(repo)
    started, finished = Event(), Event()

    def publish():
        store = PostgresStore(s.store.database_url)
        store.bind_user(str(USER))
        try:
            started.set()
            result = CatalogRepository(store).publish(COURSE, **args)
            finished.set()
            return result
        finally:
            store.close()

    with psycopg.connect(s.store.database_url, autocommit=True) as blocker:
        blocker.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s,0))", (f"knowledge:{USER}:{COURSE}",)
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(publish)
            try:
                assert started.wait(5)
                assert not finished.wait(0.2)
            finally:
                blocker.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s,0))",
                    (f"knowledge:{USER}:{COURSE}",),
                )
            assert future.result(timeout=10)["created"]


def test_claim_uses_skip_locked_and_isolates_users(catalog):
    s, repo = catalog
    other_scopes(s.store)
    first = repo.create_job(COURSE, mode="initial", base_version_id=None, snapshot_hash="one")
    second = repo.create_job("other", mode="initial", base_version_id=None, snapshot_hash="two")
    with psycopg.connect(s.store.database_url) as blocker:
        blocker.execute(f"SELECT id FROM {PREFIX}jobs WHERE id=%s FOR UPDATE", (first["id"],))
        assert repo.claim()["id"] == second["id"]
    assert repo.claim()["id"] == first["id"]
    store = PostgresStore(s.store.database_url)
    token = store.bind_user(str(SECOND_USER))
    try:
        other = CatalogRepository(store)
        assert other.claim() is None
        assert other.job(COURSE, first["id"]) is None
        assert other.head(COURSE) is None
    finally:
        store.reset_user(token)
        store.close()


def visible_knowledge(tree):
    """Point identity, facts and source locators. Catalog grouping is allowed to change."""
    points = [
        {
            key: point[key]
            for key in (
                "id",
                "name",
                "description",
                "importance_score",
                "fact_count",
                "fact_ids",
                "sources",
            )
        }
        for chapter in tree["chapters"]
        for point in chapter["points"]
    ]
    return (
        sorted(points, key=lambda point: point["id"]),
        tree["point_count"],
        tree["updated_at"],
        tree["build_status"],
    )


def test_catalog_preserves_existing_tree_assets_and_subsequent_builds(catalog):
    s, repo = catalog
    old_repo = KnowledgeRepository(s.store)
    before_tree = read_tree(old_repo, COURSE)
    before_counts = counts(s)
    before_data = {
        table: s.store.connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        for table in ("knowledge_points", "knowledge_facts", "fact_evidence")
    }
    repo.publish(COURSE, **proposal(repo))
    assert visible_knowledge(read_tree(old_repo, COURSE)) == visible_knowledge(before_tree)
    assert counts(s) == before_counts
    for table, rows in before_data.items():
        assert s.store.connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == rows
    parsed(s, "HTTP默认端口为80。", "new.pptx")
    job = process(s, [("端口", "HTTP默认端口配置", ["HTTP默认端口为80。"])])
    assert (
        s.store.connection.execute(
            "SELECT status FROM knowledge_build_jobs WHERE job_id=%s", (job["job_id"],)
        ).fetchone()["status"]
        == "succeeded"
    )
    assert read_tree(old_repo, COURSE)["point_count"] == 2
    assert repo.current(COURSE)["version"]["point_count"] == 1


def test_source_write_waits_until_snapshot_publication_commits(catalog):
    s, repo = catalog
    args = proposal(repo)
    snapshot_read, allow_commit, writer_started, writer_finished = (
        Event(),
        Event(),
        Event(),
        Event(),
    )

    class PausedRepository(CatalogRepository):
        def snapshot(self, course_id):
            result = super().snapshot(course_id)
            snapshot_read.set()
            assert allow_commit.wait(10)
            return result

    def publish():
        store = PostgresStore(s.store.database_url)
        store.bind_user(str(USER))
        try:
            return PausedRepository(store).publish(COURSE, **args)
        finally:
            store.close()

    def change_source():
        with psycopg.connect(s.store.database_url, autocommit=True) as connection:
            writer_started.set()
            connection.execute(
                "UPDATE documents SET data=jsonb_set(data,'{chapter}','\"concurrent\"')"
            )
            writer_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(publish)
        try:
            assert snapshot_read.wait(5)
            writer = pool.submit(change_source)
            assert writer_started.wait(5)
            assert not writer_finished.wait(0.2)
        finally:
            allow_commit.set()
        assert publication.result(timeout=10)["created"]
        writer.result(timeout=10)
    assert repo.current(COURSE)["version"]["knowledge_snapshot_hash"] == args["snapshot_hash"]
    assert repo.snapshot(COURSE)["hash"] != args["snapshot_hash"]


def test_lease_expiring_during_publish_rolls_back(catalog):
    s, repo = catalog
    args = proposal(repo)
    repo.create_job(
        COURSE, mode="initial", base_version_id=None, snapshot_hash=args["snapshot_hash"]
    )
    lease = repo.claim(lease_seconds=1)
    s.store.connection.execute(f"""
        CREATE FUNCTION delay_catalog_switch() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_sleep(1.1); RETURN NEW; END $$;
        CREATE TRIGGER delay_switch BEFORE UPDATE ON {PREFIX}heads FOR EACH ROW
          EXECUTE FUNCTION delay_catalog_switch();
    """)
    with pytest.raises(CatalogLeaseLost):
        repo.publish(COURSE, **args, job_id=lease["id"], lease_token=lease["lease_token"])
    assert catalog_counts(s.store.connection) == (0, 0, 0, 0)
    assert repo.job(COURSE, lease["id"])["status"] == "running"


def test_terminal_job_creation_remains_idempotent(catalog):
    _, repo = catalog
    args = proposal(repo)
    identity = dict(mode="initial", base_version_id=None, snapshot_hash=args["snapshot_hash"])
    job = repo.create_job(COURSE, **identity)
    lease = repo.claim()
    repo.publish(COURSE, **args, job_id=lease["id"], lease_token=lease["lease_token"])
    repeated = repo.create_job(COURSE, **identity)
    assert repeated["id"] == job["id"] and repeated["status"] == "succeeded"


def test_nested_transaction_can_build_catalog_and_bad_isolation_is_rejected(catalog):
    s, repo = catalog
    args = proposal(repo)
    with s.store.transaction():
        first = repo.publish(COURSE, **args)
    assert repo.head(COURSE)["current_version_id"] == first["version_id"]
    next_args = proposal(repo, base=first["version_id"], revision=1, mode="incremental")
    with pytest.raises(CatalogInvalid, match="READ COMMITTED"), s.store.transaction():
        s.store.connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        repo.publish(COURSE, **next_args)


def test_deleted_course_and_mismatching_job_cannot_publish(catalog):
    s, repo = catalog
    args = proposal(repo)
    repo.create_job(COURSE, mode="initial", base_version_id=None, snapshot_hash="outdated")
    lease = repo.claim()
    with pytest.raises(CatalogConflict):
        repo.publish(COURSE, **args, job_id=lease["id"], lease_token=lease["lease_token"])
    s.store.connection.execute("UPDATE courses SET status='deleted'")
    with pytest.raises(CatalogInvalid):
        repo.publish(COURSE, **args)
    assert catalog_counts(s.store.connection) == (0, 0, 0, 0)
