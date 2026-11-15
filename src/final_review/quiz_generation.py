"""Formal paper generation: bounded model calls and atomic publication."""

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from hashlib import sha256

from pydantic import ValidationError

from .course_chat import CourseMaterials
from .domain import DomainConflict, DomainNotFound, DomainService
from .quiz_contract import (
    QuizDraftPlan,
    QuizDraftSemanticReview,
    QuizPaperReview,
    QuizPlanEvidenceReview,
    QuizPlanPointReplacement,
    build_quiz_context,
    validate_quiz_draft,
    validate_quiz_plan,
)
from .quiz_print import build_quiz_sections
from .storage import stable_key


class QuizGenerationError(ValueError):
    def __init__(self, message, issues=None, diagnostics=None):
        super().__init__(message)
        self.issues = issues or []
        self.diagnostics = diagnostics or {}


def locked(store, table, key):
    return getattr(store, "get_for_update", store.get)(table, key)


def paper_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def quiz_context(store, config):
    materials = CourseMaterials(store, config.course_id, config.user_id)
    documents = materials.select(config.source_document_ids, config.chapter)
    # Read real chunks fairly across files; never truncate a chunk and then claim a full quote.
    evidence, coverage = materials.read(documents, budget=60000)
    evidence = [item for item in evidence if item.source_type != "ai_supplement"][:80]
    coverage.update(read_chunks=len(evidence), partial=len(evidence) < coverage["available_chunks"])
    if not evidence:
        raise QuizGenerationError("所选资料没有可用的可信证据，请调整资料或范围")
    coverage["selected_files"] = len(documents)
    coverage["read_document_ids"] = sorted({e.document_id for e in evidence})
    return build_quiz_context(
        store, config, [e.model_dump(mode="json") for e in evidence]
    ), coverage


def _plan_from_slots(slots):
    allocations = {}
    for slot in sorted(slots, key=lambda item: item["order"]):
        key = (slot["knowledge_point"], slot["question_type"])
        if key not in allocations:
            allocations[key] = {
                **slot["allocation"],
                "knowledge_point": slot["knowledge_point"],
                "question_type": slot["question_type"],
                "question_count": 0,
                "document_ids": [],
            }
        allocations[key]["question_count"] += 1
        allocations[key]["document_ids"] = sorted(
            set(allocations[key]["document_ids"]) | set(slot["allocation"].get("document_ids", []))
        )
    return {"allocations": list(allocations.values())}


def _rank_quiz_evidence(context, slots):
    terms = set()
    documents = {doc for slot in slots for doc in slot["allocation"].get("document_ids", [])}
    for slot in slots:
        for term in re.findall(
            r"[A-Za-z][A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", slot["knowledge_point"]
        ):
            terms.add(term.casefold())
            if not term.isascii():
                terms.update(term[i : i + 2] for i in range(len(term) - 1))
    return [
        item
        for _, item in sorted(
            enumerate(context.evidence),
            key=lambda pair: (
                -sum(t in pair[1].content.casefold() for t in terms),
                pair[1].document_id not in documents,
                pair[0],
            ),
        )
    ]


def _batch_context(context, slots, previous, evidence_chunk_ids=()):
    """A batch has its own quota; all citations still bind to the original snapshots."""
    plan = _plan_from_slots(slots)
    counts = {}
    for slot in slots:
        kind = slot["question_type"]
        counts[kind] = counts.get(kind, 0) + 1
    config = context.config.model_dump(mode="json")
    config.update(
        blueprint=[{"question_type": k, "question_count": v} for k, v in counts.items()],
        question_count=len(slots),
        total_score=float(sum(Decimal(str(s["score"])) for s in slots)),
        knowledge_points=sorted({s["knowledge_point"] for s in slots})
        if config["scope_mode"] == "knowledge_points"
        else [],
    )
    # Reserve evidence per slot before filling the shared batch budget.
    selected = {e.chunk_id: e for e in context.evidence if e.chunk_id in evidence_chunk_ids}
    for slot in slots:
        for item in _rank_quiz_evidence(context, [slot])[:3]:
            selected.setdefault(item.chunk_id, item)
    for item in _rank_quiz_evidence(context, slots):
        if len(selected) >= 24:
            break
        selected.setdefault(item.chunk_id, item)
    return context.model_copy(
        update={
            "config": type(context.config).model_validate(config),
            "evidence": list(selected.values()),
            "question_slots": [{k: v for k, v in s.items() if k != "allocation"} for s in slots],
            "previous_questions": [
                {k: q[k] for k in ("id", "knowledge_point", "stem", "reference_answer")}
                for q in previous
            ],
        }
    ), plan


def _new_batch_state(attempts=0):
    return {
        "attempts": attempts,
        "candidate": None,
        "evaluated": False,
        "audit": [],
        "evidence_chunk_ids": [],
        "plan_evidence_checks": {},
    }


def _batch_state_key(slots):
    return paper_hash(
        [
            {k: slot[k] for k in ("id", "knowledge_point", "question_type", "score")}
            for slot in slots
        ]
    )


def _check_plan_citations(context, citations):
    lookup = {item.chunk_id: item for item in context.evidence}
    if any(
        ref.chunk_id not in lookup or ref.quote not in lookup[ref.chunk_id].content
        for ref in citations
    ):
        raise QuizGenerationError("计划证据复核返回了无效的原文引用")
    return {lookup[ref.chunk_id].document_id for ref in citations}


def _recheck_plan_evidence(model, context, slot):
    try:
        review = QuizPlanEvidenceReview.model_validate(
            model.review_quiz_plan_evidence(context, slot)
        )
    except ValidationError:
        raise QuizGenerationError("计划证据复核结果结构无效，请重试") from None
    _check_plan_citations(context, review.citations)
    return review.model_dump(mode="json")


def _replace_plan_points(
    model,
    context,
    plan_context,
    state,
    record,
    slots,
    previous,
    max_plan_changes,
    save_checkpoint,
):
    """Only this path may change a pending slot's planned knowledge point."""
    targets = set(record["unsupported_slot_ids"])
    for slot in slots:
        if slot["id"] not in targets:
            continue
        point = slot["knowledge_point"]
        evidence = _rank_quiz_evidence(context, [slot])[:48]
        check_context = context.model_copy(update={"evidence": evidence})
        state["read_chunk_ids"] = sorted(
            set(state["read_chunk_ids"]) | {e.chunk_id for e in evidence}
        )
        checks = record["plan_evidence_checks"]
        if point not in checks:
            checks[point] = _recheck_plan_evidence(model, check_context, slot)
            state["audit"].append(
                {
                    "phase": "plan_evidence",
                    "question_id": slot["id"],
                    "knowledge_point": point,
                    "review": checks[point],
                }
            )
            save_checkpoint(state)
        if checks[point]["supported"]:
            record["evidence_chunk_ids"] = sorted(
                set(record["evidence_chunk_ids"]) | {e.chunk_id for e in evidence}
            )
            continue
        # Explicit student choices and emphasis are not replaceable model suggestions.
        protected = context.config.scope_mode == "knowledge_points" or any(
            focus.casefold() in point.casefold() or point.casefold() in focus.casefold()
            for focus in context.config.emphasis
        )
        if protected:
            raise QuizGenerationError(
                f"指定知识点或重点“{point}”缺少可用证据，请调整资料或配置",
                [
                    {
                        "code": "plan_evidence",
                        "question_id": slot["id"],
                        "message": checks[point]["reason"],
                    }
                ],
            )
        replacement_evidence = {e.chunk_id: e for e in plan_context.evidence + evidence}
        replacement_context = context.model_copy(
            update={"evidence": list(replacement_evidence.values())}
        )
        state["read_chunk_ids"] = sorted(set(state["read_chunk_ids"]) | set(replacement_evidence))
        while state["plan_changes_used"] < max_plan_changes:
            change_issues = [checks[point]["reason"]]
            change_issues.extend(
                message
                for prior in state["audit"]
                if prior.get("phase") == "plan_change"
                and prior.get("old_knowledge_point") == point
                and prior.get("status") == "rejected"
                for message in prior.get("issues", [])
            )
            state["plan_changes_used"] += 1
            entry = {
                "phase": "plan_change",
                "attempt": state["plan_changes_used"],
                "question_ids": [s["id"] for s in slots if s["knowledge_point"] == point],
                "old_knowledge_point": point,
                "status": "started",
            }
            state["audit"].append(entry)
            save_checkpoint(state)
            try:
                change = QuizPlanPointReplacement.model_validate(
                    model.replace_quiz_plan_points(
                        replacement_context,
                        slot,
                        state["plan"],
                        change_issues,
                        previous,
                    )
                )
                if "".join(change.knowledge_point.split()).casefold() == (
                    "".join(point.split()).casefold()
                ):
                    raise QuizGenerationError("更换后的知识点必须区别于原知识点")
                documents = _check_plan_citations(replacement_context, change.citations)
                if documents != set(change.document_ids):
                    raise QuizGenerationError("新知识点的来源文件必须与实际证据引用一致")
                pending = deepcopy(state["pending"])
                for target in pending[0]:
                    if target["knowledge_point"] == point:
                        target["knowledge_point"] = change.knowledge_point
                        target["allocation"] = {
                            **target["allocation"],
                            "knowledge_point": change.knowledge_point,
                            "document_ids": sorted(documents),
                            "allocation_reason": change.allocation_reason,
                        }
                all_slots = [s for b in state["completed"] for s in b["slots"]]
                all_slots += [s for batch in pending for s in batch]
                plan = _plan_from_slots(all_slots)
                issues = validate_quiz_plan(context, plan)
                if issues:
                    raise QuizGenerationError("新知识点不符合整卷计划约束", issues)
                new_slot = next(s for s in pending[0] if s["id"] == slot["id"])
                cited_ids = {ref.chunk_id for ref in change.citations}
                support_context = replacement_context.model_copy(
                    update={
                        "evidence": [
                            e for e in replacement_context.evidence if e.chunk_id in cited_ids
                        ],
                    }
                )
                support = _recheck_plan_evidence(model, support_context, new_slot)
                if not support["supported"]:
                    raise QuizGenerationError(support["reason"])
            except (ValidationError, QuizGenerationError) as exc:
                entry.update(status="rejected", issues=[str(exc)])
                save_checkpoint(state)
                continue
            entry.update(
                status="accepted",
                new_knowledge_point=change.knowledge_point,
                replacement=change.model_dump(mode="json"),
                evidence_review=support,
            )
            state.update(plan=plan, pending=pending)
            new_record = _new_batch_state()
            new_record["evidence_chunk_ids"] = sorted(
                set(record["evidence_chunk_ids"])
                | cited_ids
                | {ref["chunk_id"] for ref in support["citations"]}
            )
            new_record["plan_evidence_checks"] = {
                name: verdict
                for name, verdict in record["plan_evidence_checks"].items()
                if name != point
            }
            new_record["plan_evidence_checks"][change.knowledge_point] = support
            state["batch_generation_state"][_batch_state_key(pending[0])] = new_record
            save_checkpoint(state)
            return
        raise QuizGenerationError(
            "知识点更换次数已用完，请调整资料或重新配置试卷",
            [
                {
                    "code": "plan_change_limit",
                    "question_id": slot["id"],
                    "message": checks[point]["reason"],
                }
            ],
            {"audit": state["audit"]},
        )
    record["needs_plan_recheck"] = False
    save_checkpoint(state)


def generate_quiz(
    store,
    model,
    config,
    *,
    max_repairs=2,
    max_plan_changes=3,
    max_question_retries=3,
    progress=lambda *_: None,
    checkpoint=None,
    save_checkpoint=lambda *_: None,
    choice_batch_size=5,
    written_batch_size=2,
):
    """Resume verified batches; split truncated batches; publish only a complete validated paper."""
    from .llm import ModelOutputLimitError

    progress("retrieving")
    plan_context, coverage = quiz_context(store, config)
    materials = CourseMaterials(store, config.course_id, config.user_id)
    pool, _ = materials.read(
        materials.select(config.source_document_ids, config.chapter), budget=600000
    )
    pool = [e for e in pool if e.source_type != "ai_supplement"][:1000]
    context = build_quiz_context(store, config, [e.model_dump(mode="json") for e in pool])
    fingerprint = paper_hash(context.model_dump(mode="json"))
    state = json.loads(json.dumps(checkpoint)) if checkpoint else None
    if state and state.get("version") != 2:
        raise QuizGenerationError("旧版生成检查点不兼容新的重试规则，请重新创建试卷任务")
    if state and state.get("context_hash") != fingerprint:
        raise QuizGenerationError("资料或生成合同已变更，请重新配置试卷")
    if not state:
        progress("planning")
        audit, issues = [], []
        for repair in range(max_repairs + 1):
            plan = model.quiz_draft_plan(plan_context, issues=issues)
            issues = validate_quiz_plan(context, plan)
            audit.append({"phase": "plan", "repair": repair, "issues": issues})
            if not issues:
                break
        else:
            coverage = [item for item in issues if item.get("code") == "source_coverage"]
            message = coverage[-1]["message"] if coverage else "出题计划未满足配置，修复次数已用完"
            raise QuizGenerationError(message, issues, {"audit": audit, "plan": plan})
        count = config.question_count
        total = Decimal(str(config.total_score if config.total_score is not None else count * 10))
        # Six decimal places also support unusually small positive totals without zero scores.
        unit = total / count
        quantum = Decimal("0.01") if total >= count * Decimal("0.01") else Decimal("0.000001")
        unit = unit.quantize(quantum, rounding=ROUND_DOWN)
        if unit <= 0:
            raise QuizGenerationError("总分过小，无法为每道题分配正分值，请调整总分")
        slots, pending = [], []
        assigned = Decimal("0")
        for allocation in plan["allocations"]:
            for _ in range(allocation["question_count"]):
                order = len(slots) + 1
                cumulative = (
                    total
                    if order == count
                    else (total * order / count).quantize(quantum, rounding=ROUND_DOWN)
                )
                score = cumulative - assigned
                assigned = cumulative
                slots.append(
                    {
                        "id": f"q{order}",
                        "order": order,
                        "knowledge_point": allocation["knowledge_point"],
                        "question_type": allocation["question_type"],
                        "score": float(score),
                        "allocation": allocation,
                    }
                )
        for slot in slots:
            limit = (
                choice_batch_size
                if slot["question_type"]
                in {"choice", "multiple_choice", "true_false", "fill_blank"}
                else written_batch_size
            )
            if (
                not pending
                or len(pending[-1]) >= limit
                or (pending[-1][0]["question_type"] != slot["question_type"])
            ):
                pending.append([])
            pending[-1].append(slot)
        state = {
            "version": 2,
            "context_hash": fingerprint,
            "plan": plan,
            "pending": pending,
            "completed": [],
            "audit": audit,
            "title": None,
            "plan_changes_used": 0,
            "batch_generation_state": {},
            "limits": {"plan_changes": max_plan_changes, "question_retries": max_question_retries},
            "read_chunk_ids": [e.chunk_id for e in plan_context.evidence],
        }
        save_checkpoint(state)
    max_plan_changes = min(max_plan_changes, state["limits"]["plan_changes"])
    max_question_retries = min(max_question_retries, state["limits"]["question_retries"])
    previous = [q for batch in state["completed"] for q in batch["questions"]]
    while state["pending"]:
        slots = state["pending"][0]
        record = state["batch_generation_state"].setdefault(
            _batch_state_key(slots), _new_batch_state()
        )
        batch_context, batch_plan = _batch_context(
            context, slots, previous, record["evidence_chunk_ids"]
        )
        batch_context = batch_context.model_copy(
            update={"repair_notes": state.get("paper_issues", [])}
        )
        state["read_chunk_ids"] = sorted(
            set(state["read_chunk_ids"]) | {e.chunk_id for e in batch_context.evidence}
        )
        try:
            generated = _generate_batch(
                model,
                batch_context,
                coverage,
                batch_plan,
                slots,
                previous,
                max_question_retries,
                progress,
                batch_state=record,
                save_attempt=lambda: save_checkpoint(state),
            )
        except ModelOutputLimitError as exc:
            if len(slots) == 1:
                raise QuizGenerationError(
                    "单题输出仍达到长度上限；已完成题目已保存，请调整输出预算后重试",
                    [{"code": "output_length", "question_id": slots[0]["id"]}],
                    diagnostics=exc.diagnostics,
                ) from None
            middle = len(slots) // 2
            state["pending"][:1] = [slots[:middle], slots[middle:]]
            for child in state["pending"][:2]:
                state["batch_generation_state"].setdefault(
                    _batch_state_key(child), _new_batch_state(record["attempts"])
                )
            state["audit"].append(
                {
                    "phase": "split",
                    "question_ids": [s["id"] for s in slots],
                    "diagnostics": exc.diagnostics,
                }
            )
            save_checkpoint(state)
            continue
        if generated.get("needs_plan_recheck"):
            progress("planning")
            _replace_plan_points(
                model,
                context,
                plan_context,
                state,
                record,
                slots,
                previous,
                max_plan_changes,
                save_checkpoint,
            )
            continue
        questions = generated["draft"]["questions"]
        ids = {q["id"]: slot["id"] for q, slot in zip(questions, slots, strict=True)}
        for entry in generated["audit"]:
            for item in (entry.get("review") or {}).get("items", []):
                item["question_id"] = ids.get(item["question_id"], item["question_id"])
        for question, slot in zip(questions, slots, strict=True):
            question.update(id=slot["id"], order=slot["order"])
        state["title"] = state["title"] or generated["draft"]["title"]
        state["completed"].append(
            {"questions": questions, "slots": slots, "audit": generated["audit"]}
        )
        state["pending"].pop(0)
        previous.extend(questions)
        save_checkpoint(state)
    previous.sort(key=lambda q: q["order"])
    progress("validating")
    # Strip server-derived fields before reusing the strict candidate schema.
    from .quiz_contract import QuizDraftQuestion

    fields = set(QuizDraftQuestion.model_fields)
    candidate = {
        "title": state["title"],
        "questions": [{k: v for k, v in q.items() if k in fields} for q in previous],
    }
    validation = validate_quiz_draft(context, state["plan"], candidate)
    if not validation.valid:
        raise QuizGenerationError(
            "整卷校验未通过，已完成批次保留",
            [i.model_dump(mode="json") for i in validation.issues],
        )
    if len(state["completed"]) > 1 and hasattr(model, "review_quiz_paper"):
        progress("reviewing")
        try:
            review = QuizPaperReview.model_validate(model.review_quiz_paper(context, candidate))
        except ValidationError:
            raise QuizGenerationError("整卷复核结果结构无效，请重试") from None
        ids = {q["id"] for q in previous}
        affected = set(review.affected_question_ids)
        if not affected <= ids:
            raise QuizGenerationError("整卷复核返回未知题目，请重试")
        state["audit"].append({"phase": "paper_review", **review.model_dump(mode="json")})
        if not review.supported:
            invalid = [
                b
                for b in state["completed"]
                if not affected or any(q["id"] in affected for q in b["questions"])
            ]
            state["completed"] = [b for b in state["completed"] if b not in invalid]
            state["pending"] = sorted([b["slots"] for b in invalid], key=lambda b: b[0]["order"])
            state["paper_issues"] = review.issues
            state["paper_repairs"] = state.get("paper_repairs", 0) + 1
            for batch in invalid:
                record = state["batch_generation_state"][_batch_state_key(batch["slots"])]
                record.update(
                    passed=False,
                    evaluated=True,
                    issues=[{"code": "paper_semantic", "message": i} for i in review.issues],
                )
            save_checkpoint(state)
            if state["paper_repairs"] > max_repairs:
                raise QuizGenerationError(
                    "整卷复核未通过，修复次数已用完",
                    [{"code": "paper_semantic", "message": i} for i in review.issues],
                )
            return generate_quiz(
                store,
                model,
                config,
                max_repairs=max_repairs,
                max_plan_changes=max_plan_changes,
                max_question_retries=max_question_retries,
                progress=progress,
                checkpoint=state,
                save_checkpoint=save_checkpoint,
                choice_batch_size=choice_batch_size,
                written_batch_size=written_batch_size,
            )
        save_checkpoint(state)
    read_ids = set(state["read_chunk_ids"])
    coverage.update(
        read_chunks=len(read_ids),
        partial=len(read_ids) < coverage["available_chunks"],
        read_document_ids=sorted(
            {e.document_id for e in context.evidence if e.chunk_id in read_ids}
        ),
        eligible_chunks=len(context.evidence),
    )
    return {
        "context": context,
        "plan": state["plan"],
        "draft": validation.validated_draft,
        "coverage": coverage,
        "audit": state["audit"]
        + [
            {**entry, "question_ids": [q["id"] for q in batch["questions"]]}
            for batch in state["completed"]
            for entry in batch["audit"]
        ],
    }


def _generate_batch(
    model,
    context,
    coverage,
    plan,
    slots,
    previous,
    max_retries,
    progress,
    *,
    batch_state=None,
    save_attempt=lambda: None,
):
    record = batch_state if batch_state is not None else _new_batch_state()
    audit = record["audit"]
    while True:
        if record.get("passed"):
            return {
                "context": context,
                "plan": plan,
                "draft": record["validated_draft"],
                "coverage": coverage,
                "audit": audit,
            }
        if record.get("needs_plan_recheck"):
            return {"needs_plan_recheck": True}
        if record["candidate"] is None or record["evaluated"]:
            if record["attempts"] >= max_retries + 1:
                raise QuizGenerationError(
                    "试卷未通过校验或语义复核，生题重试次数已用完",
                    record.get("issues", []),
                    {"audit": audit, "candidate": record["candidate"], "plan": plan},
                )
            old_candidate = record["candidate"]
            record.update(attempts=record["attempts"] + 1, candidate=None, evaluated=False)
            # Persist before the model call so recovery cannot grant another free attempt.
            save_attempt()
            progress("repairing" if old_candidate is not None else "generating")
            record["candidate"] = (
                model.repair_quiz_draft(context, plan, old_candidate, record.get("issues", []))
                if old_candidate is not None
                else model.quiz_draft(context, QuizDraftPlan.model_validate(plan))
            )
            save_attempt()
        candidate = record["candidate"]
        progress("validating")
        validation = validate_quiz_draft(context, plan, candidate)
        issues = [i.model_dump(mode="json") for i in validation.issues]
        review = None
        if validation.valid:
            for index, question in enumerate(candidate["questions"]):
                if Decimal(str(question["score"])) != Decimal(str(slots[index]["score"])):
                    issues.append({"code": "slot_score", "message": "题目分值必须符合槽位分配"})
                if (question["knowledge_point"], question["question_type"]) != (
                    slots[index]["knowledge_point"],
                    slots[index]["question_type"],
                ):
                    issues.append(
                        {
                            "code": "slot_allocation",
                            "question_id": question["id"],
                            "message": f"必须保持槽位知识点“{slots[index]['knowledge_point']}”"
                            f"和题型 {slots[index]['question_type']}；实际为"
                            f"“{question['knowledge_point']}”和题型 {question['question_type']}。",
                        }
                    )
            stems = {"".join(q["stem"].split()) for q in previous}
            if any("".join(q["stem"].split()) in stems for q in candidate["questions"]):
                issues.append({"code": "duplicate_stem", "message": "题干与已完成批次重复"})
        if validation.valid and not issues:
            progress("reviewing")
            try:
                review = QuizDraftSemanticReview.model_validate(
                    model.review_quiz_draft(context, validation.validated_draft)
                ).model_dump(mode="json")
                expected = {q["id"] for q in candidate["questions"]}
                ids = [i["question_id"] for i in review["items"]]
                if set(ids) != expected or len(ids) != len(expected):
                    issues = [{"code": "review_coverage", "message": "复核遗漏或重复题目"}]
                else:
                    issues = [
                        {
                            "code": "plan_evidence"
                            if i["plan_evidence_insufficient"]
                            else "semantic",
                            "question_id": i["question_id"],
                            "message": message,
                        }
                        for i in review["items"]
                        for message in i["issues"]
                    ]
            except ValidationError:
                issues = [{"code": "review_schema", "message": "语义复核结果结构无效"}]
        audit.append(
            {
                "phase": "draft",
                "repair": record["attempts"] - 1,
                "issues": issues,
                "review": review,
            }
        )
        record.update(evaluated=True, issues=issues)
        if not issues:
            record.update(passed=True, validated_draft=validation.validated_draft)
        elif review and not any(issue["code"] == "review_coverage" for issue in issues):
            unsupported = {
                item["question_id"]
                for item in review["items"]
                if item["plan_evidence_insufficient"]
            }
            # Model IDs are local to the batch; checkpoint targets use global slot IDs.
            record["unsupported_slot_ids"] = [
                slot["id"]
                for question, slot in zip(candidate["questions"], slots, strict=True)
                if question["id"] in unsupported
                and slot["knowledge_point"] not in record["plan_evidence_checks"]
            ]
            record["needs_plan_recheck"] = bool(record["unsupported_slot_ids"])
        save_attempt()


def publish_quiz(store, generated, run_id, model_id):
    """Caller holds the job lock; all records commit together with its success state."""
    context = generated["context"]
    config = context.config
    domain = DomainService(store, config.user_id)
    asset_id = "quiz-" + stable_key(config.user_id, config.course_id, run_id)[:32]
    revision_id = "revision-" + stable_key(asset_id, "1")[:32]
    with store.transaction():
        existing = store.get("learning_asset", asset_id)
        if existing:
            return quiz_card(existing, store.get("quiz_revision_payload", revision_id))
        # Consistent lock order with material deletion; locks are held until the outer commit.
        locked(store, "course", config.course_id)
        if config.exam_id:
            locked(store, "exam", config.exam_id)
        for document_id in sorted(config.source_document_ids):
            locked(store, "document", document_id)
        build_quiz_context(store, config, [e.model_dump(mode="json") for e in context.evidence])
        domain.course(config.course_id, writable=True)
        now = datetime.now(UTC).isoformat()
        common = {"user_id": config.user_id, "course_id": config.course_id, "created_at": now}
        draft = generated["draft"]
        score = float(sum(Decimal(str(q["score"])) for q in draft["questions"]))
        asset = {
            **common,
            "asset_id": asset_id,
            "asset_type": "quiz",
            "title": draft["title"],
            "status": "draft",
            "current_revision_id": None,
            "latest_revision_id": revision_id,
            "generation_method": "ai",
            "updated_at": now,
        }
        # Include a canonical body in the existing content-hash and immutability mechanism.
        body = {"config": config.model_dump(mode="json"), "plan": generated["plan"], "draft": draft}
        revision = {
            **common,
            "asset_id": asset_id,
            "revision_id": revision_id,
            "revision_no": 1,
            "state": "draft",
            "title": draft["title"],
            "updated_at": now,
            "markdown": json.dumps(body, ensure_ascii=False, sort_keys=True),
            "source_document_ids": sorted(
                {r["document_id"] for q in draft["questions"] for r in q["references"]}
            ),
            "based_on_revision_id": None,
            "edit_source": "ai",
            "quiz_contract_version": 1,
        }
        payload = {
            **common,
            "asset_id": asset_id,
            "quiz_revision_id": revision_id,
            "state": "draft",
            "asset_revision_id": revision_id,
            "title": draft["title"],
            "config": config.model_dump(mode="json"),
            "exam_id": config.exam_id,
            "plan": generated["plan"],
            "total_score": score,
            "duration_minutes": config.duration_minutes,
            "question_count": len(draft["questions"]),
            "questions": [],
            "coverage": generated["coverage"],
            "review_audit": generated["audit"],
            "model_id": model_id,
            "prompt_version": context.prompt_version,
            "content_hash": paper_hash(body),
            "generation_run_id": run_id,
        }
        store.put("learning_asset", asset_id, asset)
        store.put("asset_revision", revision_id, revision)
        for q in draft["questions"]:
            question_id = "question-" + stable_key(asset_id, q["id"])[:32]
            question_revision_id = "question-revision-" + stable_key(revision_id, q["id"])[:32]
            question = {
                **common,
                **{k: v for k, v in q.items() if k not in {"order", "score"}},
                "question_id": question_id,
                "question_revision_id": question_revision_id,
                "quiz_revision_id": revision_id,
                "revision_no": 1,
                "edit_source": "ai",
            }
            question["content_hash"] = paper_hash(question)
            payload["questions"].append(
                {
                    "question_revision_id": question_revision_id,
                    "order": q["order"],
                    "score": q["score"],
                }
            )
            # Source references retain a question ID as well as the owning paper revision.
            for index, ref in enumerate(q["references"]):
                document = store.get("document", ref["document_id"])
                store.ensure_material_source(document)
                source_id = "source-" + stable_key(question_revision_id, str(index))[:32]
                store.put(
                    "source_reference",
                    source_id,
                    {
                        **common,
                        **ref,
                        "source_reference_id": source_id,
                        "revision_id": revision_id,
                        "asset_revision_id": revision_id,
                        "question_revision_id": question_revision_id,
                        "locator_id": ref["chunk_id"],
                    },
                )
            # The parent payload must exist before the question FK is inserted.
            store.put("quiz_revision_payload", revision_id, payload)
            store.put("question_revision", question_revision_id, question)
        publish_quiz_revision(store, domain, asset, revision, payload, draft["questions"], now)
        return quiz_card(asset, payload)


def publish_quiz_revision(store, domain, asset, revision, payload, questions, now):
    """Promote a validated revision inside the caller's transaction."""
    asset_id = asset["asset_id"]
    revision_id = revision["revision_id"]
    previous_id = asset.get("current_revision_id")
    if previous_id:
        previous = domain._owned("asset_revision", previous_id)
        previous_payload = domain._owned("quiz_revision_payload", previous_id)
        if previous.get("state") != "confirmed" or previous_payload.get("state") != "confirmed":
            raise DomainConflict("当前正式试卷版本状态无效")
    questions_markdown, full_markdown = build_quiz_sections(
        payload["title"], payload["total_score"], questions
    )
    revision.update(state="confirmed", confirmed_at=now, updated_at=now, markdown=full_markdown)
    store.put("asset_revision", revision_id, revision)
    payload.update(state="confirmed", updated_at=now, print_question_length=len(questions_markdown))
    store.put("quiz_revision_payload", revision_id, payload)
    asset.update(
        status="confirmed",
        current_revision_id=revision_id,
        latest_revision_id=revision_id,
        updated_at=now,
    )
    store.put("learning_asset", asset_id, asset)
    # The asset must point to the replacement before the previous revision is superseded.
    if previous_id:
        previous["state"] = "superseded"
        previous_payload.update(state="superseded", updated_at=now)
        store.put("asset_revision", previous_id, previous)
        store.put("quiz_revision_payload", previous_id, previous_payload)
    domain._audit(asset["course_id"], "quiz.confirmed", "learning_asset", asset_id)


def quiz_card(asset, payload):
    return {
        "asset_id": asset["asset_id"],
        "revision_id": payload["quiz_revision_id"],
        "title": payload["title"],
        "state": payload["state"],
        "question_count": payload["question_count"],
        "total_score": payload["total_score"],
    }


def read_quiz(store, user_id, course_id, asset_id, revision_id):
    domain = DomainService(store, user_id)
    domain.course(course_id)
    asset = domain._owned("learning_asset", asset_id)
    payload = domain._owned("quiz_revision_payload", revision_id)
    if (
        asset["course_id"] != course_id
        or asset["asset_type"] != "quiz"
        or payload["asset_id"] != asset_id
    ):
        raise DomainNotFound(revision_id)
    questions = []
    pending_review = set(payload.get("pending_semantic_review_question_ids", []))
    for slot in payload["questions"]:
        question = domain._owned("question_revision", slot["question_revision_id"])
        if question["quiz_revision_id"] != revision_id or question["course_id"] != course_id:
            raise DomainConflict("题目版本关系无效")
        questions.append({**question, **slot})
        for ref in questions[-1]["references"]:
            document = store.get("document", ref["document_id"])
            ref["available"] = bool(
                document
                and document.get("user_id") == user_id
                and document.get("parse_status") == "ready"
                and any(
                    c["chunk_id"] == ref["chunk_id"]
                    for c in store.list_material_chunks(ref["document_id"])
                )
            )
        if slot["question_revision_id"] in pending_review:
            # Keep submitted references available to the future editor without
            # presenting them as verified sources for changed draft content.
            questions[-1]["candidate_references"] = questions[-1]["references"]
            questions[-1]["references"] = []
            questions[-1]["source_label"] = "来源待复核"
            questions[-1]["source_review_status"] = "pending"
    return {"asset": asset, "revision": {**payload, "questions": questions}}
