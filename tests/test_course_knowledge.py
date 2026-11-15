import re

import pytest

from final_review.knowledge import (
    Comparison,
    Extraction,
    Support,
    dedup_internal,
    normalize,
    split_blocks,
    verify_facts,
)


class KnowledgeModel:
    """Scripted semantic decisions; persistence/concurrency use real PostgreSQL."""

    def __init__(self, specs=None):
        self.specs = specs or []
        self.extractions = 0
        self.fail_at = None

    @staticmethod
    def semantic(text):
        aliases = {
            "HTTP是无状态协议。": "stateless",
            "HTTP协议不保存请求间状态。": "stateless",
            "HTTP默认端口为80。": "http-port",
            "HTTP的默认服务端口是80。": "http-port",
        }
        return aliases.get(text, normalize(text))

    def structured(self, schema, task, data, **kwargs):
        if schema is Extraction:
            self.extractions += 1
            if self.extractions == self.fail_at:
                raise RuntimeError("provider failed")
            locator = re.search(r"证据ID:([^ ]+)", data["content"])[1]
            specs = self.specs(data["content"]) if callable(self.specs) else self.specs
            return Extraction(
                points=[
                    dict(
                        name=name,
                        description=description,
                        importance_score=0.8,
                        facts=[
                            dict(
                                fact_key=f"f{i}",
                                fact_text=text,
                                evidence=[dict(locator_id=locator, quote=text)],
                            )
                            for i, text in enumerate(facts)
                        ],
                    )
                    for name, description, facts in specs
                ]
            ).model_dump(mode="json")
        if schema is Support:
            return Support(support_status="supported", reason="测试中的确定性语义核验").model_dump()
        if schema is Comparison:
            left, right = data["incoming"], data["existing"]
            same = left["description"] == right["description"]
            conflict = any("是有状态协议" in x["fact_text"] for x in left["facts_json"]["facts"])
            pairs = [
                dict(incoming_key=a["fact_key"], existing_key=b["fact_key"])
                for a in left["facts_json"]["facts"]
                for b in right["facts_json"]["facts"]
                if self.semantic(a["fact_text"]) == self.semantic(b["fact_text"])
            ]
            return Comparison(
                relation="equivalent" if same else "narrower",
                conflict=conflict,
                equivalent_facts=pairs,
                reason="测试中的明确概念与事实匹配",
            ).model_dump(mode="json")
        raise AssertionError(schema)


class KnowledgeEmbeddings:
    def embed_documents(self, texts):
        return [[1.0, 0.1, 0.2] for _ in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def test_normalization_keeps_granularity_and_operators():
    assert normalize(" HTTP  （ 无状态 ） ") == normalize("http(无状态)")
    assert normalize("三次握手") != normalize("三次握手的应用")
    assert normalize("C++") != normalize("C")
    assert normalize("x > 0") != normalize("x < 0")


def test_knowledge_model_has_independent_bounded_request_budget():
    from final_review.config import ChatModelConfig, Settings
    from final_review.llm import build_review_model

    settings = Settings(
        _env_file=None,
        model_timeout=60,
        knowledge_model_timeout=180,
        knowledge_max_output_tokens=8192,
    )
    config = ChatModelConfig(
        id="test", label="test", model="test", base_url="https://example.test/v1", api_key="test"
    )
    model = build_review_model(config, settings, knowledge_generation=True).model
    assert model.request_timeout == 180
    assert model.max_tokens == 8192
    assert model.max_retries == 0
    assert settings.model_timeout == 60


def test_blocks_span_pages_but_respect_explicit_sections():
    locators = [
        dict(
            locator_id=str(i),
            locator_kind="chunk",
            ordinal=i,
            data=dict(content=text, position=i, position_kind="slide"),
        )
        for i, text in enumerate(["## HTTP", "HTTP 的性质", "HTTP 的应用", "## TCP"], 1)
    ]
    blocks = split_blocks(locators)
    assert [b["source_locator_ids"] for b in blocks] == [["1", "2", "3"], ["4"]]
    assert (blocks[0]["page_start"], blocks[0]["page_end"]) == (1, 3)


def test_invalid_or_out_of_block_citations_never_become_supported():
    point = Extraction(
        points=[
            dict(
                name="HTTP",
                description="协议",
                importance_score=0.5,
                facts=[
                    dict(
                        fact_key="f1",
                        fact_text="无状态",
                        evidence=[
                            dict(locator_id="foreign", quote="无状态"),
                            dict(locator_id="ok", quote="伪造引文"),
                        ],
                    )
                ],
            )
        ]
    )
    facts = verify_facts(
        KnowledgeModel(),
        point.points[0],
        {"source_locator_ids": ["ok"]},
        {"foreign": {"data": {"content": "无状态"}}, "ok": {"data": {"content": "HTTP 无状态"}}},
        "v1",
    )
    assert facts["facts"][0]["status"] == "pending"
    assert all(not e["quote_verified"] for e in facts["facts"][0]["evidence"])


def test_semantic_review_gets_full_context_for_substring_quote():
    class Reviewer(KnowledgeModel):
        def structured(self, schema, task, data, **kwargs):
            assert schema is Support
            assert data["source_context"] == "错误说法：HTTP是有状态协议。"
            return Support(support_status="unsupported", reason="上下文标为错误说法")

    point = Extraction(
        points=[
            dict(
                name="HTTP",
                description="协议",
                importance_score=0.5,
                facts=[
                    dict(
                        fact_key="f1",
                        fact_text="HTTP是有状态协议。",
                        evidence=[dict(locator_id="a", quote="HTTP是有状态协议。")],
                    )
                ],
            )
        ]
    )
    result = verify_facts(
        Reviewer(),
        point.points[0],
        {"source_locator_ids": ["a"]},
        {"a": {"data": {"content": "错误说法：HTTP是有状态协议。"}}},
        "v",
    )
    assert result["facts"][0]["status"] == "pending"


def test_internal_dedup_preserves_evidence_and_retries_are_idempotent():
    def candidate(cid, text):
        return dict(
            id=cid,
            name="HTTP",
            normalized_name="http",
            description="HTTP无状态性",
            status="pending",
            embedding=[1, 0, 0],
            facts_json={
                "schema_version": 1,
                "facts": [
                    dict(
                        fact_key="f1",
                        fact_text=text,
                        normalized_text=normalize(text),
                        status="supported",
                        evidence=[dict(locator_id=cid, support_status="supported")],
                    )
                ],
            },
        )

    result = dedup_internal(
        KnowledgeModel(),
        [candidate("a", "HTTP是无状态协议。"), candidate("b", "HTTP协议不保存请求间状态。")],
        5,
        0.65,
    )
    assert [c["status"] for c in result] == ["pending", "internal_merged"]
    assert len(result[0]["facts_json"]["facts"]) == 1
    assert len(result[0]["facts_json"]["facts"][0]["evidence"]) == 2
    again = dedup_internal(KnowledgeModel(), result, 5, 0.65)
    assert again == result


@pytest.mark.parametrize("vector", [[0, 0, 0], [1, float("nan"), 0], [1, 0]])
def test_rejects_invalid_vectors(vector):
    from final_review.knowledge import validate_embedding

    with pytest.raises(ValueError):
        validate_embedding(vector, 3)
