import re
from collections import Counter, defaultdict
from threading import Lock
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .checkpoints import SurrealSaver
from .config import Settings
from .domain import DomainService
from .llm import ModelError
from .policy import SOURCE_PRIORITY
from .schemas import (
    AgentRequest,
    AgentResponse,
    ExamProfile,
    GeneratedNote,
    Grades,
    GroundedAnswer,
    KnowledgePlan,
    NoteInput,
    Quiz,
    ResumeNoteRequest,
    ResumeRequest,
    Submission,
)
from .storage import stable_key


class SessionConflict(ValueError):
    pass


def _chapter_requirement(scope: str) -> tuple[str, str] | None:
    match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*章", scope.lower())
    if not match:
        return None
    chapter = match.group(0).replace(" ", "")
    number = match.group(1)
    chinese_numbers = "一二三四五六七八九十"
    if number in chinese_numbers:
        number = str(chinese_numbers.index(number) + 1)
    return chapter, number


def _file_matches_chapter(file_name: str, chapter_field: str,
                          chapter: str, number: str) -> bool:
    metadata = f"{file_name} {chapter_field}".lower()
    return chapter in metadata.replace(" ", "") or bool(
        re.match(rf"^{re.escape(number)}[.、_-]", metadata)
    )


def _focus_terms(scope: str) -> list[str]:
    simplified = re.sub(
        r"侧重|重点|请|按照|按|关于|以及|和|与|整理|生成|笔记|内容|相关|的|得分点",
        " ", scope.lower(),
    )
    return re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{2,}", simplified)


class ReviewState(TypedDict, total=False):
    run_id: str
    request: dict
    intent: str
    evidence: list[dict]
    plan: dict
    output: dict
    issues: list[str]
    attempts: int
    answers: dict
    assessment: dict
    weak_points: list[str]
    response: dict
    note_input: dict


def citation_issues(output: dict, evidence: list[dict]) -> list[str]:
    lookup = {e["chunk_id"]: e for e in evidence}
    parts = output.get("questions", output.get("points", [output]))
    issues = []
    for part in parts:
        refs = part.get("citations", [])
        if not refs:
            issues.append("缺少引用")
        sources = []
        for ref in refs:
            source = lookup.get(ref.get("chunk_id"))
            quote = ref.get("quote", "").strip()
            if source is None or not quote or quote not in source["content"]:
                issues.append("引用 ID 或原文不匹配")
            else:
                sources.append(source["source_type"])
        if "source_type" in part and sources and part["source_type"] not in sources:
            issues.append("题源标记不匹配引用")
    return issues


class FinalReviewAgent:
    def __init__(self, store, knowledge_base, model, settings: Settings):
        self.store, self.kb, self.model, self.settings = store, knowledge_base, model, settings
        # Bounded stripe locks prevent concurrent mutation of one session in a single worker.
        self.locks = [Lock() for _ in range(64)]
        graph = StateGraph(ReviewState)
        for name, node in {
            "route": self._route,
            "exam_profile": self._profile,
            "note_parse": self._note_parse,
            "note_config": self._note_config,
            "note_generate": self._note_generate,
            "retrieve": self._retrieve,
            "generate": self._generate,
            "verify": self._verify,
            "wait_answers": self._wait,
            "evaluate": self._evaluate,
            "finalize": self._finalize,
            "refuse": self._refuse,
        }.items():
            graph.add_node(name, node)
        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route",
            lambda s: {"quiz": "exam_profile", "note": "note_parse"}.get(s["intent"], "retrieve"),
            ["exam_profile", "note_parse", "retrieve"],
        )
        graph.add_edge("exam_profile", "retrieve")
        graph.add_edge("note_parse", "note_config")
        graph.add_edge("note_config", "note_generate")
        graph.add_edge("note_generate", END)
        graph.add_conditional_edges(
            "retrieve", lambda s: "generate" if s["evidence"] else "refuse", ["generate", "refuse"]
        )
        graph.add_edge("generate", "verify")
        graph.add_conditional_edges(
            "verify", self._after_verify, ["retrieve", "refuse", "wait_answers", "finalize"]
        )
        graph.add_edge("wait_answers", "evaluate")
        graph.add_edge("evaluate", "finalize")
        graph.add_edge("finalize", END)
        graph.add_edge("refuse", END)
        self.graph = graph.compile(checkpointer=SurrealSaver(store))

    def _key(self, course, session):
        if hasattr(self.store, "session_key"):
            return self.store.session_key(course, session)
        return stable_key(course, session)

    def _lock(self, key):
        return self.locks[int(key[:8], 16) % len(self.locks)]

    def _config(self, key):
        return {"configurable": {"thread_id": key}, "recursion_limit": 40}

    def invoke(self, request: AgentRequest, user_id: str | None = None) -> AgentResponse:
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            self._require_not_cancelled(key)
            snapshot = self.graph.get_state(self._config(key))
            if snapshot.next:
                raise SessionConflict("此会话有未完成任务，请补充信息、提交答案或恢复任务")
            incoming = request.model_dump(mode="json")
            incoming["note_input"] = (
                request.note_input.model_dump(mode="json", exclude_unset=True)
                if request.note_input is not None
                else {}
            )
            incoming["owner_id"] = user_id
            if incoming["exam_profile"] is None:
                incoming["exam_profile"] = snapshot.values.get("request", {}).get("exam_profile")
            state = {
                "request": incoming,
                "run_id": uuid4().hex,
                "attempts": 0,
                "evidence": [],
                "plan": {},
                "output": {},
                "issues": [],
                "answers": {},
                "assessment": {},
                "response": {},
                "weak_points": snapshot.values.get("weak_points", []),
                "note_input": {},
            }
            return self._run(key, state)

    def resume_profile(self, request: ResumeRequest):
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            self._pending(key, "exam_profile")
            return self._run(key, Command(resume=request.exam_profile.model_dump(mode="json")))

    def resume_note(self, request: ResumeNoteRequest, user_id: str | None = None):
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            self._require_not_cancelled(key)
            self._pending(key, "note_config")
            snapshot = self.graph.get_state(self._config(key))
            original = snapshot.values["request"]
            if original.get("owner_id") != user_id:
                raise SessionConflict("会话所有者不匹配")
            additions = request.note_input.model_dump(mode="json", exclude_unset=True)
            combined = {**snapshot.values["note_input"], **additions}
            note = NoteInput.model_validate(combined)
            try:
                missing, _ = self._note_boundaries(original, note)
            except ValueError as exc:
                if not str(exc).startswith("所选资料中未找到"):
                    raise
                return AgentResponse(
                    session_id=request.session_id, status="needs_input",
                    prompt={"message": str(exc), "required": ["source_document_ids"]},
                )
            if missing:
                return self._note_prompt(request.session_id, missing)
            return self._run(key, Command(resume=additions))

    def cancel_note(self, course_id: str, session_id: str, user_id: str | None = None):
        key = self._key(course_id, session_id)
        with self._lock(key):
            self._require_not_cancelled(key)
            self._pending(key, "note_config")
            snapshot = self.graph.get_state(self._config(key))
            if snapshot.values["request"].get("owner_id") != user_id:
                raise SessionConflict("会话所有者不匹配")
            existing = self.store.get("review_session", key) or {}
            self.store.put("review_session", key, {
                **existing, "course_id": course_id, "session_id": session_id,
                "cancelled": True,
            })

    def evaluate(self, request: Submission):
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            snapshot = self.graph.get_state(self._config(key))
            if not snapshot.next and snapshot.values.get("assessment"):
                if request.answers == snapshot.values.get("answers"):
                    return AgentResponse.model_validate(snapshot.values["response"])
                raise SessionConflict("本轮已评分；请开启新一轮测评")
            self._pending(key, "wait_answers")
            ids = {q["id"] for q in snapshot.values["output"]["questions"]}
            if set(request.answers) != ids:
                raise ValueError("请提交本轮所有题目的答案，题目 ID 必须完全匹配")
            return self._run(key, Command(resume=request.answers))

    def recover(self, course, session):
        key = self._key(course, session)
        with self._lock(key):
            self._require_not_cancelled(key)
            snapshot = self.graph.get_state(self._config(key))
            if not snapshot.values:
                raise KeyError("会话不存在")
            if any(task.interrupts for task in snapshot.tasks):
                return self._response(snapshot.values, snapshot.tasks)
            if not snapshot.next:
                return AgentResponse.model_validate(snapshot.values["response"])
            return self._run(key, None)

    def read(self, course, session):
        key = self._key(course, session)
        with self._lock(key):
            self._require_not_cancelled(key)
            snapshot = self.graph.get_state(self._config(key))
            if not snapshot.values:
                raise KeyError("会话不存在")
            if snapshot.next and not any(task.interrupts for task in snapshot.tasks):
                raise SessionConflict("任务尚未完成，请使用 recover 恢复")
            return self._response(snapshot.values, snapshot.tasks)

    def _pending(self, key, node):
        snapshot = self.graph.get_state(self._config(key))
        if not snapshot.values:
            raise KeyError("会话不存在")
        if node not in snapshot.next or not any(task.interrupts for task in snapshot.tasks):
            raise SessionConflict("会话当前不接受此操作")

    def _require_not_cancelled(self, key):
        if (self.store.get("review_session", key) or {}).get("cancelled"):
            raise SessionConflict("笔记任务已取消，请重新发起")

    def _run(self, key, payload):
        result = self.graph.invoke(payload, self._config(key))
        response = self._response(result)
        self.store.put(
            "review_session",
            key,
            {
                "course_id": result["request"]["course_id"],
                "session_id": result["request"]["session_id"],
                "response": response.model_dump(mode="json"),
                "weak_points": result.get("weak_points", []),
            },
        )
        return response

    def _response(self, state, tasks=()):
        interruptions = state.get("__interrupt__", [])
        if tasks:
            interruptions = [i for task in tasks for i in task.interrupts]
        if interruptions:
            payload = interruptions[0].value
            return AgentResponse(
                session_id=state["request"]["session_id"],
                status=payload["status"],
                questions=payload.get("questions", []),
                prompt=payload.get("prompt"),
                weak_points=state.get("weak_points", []),
            )
        return AgentResponse.model_validate(state["response"])

    def _route(self, state):
        intent = state["request"]["intent"]
        return {"intent": self.model.route(state["request"]) if intent == "auto" else intent}

    def _profile(self, state):
        request = dict(state["request"])
        if not request.get("exam_profile"):
            profile = interrupt(
                {
                    "status": "needs_input",
                    "prompt": {
                        "message": "请确认考试题型、重点与不考范围",
                        "required": ["question_types"],
                    },
                }
            )
            request["exam_profile"] = ExamProfile.model_validate(profile).model_dump(mode="json")
        return {"request": request}

    def _note_parse(self, state):
        extracted = NoteInput.model_validate(self.model.note_request(state["request"]))
        explicit = state["request"].get("note_input") or {}
        return {"note_input": {**extracted.model_dump(mode="json"), **explicit}}

    def _note_boundaries(self, request, note: NoteInput):
        missing = []
        if note.note_type is None:
            missing.append("note_type")
        if note.duration_minutes is None:
            missing.append("duration_minutes")
        selected = set(note.source_document_ids)
        if len(selected) != len(note.source_document_ids):
            raise ValueError("资料 ID 不可重复")
        if not selected:
            missing.append("source_document_ids")
            return missing, []
        available = set()
        for document in self.store.scan("document", {"course_id": request["course_id"]}):
            if document.get("parse_status") != "ready":
                continue
            owner = request.get("owner_id")
            if owner is not None and document.get("user_id") != owner:
                continue
            if note.source_types and document.get("source_type") not in note.source_types:
                continue
            if document["document_id"] not in selected:
                continue
            available.add(document["document_id"])
        if selected != available:
            raise ValueError("所选资料不存在、不可用或不属于当前课程")
        chapter_request = _chapter_requirement(note.scope)
        if chapter_request:
            chapter, number = chapter_request
            matching = False
            for document_id in note.source_document_ids:
                document = self.store.get("document", document_id)
                if _file_matches_chapter(document.get("file_name") or document["title"],
                                         document.get("chapter", ""), chapter, number):
                    matching = True
                    break
                if any(chapter in chunk["content"].replace(" ", "")
                       for chunk in self.store.list_material_chunks(document_id)):
                    matching = True
                    break
            if not matching:
                raise ValueError(f"所选资料中未找到“{chapter}”的明确内容，请调整资料或写作要求")
        return missing, note.source_document_ids

    @staticmethod
    def _note_prompt(session_id, missing):
        labels = {
            "note_type": "笔记类型（章节笔记、考点清单、问答卡片或口诀）",
            "duration_minutes": "目标阅读时长（分钟）",
            "source_document_ids": "指定资料（请从当前课程选择至少一份可检索资料）",
        }
        return AgentResponse(
            session_id=session_id,
            status="needs_input",
            prompt={
                "message": "请补充：" + "、".join(labels[item] for item in missing),
                "required": missing,
            },
        )

    def _note_config(self, state):
        request = state["request"]
        data = state["note_input"]
        note = NoteInput.model_validate(data)
        missing, sources = self._note_boundaries(request, note)
        if missing:
            additions = interrupt(
                self._note_prompt(request["session_id"], missing).model_dump(
                    mode="json", exclude_none=True
                )
            )
            note = NoteInput.model_validate({**data, **additions})
            missing, sources = self._note_boundaries(request, note)
            if missing:
                raise ValueError("仍缺少笔记生成所需信息")
        config = note.model_dump(mode="json")
        config["audience_level"] = config["audience_level"] or "intermediate"
        config["source_document_ids"] = sources
        response = AgentResponse(
            session_id=request["session_id"], status="configured", note_config=config
        )
        return {"note_input": config, "response": response.model_dump(mode="json")}

    def _note_generate(self, state):
        request, config = state["request"], state["note_input"]
        grouped = {}
        for document_id in config["source_document_ids"]:
            document = self.store.get("document", document_id)
            if (
                document is None
                or document.get("user_id") != request.get("owner_id")
                or document.get("course_id") != request["course_id"]
                or document.get("parse_status") != "ready"
            ):
                raise ValueError("所选资料已不可用")
            grouped[document_id] = []
            for chunk in self.store.list_material_chunks(document_id):
                grouped[document_id].append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "document_id": document_id,
                        "title": document["title"],
                        "file_name": document.get("file_name") or document["title"],
                        "source_type": document["source_type"],
                        "content": chunk["content"],
                        "position_kind": chunk.get("position_kind", "document"),
                        "position": chunk.get("position"),
                        "chapter": document.get("chapter", ""),
                        "ordinal": chunk.get("chunk_ordinal", 0),
                    }
                )
        scope = config["scope"].strip().lower()
        chapter_request = _chapter_requirement(scope)
        if chapter_request:
            chapter, chapter_number = chapter_request
            matched = {}
            for document_id, items in grouped.items():
                if not items:
                    continue
                file_matches = _file_matches_chapter(
                    items[0]["file_name"], items[0]["chapter"], chapter, chapter_number
                )
                relevant = items if file_matches else [
                    item for item in items if chapter in item["content"].replace(" ", "")
                ]
                if relevant:
                    matched[document_id] = relevant
            if not matched:
                raise ValueError(f"所选资料中未找到“{chapter}”的明确内容，请调整资料或写作要求")
            grouped = matched
        terms = _focus_terms(scope)
        for items in grouped.values():
            items.sort(key=lambda item: (
                -sum(term in item["content"].lower() for term in terms),
                item["ordinal"],
            ))
        evidence = []
        while len(evidence) < 40 and any(grouped.values()):
            for items in grouped.values():
                if items and len(evidence) < 40:
                    evidence.append(items.pop(0))
        if not evidence:
            return {"response": self._note_refusal(request["session_id"])}
        lookup = {item["chunk_id"]: item for item in evidence}
        issues = []
        for _ in range(self.settings.max_repairs + 1):
            generated = GeneratedNote.model_validate(
                self.model.note(
                    {
                        "note_config": config,
                        "evidence": evidence,
                        "issues_to_fix": issues,
                    }
                )
            )
            points, issues = [], []
            for index, point in enumerate(generated.points, 1):
                refs = []
                for citation in point.citations:
                    source = lookup.get(citation.chunk_id)
                    if source is None or citation.quote not in source["content"]:
                        issues.append(f"第 {index} 条来源片段或摘录不匹配")
                        continue
                    refs.append(
                        {
                            "chunk_id": citation.chunk_id,
                            "document_id": source["document_id"],
                            "quote": citation.quote,
                            "file_name": source["file_name"],
                            "source_type": source["source_type"],
                        }
                    )
                distinct = {ref["document_id"] for ref in refs}
                if point.provenance == "ai_supplement" and refs:
                    issues.append(f"第 {index} 条 AI 补充不得带资料引用")
                if point.provenance == "source" and len(distinct) != 1:
                    issues.append(f"第 {index} 条单来源考点须引用一份资料")
                if point.provenance == "synthesis" and len(distinct) < 2:
                    issues.append(f"第 {index} 条综合改编须引用至少两份资料")
                points.append(
                    {
                        "point_id": f"p{index}",
                        "heading": point.heading,
                        "content": point.content,
                        "provenance": point.provenance,
                        "references": refs,
                    }
                )
            if not any(point["references"] for point in points):
                issues.append("整份笔记没有可验证的资料来源")
            if not issues:
                verdict = self.model.verify(
                    {
                        "request": request,
                        "output": {"points": [point for point in points if point["references"]]},
                        "evidence": evidence,
                    }
                )
                if not verdict["supported"]:
                    issues = verdict["issues"] or ["考点未通过语义证据校验"]
            if not issues:
                break
        if issues:
            return {"response": self._note_refusal(request["session_id"])}
        lines = [f"# {generated.title}"]
        for point in points:
            label = {"source": "资料来源", "synthesis": "综合改编", "ai_supplement": "AI 补充"}[
                point["provenance"]
            ]
            lines.extend(["", f"## {point['heading']}", "", point["content"], "", f"来源：{label}"])
        created = DomainService(self.store, request["owner_id"]).create_note_draft(
            request["course_id"],
            {
                "title": generated.title,
                "markdown": "\n".join(lines),
                "note_type": config["note_type"],
                "points": points,
            },
            state["run_id"],
        )
        asset, revision = created["asset"], created["revision"]
        response = AgentResponse(
            session_id=request["session_id"],
            status="completed",
            answer="笔记草稿已生成，请打开预览。",
            draft={
                "asset_id": asset["asset_id"],
                "revision_id": revision["revision_id"],
                "title": asset["title"],
                "note_type": config["note_type"],
                "url": f"#note/{asset['asset_id']}/{revision['revision_id']}",
            },
        )
        return {"response": response.model_dump(mode="json")}

    @staticmethod
    def _note_refusal(session_id):
        return AgentResponse(
            session_id=session_id,
            status="insufficient_evidence",
            answer="现有资料不足，或笔记内容未通过来源校验。请补充相关资料后重试。",
        ).model_dump(mode="json")

    def _retrieve(self, state):
        evidence = self.model.retrieve(state["request"], self.kb, broaden=state["attempts"] > 0)
        return {"evidence": evidence}

    def _generate(self, state):
        data = {
            "request": state["request"],
            "evidence": state["evidence"],
            "issues_to_fix": state["issues"],
            "weak_points": state.get("weak_points", []),
        }
        if state["intent"] == "ask":
            output = GroundedAnswer.model_validate(self.model.answer(data)).model_dump(mode="json")
            return {"output": output}
        plan = KnowledgePlan.model_validate(self.model.plan(data)).model_dump(mode="json")
        output = Quiz.model_validate(self.model.quiz({**data, "plan": plan})).model_dump(
            mode="json"
        )
        for i, question in enumerate(output["questions"], start=1):
            question["id"] = f"{state['run_id'][:12]}-q{i}"
            refs = {c["chunk_id"] for c in question["citations"]}
            types = [e["source_type"] for e in state["evidence"] if e["chunk_id"] in refs]
            if types:
                question["source_type"] = max(types, key=lambda t: SOURCE_PRIORITY[t])
        return {"plan": plan, "output": output}

    def _verify(self, state):
        issues = citation_issues(state["output"], state["evidence"])
        if state["intent"] == "quiz":
            issues += citation_issues(state["plan"], state["evidence"])
            points = state["plan"]["points"]
            allowed = set(state["request"]["exam_profile"]["question_types"])
            if len({p["name"] for p in points}) != len(points):
                issues.append("知识点名称重复")
            counts = Counter(q["knowledge_point"] for q in state["output"]["questions"])
            if counts != Counter({p["name"]: p["question_count"] for p in points}):
                issues.append("知识点题量与计划不一致")
            planned = {p["name"]: set(p["question_types"]) for p in points}
            for p in points:
                if not set(p["question_types"]) <= allowed:
                    issues.append("计划使用了非考试题型")
            for q in state["output"]["questions"]:
                if q["question_type"] not in allowed & planned.get(q["knowledge_point"], set()):
                    issues.append("题型不符合考试或知识点计划")
                if q["question_type"] == "choice" and len(q["options"]) < 2:
                    issues.append("选择题缺少选项")
        if not issues:
            verdict = self.model.verify(
                {
                    "request": state["request"],
                    "output": state["output"],
                    "evidence": state["evidence"],
                }
            )
            if not verdict["supported"]:
                issues.extend(verdict["issues"] or ["语义证据校验失败"])
        return {"issues": issues, "attempts": state["attempts"] + 1}

    def _after_verify(self, state):
        if state["issues"]:
            return "retrieve" if state["attempts"] <= self.settings.max_repairs else "refuse"
        return "wait_answers" if state["intent"] == "quiz" else "finalize"

    def _wait(self, state):
        questions = [
            {
                k: q[k]
                for k in (
                    "id",
                    "knowledge_point",
                    "question_type",
                    "stem",
                    "options",
                    "source_type",
                )
            }
            for q in state["output"]["questions"]
        ]
        answers = interrupt({"status": "awaiting_answers", "questions": questions})
        return {"answers": answers}

    def _evaluate(self, state):
        items = Grades.model_validate(
            self.model.grade(
                {
                    "quiz": state["output"],
                    "answers": state["answers"],
                }
            )
        ).model_dump(mode="json")["items"]
        expected = {q["id"] for q in state["output"]["questions"]}
        if len(items) != len(expected) or {g["question_id"] for g in items} != expected:
            raise ModelError("评分结果题目 ID 不匹配")
        scores = defaultdict(list)
        lookup = {q["id"]: q["knowledge_point"] for q in state["output"]["questions"]}
        for grade in items:
            scores[lookup[grade["question_id"]]].append(grade["score"])
        weak = set(state.get("weak_points", []))
        for point, values in scores.items():
            if sum(values) / len(values) < 60:
                weak.add(point)
            else:
                weak.discard(point)
        return {
            "assessment": {
                "score": round(sum(g["score"] for g in items) / len(items), 2),
                "items": items,
                "reference_questions": state["output"]["questions"],
            },
            "weak_points": sorted(weak),
        }

    def _finalize(self, state):
        parts = state["output"].get("questions", [state["output"]])
        cited = {ref["chunk_id"] for part in parts for ref in part.get("citations", [])}
        response = AgentResponse(
            session_id=state["request"]["session_id"],
            status="completed",
            answer=state["output"].get("answer", ""),
            citations=[e for e in state["evidence"] if e["chunk_id"] in cited],
            assessment=state.get("assessment") or None,
            weak_points=state.get("weak_points", []),
            suggestions=[
                f"优先复习「{point}」的定义与失分步骤，再做同类题。"
                for point in state.get("weak_points", [])
            ],
        )
        if state.get("plan"):
            key = self._key(state["request"]["course_id"], state["request"]["session_id"])
            self.store.put(
                "knowledge_point",
                key,
                {
                    "course_id": state["request"]["course_id"],
                    **state["plan"],
                },
            )
        return {"response": response.model_dump(mode="json")}

    def _refuse(self, state):
        return {
            "response": AgentResponse(
                session_id=state["request"]["session_id"],
                status="insufficient_evidence",
                answer="现有资料不足，或生成内容未通过证据校验。请补充相关课程资料后重试。",
                weak_points=state.get("weak_points", []),
            ).model_dump(mode="json")
        }
