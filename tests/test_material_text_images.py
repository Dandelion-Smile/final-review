"""Deterministic text/image policy, independent of external conversion tools."""

from io import BytesIO

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches
from test_material_hybrid import deck

from final_review.material_notes import initial_body, semantic_input
from final_review.material_structure import inspect_presentation
from final_review.material_text_images import classify_pages


def screenshots(count=1):
    ppt = Presentation()
    buffer = BytesIO()
    Image.new("RGB", (50, 50), "white").save(buffer, format="PNG")
    for _ in range(count):
        slide = ppt.slides.add_slide(ppt.slide_layouts[6])
        slide.shapes.add_picture(BytesIO(buffer.getvalue()), Inches(1), Inches(1))
    output = BytesIO()
    ppt.save(output)
    return output.getvalue()


def test_mixed_pages_only_render_complex_targets_and_keep_independent_text():
    pages = classify_pages(inspect_presentation(deck(("text", "picture", "table"))))
    assert [p["position"] for p in pages if p["needs_page_image"]] == [2, 3]
    assert all(p["blocks"][0]["searchable"] for p in pages)
    assert pages[2]["blocks"][-1]["searchable"] is False


def test_screenshot_only_notes_have_no_index_input():
    pages = classify_pages(inspect_presentation(screenshots()))
    doc = initial_body(pages, "hash")
    assert doc["content"][0]["type"] == "pageImage"
    assert semantic_input(doc, searchable_only=True) == []


def test_rectangular_diagram_nodes_are_not_assumed_to_be_body_text():
    ppt = Presentation()
    slide = ppt.slides.add_slide(ppt.slide_layouts[6])
    shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, 0, 0, Inches(3), Inches(1))
    shape.text = "开始节点"
    output = BytesIO()
    ppt.save(output)
    pages = classify_pages(inspect_presentation(output.getvalue()))
    assert pages[0]["needs_page_image"]
    assert not pages[0]["blocks"][0]["searchable"]
