"""Initial-revision indexing and separately leased page rendering."""

import time
from contextlib import contextmanager

from .material_hybrid import ensure_pdf, extract_preview, presentation_source, render_page
from .material_notes import STRATEGY, digest
from .material_text_images import classify_pages, text_sections
from .schemas import MaterialInput
from .storage import stable_key


def image_cache_dir(path, source_hash):
    # Upload filenames already identify the owner/document. Full source hashes stay
    # in the source/PDF/page stamps; nesting another 64-byte component breaks Windows.
    return path.with_suffix(".analysis") / "ti1"


@contextmanager
def conversion_lock(store, job, progress):
    """Serialize immutable-file cache writers across processes, with lease checks."""
    key = f"material-render:{job['user_id']}:{job['document_id']}:{job['source_hash']}:{STRATEGY}"
    connection = store.connection
    while not connection.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok", (key,)
    ).fetchone()["ok"]:
        progress("render_cache_wait")
        time.sleep(0.2)
    try:
        progress("render_cache_lock")
        yield
    finally:
        connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (key,))


def process_text_images(store, kb, job, document, path, source_hash, progress, metrics):
    # Deliberately does not import a visual client, OCR helper or generic converter.
    cache = image_cache_dir(path, source_hash)
    phase = job["phase"]
    metrics.update(
        parse_mode=STRATEGY, purpose=job.get("purpose", phase), visual_requests=0, ocr_calls=0
    )
    started = time.monotonic()

    def checkpoint_metrics():
        metrics["processing_seconds"] = round(time.monotonic() - started, 6)
        store.save_material_metrics(job, dict(metrics))

    if phase == "preview":
        progress("native_extract")
        with conversion_lock(store, job, progress):
            pages = classify_pages(extract_preview(path, cache, source_hash))
        preview = dict(
            preview_status="ready",
            preview_version=digest([source_hash, STRATEGY]),
            preview_page_count=len(pages),
            preview_error=None,
        )
        metrics.update(
            native_blocks=sum(b["searchable"] for p in pages for b in p["blocks"]),
            excluded_blocks=sum(not b["searchable"] for p in pages for b in p["blocks"]),
        )
        metrics["status"] = "preview_ready"
        checkpoint_metrics()
        return store.handoff_text_images_preview(job, pages, preview)
    revision = store.text_image_job_source(job)
    if not revision:
        return False
    if phase == "parse":
        sections = text_sections(revision["body"])
        markdown = "\n\n".join(s["text"] for s in sections)
        if sections:
            material = MaterialInput(
                document_id=document["document_id"],
                course_id=document["course_id"],
                title=document["title"],
                source_type=document["source_type"],
                chapter=document.get("chapter", ""),
                markdown=markdown,
            )
            prepared, chunks = kb.prepare(
                material,
                source_origin="user_upload",
                sections=sections,
                plain_text=True,
                stage_callback=progress,
            )
        else:
            prepared, chunks = dict(cleaned_markdown="", chunk_count=0), []
        prepared.update(
            parse_status="ready",
            processing_pipeline=STRATEGY,
            note_revision_id=revision["revision_id"],
            text_input_hash=revision["input_hash"],
            material_version_id=stable_key(
                document["document_id"], STRATEGY, revision["revision_id"], revision["input_hash"]
            ),
            knowledge_status="queued" if sections else "no_reliable_text",
            quality_status="native_checked" if sections else "no_reliable_text",
        )
        for chunk in chunks:
            chunk["chunk_id"] = stable_key(chunk["chunk_id"], STRATEGY, revision["revision_id"])
            chunk["note_revision_id"] = revision["revision_id"]
        metrics.update(status="ready", note_revision_id=revision["revision_id"])
        prepared["parse_metrics"] = dict(metrics)
        progress("publish")
        checkpoint_metrics()
        return store.publish_text_images(job, prepared, chunks)
    if phase != "images":
        raise ValueError("不支持的资料任务阶段")
    with conversion_lock(store, job, progress):
        pages = store.material_pages(document)
        targets = set(job["target_pages"])
        if not targets or not targets <= {p["position"] for p in pages if p["needs_page_image"]}:
            raise ValueError("原页图片目标不匹配")
        missing = [
            p
            for p in pages
            if p["position"] in targets
            and not (
                p.get("image_status") == "ready"
                and (cache / f"slide-{p['position']:03d}.png").is_file()
                and (cache / f"slide-{p['position']:03d}.source").is_file()
                and (cache / f"slide-{p['position']:03d}.source").read_text() == source_hash
            )
        ]
        if missing:
            progress("pdf_conversion")
            source = presentation_source(path, cache, source_hash)
            pdf = ensure_pdf(source, cache, source_hash, len(pages))
            for page in missing:
                number = page["position"]
                progress("images")
                try:
                    render_page(pdf, cache, source_hash, number)
                except Exception:
                    if not store.checkpoint_page_image(
                        job, number, error="原页图片生成失败，可重试"
                    ):
                        return False
                    continue
                if not store.checkpoint_page_image(
                    job, number, cache_key=f"{source_hash}:{number}:v1"
                ):
                    return False
                metrics["rendered_pages"] = metrics.get("rendered_pages", 0) + 1
    metrics["status"] = "images_finished"
    checkpoint_metrics()
    return store.finish_text_images(job)
