"""Real PostgreSQL proof for M0-R1.

Set TEST_DATABASE_URL to an isolated disposable database.  The fixture drops
only its public schema, so this suite can never accidentally target DATABASE_URL.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from final_review.api import create_app
from final_review.config import Settings
from final_review.domain import DomainConflict, DomainNotFound, DomainService
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration
TEST_URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).parents[1] / "db" / "migrations"
USER = UUID("00000000-0000-0000-0000-000000000001")
SECOND_USER = UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def database_url():
    if not TEST_URL:
        pytest.skip("set TEST_DATABASE_URL to run real PostgreSQL migration tests")
    with psycopg.connect(TEST_URL, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    return TEST_URL


def _sql(connection, name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
    connection.commit()


def _seed_course(connection, *, legacy: bool = False) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
            (USER, "migration@example.test", "hash"),
        )
        cursor.execute(
            "INSERT INTO courses(record_key,user_id,course_id,data) VALUES ('course-key',%s,'course-1',%s)",
            (USER, Jsonb({"course_id": "course-1", "user_id": str(USER)})),
        )
        if legacy:
            cursor.execute(
                "INSERT INTO fast_quiz_sessions(record_key,user_id,course_id,data) VALUES ('legacy-key',%s,'course-1',%s)",
                (USER, Jsonb({"session_id": "legacy-session"})),
            )
            cursor.execute(
                "INSERT INTO attempts(record_key,user_id,course_id,data) VALUES ('attempt-key',%s,'course-1',%s)",
                (USER, Jsonb({"session_id": "legacy-session"})),
            )
    connection.commit()


def test_empty_database_runner_is_versioned_and_repeatable(database_url):
    assert apply_migrations(database_url) == [
        "001_database_auth.sql",
        "002_m0_domain_contracts.sql",
        "003_m0_database_hardening.sql",
    ]
    assert apply_migrations(database_url) == []
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
        assert [row[0] for row in cursor.fetchall()] == [
            "001_database_auth.sql",
            "002_m0_domain_contracts.sql",
            "003_m0_database_hardening.sql",
        ]


def test_existing_001_002_with_legacy_attempt_upgrades(database_url):
    with psycopg.connect(database_url) as connection:
        _sql(connection, "001_database_auth.sql")
        _sql(connection, "002_m0_domain_contracts.sql")
        _seed_course(connection, legacy=True)
    assert apply_migrations(database_url) == ["003_m0_database_hardening.sql"]
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT legacy_session_id FROM attempts WHERE record_key='attempt-key'")
        assert cursor.fetchone()[0] == "legacy-session"


def test_database_rejects_m0_relation_and_immutability_violations(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_course(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO documents(record_key,user_id,course_id,document_id,data) VALUES ('doc-key',%s,'course-1','doc-1','{}')",
                (USER,),
            )
            cursor.execute(
                "INSERT INTO learning_assets(record_key,user_id,course_id,asset_id,data) VALUES ('asset-key',%s,'course-1','asset-1','{}')",
                (USER,),
            )
            cursor.execute(
                "INSERT INTO asset_revisions(record_key,user_id,course_id,revision_id,asset_id,revision_no,state,content_hash,data) VALUES ('revision-key',%s,'course-1','revision-1','asset-1',1,'confirmed','h',%s)",
                (
                    USER,
                    Jsonb(
                        {
                            "title": "locked",
                            "markdown": "body",
                            "source_document_ids": [],
                            "state": "confirmed",
                        }
                    ),
                ),
            )
            cursor.execute(
                "INSERT INTO material_versions(record_key,user_id,course_id,document_id,material_version_id,data) VALUES ('version-key',%s,'course-1','doc-1','version-1','{}')",
                (USER,),
            )
            cursor.execute(
                "INSERT INTO material_locators(user_id,course_id,material_version_id,locator_id,locator_kind,ordinal) VALUES (%s,'course-1','version-1','document','document',0)",
                (USER,),
            )
        connection.commit()

        def rejected(statement, params=()):
            with pytest.raises(psycopg.Error):
                with connection.cursor() as cursor:
                    cursor.execute(statement, params)
            connection.rollback()

        rejected(
            "INSERT INTO asset_revisions(record_key,user_id,course_id,revision_id,asset_id,revision_no,state,content_hash,data) VALUES ('duplicate',%s,'course-1','revision-2','asset-1',1,'draft','x','{}')",
            (USER,),
        )
        rejected(
            "INSERT INTO attempts(record_key,user_id,course_id,data) VALUES ('no-parent',%s,'course-1','{}')",
            (USER,),
        )
        rejected(
            "INSERT INTO source_references(record_key,user_id,course_id,asset_revision_id,material_version_id,locator_id,data) VALUES ('bad-source',%s,'course-1','missing','version-1','document','{}')",
            (USER,),
        )
        rejected(
            "UPDATE asset_revisions SET data=%s WHERE revision_id='revision-1'",
            (
                Jsonb(
                    {
                        "title": "rewritten",
                        "markdown": "body",
                        "source_document_ids": [],
                        "state": "confirmed",
                    }
                ),
            ),
        )


def test_postgres_owner_predicate_and_confirmation_consumption_are_atomic(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        for user_id, email in ((USER, "owner@example.test"), (SECOND_USER, "other@example.test")):
            cursor.execute(
                "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
                (user_id, email, "hash"),
            )
        connection.commit()

    first = PostgresStore(database_url)
    other = PostgresStore(database_url)
    first_token = first.bind_user(str(USER))
    try:
        first.put(
            "course",
            "course-1",
            {
                "course_id": "course-1",
                "user_id": str(USER),
                "name": "private",
                "status": "active",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        )
        owner_domain = DomainService(first, str(USER))
        preview = owner_domain.deletion_preview("course-1")

        other_token = other.bind_user(str(SECOND_USER))
        try:
            with pytest.raises(DomainNotFound):
                DomainService(other, str(SECOND_USER)).course("course-1")
        finally:
            other.reset_user(other_token)

        def consume(_):
            store = PostgresStore(database_url)
            thread_token = store.bind_user(str(USER))
            try:
                service = DomainService(store, str(USER))
                with service._transaction():
                    service._consume(
                        preview["confirmation_id"],
                        action="course.delete",
                        resource_type="course",
                        resource_id="course-1",
                        payload={
                            "materials": 0,
                            "exams": 0,
                            "learning_assets": 0,
                            "attempts": 0,
                        },
                    )
                return "deleted"
            except DomainConflict:
                return "conflict"
            finally:
                store.reset_user(thread_token)
                store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, range(2)))
        assert sorted(results) == ["conflict", "deleted"]

        rollback_preview = owner_domain.deletion_preview("course-1")
        with pytest.raises(RuntimeError, match="force rollback"):
            with owner_domain._transaction():
                owner_domain._consume(
                    rollback_preview["confirmation_id"],
                    action="course.delete",
                    resource_type="course",
                    resource_id="course-1",
                    payload={
                        "materials": 0,
                        "exams": 0,
                        "learning_assets": 0,
                        "attempts": 0,
                    },
                )
                raise RuntimeError("force rollback")
        assert first.get("confirmation", rollback_preview["confirmation_id"])["consumed_at"] is None
    finally:
        first.reset_user(first_token)
        first.close()
        other.close()


def test_database_api_returns_404_for_another_users_course(database_url):
    apply_migrations(database_url)
    settings = Settings(
        _env_file=None,
        auth_mode="database",
        auth_cookie_secure=False,
        database_url=database_url,
        llm_api_key="test-key",
        embedding_api_key="test-key",
        embedding_dimensions=3,
    )
    with TestClient(create_app(settings)) as owner, TestClient(create_app(settings)) as other:
        assert (
            owner.post(
                "/api/auth/sign-up",
                json={"email": "owner-api@example.test", "password": "password1"},
            ).status_code
            == 200
        )
        course = owner.post("/api/courses", json={"name": "private course"}).json()
        assert (
            other.post(
                "/api/auth/sign-up",
                json={"email": "other-api@example.test", "password": "password1"},
            ).status_code
            == 200
        )
        assert other.post(f"/api/courses/{course['course_id']}/deletion-preview").status_code == 404


def test_course_and_exams_work_without_model_keys(database_url):
    apply_migrations(database_url)
    settings = Settings(
        _env_file=None,
        auth_mode="database",
        auth_cookie_secure=False,
        database_url=database_url,
        llm_api_key="",
        embedding_api_key="",
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/auth/sign-up",
                json={"email": "no-models@example.test", "password": "password1"},
            ).status_code
            == 200
        )
        assert client.app.state.agent is None

        created = client.post("/api/courses", json={"name": "高等数学"})
        assert created.status_code == 200
        course_id = created.json()["course_id"]
        assert [item["course_id"] for item in client.get("/api/courses").json()["items"]] == [
            course_id
        ]

        for name in ("期中考试", "期末考试"):
            response = client.post(f"/api/courses/{course_id}/exams", json={"name": name})
            assert response.status_code == 200
        exams = client.get(f"/api/courses/{course_id}/exams")
        assert exams.status_code == 200
        assert {item["name"] for item in exams.json()["items"]} == {"期中考试", "期末考试"}


def test_postgres_snapshot_delete_removes_retrieval_chunks(database_url):
    apply_migrations(database_url)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
            (USER, "snapshot@example.test", "hash"),
        )
        connection.commit()

    store = PostgresStore(database_url)
    token = store.bind_user(str(USER))
    try:
        store.put(
            "course",
            "course-1",
            {
                "course_id": "course-1",
                "user_id": str(USER),
                "name": "course",
                "status": "active",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store.ingest(
            {
                "document_id": "document-1",
                "course_id": "course-1",
                "user_id": str(USER),
                "title": "source",
                "file_name": "source.md",
                "source_type": "teacher_ppt",
                "parse_status": "ready",
                "cleaned_markdown": "retrieval source",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "document-1",
                    "course_id": "course-1",
                    "title": "source",
                    "chapter": "",
                    "source_type": "teacher_ppt",
                    "content": "retrieval source",
                    "embedding": [1.0, 0.0, 0.0],
                }
            ],
        )
        domain = DomainService(store, str(USER))
        created = domain.create_asset(
            "course-1",
            {
                "asset_type": "note",
                "title": "note",
                "markdown": "# note",
                "source_document_ids": ["document-1"],
            },
        )
        domain.confirm_revision(created["asset"]["asset_id"], created["revision"]["revision_id"])
        preview = domain.material_deletion_preview("document-1")
        assert domain.delete_material(
            "document-1", preview["confirmation_id"], "retain_source_snapshot"
        ) == {"deleted": True, "retained_source_snapshot": True}
        assert store.scan("source_snapshot", {"course_id": "course-1"})
        assert store.get("document", "document-1")["parse_status"] == "deleted"
        assert store.search([1.0, 0.0, 0.0], "course-1", "", 1) == []
    finally:
        store.reset_user(token)
        store.close()
