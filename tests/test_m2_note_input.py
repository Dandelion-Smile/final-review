import pytest
from fastapi.testclient import TestClient

from final_review.agent import FinalReviewAgent, SessionConflict
from final_review.api import create_app
from final_review.schemas import AgentRequest, NoteInput, ResumeNoteRequest


def ready_document(system, *, owner="local-user", course="net", status="ready"):
    system.store.put(
        "course",
        "net",
        {"course_id": "net", "user_id": "local-user", "name": "网络", "status": "active"},
    )
    document = system.store.scan("document", {"course_id": "net"})[0].copy()
    document.update(user_id=owner, course_id=course, parse_status=status)
    system.store.put("document", document["document_id"], document)
    return document


@pytest.mark.parametrize("kind", ["chapter", "key_points", "qa_cards", "mnemonic"])
def test_four_note_types_create_draft(system, kind):
    document = ready_document(system)
    result = system.invoke(
        AgentRequest(
            course_id="net",
            session_id=kind,
            message="生成笔记",
            intent="note",
            note_input=NoteInput(note_type=kind, scope="TCP", duration_minutes=10,
                                 source_document_ids=[document["document_id"]]),
        ),
        "local-user",
    )
    assert result.status == "completed"
    assert result.draft["note_type"] == kind
    revision = system.store.get("asset_revision", result.draft["revision_id"])
    assert revision["note_type"] == kind
    assert revision["state"] == "draft"
    assert revision["source_document_ids"] == [document["document_id"]]


def test_minimal_clarification_and_restart_resume(system):
    document = ready_document(system)
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
    assert first.prompt["required"] == ["duration_minutes", "source_document_ids"]
    restarted = FinalReviewAgent(system.store, system.kb, system.model, system.settings)
    still_missing = restarted.resume_note(
        ResumeNoteRequest(
            course_id="net",
            session_id="note-clarify",
            note_input=NoteInput(scope="TCP"),
        ),
        "local-user",
    )
    assert still_missing.prompt["required"] == ["duration_minutes", "source_document_ids"]
    result = restarted.resume_note(
        ResumeNoteRequest(
            course_id="net",
            session_id="note-clarify",
            note_input=NoteInput(scope="TCP", duration_minutes=10,
                                 source_document_ids=[document["document_id"]]),
        ),
        "local-user",
    )
    assert result.status == "completed"
    assert result.draft
    assert restarted.read("net", "note-clarify").draft == result.draft


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
        ResumeNoteRequest(course_id="net", session_id="note-source",
                          note_input=NoteInput(source_document_ids=[document["document_id"]])),
        "local-user",
    )
    assert result.status == "completed"


def test_auto_route_extracts_message_and_explicit_fields_take_precedence(system):
    document = ready_document(system)
    system.model.note_request = lambda request: {
        "note_type": "chapter",
        "scope": "TCP",
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
            note_input=NoteInput(note_type="key_points", duration_minutes=10,
                                 source_document_ids=[document["document_id"]]),
        ),
        "local-user",
    )
    assert result.status == "completed"
    assert (
        system.store.get("asset_revision", result.draft["revision_id"])["note_type"] == "key_points"
    )


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
    document = ready_document(system)
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
        assert first.json()["prompt"]["required"] == ["duration_minutes", "source_document_ids"]
        result = client.post(
            "/agent/resume-note",
            json={
                "course_id": "net",
                "session_id": "note-http",
                "note_input": {"scope": "TCP", "duration_minutes": 15,
                               "source_document_ids": [document["document_id"]]},
            },
        )
        assert result.status_code == 200
        assert result.json()["status"] == "completed"
        assert result.json()["draft"]["asset_id"]


def test_note_chat_keeps_prompt_and_draft_in_renamable_history(system):
    document = ready_document(system)
    with TestClient(create_app(system.settings, system)) as client:
        start = client.post(
            "/agent/invoke?conversation_id=chat-note-history",
            json={"course_id": "net", "session_id": "note-history",
                  "message": "生成笔记", "intent": "note"},
        )
        assert start.status_code == 200
        assert start.json()["status"] == "needs_input"
        history_url = "/api/courses/net/conversations/chat-note-history/messages"
        history = client.get(history_url).json()
        assert [item["role"] for item in history["items"]] == ["user", "assistant"]
        assert history["active_note"]["session_id"] == "note-history"
        assert history["active_note"]["status"] == "needs_input"
        assert client.patch(
            "/api/courses/net/conversations/chat-note-history",
            json={"title": "TCP 笔记对话"},
        ).status_code == 200
        resumed = client.post(
            "/agent/resume-note?conversation_id=chat-note-history&event_id=resume-once",
            json={"course_id": "net", "session_id": "note-history",
                  "note_input": {"note_type": "chapter", "scope": "TCP",
                                 "duration_minutes": 10,
                                 "source_document_ids": [document["document_id"]]}},
        )
        assert resumed.status_code == 200, resumed.text
        draft = resumed.json()["draft"]
        history = client.get(history_url).json()
        assert [item["role"] for item in history["items"]] == [
            "user", "assistant", "user", "assistant"
        ]
        assert history["items"][-1]["draft"] == draft
        assert history["active_note"] is None
        repeated = client.post(
            "/agent/resume-note?conversation_id=chat-note-history&event_id=resume-once",
            json={"course_id": "net", "session_id": "note-history",
                  "note_input": {"note_type": "chapter", "scope": "TCP",
                                 "duration_minutes": 10,
                                 "source_document_ids": [document["document_id"]]}},
        )
        assert repeated.status_code == 200
        assert repeated.json()["draft"] == draft
        assert len(client.get(history_url).json()["items"]) == 4
        listing = client.get("/api/courses/net/conversations").json()["items"]
        assert listing[0]["title"] == "TCP 笔记对话"
        recovered = client.post(
            "/agent/recover?course_id=net&session_id=note-history"
            "&conversation_id=chat-note-history"
        )
        assert recovered.status_code == 200
        assert recovered.json()["draft"] == draft
        assert len(client.get(history_url).json()["items"]) == 4


def test_failed_note_start_can_recover_without_duplicate_messages(system, monkeypatch):
    ready_document(system)
    original = system.invoke
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(system, "invoke", fail_once)
    with TestClient(create_app(system.settings, system), raise_server_exceptions=False) as client:
        response = client.post(
            "/agent/invoke?conversation_id=chat-recover",
            json={"course_id": "net", "session_id": "note-recover",
                  "message": "生成笔记", "intent": "note"},
        )
        assert response.status_code == 500
        history_url = "/api/courses/net/conversations/chat-recover/messages"
        assert client.get(history_url).json()["active_note"]["status"] == "failed"
        recovered = client.post(
            "/agent/recover?course_id=net&session_id=note-recover"
            "&conversation_id=chat-recover"
        )
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "needs_input"
        history = client.get(history_url).json()
        assert [item["role"] for item in history["items"]] == ["user", "assistant"]
        assert history["active_note"]["status"] == "needs_input"


def test_cancel_note_closes_pending_chat_and_blocks_old_session(system):
    ready_document(system)
    with TestClient(create_app(system.settings, system)) as client:
        start = client.post(
            "/agent/invoke?conversation_id=chat-cancel",
            json={"course_id": "net", "session_id": "note-to-cancel",
                  "message": "生成笔记", "intent": "note"},
        )
        assert start.status_code == 200
        assert start.json()["status"] == "needs_input"
        url = "/agent/cancel-note?conversation_id=chat-cancel"
        payload = {"course_id": "net", "session_id": "note-to-cancel"}
        assert client.post(url, json=payload).json() == {"cancelled": True}
        assert client.post(url, json=payload).json() == {"cancelled": True}
        history = client.get("/api/courses/net/conversations/chat-cancel/messages").json()
        assert history["active_note"] is None
        assert [item["role"] for item in history["items"]] == [
            "user", "assistant", "assistant"
        ]
        assert "已取消笔记生成" in history["items"][-1]["content"]
        assert client.post(
            "/agent/resume-note?conversation_id=chat-cancel",
            json={"course_id": "net", "session_id": "note-to-cancel"},
        ).status_code == 409
        assert client.post(
            "/agent/recover?course_id=net&session_id=note-to-cancel"
        ).status_code == 409
        new_note = client.post(
            "/agent/invoke?conversation_id=chat-cancel",
            json={"course_id": "net", "session_id": "note-after-cancel",
                  "message": "重新生成笔记", "intent": "note"},
        )
        assert new_note.status_code == 200
        assert new_note.json()["status"] == "needs_input"


def test_note_requires_explicit_material_selection(system):
    ready_document(system)
    response = system.invoke(
        AgentRequest(course_id="net", session_id="explicit-source", message="生成笔记",
                     intent="note", note_input=NoteInput(note_type="key_points",
                                                           duration_minutes=10)),
        "local-user",
    )
    assert response.status == "needs_input"
    assert response.prompt["required"] == ["source_document_ids"]
    assert not system.store.scan("learning_asset", {"course_id": "net"})


def test_chapter_request_rejects_unrelated_selected_file(system):
    document = ready_document(system)
    document["file_name"] = "3.HTTP协议.pptx"
    system.store.put("document", document["document_id"], document)
    with pytest.raises(ValueError, match="所选资料中未找到.*第一章"):
        system.invoke(
            AgentRequest(course_id="net", session_id="wrong-chapter", message="生成笔记",
                         intent="note", note_input=NoteInput(
                             note_type="chapter", scope="第一章", duration_minutes=10,
                             source_document_ids=[document["document_id"]],
                         )),
            "local-user",
        )


def test_note_evidence_is_balanced_across_only_selected_files(system):
    first = ready_document(system)
    second = {**first, "document_id": "second-document", "title": "第二份讲义",
              "file_name": "第二份讲义.pptx"}
    system.store.put("document", second["document_id"], second)
    original_chunk = next(chunk for chunk in system.store.chunks
                          if chunk["document_id"] == first["document_id"])
    system.store.chunks = [
        {**original_chunk, "document_id": document["document_id"],
         "chunk_id": f"{document['document_id']}-{ordinal}",
         "chunk_ordinal": ordinal, "content": f"TCP 三次握手资料 {ordinal}"}
        for document in (first, second) for ordinal in range(30)
    ]
    seen = []
    original_note = system.model.note

    def capture_note(data):
        seen.append(data["evidence"])
        return original_note(data)

    system.model.note = capture_note
    result = system.invoke(
        AgentRequest(course_id="net", session_id="balanced", message="生成笔记",
                     intent="note", note_input=NoteInput(
                         note_type="key_points", duration_minutes=10,
                         source_document_ids=[first["document_id"], second["document_id"]],
                     )),
        "local-user",
    )
    assert result.status == "completed"
    assert len(seen) == 4
    assert all(len(batch) == 10 for batch in seen)
    selected = [item for batch in seen for item in batch]
    assert [item["document_id"] for item in selected[:4]] == [
        first["document_id"], second["document_id"],
        first["document_id"], second["document_id"],
    ]
    assert {item["document_id"] for item in selected} == {
        first["document_id"], second["document_id"]
    }


def test_first_chapter_request_excludes_selected_third_chapter_file(system):
    first = ready_document(system)
    first["file_name"] = "1.HTML与CSS.pptx"
    system.store.put("document", first["document_id"], first)
    third = {**first, "document_id": "third-document", "file_name": "3.HTTP协议.pptx"}
    system.store.put("document", third["document_id"], third)
    original_chunk = next(chunk for chunk in system.store.chunks
                          if chunk["document_id"] == first["document_id"])
    system.store.chunks.append({**original_chunk, "document_id": third["document_id"],
                                "chunk_id": "third-chunk", "content": "HTTP 状态码"})
    seen = []
    original_note = system.model.note

    def capture_note(data):
        seen.append(data["evidence"])
        return original_note(data)

    system.model.note = capture_note
    result = system.invoke(
        AgentRequest(course_id="net", session_id="first-chapter", message="生成笔记",
                     intent="note", note_input=NoteInput(
                         note_type="chapter", scope="第一章", duration_minutes=10,
                         source_document_ids=[first["document_id"], third["document_id"]],
                     )),
        "local-user",
    )
    assert result.status == "completed"
    assert {item["document_id"] for item in seen[0]} == {first["document_id"]}


def test_optional_writing_focus_promotes_relevant_late_chunk(system):
    document = ready_document(system)
    original_chunk = next(chunk for chunk in system.store.chunks
                          if chunk["document_id"] == document["document_id"])
    system.store.chunks = [
        {**original_chunk, "chunk_id": f"focus-{ordinal}", "chunk_ordinal": ordinal,
         "content": "TCP 三次握手与状态码" if ordinal == 44 else f"TCP 三次握手基础 {ordinal}"}
        for ordinal in range(45)
    ]
    seen = []
    original_note = system.model.note

    def capture_note(data):
        seen.append(data["evidence"])
        return original_note(data)

    system.model.note = capture_note
    result = system.invoke(
        AgentRequest(course_id="net", session_id="focus", message="生成笔记",
                     intent="note", note_input=NoteInput(
                         note_type="key_points", scope="侧重状态码", duration_minutes=10,
                         source_document_ids=[document["document_id"]],
                     )),
        "local-user",
    )
    assert result.status == "completed"
    assert seen[0][0]["chunk_id"] == "focus-44"
