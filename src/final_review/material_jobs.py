"""Durable material processing, shared by the worker and in-memory tests."""

import logging
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread

from .config import Settings
from .embeddings import build_embeddings
from .material_conversion import IMAGE_FORMATS, convert_material
from .material_hybrid import extract_preview, parse_pages, render_remaining
from .material_structure import STRATEGY_VERSION, preview_version
from .material_text_images_worker import process_text_images
from .observe import flush, job_trace, observation
from .pipeline_runtime import run_queue
from .postgres import PostgresStore
from .rag import KnowledgeBase
from .schemas import MaterialInput
from .slide_understanding import SlideInterpreter, VisionServiceUnavailable, material_quality
from .source_locators import located_sections, material_version

logger = logging.getLogger(__name__)


def process_material_job(store, kb: KnowledgeBase, job: dict, max_bytes: int) -> None:
    """Process a claimed job; the attempt number fences out stale workers."""
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
                if not lease_store.renew_material_job(
                    job["job_id"], job["attempts"], job.get("lease_token")
                ):
                    lost.set()
                    return
        except Exception:
            lost.set()
            logger.exception("资料任务续约失败: %s", job["job_id"])
        finally:
            lease_store.close()

    thread = Thread(target=heartbeat, daemon=True, name="material-heartbeat")
    if isinstance(store, PostgresStore):
        thread.start()
    try:
        with guard as allowed:
            if allowed:
                with job_trace("material-worker", job) as metrics:
                    _process_material_job(store, kb, job, max_bytes, lost, metrics)
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=5)


def _process_material_job(
    store, kb: KnowledgeBase, job: dict, max_bytes: int, lost=None, metrics=None
) -> None:
    token = store.bind_user(job["user_id"]) if hasattr(store, "bind_user") else None
    interpreter = None
    metrics = metrics if metrics is not None else {}
    started = time.monotonic()
    phase = job.get("phase", "parse")
    lease = {"lease_token": job["lease_token"]} if job.get("lease_token") else {}
    if job.get("available_at") and job.get("started_at"):
        metrics["queue_seconds"] = max(
            0,
            (
                datetime.fromisoformat(job["started_at"])
                - datetime.fromisoformat(job["available_at"])
            ).total_seconds(),
        )

    def save_metrics():
        metrics["processing_seconds"] = round(time.monotonic() - started, 6)
        if hasattr(store, "save_material_metrics"):
            store.save_material_metrics(job, dict(metrics))

    def progress(stage):
        if lost is not None and lost.is_set():
            raise MaterialLeaseLost()
        valid = store.update_material_job(job["job_id"], job["attempts"], stage=stage, **lease)
        if isinstance(store, PostgresStore) and not valid:
            raise MaterialLeaseLost()

    try:
        document = store.get("document", job["document_id"])
        if not document:
            raise ValueError("原始资料不存在")
        course = store.get("course", job["course_id"])
        if course and course.get("status") in {"deleted", "purged"}:
            raise ValueError("课程已删除，资料处理已停止")
        path = Path(document["file_path"])
        if not path.is_absolute():
            # Older records stored paths relative to the API's project directory.
            path = Path(__file__).resolve().parents[2] / path
        if not path.is_file():
            raise ValueError("原始文件不存在，请重新上传")
        if path.suffix.lower() in IMAGE_FORMATS:
            store.update_material_job(job["job_id"], job["attempts"], stage="ocr")
        content = path.read_bytes()
        if len(content) > max_bytes:
            raise ValueError("资料超过上传大小限制")
        source_hash = sha256(content).hexdigest()
        if document.get("content_sha256", source_hash) != source_hash:
            raise ValueError("原文件已变化，请重新上传")
        cache_dir = path.with_suffix(".analysis")
        if document.get("parse_strategy") == "text-images-v1":
            if isinstance(store, PostgresStore):
                store.knowledge_settings = kb.settings
            if not process_text_images(
                store, kb, job, document, path, source_hash, progress, metrics
            ):
                raise MaterialLeaseLost()
            return
        if document.get("parse_strategy") == "text-polish-v1":
            from .material_text_polish_worker import process_text_polish

            if isinstance(store, PostgresStore):
                store.knowledge_settings = kb.settings
            if not process_text_polish(
                store, kb, job, document, path, source_hash, progress, metrics
            ):
                raise MaterialLeaseLost()
            return
        if phase == "preview":
            progress("native_extract")
            pages = extract_preview(
                path,
                cache_dir,
                source_hash,
                parse_strategy=job.get("parse_strategy") or document.get("parse_strategy"),
            )
            now = datetime.now(UTC)
            preview = dict(
                preview_status="ready",
                preview_version=preview_version(source_hash),
                preview_strategy=STRATEGY_VERSION,
                preview_page_count=len(pages),
                preview_error=None,
                preview_ready_at=now.isoformat(),
            )
            if document.get("uploaded_at"):
                metrics["basic_preview_seconds"] = (
                    now - datetime.fromisoformat(document["uploaded_at"])
                ).total_seconds()
            metrics["status"] = "preview_ready"
            save_metrics()
            with observation("material.preview_handoff"):
                if not store.handoff_material_preview(job, pages, preview, dict(metrics)):
                    raise MaterialLeaseLost()
            return
        if phase == "images":
            pages = store.material_pages(document)
            render_remaining(path, cache_dir, source_hash, pages, progress)
            metrics["status"] = "images_ready"
            save_metrics()
            if not store.finish_material_images(job):
                raise MaterialLeaseLost()
            return
        hybrid = document.get("preview_strategy") == STRATEGY_VERSION
        if hybrid:
            pages = store.material_pages(document)
            if len(pages) != document["preview_page_count"]:
                raise ValueError("页面检查点不完整，请重新上传资料")

            def checkpoint(page):
                progress("checkpoint")
                if not store.checkpoint_material_page(job, page):
                    raise MaterialLeaseLost()

            def factory():
                if not kb.settings.material_vision_enabled:
                    raise VisionServiceUnavailable("复杂页面需要启用资料视觉解析")
                return SlideInterpreter(kb.settings)

            converted = parse_pages(
                path, cache_dir, source_hash, pages, factory, checkpoint, progress
            )
        else:
            if path.suffix.lower() in {".ppt", ".pptx"} and kb.settings.material_vision_enabled:
                interpreter = SlideInterpreter(kb.settings)
            with observation("material.legacy_parse"):
                converted = convert_material(
                    content,
                    document["file_name"],
                    max_bytes,
                    stage_callback=progress,
                    interpreter=interpreter,
                    cache_dir=cache_dir,
                )
        markdown = converted.markdown
        sections = converted.sections or located_sections(content, document["file_name"])
        material = MaterialInput(
            document_id=document["document_id"],
            course_id=document["course_id"],
            title=document["title"],
            source_type=document["source_type"],
            chapter=document.get("chapter", ""),
            markdown=markdown,
        )
        with observation("material.index"):
            prepared, chunks = kb.prepare(
                material,
                source_origin="user_upload",
                sections=sections,
                plain_text=converted.pipeline is not None or path.suffix.lower() == ".txt",
                stage_callback=progress,
            )
        ready = {**document, **prepared, "parse_status": "ready"}
        if converted.pages is not None:
            ready["analysis_pages"] = converted.pages
            ready["processing_pipeline"] = converted.pipeline
            ready["quality_status"] = material_quality(converted.pages)
            ready.pop("material_version_id", None)
            for chunk in chunks:
                from .storage import stable_key

                chunk["chunk_id"] = stable_key(
                    chunk["chunk_id"], converted.pipeline, chunk["content"]
                )
        ready["material_version_id"] = material_version(ready)["material_version_id"]
        ready.pop("parse_error", None)
        if hybrid:
            ready["images_status"] = (
                "queued" if any(not p["image_available"] for p in converted.pages) else "ready"
            )
        metrics["status"] = "ready"
        if document.get("uploaded_at"):
            metrics["searchable_seconds"] = (
                datetime.now(UTC) - datetime.fromisoformat(document["uploaded_at"])
            ).total_seconds()
        save_metrics()
        ready["parse_metrics"] = dict(metrics)
        if isinstance(store, PostgresStore):
            store.knowledge_settings = kb.settings
        with observation("material.publish"):
            if not store.publish_material_job(
                job["job_id"], job["attempts"], ready, chunks, **lease
            ):
                raise MaterialLeaseLost()
    except MaterialLeaseLost:
        metrics["status"] = "lease_lost"
        logger.warning("资料任务租约失效，停止发布: %s", job["job_id"])
    except VisionServiceUnavailable as exc:
        metrics["status"] = "failed"
        save_metrics()
        store.fail_material_job(
            job["job_id"],
            job["attempts"],
            code="vision_unavailable",
            message=str(exc),
            retry=False,
            **lease,
        )
    except ValueError as exc:
        metrics["status"] = "failed"
        save_metrics()
        store.fail_material_job(
            job["job_id"],
            job["attempts"],
            code="invalid_material",
            message=str(exc),
            retry=False,
            **lease,
        )
    except Exception:
        logger.exception("资料 Job 处理失败: %s", job["job_id"])
        metrics["status"] = "failed"
        save_metrics()
        store.fail_material_job(
            job["job_id"],
            job["attempts"],
            code="processing_unavailable",
            message="资料处理服务暂时不可用，请稍后重试",
            retry=job.get("phase_attempts", job["attempts"]) < job["max_attempts"],
            **lease,
        )
    finally:
        if interpreter is not None:
            interpreter.close()
        if token is not None:
            store.reset_user(token)


class MaterialLeaseLost(RuntimeError):
    pass


def main() -> None:
    settings = Settings()
    key = settings.embedding_api_key.get_secret_value()
    database_url = settings.database_url.get_secret_value()
    if not database_url:
        raise RuntimeError("worker 需要 DATABASE_URL")
    store = PostgresStore(database_url)
    store.setup()

    def execute(job):
        task_store = PostgresStore(database_url)
        embeddings = (
            build_embeddings(settings) if key and job.get("phase", "parse") == "parse" else None
        )
        kb = KnowledgeBase(task_store, embeddings, settings)
        try:
            process_material_job(task_store, kb, job, settings.max_upload_mb * 1024 * 1024)
        finally:
            task_store.close()

    try:
        # Each queue owns claim connections; preview slots never wait for formal file slots.
        shutdown = Event()

        def queue(phase, capacity):
            queue_store = PostgresStore(database_url)
            try:
                run_queue(
                    lambda: queue_store.claim_material_job(phase),
                    execute,
                    capacity,
                    stop_event=shutdown,
                )
            finally:
                queue_store.close()

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="material-queue") as pools:
            futures = [
                pools.submit(queue, phase, capacity)
                for phase, capacity in (
                    ("preview", settings.material_preview_concurrency),
                    ("parse", settings.material_file_concurrency),
                    ("images", settings.material_image_concurrency),
                )
            ]
            futures.append(pools.submit(_polish_queue_loop, shutdown, database_url, settings))
            try:
                completed, _ = wait(futures, return_when=FIRST_EXCEPTION)
                for future in completed:
                    future.result()
            finally:
                shutdown.set()
    finally:
        store.close()
        flush()


def _polish_queue_loop(shutdown, database_url, settings):
    """Dedicated queue for note polish; extraction never waits on the model."""
    from .material_note_polish_jobs import process_polish_job

    queue_store = PostgresStore(database_url)

    def execute(job):
        task_store = PostgresStore(database_url)
        try:
            process_polish_job(task_store, job, settings)
        finally:
            task_store.close()

    try:
        run_queue(
            queue_store.claim_polish_job,
            execute,
            settings.material_note_polish_concurrency,
            stop_event=shutdown,
        )
    finally:
        queue_store.close()


if __name__ == "__main__":
    main()
