# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx>=0.28,<1",
#   "langfuse==5.1.0",
#   "psycopg[binary]>=3.2,<4",
#   "python-dotenv>=1,<2",
# ]
# ///
"""Evaluate local formal quiz generation with excerpts from the Web server course.

The excerpts live in eval/fixtures/web_quiz_materials.json. Each item keeps the
original PPT file name from Web服务端技术原理及应用. This script ingests those
excerpts as teacher_ppt markdown; it does not convert the PPT or PDF itself.
"""

import argparse
import json
import os
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from dotenv import dotenv_values
from langfuse import get_client, propagate_attributes

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "web_quiz_materials.json"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MATERIALS = {
    item["key"]: item for item in json.loads(FIXTURE.read_text(encoding="utf-8"))["materials"]
}
CHOICE_TYPES = {"choice", "multiple_choice"}
CASES = {
    "config-incomplete": {
        "selected": ["http"],
        "expect_http": 422,
        "omit": [
            "blueprint",
            "duration_mode",
            "duration_minutes",
            "difficulty",
            "include_imported_questions",
            "allow_ai_supplement",
        ],
    },
    "blueprint-over-limit": {
        "selected": ["http"],
        "expect_http": 422,
        "blueprint": [
            {"question_type": "choice", "question_count": 60},
            {"question_type": "short_answer", "question_count": 41},
        ],
    },
    "duplicate-source-ids": {
        "selected": ["servlet"],
        "expect_http": 422,
        "duplicate_sources": True,
    },
    "knowledge-point-cap": {
        "selected": ["servlet-lifecycle"],
        "expect_http": 422,
        "scope_mode": "knowledge_points",
        "knowledge_points": ["Servlet生命周期"],
        "blueprint": [{"question_type": "choice", "question_count": 7}],
    },
    "single-lecture": {
        "selected": ["http"],
        "blueprint": [
            {"question_type": "choice", "question_count": 2},
            {"question_type": "true_false", "question_count": 1},
        ],
        "duration_minutes": 20,
        "min_cited_files": 1,
        "terms": [("无状态",), ("三次握手", "SYN")],
    },
    "selected-versus-excluded": {
        "selected": ["servlet", "request-response"],
        "unselected": ["upload"],
        "blueprint": [
            {"question_type": "choice", "question_count": 2},
            {"question_type": "short_answer", "question_count": 1},
        ],
        "min_cited_files": 2,
        "excluded_terms": ["日志门面", "Part接口"],
        "terms": [("HttpServlet", "doGet"), ("转发", "重定向")],
    },
    "chapter-scope": {
        "selected": ["filter", "session"],
        "resolved_keys": ["filter"],
        "scope_mode": "chapter",
        "chapter": "第13章",
        "blueprint": [
            {"question_type": "choice", "question_count": 2},
            {"question_type": "true_false", "question_count": 1},
        ],
        "min_cited_files": 1,
        "terms": [("doFilter", "过滤器链")],
    },
    "select-all-ready": {
        "selected": ["http", "servlet", "session"],
        "select_all": True,
        "resolved_keys": ["http", "servlet", "session"],
        "blueprint": [
            {"question_type": "choice", "question_count": 2},
            {"question_type": "true_false", "question_count": 1},
        ],
        "min_cited_files": 3,
    },
    "four-lecture-paper": {
        "selected": ["html-css", "http", "servlet-lifecycle", "jsp"],
        "blueprint": [
            {"question_type": "choice", "question_count": 4},
            {"question_type": "true_false", "question_count": 2},
            {"question_type": "fill_blank", "question_count": 2},
            {"question_type": "short_answer", "question_count": 2},
        ],
        "duration_minutes": 45,
        "emphasis": ["TCP三次握手", "Servlet生命周期"],
        "min_cited_files": 4,
    },
    "eight-lecture-exam": {
        "selected": [
            "html-css",
            "js-jquery",
            "http",
            "jdbc",
            "jsp-objects",
            "ajax",
            "filter",
            "session",
        ],
        "blueprint": [
            {"question_type": "choice", "question_count": 5},
            {"question_type": "multiple_choice", "question_count": 2},
            {"question_type": "true_false", "question_count": 2},
            {"question_type": "fill_blank", "question_count": 2},
            {"question_type": "short_answer", "question_count": 2},
        ],
        "duration_minutes": 90,
        "total_score": 100,
        "difficulty": "mixed",
        "min_cited_files": 4,
    },
}
DEFAULT_CASES = list(CASES)


class CaseError(Exception):
    """An API, workflow, or fixture contract failure."""


def api(client, method, path, *, allowed=(200,), **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code not in allowed:
        raise CaseError(f"{method} {path} HTTP {response.status_code}: {response.text[:500]}")
    return response


def observed_request(client, lf, name, method, path, **kwargs):
    with lf.start_as_current_observation(
        name=name, as_type="span", input={"method": method, "path": path}
    ) as span:
        response = api(client, method, path, **kwargs)
        span.update(output={"http_status": response.status_code})
        return response


def stage_call(client, lf, name, method, path, **kwargs):
    return observed_request(client, lf, name, method, path, **kwargs).json()


def prepare(client, lf, case, row):
    course_id = stage_call(
        client,
        lf,
        "create-eval-course",
        "POST",
        "/api/courses",
        allowed=(200, 201),
        json={"name": f"试卷评测 {row['case_id']} {row['run_id']}"[:100]},
    )["course_id"]
    row["course_id"] = course_id
    row["conversation_id"] = stage_call(
        client,
        lf,
        "create-conversation",
        "POST",
        f"/api/courses/{course_id}/conversations",
        json={"title": f"试卷评测 {row['case_id']}"[:100]},
    )["conversation_id"]
    row["materials"] = {}
    sources = {}
    for key in case["selected"] + case.get("unselected", []):
        item = MATERIALS[key]
        material = stage_call(
            client,
            lf,
            f"index-material/{key}",
            "POST",
            "/knowledge/ingest",
            json={
                "course_id": course_id,
                "title": item["title"],
                "chapter": item["chapter"],
                "source_type": "teacher_ppt",
                "markdown": f"# {item['source_file']}\n\n{item['markdown']}",
            },
        )
        document_id = material["document_id"]
        row["materials"][key] = {
            "document_id": document_id,
            "chunks": material["chunks"],
            "source_file": item["source_file"],
        }
        chunks = stage_call(
            client,
            lf,
            f"read-source-chunks/{key}",
            "GET",
            f"/api/courses/{course_id}/documents/{document_id}/chunks",
            params={"include_content": "true"},
        )["items"]
        if not chunks:
            raise CaseError(f"资料 {item['source_file']} 入库后没有可读片段")
        sources[document_id] = {chunk["chunk_id"]: chunk["content"] for chunk in chunks}
    return sources


def quiz_input(case, row):
    selected = [row["materials"][key]["document_id"] for key in case["selected"]]
    if case.get("duplicate_sources"):
        selected = [selected[0], selected[0]]
    elif case.get("select_all"):
        selected = []
    body = {
        "scope_mode": case.get("scope_mode", "course"),
        "duration_mode": "timed",
        "duration_minutes": case.get("duration_minutes", 30),
        "difficulty": case.get("difficulty", "standard"),
        "include_imported_questions": False,
        "allow_ai_supplement": False,
        "source_document_ids": selected,
        "output_kind": "quiz_draft",
    }
    if "blueprint" in case:
        body["blueprint"] = case["blueprint"]
    if case.get("chapter"):
        body["chapter"] = case["chapter"]
    if case.get("knowledge_points"):
        body["knowledge_points"] = case["knowledge_points"]
    if case.get("emphasis"):
        body["emphasis"] = case["emphasis"]
    if case.get("total_score"):
        body["total_score"] = case["total_score"]
    for name in case.get("omit", []):
        body.pop(name, None)
    return body


def wait_for_worker(client, lf, row, timeout):
    start = monotonic()
    deadline = start + timeout
    path = f"/api/courses/{row['course_id']}/quiz-jobs/{row['job_id']}"
    timeline = row["worker_timeline"] = []
    previous = None
    polls = 0
    with lf.start_as_current_observation(
        name="observe-quiz-worker", as_type="span", input={"job_id": row["job_id"]}
    ) as span:
        try:
            while (remaining := deadline - monotonic()) > 0:
                polls += 1
                job = api(client, "GET", path, timeout=min(20, remaining)).json()
                state = {
                    "status": job.get("status"),
                    "stage": job.get("stage"),
                    "error": job.get("error"),
                    "progress": job.get("progress"),
                }
                if state != previous:
                    entry = {"observed_after_seconds": round(monotonic() - start, 3), **state}
                    timeline.append(entry)
                    progress = state.get("progress") or {}
                    done = progress.get("completed_questions")
                    total = progress.get("total_questions")
                    fraction = f" {done}/{total}" if done is not None and total is not None else ""
                    print(f"  {state['status']} {state.get('stage') or ''}{fraction}", flush=True)
                    previous = state
                if state["status"] not in {"queued", "running"}:
                    row["worker_terminal_observed"] = True
                    row["job"] = {
                        key: job.get(key)
                        for key in ("status", "stage", "error", "issues", "result", "progress")
                    }
                    return
                sleep(min(2, max(0, deadline - monotonic())))
            raise CaseError(f"等待试卷 worker 超过 {timeout:g} 秒，任务可能仍在运行")
        finally:
            span.update(output={"polls": polls, "timeline": timeline})


def worker_snapshot(dsn, row):
    """Read a snapshot of only the job created by this case."""
    try:
        with psycopg.connect(
            dsn, connect_timeout=5, options="-c default_transaction_read_only=on"
        ) as conn:
            record = conn.execute(
                "SELECT data FROM public.quiz_jobs WHERE record_key = %s AND course_id = %s "
                "AND conversation_id = %s",
                (row["job_id"], row["course_id"], row["conversation_id"]),
            ).fetchone()
    except (psycopg.Error, ValueError) as exc:
        return {"snapshot_error": type(exc).__name__}
    if not record:
        return {"snapshot_error": "job_not_found"}
    job = record[0]
    progress = job.get("progress") or {}
    return {
        "status": job.get("status"),
        "stage": job.get("stage"),
        "error": job.get("error"),
        "attempts": job.get("attempts"),
        "completed_questions": progress.get("completed_questions"),
        "total_questions": progress.get("total_questions"),
        "completed_batches": progress.get("completed_batches"),
        "remaining_batches": progress.get("remaining_batches"),
        "issue_count": len(job.get("issues") or []),
    }


def answer_present(question):
    kind = question.get("question_type")
    if kind in CHOICE_TYPES and not question.get("options"):
        return False
    if kind == "choice" and question.get("correct_option") is None:
        return False
    if kind == "multiple_choice" and not question.get("correct_options"):
        return False
    if kind == "true_false" and question.get("boolean_answer") is None:
        return False
    if kind == "fill_blank" and not (
        question.get("blank_answers") or question.get("accepted_answers")
    ):
        return False
    return bool(str(question.get("reference_answer") or "").strip())


def check_paper(row, case, sources, paper, listed):
    revision = paper["revision"]
    questions = revision.get("questions") or []
    expected = {item["question_type"]: item["question_count"] for item in case.get("blueprint", [])}
    refs = [ref for question in questions for ref in question.get("references") or []]
    cited = {ref.get("document_id") for ref in refs}
    key_by_id = {item["document_id"]: key for key, item in row["materials"].items()}
    resolved = [
        key_by_id.get(document_id, document_id)
        for document_id in (revision.get("config") or {}).get("source_document_ids") or []
    ]
    text = "\n".join(
        "\n".join(
            str(question.get(name) or "")
            for name in ("stem", "reference_answer", "explanation", "core_point")
        )
        for question in questions
    )
    row["resolved_source_keys"] = resolved
    row["cited_source_files"] = sorted(
        row["materials"][key_by_id[document_id]]["source_file"]
        for document_id in cited
        if document_id in key_by_id
    )
    row["term_hits"] = [
        {"terms": options, "matched": any(term in text for term in options)}
        for options in case.get("terms", [])
    ]
    row["excluded_term_hits"] = [term for term in case.get("excluded_terms", []) if term in text]
    allowed_keys = case.get("resolved_keys") or case["selected"]
    selected_ids = {row["materials"][key]["document_id"] for key in allowed_keys}
    checks = {
        "saved_quiz": bool(
            listed
            and revision.get("state") in {"draft", "confirmed"}
            and questions
            and str(revision.get("title") or "").strip()
        ),
        "question_count": len(questions) == sum(expected.values()),
        "blueprint_types": Counter(question.get("question_type") for question in questions)
        == Counter(expected),
        "answers_present": bool(questions)
        and all(answer_present(question) for question in questions),
        "no_ai_supplement": all(
            question.get("provenance") != "ai_supplement" for question in questions
        ),
        "citations_from_selected_files": bool(refs)
        and cited <= selected_ids
        and all(question.get("references") for question in questions),
        "quotes_match_source_chunks": bool(refs)
        and all(
            ref.get("quote")
            and ref["quote"] in sources.get(ref.get("document_id"), {}).get(ref.get("chunk_id"), "")
            for ref in refs
        ),
        "listed_in_course": listed,
    }
    if case.get("min_cited_files"):
        checks["multiple_sources_used"] = len(cited) >= case["min_cited_files"]
    if case.get("resolved_keys"):
        checks["resolved_sources"] = sorted(resolved) == sorted(case["resolved_keys"])
    return checks


def compact_question(question):
    return {
        "order": question.get("order"),
        "question_type": question.get("question_type"),
        "knowledge_point": question.get("knowledge_point"),
        "stem": question.get("stem"),
        "provenance": question.get("provenance"),
        "source_files": [ref.get("file_name") for ref in question.get("references") or []],
        "reference_answer": question.get("reference_answer"),
    }


def generate(client, lf, case, row, sources, timeout):
    body = quiz_input(case, row)
    expected_http = case.get("expect_http", 202)
    response = observed_request(
        client,
        lf,
        "queue-quiz",
        "POST",
        f"/api/courses/{row['course_id']}/quiz-jobs",
        allowed=(202, 422),
        headers={"Idempotency-Key": f"quiz-{row['run_id']}-{row['case_id']}"},
        json={"quiz_input": body, "conversation_id": row["conversation_id"]},
    )
    row["queue_http_status"] = response.status_code
    queued = row["queue_response"] = response.json()
    if response.status_code == 422:
        row["worker_terminal_observed"] = True
        detail = queued.get("detail", queued)
        text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
        return {"configuration_rejected": expected_http == 422 and bool(text.strip())}
    row["job_id"] = queued.get("job_id")
    if expected_http != 202 or not row["job_id"]:
        raise CaseError(f"提交试卷返回 HTTP {response.status_code}，未得到可等待的任务")
    wait_for_worker(client, lf, row, timeout)
    job = row["job"]
    if job["status"] != "succeeded":
        raise CaseError(job.get("error") or f"试卷任务终态为 {job['status']}")
    result = job.get("result") or {}
    asset_id, revision_id = result.get("asset_id"), result.get("revision_id")
    if not asset_id or not revision_id:
        raise CaseError("成功任务没有返回试卷资产")
    paper = stage_call(
        client,
        lf,
        "read-saved-quiz",
        "GET",
        f"/api/courses/{row['course_id']}/quizzes/{asset_id}/revisions/{revision_id}",
    )
    listed = stage_call(
        client, lf, "read-quiz-list", "GET", f"/api/courses/{row['course_id']}/quizzes"
    )
    visible = any(item.get("asset_id") == asset_id for item in listed.get("items") or [])
    row["questions"] = [
        compact_question(question) for question in paper["revision"].get("questions") or []
    ]
    return check_paper(row, case, sources, paper, visible)


def run_case(client, lf, case_id, report, timeout, dsn):
    row = {"case_id": case_id, "run_id": report["run_id"]}
    report["cases"].append(row)
    case = CASES[case_id]
    start = monotonic()
    print(f"运行 {case_id}", flush=True)
    with lf.start_as_current_observation(
        name=f"final-review-quiz/{case_id}",
        as_type="span",
        input={"run_id": report["run_id"], "quiz_model": report.get("quiz_model"), **case},
    ) as trace:
        row["trace_id"] = trace.trace_id
        try:
            sources = prepare(client, lf, case, row)
            row["checks"] = generate(client, lf, case, row, sources, timeout)
        except (CaseError, httpx.HTTPError, KeyError, ValueError) as exc:
            row["error"] = str(exc) if isinstance(exc, CaseError) else type(exc).__name__
            row["checks"] = {}
        if dsn and row.get("job_id"):
            with lf.start_as_current_observation(
                name="read-worker-snapshot", as_type="span"
            ) as span:
                row["worker_snapshot"] = worker_snapshot(dsn, row)
                span.update(output=row["worker_snapshot"])
        row["passed"] = bool(row["checks"]) and all(row["checks"].values())
        row["elapsed_seconds"] = round(monotonic() - start, 3)
        trace.update(
            output={
                key: value for key, value in row.items() if key not in {"paper", "queue_response"}
            }
        )
        if not row["passed"]:
            trace.update(level="ERROR", status_message=row.get("error", "试卷规则检查未通过"))
        trace.score_trace(
            name="quiz_required_checks", value=float(row["passed"]), data_type="NUMERIC"
        )
    row["trace_url"] = lf.get_trace_url(trace_id=row["trace_id"])
    print(
        f"  {'通过' if row['passed'] else '未通过'} {row['trace_url'] or row['trace_id']}",
        flush=True,
    )
    lf.flush()
    return not row.get("job_id") or row.get("worker_terminal_observed", False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--case", action="append", choices=CASES, help="可重复传入，按指定顺序运行")
    parser.add_argument("--worker-timeout", type=int, default=1200, metavar="SECONDS")
    parser.add_argument(
        "--worker-snapshot", action="store_true", help="只读查询本机 PostgreSQL 任务"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if (
        url.scheme not in {"http", "https"}
        or url.hostname not in LOOPBACK
        or url.username is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        parser.error("--url 必须是本机回环 API 地址")
    cases = args.case or DEFAULT_CASES
    if len(cases) != len(set(cases)) or args.worker_timeout <= 0:
        parser.error("用例不得重复，worker 等待时间必须大于 0")
    for name in (
        "LANGFUSE_BASE_URL",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "FINAL_REVIEW_EVAL_EMAIL",
        "FINAL_REVIEW_EVAL_PASSWORD",
    ):
        if not os.getenv(name):
            parser.error(f"请设置 {name}")
    local_env = dotenv_values(ROOT / ".env")
    dsn = None
    if args.worker_snapshot:
        dsn = os.getenv("DATABASE_URL") or local_env.get("DATABASE_URL")
        try:
            db = psycopg.conninfo.conninfo_to_dict(dsn) if dsn else {}
        except psycopg.Error:
            parser.error("DATABASE_URL 格式有误")
        if db.get("host") not in LOOPBACK or db.get("hostaddr", "127.0.0.1") not in LOOPBACK:
            parser.error("--worker-snapshot 需要指向本机 PostgreSQL 的 DATABASE_URL")

    run_id = f"quiz-eval-{uuid4().hex[:10]}"
    output = args.output or ROOT / "storage" / "langfuse-eval" / f"{run_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id,
        "app_url": args.url,
        "source_fixture": str(FIXTURE),
        "requested_cases": cases,
        "cases": [],
        "started_at_utc": datetime.now(UTC).isoformat(),
    }
    os.environ.setdefault("LANGFUSE_TIMEOUT", "60")
    os.environ.setdefault("LANGFUSE_FLUSH_AT", "1")
    lf = get_client()
    token = os.getenv("API_TOKEN") or local_env.get("API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        if not lf.auth_check():
            raise CaseError("Langfuse 项目鉴权失败")
        with httpx.Client(
            base_url=args.url, headers=headers, timeout=180, trust_env=False
        ) as client:
            api(client, "GET", "/health")
            api(
                client,
                "POST",
                "/api/auth/sign-in",
                json={
                    "email": os.environ["FINAL_REVIEW_EVAL_EMAIL"],
                    "password": os.environ["FINAL_REVIEW_EVAL_PASSWORD"],
                },
            )
            try:
                models = api(client, "GET", "/api/chat/models").json()
                report["quiz_model"] = models.get("quiz_model") or models.get("note_model")
                with propagate_attributes(session_id=run_id, tags=["quiz-evaluation"]):
                    for case_id in cases:
                        if not run_case(client, lf, case_id, report, args.worker_timeout, dsn):
                            report["run_error"] = "未确认上一任务结束，停止提交后续用例"
                            break
            finally:
                with suppress(httpx.HTTPError):
                    client.post("/api/auth/sign-out", timeout=10)
    except (CaseError, httpx.HTTPError, ValueError) as exc:
        report["run_error"] = str(exc) if isinstance(exc, CaseError) else type(exc).__name__
        print(report["run_error"], flush=True)
    finally:
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"本机报告 {output.resolve()}", flush=True)
        lf.flush()
    return int(
        bool(report.get("run_error"))
        or len(report["cases"]) != len(cases)
        or any(not row["passed"] for row in report["cases"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
