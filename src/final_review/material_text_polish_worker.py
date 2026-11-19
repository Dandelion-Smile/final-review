"""text-polish-v1 worker phases: native extraction, snapshot, original-text index.

Deliberately imports no vision client, no OCR helper, no PDF renderer and no
page-image machinery. The PPT-to-PPTX legacy conversion (for ``.ppt`` files)
is the only external conversion this path may perform.
"""

import time
from contextlib import contextmanager

from .material_conversion import presentation_slide_limit, validate_presentation_slide_count
from .material_note_polish import polish_model_config
from .material_notes import digest
from .material_text_images import text_sections
from .material_text_source import (
    STRATEGY,
    classify_blocks,
    extract_source_pages,
    has_native_text,
    source_units,
    units_input_hash,
)
from .schemas import MaterialInput
from .storage import stable_key


@contextmanager
def polish_conversion_lock(store, job, progress):
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


def process_text_polish(store, kb, job, document, path, source_hash, progress, metrics):
    """Handle preview (snapshot) and parse (original-text index) phases."""
    cache = path.with_suffix(".analysis")
    phase = job["phase"]
    settings = kb.settings
    # Sentinels: these must stay zero for the whole strategy.
    metrics.update(
        parse_mode=STRATEGY,
        purpose=job.get("purpose", phase),
        visual_requests=0,
        ocr_calls=0,
        page_renders=0,
        pdf_conversions=0,
    )
    started = time.monotonic()
    # One decision for preview and parse, from the saved strategy, not the live mode.
    strategy = job.get("parse_strategy") or document.get("parse_strategy")
    slide_limit = presentation_slide_limit(
        strategy, text_max_slides=settings.material_text_max_slides
    )

    def checkpoint_metrics():
        metrics["processing_seconds"] = round(time.monotonic() - started, 6)
        store.save_material_metrics(job, dict(metrics))

    if phase == "preview":
        progress("native_extract")
        with polish_conversion_lock(store, job, progress):
            pages = classify_blocks(
                extract_source_pages(
                    path,
                    cache,
                    source_hash,
                    parse_strategy=strategy,
                    text_max_slides=settings.material_text_max_slides,
                )
            )
        model_config = polish_model_config(settings)
        units = source_units(pages, source_hash)
        input_hash = units_input_hash(
            units, model_config, settings.material_note_polish_prompt_version
        )
        preview = dict(
            preview_status="ready",
            preview_version=digest([source_hash, STRATEGY]),
            preview_page_count=len(pages),
            preview_error=None,
        )
        metrics.update(
            native_blocks=sum(b["searchable"] for p in pages for b in p["blocks"]),
            excluded_blocks=sum(not b["searchable"] for p in pages for b in p["blocks"]),
            has_native_text=has_native_text(pages),
            polish_units=len(units),
        )
        metrics["status"] = "preview_ready"
        checkpoint_metrics()
        return store.handoff_text_polish_preview(
            job,
            pages,
            preview,
            dict(metrics),
            model_config=model_config,
            input_hash=input_hash,
            prompt_version=settings.material_note_polish_prompt_version,
        )
    if phase == "parse" and path.suffix.lower() in {".ppt", ".pptx"}:
        # Same validator and same saved-strategy cap as preview. Do not re-read
        # the file and do not apply MAX_PRESENTATION_SLIDES here.
        preview_count = document.get("preview_page_count")
        if isinstance(preview_count, int):
            validate_presentation_slide_count(
                preview_count, limit=slide_limit, parse_strategy=strategy
            )
    revision = store.text_polish_job_source(job)
    if not revision:
        return False
    if phase == "parse":
        # Retrieval and knowledge evidence always come from the immutable
        # original snapshot revision, never from the polished note.
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
        return store.publish_text_polish(job, prepared, chunks)
    raise ValueError("不支持的资料任务阶段")
