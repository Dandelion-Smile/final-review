"""The workspace must preserve version ownership, unknown scores and progress."""

from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_m3_formal_attempt import confirmed_case
from test_m3_formal_result import graded_case
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case

from final_review.api import create_app
from final_review.domain import DomainNotFound
from final_review.formal_attempt import (
    list_formal_attempts,
    save_formal_answer,
    start_formal_attempt,
    submit_formal_attempt,
)
from final_review.quiz_jobs import enqueue_quiz
from final_review.quiz_workspace import list_quiz_jobs, list_quiz_summaries


def test_quiz_summary_and_jobs_are_owned_and_do_not_include_answer_payloads(draft_case):
    system, card = confirmed_case(draft_case)
    _, context, _, _ = draft_case
    job = enqueue_quiz(system.store, context.config, model_id="default")
    foreign = deepcopy(system.store.get("quiz_job", job["job_id"]))
    foreign.update(job_id="foreign-job", user_id="someone-else")
    system.store.put("quiz_job", "foreign-job", foreign)
    with TestClient(create_app(system.settings, system)) as client:
        summaries = client.get("/api/courses/net/quizzes").json()["items"]
        summary = next(item for item in summaries if item["asset_id"] == card["asset_id"])
        assert summary["question_count"] == 1
        assert summary["total_score"] == 10
        assert summary["latest_revision_state"] == "confirmed"
        assert summary["blueprint"] == context.config.model_dump()["blueprint"]
        assert "questions" not in summary
        jobs = client.get("/api/courses/net/quiz-jobs").json()["items"]
        assert [item["job_id"] for item in jobs] == [job["job_id"]]
        assert "config" not in jobs[0]
    for reader in (list_quiz_summaries, list_quiz_jobs):
        with pytest.raises(DomainNotFound):
            reader(system.store, "someone-else", "net")


def test_attempt_summary_keeps_ungraded_score_unknown_and_saved_progress(draft_case):
    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    question_id = started["questions"][0]["question_revision_id"]
    save_formal_answer(
        system.store, "local-user", "net", attempt_id, question_id, "同步初始序列号", 0
    )
    item = list_formal_attempts(system.store, "local-user", "net")["items"][0]
    assert item["title"] == started["quiz"]["title"]
    assert item["answered_count"] == item["question_count"] == 1
    assert item["total_score"] is None
    assert "answers" not in item
    submit_formal_attempt(system.store, "local-user", "net", attempt_id)
    item = list_formal_attempts(system.store, "local-user", "net")["items"][0]
    assert item["status"] == "submitted" and item["grading_status"] == "pending"
    assert item["total_score"] is None


def test_attempt_summary_reads_final_grade_and_preserves_old_title(draft_case):
    system, card, _, _ = graded_case(draft_case)
    asset = system.store.get("learning_asset", card["asset_id"])
    asset["title"] = "后续修改的标题"
    system.store.put("learning_asset", card["asset_id"], asset)
    item = list_formal_attempts(system.store, "local-user", "net")["items"][0]
    assert item["grading_status"] == "completed" and item["total_score"] == 10
    assert item["title"] != asset["title"]


def test_expired_attempt_moves_out_of_in_progress_in_workspace(draft_case, monkeypatch):
    from final_review.formal_attempt import _now

    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt = started["attempt"]
    deadline = _now() - timedelta(seconds=1)
    attempt["deadline_at"] = deadline.isoformat()
    system.store.put("attempt", attempt["attempt_id"], attempt)
    item = list_formal_attempts(system.store, "local-user", "net")["items"][0]
    assert item["status"] == "submitted"
    assert item["grading_status"] == "pending" and item["total_score"] is None
