"""Behavioral proofs: routing, early visibility, resumability and model-free native pages."""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches

from final_review.api import create_app
from final_review.material_jobs import process_material_job
from final_review.material_structure import STRATEGY_VERSION, inspect_presentation
from final_review.observe import job_trace


def deck(kinds=("text",)):
    presentation = Presentation()
    for kind in kinds:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        owner = slide.shapes.add_group_shape() if kind == "group" else slide.shapes
        shapes = owner.shapes if kind == "group" else owner
        box = shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(8), Inches(1.5))
        box.text = "DNS 并非永远只使用 UDP。\n端口号为 53；TCP 也可以使用。"
        slide.notes_slide.notes_text_frame.text = "教学提示：保留否定与数值。"
        if kind == "picture":
            output = BytesIO()
            Image.new("RGB", (20, 20), "white").save(output, format="PNG")
            slide.shapes.add_picture(BytesIO(output.getvalue()), Inches(1), Inches(3))
        elif kind == "table":
            slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(4), Inches(1))
        elif kind == "chart":
            data = ChartData()
            data.categories = ["A", "B"]
            data.add_series("数量", [1, 2])
            slide.shapes.add_chart(
                XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(3), Inches(4), Inches(2), data
            )
        elif kind == "formula":
            box._element.append(
                etree.Element("{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")
            )
        elif kind == "superscript":
            box.text_frame.paragraphs[0].runs[0]._r.get_or_add_rPr().set("baseline", "30000")
        elif kind == "connector":
            slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, 0, Inches(2), Inches(1))
        elif kind == "overlap":
            slide.shapes.add_textbox(Inches(4), Inches(0.6), Inches(2), Inches(1)).text = "第二列"
        elif kind == "unknown":
            box._element.append(OxmlElement("p:contentPart"))
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


@pytest.mark.parametrize("kind", ["text", "group"])
def test_native_pages_preserve_group_text_notes_and_positions(kind):
    page = inspect_presentation(deck((kind,)))[0]
    assert page["route"] == "native", page["reasons"]
    assert "并非永远" in page["native_text"] and "53" in page["native_text"]
    assert "教学提示" not in page["native_text"]
    assert "教学提示" in page["notes"]
    assert page["blocks"][0]["x"] >= 0
    assert page["blocks"][0]["paragraphs"][0]["level"] == 0


@pytest.mark.parametrize(
    "kind",
    ["picture", "table", "chart", "formula", "superscript", "connector", "overlap", "unknown"],
)
def test_complex_pages_are_never_routed_to_native(kind):
    page = inspect_presentation(deck((kind,)))[0]
    assert page["route"] == "visual", page
    assert page["reasons"] != ["原生文字提取完整"]


def test_inherited_bullets_and_explicit_bu_none_are_preserved():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "标题"
    body = slide.placeholders[1]
    body.text = "继承母版圆点"
    paragraph = body.text_frame.add_paragraph()
    paragraph.text = "显式禁用圆点"
    paragraph._p.get_or_add_pPr().append(OxmlElement("a:buNone"))
    data = BytesIO()
    presentation.save(data)
    page = inspect_presentation(data.getvalue())[0]
    paragraphs = next(b["paragraphs"] for b in page["blocks"] if b["kind"] == "text")
    assert paragraphs[0]["bullet"] and paragraphs[0]["bullet_marker"]
    assert not paragraphs[1]["bullet"]


def test_only_reviewed_inherited_artwork_is_ignored(monkeypatch):
    from hashlib import sha256

    from pptx.enum.shapes import MSO_SHAPE_TYPE

    from final_review import material_structure

    presentation = Presentation(BytesIO(deck(("picture",))))
    slide = presentation.slides[0]
    picture = next(s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE)
    monkeypatch.setattr(
        material_structure, "DECORATIVE_TEMPLATE_IMAGES", {sha256(picture.image.blob).hexdigest()}
    )
    # Being a known logo does not authorize ignoring a slide-owned image.
    assert material_structure.presentation_pages(presentation)[0]["route"] == "visual"
    slide.shapes._spTree.remove(picture._element)
    # Unknown inherited content remains visual, even if the body is ordinary text.
    shape = slide.slide_layout.shapes
    # Add the picture's XML through its existing relationship on the layout part.
    image_part, r_id = slide.slide_layout.part.get_or_add_image_part(BytesIO(picture.image.blob))
    copied = deepcopy(picture._element)
    copied.blipFill.blip.rEmbed = r_id
    shape._spTree.append(copied)
    assert material_structure.presentation_pages(presentation)[0]["route"] == "native"
    monkeypatch.setattr(material_structure, "DECORATIVE_TEMPLATE_IMAGES", set())
    assert material_structure.presentation_pages(presentation)[0]["route"] == "visual"


def upload(system, tmp_path, content):
    system.settings.uploads_dir = str(tmp_path)
    system.settings.material_hybrid_enabled = True
    system.settings.material_vision_enabled = True
    with TestClient(create_app(system.settings, system)) as client:
        response = client.post(
            "/knowledge/upload",
            data={
                "course_id": "net",
                "title": "混合解析",
                "source_type": "teacher_ppt",
            },
            files={"file": ("test.pptx", content)},
        )
        assert response.status_code == 202, response.text
        return response.json()["document_id"]


def run(system, phase):
    job = system.store.claim_material_job(phase)
    assert job
    process_material_job(system.store, system.kb, job, 50 * 1024 * 1024)
    return job


def test_preview_available_before_any_formal_chunks_and_native_needs_no_vision(
    system,
    tmp_path,
    monkeypatch,
):
    from final_review import material_hybrid, material_jobs

    def forbidden(*args, **kwargs):
        pytest.fail("native formal parsing must not render, OCR or create a vision client")

    monkeypatch.setattr(material_jobs, "SlideInterpreter", forbidden)
    monkeypatch.setattr(material_hybrid, "ensure_pdf", forbidden)
    document_id = upload(system, tmp_path, deck(("text", "group")))
    initial_chunks = deepcopy(system.store.chunks)
    preview_job = run(system, "preview")
    document = system.store.get("document", document_id)
    assert document["preview_status"] == "ready"
    assert document["parse_status"] != "ready"
    assert system.store.chunks == initial_chunks
    assert not system.store.checkpoint_material_page(preview_job, {"position": 1})
    assert not system.store.handoff_material_preview(preview_job, [], {}, {})
    with TestClient(create_app(system.settings, system)) as client:
        preview = client.get(f"/api/courses/net/documents/{document_id}/basic-preview")
        assert preview.status_code == 200, preview.text
        assert preview.json()["total_pages"] == 2
        assert "并非永远" in preview.json()["pages"][0]["native_text"]
        assert client.get(f"/api/courses/net/documents/{document_id}/chunks").status_code == 404
    run(system, "parse")
    ready = system.store.get("document", document_id)
    assert ready["parse_status"] == "ready"
    assert ready["quality_status"] == "native_checked"
    assert ready["processing_pipeline"] == STRATEGY_VERSION
    assert "并非永远" in ready["cleaned_markdown"] and "53" in ready["cleaned_markdown"]
    assert ready["images_status"] == "queued"
    assert ready["parse_metrics"].get("model_requests", 0) == 0
    assert all(p["quality"] == "native_checked" for p in ready["analysis_pages"])


def fake_visual(monkeypatch, calls, fail_page=None):
    from final_review import material_hybrid, material_jobs

    monkeypatch.setattr(material_hybrid, "ensure_pdf", lambda *args: Path("test.pdf"))

    def render(pdf, cache, signature, number):
        image = cache / f"slide-{number:03d}.png"
        image.write_bytes(b"image")
        return image

    monkeypatch.setattr(material_hybrid, "render_page", render)

    def read(image, native, extracted, cache, **kwargs):
        calls.append(image.stem)
        if fail_page == image.stem:
            raise RuntimeError("temporary service failure")
        return dict(
            kind="knowledge",
            title="图解",
            quality="verified",
            issues=[],
            blocks=[
                dict(
                    block_id="b1",
                    title="图解",
                    text="视觉核验后的内容",
                    quality="verified",
                    issues=[],
                ),
            ],
        )

    monkeypatch.setattr(
        material_jobs,
        "SlideInterpreter",
        lambda settings: SimpleNamespace(
            read=read, identity="test-visual", concurrency=1, close=lambda: None
        ),
    )


def test_mixed_only_calls_visual_for_complex_pages_and_reuses_completed_pages(
    system,
    tmp_path,
    monkeypatch,
):
    calls = []
    fake_visual(monkeypatch, calls, fail_page="slide-003")
    document_id = upload(system, tmp_path, deck(("text", "picture", "picture")))
    run(system, "preview")
    failed = run(system, "parse")
    assert sorted(calls) == ["slide-002", "slide-003"]
    assert system.store.get("document", document_id)["preview_status"] == "ready"
    assert not system.store.list_material_chunks(document_id)
    fake_visual(monkeypatch, calls)
    # Automatic retry is already queued; claim it like a restarted worker.
    run(system, "parse")
    assert calls.count("slide-002") == 1
    assert calls.count("slide-003") == 2
    assert not system.store.checkpoint_material_page(failed, {"position": 2})
    ready = system.store.get("document", document_id)
    assert ready["parse_status"] == "ready"
    assert ready["parse_metrics"]["page_cache_hits"] == 2
    assert "视觉核验后的内容" in ready["cleaned_markdown"]


def test_failed_complex_page_keeps_preview_and_no_formal_publication(system, tmp_path, monkeypatch):
    calls = []
    fake_visual(monkeypatch, calls, fail_page="slide-001")
    document_id = upload(system, tmp_path, deck(("picture",)))
    run(system, "preview")
    for _ in range(3):
        run(system, "parse")
    document = system.store.get("document", document_id)
    assert document["parse_status"] == "failed"
    assert document["preview_status"] == "ready"
    assert not system.store.list_material_chunks(document_id)
    with TestClient(create_app(system.settings, system)) as client:
        result = client.get(f"/api/courses/net/documents/{document_id}/basic-preview").json()
        assert result["parse_status"] == "failed"
        assert result["pages"][0]["status"] == "failed"


def test_trace_context_reaches_parallel_visual_pages(system, tmp_path, monkeypatch):
    calls = []
    fake_visual(monkeypatch, calls)
    from final_review import material_hybrid
    from final_review.observe import count

    old_render = material_hybrid.render_page

    def render(*args):
        count("thread_seen")
        return old_render(*args)

    monkeypatch.setattr(material_hybrid, "render_page", render)
    document_id = upload(system, tmp_path, deck(("picture", "picture")))
    run(system, "preview")
    run(system, "parse")
    assert system.store.get("document", document_id)["parse_metrics"]["thread_seen"] == 2


def test_tracing_unavailable_never_swallows_application_errors(monkeypatch):
    from final_review import observe

    monkeypatch.setattr(observe, "_client", lambda: None)
    with pytest.raises(ValueError, match="expected"):
        with job_trace("test", dict(user_id="u", document_id="d", job_id="j")):
            raise ValueError("expected")


def test_office_scratch_is_independent_of_deep_upload_paths(tmp_path, monkeypatch):
    from contextlib import nullcontext

    import pdfplumber

    from final_review import material_hybrid

    cache = tmp_path / ("deep-upload-" * 6)
    cache.mkdir()
    source = cache / "source.pptx"
    source.write_bytes(deck())

    def convert(staged, directory):
        assert not directory.is_relative_to(cache)
        pdf = directory / "source.pdf"
        pdf.write_bytes(b"fake-pdf")
        return pdf

    monkeypatch.setattr(material_hybrid.conversion, "_convert_presentation_to_pdf", convert)
    monkeypatch.setattr(pdfplumber, "open", lambda path: nullcontext(SimpleNamespace(pages=[1])))
    assert material_hybrid.ensure_pdf(source, cache, "hash", 1).is_file()
