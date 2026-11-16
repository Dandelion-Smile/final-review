"""Unified native-text extraction and the immutable source snapshot contract.

Strategy ``text-polish-v1``: every supported upload is reduced to native text
only (headings, paragraphs, lists, notes, tables with their row/column shape).
No vision model, no OCR, no page rendering and no PPT-to-PDF conversion ever
run on this path. The extracted pages become the immutable source snapshot
that both retrieval indexing and the note-polish model read from.
"""

import re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

STRATEGY = "text-polish-v1"
STRATEGY_VERSION = "text-polish-v1"

# Block-level fragment reasons. Only these demote a block to "unreliable
# original text"; page-level layout ambiguity (overlaps, connectors, free
# shapes, side-by-side boxes) no longer demotes anything by itself.
FRAGMENT_ISSUES = frozenset(
    {
        "公式、上下标或隐藏文字",
        "复杂结构中的原生片段",
        "旋转文字",
        "异常字符",
        "页码或装饰占位文字",
        "无独立正文",
    }
)
_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_LINE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
MAX_PAGE_CHARS = 6000
MAX_BLOCK_CHARS = 4000


def _digest(value) -> str:
    import json

    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]


def _safe(text: str) -> str:
    return _BAD.sub("\ufffd", text)


def _clean_line(line: str) -> str:
    return _safe(line.rstrip())


def _blocks_from_paragraphs(paragraphs, start_index=0):
    """Turn plain paragraph strings into snapshot blocks with stable paths."""
    blocks = []
    for offset, paragraph in enumerate(paragraphs):
        text = _clean_line(paragraph)
        if not text.strip():
            continue
        blocks.append(
            dict(
                shape_path=f"b{start_index + offset}",
                kind="text",
                text_issues=[],
                text=text,
                paragraphs=[dict(text=text, level=0, bullet=bool(_LIST_LINE.match(text)))],
                x=None,
                y=None,
                width=None,
                height=None,
            )
        )
    return blocks


def _synthetic_page(position: int, title, paragraphs, *, position_kind="section") -> dict:
    blocks = []
    if title and title.strip():
        blocks.append(
            dict(
                shape_path="title",
                kind="title",
                text_issues=[],
                text=title.strip(),
                paragraphs=[dict(text=title.strip(), level=0, bullet=False)],
                x=None,
                y=None,
                width=None,
                height=None,
            )
        )
    blocks.extend(_blocks_from_paragraphs(paragraphs, start_index=len(blocks)))
    return dict(
        position_kind=position_kind,
        position=position,
        title=title.strip() if title and title.strip() else f"第 {position} 节",
        native_text="\n".join(block["text"] for block in blocks),
        notes="",
        blocks=blocks,
        route="native",
        reasons=["原生文字提取完整"],
        needs_ocr=False,
        decorative_template_images=[],
        quality="pending",
        status="pending",
    )


def _markdown_pages(markdown: str) -> list[dict]:
    """Heading-delimited sections for DOCX/MD; each section is one synthetic page."""
    pages = []
    current_title, paragraphs = None, []
    for line in (markdown or "").splitlines() or [""]:
        match = _HEADING.match(line)
        if match:
            if paragraphs or current_title:
                pages.append(_synthetic_page(len(pages) + 1, current_title, paragraphs))
            current_title = _clean_line(match.group(2))[:120]
            paragraphs = []
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        paragraphs.append(line)
    if paragraphs or current_title:
        pages.append(_synthetic_page(len(pages) + 1, current_title, paragraphs))
    return pages or [_synthetic_page(1, None, [])]


def _plain_pages(text: str) -> list[dict]:
    """TXT: batch blank-line paragraph groups into synthetic pages."""
    groups, current = [], []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    pages = []
    pending, pending_chars = [], 0
    for group in groups:
        group_chars = sum(len(line) for line in group)
        if pending and pending_chars + group_chars > MAX_PAGE_CHARS:
            pages.append(_synthetic_page(len(pages) + 1, None, pending))
            pending, pending_chars = [], 0
        pending.extend(group)
        pending_chars += group_chars
    if pending:
        pages.append(_synthetic_page(len(pages) + 1, None, pending))
    return pages or [_synthetic_page(1, None, [])]


def _pdf_pages(path) -> list[dict]:
    """Text-layer PDF pages; scanned pages simply yield no blocks."""
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as document:
        for number, page in enumerate(document.pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            lines = [_clean_line(line) for line in text.splitlines()]
            lines = [line for line in lines if line.strip()]
            blocks = _blocks_from_paragraphs(lines)
            title = next(
                (block["text"] for block in blocks if len(block["text"]) <= 80),
                f"第 {number} 页",
            )
            pages.append(
                _synthetic_page(number, title, [], position_kind="page")
                | dict(blocks=blocks, native_text="\n".join(block["text"] for block in blocks))
            )
    return pages


def extract_source_pages(path: Path, cache_dir, source_hash) -> list[dict]:
    """Extract native text for any supported format. Never renders or OCRs."""
    from .material_conversion import MAX_PRESENTATION_SLIDES

    suffix = path.suffix.lower()
    if suffix in {".ppt", ".pptx"}:
        from .material_hybrid import presentation_source
        from .material_structure import inspect_presentation

        source = presentation_source(path, cache_dir, source_hash)
        pages = inspect_presentation(source.read_bytes())
        if not pages or len(pages) > MAX_PRESENTATION_SLIDES:
            raise ValueError(f"PPT 页数为空或超过 {MAX_PRESENTATION_SLIDES} 页限制")
        return pages
    if suffix in {".doc", ".docx"}:
        from markitdown import MarkItDown

        if suffix == ".doc":
            from .material_conversion import _convert_legacy

            with TemporaryDirectory(prefix="final-review-doc-") as temporary:
                converted = _convert_legacy(path, Path(temporary))
                markdown = MarkItDown(enable_plugins=False).convert(str(converted)).text_content
        else:
            markdown = MarkItDown(enable_plugins=False).convert(str(path)).text_content
        return _markdown_pages(markdown or "")
    if suffix == ".pdf":
        return _pdf_pages(path)
    if suffix == ".md":
        return _markdown_pages(_safe(path.read_text(encoding="utf-8-sig", errors="replace")))
    if suffix == ".txt":
        return _plain_pages(_safe(path.read_text(encoding="utf-8-sig", errors="replace")))
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        # Pure images carry no native text on this strategy. Keep the page record
        # so the note exists and the UI can explain what happened; no OCR runs.
        page = _synthetic_page(1, "纯图片资料", [])
        page["reasons"] = ["没有可提取的原生文字（本流程不使用 OCR）"]
        return [page]
    raise ValueError("不支持此文件格式")


def classify_blocks(pages) -> list[dict]:
    """Block-level reliability v2.

    Unlike the text-images classifier, layout ambiguity never demotes a block:
    overlapping or side-by-side text stays searchable native text. Only the
    fragment reasons above (math, hidden, garbled, placeholders, empty) mark a
    block unreliable. Tables keep their row/column text and stay searchable.
    """
    pages = deepcopy(pages)
    for page in pages:
        for block in page["blocks"]:
            text = block["text"]
            issues = [issue for issue in block.get("text_issues", []) if issue in FRAGMENT_ISSUES]
            if "\ufffd" in text:
                issues.append("异常字符")
            stripped = text.strip()
            if not stripped or stripped.isdigit():
                issues.append("无独立正文")
            block["searchable"] = not issues
            block["exclusion_reasons"] = sorted(set(issues))
        page["needs_page_image"] = False
        page["image_status"] = "not_required"
        page["status"] = "completed"
        page["quality"] = (
            "native_checked" if all(b["searchable"] for b in page["blocks"]) else "review_needed"
        )
        notes = page.get("notes", "")
        page["notes_searchable"] = bool(notes.strip()) and not (
            "\ufffd" in notes or "备注提取异常" in page.get("reasons", [])
        )
    return pages


def has_native_text(pages) -> bool:
    return any(block["text"].strip() for page in pages for block in page["blocks"]) or any(
        page.get("notes", "").strip() for page in pages
    )


def source_units(pages, source_hash) -> list[dict]:
    """Flatten the immutable snapshot into polish input units.

    Block IDs reuse the exact scheme of ``material_notes.initial_body`` so the
    polished note can reference blocks that also exist in the original revision.
    """
    units = []
    for page in pages:
        number = page["position"]
        for block in page["blocks"]:
            if not block["text"].strip():
                continue
            units.append(
                dict(
                    id="native-" + _digest([source_hash, number, block["shape_path"]]),
                    page=number,
                    kind=block["kind"],
                    text=block["text"],
                    reliable=bool(block.get("searchable")),
                    issues=sorted(set(block.get("exclusion_reasons", []))),
                )
            )
        if page.get("notes", "").strip():
            units.append(
                dict(
                    id="notes-" + _digest([source_hash, number]),
                    page=number,
                    kind="note",
                    text=page["notes"],
                    reliable=bool(page.get("notes_searchable")),
                    issues=[],
                )
            )
    # Preserve the complete source. A long text box is split into addressable
    # fragments; origin_id still points at the immutable original editor block.
    expanded = []
    for unit in units:
        text = unit["text"]
        # A teaching wrapper can mix a navigation sentence with actual knowledge.
        # Split its lines, retaining offsets, so a summary is integrated by topic
        # rather than forcing the whole course overview into one chapter.
        teaching_wrapper = (
            "\n" in text
            and unit["kind"] not in {"table", "note"}
            and re.match(r"^(本[节章堂](?:课)?|本课程|掌握|了解|熟悉|理解|0?1\s)", text.lstrip())
        )
        spans, start = [], 0
        for line in text.splitlines(keepends=True) if teaching_wrapper else [text]:
            for part in range(0, len(line), MAX_BLOCK_CHARS):
                spans.append((start + part, line[part : part + MAX_BLOCK_CHARS]))
            start += len(line)
        for ordinal, (offset, part) in enumerate(spans, 1):
            fragment = dict(unit)
            fragment["origin_id"] = unit["id"]
            fragment["source_offset"] = offset
            fragment["text"] = part
            if len(spans) > 1:
                fragment["id"] = f"{unit['id']}-part-{ordinal}"
            expanded.append(fragment)
    return expanded


def units_input_hash(units, model_config, prompt_version) -> str:
    import json

    payload = json.dumps(
        dict(units=units, model=model_config, prompt_version=prompt_version),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()


def batch_cache_key(
    user_id, document_id, source_hash, prompt_version, model_config, block_ids, *, context=None
):
    import json

    payload = json.dumps(
        dict(
            user_id=str(user_id),
            document_id=document_id,
            source_hash=source_hash,
            prompt_version=prompt_version,
            model=model_config,
            block_ids=block_ids,
            context=context,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()
