"""Formal quiz generation contract; no revision writes or model grading here."""

from collections import Counter
from decimal import Decimal
from typing import Annotated

from pydantic import Field, ValidationError, model_validator

from .domain import DomainConflict, DomainNotFound, DomainService
from .policy import SOURCE_LABELS
from .quiz_answers import validate_extended_key
from .schemas import Citation, Evidence, FormalQuestionType, Model, ResolvedQuizConfig, Text
from .source_locators import material_version


class QuizEvidence(Evidence):
    user_id: str
    material_version_id: str


class QuizGenerationContext(Model):
    config: ResolvedQuizConfig
    evidence: list[QuizEvidence] = Field(min_length=1, max_length=1000)
    prompt_version: str = "quiz-draft-v3-plan-recovery"
    question_slots: list[dict] = Field(default_factory=list, max_length=100)
    previous_questions: list[dict] = Field(default_factory=list, max_length=100)
    repair_notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_evidence(self):
        ids = [item.chunk_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("证据片段 ID 重复")
        for item in self.evidence:
            if (
                item.user_id != self.config.user_id
                or item.course_id != self.config.course_id
                or item.document_id not in self.config.material_versions
                or item.material_version_id != self.config.material_versions[item.document_id]
                or (self.config.source_types and item.source_type not in self.config.source_types)
                or item.source_type == "ai_supplement"
            ):
                raise ValueError("证据超出配置范围或资料版本不匹配")
        return self


def build_quiz_context(store, config: ResolvedQuizConfig, evidence: list[dict]):
    """Bind retrieved evidence to authoritative stored chunks and current snapshots."""
    from .course_chat import CourseMaterials

    domain = DomainService(store, config.user_id)
    domain.course(config.course_id, writable=True)
    if config.exam_id:
        exam = domain.exam(config.exam_id)
        if exam["course_id"] != config.course_id:
            raise DomainNotFound(config.exam_id)
        if exam.get("status") != "active" or exam.get("updated_at") != config.exam_updated_at:
            raise DomainConflict("考试配置已变更，请重新确认试卷配置")
    documents = CourseMaterials(store, config.course_id, config.user_id).select(
        config.source_document_ids, config.chapter
    )
    if {d["document_id"] for d in documents} != set(config.source_document_ids):
        raise DomainConflict("资料章节范围已变更，请重新确认配置")
    chunks = {}
    for document in documents:
        if document.get("user_id") != config.user_id:
            raise DomainNotFound(document["document_id"])
        version = material_version(document)["material_version_id"]
        if version != config.material_versions[document["document_id"]]:
            raise DomainConflict("资料版本已变更，请重新确认试卷配置")
        for chunk in document.get(
            "_chat_chunks", store.list_material_chunks(document["document_id"])
        ):
            if (
                chunk.get("user_id", config.user_id) != config.user_id
                or chunk.get("course_id") != config.course_id
            ):
                continue
            authoritative = CourseMaterials.evidence(chunk, document).model_dump(mode="json")
            chunks[chunk["chunk_id"]] = {
                **authoritative,
                "user_id": config.user_id,
                "material_version_id": version,
            }
    selected = []
    for supplied in evidence:
        actual = chunks.get(supplied["chunk_id"])
        if actual is None or supplied["content"] != actual["content"]:
            raise DomainConflict("检索证据不属于本次范围或原文已变更")
        selected.append(actual)
    return QuizGenerationContext(config=config, evidence=selected)


class QuizPlanItem(Model):
    knowledge_point: Text
    question_type: FormalQuestionType
    question_count: int = Field(ge=1, le=100, strict=True)
    importance: int = Field(ge=1, le=5, strict=True)
    allocation_reason: Text
    document_ids: list[str] = Field(default_factory=list, max_length=100)


class QuizDraftPlan(Model):
    allocations: list[QuizPlanItem] = Field(min_length=1, max_length=100)


class QuizDraftQuestion(Model):
    id: Text
    order: int = Field(ge=1, le=100, strict=True)
    knowledge_point: Text
    question_type: FormalQuestionType
    stem: Text
    options: list[Text] = Field(default_factory=list, max_length=8)
    correct_option: int | None = Field(default=None, ge=0, le=7, strict=True)
    correct_options: list[Annotated[int, Field(ge=0, le=7, strict=True)]] = Field(
        default_factory=list, max_length=8
    )
    boolean_answer: bool | None = Field(default=None, strict=True)
    accepted_answers: list[Text] = Field(default_factory=list, max_length=30)
    blank_answers: list[list[Text]] = Field(default_factory=list, max_length=30)
    score: float = Field(gt=0, le=10000, allow_inf_nan=False)
    reference_answer: Text
    explanation: Text
    core_point: Text
    must_include: list[Text] = Field(min_length=1, max_length=30)
    common_mistakes: list[Text] = Field(min_length=1, max_length=30)
    scoring_tips: Text
    provenance: str = Field(pattern=r"^(source|synthesis|ai_supplement)$")
    citations: list[Citation] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def check_extended_keys(self):
        validate_extended_key(self.model_dump())
        return self


class QuizDraftPayload(Model):
    title: Text
    questions: list[QuizDraftQuestion] = Field(min_length=1, max_length=100)


class QuizValidationIssue(Model):
    code: str
    path: str
    message: str


class QuizValidationResult(Model):
    valid: bool
    issues: list[QuizValidationIssue]
    validated_draft: dict | None = None


class QuizQuestionReview(Model):
    question_id: Text
    supported: bool = Field(strict=True)
    issues: list[Text] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def consistent_verdict(self):
        if self.supported == bool(self.issues):
            raise ValueError("通过时不能有问题；未通过时必须说明问题")
        return self


class QuizSemanticReview(Model):
    items: list[QuizQuestionReview] = Field(min_length=1, max_length=100)


class QuizDraftQuestionReview(QuizQuestionReview):
    plan_evidence_insufficient: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def check_plan_verdict(self):
        if self.supported and self.plan_evidence_insufficient:
            raise ValueError("通过的题目不能同时声明计划证据不足")
        return self


class QuizDraftSemanticReview(Model):
    items: list[QuizDraftQuestionReview] = Field(min_length=1, max_length=100)


class QuizPlanEvidenceReview(Model):
    supported: bool = Field(strict=True)
    reason: Text
    citations: list[Citation] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def require_support(self):
        if self.supported and not self.citations:
            raise ValueError("确认知识点有证据时必须给出原文引用")
        return self


class QuizPlanPointReplacement(Model):
    knowledge_point: Text
    document_ids: list[Text] = Field(min_length=1, max_length=100)
    allocation_reason: Text
    citations: list[Citation] = Field(min_length=1, max_length=30)


class QuizPaperReview(Model):
    supported: bool = Field(strict=True)
    issues: list[Text] = Field(default_factory=list, max_length=20)
    affected_question_ids: list[Text] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def consistent_verdict(self):
        if self.supported == bool(self.issues):
            raise ValueError("整卷通过时不能有问题；未通过时必须说明问题")
        if self.supported and self.affected_question_ids:
            raise ValueError("整卷通过时不能标记待修复题目")
        return self


def _covering_quote(content: str, quotes: list[str]) -> str | None:
    """Join same-chunk quotes when one continuous excerpt of at most 1500 characters covers them."""
    if len(quotes) < 2 or any(quote not in content for quote in quotes):
        return None
    ranges = []
    for quote in quotes:
        start = content.find(quote)
        ranges.append((start, start + len(quote)))
    start = min(item[0] for item in ranges)
    end = max(item[1] for item in ranges)
    span = content[start:end]
    if len(span) > 1500 or any(quote not in span for quote in quotes):
        return None
    return span


def _merge_same_chunk_citations(draft, evidence) -> None:
    lookup = {item.chunk_id: item.content for item in evidence}
    for question in draft.questions:
        grouped: dict[str, list] = {}
        order: list[str] = []
        for ref in question.citations:
            grouped.setdefault(ref.chunk_id, [])
            if not grouped[ref.chunk_id]:
                order.append(ref.chunk_id)
            grouped[ref.chunk_id].append(ref)
        merged = []
        for chunk_id in order:
            refs = grouped[chunk_id]
            content = lookup.get(chunk_id)
            span = _covering_quote(content, [ref.quote for ref in refs]) if content else None
            if span is None:
                merged.extend(refs)
            else:
                merged.append(refs[0].model_copy(update={"quote": span}))
        question.citations = merged


def validate_quiz_plan(context: QuizGenerationContext, value: dict) -> list[dict]:
    """Check allocation before spending a model call on the candidate paper."""
    try:
        plan = QuizDraftPlan.model_validate(value)
        """ 表示尝试把 DeepSeek 返回的字典数据验证并转换成 QuizDraftPlan 类型的对象。 """
    except ValidationError:
        return [{"code": "plan_schema", "message": "出题计划结构无效"}]
        """ 如果数据格式不符合规定，就可能抛出 ValidationError。 """
    config = context.config
    """ 就是从 context 对象中取出 config 属性，并赋值给一个局部变量。 """
    expected = Counter({i.question_type: i.question_count for i in config.blueprint})

    actual, points, slots = Counter(), Counter(), set()
    issues = []
    for item in plan.allocations:
        actual[item.question_type] += item.question_count
        points[item.knowledge_point] += item.question_count
        key = (item.knowledge_point, item.question_type)
        if key in slots or item.knowledge_point in config.excluded_topics:
            """判断：这个知识点和题型的组合是否已经分配过？
            和 判断：这个知识点是否属于用户明确排除的内容？
            """
            issues.append({"code": "allocation", "message": "分配重复或包含排除项"})
        slots.add(key)
    if actual != expected:
        issues.append({"code": "plan_count", "message": "计划题型数量不符合配置"})
    if any(count > 6 for count in points.values()):
        issues.append({"code": "knowledge_count", "message": "每个知识点最多 6 题"})
    if config.scope_mode == "knowledge_points" and set(points) != set(config.knowledge_points):
        """表示用户选择的是“按指定知识点出题”的模式。
        和 表示实际出题知识点集合与用户指定的知识点集合不相同。
        """
        issues.append({"code": "knowledge_scope", "message": "计划未覆盖所选知识点"})
    known = set(config.source_document_ids)
    """ 这里用集合主要是为了方便判断某个资料 ID 是否存在。 """
    if any(doc and doc not in known for item in plan.allocations for doc in item.document_ids):
        issues.append({"code": "source_coverage", "message": "计划包含未选资料"})
    return issues


def validate_quiz_draft(context: QuizGenerationContext, plan: dict, payload: dict):
    """Deterministic checks precede M3-02's semantic verification and publication."""
    issues = []

    def fail(code, path, message):
        issues.append(QuizValidationIssue(code=code, path=path, message=message))

    parsed = []
    for schema, value, name in (
        (QuizDraftPlan, plan, "plan"),
        (QuizDraftPayload, payload, "draft"),
    ):
        try:
            parsed.append(schema.model_validate(value))
        except ValidationError as exc:
            for error in exc.errors():
                fail("schema", name + "." + ".".join(map(str, error["loc"])), error["msg"])
    if issues:
        return QuizValidationResult(valid=False, issues=issues)
    plan, draft = parsed
    _merge_same_chunk_citations(draft, context.evidence)
    config = context.config
    expected = Counter({item.question_type: item.question_count for item in config.blueprint})
    planned = Counter()
    slots = Counter()
    for index, item in enumerate(plan.allocations):
        key = (item.knowledge_point, item.question_type)
        if key in slots:
            fail("duplicate_allocation", f"plan.allocations.{index}", "知识点/题型分配重复")
        slots[key] += item.question_count
        planned[item.question_type] += item.question_count
        if item.knowledge_point in config.excluded_topics:
            fail("excluded_topic", f"plan.allocations.{index}", "计划包含排除知识点")
    if planned != expected:
        fail("plan_count", "plan.allocations", "计划的题型数量与蓝图不一致")
    point_counts = Counter()
    for item in plan.allocations:
        point_counts[item.knowledge_point] += item.question_count
    if any(count > 6 for count in point_counts.values()):
        fail("knowledge_count", "plan.allocations", "每个知识点最多分配 6 题")
    if config.scope_mode == "knowledge_points" and (
        {item.knowledge_point for item in plan.allocations} != set(config.knowledge_points)
    ):
        fail("knowledge_scope", "plan.allocations", "计划未严格覆盖所选知识点")
    if Counter(q.question_type for q in draft.questions) != expected:
        fail("question_count", "draft.questions", "题型或数量与蓝图不一致")
    if Counter((q.knowledge_point, q.question_type) for q in draft.questions) != slots:
        fail("allocation_count", "draft.questions", "题目与知识点分配计划不一致")
        for index, (question, slot) in enumerate(zip(draft.questions, context.question_slots)):
            if (question.knowledge_point, question.question_type) != (
                slot["knowledge_point"],
                slot["question_type"],
            ):
                fail(
                    "slot_allocation",
                    f"draft.questions.{index}.knowledge_point",
                    f"题目 {question.id} 必须保持计划知识点“{slot['knowledge_point']}”及题型"
                    f" {slot['question_type']}；实际为“{question.knowledge_point}”及题型"
                    f" {question.question_type}。请按原计划修题，不要自行更换知识点。",
                )
    if len({q.id for q in draft.questions}) != len(draft.questions):
        fail("duplicate_id", "draft.questions", "题目 ID 重复")
    if [q.order for q in draft.questions] != list(range(1, len(draft.questions) + 1)):
        fail("order", "draft.questions", "题目顺序必须从 1 连续排列")
    stems = ["".join(q.stem.split()) for q in draft.questions]
    if len(set(stems)) != len(stems):
        fail("duplicate_stem", "draft.questions", "题干重复")
    if config.total_score is not None and (
        sum(Decimal(str(q.score)) for q in draft.questions) != Decimal(str(config.total_score))
    ):
        fail("total_score", "draft.questions", "题目分值合计与指定总分不一致")
    lookup = {item.chunk_id: item for item in context.evidence}
    canonical = draft.model_dump(mode="json")
    for index, question in enumerate(draft.questions):
        path = f"draft.questions.{index}"
        if question.question_type == "choice":
            if (
                len(question.options) < 2
                or len(set(question.options)) != len(question.options)
                or question.correct_option is None
                or question.correct_option >= len(question.options)
            ):
                fail("choice_structure", path, "选择题需要不同选项及有效正确选项索引")
        elif question.question_type != "multiple_choice" and (
            question.options or question.correct_option is not None
        ):
            fail("options", path, "非选择题不能包含选择题选项或答案索引")
        if question.question_type == "true_false" and question.boolean_answer is None:
            fail("boolean_answer", path, "判断题需要布尔答案")
        if question.question_type != "true_false" and question.boolean_answer is not None:
            fail("boolean_answer", path, "非判断题不能包含布尔答案")
        if question.question_type == "fill_blank" and not (
            question.accepted_answers or question.blank_answers
        ):
            fail("accepted_answers", path, "填空题需要可接受答案")
        if question.question_type != "fill_blank" and question.accepted_answers:
            fail("accepted_answers", path, "非填空题不能包含填空答案列表")
        refs = []
        for ref in question.citations:
            item = lookup.get(ref.chunk_id)
            if item is None or ref.quote not in item.content:
                fail(
                    "citation",
                    path + ".citations",
                    f"片段 {ref.chunk_id!r} 不存在或找不到逐字摘录 {ref.quote!r}；"
                    "请复制 evidence 的 chunk_id，并从对应 content 复制连续原文，保持空格和标点。",
                )
            else:
                refs.append(item)
        if len({ref.chunk_id for ref in question.citations}) != len(question.citations):
            fail(
                "duplicate_citation",
                path,
                "同一片段被引用了两次，且这些摘录无法合并成该片段中的一段连续原文",
            )
        documents = {item.document_id for item in refs}
        if question.provenance == "ai_supplement":
            if not config.allow_ai_supplement or question.citations:
                fail("ai_policy", path, "AI 补充未获允许或伪装为资料题")
            label = "AI补充"
        else:
            count = len(documents)
            if (question.provenance == "source" and count != 1) or (
                question.provenance == "synthesis" and count < 2
            ):
                fail(
                    "provenance",
                    path,
                    f"本题实际引用 {count} 份不同文件，provenance={question.provenance} 不匹配。"
                    "source只用于恰好一份文件；引用两份及以上文件必须设synthesis。"
                    "文件数按document_id计算，不按chunk数量或知识点数量计算。",
                )
            labels = sorted({SOURCE_LABELS[item.source_type] for item in refs})
            label = (
                "综合改编（" + " + ".join(labels) + "）"
                if question.provenance == "synthesis"
                else "、".join(labels)
            )
        canonical["questions"][index].update(
            source_label=label,
            source_types=sorted({item.source_type.value for item in refs}),
            references=[
                {
                    "chunk_id": ref.chunk_id,
                    "quote": ref.quote,
                    "document_id": lookup[ref.chunk_id].document_id,
                    "material_version_id": lookup[ref.chunk_id].material_version_id,
                    "file_name": lookup[ref.chunk_id].file_name,
                    "source_type": lookup[ref.chunk_id].source_type.value,
                    "position_kind": lookup[ref.chunk_id].position_kind,
                    "position": lookup[ref.chunk_id].position,
                }
                for ref in question.citations
                if ref.chunk_id in lookup
            ],
        )
    return QuizValidationResult(
        valid=not issues, issues=issues, validated_draft=canonical if not issues else None
    )
