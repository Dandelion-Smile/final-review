import copy
from threading import Barrier, Event, Lock

import pytest

from final_review.config import Settings
from final_review.knowledge import Comparison, Support, dedup_internal
from final_review.knowledge_matching import AtomicSplit, AuditedMatcher, SplitCoverage
from final_review.pipeline_runtime import embedding_batches, resource_slot, run_queue


def candidate(cid, facts, name="概念"):
    return dict(
        id=cid,
        name=name,
        normalized_name=name,
        description="定义",
        status="pending",
        importance_score=0.5,
        embedding=[1, 0, 0],
        facts_json={
            "facts": [
                dict(
                    fact_key=key,
                    fact_text=text,
                    normalized_text=text,
                    status="supported",
                    evidence=[
                        dict(
                            locator_id="c1",
                            quote="变更写入可写层，并隐藏旧文件。",
                            quote_verified=True,
                            support_status="supported",
                            material_version_id="v",
                            verification_method="llm",
                        )
                    ],
                )
                for key, text in facts
            ]
        },
    )


class AuditMemory:
    def __init__(self):
        self.cache = {}
        self.records = []
        self.saved = []

    def match_decision(self, job, incoming, target, phase, fingerprint, version):
        return self.cache.get((incoming["id"], target["id"], phase, fingerprint, version))

    def record_match(
        self, job, incoming, target, phase, fingerprint, version, data, decision, status, error=None
    ):
        self.records.append(dict(status=status, error=error, decision=decision))
        if status == "succeeded":
            self.cache[(incoming["id"], target["id"], phase, fingerprint, version)] = decision

    def save_candidate_facts(self, job, incoming):
        self.saved.append(copy.deepcopy(incoming))

    def locators(self, job):
        return [dict(locator_id="c1", data={"content": "变更写入可写层，并隐藏旧文件。"})]


def test_embedding_service_limit_and_order():
    calls = []

    class Embeddings:
        def embed_documents(self, texts):
            assert len(texts) <= 10
            calls.append(len(texts))
            return [[int(text)] for text in texts]

    output = []
    for _, vectors in embedding_batches(
        Embeddings(),
        [str(i) for i in range(23)],
        settings=Settings(_env_file=None, database_url=""),
    ):
        output.extend(vectors)
    assert calls == [10, 10, 3]
    assert output == [[i] for i in range(23)]


def test_compound_fact_repaired_and_reverified_then_cached():
    repo = AuditMemory()

    class Model:
        calls = 0

        def structured(self, schema, task, data, **kwargs):
            self.calls += 1
            if schema is Comparison:
                assert all(
                    f["fact_key"].startswith("L") for f in data["incoming"]["facts_json"]["facts"]
                )
                n = len(data["incoming"]["facts_json"]["facts"])
                pairs = [
                    dict(incoming_key="L1", existing_key="R1"),
                    dict(incoming_key="L1" if n == 1 else "L2", existing_key="R2"),
                ]
                return dict(
                    relation="equivalent", conflict=False, equivalent_facts=pairs, reason="同义"
                )
            if schema is AtomicSplit:
                return dict(parts=["变更写入可写层。", "旧文件被隐藏。"])
            if schema is SplitCoverage:
                return dict(
                    preserves_all_claims=True,
                    adds_no_claims=True,
                    preserves_conditions=True,
                    independently_testable=True,
                    reason="覆盖完整",
                )
            if schema is Support:
                return dict(support_status="supported", reason="原文支持")
            raise AssertionError(schema)

    model = Model()
    matcher = AuditedMatcher(model, repo, {"material_version_id": "v"})
    incoming = candidate("a", [("compound-long-id", "变更写入可写层，并隐藏旧文件。")])
    target = candidate("b", [("write", "变更写入可写层。"), ("hide", "旧文件被隐藏。")])
    result = matcher.compare_points(incoming, target)
    assert len(incoming["facts_json"]["facts"]) == 2
    assert len(incoming["facts_json"]["raw_facts"]) == 1
    assert all(f["status"] == "supported" for f in incoming["facts_json"]["facts"])
    assert len(result.equivalent_facts) == 2
    assert repo.records[0]["error"] == "one_to_many"
    assert repo.saved
    before = model.calls
    assert matcher.compare_points(incoming, target) == result
    assert model.calls == before


def test_identical_repeated_edges_are_harmless():
    class Model:
        def structured(self, *args, **kwargs):
            pair = dict(incoming_key="L1", existing_key="R1")
            return dict(
                relation="equivalent", conflict=False, equivalent_facts=[pair, pair], reason="同义"
            )

    matcher = AuditedMatcher(Model(), AuditMemory(), {})
    assert (
        len(
            matcher.compare_points(
                candidate("a", [("x", "A")]), candidate("b", [("y", "A")])
            ).equivalent_facts
        )
        == 1
    )


def test_invalid_mapping_quarantines_only_affected_candidate():
    class Model:
        calls = 0

        def structured(self, schema, task, data, **kwargs):
            self.calls += 1
            if data["incoming"]["name"] == "坏候选":
                return dict(
                    relation="equivalent",
                    conflict=False,
                    equivalent_facts=[dict(incoming_key="L999", existing_key="R1")],
                    reason="无效编号",
                )
            return dict(
                relation="different", conflict=False, equivalent_facts=[], reason="独立概念"
            )

    repo = AuditMemory()
    matcher = AuditedMatcher(Model(), repo, {})
    result = dedup_internal(
        matcher,
        [
            candidate("a", [("a", "A")], "第一个"),
            candidate("b", [("b", "B")], "坏候选"),
            candidate("c", [("c", "C")], "后续正常"),
        ],
        5,
        0.65,
    )
    assert [c["status"] for c in result] == ["pending", "review_needed", "pending"]
    assert sum(r["status"] == "invalid" for r in repo.records) == 3
    assert any(r["status"] == "succeeded" for r in repo.records)


def test_scheduler_runs_five_files_together_and_isolates_failure():
    jobs = iter(range(5))
    barrier = Barrier(5)
    stop = Event()
    lock = Lock()
    done = []
    active = 0
    peak = 0

    def process(job):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=5)
            if job == 2:
                raise ValueError("single file failed")
        finally:
            with lock:
                active -= 1
                done.append(job)
                if len(done) == 5:
                    stop.set()

    run_queue(lambda: next(jobs, None), process, 5, stop_event=stop, poll_seconds=0.01)
    assert peak == 5
    assert sorted(done) == list(range(5))


def test_global_resource_budget_releases_after_exception():
    settings = Settings(_env_file=None, database_url="", material_conversion_concurrency=1)
    with pytest.raises(ValueError), resource_slot("conversion", settings):
        raise ValueError("conversion failed")
    with resource_slot("conversion", settings):
        pass
