"""Durable material processing, shared by the worker and in-memory tests."""

import logging
import time
from pathlib import Path

from langchain_openai import OpenAIEmbeddings

from .config import Settings
from .material_conversion import IMAGE_FORMATS, convert_upload
from .postgres import PostgresStore
from .rag import KnowledgeBase
from .schemas import MaterialInput

logger = logging.getLogger(__name__)


def process_material_job(store, kb: KnowledgeBase, job: dict, max_bytes: int) -> None:
    """Process a claimed job; the attempt number fences out stale workers."""
    token = store.bind_user(job["user_id"]) if hasattr(store, "bind_user") else None
    try:
        document = store.get("document", job["document_id"])
        if not document:
            raise ValueError("原始资料不存在")
        course = store.get("course", job["course_id"])
        if course and course.get("status") in {"deleted", "purged"}:
            raise ValueError("课程已删除，资料处理已停止")
        path = Path(document["file_path"])
        if not path.is_file():
            raise ValueError("原始文件不存在，请重新上传")
        if path.suffix.lower() in IMAGE_FORMATS:
            store.update_material_job(job["job_id"], job["attempts"], stage="ocr")
        markdown = convert_upload(path.read_bytes(), document["file_name"], max_bytes)
        material = MaterialInput(
            document_id=document["document_id"], course_id=document["course_id"],
            title=document["title"], source_type=document["source_type"],
            chapter=document.get("chapter", ""), markdown=markdown,
        )
        prepared, chunks = kb.prepare(
            material, source_origin="user_upload",
            stage_callback=lambda stage: store.update_material_job(
                job["job_id"], job["attempts"], stage=stage
            ),
        )
        ready = {**document, **prepared, "parse_status": "ready"}
        ready.pop("parse_error", None)
        store.publish_material_job(job["job_id"], job["attempts"], ready, chunks)
    except ValueError as exc:
        store.fail_material_job(job["job_id"], job["attempts"], code="invalid_material",
                                message=str(exc), retry=False)
    except Exception:
        logger.exception("资料 Job 处理失败: %s", job["job_id"])
        store.fail_material_job(
            job["job_id"], job["attempts"], code="processing_unavailable",
            message="资料处理服务暂时不可用，请稍后重试",
            retry=job["attempts"] < job["max_attempts"],
        )
    finally:
        if token is not None:
            store.reset_user(token)


def main() -> None:
    settings = Settings()
    key = settings.embedding_api_key.get_secret_value()
    database_url = settings.database_url.get_secret_value()
    if not key or not database_url:
        raise RuntimeError("worker 需要 DATABASE_URL 和 EMBEDDING_API_KEY")
    store = PostgresStore(database_url)
    store.setup()
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model, api_key=key, base_url=settings.embedding_base_url,
        dimensions=settings.embedding_dimensions, request_timeout=settings.model_timeout,
        max_retries=2, check_embedding_ctx_length=False,
    )
    kb = KnowledgeBase(store, embeddings, settings)
    try:
        while True:
            job = store.claim_material_job()
            if job is None:
                time.sleep(1)
                continue
            process_material_job(store, kb, job, settings.max_upload_mb * 1024 * 1024)
    finally:
        store.close()


if __name__ == "__main__":
    main()
