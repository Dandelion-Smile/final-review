"""Deterministic proofs for the text-polish strategy, independent of any model."""

import json

from final_review.material_note_polish import (
    assemble_body,
    build_batches,
    check_batch,
    final_coverage,
    outline_input,
    repair_outline,
    validate_outline,
)
from final_review.material_notes import initial_body, node_text, semantic_input, validate_body
from final_review.material_structure import inspect_presentation
from final_review.material_text_source import (
    STRATEGY,
    classify_blocks,
    extract_source_pages,
    has_native_text,
    source_units,
)


def pages_of(deck_bytes):
    return classify_blocks(inspect_presentation(deck_bytes))


def units_for(pages, source_hash="hash"):
    return source_units(pages, source_hash)


# ---------------------------------------------------------------- extraction


def test_side_by_side_text_stays_searchable_instead_of_demoting_the_page():
    from test_material_hybrid import deck

    pages = pages_of(deck(("overlap",)))
    assert pages[0]["needs_page_image"] is False
    assert [b["searchable"] for b in pages[0]["blocks"]] == [True, True]


def test_tables_and_pictures_no_longer_demote_native_text():
    from test_material_hybrid import deck

    pages = pages_of(deck(("table", "picture")))
    for page in pages:
        assert page["needs_page_image"] is False
        assert page["blocks"][0]["searchable"] is True
    # The deck fixture's table is a 2x2 grid without cell text: it stays a
    # fragment ("无独立正文") at the block level instead of demoting the page.
    empty_table = pages[0]["blocks"][-1]
    assert empty_table["kind"] == "table"
    assert empty_table["searchable"] is False
    assert "无独立正文" in empty_table["exclusion_reasons"]


def test_table_with_cell_text_keeps_rows_searchable():
    from io import BytesIO

    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "协议"
    table.cell(0, 1).text = "端口"
    table.cell(1, 0).text = "DNS"
    table.cell(1, 1).text = "53"
    output = BytesIO()
    presentation.save(output)

    pages = pages_of(output.getvalue())
    assert pages[0]["needs_page_image"] is False
    table_blocks = [b for b in pages[0]["blocks"] if b["kind"] == "table"]
    assert table_blocks and all(b["searchable"] for b in table_blocks)
    assert "DNS\t53" in table_blocks[0]["text"]


def test_formula_and_superscript_blocks_become_fragments():
    from test_material_hybrid import deck

    pages = pages_of(deck(("formula", "superscript")))
    for page in pages:
        assert page["blocks"][0]["searchable"] is False
        assert page["blocks"][0]["exclusion_reasons"]


def test_screenshot_only_page_reports_no_native_text():
    from test_material_text_images import screenshots

    pages = pages_of(screenshots())
    assert has_native_text(pages) is False
    body = initial_body(pages, "hash")
    assert semantic_input(body, searchable_only=True) == []
    assert all(node["type"] != "pageImage" for node in body["content"])


def test_unit_ids_match_initial_body_block_ids():
    from test_material_hybrid import deck

    pages = pages_of(deck(("text",)))
    body = initial_body(pages, "hash")
    units = units_for(pages, "hash")
    body_ids = {n["attrs"]["block_id"] for n in body["content"]}
    unit_ids = {u["id"] for u in units}
    assert unit_ids <= body_ids
    assert any(u["kind"] == "note" for u in units)


def test_markdown_and_plain_text_extraction(tmp_path):
    markdown = tmp_path / "notes.md"
    markdown.write_text(
        "# 第一章 Docker 基础\n\n容器是进程级的隔离。\n\n- 镜像是只读模板\n- 容器是运行实例\n\n"
        "## 1.1 镜像\n\n镜像分层存储，端口默认 53。\n",
        encoding="utf-8",
    )
    pages = classify_blocks(extract_source_pages(markdown, tmp_path, "h1"))
    assert [p["title"] for p in pages] == ["第一章 Docker 基础", "1.1 镜像"]
    assert pages[0]["blocks"][0]["kind"] == "title"
    assert all(b["searchable"] for p in pages for b in p["blocks"])
    plain = tmp_path / "notes.txt"
    plain.write_text("第一段。\n\n第二段，含数字 80。\n\n第三段。\n", encoding="utf-8")
    text_pages = classify_blocks(extract_source_pages(plain, tmp_path, "h2"))
    assert has_native_text(text_pages)
    assert all(p["needs_page_image"] is False for p in text_pages)


# ------------------------------------------------------------------- outline


def test_validate_outline_requires_exactly_one_assignment_per_block():
    units = units_for(pages_of(_deck_two_text_pages()))
    outline = {
        "document_title": "课件",
        "sections": [{"title": "第一章", "level": 1, "block_ids": [u["id"] for u in units]}],
        "excluded": [],
    }
    assert validate_outline(outline, units) == []
    dropped = dict(outline)
    dropped["sections"] = [dict(outline["sections"][0], block_ids=units[0]["id"])]
    assert validate_outline(dropped, units)
    duplicated = dict(outline)
    duplicated["sections"] = [outline["sections"][0], dict(outline["sections"][0], title="第二章")]
    assert any("重复" in error for error in validate_outline(duplicated, units))
    unknown = dict(outline)
    unknown["sections"] = [dict(outline["sections"][0], block_ids=["native-nonexistent"])]
    assert validate_outline(unknown, units)


def test_repair_outline_fixes_model_mistakes_instead_of_failing():
    units = units_for(pages_of(_deck_two_text_pages()))
    good = units[0]["id"]
    truncated = "native-" + good[len("native-") :][:4]  # model truncated the long id
    outline = {
        "document_title": "课件",
        "sections": [
            {"title": "第一章", "level": 1, "block_ids": [good, truncated, "native-26"]},
            {"title": "第二章", "level": 1, "block_ids": [good]},  # duplicate assignment
        ],
        "excluded": [
            dict(block_id=good, reason="页脚"),  # conflict: assigned AND excluded
            dict(block_id=units[1]["id"], reason=""),
        ],
    }
    repaired, notes = repair_outline(outline, units)
    assert validate_outline(repaired, units) == []
    assigned = [b for s in repaired["sections"] for b in s["block_ids"]]
    excluded_ids = {item["block_id"] for item in repaired["excluded"]}
    # First assignment wins; truncated id resolved to the same block.
    assert assigned[0] == good
    assert len(assigned) == len(set(assigned))
    assert good not in excluded_ids  # assignment wins over exclusion
    assert units[1]["id"] in assigned  # invalid exclusions return to writing
    # Every unit is either assigned or excluded — nothing dropped, no failure.
    assert set(assigned) | excluded_ids == {u["id"] for u in units}


def test_build_batches_never_cross_sections_and_cache_keys_differ():
    units = units_for(pages_of(_deck_two_text_pages()))
    sections = [
        {"title": "第一章", "level": 1, "block_ids": [u["id"] for u in units[:2]]},
        {"title": "第二章", "level": 1, "block_ids": [u["id"] for u in units[2:]]},
    ]
    batches = build_batches(
        sections,
        units,
        max_blocks=1,
        user_id="u",
        document_id="d",
        source_hash="h",
        model_config={"m": 1},
        prompt_version="v1",
    )
    assert [b["section_title"] for b in batches] == ["第一章"] * 2 + ["第二章"] * 2
    assert len({b["cache_key"] for b in batches}) == len(batches)


# ------------------------------------------------------- batch-level checks


def _batch(units):
    return {
        "section_title": "第一章",
        "section_level": 1,
        "units": units,
        "cache_key": "k",
    }


def test_check_batch_catches_unreferenced_blocks_and_ungrounded_numbers():
    from test_material_hybrid import deck

    pages = pages_of(deck(("text",)))
    units = units_for(pages)
    batch = _batch(units)
    result = {
        "elements": [
            {
                "type": "paragraph",
                "text": "端口号为 53。",
                "items": [],
                "source_refs": [units[0]["id"]],
            },
        ],
        "excluded": [],
    }
    # The teaching note block is assigned but never referenced.
    errors = check_batch(result, batch)
    assert any("未引用" in error or "既未引用" in error for error in errors)
    ungrounded = {
        "elements": [
            {
                "type": "paragraph",
                "text": "端口号为 5300。",
                "items": [],
                "source_refs": [u["id"] for u in units],
            },
        ],
        "excluded": [],
    }
    assert any("5300" in error for error in check_batch(ungrounded, batch))


def test_check_batch_accepts_faithful_numbers_and_verbatim_code():
    batch = _batch(
        [
            dict(
                id="native-a",
                page=1,
                kind="text",
                reliable=True,
                issues=[],
                text="默认端口 8080；配置命令：\ndocker run -p 8080:80",
            ),
        ]
    )
    result = {
        "elements": [
            {
                "type": "paragraph",
                "text": "默认端口是 8080。",
                "items": [],
                "source_refs": ["native-a"],
            },
            {
                "type": "codeBlock",
                "text": "docker run -p 8080:80",
                "items": [],
                "source_refs": ["native-a"],
            },
        ],
        "excluded": [],
    }
    assert check_batch(result, batch) == []


def test_check_batch_rejects_modified_code():
    batch = _batch(
        [
            dict(
                id="native-a",
                page=1,
                kind="text",
                reliable=True,
                issues=[],
                text="docker run -p 8080:80",
            ),
        ]
    )
    result = {
        "elements": [
            {
                "type": "codeBlock",
                "text": "docker run -p 9090:80",
                "items": [],
                "source_refs": ["native-a"],
            },
        ],
        "excluded": [],
    }
    assert any("逐字" in error for error in check_batch(result, batch))


def test_check_batch_allows_reasoned_exclusion_of_placeholders():
    batch = _batch(
        [
            dict(id="native-a", page=1, kind="text", reliable=True, issues=[], text="正文内容"),
            dict(id="native-b", page=1, kind="text", reliable=True, issues=[], text="12"),
        ]
    )
    result = {
        "elements": [
            {"type": "paragraph", "text": "正文内容", "items": [], "source_refs": ["native-a"]},
        ],
        "excluded": [{"block_id": "native-b", "reason": "页码"}],
    }
    assert check_batch(result, batch) == []


# --------------------------------------------------------- assembly/publish


def _deck_two_text_pages():
    from test_material_hybrid import deck

    return deck(("text", "text"))


def test_assemble_body_builds_polished_blocks_with_refs_and_supplement():
    units = units_for(pages_of(_deck_two_text_pages()))
    reliable = [u for u in units if u["reliable"]]
    orphan = dict(
        id="native-orphan",
        page=9,
        kind="text",
        reliable=False,
        issues=["图形内文字"],
        text="流程节点甲",
    )
    units = units + [orphan]
    batches_results = [
        {
            "section_title": "第一章",
            "section_level": 1,
            "result": {
                "elements": [
                    {
                        "type": "heading",
                        "text": "第一章 概述",
                        "items": [],
                        "level": 2,
                        "source_refs": [reliable[0]["id"]],
                    },
                    {
                        "type": "paragraph",
                        "text": "整理后的正文。",
                        "items": [],
                        "source_refs": [u["id"] for u in reliable],
                    },
                ],
                "excluded": [],
            },
        }
    ]
    body = assemble_body("doc-1", batches_results, units, "课件笔记")
    kinds = [node["type"] for node in body["content"]]
    assert kinds[0] == "sourceBlock"
    polished = [n for n in body["content"] if n["attrs"]["content_origin"] == "model_polished"]
    assert polished
    assert all(n["attrs"]["searchable"] is False for n in polished)
    assert any(
        "source_refs" in n["attrs"] and reliable[0]["id"] in n["attrs"]["source_refs"]
        for n in polished
    )
    assert any("课件笔记" in node_text(n) for n in body["content"])
    supplement = [n for n in body["content"] if "原文补充" in node_text(n)]
    assert supplement and orphan["id"] in supplement[0]["attrs"]["source_refs"]
    assert final_coverage(batches_results, units) == []


def test_final_coverage_reports_missing_reliable_blocks():
    units = units_for(pages_of(_deck_two_text_pages()))
    partial = [
        {
            "section_title": "第一章",
            "section_level": 1,
            "result": {
                "elements": [
                    {
                        "type": "paragraph",
                        "text": "部分正文。",
                        "items": [],
                        "source_refs": [units[0]["id"]],
                    },
                ],
                "excluded": [],
            },
        }
    ]
    errors = final_coverage(partial, units)
    assert any("未进入笔记" in error for error in errors)


def test_validate_body_preserves_source_refs_and_strips_them_from_new_blocks():
    units = units_for(pages_of(_deck_two_text_pages()))
    polished = dict(
        type="sourceBlock",
        attrs=dict(
            block_id="polish-abc",
            page=1,
            content_origin="model_polished",
            searchable=False,
            exclusion_reasons=[],
            source_refs=[units[0]["id"]],
        ),
        content=[dict(type="paragraph", content=[dict(type="text", text="整理后的正文。")])],
    )
    doc = dict(type="doc", content=[deepcopy_block(polished)])
    saved = validate_body(doc, dict(type="doc", content=[polished]))
    assert saved["content"][0]["attrs"]["source_refs"] == [units[0]["id"]]
    assert saved["content"][0]["attrs"]["content_origin"] == "model_polished"
    # A user-added block must not be able to claim polished provenance.
    edited = dict(
        type="sourceBlock",
        attrs=dict(
            block_id="user-new",
            page=None,
            content_origin="model_polished",
            searchable=True,
            exclusion_reasons=[],
            source_refs=[units[0]["id"]],
        ),
        content=[dict(type="paragraph", content=[dict(type="text", text="我补充的。")])],
    )
    saved = validate_body(
        dict(type="doc", content=[deepcopy_block(polished), edited]),
        dict(type="doc", content=[deepcopy_block(polished)]),
    )
    user_block = next(n for n in saved["content"] if n["attrs"]["block_id"] == "user-new")
    assert user_block["attrs"]["content_origin"] == "user_added"
    assert user_block["attrs"].get("source_refs", []) == []
    # Editing polished text turns it into user content but keeps the references.
    changed = dict(type="doc", content=[deepcopy_block(polished)])
    changed["content"][0]["content"][0]["content"][0]["text"] = "我改过的正文。"
    saved = validate_body(changed, dict(type="doc", content=[deepcopy_block(polished)]))
    assert saved["content"][0]["attrs"]["content_origin"] == "user_edit"
    assert saved["content"][0]["attrs"]["source_refs"] == [units[0]["id"]]


def test_validate_body_rejects_malformed_refs():
    polished = dict(
        type="sourceBlock",
        attrs=dict(
            block_id="polish-abc",
            page=1,
            content_origin="model_polished",
            searchable=False,
            exclusion_reasons=[],
            source_refs=[123],
        ),
        content=[dict(type="paragraph", content=[dict(type="text", text="正文。")])],
    )
    try:
        validate_body(dict(type="doc", content=[polished]), dict(type="doc", content=[]))
    except ValueError as exc:
        assert "来源引用" in str(exc)
    else:
        raise AssertionError("malformed source_refs must be rejected")


def test_outline_planning_receives_complete_knowledge_not_title_previews():
    units = units_for(pages_of(_deck_two_text_pages()))
    data = outline_input("课件", units)
    assert [block["text"] for block in data["blocks"]] == [unit["text"] for unit in units]


def test_strategy_identifier_is_isolated_from_text_images():
    from pathlib import Path

    from final_review import material_text_source
    from final_review.material_notes import STRATEGY as TEXT_IMAGES

    assert STRATEGY == "text-polish-v1"
    assert TEXT_IMAGES == "text-images-v1"
    assert STRATEGY != TEXT_IMAGES
    # The pure-text module must never pull in the screenshot/vision pipeline.
    source = Path(material_text_source.__file__).read_text(encoding="utf-8").lower()
    for banned in ("soffice", "libreoffice", "slideinterpreter", "poppler", "render_page"):
        assert banned not in source


def deepcopy_block(node):
    return json.loads(json.dumps(node, ensure_ascii=False))
