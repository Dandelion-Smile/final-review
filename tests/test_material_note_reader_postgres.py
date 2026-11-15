"""UI contract: canonical save responses and body-free background polling."""
# ruff: noqa: F811 -- imported fixtures for pytest discovery.

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_course_knowledge_postgres import knowledge_system  # noqa: F401
from test_material_text_images_postgres import app_for, run, submit
from test_postgres_migrations import database_url  # noqa: F401

pytestmark = pytest.mark.integration


def test_reader_bootstrap_and_status_never_expose_local_resources(knowledge_system, tmp_path):
    s = knowledge_system
    document = submit(s, tmp_path)
    run(s, "preview")
    app, _ = app_for(s)
    prefix = f"/api/courses/course-1/documents/{document['document_id']}/reading-note"
    with TestClient(app) as client:
        note = client.get(prefix).json()
        assert note["initial_revision_id"] == note["revision"]["revision_id"]
        assert note["document"]["title"] == document["title"]
        assert note["pages"][0]["page_number"] == 1
        assert note["editable"] is False
        summary = client.get(prefix + "/status").json()
        assert summary["note_status"] == "ready"
        assert "body" not in summary and "revision" not in summary
        assert "file_path" not in str(note) and "lease_token" not in str(note)
        run(s, "parse")
        summary = client.get(prefix + "/status").json()
        assert summary["revision_numbers"][summary["indexed_revision_id"]] == 1


def test_save_returns_normalized_editor_provenance_and_keeps_initial_index(
    knowledge_system, tmp_path
):
    s = knowledge_system
    s.settings.material_note_edit_enabled = True
    document = submit(s, tmp_path)
    run(s, "preview")
    run(s, "parse")
    app, _ = app_for(s)
    prefix = f"/api/courses/course-1/documents/{document['document_id']}/reading-note"
    with TestClient(app) as client:
        original = client.get(prefix).json()
        body = deepcopy(original["revision"]["body"])
        body["content"][0]["content"][0]["content"][0]["text"] = "来自网页编辑器的修改"
        payload = dict(
            base_revision_id=original["latest_saved_revision_id"],
            idempotency_key="browser-save",
            body=body,
        )
        response = client.put(prefix, json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["revision"]["revision_no"] == 2
        assert result["revision"]["body"]["content"][0]["attrs"]["content_origin"] == "user_edit"
        assert result["indexed_revision_id"] == original["initial_revision_id"]
        assert result["sync_status"] == "pending_integration"
        assert client.put(prefix, json=payload).json() == result
