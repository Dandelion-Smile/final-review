"""Version-bound Attempt survives a later quiz confirmation in real PostgreSQL."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import ScriptedModel
from test_m3_quiz_edit import request_for
from test_m3_quiz_edit_postgres import generated
from test_m3_quiz_postgres import USER, database_url, prepared  # noqa: F401

from final_review.account_purge import purge_expired_accounts
from final_review.config import Settings
from final_review.formal_attempt import (
    read_formal_attempt,
    save_formal_answer,
    start_formal_attempt,
    submit_formal_attempt,
)
from final_review.formal_grading import grade_formal_attempt
from final_review.formal_result import ask_quiz_followup, read_formal_result
from final_review.quiz_edit import save_quiz_revision
from final_review.quiz_generation import read_quiz
from final_review.quiz_review import (
    confirm_quiz_revision,
    quiz_confirm_preview,
    review_quiz_revision,
)
from final_review.storage import StorageError, stable_key

pytestmark = pytest.mark.integration


def test_deadline_sweep_persists_saved_answers_without_open_client(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        course = "course-1"
        owner = str(USER)
        asset = card["asset_id"]
        started = start_formal_attempt(store, owner, course, asset, "exam", 1)
        attempt_id = started["attempt"]["attempt_id"]
        question_id = started["questions"][0]["question_revision_id"]
        attempt = save_formal_answer(
            store, owner, course, attempt_id, question_id, "同步序列号", 0
        )["attempt"]
        started_at = datetime.now(UTC) - timedelta(minutes=2)
        attempt.update(
            started_at=started_at.isoformat(),
            deadline_at=(started_at + timedelta(minutes=1)).isoformat(),
        )
        store.put("attempt", attempt_id, attempt)
        store.expire_quiz_attempts()
        submitted = store.get("attempt", attempt_id)
        assert submitted["status"] == "submitted"
        assert submitted["submission_reason"] == "timeout"
        assert submitted["elapsed_seconds"] == 60
        assert submitted["answers"] == attempt["answers"]
        store.expire_quiz_attempts()
        assert store.get("attempt", attempt_id) == submitted
    finally:
        store.close()


def test_formal_attempt_can_finish_after_parent_is_superseded(database_url, tmp_path):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        asset_id, first_id = card["asset_id"], card["revision_id"]
        attempt = start_formal_attempt(store, str(USER), "course-1", asset_id)
        attempt_id = attempt["attempt"]["attempt_id"]
        question_id = attempt["questions"][0]["question_revision_id"]

        first = read_quiz(store, str(USER), "course-1", asset_id, first_id)
        edited = save_quiz_revision(
            store,
            str(USER),
            "course-1",
            asset_id,
            request_for(first, stem="新版 TCP 题目"),
        )
        second_id = edited["revision"]["quiz_revision_id"]
        review_quiz_revision(
            store, str(USER), "course-1", asset_id, second_id, ScriptedModel(), "default"
        )
        token = quiz_confirm_preview(store, str(USER), "course-1", asset_id, second_id)
        confirm_quiz_revision(
            store, str(USER), "course-1", asset_id, second_id, token["confirmation_id"]
        )

        saved = save_formal_answer(
            store, str(USER), "course-1", attempt_id, question_id, "同步序列号", 0
        )
        assert saved["attempt"]["quiz_revision_id"] == first_id
        assert len(store.scan("attempt_answer", {"course_id": "course-1"})) == 1
        assert (
            submit_formal_attempt(store, str(USER), "course-1", attempt_id)["attempt"]["status"]
            == "submitted"
        )
        graded = grade_formal_attempt(
            store, str(USER), "course-1", attempt_id, ScriptedModel(), "default", "fixture-v1"
        )
        assert graded["attempt"]["grading_snapshot"]["total_score"] == 10
        grade_row = store.scan("question_grade", {"course_id": "course-1"})[0]
        grade_key = "question-grade-" + stable_key(attempt_id, question_id)[:32]
        with pytest.raises(StorageError):
            store.put("question_grade", grade_key, {**grade_row, "score": 0})
        assert read_formal_result(store, str(USER), "course-1", attempt_id)["items"][0][
            "references"
        ]
        followup = ask_quiz_followup(
            store,
            str(USER),
            "course-1",
            attempt_id,
            question_id,
            "为什么？",
            ScriptedModel(),
            "default",
            "fixture-v1",
        )
        assert followup["citations"]
        assert (
            read_formal_attempt(store, str(USER), "course-1", attempt_id)["questions"][0]["stem"]
            != "新版 TCP 题目"
        )
        assert (
            store.connection.execute(
                "SELECT kind FROM user_notifications WHERE event_key=%s", (f"grade:{attempt_id}",)
            ).fetchone()["kind"]
            == "grading_ready"
        )
        uploads, exports = tmp_path / "uploads", tmp_path / "exports"
        directory = uploads / str(USER) / "course-1"
        directory.mkdir(parents=True)
        exports.mkdir()
        (directory / "personal.txt").write_text("account file")
        export_id = "export-" + "1" * 32
        export_file = exports / f"{export_id}-1.md"
        export_file.write_text("account export")
        unrelated_file = exports / "unrelated.txt"
        unrelated_file.write_text("preserve")
        store.connection.execute(
            """INSERT INTO export_jobs(record_key,user_id,course_id,data)
               VALUES(%s,%s,'course-1','{}')""",
            (export_id, USER),
        )
        settings = Settings(_env_file=None, uploads_dir=str(uploads), exports_dir=str(exports))
        assert purge_expired_accounts(store, settings) == 0
        store.connection.execute(
            "UPDATE app_users SET deletion_due_at=now()-interval '1 second' WHERE id=%s",
            (USER,),
        )
        assert purge_expired_accounts(store, settings) == 1
        assert not directory.exists() and not export_file.exists() and unrelated_file.exists()
        for table in ("app_users", "asset_revisions", "question_grades", "user_notifications"):
            assert (
                store.connection.execute(f"SELECT count(*) AS total FROM {table}").fetchone()[
                    "total"
                ]
                == 0
            )
    finally:
        store.close()
