import pytest
from fastapi.testclient import TestClient

from final_review.agent import FinalReviewAgent, SessionConflict
from final_review.api import create_app
from final_review.schemas import AgentRequest, NoteInput, ResumeNoteRequest


def ready_document(system, *, owner="local-user", course="net", status="ready"):
    document = system.store.scan("document", {"course_id": "net"})[0].copy()
    document.update(user_id=owner, course_id=course, parse_status=status)
    system.store.put("document", document["document_id"], document)
    return document


@pytest.mark.parametrize("kind", ["chapter", "key_points", "qa_cards", "mnemonic"])
def test_four_note_types_return_configuration_without_draft(system, kind):
    document = ready_document(system)
    result = system.invoke(
        AgentRequest(
            course_id="net",
            session_id=kind,
            message="生成笔记",
            intent="note",
            note_input=NoteInput(note_type=kind, scope="TCP", duration_minutes=10),
        ),
        "local-user",
    )
    assert result.status == "configured"
    assert result.note_config["note_type"] == kind
    assert result.note_config["audience_level"] == "intermediate"
    assert result.note_config["source_document_ids"] == [document["document_id"]]
    assert not system.store.scan("learning_asset", {"course_id": "net"})


def test_minimal_clarification_and_restart_resume(system):
    ready_document(system)
    first = system.invoke(
        AgentRequest(
            course_id="net",
            session_id="note-clarify",
            message="生成笔记",
            intent="note",
            note_input=NoteInput(note_type="key_points"),
        ),
        "local-user",
    )
    assert first.status == "needs_input"
    assert first.prompt["required"] == ["scope", "duration_minutes"]
    restarted = FinalReviewAgent(system.store, system.kb, system.model, system.settings)
    still_missing = restarted.resume_note(
        ResumeNoteRequest(
            course_id="net",
            session_id="note-clarify",
            note_input=NoteInput(scope="TCP"),
        ),
        "local-user",
    )
    assert still_missing.prompt["required"] == ["duration_minutes"]
    result = restarted.resume_note(
        ResumeNoteRequest(
            course_id="net",
            session_id="note-clarify",
            note_input=NoteInput(scope="TCP", duration_minutes=10),
        ),
        "local-user",
    )
    assert result.status == "configured"
    assert result.note_config["scope"] == "TCP"
    assert result.note_config["duration_minutes"] == 10
    assert not system.store.scan("learning_asset", {"course_id": "net"})


def test_missing_sources_can_resume_after_material_becomes_ready(system):
    document = ready_document(system, status="failed")
    request = AgentRequest(
        course_id="net",
        session_id="note-source",
        message="生成笔记",
        intent="note",
        note_input=NoteInput(note_type="chapter", scope="TCP", duration_minutes=10),
    )
    first = system.invoke(request, "local-user")
    assert first.prompt["required"] == ["source_document_ids"]
    document["parse_status"] = "ready"
    system.store.put("document", document["document_id"], document)
    result = system.resume_note(
        ResumeNoteRequest(course_id="net", session_id="note-source"), "local-user"
    )
    assert result.status == "configured"


def test_auto_route_extracts_message_and_explicit_fields_take_precedence(system):
    ready_document(system)
    system.model.note_request = lambda request: {
        "note_type": "chapter",
        "scope": "第三章",
        "duration_minutes": 20,
        "emphasis": ["定义"],
        "audience_level": "beginner",
        "source_types": [],
    }
    result = system.invoke(
        AgentRequest(
            course_id="net",
            session_id="note-auto",
            message="生成笔记",
            note_input=NoteInput(note_type="key_points", duration_minutes=10),
        ),
        "local-user",
    )
    assert result.status == "configured"
    assert result.note_config["note_type"] == "key_points"
    assert result.note_config["scope"] == "第三章"
    assert result.note_config["duration_minutes"] == 10
    assert result.note_config["emphasis"] == ["定义"]


@pytest.mark.parametrize(
    "owner,course,status",
    [
        ("another-user", "net", "ready"),
        ("local-user", "other", "ready"),
        ("local-user", "net", "failed"),
    ],
)
def test_explicit_unavailable_source_is_rejected(system, owner, course, status):
    document = ready_document(system, owner=owner, course=course, status=status)
    with pytest.raises(ValueError, match="所选资料"):
        system.invoke(
            AgentRequest(
                course_id="net",
                session_id="bad-source",
                message="生成笔记",
                intent="note",
                note_input=NoteInput(
                    note_type="chapter",
                    scope="TCP",
                    duration_minutes=10,
                    source_document_ids=[document["document_id"]],
                ),
            ),
            "local-user",
        )


def test_source_type_filter_and_owner_on_resume(system):
    ready_document(system)
    first = system.invoke(
        AgentRequest(
            course_id="net",
            session_id="note-filter",
            message="生成笔记",
            intent="note",
            note_input=NoteInput(
                note_type="chapter", scope="TCP", duration_minutes=10, source_types=["past_exam"]
            ),
        ),
        "local-user",
    )
    assert first.prompt["required"] == ["source_document_ids"]
    with pytest.raises(SessionConflict, match="所有者"):
        system.resume_note(
            ResumeNoteRequest(course_id="net", session_id="note-filter"), "another-user"
        )


def test_note_api_contract(system):
    ready_document(system)
    with TestClient(create_app(system.settings, system)) as client:
        first = client.post(
            "/agent/invoke",
            json={
                "course_id": "net",
                "session_id": "note-http",
                "message": "生成笔记",
                "intent": "note",
                "note_input": {"note_type": "qa_cards"},
            },
        )
        assert first.status_code == 200
        assert first.json()["prompt"]["required"] == ["scope", "duration_minutes"]
        result = client.post(
            "/agent/resume-note",
            json={
                "course_id": "net",
                "session_id": "note-http",
                "note_input": {"scope": "TCP", "duration_minutes": 15},
            },
        )
        assert result.status_code == 200
        assert result.json()["status"] == "configured"
