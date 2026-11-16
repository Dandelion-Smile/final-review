"""Owner-scoped reading note, polish lifecycle and page-image endpoints."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from .auth import CurrentUser
from .material_notes import STRATEGY, NoteConflict
from .material_text_images_worker import image_cache_dir
from .material_text_source import STRATEGY as POLISH_STRATEGY
from .schemas import Identifier, MaterialNoteCandidateApply, MaterialNoteSave

NOTE_STRATEGIES = {STRATEGY, POLISH_STRATEGY}


def material_note_router(settings, authorize, require_course, store_for, require_file_access):
    router = APIRouter(
        prefix="/api/courses/{course_id}/documents/{document_id}", tags=["material-reading-notes"]
    )

    def source(course_id, document_id, user):
        require_course(course_id, user)
        store = store_for()
        document = store.get("document", document_id)
        if (
            not document
            or document.get("user_id") != user.id
            or document.get("course_id") != course_id
            or document.get("parse_status") == "deleted"
            or document.get("parse_strategy") not in NOTE_STRATEGIES
        ):
            raise HTTPException(404, "资料笔记不存在")
        if not hasattr(store, "read_material_note"):
            raise HTTPException(503, "资料笔记需要 PostgreSQL")
        return store, document

    def polish_model_setup():
        from .material_note_polish import polish_model_config

        return polish_model_config(settings), settings.material_note_polish_prompt_version

    @router.get("/reading-note")
    def read_note(
        course_id: Identifier, document_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        store, document = source(course_id, document_id, user)
        note = store.read_material_note(course_id, document_id)
        if not note:
            raise HTTPException(409, "资料笔记正在提取")
        course = store.get("course", course_id)
        return {
            **note,
            "document": {
                key: document.get(key)
                for key in ("document_id", "title", "file_name", "source_type", "parse_strategy")
            },
            "editable": settings.material_note_edit_enabled
            and (not course or course.get("status", "active") == "active"),
            "pages": [
                {"page_number": p["position"], "title": p["title"]}
                for p in store.material_pages(document)
            ],
            "status": store.material_note_summary(document),
        }

    @router.get("/reading-note/status")
    def note_status(
        course_id: Identifier, document_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        store, document = source(course_id, document_id, user)
        return store.material_note_summary(document)

    @router.put("/reading-note", dependencies=[Depends(require_file_access)])
    def save_note(
        course_id: Identifier,
        document_id: Identifier,
        request: MaterialNoteSave,
        user: CurrentUser = Depends(authorize),
    ):
        store, document = source(course_id, document_id, user)
        if not settings.material_note_edit_enabled:
            raise HTTPException(409, "资料笔记编辑同步尚未开放")
        course = store.get("course", course_id)
        if course and course.get("status", "active") != "active":
            raise HTTPException(409, "课程当前不可编辑")
        try:
            result = store.save_material_note(
                document, request.base_revision_id, request.idempotency_key, request.body
            )
        except NoteConflict as exc:
            return JSONResponse(
                status_code=409,
                content=dict(detail=str(exc), latest_revision_id=exc.latest_revision_id),
            )
        # The save result is idempotent; current pointers may advance independently.
        latest = store.read_material_note(course_id, document_id)
        return {
            **result,
            "revision": store.note_revision(course_id, document_id, result["revision_id"]),
            "latest_saved_revision_id": latest["latest_saved_revision_id"],
            "indexed_revision_id": latest["indexed_revision_id"],
        }

    @router.get("/reading-note/revisions/{revision_id}")
    def read_revision(
        course_id: Identifier,
        document_id: Identifier,
        revision_id: Identifier,
        user: CurrentUser = Depends(authorize),
    ):
        store, _ = source(course_id, document_id, user)
        revision = store.note_revision(course_id, document_id, revision_id)
        if not revision:
            raise HTTPException(404, "笔记版本不存在")
        return revision

    @router.post("/reading-note/polish/retry", dependencies=[Depends(require_file_access)])
    def retry_polish(
        course_id: Identifier, document_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        store, document = source(course_id, document_id, user)
        if document.get("parse_strategy") != POLISH_STRATEGY:
            raise HTTPException(409, "此资料不使用纯文字整理流程")
        try:
            return store.retry_polish(document)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post("/reading-note/polish/regenerate", dependencies=[Depends(require_file_access)])
    def regenerate_polish(
        course_id: Identifier, document_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        """Re-polish any existing note from its parsed original text."""
        store, document = source(course_id, document_id, user)
        model_config, prompt_version = polish_model_setup()
        try:
            return store.regenerate_polish(
                document, model_config=model_config, prompt_version=prompt_version
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post(
        "/reading-note/polish/candidate/apply", dependencies=[Depends(require_file_access)]
    )
    def apply_candidate(
        course_id: Identifier,
        document_id: Identifier,
        request: MaterialNoteCandidateApply,
        user: CurrentUser = Depends(authorize),
    ):
        store, _ = source(course_id, document_id, user)
        try:
            return store.apply_polish_candidate(
                course_id,
                document_id,
                base_revision_id=request.base_revision_id,
                candidate_revision_id=request.candidate_revision_id,
            )
        except NoteConflict as exc:
            return JSONResponse(
                status_code=409,
                content=dict(detail=str(exc), latest_revision_id=exc.latest_revision_id),
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.get("/page-images")
    def list_images(
        course_id: Identifier, document_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        store, document = source(course_id, document_id, user)
        if document.get("parse_strategy") != STRATEGY:
            return dict(status="not_required", items=[])
        prefix = f"/api/courses/{course_id}/documents/{document_id}/page-images"
        return dict(
            status=document.get("images_status", "queued"),
            items=[
                dict(
                    page_number=p["position"],
                    status=p.get("image_status", "queued"),
                    error=p.get("image_error"),
                    url=f"{prefix}/{p['position']}" if p.get("image_status") == "ready" else None,
                )
                for p in store.material_pages(document)
                if p.get("needs_page_image")
            ],
        )

    @router.post("/page-images/retry", dependencies=[Depends(require_file_access)])
    def retry_images(
        course_id: Identifier, document_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        store, document = source(course_id, document_id, user)
        if document.get("parse_strategy") != STRATEGY:
            raise HTTPException(409, "此资料没有原页图片任务")
        return store.retry_page_images(document)

    @router.get("/page-images/{page_number}", dependencies=[Depends(require_file_access)])
    def read_image(
        course_id: Identifier,
        document_id: Identifier,
        page_number: int,
        user: CurrentUser = Depends(authorize),
    ):
        store, document = source(course_id, document_id, user)
        if document.get("parse_strategy") != STRATEGY:
            raise HTTPException(404, "原页图片尚未就绪")
        page = next(
            (
                p
                for p in store.material_pages(document)
                if p["position"] == page_number and p.get("needs_page_image")
            ),
            None,
        )
        if not page or page.get("image_status") != "ready":
            raise HTTPException(404, "原页图片尚未就绪")
        source_path = Path(document["file_path"])
        if not source_path.is_absolute():
            source_path = Path(__file__).resolve().parents[2] / source_path
        directory = image_cache_dir(source_path, document["content_sha256"])
        image = directory / f"slide-{page_number:03d}.png"
        if (
            not image.resolve().is_relative_to(Path(settings.uploads_dir).resolve())
            or not image.is_file()
            or page.get("image_cache_key") != f"{document['content_sha256']}:{page_number}:v1"
            or not image.with_suffix(".source").is_file()
            or image.with_suffix(".source").read_text() != document["content_sha256"]
        ):
            raise HTTPException(404, "原页图片不可用")
        return FileResponse(image, media_type="image/png")

    return router
