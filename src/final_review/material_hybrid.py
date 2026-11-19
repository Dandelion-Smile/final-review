"""PPTX preview, native fast path and selective visual processing."""

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from . import material_conversion as conversion
from .material_structure import STRATEGY_VERSION, inspect_presentation, native_record
from .observe import count, observation


def _write(path, data):
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(data, encoding="utf-8")
    temporary.replace(path)


def presentation_source(path, cache_dir, source_hash):
    """Legacy conversion is cached against the original bytes, not the filename."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target, stamp = cache_dir / "source.pptx", cache_dir / "source.sha256"
    if target.is_file() and stamp.is_file() and stamp.read_text() == source_hash:
        return target
    # Office creates deep UNO registry paths; a nested upload path can exceed
    # Windows' legacy path limit. Cleanup failures must not hide conversion errors.
    with TemporaryDirectory(prefix="final-review-office-", ignore_cleanup_errors=True) as temporary:
        directory = Path(temporary)
        source = directory / ("source" + path.suffix.lower())
        shutil.copyfile(path, source)
        if source.suffix == ".ppt":
            with observation("material.legacy_conversion"):
                source = conversion._convert_legacy(source, directory)
        conversion._check_office_archive(source.read_bytes(), 50 * 1024 * 1024)
        source.replace(target)
    _write(stamp, source_hash)
    return target


def load_presentation_pages(path, cache_dir, source_hash, *, limit, parse_strategy=None):
    """Open a presentation and apply one shared slide check.

    Callers choose ``limit``. Visual and text-images callers pass
    ``MAX_PRESENTATION_SLIDES``. The text-only worker passes the strategy cap.
    """
    try:
        source = presentation_source(path, cache_dir, source_hash)
        pages = inspect_presentation(source.read_bytes())
    except conversion.PresentationSlideError:
        raise
    except Exception as exc:
        mapped = conversion.as_corrupt_slide_error(exc, limit=limit, parse_strategy=parse_strategy)
        if mapped is None:
            raise
        raise mapped from exc
    conversion.validate_presentation_slide_count(
        len(pages), limit=limit, parse_strategy=parse_strategy
    )
    return pages


def extract_preview(path, cache_dir, source_hash, *, parse_strategy=None):
    """Hybrid and text-images preview. The text-only cap never applies here."""
    with observation("material.native_extraction"):
        pages = load_presentation_pages(
            path,
            cache_dir,
            source_hash,
            limit=conversion.MAX_PRESENTATION_SLIDES,
            parse_strategy=parse_strategy,
        )
    with observation("material.classification") as span:
        summary = {
            "native_pages": sum(p["route"] == "native" for p in pages),
            "visual_pages": sum(p["route"] == "visual" for p in pages),
        }
        span.update(output=summary)
        for key, value in summary.items():
            count(key, value)
    return pages


def ensure_pdf(source, cache_dir, source_hash, page_count):
    pdf, stamp = cache_dir / "source.pdf", cache_dir / "pdf.sha256"
    if not (pdf.is_file() and stamp.is_file() and stamp.read_text() == source_hash):
        with observation("material.pdf_conversion"):
            # A private directory avoids a stale worker overwriting a newer conversion.
            with TemporaryDirectory(
                prefix="final-review-office-", ignore_cleanup_errors=True
            ) as temporary:
                directory = Path(temporary)
                staged = directory / "source.pptx"
                shutil.copyfile(source, staged)
                result = conversion._convert_presentation_to_pdf(staged, directory)
                import pdfplumber

                with pdfplumber.open(result) as document:
                    if len(document.pages) != page_count:
                        raise ValueError("PPT 与 PDF 页数不一致，无法可靠定位原页")
                result.replace(pdf)
            _write(stamp, source_hash)
    return pdf


def render_page(pdf, cache_dir, source_hash, number):
    prefix = cache_dir / f"slide-{number:03d}"
    image, stamp = prefix.with_suffix(".png"), prefix.with_suffix(".source")
    if image.is_file() and stamp.is_file() and stamp.read_text() == source_hash:
        count("render_cache_hits")
        return image
    renderer = conversion._find_executable(("pdftoppm",), "")
    if not renderer:
        raise ValueError("PDF 页面渲染服务不可用，请安装 Poppler")
    with observation("material.render", {"page": number}):
        with TemporaryDirectory(dir=cache_dir) as temporary:
            target = Path(temporary) / "page"
            conversion._run(
                [
                    renderer,
                    "-f",
                    str(number),
                    "-l",
                    str(number),
                    "-singlefile",
                    "-scale-to",
                    "2000",
                    "-png",
                    str(pdf),
                    str(target),
                ],
                "PDF 页面渲染",
            )
            if not target.with_suffix(".png").is_file():
                raise ValueError(f"第 {number} 页 PDF 渲染失败")
            target.with_suffix(".png").replace(image)
        _write(stamp, source_hash)
    return image


def parse_pages(path, cache_dir, source_hash, pages, interpreter_factory, checkpoint, progress):
    """Checkpoint each page independently; only vetted content becomes formal text."""
    source = presentation_source(path, cache_dir, source_hash)
    visual = [p for p in pages if p["route"] == "visual"]
    interpreter = interpreter_factory() if visual else None
    completed, pending = {}, []
    try:
        for page in pages:
            progress("native_check" if page["route"] == "native" else "visual")
            identity = interpreter.identity if page["route"] == "visual" else STRATEGY_VERSION
            old = page.get("result")
            if (
                old
                and old.get("quality") in {"verified", "native_checked"}
                and page.get("processor_identity") == identity
            ):
                completed[page["position"]] = old
                count("page_cache_hits")
            elif page["route"] == "native":
                with observation("material.native_check", {"page": page["position"]}):
                    # Re-inspection of immutable original bytes occurred in the preview phase.
                    record = native_record(page)
                    checkpoint(
                        {
                            **page,
                            "result": record,
                            "processor_identity": identity,
                            "quality": record["quality"],
                            "status": "completed",
                        }
                    )
                    completed[page["position"]] = record
            else:
                pending.append(page)
        if pending:
            progress("convert")
            pdf = ensure_pdf(source, cache_dir, source_hash, len(pages))

            def read(page):
                with observation(
                    "material.visual_page", {"page": page["position"], "reasons": page["reasons"]}
                ):
                    image = render_page(pdf, cache_dir, source_hash, page["position"])
                    # Notes stay distinguishable from slide body in evidence collection.
                    extracted = ("### Notes:\n" + page["notes"]) if page["notes"] else ""
                    return interpreter.read(
                        image,
                        page["native_text"],
                        extracted,
                        cache_dir / "readings",
                        use_ocr=page["needs_ocr"],
                    )

            errors = []
            with ThreadPoolExecutor(max_workers=interpreter.concurrency) as pool:
                futures = {pool.submit(copy_context().run, read, p): p for p in pending}
                done = len(visual) - len(pending)
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        record = future.result()
                    except Exception as exc:
                        checkpoint({**page, "status": "failed", "error": type(exc).__name__})
                        errors.append(exc)
                        continue
                    checkpoint(
                        {
                            **page,
                            "result": record,
                            "processor_identity": interpreter.identity,
                            "quality": record["quality"],
                            "status": "completed",
                        }
                    )
                    completed[page["position"]] = record
                    done += 1
                    progress(f"复杂页面 {done}/{len(visual)}")
            if errors:
                raise errors[0]
        sections, analysis = [], []
        for page in pages:
            record = completed[page["position"]]
            image = cache_dir / f"slide-{page['position']:03d}.png"
            analysis.append(
                dict(
                    position=page["position"],
                    title=record["title"],
                    kind=record["kind"],
                    quality=record["quality"],
                    issues=record.get("issues", []),
                    route=page["route"],
                    reasons=page["reasons"],
                    verification_method="native_integrity"
                    if page["route"] == "native"
                    else "visual_review",
                    image_available=image.is_file(),
                    blocks=[
                        {
                            k: block.get(k)
                            for k in (
                                "block_id",
                                "title",
                                "quality",
                                "issues",
                                "evidence",
                                "warnings",
                            )
                        }
                        for block in record["blocks"]
                    ],
                )
            )
            if record["kind"] != "knowledge":
                continue
            for block in record["blocks"]:
                if block.get("quality", record["quality"]) not in {"verified", "native_checked"}:
                    continue
                text = block.get("text", block.get("markdown", ""))
                if page["route"] != "native":
                    text = f"{block['title']}\n\n{text}"
                if text.strip():
                    sections.append(
                        dict(position_kind="slide", position=page["position"], text=text)
                    )
        if not sections:
            raise ValueError("PPT 没有通过检查的内容；基础预览仍可查看，请核对原页")
        return conversion.ConvertedMaterial(
            markdown="\n\n".join(s["text"] for s in sections),
            sections=sections,
            pages=analysis,
            pipeline=STRATEGY_VERSION,
        )
    finally:
        if interpreter is not None:
            interpreter.close()


def render_remaining(path, cache_dir, source_hash, pages, progress):
    missing = [p for p in pages if not (cache_dir / f"slide-{p['position']:03d}.png").is_file()]
    if not missing:
        return
    source = presentation_source(path, cache_dir, source_hash)
    pdf = ensure_pdf(source, cache_dir, source_hash, len(pages))
    for page in missing:
        progress("images")
        render_page(pdf, cache_dir, source_hash, page["position"])
