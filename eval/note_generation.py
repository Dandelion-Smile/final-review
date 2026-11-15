# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx>=0.28,<1",
#   "langfuse==5.1.0",
#   "psycopg[binary]>=3.2,<4",
#   "python-dotenv>=1,<2",
# ]
# ///
"""Evaluate the running local Final Review note workflow with synthetic course materials."""

import argparse
import json
import os
from contextlib import suppress
from contextvars import ContextVar
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
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MATERIALS = {
    "expiry": ("缓存有效期", "缓存条目有效期为五分钟。过期后应重新从数据源读取。"),
    "invalidation": ("缓存主动失效", "写入成功后应主动失效对应缓存。下次读取时回源。"),
    "excluded": ("未选资料", "合成实验中的专用标签为 NEBULA7391，仅用于对照检查。"),
    "atomicity": ("事务原子性", "同一事务中的操作全部提交或全部回滚。"),
    "index": ("数据库索引", "索引可以减少读取成本，但需要额外存储并增加写入成本。"),
    "balancing": ("负载均衡", "负载均衡器将请求分发到健康的后端实例。"),
}
CASES = {
    "single-source": {
        "selected": ["expiry"],
        "scope": "说明缓存有效期和过期后如何处理。",
        "terms": [("五分钟", "5分钟", "5 分钟"), ("回源", "数据源")],
    },
    "selected-scope": {
        "selected": ["expiry", "invalidation"],
        "unselected": ["excluded"],
        "scope": "分别说明缓存过期与写入后的主动失效。",
        "terms": [("过期", "有效期"), ("失效", "删除缓存")],
        "excluded_term": "NEBULA7391",
    },
    "six-source-limit": {"selected": list(MATERIALS), "scope": "", "queue_status": 422},
    "five-source-batches": {
        "selected": ["expiry", "invalidation", "atomicity", "index", "balancing"],
        "scope": "梳理资料中的关键概念和注意事项。",
        "multi_batch": True,
    },
}
DEFAULT_CASES = ["single-source", "selected-scope", "six-source-limit"]


class CaseError(Exception):
    """An API, workflow, or fixture contract failure."""


_span_names: ContextVar[list[str] | None] = ContextVar("note_eval_spans", default=None)


def remember(name: str) -> None:
    names = _span_names.get()
    if names is not None:
        names.append(name)


def api(client, method, path, *, allowed=(200,), **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code not in allowed:
        raise CaseError(f"{method} {path} HTTP {response.status_code}")
    return response


def observed_request(client, lf, name, method, path, **kwargs):
    remember(name)
    with lf.start_as_current_observation(
        name=name, as_type="span", input={"method": method, "path": path}
    ) as span:
        response = api(client, method, path, **kwargs)
        span.update(output={"http_status": response.status_code})
        return response


def stage_call(client, lf, name, method, path, **kwargs):
    return observed_request(client, lf, name, method, path, **kwargs).json()


def material_text(title, fact, multi_batch):
    if not multi_batch:
        return f"# {title}\n\n{fact}"
    return "\n\n".join(
        f"## {title} 练习 {exercise}\n\n"
        + "\n".join(
            f"例 {exercise}-{line}。{fact} 复习时写出适用条件、操作和结果。"
            for line in range(1, 22)
        )
        for exercise in range(1, 6)
    )


def prepare(client, lf, case, row):
    course_id = stage_call(
        client,
        lf,
        "create-eval-course",
        "POST",
        "/api/courses",
        allowed=(200, 201),
        json={"name": f"笔记评测 {row['case_id']} {row['run_id']}"},
    )["course_id"]
    row["course_id"] = course_id
    row["materials"] = {}
    sources = {}
    for key in case["selected"] + case.get("unselected", []):
        title, fact = MATERIALS[key]
        material = stage_call(
            client,
            lf,
            f"index-material/{key}",
            "POST",
            "/knowledge/ingest",
            json={
                "course_id": course_id,
                "title": f"合成教学资料 · {title}",
                "source_type": "teacher_ppt",
                "markdown": material_text(title, fact, case.get("multi_batch", False)),
            },
        )
        document_id = material["document_id"]
        row["materials"][key] = {"document_id": document_id, "chunks": material["chunks"]}
        if key in case["selected"] and case.get("queue_status", 200) == 200:
            chunks = stage_call(
                client,
                lf,
                f"read-source-chunks/{key}",
                "GET",
                f"/api/courses/{course_id}/documents/{document_id}/chunks",
                params={"include_content": "true"},
            )["items"]
            if not chunks:
                raise CaseError(f"合成资料 {key} 入库后没有可读片段")
            sources[document_id] = {item["chunk_id"]: item["content"] for item in chunks}
    selected = [row["materials"][key]["document_id"] for key in case["selected"]]
    if case.get("multi_batch") and sum(map(len, sources.values())) <= 12:
        raise CaseError("可读片段不足 13 条，未达到当前 worker 的分批阈值")
    return selected, sources


def wait_for_worker(client, lf, row, path, timeout):
    start = monotonic()
    deadline = start + timeout
    timeline = row["worker_timeline"] = []
    previous = None
    polls = 0
    remember("observe-note-worker")
    with lf.start_as_current_observation(
        name="observe-note-worker", as_type="span", input={"job_id": row["job_id"]}
    ) as span:
        try:
            while (remaining := deadline - monotonic()) > 0:
                polls += 1
                history = api(client, "GET", path, timeout=min(20, remaining)).json()
                active = history.get("active_note")
                state = (
                    {key: active.get(key) for key in ("status", "stage", "error", "coverage")}
                    if active
                    else {"status": "inactive"}
                )
                if state != previous:
                    entry = {"observed_after_seconds": round(monotonic() - start, 3), **state}
                    timeline.append(entry)
                    print(f"  {state['status']} {state.get('stage') or ''}", flush=True)
                    previous = state
                if not active or state["status"] not in {"queued", "running"}:
                    row["worker_terminal_observed"] = True
                    row["assistant_message"] = next(
                        (
                            item["content"]
                            for item in reversed(history["items"])
                            if item["role"] == "assistant"
                        ),
                        "",
                    )
                    row["conversation_draft"] = next(
                        (item["draft"] for item in reversed(history["items"]) if item.get("draft")),
                        None,
                    )
                    return
                sleep(min(2, max(0, deadline - monotonic())))
            raise CaseError(f"等待 worker 超过 {timeout:g} 秒，任务可能仍在运行")
        finally:
            span.update(output={"polls": polls, "timeline": timeline})


def worker_snapshot(dsn, row):
    """Read a snapshot of only the job created by this case."""
    try:
        with psycopg.connect(
            dsn, connect_timeout=5, options="-c default_transaction_read_only=on"
        ) as conn:
            record = conn.execute(
                "SELECT data FROM public.note_jobs WHERE record_key = %s AND course_id = %s "
                "AND conversation_id = %s",
                (row["job_id"], row["course_id"], row["conversation_id"]),
            ).fetchone()
    except (psycopg.Error, ValueError) as exc:
        return {"snapshot_error": type(exc).__name__}
    if not record:
        return {"snapshot_error": "job_not_found"}
    job = record[0]
    return {
        **{
            key: job.get(key) for key in ("status", "attempts", "stage", "error", "publish_started")
        },
        "batches": [
            {
                "index": index,
                "source_count": len(batch.get("source_ids", [])),
                "fallback_reason": batch.get("fallback_reason"),
            }
            for index, batch in (job.get("partial_batches") or {}).items()
        ],
    }


def check_draft(row, case, sources, listed):
    revision = row["draft"]["revision"]
    points = revision.get("points") or []
    refs = [ref for point in points for ref in point.get("references", [])]
    expected_ids = set(sources)
    coverage = revision.get("coverage") or {}
    text = revision.get("markdown", "").casefold()
    checks = {
        "saved_draft": bool(
            listed
            and revision.get("state") == "draft"
            and text.strip()
            and points
            and revision.get("note_type") == "key_points"
        ),
        "citations_from_selected_files": bool(refs)
        and all(ref.get("document_id") in expected_ids for ref in refs),
        "quotes_match_source_chunks": bool(refs)
        and all(
            ref.get("quote")
            and ref["quote"] in sources.get(ref.get("document_id"), {}).get(ref.get("chunk_id"), "")
            for ref in refs
        ),
        "selected_files_read": (
            coverage.get("selected_files") == len(expected_ids)
            and {
                item["document_id"] for item in coverage.get("files", []) if item["read_chunks"] > 0
            }
            == expected_ids
        ),
    }
    if case.get("multi_batch"):
        checks["multi_batch_input_read"] = coverage.get("read_chunks", 0) > 12
    if case.get("excluded_term"):
        checks["unselected_marker_absent"] = case["excluded_term"].casefold() not in text
    row["term_hits"] = [
        {"terms": options, "matched": any(term.casefold() in text for term in options)}
        for options in case.get("terms", [])
    ]
    row["fallback_observed"] = "资料原文摘录" in row["session"].get("answer", "")
    checks["no_excerpt_fallback"] = not row["fallback_observed"]
    return checks


def generate(client, lf, case, row, timeout):
    selected, sources = prepare(client, lf, case, row)
    case_key = f"{row['run_id']}-{row['case_id']}"
    conversation_id = row["conversation_id"] = f"chat-{case_key}"
    session_id = row["session_id"] = f"note-{case_key}"
    course_id = row["course_id"]
    payload = {"course_id": course_id, "session_id": session_id}
    started = stage_call(
        client,
        lf,
        "start-note",
        "POST",
        "/agent/invoke",
        params={"conversation_id": conversation_id},
        json={**payload, "intent": "note", "message": "生成笔记"},
    )
    row["start_status"] = started["status"]
    if started["status"] != "needs_input":
        raise CaseError(f"发起笔记后预期 needs_input，实际 {started['status']}")

    expected_http = case.get("queue_status", 200)
    row["queue_attempted"] = True
    response = observed_request(
        client,
        lf,
        "queue-note",
        "POST",
        "/agent/queue-note",
        allowed=(200, 422),
        params={"conversation_id": conversation_id, "event_id": f"submit-{case_key}"},
        json={
            **payload,
            "note_input": {
                "note_type": "key_points",
                "scope": case["scope"],
                "duration_minutes": 10,
                "source_document_ids": selected,
            },
        },
    )
    row["queue_http_status"] = response.status_code
    queued = row["queue_response"] = response.json()
    row["job_id"] = queued.get("job_id")
    if response.status_code == 422:
        row["worker_terminal_observed"] = True
        return {
            "six_source_limit_rejected": expected_http == 422
            and not row["job_id"]
            and queued.get("detail") == "每次生成笔记最多选择 5 份资料"
        }
    if not row["job_id"]:
        raise CaseError("提交笔记未返回任务 ID")

    wait_for_worker(
        client,
        lf,
        row,
        f"/api/courses/{course_id}/conversations/{conversation_id}/messages",
        timeout,
    )
    last = row["worker_timeline"][-1]
    if last["status"] in {"failed", "cancelled"}:
        raise CaseError(last.get("error") or f"worker 状态为 {last['status']}")
    result = row["session"] = stage_call(
        client,
        lf,
        "read-agent-result",
        "GET",
        "/agent/session",
        params=payload,
    )
    draft = result.get("draft")
    if result["status"] != "completed" or not draft:
        raise CaseError(f"未生成草稿，Agent 终态为 {result['status']}")
    asset_id, revision_id = draft["asset_id"], draft["revision_id"]
    row["draft"] = stage_call(
        client,
        lf,
        "read-saved-draft",
        "GET",
        f"/api/assets/{asset_id}/revisions/{revision_id}",
    )
    notes = stage_call(client, lf, "read-notes-list", "GET", f"/api/courses/{course_id}/notes")
    listed = any(
        item["asset_id"] == asset_id and item["latest_revision_id"] == revision_id
        for item in notes["items"]
    )
    checks = check_draft(row, case, sources, listed)
    published = row["conversation_draft"] or {}
    checks["published_to_conversation"] = (
        published.get("asset_id") == asset_id and published.get("revision_id") == revision_id
    )
    if expected_http == 422:
        checks["six_source_limit_rejected"] = False
    return checks


def observation_names(lf, **filters):
    names = set()
    cursor = None
    while True:
        page = lf.api.observations.get_many(fields="basic", limit=100, cursor=cursor, **filters)
        names.update(item.name for item in page.data if item.name)
        cursor = page.meta.cursor
        if not cursor:
            return names


def confirm_export(lf, row):
    """Local success is not enough: the trace must contain the spans this case opened."""
    expected = set(row.get("expected_spans") or [])
    need_worker = bool(row.get("job_id")) and row.get("queue_http_status") == 200
    missing, worker_missing, error = sorted(expected), [], None
    for attempt in range(5):
        lf.flush()
        try:
            found = observation_names(lf, trace_id=row["trace_id"])
            missing = sorted(expected - found)
            worker_missing = []
            if need_worker:
                worker = observation_names(lf, session_id=row.get("session_id"))
                worker_missing = sorted({"note-batch-generate", "note-batch-save"} - worker)
                if not ({"note-batch-verify", "note-batch-excerpt-fallback"} & worker):
                    worker_missing.append("note-batch-verify|note-batch-excerpt-fallback")
            error = None
            if not missing and not worker_missing:
                break
        except Exception as exc:
            error = type(exc).__name__
            missing = sorted(expected)
        if attempt < 4:
            sleep(2)
    row["missing_spans"] = missing + [f"worker:{name}" for name in worker_missing]
    if error:
        row["trace_error"] = error
    row["trace_complete"] = not row["missing_spans"]
    return row["trace_complete"]


def run_case(client, lf, case_id, report, timeout, dsn):
    row = {"case_id": case_id, "run_id": report["run_id"]}
    report["cases"].append(row)
    case = CASES[case_id]
    start = monotonic()
    print(f"运行 {case_id}", flush=True)
    expected: list[str] = []
    token = _span_names.set(expected)
    try:
        with lf.start_as_current_observation(
            name=f"final-review-note/{case_id}",
            as_type="span",
            input={"run_id": report["run_id"], "note_model": report["note_model"], **case},
        ) as trace:
            remember(f"final-review-note/{case_id}")
            row["trace_id"] = trace.trace_id
            try:
                row["checks"] = generate(client, lf, case, row, timeout)
            except (CaseError, httpx.HTTPError, KeyError, ValueError) as exc:
                row["error"] = str(exc) if isinstance(exc, CaseError) else type(exc).__name__
                row["checks"] = {}
            if dsn and row.get("job_id"):
                remember("read-worker-snapshot")
                with lf.start_as_current_observation(
                    name="read-worker-snapshot", as_type="span"
                ) as span:
                    row["worker_snapshot"] = worker_snapshot(dsn, row)
                    span.update(output=row["worker_snapshot"])
            batches = (row.get("worker_snapshot") or {}).get("batches") or []
            if any(batch.get("fallback_reason") for batch in batches):
                row["fallback_observed"] = True
                if "no_excerpt_fallback" in row["checks"]:
                    row["checks"]["no_excerpt_fallback"] = False
            row["checks_passed"] = bool(row["checks"]) and all(row["checks"].values())
            row["elapsed_seconds"] = round(monotonic() - start, 3)
            trace.update(output={key: value for key, value in row.items() if key != "draft"})
            if not row["checks_passed"]:
                trace.update(level="ERROR", status_message=row.get("error", "笔记规则检查未通过"))
    finally:
        row["expected_spans"] = expected
        _span_names.reset(token)
    row["trace_complete"] = confirm_export(lf, row)
    row["passed"] = bool(row.get("checks_passed")) and row["trace_complete"]
    if not row["trace_complete"]:
        missing = "、".join(row["missing_spans"])
        row["error"] = row.get("error") or f"Langfuse 缺少预期步骤：{missing}"
    fallback = bool(row.get("fallback_observed"))
    lf.create_score(
        name="note_required_checks",
        value=float(row["passed"]),
        trace_id=row["trace_id"],
        data_type="NUMERIC",
    )
    lf.create_score(
        name="note_excerpt_fallback",
        value=0.0 if fallback else 1.0,
        trace_id=row["trace_id"],
        data_type="NUMERIC",
        comment="原文摘录回退" if fallback else "没有原文摘录回退",
    )
    lf.flush()
    row["trace_url"] = lf.get_trace_url(trace_id=row["trace_id"])
    print(
        f"  {'通过' if row['passed'] else '未通过'} {row['trace_url'] or row['trace_id']}",
        flush=True,
    )
    return not row.get("queue_attempted") or row.get("worker_terminal_observed", False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--case", action="append", choices=CASES, help="可重复传入，按指定顺序运行")
    parser.add_argument("--worker-timeout", type=int, default=900, metavar="SECONDS")
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

    run_id = f"note-eval-{uuid4().hex[:10]}"
    output = args.output or ROOT / "storage" / "langfuse-eval" / f"{run_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id,
        "app_url": args.url,
        "requested_cases": cases,
        "cases": [],
        "started_at_utc": datetime.now(UTC).isoformat(),
    }
    if int(os.environ.get("LANGFUSE_TIMEOUT", "0") or "0") < 60:
        os.environ["LANGFUSE_TIMEOUT"] = "60"
    os.environ["LANGFUSE_FLUSH_AT"] = "1"
    os.environ["LANGFUSE_FLUSH_INTERVAL"] = "1"
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
                report["note_model"] = (
                    api(client, "GET", "/api/chat/models").json().get("note_model")
                )
                with propagate_attributes(session_id=run_id, tags=["note-evaluation"]):
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
