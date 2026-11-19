"""Strategy-specific PPT slide caps. No model, OCR, or PDF rendering."""

from contextlib import contextmanager

import pytest
from pptx import Presentation
from pptx.util import Inches
from pydantic import ValidationError

from final_review import material_conversion as conversion
from final_review import material_hybrid, material_text_polish_worker
from final_review.config import Settings
from final_review.material_conversion import (
    MATERIAL_TEXT_MAX_SLIDES_MAX,
    MAX_PRESENTATION_SLIDES,
    TEXT_POLISH_STRATEGY,
    PresentationSlideError,
    presentation_slide_limit,
)
from final_review.material_text_source import STRATEGY, extract_source_pages


@pytest.fixture(scope="module")
def deck_bytes():
    cache = {}

    def build(count):
        if count not in cache:
            presentation = Presentation()
            for index in range(count):
                slide = presentation.slides.add_slide(presentation.slide_layouts[6])
                box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(4), Inches(0.4))
                box.text = f"P{index + 1}"
            from io import BytesIO

            output = BytesIO()
            presentation.save(output)
            cache[count] = output.getvalue()
        return cache[count]

    return build


def write_deck(tmp_path, payload, name="deck.pptx"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def settings_for(*, mode="hybrid_visual", text_max=120):
    return Settings(
        _env_file=None,
        material_pptx_parse_mode=mode,
        material_text_max_slides=text_max,
        material_vision_enabled=True,
    )


def _boom(calls, name):
    def inner(*args, **kwargs):
        calls.append(name)
        raise AssertionError(name)

    return inner


def forbid_visual(monkeypatch):
    calls = []
    monkeypatch.setattr(conversion, "_ocr_image", _boom(calls, "ocr"))
    monkeypatch.setattr(conversion, "_convert_presentation_to_pdf", _boom(calls, "pdf"))
    monkeypatch.setattr(conversion, "_convert_presentation", _boom(calls, "convert"))
    monkeypatch.setattr(conversion, "MarkItDown", _boom(calls, "markitdown"))
    monkeypatch.setattr(material_hybrid, "ensure_pdf", _boom(calls, "ensure_pdf"))
    monkeypatch.setattr(material_hybrid, "render_page", _boom(calls, "render"))
    monkeypatch.setattr(material_hybrid, "extract_preview", _boom(calls, "extract_preview"))
    monkeypatch.setattr("final_review.slide_understanding.SlideInterpreter", _boom(calls, "vision"))
    return calls


def _expect_slide_error(callback, reason, actual, limit):
    with pytest.raises(PresentationSlideError) as caught:
        callback()
    error = caught.value
    assert error.reason == reason
    assert error.actual == actual
    assert error.limit == limit
    assert f"slide_error={reason}" in str(error)
    if actual is not None:
        assert f"actual={actual}" in str(error)
    if limit is not None:
        assert f"limit={limit}" in str(error)
    return error


def test_visual_cap_constant_and_text_default_are_independent(monkeypatch):
    assert MAX_PRESENTATION_SLIDES == 80
    monkeypatch.delenv("MATERIAL_TEXT_MAX_SLIDES", raising=False)
    monkeypatch.delenv("MATERIAL_PPTX_PARSE_MODE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.material_text_max_slides == 120
    assert settings.material_pptx_parse_mode == "hybrid_visual"


@pytest.mark.parametrize("value", [0, -1, 501, True, 1.5, "abc", "", None])
def test_illegal_text_slide_cap_is_rejected(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, material_text_max_slides=value)


@pytest.mark.parametrize("value", [0, -1, 501, True, 1.5, "abc", ""])
def test_direct_text_slide_cap_rejects_illegal_integers(value):
    # None means "read Settings", not an illegal configured value.
    with pytest.raises(ValueError):
        presentation_slide_limit(TEXT_POLISH_STRATEGY, text_max_slides=value)


def test_saved_strategy_selects_the_cap_not_the_live_mode():
    text_mode = settings_for(mode="text_only_polished", text_max=120)
    visual_mode = settings_for(mode="hybrid_visual", text_max=120)
    lowered = settings_for(mode="text_only_polished", text_max=100)
    assert (
        presentation_slide_limit(
            "text-polish-v1", text_max_slides=text_mode.material_text_max_slides
        )
        == 120
    )
    assert (
        presentation_slide_limit(
            "text-polish-v1", text_max_slides=visual_mode.material_text_max_slides
        )
        == 120
    )
    assert (
        presentation_slide_limit("text-polish-v1", text_max_slides=lowered.material_text_max_slides)
        == 100
    )
    for strategy in (None, "", "text-images-v1", "hybrid-slides-v1"):
        assert (
            presentation_slide_limit(strategy, text_max_slides=text_mode.material_text_max_slides)
            == MAX_PRESENTATION_SLIDES
        )
    assert MATERIAL_TEXT_MAX_SLIDES_MAX == 500
    Settings(_env_file=None, material_text_max_slides=MATERIAL_TEXT_MAX_SLIDES_MAX)


@pytest.mark.parametrize(
    ("count", "strategy", "text_max", "expected"),
    [
        (0, "text-polish-v1", 120, "empty"),
        (80, "text-polish-v1", 120, "accept"),
        (81, "text-polish-v1", 120, "accept"),
        (91, "text-polish-v1", 120, "accept"),
        (120, "text-polish-v1", 120, "accept"),
        (121, "text-polish-v1", 120, "over_limit"),
        (80, None, 120, "accept"),
        (81, None, 120, "over_limit"),
        (91, "text-images-v1", 120, "over_limit"),
        (91, "text-polish-v1", 80, "over_limit"),
    ],
)
def test_text_extraction_boundaries(
    tmp_path, deck_bytes, monkeypatch, count, strategy, text_max, expected
):
    forbid_visual(monkeypatch)
    path = write_deck(tmp_path, deck_bytes(count))
    kwargs = {"parse_strategy": strategy, "text_max_slides": text_max}
    limit = presentation_slide_limit(strategy, text_max_slides=text_max)

    def extract():
        return extract_source_pages(path, tmp_path / "cache", "hash", **kwargs)

    if expected == "accept":
        pages = extract()
        assert len(pages) == count
    else:
        _expect_slide_error(extract, expected, 0 if expected == "empty" else count, limit)


def test_text_91_pages_do_not_call_visual_ocr_or_pdf(tmp_path, deck_bytes, monkeypatch):
    calls = forbid_visual(monkeypatch)
    path = write_deck(tmp_path, deck_bytes(91))
    pages = extract_source_pages(
        path,
        tmp_path / "cache",
        "hash",
        parse_strategy=STRATEGY,
        text_max_slides=120,
    )
    assert len(pages) == 91
    assert calls == []
    assert STRATEGY == "text-polish-v1"


def test_visual_preview_keeps_80_even_if_text_strategy_is_passed(tmp_path, deck_bytes, monkeypatch):
    calls = []
    monkeypatch.setattr(conversion, "_convert_presentation_to_pdf", _boom(calls, "pdf"))
    monkeypatch.setattr(conversion, "_ocr_image", _boom(calls, "ocr"))
    path = write_deck(tmp_path, deck_bytes(81))
    _expect_slide_error(
        lambda: material_hybrid.extract_preview(
            path, tmp_path / "cache", "hash", parse_strategy="text-polish-v1"
        ),
        "over_limit",
        81,
        80,
    )
    accepted = material_hybrid.extract_preview(
        write_deck(tmp_path, deck_bytes(80), "eighty.pptx"),
        tmp_path / "cache80",
        "hash80",
        parse_strategy="text_only_polished",
    )
    assert len(accepted) == 80
    assert calls == []


@pytest.mark.parametrize(
    ("count", "reason"),
    [(0, "empty"), (81, "over_limit"), (91, "over_limit"), (121, "over_limit")],
)
def test_legacy_visual_conversion_rejects_before_pdf(
    tmp_path, deck_bytes, monkeypatch, count, reason
):
    calls = []
    monkeypatch.setattr(conversion, "_convert_presentation_to_pdf", _boom(calls, "pdf"))
    monkeypatch.setattr(conversion, "MarkItDown", _boom(calls, "markitdown"))
    path = write_deck(tmp_path, deck_bytes(count))
    _expect_slide_error(
        lambda: conversion._convert_presentation(path, tmp_path),
        reason,
        0 if reason == "empty" else count,
        80,
    )
    assert calls == []


def test_corrupt_pptx_is_not_reported_as_empty_or_over_limit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(conversion, "_convert_presentation_to_pdf", _boom(calls, "pdf"))
    path = tmp_path / "broken.pptx"
    path.write_bytes(b"this is not a pptx")
    _expect_slide_error(
        lambda: material_hybrid.extract_preview(path, tmp_path / "cache", "hash"),
        "corrupt",
        None,
        80,
    )
    _expect_slide_error(
        lambda: extract_source_pages(
            path, tmp_path / "text-cache", "hash", parse_strategy=STRATEGY, text_max_slides=120
        ),
        "corrupt",
        None,
        120,
    )
    _expect_slide_error(
        lambda: conversion._convert_presentation(path, tmp_path),
        "corrupt",
        None,
        80,
    )
    assert calls == []


class _Store:
    def __init__(self, source=None):
        self.metrics = None
        self.handoff = None
        self.published = None
        self.source_calls = 0
        self.source = source

    def save_material_metrics(self, job, metrics):
        self.metrics = metrics

    def handoff_text_polish_preview(self, job, pages, preview, metrics, **kwargs):
        self.handoff = preview
        return True

    def text_polish_job_source(self, job):
        self.source_calls += 1
        return self.source

    def publish_text_polish(self, job, prepared, chunks):
        self.published = prepared
        return True


@contextmanager
def _noop_lock(*args, **kwargs):
    yield


def _run_polish(
    monkeypatch,
    tmp_path,
    deck_bytes,
    *,
    phase,
    page_count,
    text_max=120,
    strategy=STRATEGY,
    preview_count=None,
):
    calls = forbid_visual(monkeypatch)
    monkeypatch.setattr(material_text_polish_worker, "polish_conversion_lock", _noop_lock)
    path = write_deck(tmp_path, deck_bytes(page_count)) if page_count else tmp_path / "unused.pptx"
    if page_count == 0:
        path = write_deck(tmp_path, deck_bytes(0))
    if phase == "parse":
        monkeypatch.setattr(
            material_text_polish_worker,
            "extract_source_pages",
            _boom(calls, "extract_source_pages"),
        )
    settings = settings_for(mode="hybrid_visual", text_max=text_max)
    store = _Store(source={"revision_id": "rev", "input_hash": "hash", "body": {"content": []}})
    job = {"phase": phase, "parse_strategy": strategy, "purpose": phase}
    document = {
        "document_id": "doc",
        "parse_strategy": strategy,
        "preview_page_count": preview_count,
    }
    metrics = {}
    material_text_polish_worker.process_text_polish(
        store,
        type("KB", (), {"settings": settings})(),
        job,
        document,
        path,
        "source-hash",
        lambda stage: None,
        metrics,
    )
    return store, metrics, calls


def test_preview_and_parse_use_the_same_text_limit_without_visual_calls(
    tmp_path, deck_bytes, monkeypatch
):
    store, metrics, calls = _run_polish(
        monkeypatch, tmp_path, deck_bytes, phase="preview", page_count=91
    )
    assert store.handoff["preview_page_count"] == 91
    assert metrics["visual_requests"] == 0
    assert metrics["ocr_calls"] == 0
    assert metrics["page_renders"] == 0
    assert metrics["pdf_conversions"] == 0
    assert calls == []

    store, metrics, calls = _run_polish(
        monkeypatch,
        tmp_path,
        deck_bytes,
        phase="parse",
        page_count=91,
        preview_count=91,
    )
    assert store.published is not None
    assert store.published["parse_status"] == "ready"
    assert store.source_calls == 1
    assert metrics["visual_requests"] == 0
    assert calls == []


def test_parse_rejects_121_with_the_text_cap_and_does_not_open_the_snapshot(
    tmp_path, deck_bytes, monkeypatch
):
    calls = forbid_visual(monkeypatch)
    monkeypatch.setattr(material_text_polish_worker, "polish_conversion_lock", _noop_lock)
    opened = []
    monkeypatch.setattr(
        material_text_polish_worker,
        "extract_source_pages",
        lambda *args, **kwargs: opened.append(kwargs) or [],
    )
    settings = settings_for(mode="text_only_polished", text_max=120)
    store = _Store()
    _expect_slide_error(
        lambda: material_text_polish_worker.process_text_polish(
            store,
            type("KB", (), {"settings": settings})(),
            {"phase": "parse", "parse_strategy": STRATEGY},
            {"parse_strategy": STRATEGY, "preview_page_count": 121},
            tmp_path / "missing.pptx",
            "source-hash",
            lambda stage: None,
            {},
        ),
        "over_limit",
        121,
        120,
    )
    assert store.source_calls == 0
    assert opened == []
    assert calls == []


def test_historical_strategy_on_the_text_worker_still_uses_80(tmp_path, deck_bytes, monkeypatch):
    calls = forbid_visual(monkeypatch)
    monkeypatch.setattr(material_text_polish_worker, "polish_conversion_lock", _noop_lock)
    settings = settings_for(mode="text_only_polished", text_max=120)
    _expect_slide_error(
        lambda: material_text_polish_worker.process_text_polish(
            _Store(),
            type("KB", (), {"settings": settings})(),
            {"phase": "parse", "parse_strategy": "text-images-v1"},
            {"parse_strategy": "text-images-v1", "preview_page_count": 81},
            tmp_path / "missing.pptx",
            "source-hash",
            lambda stage: None,
            {},
        ),
        "over_limit",
        81,
        80,
    )
    assert calls == []
