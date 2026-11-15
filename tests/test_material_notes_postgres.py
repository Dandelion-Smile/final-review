"""Immutable revision history and real concurrent optimistic saves."""
# ruff: noqa: F811 -- imported pytest fixtures.

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_course_knowledge_postgres import knowledge_system  # noqa: F401
from test_material_text_images_postgres import app_for, run, submit
from test_postgres_migrations import USER, database_url  # noqa: F401

from final_review.material_notes import NoteConflict
from final_review.postgres import PostgresStore

pytestmark = pytest.mark.integration


def test_save_idempotency_conflict_format_and_empty_body(knowledge_system, tmp_path):
    s = knowledge_system
    document = submit(s, tmp_path)
    run(s, "preview")
    original = s.store.read_material_note("course-1", document["document_id"])
    base = original["latest_saved_revision_id"]
    body = deepcopy(original["revision"]["body"])
    body["content"][0]["content"][0]["content"][0]["marks"] = [dict(type="bold")]
    saved = s.store.save_material_note(document, base, "format", body)
    assert saved["sync_status"] == "unchanged" and saved["revision_id"] != base
    assert s.store.save_material_note(document, base, "format", body) == saved
    assert s.store.note_head("course-1", document["document_id"])["text_generation"] == 1
    assert s.store.note_revision("course-1", document["document_id"], base) == original["revision"]
    with pytest.raises(NoteConflict):
        s.store.save_material_note(document, base, "other", body)
    with pytest.raises(NoteConflict):
        s.store.save_material_note(document, base, "format", dict(type="doc", content=[]))
    empty = s.store.save_material_note(
        document, saved["revision_id"], "clear", dict(type="doc", content=[])
    )
    assert empty["sync_status"] == "pending_integration"
    assert s.store.note_head("course-1", document["document_id"])["text_generation"] == 2
    assert s.store.claim_material_job("parse")["target_revision_id"] == base
    assert s.store.claim_material_job("parse") is None
    assert (
        s.store.connection.execute("SELECT count(*) AS n FROM knowledge_build_jobs").fetchone()["n"]
        == 0
    )


def test_two_connections_save_one_success_one_conflict(knowledge_system, tmp_path):
    s = knowledge_system
    document = submit(s, tmp_path)
    run(s, "preview")
    original = s.store.read_material_note("course-1", document["document_id"])
    barrier = Barrier(2)

    def save(index):
        store = PostgresStore(s.store.database_url)
        store.bind_user(str(USER))
        body = deepcopy(original["revision"]["body"])
        body["content"][0]["content"][0]["content"][0]["text"] = f"客户端 {index}"
        try:
            barrier.wait(timeout=10)
            return store.save_material_note(
                document, original["latest_saved_revision_id"], f"client-{index}", body
            )
        except NoteConflict as exc:
            return dict(conflict=exc.latest_revision_id)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [1, 2]))
    assert sum("conflict" in r for r in results) == 1
    winner = next(r for r in results if "revision_id" in r)["revision_id"]
    assert next(r for r in results if "conflict" in r)["conflict"] == winner
    assert s.store.note_head("course-1", document["document_id"])["save_sequence"] == 2


def test_database_rejects_revision_mutation_and_cross_document_heads(knowledge_system, tmp_path):
    s = knowledge_system
    one = submit(s, tmp_path)
    two = submit(s, tmp_path)
    run(s, "preview")
    run(s, "preview")
    note = s.store.read_material_note("course-1", one["document_id"])
    with pytest.raises(psycopg.Error, match="immutable"):
        s.store.connection.execute(
            "UPDATE material_note_revisions SET body='{}' WHERE revision_id=%s",
            (note["latest_saved_revision_id"],),
        )
    with pytest.raises(psycopg.Error, match="immutable"):
        s.store.connection.execute(
            "DELETE FROM material_note_revisions WHERE revision_id=%s",
            (note["latest_saved_revision_id"],),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation), s.store.transaction():
        s.store.connection.execute(
            "UPDATE material_note_heads SET latest_saved_revision_id=%s WHERE document_id=%s",
            (note["latest_saved_revision_id"], two["document_id"]),
        )


def test_edit_api_gate_and_conflict_keep_history(knowledge_system, tmp_path):
    s = knowledge_system
    document = submit(s, tmp_path)
    run(s, "preview")
    note = s.store.read_material_note("course-1", document["document_id"])
    app, _ = app_for(s)
    prefix = f"/api/courses/course-1/documents/{document['document_id']}/reading-note"
    payload = dict(
        base_revision_id=note["latest_saved_revision_id"],
        idempotency_key="one",
        body=dict(type="doc", content=[]),
    )
    with TestClient(app) as client:
        assert client.put(prefix, json=payload).status_code == 409
        s.settings.material_note_edit_enabled = True
        response = client.put(prefix, json=payload)
        assert response.status_code == 200, response.text
        assert client.put(prefix, json=payload).json() == response.json()
        payload["idempotency_key"] = "two"
        conflict = client.put(prefix, json=payload)
        assert conflict.status_code == 409
        assert conflict.json()["latest_revision_id"] == response.json()["revision_id"]
        assert (
            client.get(prefix + "/revisions/" + note["latest_saved_revision_id"]).status_code == 200
        )


def test_document_deletion_cascades_notes_requests_and_children(knowledge_system, tmp_path):
    s = knowledge_system
    document = submit(s, tmp_path)
    run(s, "preview")
    note = s.store.read_material_note("course-1", document["document_id"])
    s.store.save_material_note(
        document, note["latest_saved_revision_id"], "clear", dict(type="doc", content=[])
    )
    s.store.connection.execute(
        "DELETE FROM documents WHERE document_id=%s", (document["document_id"],)
    )
    for table in (
        "material_note_heads",
        "material_note_revisions",
        "material_note_save_requests",
        "material_jobs",
        "material_page_results",
    ):
        assert s.store.connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
