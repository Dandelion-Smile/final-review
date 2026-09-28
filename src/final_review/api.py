import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from .agent import FinalReviewAgent, SessionConflict
from .auth import CurrentUser, DatabaseAuth
from .config import Settings
from .domain import DomainConflict, DomainNotFound, DomainService
from .llm import ModelError, build_fast_quiz_model, build_models, build_review_model
from .material_conversion import SUPPORTED_SUFFIXES
from .postgres import PostgresStore
from .rag import KnowledgeBase
from .rendering import render_markdown
from .schemas import (
    AgentRequest,
    AgentResponse,
    AssetCreate,
    AssetRevisionCreate,
    ChatRequest,
    ChatResponse,
    ConfirmationConsume,
    ConversationCreate,
    CourseCreate,
    CourseUpdate,
    Credentials,
    ExamCreate,
    ExamUpdate,
    FastQuizRequest,
    FastQuizSubmission,
    Identifier,
    MaterialDelete,
    MaterialInput,
    MaterialUpdate,
    ResumeRequest,
    SourceType,
    Submission,
)
from .storage import StorageError, stable_key

logger = logging.getLogger(__name__)


def is_small_talk(message: str) -> bool:
    """Avoid treating greetings as evidence-backed course questions."""
    normalized = "".join(char for char in message.lower().strip() if char.isalnum())
    return normalized in {"hi", "hello", "hey", "你好", "您好", "在吗", "嗨"}


def create_app(settings: Settings | None = None, agent: FinalReviewAgent | None = None):
    settings = settings or Settings()
    database_auth = DatabaseAuth(settings)

    @asynccontextmanager
    async def lifespan(app):
        if agent is not None:
            app.state.agent = agent
            app.state.agents = {model.id: agent for model in settings.available_chat_models()}
            app.state.store = agent.store
            yield
            return
        store = None
        app.state.store = None
        app.state.agent = None
        app.state.agents = {}
        try:
            if database_auth.enabled:
                database_auth.setup()
            database_url = settings.database_url.get_secret_value()
            if database_url:
                store = PostgresStore(database_url)
                store.setup()
                app.state.store = store
            # Course and exam data only need PostgreSQL. The RAG agent needs both
            # model providers, so it can remain unavailable during M1-01 testing.
            if (
                settings.llm_api_key.get_secret_value()
                and settings.embedding_api_key.get_secret_value()
            ):
                if store is None:
                    raise RuntimeError("DATABASE_URL is required for the application store")
                model, embeddings = build_models(settings)
                kb = KnowledgeBase(store, embeddings, settings)
                app.state.agent = FinalReviewAgent(store, kb, model, settings)
                app.state.agents = {
                    config.id: FinalReviewAgent(
                        store, kb, build_review_model(config, settings), settings
                    )
                    for config in settings.available_chat_models()
                }
            yield
        finally:
            if store is not None:
                store.close()
            database_auth.close()

    app = FastAPI(
        title="Final Review Agent",
        version="0.1.0",
        description="课程资料入库、证据问答、模拟测评与薄弱点反馈。单进程后端。",
        lifespan=lifespan,
    )
    bearer = HTTPBearer(auto_error=False)
    # Passing an Agent is the explicit in-memory test seam. Production launches
    # through the factory with agent=None, so it always enables configured Auth.
    auth_enabled = database_auth.enabled and agent is None
    local_test_mode = agent is not None

    @app.middleware("http")
    async def bind_database_user(request: Request, call_next):
        """Propagate the authenticated owner into sync endpoints and LangGraph.

        A sync FastAPI dependency runs in a separate worker context, so binding
        there does not reliably reach the endpoint. Middleware sets the
        ContextVar before FastAPI dispatches the request instead.
        """
        token = None
        store = getattr(app.state, "store", None)
        if auth_enabled and store is not None and hasattr(store, "bind_user"):
            try:
                user = database_auth.current_user(request)
            except HTTPException:
                user = None
            if user is not None:
                token = store.bind_user(user.id)
        try:
            return await call_next(request)
        finally:
            if token is not None:
                store.reset_user(token)

    def authorize(
        request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
    ):
        expected = settings.api_token.get_secret_value()
        if expected and (
            credentials is None
            or not secrets.compare_digest(
                credentials.credentials.encode(),
                expected.encode(),
            )
        ):
            raise HTTPException(401, "需要有效 Bearer Token")
        if auth_enabled:
            user = database_auth.current_user(request)
        elif local_test_mode:
            user = CurrentUser(id="local-user", is_local=True)
        else:
            raise HTTPException(503, "当前服务尚未配置本地认证数据库")
        yield user

    def runtime(model_id: str | None = None):
        if app.state.agent is None:
            raise HTTPException(503, "资料库服务尚未配置")
        if model_id is None:
            return app.state.agent
        try:
            return app.state.agents[model_id]
        except KeyError:
            raise HTTPException(422, "所选模型不可用于资料库对话") from None

    def conversation_store():
        """Persist UI data even when the optional RAG agent is unavailable."""
        store = app.state.store
        if store is None:
            raise HTTPException(503, "课程数据服务尚未配置")
        return store

    def require_course(course_id: str, user: CurrentUser):
        """Reject guessed course IDs before the Agent or storage layer sees them."""
        course = conversation_store().get("course", course_id)
        if course is None and user.is_local:
            return
        if course is None or course.get("user_id") != user.id:
            raise HTTPException(404, "课程不存在")
        if course.get("status") in {"deleted", "purged"}:
            raise HTTPException(409, "课程已删除，不能继续操作")

    def domain(user: CurrentUser) -> DomainService:
        return DomainService(conversation_store(), user.id)

    @app.exception_handler(SessionConflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def validation_handler(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def model_validation_handler(request, exc):
        logger.warning("Structured data validation failed: %s", type(exc).__name__)
        return JSONResponse(status_code=502, content={"detail": "模型或资料输出格式不符合约束"})

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": "会话不存在"})

    @app.exception_handler(DomainNotFound)
    async def domain_missing_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": "资源不存在"})

    @app.exception_handler(DomainConflict)
    async def domain_conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(StorageError)
    async def storage_handler(request, exc):
        # Preserve the chained driver exception in server logs for operations
        # staff while keeping the HTTP response free of database internals.
        logger.exception("Storage operation failed")
        return JSONResponse(
            status_code=503, content={"detail": "数据库操作失败，请检查服务日志与配置"}
        )

    async def model_handler(request, exc):
        logger.warning("Model operation failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=502, content={"detail": "模型调用失败，可使用 recover 恢复任务"}
        )

    app.add_exception_handler(ModelError, model_handler)
    app.add_exception_handler(OpenAIError, model_handler)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/api/auth/sign-up")
    def sign_up(request: Credentials, response: Response):
        if not auth_enabled:
            raise HTTPException(503, "当前服务尚未配置本地认证数据库")
        user, session_id = database_auth.sign_up(request.email, request.password)
        database_auth.set_session_cookie(response, session_id)
        return {"confirmation_required": False, "user": {"email": user.email}}

    @app.post("/api/auth/sign-in")
    def sign_in(request: Credentials, response: Response):
        if not auth_enabled:
            raise HTTPException(503, "当前服务尚未配置本地认证数据库")
        remote_addr = request.client.host if request.client else None
        user, session_id = database_auth.sign_in(request.email, request.password, remote_addr)
        database_auth.set_session_cookie(response, session_id)
        return {"user": {"email": user.email}}

    @app.post("/api/auth/sign-out")
    def sign_out(request: Request, response: Response):
        if auth_enabled:
            database_auth.revoke(request)
        database_auth.clear_session_cookie(response)
        return {"signed_out": True}

    @app.get("/api/auth/me")
    def who_am_i(user: CurrentUser = Depends(authorize)):
        return {"id": user.id, "email": user.email, "local": user.is_local}

    @app.get("/api/chat/models", dependencies=[Depends(authorize)])
    def list_chat_models():
        return {
            "items": [
                {"id": model.id, "label": model.label} for model in settings.available_chat_models()
            ]
        }

    @app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(authorize)])
    def chat(request: ChatRequest, user: CurrentUser = Depends(authorize)):
        require_course(request.course_id, user)
        try:
            selected_model = settings.get_chat_model(request.model_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        def ordinary_reply() -> str:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是考前笔记的复习助手。用中文回答，清晰、简洁、以考试得分为导向。"
                        "当前没有足够的课程资料依据时，可以使用通用知识回答，但不要声称答案来自课程资料。"
                    ),
                },
                *[item.model_dump() for item in request.history],
                {"role": "user", "content": request.message},
            ]
            client = OpenAI(
                api_key=selected_model.api_key.get_secret_value(), base_url=selected_model.base_url
            )
            completion = client.chat.completions.create(
                model=selected_model.model, messages=messages, max_tokens=1200
            )
            if not completion.choices[0].message.content:
                raise HTTPException(502, "模型未返回可显示的内容")
            return completion.choices[0].message.content

        def fallback_reply() -> str:
            try:
                return ordinary_reply()
            except OpenAIError:
                # The evidence-backed flow remains usable even when the optional
                # general-chat provider is temporarily unavailable.
                logger.warning("General-chat fallback provider is unavailable")
                return "当前资料没有直接依据，通用问答服务暂时不可用，请稍后重试。"

        # Some OpenAI-compatible providers only support ordinary chat reliably.
        # Keep those models usable without sending them through the agent's
        # multi-step tool and structured-output workflow.
        if is_small_talk(request.message):
            reply = "你好！我已经准备好了。你可以问课程资料里的知识点，或让我根据资料出题。"
            citations, model_name = [], selected_model.label
        elif app.state.agent is not None and selected_model.grounded:
            result = runtime(selected_model.id).invoke(
                AgentRequest(
                    course_id=request.course_id,
                    session_id=request.conversation_id,
                    message=request.message,
                    intent="ask",
                )
            )
            if result.status == "insufficient_evidence":
                reply, citations, model_name = fallback_reply(), [], selected_model.label
            else:
                reply, citations, model_name = result.answer, result.citations, selected_model.label
        else:
            reply = ordinary_reply()
            citations, model_name = [], selected_model.label

        store = conversation_store()
        conversation_key = stable_key(request.course_id, request.conversation_id)
        created_at = datetime.now(UTC).isoformat()
        existing_conversation = store.get("conversation", conversation_key) or {}
        store.put(
            "conversation",
            conversation_key,
            {
                **existing_conversation,
                "conversation_id": request.conversation_id,
                "course_id": request.course_id,
                "user_id": user.id,
                "title": existing_conversation.get("title") or "未命名对话",
                "created_at": existing_conversation.get("created_at", created_at),
                "updated_at": created_at,
            },
        )
        store.put(
            "message",
            stable_key(conversation_key, "user", created_at),
            {
                "conversation_id": request.conversation_id,
                "course_id": request.course_id,
                "user_id": user.id,
                "role": "user",
                "content": request.message,
                "created_at": created_at,
            },
        )
        assistant_created_at = datetime.now(UTC).isoformat()
        store.put(
            "message",
            stable_key(conversation_key, "assistant", assistant_created_at),
            {
                "conversation_id": request.conversation_id,
                "course_id": request.course_id,
                "user_id": user.id,
                "role": "assistant",
                "content": reply,
                "created_at": assistant_created_at,
            },
        )
        return ChatResponse(reply=reply, model=model_name, citations=citations)

    @app.post("/api/chat/recover", response_model=ChatResponse, dependencies=[Depends(authorize)])
    def recover_chat(
        course_id: Identifier,
        conversation_id: Identifier,
        model_id: Identifier | None = None,
        user: CurrentUser = Depends(authorize),
    ):
        """Resume an interrupted chat task without exposing agent endpoints to the UI."""
        require_course(course_id, user)
        try:
            selected_model = settings.get_chat_model(model_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        result = runtime(selected_model.id).recover(course_id, conversation_id)
        if result.status == "completed":
            reply = result.answer
        elif result.status == "needs_input":
            reply = "当前任务需要补充考试信息后才能继续。"
        elif result.status == "awaiting_answers":
            reply = "当前任务正在等待提交本轮题目的答案。"
        else:
            reply = result.answer or "当前资料不足，暂时无法完成此任务。"
        return ChatResponse(reply=reply, model=selected_model.label, citations=result.citations)

    @app.get("/api/courses", dependencies=[Depends(authorize)])
    def list_courses(user: CurrentUser = Depends(authorize)):
        items = (
            conversation_store().scan("course", {"user_id": user.id})
            if not user.is_local
            else conversation_store().scan("course", {})
        )
        # A deleted course remains visible during the recovery window. Purged
        # courses must never be offered for recovery in the workbench.
        return {"items": [item for item in items if item.get("status") != "purged"]}

    @app.post("/api/courses", dependencies=[Depends(authorize)])
    def create_course(request: CourseCreate, user: CurrentUser = Depends(authorize)):
        store = conversation_store()
        course_id = stable_key(request.name, uuid4().hex)[:20]
        now = datetime.now(UTC).isoformat()
        item = {
            "course_id": course_id,
            "user_id": user.id,
            "name": request.name,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        store.put("course", course_id, item)
        return item

    @app.patch("/api/courses/{course_id}", dependencies=[Depends(authorize)])
    def update_course(
        course_id: Identifier, request: CourseUpdate, user: CurrentUser = Depends(authorize)
    ):
        return domain(user).update_course(
            course_id,
            request.model_dump(exclude={"expected_updated_at"}),
            request.expected_updated_at,
        )

    @app.post("/api/courses/{course_id}/archive", dependencies=[Depends(authorize)])
    def archive_course(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        return domain(user).archive_course(course_id)

    @app.post("/api/courses/{course_id}/restore", dependencies=[Depends(authorize)])
    def restore_course(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        return domain(user).restore_course(course_id)

    @app.post("/api/courses/{course_id}/deletion-preview", dependencies=[Depends(authorize)])
    def course_deletion_preview(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        return domain(user).deletion_preview(course_id)

    @app.post("/api/courses/{course_id}/delete", dependencies=[Depends(authorize)])
    def delete_course(
        course_id: Identifier, request: ConfirmationConsume, user: CurrentUser = Depends(authorize)
    ):
        return domain(user).delete_course(course_id, request.confirmation_id)

    @app.get("/api/courses/{course_id}/exams", dependencies=[Depends(authorize)])
    def list_exams(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        return {"items": domain(user).list_exams(course_id)}

    @app.post("/api/courses/{course_id}/exams", dependencies=[Depends(authorize)])
    def create_exam(
        course_id: Identifier, request: ExamCreate, user: CurrentUser = Depends(authorize)
    ):
        return domain(user).create_exam(course_id, request.model_dump())

    @app.get("/api/exams/{exam_id}", dependencies=[Depends(authorize)])
    def read_exam(exam_id: Identifier, user: CurrentUser = Depends(authorize)):
        return domain(user).exam(exam_id)

    @app.patch("/api/exams/{exam_id}", dependencies=[Depends(authorize)])
    def update_exam(
        exam_id: Identifier, request: ExamUpdate, user: CurrentUser = Depends(authorize)
    ):
        return domain(user).update_exam(
            exam_id,
            request.model_dump(exclude={"expected_updated_at"}),
            request.expected_updated_at,
        )

    @app.post("/api/exams/{exam_id}/archive", dependencies=[Depends(authorize)])
    def archive_exam(exam_id: Identifier, user: CurrentUser = Depends(authorize)):
        return domain(user).archive_exam(exam_id)

    @app.post("/api/courses/{course_id}/assets", dependencies=[Depends(authorize)])
    def create_asset(
        course_id: Identifier, request: AssetCreate, user: CurrentUser = Depends(authorize)
    ):
        return domain(user).create_asset(course_id, request.model_dump())

    @app.post("/api/assets/{asset_id}/revisions", dependencies=[Depends(authorize)])
    def create_asset_revision(
        asset_id: Identifier, request: AssetRevisionCreate, user: CurrentUser = Depends(authorize)
    ):
        return domain(user).create_revision(asset_id, request.model_dump())

    @app.post(
        "/api/assets/{asset_id}/revisions/{revision_id}/confirm", dependencies=[Depends(authorize)]
    )
    def confirm_asset_revision(
        asset_id: Identifier, revision_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        return domain(user).confirm_revision(asset_id, revision_id)

    @app.post("/api/assets/{asset_id}/archive", dependencies=[Depends(authorize)])
    def archive_asset(asset_id: Identifier, user: CurrentUser = Depends(authorize)):
        return domain(user).archive_asset(asset_id)

    @app.get("/api/courses/{course_id}/documents", dependencies=[Depends(authorize)])
    def list_documents(
        course_id: Identifier, chapter: str | None = None,
        source_type: SourceType | None = None,
        status: str | None = None, user: CurrentUser = Depends(authorize),
    ):
        require_course(course_id, user)
        if status is not None and status not in {"queued", "running", "ready", "failed"}:
            raise HTTPException(422, "未知资料状态")
        items = conversation_store().scan("document", {"course_id": course_id})
        return {"items": [item for item in items
                          if item.get("parse_status") != "deleted"
                          and (chapter is None or item.get("chapter", "") == chapter)
                          and (source_type is None or item.get("source_type") == source_type.value)
                          and (status is None or item.get("parse_status") == status)]}

    def ready_material(course_id: str, document_id: str, user: CurrentUser) -> dict:
        require_course(course_id, user)
        document = conversation_store().get("document", document_id)
        if (document is None or document.get("user_id") != user.id
                or document.get("course_id") != course_id
                or document.get("parse_status") != "ready"):
            raise HTTPException(404, "资料不存在")
        return document

    def material_chunks(course_id: str, document_id: str, user: CurrentUser):
        document = ready_material(course_id, document_id, user)
        store = conversation_store()
        version = store.ensure_material_source(document)
        chunks = store.list_material_chunks(document_id)
        return document, version, chunks

    def public_chunk(chunk: dict, *, include_content: bool = False) -> dict:
        result = {
            "chunk_id": chunk["chunk_id"],
            "locator_id": chunk["chunk_id"],
            "position_kind": chunk.get("position_kind", "document"),
            "position": chunk.get("position"),
            "text_start": chunk.get("text_start"),
            "text_end": chunk.get("text_end"),
            "excerpt": chunk["content"][:300],
        }
        if include_content:
            result["content"] = chunk["content"]
        return result

    @app.get("/api/courses/{course_id}/documents/{document_id}/chunks",
             dependencies=[Depends(authorize)])
    def list_document_chunks(course_id: Identifier, document_id: Identifier,
                             user: CurrentUser = Depends(authorize)):
        document, version, chunks = material_chunks(course_id, document_id, user)
        return {
            "document_id": document_id,
            "material_version_id": version["material_version_id"],
            "file_name": version["file_name"],
            "source_type": version["source_type"],
            "items": [public_chunk(chunk) for chunk in chunks],
        }

    @app.get("/api/courses/{course_id}/documents/{document_id}/chunks/{chunk_id}",
             dependencies=[Depends(authorize)])
    def get_document_chunk(course_id: Identifier, document_id: Identifier,
                           chunk_id: Identifier, user: CurrentUser = Depends(authorize)):
        document, version, chunks = material_chunks(course_id, document_id, user)
        chunk = next((item for item in chunks if item["chunk_id"] == chunk_id), None)
        if chunk is None:
            raise HTTPException(404, "资料片段不存在")
        return {
            "document_id": document["document_id"],
            "material_version_id": version["material_version_id"],
            "file_name": version["file_name"],
            "source_type": version["source_type"],
            **public_chunk(chunk, include_content=True),
        }

    @app.get("/api/courses/{course_id}/documents/{document_id}/download",
             dependencies=[Depends(authorize)])
    def download_document(course_id: Identifier, document_id: Identifier,
                          user: CurrentUser = Depends(authorize)):
        document = ready_material(course_id, document_id, user)
        path_value = document.get("file_path")
        if not path_value:
            raise HTTPException(404, "原文件不存在")
        path = Path(path_value)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        path = path.resolve()
        upload_root = Path(settings.uploads_dir).resolve()
        if not path.is_relative_to(upload_root) or not path.is_file():
            raise HTTPException(404, "原文件不存在")
        return FileResponse(path, filename=Path(document.get("file_name") or path.name).name)

    @app.patch("/api/courses/{course_id}/documents/{document_id}",
               dependencies=[Depends(authorize)])
    def update_document(course_id: Identifier, document_id: Identifier,
                        request: MaterialUpdate, user: CurrentUser = Depends(authorize)):
        require_course(course_id, user)
        return domain(user).update_material(document_id, course_id, request.model_dump(mode="json"))

    @app.get("/api/courses/{course_id}/conversations", dependencies=[Depends(authorize)])
    def list_conversations(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        require_course(course_id, user)
        items = conversation_store().scan("conversation", {"course_id": course_id})
        return {"items": sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)}

    @app.post("/api/courses/{course_id}/conversations", dependencies=[Depends(authorize)])
    def create_conversation(
        course_id: Identifier, request: ConversationCreate, user: CurrentUser = Depends(authorize)
    ):
        require_course(course_id, user)
        store = conversation_store()
        conversation_id = f"chat-{uuid4().hex}"
        now = datetime.now(UTC).isoformat()
        item = {
            "conversation_id": conversation_id,
            "course_id": course_id,
            "user_id": user.id,
            "title": request.title,
            "created_at": now,
            "updated_at": now,
        }
        store.put("conversation", stable_key(course_id, conversation_id), item)
        return item

    @app.get(
        "/api/courses/{course_id}/conversations/{conversation_id}/messages",
        dependencies=[Depends(authorize)],
    )
    def list_messages(
        course_id: Identifier, conversation_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        require_course(course_id, user)
        rows = conversation_store().scan(
            "message", {"conversation_id": conversation_id, "course_id": course_id}
        )
        return {"items": sorted(rows, key=lambda item: item.get("created_at", ""))}

    @app.post("/knowledge/ingest", dependencies=[Depends(authorize)])
    def ingest(request: MaterialInput, user: CurrentUser = Depends(authorize)):
        require_course(request.course_id, user)
        return runtime().kb.ingest(request, user_id=user.id)

    @app.post("/knowledge/upload", dependencies=[Depends(authorize)])
    def upload(
        request: Request,
        course_id: Identifier = Form(),
        title: str = Form(),
        source_type: SourceType = Form(),
        chapter: str = Form(""),
        file: UploadFile = File(),
        user: CurrentUser = Depends(authorize),
    ):
        store = conversation_store()
        require_course(course_id, user)
        upload_path = None
        try:
            suffix = Path(file.filename or "").suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                raise HTTPException(
                    422, "不支持此文件格式；支持 md/txt/pdf/ppt/pptx/doc/docx/png/jpg/webp"
                )
            raw = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
            if not raw:
                raise HTTPException(422, "文件为空")
            if len(raw) > settings.max_upload_mb * 1024 * 1024:
                raise HTTPException(422, "文件超过上传大小限制")
            key = request.headers.get("Idempotency-Key")
            if key is not None and (not key.strip() or len(key) > 200):
                raise HTTPException(422, "Idempotency-Key 长度须为 1 到 200")
            fingerprint = sha256(
                raw + json.dumps([title, chapter, source_type.value, file.filename],
                                 ensure_ascii=False).encode()
            ).hexdigest()
            if key:
                existing = store.find_material_job(course_id, key)
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise HTTPException(409, "相同幂等键对应不同资料")
                    return JSONResponse(status_code=202, content={
                        "job_id": existing["job_id"], "document_id": existing["document_id"],
                        "status": existing["status"],
                        "status_url": (
                            f"/api/courses/{course_id}/material-jobs/{existing['job_id']}"
                        ),
                    })
            document_id = uuid4().hex
            job_id = uuid4().hex
            upload_dir = Path(settings.uploads_dir).resolve() / user.id / course_id
            upload_dir.mkdir(parents=True, exist_ok=True)
            upload_path = upload_dir / f"{uuid4().hex}{suffix}"
            upload_path.write_bytes(raw)
            document = {
                "document_id": document_id,
                "course_id": course_id,
                "user_id": user.id,
                "title": title,
                "source_type": source_type.value,
                "source_origin": "user_upload",
                "chapter": chapter,
                "file_name": file.filename or "upload",
                "file_size": len(raw),
                "file_path": str(upload_path),
                "storage_key": None,
                "uploaded_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "chunk_count": 0,
                "parse_status": "queued",
            }
            job = store.create_material_job(document, {
                "job_id": job_id, "document_id": document_id, "course_id": course_id,
                "idempotency_key": key, "fingerprint": fingerprint,
            })
            if job["document_id"] != document_id:
                upload_path.unlink(missing_ok=True)
            if job["fingerprint"] != fingerprint:
                raise HTTPException(409, "相同幂等键对应不同资料")
            return JSONResponse(status_code=202, content={
                "job_id": job["job_id"], "document_id": job["document_id"],
                "status": job["status"],
                "status_url": f"/api/courses/{course_id}/material-jobs/{job['job_id']}",
            })
        except Exception:
            if upload_path is not None and upload_path.exists():
                upload_path.unlink(missing_ok=True)
            raise
        finally:
            file.file.close()

    @app.get("/api/courses/{course_id}/material-jobs", dependencies=[Depends(authorize)])
    def list_material_jobs(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        require_course(course_id, user)
        return {"items": conversation_store().list_material_jobs(course_id)}

    @app.get("/api/courses/{course_id}/material-jobs/{job_id}", dependencies=[Depends(authorize)])
    def get_material_job(course_id: Identifier, job_id: Identifier,
                         user: CurrentUser = Depends(authorize)):
        require_course(course_id, user)
        job = conversation_store().get_material_job(job_id)
        if not job or job["course_id"] != course_id:
            raise HTTPException(404, "资料任务不存在")
        return job

    @app.post("/api/courses/{course_id}/material-jobs/{job_id}/retry",
              dependencies=[Depends(authorize)])
    def retry_material_job(course_id: Identifier, job_id: Identifier,
                           user: CurrentUser = Depends(authorize)):
        require_course(course_id, user)
        store = conversation_store()
        job = store.get_material_job(job_id)
        if not job or job["course_id"] != course_id:
            raise HTTPException(404, "资料任务不存在")
        updated = store.retry_material_job(job_id)
        if updated is None:
            raise HTTPException(409, "只有失败的资料任务可以重试")
        return JSONResponse(status_code=202, content=updated)

    @app.post("/api/quiz/generate", dependencies=[Depends(authorize)])
    def generate_fast_quiz(request: FastQuizRequest, user: CurrentUser = Depends(authorize)):
        """Default practice path: deterministic RAG retrieval plus one model call."""
        require_course(request.course_id, user)
        agent_runtime = runtime()
        try:
            selected_model = settings.get_chat_model(request.model_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        evidence = agent_runtime.kb.search(
            request.chapter or "课程核心知识点", request.course_id, request.chapter
        )
        if not evidence:
            raise HTTPException(422, "当前课程或章节没有可用于出题的资料")
        data = {
            "course_id": request.course_id,
            "chapter": request.chapter,
            "question_types": request.question_types,
            "question_count": request.question_count,
            "evidence": [item.model_dump(mode="json") for item in evidence],
        }
        quiz = build_fast_quiz_model(selected_model, settings).fast_quiz(data)
        questions = quiz["questions"]
        evidence_ids = {item.chunk_id for item in evidence}
        if len(questions) != request.question_count:
            raise ModelError("模型返回的题目数量与请求不一致")
        if any(question["question_type"] not in request.question_types for question in questions):
            raise ModelError("模型返回了未选择的题型")
        if any(
            not set(question["source_chunk_ids"]).issubset(evidence_ids) for question in questions
        ):
            raise ModelError("模型引用了当前课程资料以外的片段")
        session_id = f"fast-quiz-{uuid4().hex}"
        for index, question in enumerate(questions, start=1):
            question["id"] = f"{session_id[:20]}-q{index}"
        created_at = datetime.now(UTC).isoformat()
        conversation_store().put(
            "fast_quiz_session",
            stable_key(request.course_id, session_id),
            {
                "course_id": request.course_id,
                "user_id": user.id,
                "session_id": session_id,
                "model_id": selected_model.id,
                "chapter": request.chapter,
                "questions": questions,
                "created_at": created_at,
            },
        )
        public_questions = [
            {
                key: question[key]
                for key in ("id", "knowledge_point", "question_type", "stem", "options")
            }
            for question in questions
        ]
        return {
            "session_id": session_id,
            "questions": public_questions,
            "sources": [{"title": item.title, "chapter": item.chapter} for item in evidence],
        }

    @app.post("/api/quiz/{session_id}/submit", dependencies=[Depends(authorize)])
    def submit_fast_quiz(
        session_id: Identifier, request: FastQuizSubmission, user: CurrentUser = Depends(authorize)
    ):
        store = conversation_store()
        require_course(request.course_id, user)
        attempt_id = stable_key(request.course_id, session_id)
        existing_attempt = store.get("attempt", attempt_id)
        if existing_attempt is not None:
            return {
                "session_id": session_id,
                "assessment": existing_attempt["assessment"],
                "weak_points": existing_attempt.get("weak_points", []),
                "already_graded": True,
            }
        session = store.get("fast_quiz_session", stable_key(request.course_id, session_id))
        if session is None:
            raise HTTPException(404, "测验不存在或不属于当前课程")
        questions = session["questions"]
        ids = {question["id"] for question in questions}
        if set(request.answers) != ids:
            raise HTTPException(422, "请提交本轮所有题目的答案")
        selected_model = settings.get_chat_model(session["model_id"])
        grades = build_fast_quiz_model(selected_model, settings).grade(
            {"quiz": {"questions": questions}, "answers": request.answers}
        )["items"]
        if {grade["question_id"] for grade in grades} != ids:
            raise ModelError("评分结果的题目 ID 不匹配")
        score = round(sum(grade["score"] for grade in grades) / len(grades), 2)
        question_by_id = {question["id"]: question for question in questions}
        weak_points = sorted(
            {
                question_by_id[grade["question_id"]]["knowledge_point"]
                for grade in grades
                if grade["score"] < 60
            }
        )
        created_at = datetime.now(UTC).isoformat()
        assessment = {"score": score, "items": grades, "reference_questions": questions}
        store.put(
            "attempt",
            attempt_id,
            {
                "attempt_id": attempt_id,
                "course_id": request.course_id,
                "user_id": user.id,
                "session_id": session_id,
                "legacy_session_id": session_id,
                "score": score,
                "question_count": len(questions),
                "weak_points": weak_points,
                "assessment": assessment,
                "answers": request.answers,
                "chapter": session.get("chapter", ""),
                "created_at": created_at,
            },
        )
        store.put(
            "learning_event",
            stable_key(attempt_id, created_at),
            {
                "course_id": request.course_id,
                "user_id": user.id,
                "event_type": "assessment_completed",
                "score": score,
                "topics": weak_points,
                "created_at": created_at,
            },
        )
        return {"session_id": session_id, "assessment": assessment, "weak_points": weak_points}

    @app.post(
        "/api/courses/{course_id}/documents/{document_id}/deletion-preview",
        dependencies=[Depends(authorize)],
    )
    def document_deletion_preview(
        course_id: Identifier, document_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        require_course(course_id, user)
        return domain(user).material_deletion_preview(document_id, course_id)

    @app.post(
        "/api/courses/{course_id}/documents/{document_id}/delete", dependencies=[Depends(authorize)]
    )
    def delete_document(
        course_id: Identifier,
        document_id: Identifier,
        request: MaterialDelete,
        user: CurrentUser = Depends(authorize),
    ):
        require_course(course_id, user)
        document = conversation_store().get("document", document_id)
        if document is None or document.get("course_id") != course_id:
            raise HTTPException(404, "资料不存在")
        result = domain(user).delete_material(document_id, request.confirmation_id, request.mode)
        path = document.get("file_path")
        if path:
            Path(path).unlink(missing_ok=True)
        return result

    @app.post("/agent/invoke", response_model=AgentResponse, dependencies=[Depends(authorize)])
    def invoke(request: AgentRequest, user: CurrentUser = Depends(authorize)):
        require_course(request.course_id, user)
        return runtime().invoke(request)

    @app.post("/agent/resume", response_model=AgentResponse, dependencies=[Depends(authorize)])
    def resume(request: ResumeRequest, user: CurrentUser = Depends(authorize)):
        require_course(request.course_id, user)
        return runtime().resume_profile(request)

    @app.post(
        "/assessment/evaluate", response_model=AgentResponse, dependencies=[Depends(authorize)]
    )
    def evaluate(request: Submission, user: CurrentUser = Depends(authorize)):
        require_course(request.course_id, user)
        result = runtime().evaluate(request)
        if result.assessment:
            store = conversation_store()
            now = datetime.now(UTC).isoformat()
            attempt_id = stable_key(request.course_id, request.session_id)
            attempt = {
                "attempt_id": attempt_id,
                "course_id": request.course_id,
                "user_id": user.id,
                "session_id": request.session_id,
                "legacy_session_id": request.session_id,
                "score": result.assessment["score"],
                "question_count": len(result.assessment.get("items", [])),
                "weak_points": result.weak_points,
                "assessment": result.assessment,
                "answers": request.answers,
                "created_at": now,
            }
            store.put("attempt", attempt_id, attempt)
            store.put(
                "learning_event",
                stable_key(attempt_id, now),
                {
                    "course_id": request.course_id,
                    "user_id": user.id,
                    "event_type": "assessment_completed",
                    "score": result.assessment["score"],
                    "topics": result.weak_points,
                    "created_at": now,
                },
            )
        return result

    @app.get("/api/courses/{course_id}/attempts", dependencies=[Depends(authorize)])
    def list_attempts(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        require_course(course_id, user)
        attempts = conversation_store().scan("attempt", {"course_id": course_id})
        items = [
            {
                key: attempt.get(key)
                for key in (
                    "session_id",
                    "score",
                    "question_count",
                    "weak_points",
                    "chapter",
                    "created_at",
                )
            }
            for attempt in attempts
        ]
        return {"items": sorted(items, key=lambda item: item.get("created_at") or "", reverse=True)}

    @app.get(
        "/api/courses/{course_id}/attempts/{session_id}",
        dependencies=[Depends(authorize)],
    )
    def read_attempt(
        course_id: Identifier, session_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        require_course(course_id, user)
        attempt = conversation_store().get("attempt", stable_key(course_id, session_id))
        if attempt is None:
            raise HTTPException(404, "测验记录不存在")
        return attempt

    @app.get("/api/courses/{course_id}/report", dependencies=[Depends(authorize)])
    def report(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        require_course(course_id, user)
        attempts = conversation_store().scan("attempt", {"course_id": course_id})
        question_count = sum(item.get("question_count", 0) for item in attempts)
        average = (
            round(sum(item.get("score", 0) for item in attempts) / len(attempts), 1)
            if attempts
            else 0
        )
        topic_scores: dict[str, list[float]] = {}
        for attempt in attempts:
            questions = {
                q["id"]: q for q in attempt.get("assessment", {}).get("reference_questions", [])
            }
            for grade in attempt.get("assessment", {}).get("items", []):
                topic = questions.get(grade["question_id"], {}).get("knowledge_point", "未分类")
                topic_scores.setdefault(topic, []).append(grade["score"])
        topics = [
            {"name": name, "score": round(sum(scores) / len(scores), 1)}
            for name, scores in topic_scores.items()
        ]
        topics.sort(key=lambda item: item["score"])
        return {
            "attempt_count": len(attempts),
            "question_count": question_count,
            "average_score": average,
            "topics": topics,
            "attempts": attempts[-10:],
        }

    @app.post("/agent/recover", response_model=AgentResponse, dependencies=[Depends(authorize)])
    def recover(
        course_id: Identifier, session_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        require_course(course_id, user)
        return runtime().recover(course_id, session_id)

    @app.get("/agent/session", response_model=AgentResponse, dependencies=[Depends(authorize)])
    def session(
        course_id: Identifier, session_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        require_course(course_id, user)
        return runtime().read(course_id, session_id)

    @app.get("/agent/export", dependencies=[Depends(authorize)])
    def export(
        course_id: Identifier, session_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        require_course(course_id, user)
        response = runtime().read(course_id, session_id)
        return PlainTextResponse(render_markdown(response), media_type="text/markdown")

    return app
