"""Tree grouping, verified evidence, live source filtering and access isolation."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from test_course_knowledge_postgres import (  # noqa: F401
    STATELESS,
    knowledge_system,
    parsed,
    process,
)
from test_postgres_migrations import database_url  # noqa: F401

from final_review.api import create_app
from final_review.auth import CurrentUser, DatabaseAuth
from final_review.knowledge_store import KnowledgeRepository
from final_review.knowledge_tree import assemble_tree, chapter_key, read_tree


def source_row(point="point-1", chapter="第2章 HTTP", **changes):
    return {
        "id": point,
        "name": "HTTP无状态性",
        "description": "请求之间不自动保留状态",
        "importance_score": 0.8,
        "fact_id": "fact-1",
        "document_id": "doc-1",
        "title": "HTTP课件",
        "chapter": chapter,
        "locator_id": "chunk-1",
        "position_kind": "slide",
        "position": 3,
        "ordinal": 2,
        "updated_at": "2026-11-14T10:00:00+00:00",
        "document_updated_at": None,
        **changes,
    }


def test_tree_orders_chapters_naturally_and_keeps_unassigned_last():
    tree = assemble_tree(
        [
            source_row("p10", "第10章 实践"),
            source_row("p2", "第2章 HTTP"),
            source_row("p0", "  "),
            source_row("p1", "第1章 基础"),
        ]
    )
    assert [chapter["title"] for chapter in tree["chapters"]] == [
        "第1章 基础",
        "第2章 HTTP",
        "第10章 实践",
        "未分章",
    ]
    assert tree["point_count"] == 4
    assert chapter_key("第二章") < chapter_key("第十一章") < chapter_key("第一百章")


def test_merged_point_appears_once_and_retains_all_source_links():
    tree = assemble_tree(
        [
            source_row(chapter="第10章", document_id="doc-2", locator_id="chunk-2"),
            source_row(chapter="第2章"),
            source_row(chapter="第2章", fact_id="fact-2"),
        ]
    )
    assert tree["point_count"] == 1
    assert len(tree["chapters"]) == 1
    point = tree["chapters"][0]["points"][0]
    assert tree["chapters"][0]["title"] == "第2章"
    assert point["fact_count"] == 2
    assert point["fact_ids"] == ["fact-1", "fact-2"]
    assert len(point["sources"]) == 2
    assert point["sources"][0]["position"] == 3
    assert assemble_tree([]) == {"chapters": [], "point_count": 0, "updated_at": None}


@pytest.mark.integration
def test_tree_uses_only_verified_facts_and_accessible_current_documents(knowledge_system):  # noqa: F811
    s = knowledge_system
    document = parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    repo = KnowledgeRepository(s.store)
    assert read_tree(repo, "course-1")["point_count"] == 1
    assert read_tree(repo, "foreign")["point_count"] == 0
    s.store.connection.execute(
        "UPDATE documents SET data=data || %s WHERE document_id=%s",
        (Jsonb({"chapter": "第2章 HTTP"}), document),
    )
    tree = read_tree(repo, "course-1")
    assert tree["chapters"][0]["title"] == "第2章 HTTP"
    assert tree["chapters"][0]["points"][0]["sources"][0]["document_id"] == document
    s.store.connection.execute("UPDATE fact_evidence SET support_status='uncertain'")
    assert read_tree(repo, "course-1")["point_count"] == 0
    s.store.connection.execute("UPDATE fact_evidence SET support_status='supported'")
    s.store.connection.execute("UPDATE knowledge_facts SET status='inactive'")
    assert read_tree(repo, "course-1")["point_count"] == 0
    s.store.connection.execute("UPDATE knowledge_facts SET status='supported'")
    s.store.connection.execute(
        "UPDATE documents SET data=data || %s WHERE document_id=%s",
        (Jsonb({"parse_status": "deleted"}), document),
    )
    assert read_tree(repo, "course-1")["point_count"] == 0


@pytest.mark.integration
def test_tree_endpoint_requires_owner_and_returns_actual_knowledge(knowledge_system):  # noqa: F811
    s = knowledge_system
    parsed(s)
    process(s, [("HTTP", "HTTP无状态性", [STATELESS])])
    s.settings.auth_mode = "database"
    s.settings.auth_cookie_secure = False
    from pydantic import SecretStr

    s.settings.api_token = SecretStr("")
    with TestClient(create_app(s.settings)) as client:
        assert client.get("/api/courses/course-1/knowledge-tree").status_code == 401
        auth = DatabaseAuth(s.settings)
        auth.setup()
        try:
            with auth.transaction() as cursor:
                session = auth._new_session(cursor, CurrentUser(str(s.store._user())))
            client.cookies.set(s.settings.auth_cookie_name, session)
            response = client.get("/api/courses/course-1/knowledge-tree")
            assert response.status_code == 200, response.text
            point = response.json()["chapters"][0]["points"][0]
            assert point["name"] == "HTTP"
            assert point["fact_count"] == 1
            assert client.get("/api/courses/foreign/knowledge-tree").status_code == 404
            other = str(uuid4())
            with auth.transaction() as cursor:
                cursor.execute(
                    "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,'hash')",
                    (other, f"{other}@example.test"),
                )
                other_session = auth._new_session(cursor, CurrentUser(other))
            client.cookies.set(s.settings.auth_cookie_name, other_session)
            assert client.get("/api/courses/course-1/knowledge-tree").status_code == 404
        finally:
            auth.close()
