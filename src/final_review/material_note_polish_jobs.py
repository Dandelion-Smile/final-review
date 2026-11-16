"""Durable material-note polish jobs: outline → batched drafting → checks → publish.

Runs on its own queue so extraction and indexing never wait on the text model.
Batch results are checkpointed; a retry only re-runs unfinished or failed
batches. A late result never overwrites user edits: if the user already saved
their own revision, the polished note is published as a candidate instead.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from threading import Event, Thread

from .llm import ModelError, ModelOutputLimitError, build_polish_review_model
from .material_note_polish import (
    BATCH_TASK,
    DOCUMENT_REVIEW_TASK,
    OUTLINE_TASK,
    REVIEW_TASK,
    PolishBatch,
    PolishBatchRepair,
    PolishBatchReview,
    PolishDocumentReview,
    PolishOutline,
    assemble_body,
    batch_input,
    build_batches,
    check_batch,
    consolidate_outline,
    final_coverage,
    outline_input,
    polish_model_config,
    repair_batch,
    repair_outline,
    review_input,
    validate_outline,
)
from .material_text_source import STRATEGY, batch_cache_key, units_input_hash
from .observe import flush, job_trace
from .pipeline_runtime import run_queue
from .postgres import PostgresStore

logger = logging.getLogger(__name__)


class PolishLeaseLost(RuntimeError):
    pass


def process_polish_job(store, job: dict, settings) -> None:
    guard = (
        store.account_file_access(job["user_id"])
        if isinstance(store, PostgresStore)
        else nullcontext(True)
    )
    stop, lost = Event(), Event()

    def heartbeat():
        lease_store = PostgresStore(store.database_url)
        lease_store.bind_user(job["user_id"])
        try:
            while not stop.wait(30):
                if not lease_store.renew_polish_job(
                    job["job_id"], job["attempts"], job.get("lease_token")
                ):
                    lost.set()
                    return
        except Exception:
            lost.set()
            logger.exception("润色任务续约失败: %s", job["job_id"])
        finally:
            lease_store.close()

    thread = Thread(target=heartbeat, daemon=True, name="material-polish-heartbeat")
    if isinstance(store, PostgresStore):
        thread.start()
    try:
        with guard as allowed:
            if allowed:
                with job_trace("material-note-polish", job) as metrics:
                    _process_polish_job(store, job, settings, lost, metrics)
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=5)


def _process_polish_job(store, job: dict, settings, lost, metrics) -> None:
    token = store.bind_user(job["user_id"]) if hasattr(store, "bind_user") else None
    metrics.update(
        purpose=job.get("purpose"),
        generation=job.get("generation"),
        parse_mode=STRATEGY,
        visual_requests=0,
        ocr_calls=0,
    )
    started = time.monotonic()

    def progress(**kwargs):
        if lost is not None and lost.is_set():
            raise PolishLeaseLost()
        valid = store.update_polish_job(
            job["job_id"], job["attempts"], lease_token=job.get("lease_token"), **kwargs
        )
        if isinstance(store, PostgresStore) and not valid:
            raise PolishLeaseLost()

    def fail(code, message, retry):
        metrics["status"] = "failed"
        metrics["processing_seconds"] = round(time.monotonic() - started, 6)
        store.fail_polish_job(
            job,
            code=code,
            message=message,
            retry=retry and job.get("phase_attempts", 1) < job.get("max_attempts", 3),
        )

    try:
        context = store.polish_job_context(job)
        if not context:
            raise PolishLeaseLost()
        document, units = context["document"], context["units"]
        if not units:
            metrics["status"] = "not_required"
            metrics["processing_seconds"] = round(time.monotonic() - started, 6)
            if not store.finish_polish_not_required(job, dict(metrics)):
                raise PolishLeaseLost()
            return
        frozen = job.get("model_config") or {}
        if frozen.get("contract_version"):
            fields = {
                "model_id": "material_note_polish_model_id",
                "model": "material_note_polish_model",
                "timeout": "material_note_polish_timeout",
                "max_output_tokens": "material_note_polish_max_output_tokens",
                "batch_blocks": "material_note_polish_batch_blocks",
                "prompt_version": "material_note_polish_prompt_version",
            }
            settings = settings.model_copy(
                update={target: frozen[key] for key, target in fields.items() if key in frozen}
            )
            current = polish_model_config(settings)
            if any(
                current.get(key) != frozen.get(key)
                for key in ("resolved_model", "base_url", "contract_version", "prompt_digest")
            ):
                raise ValueError("润色规则或模型配置已更新，请基于原文重新整理")
        model = build_polish_review_model(settings)
        model_config = polish_model_config(settings)
        document_title = outline_title(document)

        # Stage 1: plan from complete source text in bounded windows.
        progress(stage="outline")
        plan_hash = units_input_hash(
            units, model_config, settings.material_note_polish_prompt_version
        )
        checkpoint = job.get("metrics", {}).get("outline_checkpoint", {})
        if checkpoint.get("input_hash") == plan_hash and not validate_outline(
            checkpoint.get("outline", {}), units
        ):
            outline = checkpoint["outline"]
        else:
            outline = _plan_outline(model, document_title, units, metrics)
            progress(metrics={"outline_checkpoint": dict(input_hash=plan_hash, outline=outline)})

        # Stage 2: verified batches are committed immediately, including before
        # another batch fails. Only the coordinator touches the database connection.
        previous = {row["cache_key"]: row for row in store.load_polish_batches(job)}
        key_args = dict(
            user_id=job["user_id"],
            document_id=document["document_id"],
            source_hash=document["content_sha256"],
            model_config=model_config,
            prompt_version=settings.material_note_polish_prompt_version,
        )
        batches = build_batches(
            outline["sections"],
            units,
            max_blocks=settings.material_note_polish_batch_blocks,
            **key_args,
        )
        batches = _split_failed_batches(batches, previous, **key_args)
        rows = store.prepare_polish_batches(job, batches)
        if rows is None:
            raise PolishLeaseLost()
        concurrency = max(1, min(settings.material_note_polish_concurrency, len(batches)))
        batches_results = [None] * len(batches)
        done_count = sum(
            row["status"] == "done"
            and bool(row["result"])
            and not check_batch(row["result"], batch)
            for row, batch in zip(rows, batches)
        )
        failures = []

        def run_one(position, batch, row):
            if row["status"] == "done" and row["result"] and not check_batch(row["result"], batch):
                return {"result": row["result"], "review": row["review"], "cached": True}
            # Local counters avoid concurrent read/modify/write of shared metrics.
            local_metrics = {}
            result, review = _run_batch(
                model,
                document_title,
                batch,
                "",
                "",
                local_metrics,
                review_mode="always",
            )
            return dict(result=result, review=review, cached=False, metrics=local_metrics)

        progress(stage="batches", batches_done=done_count, batches_total=len(batches))
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="polish-batch") as pool:
            futures = {
                pool.submit(run_one, pos, batch, row): pos
                for pos, (row, batch) in enumerate(zip(rows, batches))
            }
            for future in as_completed(futures):
                position = futures[future]
                progress(stage="batches")
                error = None
                entry = None
                try:
                    entry = future.result()
                    metrics["model_requests"] = metrics.get("model_requests", 0) + entry.get(
                        "metrics", {}
                    ).get("model_requests", 0)
                    if entry["result"] is None:
                        error = "faithfulness_failed"
                except ModelOutputLimitError:
                    error = "output_limit"
                except Exception as exc:
                    error = "model_unavailable"
                    logger.warning("润色批次 %s 失败: %s", position, type(exc).__name__)
                if error:
                    if not store.checkpoint_polish_batch(
                        job,
                        rows[position]["batch_index"],
                        error=error,
                        review=entry.get("review") if entry else None,
                    ):
                        raise PolishLeaseLost()
                    failures.append(error)
                    continue
                if not entry.get("cached"):
                    if not store.checkpoint_polish_batch(
                        job,
                        rows[position]["batch_index"],
                        result=entry["result"],
                        review=entry["review"],
                    ):
                        raise PolishLeaseLost()
                    done_count += 1
                batches_results[position] = {**batches[position], **entry}
                progress(stage="batches", batches_done=done_count, batches_total=len(batches))
        if failures:
            fail(failures[0], "部分章节整理未完成，已保存通过校验的批次，可重试继续", True)
            return

        # Editorial pass sees complete draft groups, the global plan and neighbours.
        # Repairs are bounded and source-reviewed again before replacing checkpoints.
        progress(stage="editorial")
        issues = _review_document(model, document_title, outline, batches_results, metrics)
        for position, issue in issues.items():
            if not 0 <= position < len(batches):
                raise ValueError("全文复核引用了不存在的批次")
            batch = {**batches[position], "editorial_issues": issue}
            result, review = _run_batch(
                model,
                document_title,
                batch,
                "",
                "",
                metrics,
                review_mode="always",
            )
            if result is None:
                store.checkpoint_polish_batch(job, position, error="editorial_failed")
                fail("editorial_failed", "章节未通过全文整理复核，可重试继续", True)
                return
            if not store.checkpoint_polish_batch(job, position, result=result, review=review):
                raise PolishLeaseLost()
            batches_results[position] = {**batch, "result": result, "review": review}
        if issues and _review_document(
            model, document_title, outline, batches_results, metrics, blocking_only=True
        ):
            fail("editorial_failed", "全文仍存在重复或衔接问题，可重新整理", False)
            return

        # Stage 3: whole-document coverage verification, then atomic publish.
        progress(stage="verify")
        errors = final_coverage(
            batches_results, units, outline_excluded=outline.get("excluded", [])
        )
        if errors:
            fail("coverage_failed", "整份笔记未通过覆盖检查：" + "；".join(errors[:5]), True)
            return
        body = assemble_body(
            document["document_id"],
            batches_results,
            units,
            outline.get("document_title") or document_title,
            outline_excluded=outline.get("excluded", []),
        )
        metrics["status"] = "ready"
        metrics["processing_seconds"] = round(time.monotonic() - started, 6)
        published = store.publish_polished_note(job, body, dict(metrics))
        if not published:
            raise PolishLeaseLost()
        metrics["published_as"] = published.get("published_as")
    except PolishLeaseLost:
        metrics["status"] = "lease_lost"
        logger.warning("润色任务租约失效，停止发布: %s", job["job_id"])
    except ValueError as exc:
        fail("polish_unavailable", str(exc), False)
    except ModelError:
        fail("model_unavailable", "文本模型暂时不可用，请稍后重试", True)
    except Exception:
        logger.exception("润色任务处理失败: %s", job["job_id"])
        fail("processing_unavailable", "笔记整理服务暂时不可用，请稍后重试", True)
    finally:
        if token is not None:
            store.reset_user(token)


def _request(model, schema, task, data, metrics):
    metrics["model_requests"] = metrics.get("model_requests", 0) + 1
    return model.structured(schema, task, data)


def _plan_outline(model, title, units, metrics):
    groups, pending, chars = [], [], 0
    for unit in units:
        if pending and (chars + len(unit["text"]) > 32000 or len(pending) >= 200):
            groups.append(pending)
            pending, chars = [], 0
        pending.append(unit)
        chars += len(unit["text"])
    if pending:
        groups.append(pending)

    def plan(group, candidates=None):
        errors, outline = [], None
        for attempt in range(2):
            aliases = {unit["id"]: f"b{index + 1}" for index, unit in enumerate(group)}
            if candidates is not None:
                candidates = [
                    {
                        **candidate,
                        "sections": [
                            {
                                **section,
                                "block_ids": [
                                    aliases.get(key, key) for key in section["block_ids"]
                                ],
                            }
                            for section in candidate["sections"]
                        ],
                        "excluded": [
                            {
                                **item,
                                "block_id": aliases.get(item["block_id"], item["block_id"]),
                                "duplicate_of": aliases.get(
                                    item.get("duplicate_of"), item.get("duplicate_of", "")
                                ),
                            }
                            for item in candidate.get("excluded", [])
                        ],
                    }
                    for candidate in candidates
                ]
            data = (
                outline_input(title, group)
                if candidates is None
                else dict(
                    document_title=title,
                    candidate_plans=candidates,
                    instruction="将候选规划中同主题章节合并，按学习顺序重排；保留全部块ID的唯一分配。",
                )
            )
            if attempt:
                data.update(issues=errors, previous_outline=outline)
            outline = _request(model, PolishOutline, OUTLINE_TASK, data, metrics)
            outline, notes = repair_outline(outline, group)
            if notes:
                metrics.setdefault("outline_repairs", []).extend(notes)
            errors = validate_outline(outline, group)
            if not any(s["title"] != "补充知识" for s in outline["sections"]):
                errors.append("未成功分配知识章节，请将输入块ID逐个写入真实章节的block_ids")
            if not errors:
                break
        if errors:
            raise ValueError("章节规划无效：" + "；".join(errors[:4]))
        return outline

    plans = [plan(group) for group in groups]
    outline = plans[0] if len(plans) == 1 else plan(units, plans)
    # Exclusions never reach drafting, so explicitly review them here.
    if outline.get("excluded"):
        by_id = {u["id"]: u for u in units}
        excluded_ids = {item["block_id"] for item in outline["excluded"]}
        excluded_ids.update(item.get("duplicate_of") for item in outline["excluded"])
        review = _request(
            model,
            PolishBatchReview,
            "检查被排除的来源是否仅为目录导航、重复标题、页眉页脚、逐字重复或不可读碎片。"
            "包含独立知识的教学目标、回顾、正文不能被整体删除。"
            "只检查输入块text实际包含的文字，不推测该标题后可能还有什么正文。"
            "仅写着‘教学目标’‘前期回顾’等几个字的标题本身没有独立知识，可删除。"
            "标题所对应的正文是另一块；不能因为没有看到正文就假设正文也被删除。"
            "有知识遗漏则 supported=false，在 issues 给出具体块ID与原因；否则 true 且 issues=[]。",
            dict(
                excluded=outline["excluded"],
                blocks=[by_id[key] for key in excluded_ids if key in by_id],
            ),
            metrics,
        )
        if not review.get("supported") or review.get("issues"):
            # Route disputed exclusions into writing, rather than losing knowledge.
            # Return only disputed content to drafting, keeping safe packaging out.
            messages = " ".join(issue["issue"] for issue in review.get("issues", []))
            ids = [item["block_id"] for item in outline["excluded"] if item["block_id"] in messages]
            if not ids:
                ids = [item["block_id"] for item in outline["excluded"]]
            outline["sections"].append(
                dict(
                    title="补充知识",
                    level=1,
                    note="保留原规划排除内容中的独立知识，合并导航包装",
                    key_points=[],
                    block_ids=ids,
                )
            )
            outline["excluded"] = [
                item for item in outline["excluded"] if item["block_id"] not in ids
            ]
    return consolidate_outline(outline, units)


def _review_document(model, title, outline, results, metrics, *, blocking_only=False):
    import json

    groups, group, size = [], [], 0
    for index, entry in enumerate(results):
        if not entry["result"]["elements"]:
            continue
        item = dict(
            batch_index=index,
            section_title=entry["section_title"],
            part=entry.get("part", 1),
            parts=entry.get("parts", 1),
            elements=entry["result"]["elements"],
            source_gaps=[
                item
                for item in entry["result"].get("excluded", [])
                if item.get("category") == "source_gap"
            ],
        )
        chars = len(json.dumps(item, ensure_ascii=False))
        if group and size + chars > 40000:
            groups.append(group)
            group, size = [], 0
        group.append(item)
        size += chars
    if group:
        groups.append(group)
    issues = {}
    for position, group in enumerate(groups):
        data = dict(
            document_title=title,
            outline=[
                dict(title=s["title"], key_points=s.get("key_points", []))
                for s in outline["sections"]
            ],
            chapters=group,
            previous_tail=groups[position - 1][-1]["elements"][-2:] if position else [],
            next_head=groups[position + 1][0]["elements"][:2] if position + 1 < len(groups) else [],
        )
        review = _request(model, PolishDocumentReview, DOCUMENT_REVIEW_TASK, data, metrics)
        if not review.get("supported") and not review.get("issues"):
            raise ValueError("全文复核未通过且未给出可修复的问题")
        valid_indices = {item["batch_index"] for item in group}
        for issue in review.get("issues", []):
            if issue["batch_index"] not in valid_indices:
                raise ValueError("全文复核引用了本组之外的章节")
            if blocking_only and issue.get("severity", "suggestion") != "error":
                continue
            issues.setdefault(issue["batch_index"], []).append(issue["issue"])
    return issues


def _run_batch(model, document_title, batch, previous_tail, next_head, metrics, *, review_mode):
    """One drafting call plus one review call; at most one repair round.

    Learning notes always receive semantic review, even when a legacy
    deployment still carries ``review_mode=on_check_failed``.
    """
    data = batch_input(document_title, batch, previous_tail, next_head)
    if batch.get("editorial_issues"):
        data["editorial_issues"] = batch["editorial_issues"]
    result = repair_batch(model.structured(PolishBatch, BATCH_TASK, data), batch)
    metrics["model_requests"] = metrics.get("model_requests", 0) + 1
    errors = check_batch(result, batch)
    review = _review_decision(
        model.structured(
            PolishBatchReview, REVIEW_TASK, review_input(document_title, batch, result)
        )
    )
    metrics["model_requests"] = metrics.get("model_requests", 0) + 1
    if not errors and review.get("supported") and not review.get("issues"):
        return result, review
    # Optional explanations can be removed without losing the source material.
    # This is a bounded repair, followed by another semantic review, not a bypass.
    removable = {
        issue["index"]
        for issue in review.get("issues", [])
        if 0 <= issue["index"] < len(result["elements"])
    }
    for error in errors:
        match = re.search(r"元素 (\d+)", error)
        if match and int(match[1]) < len(result["elements"]):
            removable.add(int(match[1]))
    if removable:
        trimmed = {
            **result,
            "elements": [e for i, e in enumerate(result["elements"]) if i not in removable],
        }
        from .material_note_polish import _element_nodes
        from .material_notes import node_text

        remaining = node_text(
            dict(
                type="group",
                content=[node for e in trimmed["elements"] for node in _element_nodes(e)],
            )
        )
        excluded_ids = {item["block_id"] for item in trimmed.get("excluded", [])}
        source_numbers = {
            token
            for unit in batch["units"]
            if unit["reliable"] and unit["id"] not in excluded_ids
            for token in re.findall(r"\d+(?:\.\d+)?%?", unit["text"])
        }
        retained_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", remaining))
        if source_numbers <= retained_numbers and not check_batch(trimmed, batch):
            verdict = _review_decision(
                model.structured(
                    PolishBatchReview, REVIEW_TASK, review_input(document_title, batch, trimmed)
                )
            )
            metrics["model_requests"] = metrics.get("model_requests", 0) + 1
            if verdict["supported"]:
                return trimmed, verdict
            return None, {**verdict, "program_errors": []}
    issues = list(errors) + [
        f"元素 {item.get('index')}：{item.get('issue')}" for item in review.get("issues", [])
    ]
    repair_data = {
        **data,
        "issues": issues,
        "draft": [{"index": i, "element": e} for i, e in enumerate(result["elements"])],
        "excluded": result.get("excluded", []),
        "valid_indices": list(range(len(result["elements"]))),
    }
    patch = model.structured(
        PolishBatchRepair,
        "你是学习笔记修订编辑，仅修复issues指出的元素，不重写整批。"
        "changes中index严格复制draft中的index，不是来源块编号；每个index只能出现一次。"
        "replacement为修订后的完整元素（不含index）；删除设null；不能使用valid_indices以外的序号。"
        "保留来源支持的实质知识，删除无来源命令、机制和数值，减少有问题的辅助扩写。"
        "来源只有操作标题而无实际命令时，不补造命令，将该块记入excluded(category=source_gap)。"
        "教学目录和纯目标导航可作为navigation，预习安排可作为course_admin。"
        "新增excluded给出block_id/category/reason/duplicate_of；已存在的排除项无需重复。"
        "不改动未受影响的元素；只使用blocks的来源ID；解释不必逐字但不得改变原意。",
        repair_data,
    )
    metrics["model_requests"] = metrics.get("model_requests", 0) + 1
    replacements = {}
    for change in patch["changes"]:
        index = change["index"]
        if index in replacements or not 0 <= index < len(result["elements"]):
            return None, {"supported": False, "issues": [], "program_errors": ["修订序号无效"]}
        replacements[index] = change["replacement"]
    revised = [replacements.get(i, element) for i, element in enumerate(result["elements"])]
    excluded = {item["block_id"]: item for item in result.get("excluded", [])}
    excluded.update({item["block_id"]: item for item in patch["excluded"]})
    result = repair_batch(
        dict(elements=[e for e in revised if e is not None], excluded=list(excluded.values())),
        batch,
    )
    errors = check_batch(result, batch)
    review = _review_decision(
        model.structured(
            PolishBatchReview, REVIEW_TASK, review_input(document_title, batch, result)
        )
    )
    metrics["model_requests"] = metrics.get("model_requests", 0) + 1
    if errors or not review.get("supported") or review.get("issues"):
        return None, {**review, "program_errors": errors}
    return result, review


def _review_decision(review):
    errors = [
        issue for issue in review.get("issues", []) if issue.get("severity", "error") == "error"
    ]
    suggestions = [
        issue for issue in review.get("issues", []) if issue.get("severity") == "suggestion"
    ]
    return {
        **review,
        "supported": bool((review.get("supported") or suggestions) and not errors),
        "issues": errors,
        "suggestions": suggestions,
    }


def _split_failed_batches(batches, previous, **key_args):
    """Reuse prior subdivisions as well as splitting freshly truncated batches."""
    expanded = []

    def fragment(batch, units):
        child = {**batch, "units": units}
        child["cache_key"] = batch_cache_key(
            key_args["user_id"],
            key_args["document_id"],
            key_args["source_hash"],
            key_args["prompt_version"],
            key_args["model_config"],
            [unit["id"] for unit in units],
            context=child.get("cache_context"),
        )
        return child

    def emit(batch):
        row = previous.get(batch["cache_key"])
        if row and row.get("error") == "output_limit" and len(batch["units"]) >= 2:
            middle = len(batch["units"]) // 2
            emit(fragment(batch, batch["units"][:middle]))
            emit(fragment(batch, batch["units"][middle:]))
            return
        # Reindexing child rows must not erase the fact that their parent split.
        ids = [unit["id"] for unit in batch["units"]]
        pieces, start = [], 0
        while start < len(ids):
            matches = [
                r
                for r in previous.values()
                if r.get("block_ids")
                and len(r["block_ids"]) < len(ids)
                and ids[start : start + len(r["block_ids"])] == r["block_ids"]
            ]
            if not matches:
                break
            chosen = max(matches, key=lambda r: len(r["block_ids"]))
            end = start + len(chosen["block_ids"])
            pieces.append(batch["units"][start:end])
            start = end
        if start == len(ids) and len(pieces) > 1:
            for piece in pieces:
                emit(fragment(batch, piece))
            return
        expanded.append(batch)

    for batch in batches:
        emit(batch)
    return expanded


def outline_title(document) -> str:
    title = (document.get("title") or "").strip()
    return title or (document.get("file_name") or "资料笔记")


def main() -> None:
    from .config import Settings

    settings = Settings()
    database_url = settings.database_url.get_secret_value()
    if not database_url:
        raise RuntimeError("polish worker 需要 DATABASE_URL")
    store = PostgresStore(database_url)
    store.setup()
    shutdown = Event()

    def execute(job):
        task_store = PostgresStore(database_url)
        try:
            process_polish_job(task_store, job, settings)
        finally:
            task_store.close()

    try:
        run_queue(
            store.claim_polish_job,
            execute,
            settings.material_note_polish_concurrency,
            stop_event=shutdown,
        )
    finally:
        store.close()
        flush()


if __name__ == "__main__":
    main()
