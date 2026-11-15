"""Source preservation, conservative eligibility and semantic save contracts."""

from copy import deepcopy

import pytest
from test_material_hybrid import deck

from final_review.material_notes import initial_body, input_hash, semantic_input, validate_body
from final_review.material_structure import inspect_presentation
from final_review.material_text_images import classify_pages, text_sections


def body(kinds=("text",)):
    return initial_body(classify_pages(inspect_presentation(deck(kinds))), "test-hash")


def test_native_text_notes_and_stable_source_ids():
    doc = body()
    assert doc == body()
    units = semantic_input(doc, searchable_only=True)
    assert {u["origin"] for u in units} == {"native", "teaching_note"}
    assert "并非永远" in units[0]["text"] and "53" in units[0]["text"]
    assert all(u["page"] == 1 for u in units)
    assert validate_body(doc, doc) == doc


def test_format_and_image_visibility_do_not_change_semantic_input():
    doc = body(("picture",))
    edited = deepcopy(doc)
    edited["content"][0]["content"][0]["content"][0]["marks"] = [{"type": "bold"}]
    edited["content"][-1]["attrs"]["hidden"] = True
    normalized = validate_body(edited, doc)
    assert normalized != doc
    assert input_hash(normalized) == input_hash(doc)


def test_source_cannot_be_forged_or_dangerous_fragments_promoted():
    doc = body(("formula",))
    assert doc["content"][0]["attrs"]["searchable"] is False
    changed = deepcopy(doc)
    changed["content"][0]["content"][0]["content"][0]["text"] = "用户重新说明公式"
    changed["content"][0]["attrs"].update(searchable=True, content_origin="native")
    normalized = validate_body(changed, doc)
    assert normalized["content"][0]["attrs"]["content_origin"] == "user_edit"
    assert normalized["content"][0]["attrs"]["searchable"] is False
    normalized["content"].append(
        dict(
            type="sourceBlock",
            attrs=dict(block_id="user-new", page=99, content_origin="native", searchable=False),
            content=[dict(type="paragraph", content=[dict(type="text", text="用户完整补充说明")])],
        )
    )
    normalized = validate_body(normalized, doc)
    assert normalized["content"][-1]["attrs"] == dict(
        block_id="user-new",
        page=None,
        content_origin="user_added",
        searchable=True,
        exclusion_reasons=[],
    )
    assert text_sections(normalized)[-1]["content_origin"] == "user_added"


def test_empty_notes_and_removed_text_have_distinct_hashes():
    assert validate_body(dict(type="doc", content=[]), body()) == dict(type="doc", content=[])
    assert input_hash(dict(type="doc", content=[])) != input_hash(body())


@pytest.mark.parametrize("kind", ["connector", "formula", "superscript", "overlap", "unknown"])
def test_ambiguous_body_is_excluded_but_teaching_notes_remain_independent(kind):
    doc = body((kind,))
    units = semantic_input(doc, searchable_only=True)
    assert units and all(u["origin"] == "teaching_note" for u in units)


@pytest.mark.parametrize("mutation", ["url", "html", "duplicate", "foreign_image", "mark_url"])
def test_untrusted_nodes_are_rejected(mutation):
    doc = body(("picture",))
    changed = deepcopy(doc)
    if mutation == "url":
        changed["content"][-1]["attrs"]["src"] = "file:///private"
    elif mutation == "html":
        changed["content"][0]["content"] = [dict(type="html", content="<script>")]
    elif mutation == "duplicate":
        changed["content"].append(deepcopy(changed["content"][0]))
    elif mutation == "foreign_image":
        changed["content"][-1]["attrs"]["page"] = 999
    else:
        changed["content"][0]["content"][0]["content"][0]["marks"] = [
            dict(type="link", attrs=dict(href="https://example.com"))
        ]
    with pytest.raises(ValueError):
        validate_body(changed, doc)
