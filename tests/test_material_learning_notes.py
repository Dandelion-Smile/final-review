"""Knowledge completeness, safe rich text and durable partial successes."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from final_review import material_note_polish_jobs as worker
from final_review.config import Settings
from final_review.material_note_polish import (
    MAX_SECTION_CHARS,
    assemble_body,
    build_batches,
    check_batch,
    consolidate_outline,
    exclusion_errors,
    final_coverage,
)
from final_review.material_notes import node_text, validate_body
from final_review.material_text_source import _synthetic_page, classify_blocks, source_units


def unit(key="a", text="默认端口为 8080", **kwargs):
    return dict(id=key, page=1, kind="text", reliable=True, issues=[], text=text, **kwargs)


def result(ref="a", **kwargs):
    return dict(
        elements=[
            dict(type="paragraph", text="默认端口为 **8080**。", source_refs=[ref], **kwargs)
        ],
        excluded=[],
    )


def batch(units):
    return dict(section_title="服务配置", section_level=1, units=units, cache_key="cache")


def test_long_text_and_notes_survive_fragmentation_and_map_back_to_original():
    text = "前半段。" * 1300 + "不能遗漏的末尾知识"
    page = _synthetic_page(1, None, [text])
    page["notes"] = text
    units = source_units(classify_blocks([page]), "hash")
    for kind in ("text", "note"):
        fragments = [u for u in units if u["kind"] == kind]
        assert "".join(u["text"] for u in fragments) == text
        assert len({u["id"] for u in fragments}) == len(fragments) > 1
        assert len({u["origin_id"] for u in fragments}) == 1
    refs = [u["id"] for u in units]
    body = assemble_body(
        "d",
        [
            dict(
                section_title="正文",
                result=dict(
                    elements=[dict(type="paragraph", text="正文", source_refs=refs)], excluded=[]
                ),
            )
        ],
        units,
        "学习笔记",
    )
    assert set(body["content"][-1]["attrs"]["source_refs"]) == {u["origin_id"] for u in units}


def test_batch_size_is_bounded_by_text_and_cache_includes_chapter_context():
    units = [unit(str(i), "字" * 4000) for i in range(10)]
    section = dict(title="服务配置", level=1, block_ids=[u["id"] for u in units], note="理解配置")
    args = dict(
        max_blocks=24,
        user_id="u",
        document_id="d",
        source_hash="h",
        model_config={},
        prompt_version="v2",
    )
    batches = build_batches([section], units, **args)
    assert len(batches) == 3
    assert all(sum(len(u["text"]) for u in b["units"]) <= MAX_SECTION_CHARS for b in batches)
    changed = build_batches([{**section, "note": "比较配置"}], units, **args)
    assert batches[0]["cache_key"] != changed[0]["cache_key"]


def test_navigation_exclusion_does_not_allow_dropping_independent_knowledge():
    by_id = {"a": unit()}
    assert exclusion_errors([dict(block_id="a", reason="不重要", category="navigation")], by_id)
    assert exclusion_errors([dict(block_id="a", reason="不可读", category="unreadable")], by_id)


def test_review_and_summary_packaging_is_integrated_into_real_chapters():
    units = [
        unit("config", "配置需要保留端口 8080"),
        unit("review", "配置时应指定端口"),
        {**unit("heading", "本章小结"), "kind": "title"},
    ]
    outline = dict(
        sections=[
            dict(title="服务配置", block_ids=["config"]),
            dict(title="本章知识小结", block_ids=["review", "heading"]),
        ],
        excluded=[],
    )
    out = consolidate_outline(outline, units)
    assert [s["title"] for s in out["sections"]] == ["服务配置"]
    assert out["sections"][0]["block_ids"] == ["config", "review"]
    assert [e["block_id"] for e in out["excluded"]] == ["heading"]


def test_model_aliases_resolve_to_original_source_ids():
    from final_review.material_note_polish import repair_batch, repair_outline

    units = [unit("native-long-id-abc"), unit("notes-long-id-def")]
    out, _ = repair_outline(
        dict(
            document_title="配置",
            sections=[dict(title="配置", block_ids=["b1", "b2"])],
            excluded=[],
        ),
        units,
    )
    assert out["sections"][0]["block_ids"] == [u["id"] for u in units]
    draft = repair_batch(
        dict(elements=[dict(type="paragraph", text="配置", source_refs=["b1", "b2"])], excluded=[]),
        batch(units),
    )
    assert draft["elements"][0]["source_refs"] == [u["id"] for u in units]


def test_teaching_wrapper_is_split_without_losing_facts_or_original_identity():
    text = (
        "本节课重点讲解以下内容：\n服务部署的两种方式：create 与 stack deploy。\n配置管理与滚动升级"
    )
    page = _synthetic_page(1, None, [text])
    units = source_units(classify_blocks([page]), "hash")
    assert len(units) == 3
    assert "".join(u["text"] for u in units) == text
    assert len({u["origin_id"] for u in units}) == 1
    assert len({u["id"] for u in units}) == 3


def test_model_summary_notation_and_unused_nulls_are_safe_rich_text():
    from final_review.material_note_polish import PolishElement

    node = PolishElement.model_validate(
        dict(type="summary", text="归纳内容", items=None, rows=None, headers=None)
    )
    assert node.type == "paragraph" and node.content_role == "summary"
    assert node.items == [] and node.rows == []


def test_explicit_polish_model_uses_selected_provider_credentials_and_address():
    from final_review.config import ChatModelConfig
    from final_review.llm import build_polish_review_model
    from final_review.material_note_polish import polish_model_config

    settings = Settings(
        _env_file=None,
        llm_api_key="legacy-key",
        llm_base_url="https://legacy.invalid/v1",
        material_note_polish_model_id="provider",
        material_note_polish_model="quality-model",
        chat_models=[
            ChatModelConfig(
                id="provider",
                label="文本提供商",
                model="fast-model",
                base_url="https://provider.invalid/v1",
                api_key="provider-key",
            )
        ],
    )
    model = build_polish_review_model(settings).model
    assert model.model_name == "quality-model"
    assert model.openai_api_base == "https://provider.invalid/v1"
    assert model.openai_api_key.get_secret_value() == "provider-key"
    assert polish_model_config(settings)["base_url"] == "https://provider.invalid/v1"


def test_outline_exclusions_participate_in_final_coverage():
    units = [unit(), unit("nav", "目录")]
    entries = [dict(section_title="配置", result=result())]
    excluded = [dict(block_id="nav", reason="目录导航", category="navigation")]
    assert final_coverage(entries, units, outline_excluded=excluded) == []


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("orderedList", dict(items=["默认端口为 9090。"])),
        ("table", dict(headers=["项目", "值"], rows=[["端口", "9090"]])),
        ("paragraph", dict(text="默认端口为 9090。")),
    ],
)
def test_numbers_are_checked_per_element_including_lists_and_tables(kind, fields):
    units = [unit(), unit("b", "另一个服务端口为 9090")]
    data = dict(
        elements=[
            dict(type=kind, source_refs=["a"], **fields),
            dict(type="paragraph", text="另一个服务端口为 9090", source_refs=["b"]),
        ],
        excluded=[],
    )
    assert any("9090" in e for e in check_batch(data, batch(units)))


def test_tables_bold_code_and_explanation_roles_survive_edit_save():
    units = [unit()]
    data = dict(
        elements=[
            dict(
                type="table",
                headers=["项目", "说明"],
                rows=[["**端口**", "使用 `8080`"]],
                source_refs=["a"],
                content_role="source",
            ),
            dict(
                type="paragraph",
                text="端口可理解为服务的入口。",
                source_refs=["a"],
                content_role="explanation",
            ),
        ],
        excluded=[],
    )
    assert check_batch(data, batch(units)) == []
    body = assemble_body("d", [dict(section_title="配置", result=data)], units, "学习笔记")
    assert validate_body(body, body) == body
    table = next(n for n in body["content"] if n["content"][0]["type"] == "table")
    cell = table["content"][0]["content"][1]["content"][0]["content"][0]["content"][0]
    assert cell["marks"] == [dict(type="bold")]
    assert body["content"][-1]["attrs"]["content_role"] == "explanation"
    forged = deepcopy(body["content"][-1])
    forged["attrs"]["block_id"] = "user-new"
    saved = validate_body(dict(type="doc", content=[forged]), dict(type="doc", content=[]))
    assert saved["content"][0]["attrs"]["content_origin"] == "user_added"
    assert "content_role" not in saved["content"][0]["attrs"]


def test_malformed_table_is_rejected_before_publish_and_on_save():
    data = dict(
        elements=[dict(type="table", headers=["a", "b"], rows=[["one"]], source_refs=["a"])],
        excluded=[],
    )
    assert any("等宽" in e for e in check_batch(data, batch([unit()])))
    body = assemble_body("d", [dict(section_title="配置", result=data)], [unit()], "笔记")
    with pytest.raises(ValueError, match="行宽"):
        validate_body(body, body)


def test_multiple_batches_share_one_chapter_heading_and_exact_repeats_merge_refs():
    units = [unit(), unit("b")]
    body = assemble_body(
        "d",
        [
            dict(section_title="配置", section_index=0, result=result("a")),
            dict(section_title="配置", section_index=0, result=result("b")),
        ],
        units,
        "学习笔记",
    )
    headings = [node_text(n) for n in body["content"] if n["content"][0]["type"] == "heading"]
    assert headings == ["学习笔记", "配置"]
    assert body["content"][-1]["attrs"]["source_refs"] == ["a", "b"]


def test_even_legacy_review_setting_runs_semantic_review():
    class Model:
        def __init__(self):
            self.calls = []

        def structured(self, schema, task, data):
            self.calls.append(schema.__name__)
            return result() if schema.__name__ == "PolishBatch" else dict(supported=True, issues=[])

    model = Model()
    draft, _ = worker._run_batch(
        model, "笔记", batch([unit()]), "", "", {}, review_mode="on_check_failed"
    )
    assert draft
    assert model.calls == ["PolishBatch", "PolishBatchReview"]


def test_bounded_repair_changes_only_the_invalid_element():
    good = result()
    bad = deepcopy(good)
    bad["elements"][0]["text"] = "默认端口为 9090。"
    untouched = dict(
        type="paragraph", text="这是服务的入口。", source_refs=["a"], content_role="explanation"
    )
    bad["elements"].append(untouched)

    class Model:
        def __init__(self):
            self.calls = []

        def structured(self, schema, task, data):
            self.calls.append(schema.__name__)
            if schema.__name__ == "PolishBatch":
                return bad
            if schema.__name__ == "PolishBatchRepair":
                return dict(changes=[dict(index=0, replacement=good["elements"][0])], excluded=[])
            return dict(supported=True, issues=[])

    model = Model()
    repaired, _ = worker._run_batch(
        model, "笔记", batch([unit()]), "", "", {}, review_mode="always"
    )
    assert repaired["elements"][0]["text"] == "默认端口为 **8080**。"
    assert repaired["elements"][1] == untouched
    assert model.calls == [
        "PolishBatch",
        "PolishBatchReview",
        "PolishBatchRepair",
        "PolishBatchReview",
    ]


def test_truncated_subdivisions_remain_reusable_after_parent_row_is_reindexed():
    units = [unit(str(i)) for i in range(4)]
    parent = batch(units)
    args = dict(user_id="u", document_id="d", source_hash="h", model_config={}, prompt_version="v2")
    split = worker._split_failed_batches([parent], {"cache": dict(error="output_limit")}, **args)
    previous = {
        child["cache_key"]: dict(status="done", block_ids=[u["id"] for u in child["units"]])
        for child in split
    }
    retried = worker._split_failed_batches([parent], previous, **args)
    assert [b["cache_key"] for b in retried] == [b["cache_key"] for b in split]


def test_source_gaps_are_concentrated_in_one_notice_without_empty_chapters():
    units = [unit("a", "配置示例："), unit("b", "部署服务：")]
    results = [
        dict(
            section_title="空章节",
            result=dict(
                elements=[],
                excluded=[
                    dict(block_id=u["id"], reason="只有标题，命令在截图中", category="source_gap")
                    for u in units
                ],
            ),
        )
    ]
    assert final_coverage(results, units) == []
    body = assemble_body("d", results, units, "学习笔记")
    assert "空章节" not in node_text(body)
    assert node_text(body).count("原文补充说明") == 1


def test_success_is_checkpointed_even_when_a_sibling_batch_fails(monkeypatch):
    units = [unit(), unit("b")]
    outline = dict(
        document_title="笔记",
        sections=[
            dict(title="配置", level=1, block_ids=["a"]),
            dict(title="第二节", level=1, block_ids=["b"]),
        ],
        excluded=[],
    )
    calls = []

    class Store:
        def polish_job_context(self, job):
            return dict(
                document=dict(document_id="d", content_sha256="h", title="笔记"), units=units
            )

        def load_polish_batches(self, job):
            return []

        def prepare_polish_batches(self, job, batches):
            return [dict(batch_index=i, status="pending", result=None) for i in range(len(batches))]

        def update_polish_job(self, *args, **kwargs):
            return True

        def checkpoint_polish_batch(self, job, index, **kwargs):
            calls.append(("checkpoint", index, kwargs))
            return True

        def fail_polish_job(self, job, **kwargs):
            calls.append(("failed", kwargs))

    monkeypatch.setattr(worker, "_plan_outline", lambda *args: outline)
    monkeypatch.setattr(worker, "build_polish_review_model", lambda *args: SimpleNamespace())
    monkeypatch.setattr(
        worker,
        "_run_batch",
        lambda model, title, b, *args, **kwargs: (
            (result(), dict(supported=True, issues=[]))
            if b["units"][0]["id"] == "a"
            else (None, dict(supported=False, issues=[]))
        ),
    )
    worker._process_polish_job(
        Store(),
        dict(user_id="u", document_id="d", job_id="j", attempts=1),
        Settings(
            _env_file=None,
            llm_api_key="test",
            material_note_polish_model_id="default",
            chat_models=[],
        ),
        None,
        {},
    )
    assert any(c[0] == "checkpoint" and c[1] == 0 and c[2].get("result") for c in calls)
    assert calls[-1][0] == "failed"
