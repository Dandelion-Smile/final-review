import pytest

from final_review.agent import FinalReviewAgent, SessionConflict
from final_review.schemas import AgentRequest, ResumeRequest, Submission


def request(**kwargs):
    return AgentRequest(course_id="net", session_id="test", message="请出题", **kwargs)


def start_quiz(system):
    return system.invoke(request(intent="quiz", exam_profile={"question_types": ["short_answer"]}))


def answers_for(system, text="建立连接"):
    return {q["id"]: text for q in system.recover("net", "test").questions}


def test_missing_profile_interrupt_and_restart(system):
    assert system.invoke(request(intent="quiz")).status == "needs_input"
    # Reconstruct the graph AND saver. Recovery must use persisted data rather than object memory.
    restarted = FinalReviewAgent(system.store, system.kb, system.model, system.settings)
    result = restarted.resume_profile(
        ResumeRequest(
            course_id="net",
            session_id="test",
            exam_profile={"question_types": ["short_answer"]},
        )
    )
    assert result.status == "awaiting_answers"
    assert len(result.questions) == 1
    assert "reference_answer" not in result.questions[0]
    assert not result.citations  # evidence text could disclose the answer


def test_quiz_grade_feedback_and_idempotency(system):
    assert start_quiz(system).status == "awaiting_answers"
    submission = Submission(course_id="net", session_id="test", answers=answers_for(system))
    restarted = FinalReviewAgent(system.store, system.kb, system.model, system.settings)
    result = restarted.evaluate(submission)
    assert result.assessment["score"] == 30
    assert result.weak_points == ["三次握手"]
    assert result.assessment["reference_questions"][0]["reference_answer"]
    assert restarted.evaluate(submission) == result
    assert system.model.grading_calls == 1
    start_quiz(restarted)
    assert system.model.seen_weak_points == ["三次握手"]


def test_wrong_ids_do_not_consume_interrupt(system):
    start_quiz(system)
    with pytest.raises(ValueError, match="ID"):
        system.evaluate(Submission(course_id="net", session_id="test", answers={"wrong": "答案"}))
    assert system.recover("net", "test").status == "awaiting_answers"
    assert (
        system.evaluate(
            Submission(
                course_id="net",
                session_id="test",
                answers=answers_for(system),
            )
        ).status
        == "completed"
    )


def test_active_session_not_overwritten(system):
    start_quiz(system)
    with pytest.raises(SessionConflict):
        system.invoke(request(intent="ask"))


def test_provider_failure_recover_from_checkpoint(system):
    start_quiz(system)
    system.model.fail_grade_once = True
    with pytest.raises(RuntimeError, match="outage"):
        system.evaluate(Submission(course_id="net", session_id="test", answers=answers_for(system)))
    restarted = FinalReviewAgent(system.store, system.kb, system.model, system.settings)
    assert restarted.recover("net", "test").status == "completed"
    assert system.model.retrieval_calls == 1  # completed retrieval was not repeated


@pytest.mark.parametrize("bad_citations,unsupported", [(True, False), (False, True)])
def test_bounded_evidence_repair(system, bad_citations, unsupported):
    system.model.bad_citations = bad_citations
    system.model.unsupported = unsupported
    result = system.invoke(request(intent="ask"))
    assert result.status == "insufficient_evidence"
    assert system.model.retrieval_calls == 1
    state = system.graph.get_state(system._config(system._key("net", "test"))).values
    assert state["attempts"] == system.settings.max_repairs + 1
    assert "回答未通过证据校验" in result.answer
    assert not result.citations


@pytest.mark.parametrize("needs_more_evidence", [False, True])
def test_ask_chooses_repair_stage(system, monkeypatch, needs_more_evidence):
    answer_text = "缓存条目的有效期为五分钟。"
    retrieve_calls = []

    def retrieve(_request, _kb, broaden=False):
        retrieve_calls.append(broaden)
        content = "缓存可减少重复读取。" if needs_more_evidence and not broaden else answer_text
        return [
            {
                "chunk_id": "cache-expanded" if broaden else "cache-initial",
                "document_id": "cache-guide",
                "title": "缓存说明",
                "course_id": "cache",
                "chapter": "缓存",
                "source_type": "ai_supplement",
                "content": content,
                "similarity": 0.9,
            }
        ]

    def answer(data):
        source = data["evidence"][0]
        text = source["content"]
        if not needs_more_evidence and not data["issues_to_fix"]:
            text += "这是考试必考点。"
        return {
            "answer": text,
            "citations": [{"chunk_id": source["chunk_id"], "quote": source["content"]}],
        }

    def verify(data):
        supported = data["output"]["answer"] == answer_text
        issue = "缺少有效期资料" if needs_more_evidence else "无考试依据"
        return {
            "supported": supported,
            "issues": [] if supported else [issue],
            "needs_more_evidence": needs_more_evidence and not supported,
        }

    monkeypatch.setattr(system.model, "retrieve", retrieve)
    monkeypatch.setattr(system.model, "answer", answer)
    monkeypatch.setattr(system.model, "verify", verify)
    result = system.invoke(
        AgentRequest(course_id="cache", session_id="repair", message="缓存多久过期？", intent="ask")
    )
    assert result.status == "completed"
    assert result.answer == answer_text
    assert result.citations[0].content == answer_text
    assert retrieve_calls == ([False, True] if needs_more_evidence else [False])


def test_no_evidence_does_not_generate(system):
    result = system.invoke(
        AgentRequest(
            course_id="unrelated",
            session_id="test",
            message="解释 TCP",
            intent="ask",
        )
    )
    assert result.status == "insufficient_evidence"
    assert "资料不足以回答" in result.answer


@pytest.mark.parametrize("missing_evidence", [False, True])
def test_ask_uncited_abstention_is_verified_and_bounded(system, monkeypatch, missing_evidence):
    original_answer = system.model.answer
    answers = []

    def answer(data):
        answers.append(data)
        if missing_evidence or len(answers) == 1:
            return {"answer": "材料未提供这项信息。", "answerable": False, "citations": []}
        return original_answer(data)

    monkeypatch.setattr(system.model, "answer", answer)
    monkeypatch.setattr(
        system.model,
        "verify",
        lambda data: {
            "supported": True,
            "issues": [],
            "needs_more_evidence": missing_evidence,
        },
    )
    result = system.invoke(request(intent="ask"))
    assert len(answers) == 2
    assert system.model.retrieval_calls == (2 if missing_evidence else 1)
    assert result.status == ("insufficient_evidence" if missing_evidence else "completed")


def test_ask_has_grounded_citation(system):
    result = system.invoke(request(intent="ask"))
    assert result.status == "completed"
    assert result.answer and result.citations[0].course_id == "net"


def test_exam_types_are_enforced(system):
    result = system.invoke(request(intent="quiz", exam_profile={"question_types": ["proof"]}))
    assert result.status == "insufficient_evidence"


def test_checkpoint_history_and_pending_writes(system):
    start_quiz(system)
    key = system._key("net", "test")
    history = list(system.graph.checkpointer.list(system._config(key), limit=3))
    assert len(history) == 3
    assert history[0].parent_config
    assert history[0].pending_writes  # interrupt is durable too
    before = list(
        system.graph.checkpointer.list(
            system._config(key),
            before=history[1].config,
            limit=1,
        )
    )
    assert before[0].checkpoint["id"] < history[1].checkpoint["id"]
