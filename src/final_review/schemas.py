from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=20000)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceType(StrEnum):
    past_exam = "past_exam"
    teacher_ppt = "teacher_ppt"
    homework = "homework"
    other_practice = "other_practice"
    external_upload = "external_upload"
    crash_course = "crash_course"
    ai_supplement = "ai_supplement"


QuestionType = Literal["choice", "fill_blank", "true_false", "short_answer", "calculation", "proof"]


class MaterialInput(Model):
    document_id: Identifier | None = None
    course_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    source_type: SourceType
    chapter: Annotated[str, Field(max_length=200)] = ""
    markdown: Annotated[str, Field(min_length=1, max_length=500000)]


class CourseCreate(Model):
    name: Annotated[str, Field(min_length=1, max_length=100)]


class CourseUpdate(Model):
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    subject: Annotated[str, Field(max_length=100)] | None = None
    expected_updated_at: str


class ExamBlueprintItem(Model):
    question_type: QuestionType
    question_count: int = Field(ge=1, le=100)
    score: float = Field(gt=0, le=10000)


class ExamCreate(Model):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    exam_at: str | None = None
    total_score: float | None = Field(default=None, gt=0, le=10000)
    blueprint: list[ExamBlueprintItem] = Field(default_factory=list, max_length=6)
    emphasis: list[str] = Field(default_factory=list, max_length=30)
    exclusions: list[str] = Field(default_factory=list, max_length=30)
    notes: Annotated[str, Field(max_length=4000)] = ""
    generation_preferences: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ExamUpdate(ExamCreate):
    expected_updated_at: str


class AssetCreate(Model):
    asset_type: Literal["note", "quiz"]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    markdown: Annotated[str, Field(min_length=1, max_length=100000)]
    source_document_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class AssetRevisionCreate(Model):
    base_revision_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    markdown: Annotated[str, Field(min_length=1, max_length=100000)]
    source_document_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class NoteReferenceEdit(Model):
    document_id: Identifier
    chunk_id: Identifier
    quote: Annotated[str, Field(min_length=1, max_length=10000)]


class NotePointEdit(Model):
    point_id: Identifier
    heading: Annotated[str, Field(min_length=1, max_length=200)]
    content: Annotated[str, Field(min_length=1, max_length=50000)]
    provenance: Literal["source", "synthesis", "ai_supplement"]
    # Generation merges references/content from up to twelve reading batches.
    references: list[NoteReferenceEdit] = Field(default_factory=list, max_length=128)


class NoteRevisionEdit(Model):
    base_revision_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    points: list[NotePointEdit] = Field(min_length=1, max_length=100)


class NoteConfirmPreview(Model):
    revision_id: Identifier


class NoteConfirm(NoteConfirmPreview):
    confirmation_id: Identifier


class ConfirmationConsume(Model):
    confirmation_id: Identifier


class MaterialDelete(Model):
    confirmation_id: Identifier
    mode: Literal["block", "retain_source_snapshot"] = "block"


class MaterialUpdate(Model):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    chapter: Annotated[str, Field(max_length=200)] = ""
    source_type: SourceType
    expected_updated_at: str | None = None


class ConversationCreate(Model):
    title: Annotated[str, Field(min_length=1, max_length=100)]


class ConversationRename(Model):
    title: Annotated[str, Field(min_length=1, max_length=100)]


class Credentials(Model):
    email: Annotated[str, Field(min_length=3, max_length=320)]
    password: Annotated[str, Field(min_length=8, max_length=128)]


class ExamProfile(Model):
    question_types: list[QuestionType] = Field(min_length=1, max_length=6)
    emphasis: list[str] = Field(default_factory=list, max_length=30)
    excluded_topics: list[str] = Field(default_factory=list, max_length=30)


NoteType = Literal["chapter", "key_points", "qa_cards", "mnemonic"]
AudienceLevel = Literal["beginner", "intermediate", "advanced"]


class NoteInput(Model):
    chapter: Annotated[str, Field(max_length=200)] = ""
    note_type: NoteType | None = None
    scope: Annotated[str, Field(max_length=1000)] = ""
    duration_minutes: int | None = Field(default=None, ge=1, le=240)
    emphasis: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )
    audience_level: AudienceLevel | None = None
    source_types: list[SourceType] = Field(default_factory=list, max_length=6)
    source_document_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class AgentRequest(Model):
    course_id: Identifier
    session_id: Identifier
    message: Annotated[str, Field(min_length=1, max_length=4000)]
    intent: Literal["auto", "ask", "quiz", "note"] = "auto"
    chapter: Annotated[str, Field(max_length=200)] = ""
    exam_profile: ExamProfile | None = None
    note_input: NoteInput | None = None


class ChatMessage(Model):
    role: Literal["user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=4000)]


class ChatRequest(Model):
    message: Text
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    course_id: Identifier = "software-engineering-basics"
    conversation_id: Identifier = "default"
    model_id: Identifier | None = None
    attachment_document_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    mode: Literal["auto", "direct"] = "auto"
    source_document_ids: list[Identifier] | None = Field(default=None, max_length=100)
    chapter: Annotated[str, Field(max_length=200)] = ""
    materials_only: bool = False
    retrieval_action: Literal["general", "overview", "search", "read"] = "search"
    retrieval_query: Annotated[str, Field(max_length=2000)] = ""
    overview_kind: Literal["course", "statistics", "list", "all"] = "all"


class ChatDecision(Model):
    intent: Literal["note", "quiz", "ask", "clarify"] = "ask"
    action: Literal["general", "overview", "search", "read"] = "search"
    overview_kind: Literal["course", "statistics", "list", "all"] = "all"
    query: Annotated[str, Field(max_length=2000)] = ""
    source_document_ids: list[Identifier] | None = Field(default=None, max_length=100)
    chapter: Annotated[str, Field(max_length=200)] | None = None
    materials_only: bool | None = None
    clarification: Annotated[str, Field(max_length=500)] = ""
    task_message: Annotated[str, Field(max_length=4000)] = ""
    question_count: int = Field(default=5, ge=1, le=10)
    question_types: list[QuestionType] = Field(default_factory=lambda: ["short_answer"],
                                              min_length=1, max_length=6)
    random: bool = True


class ChatResponse(Model):
    reply: Text
    model: str
    citations: list["Evidence"] = Field(default_factory=list)


class ResumeRequest(Model):
    course_id: Identifier
    session_id: Identifier
    exam_profile: ExamProfile


class ResumeNoteRequest(Model):
    course_id: Identifier
    session_id: Identifier
    note_input: NoteInput = Field(default_factory=NoteInput)


class CancelNoteRequest(Model):
    course_id: Identifier
    session_id: Identifier


class Submission(Model):
    course_id: Identifier
    session_id: Identifier
    answers: dict[str, Text] = Field(min_length=1, max_length=36)


class FastQuizRequest(Model):
    course_id: Identifier
    chapter: Annotated[str, Field(max_length=200)] = ""
    question_types: list[QuestionType] = Field(min_length=1, max_length=6)
    question_count: int = Field(default=5, ge=1, le=10)
    model_id: Identifier | None = None
    source_document_ids: list[Identifier] | None = Field(default=None, max_length=100)
    query: Annotated[str, Field(max_length=2000)] = ""
    random: bool = True


class FastQuizQuestion(Model):
    id: str
    knowledge_point: Annotated[str, Field(min_length=1, max_length=200)]
    question_type: QuestionType
    stem: Text
    options: list[str] = Field(default_factory=list, max_length=8)
    reference_answer: Text
    explanation: Text
    must_include: list[str] = Field(default_factory=list, max_length=8)
    source_chunk_ids: list[str] = Field(min_length=1, max_length=3)


class FastQuiz(Model):
    questions: list[FastQuizQuestion] = Field(min_length=1, max_length=10)


class FastQuizSubmission(Model):
    course_id: Identifier
    answers: dict[str, Text] = Field(min_length=1, max_length=10)


class Evidence(Model):
    chunk_id: str
    chunk_ordinal: int = 0
    document_id: str
    title: str
    file_name: str = ""
    citation_number: int = 0
    course_id: str
    chapter: str
    source_type: SourceType
    content: str
    position_kind: Literal["document", "page", "slide"] = "document"
    position: int | None = None
    text_start: int | None = None
    text_end: int | None = None
    similarity: float
    rank_score: float = 0


class Citation(Model):
    chunk_id: str
    quote: Annotated[str, Field(min_length=1, max_length=1500)]


class GroundedAnswer(Model):
    answer: Text
    citations: list[Citation] = Field(min_length=1, max_length=10)


class KnowledgePoint(Model):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Text
    importance: Literal[1, 2, 3, 4, 5]
    question_count: int = Field(ge=1, le=6)
    question_types: list[QuestionType] = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)


class KnowledgePlan(Model):
    points: list[KnowledgePoint] = Field(min_length=1, max_length=6)


class Question(Model):
    id: str
    knowledge_point: str
    question_type: QuestionType
    stem: Text
    options: list[str] = Field(default_factory=list, max_length=8)
    reference_answer: Text
    core_point: Text
    must_include: list[str] = Field(min_length=1)
    common_mistakes: list[str] = Field(min_length=1)
    scoring_tips: Text
    source_type: SourceType
    citations: list[Citation] = Field(min_length=1)


class Quiz(Model):
    questions: list[Question] = Field(min_length=1, max_length=36)


class Grade(Model):
    question_id: str
    score: float = Field(ge=0, le=100)
    error_type: str
    feedback: Text
    missing_points: list[str]


class Grades(Model):
    items: list[Grade] = Field(min_length=1, max_length=36)


class Verification(Model):
    supported: bool
    issues: list[str]


class Route(Model):
    intent: Literal["ask", "quiz", "note"]


class NoteExtraction(Model):
    note_type: NoteType | None = None
    scope: str = ""
    duration_minutes: int | None = None
    emphasis: list[str] = Field(default_factory=list)
    audience_level: AudienceLevel | None = None
    source_types: list[SourceType] = Field(default_factory=list)


class NotePoint(Model):
    heading: Annotated[str, Field(min_length=1, max_length=200)]
    content: Annotated[str, Field(min_length=1, max_length=4000)]
    provenance: Literal["source", "synthesis", "ai_supplement"]
    citations: list[Citation] = Field(default_factory=list, max_length=8)


class GeneratedNote(Model):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    points: list[NotePoint] = Field(min_length=1, max_length=100)


class AgentResponse(Model):
    session_id: str
    status: Literal[
        "completed", "configured", "needs_input", "awaiting_answers", "insufficient_evidence"
    ]
    answer: str = ""
    questions: list[dict] = Field(default_factory=list)
    citations: list[Evidence] = Field(default_factory=list)
    assessment: dict | None = None
    weak_points: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    prompt: dict | None = None
    note_config: dict | None = None
    draft: dict | None = None

    @model_validator(mode="after")
    def public_questions(self):
        private = {"reference_answer", "must_include", "scoring_tips", "common_mistakes"}
        if any(private & q.keys() for q in self.questions):
            raise ValueError("待作答响应不能包含参考答案")
        return self
