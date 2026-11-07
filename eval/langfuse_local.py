# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28,<1", "langfuse==5.1.0", "python-dotenv>=1,<2"]
# ///
"""Run the three deployment baseline cases against a local Final Review API."""

import argparse
import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from langfuse import Evaluation, get_client

ROOT = Path(__file__).resolve().parents[1]


def api_json(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
    return response.json()


def prepare_cases(client, run_id, extended=False):
    course = api_json(
        client, "POST", "/api/courses", json={"name": f"Langfuse 本机资料验收 {run_id}"}
    )["course_id"]
    empty_course = api_json(
        client, "POST", "/api/courses", json={"name": f"Langfuse 本机空资料验收 {run_id}"}
    )["course_id"]
    fixtures = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    documents = {}
    for document in fixtures["documents"]:
        name = document["name"]
        if name in {"tcp", "dns"}:
            result = api_json(
                client,
                "POST",
                "/knowledge/ingest",
                json={
                    "course_id": course,
                    "title": f"Langfuse 验收合成材料 {name}",
                    "chapter": name,
                    "source_type": "ai_supplement",
                    "markdown": document["markdown"],
                },
            )
            documents[name] = result["document_id"]

    cases = [
        {
            "input": {
                "case_id": "tcp-sequence",
                "course_id": course,
                "query": "写出 TCP 三次握手的报文顺序",
                "chapter": "tcp",
            },
            "expected_output": {
                "status": "completed",
                "document_id": documents["tcp"],
                "terms": ["SYN", "ACK"],
                "reference_answer": "客户端 SYN → 服务端 SYN+ACK → 客户端 ACK。",
            },
        },
        {
            "input": {
                "case_id": "dns-transport",
                "course_id": course,
                "query": "DNS 永远只使用 UDP，对吗？",
                "chapter": "dns",
            },
            "expected_output": {
                "status": "completed",
                "document_id": documents["dns"],
                "terms": ["UDP", "TCP"],
                "reference_answer": "DNS 既可以使用 UDP，也可以使用 TCP。",
            },
        },
        {
            "input": {
                "case_id": "empty-course",
                "course_id": empty_course,
                "query": "请解释 TCP 三次握手",
                "chapter": "",
            },
            "expected_output": {
                "status": "insufficient_evidence",
                "reference_answer": "说明课程资料不足，且不提供引用。",
            },
        },
    ]
    if extended:
        document = api_json(
            client,
            "POST",
            "/knowledge/ingest",
            json={
                "course_id": course,
                "title": "缓存教学合成材料",
                "chapter": "cache",
                "source_type": "ai_supplement",
                "markdown": "缓存条目的有效期为五分钟。条目过期后，应读取数据源中的新版本。",
            },
        )["document_id"]
        for case_id, query, unanswerable in [
            ("cache-expiry", "根据材料，缓存条目多久过期？", False),
            (
                "cache-missing-port",
                "材料中规定缓存服务监听哪个端口？请给出资料依据，资料没有就明确说明。",
                True,
            ),
            (
                "cache-exam-rules",
                "这份材料规定考试答缓存有效期时如何给分、漏写会扣几分？资料没有规定就明确说明。",
                True,
            ),
        ]:
            expected = {"status": "completed", "document_id": document, "terms": ["五分钟"]}
            if unanswerable:
                expected = {
                    "statuses": ["completed", "insufficient_evidence"],
                    "unanswerable": True,
                }
            cases.append(
                {
                    "input": {
                        "case_id": case_id,
                        "course_id": course,
                        "query": query,
                        "chapter": "cache",
                    },
                    "expected_output": expected,
                }
            )
    return cases


def score_case(output, expected):
    response = output["result"]
    http_ok = output["http_status"] == 200
    statuses = expected.get("statuses", [expected.get("status")])
    status_ok = http_ok and response.get("status") in statuses
    citations = response.get("citations", [])
    scores = {"http_ok": float(http_ok), "expected_status": float(status_ok)}
    if expected.get("unanswerable"):
        evidence_ok = any(
            term in response.get("answer", "")
            for term in ("没有", "未提供", "未规定", "未说明", "未提及", "未包含", "不足", "无法")
        )
        scores["abstention_contract"] = float(status_ok and evidence_ok)
    elif "document_id" in expected:
        evidence_ok = any(item.get("document_id") == expected["document_id"] for item in citations)
        answer = response.get("answer", "").casefold()
        terms = expected["terms"]
        scores["citation_document_hit"] = float(evidence_ok)
        scores["keyword_coverage"] = sum(term.casefold() in answer for term in terms) / len(terms)
    else:
        evidence_ok = not citations
        scores["abstention_contract"] = float(status_ok and evidence_ok)
    scores["required_checks"] = float(status_ok and evidence_ok)
    return scores


def run_local(client, langfuse, report):
    run_id = report["run_id"]
    api_json(client, "GET", "/health")
    api_json(
        client,
        "POST",
        "/api/auth/sign-in",
        json={
            "email": os.environ["FINAL_REVIEW_EVAL_EMAIL"],
            "password": os.environ["FINAL_REVIEW_EVAL_PASSWORD"],
        },
    )
    try:
        api_json(client, "GET", "/api/auth/me")
        models = api_json(client, "GET", "/api/chat/models")
        if not models["items"]:
            raise RuntimeError("本机后端没有可用的聊天模型")
        report["configured_default_model"] = models["note_model"]
        print(
            f"本机登录成功。配置的默认模型为 {models['note_model']}。开始准备测试资料。", flush=True
        )
        data = prepare_cases(client, run_id, extended=report["extended"])
        if report["case_ids"]:
            by_id = {item["input"]["case_id"]: item for item in data}
            data = [by_id[case_id] for case_id in report["case_ids"]]
        rows = report["cases"]

        def task(*, item, **_kwargs):
            case = item["input"]
            payload = {
                "course_id": case["course_id"],
                "session_id": f"{run_id}-{case['case_id']}",
                "message": case["query"],
                "chapter": case["chapter"],
                "intent": "ask",
            }
            print(f"运行 {case['case_id']}", flush=True)
            output = {"http_status": None, "result": {}}
            with langfuse.start_as_current_observation(
                name="final-review-local-http", as_type="span", input=payload
            ) as span:
                start = monotonic()
                try:
                    response = client.post("/agent/invoke", json=payload)
                    output["http_status"] = response.status_code
                    if response.status_code == 200:
                        output["result"] = response.json()
                    else:
                        output["error"] = f"HTTP {response.status_code}"
                except (httpx.RequestError, ValueError) as exc:
                    output["error"] = type(exc).__name__
                output["elapsed_seconds"] = round(monotonic() - start, 3)
                span.update(output=output)
                if "error" in output:
                    span.update(level="ERROR", status_message=output["error"])
                trace_id = span.trace_id
            rows.append(
                {
                    "input": case,
                    "expected": item["expected_output"],
                    "output": output,
                    "trace_id": trace_id,
                }
            )
            return {**output, "case_id": case["case_id"]}

        def evaluate(*, output, expected_output, **_kwargs):
            scores = score_case(output, expected_output)
            row = next(row for row in rows if row["input"]["case_id"] == output["case_id"])
            row["scores"] = scores
            outcome = "通过" if scores["required_checks"] else "未通过"
            print(f"{output['case_id']} {outcome}，耗时 {output['elapsed_seconds']} 秒", flush=True)
            return [Evaluation(name=name, value=value) for name, value in scores.items()]

        result = langfuse.run_experiment(
            name=f"Final Review · Local {run_id}",
            description="本机 Final Review 真实接口调用，合成课程材料与规则评分。",
            data=data,
            task=task,
            evaluators=[evaluate],
            max_concurrency=1,
            metadata={"target": "local", "api": report["app_url"], "run_id": run_id},
        )
        report["experiment"] = result.format()
        report["required_passed"] = sum(
            row.get("scores", {}).get("required_checks", 0) for row in rows
        )
        report["case_count"] = len(data)
        print(report["experiment"], flush=True)
        if len(rows) != len(data) or any("scores" not in row for row in rows):
            raise RuntimeError("实验未为全部用例生成评分，请查看逐题记录")
    finally:
        with suppress(httpx.RequestError):
            client.post("/api/auth/sign-out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="本机 Final Review API 地址")
    parser.add_argument("--output", type=Path, help="JSON 报告路径")
    parser.add_argument("--extended", action="store_true", help="增加三条缓存资料与缺失信息用例")
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        choices=[
            "tcp-sequence",
            "dns-transport",
            "empty-course",
            "cache-expiry",
            "cache-missing-port",
            "cache-exam-rules",
        ],
        help="指定用例及执行顺序，可重复传入",
    )
    args = parser.parse_args()
    if args.case_ids and len(set(args.case_ids)) != len(args.case_ids):
        parser.error("一轮实验中的用例不能重复，请分别运行多轮进行对比")
    if args.case_ids and any(case.startswith("cache-") for case in args.case_ids):
        args.extended = True
    url = urlsplit(args.url)
    if (
        url.scheme not in {"http", "https"}
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.username is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        parser.error("--url 必须是本机回环 API 地址，例如 http://127.0.0.1:8080")
    for name in (
        "LANGFUSE_BASE_URL",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "FINAL_REVIEW_EVAL_EMAIL",
        "FINAL_REVIEW_EVAL_PASSWORD",
    ):
        if not os.getenv(name):
            parser.error(f"请设置 {name}")

    run_id = f"local-{uuid4().hex[:10]}"
    path = args.output or ROOT / "storage" / "langfuse-eval" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id,
        "app_url": args.url,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "scoring": "HTTP、业务状态、引用和拒答规则。关键词覆盖单独记录。",
        "cases": [],
        "extended": args.extended,
        "case_ids": args.case_ids,
    }
    token = os.getenv("API_TOKEN", dotenv_values(ROOT / ".env").get("API_TOKEN") or "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    langfuse = get_client()
    try:
        if not langfuse.auth_check():
            raise RuntimeError("Langfuse 项目鉴权失败，请检查地址及项目密钥")
        with httpx.Client(
            base_url=args.url, headers=headers, timeout=180, trust_env=False
        ) as client:
            run_local(client, langfuse, report)
    except (httpx.HTTPError, RuntimeError) as exc:
        report["run_error"] = str(exc)
        raise SystemExit(report["run_error"]) from exc
    finally:
        langfuse.flush()
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"本机报告 {path.resolve()}", flush=True)


if __name__ == "__main__":
    main()
