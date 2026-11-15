"""Audited, cached matching with local repair of compound facts and model identifiers."""

import copy

from pydantic import Field, ValidationError

from .knowledge import (
    POLICY,
    Comparison,
    ComparisonPending,
    Contract,
    ExtractedPoint,
    FactPair,
    digest,
    normalize,
    verify_facts,
)

MATCHER_VERSION = "atomic-matcher-v2"


class AtomicSplit(Contract):
    parts: list[str] = Field(min_length=2, max_length=6)


class SplitCoverage(Contract):
    preserves_all_claims: bool
    adds_no_claims: bool
    preserves_conditions: bool
    independently_testable: bool
    reason: str


class MappingError(ValueError):
    def __init__(self, code, message, fact_key=None):
        super().__init__(message)
        self.code, self.fact_key = code, fact_key


def point_input(point):
    return dict(
        name=point["name"],
        description=point["description"],
        facts_json={
            "facts": [
                {k: fact[k] for k in ("fact_key", "fact_text", "normalized_text", "status")}
                for fact in point["facts_json"]["facts"]
            ]
        },
    )


def aliases(point, prefix):
    point = copy.deepcopy(point)
    mapping = {}
    for i, fact in enumerate(point["facts_json"]["facts"], 1):
        key = f"{prefix}{i}"
        mapping[key], fact["fact_key"] = fact["fact_key"], key
    return point, mapping


def decode(result, left, right):
    result = Comparison.model_validate(result)
    used, pairs = {}, []
    for pair in result.equivalent_facts:
        if pair.incoming_key not in left or pair.existing_key not in right:
            raise MappingError("unknown_fact_id", "返回了未提供的事实编号")
        incoming, existing = left[pair.incoming_key], right[pair.existing_key]
        if incoming in used:
            if used[incoming] == existing:
                continue  # Identical repeated edges do not change meaning.
            raise MappingError("one_to_many", "同一事实匹配到多个目标，需要拆分或核对", incoming)
        used[incoming] = existing
        pairs.append(FactPair(incoming_key=incoming, existing_key=existing))
    return result.model_copy(update={"equivalent_facts": pairs})


class AuditedMatcher:
    def __init__(self, base, repo, job):
        self.base, self.repo, self.job = base, repo, job
        self.phase = "dedup_internal"
        self._locators = None

    def _input(self, incoming, existing):
        return dict(incoming=point_input(incoming), existing=point_input(existing))

    def compare_points(self, incoming, existing):
        feedback, repaired = "", False
        for attempt in range(3):
            data = self._input(incoming, existing)
            fingerprint = digest(data)
            cached = self.repo.match_decision(
                self.job, incoming, existing, self.phase, fingerprint, MATCHER_VERSION
            )
            if cached:
                return Comparison.model_validate(cached["mapped"])
            left, lm = aliases(data["incoming"], "L")
            right, rm = aliases(data["existing"], "R")
            raw = {}
            try:
                raw = self.base.structured(
                    Comparison,
                    "比较双方知识点的概念、范围和事实。只有同一概念及粒度才equivalent；"
                    "包含为broader/narrower，相关为related，不同为different。conflict仅指同一条件下"
                    "互相矛盾的结论，不能把用途不同当成矛盾。equivalent_facts只能是一条事实与"
                    "另一条事实完整等价的匹配；A+B不等价于单独A或B。每个L编号最多出现一次。"
                    "只返回输入的L/R编号，不得发明。" + feedback,
                    dict(incoming=left, existing=right),
                    policy=POLICY,
                )
                mapped = decode(raw, lm, rm)
                self.repo.record_match(
                    self.job,
                    incoming,
                    existing,
                    self.phase,
                    fingerprint,
                    MATCHER_VERSION,
                    data,
                    {"raw": raw, "mapped": mapped.model_dump(mode="json")},
                    "succeeded",
                )
                return mapped
            except Exception as exc:
                local = (
                    isinstance(exc, (MappingError, ValidationError))
                    or type(exc).__name__ in {"ModelError", "ModelOutputLimitError"}
                    or "Timeout" in type(exc).__name__
                )
                if not local:
                    raise
                code = getattr(exc, "code", type(exc).__name__)
                self.repo.record_match(
                    self.job,
                    incoming,
                    existing,
                    self.phase,
                    fingerprint,
                    MATCHER_VERSION,
                    data,
                    {"raw": raw, "error": str(exc)[:1000]},
                    "invalid",
                    code,
                )
                feedback = f"\n上次校验失败：{exc}。请重新核对，只使用指定编号。"
                if isinstance(exc, MappingError) and exc.code == "one_to_many" and not repaired:
                    repaired = True
                    try:
                        restored = self._repair(incoming, exc.fact_key)
                    except Exception as repair_error:
                        if (
                            isinstance(repair_error, (ValidationError, MappingError))
                            or type(repair_error).__name__
                            in {"ModelError", "ModelOutputLimitError"}
                            or "Timeout" in type(repair_error).__name__
                        ):
                            restored = False
                            feedback += "；事实拆分校验未通过，保留原事实。"
                        else:
                            raise
                    if restored:
                        feedback = "\n输入复合事实已拆分，重新比较所有事实，保持一对一完整等价。"
        reason = "局部比较三次仍无法通过校验：" + feedback.split("：", 1)[-1][:500]
        self.repo.record_match(
            self.job,
            incoming,
            existing,
            self.phase,
            digest(self._input(incoming, existing)),
            MATCHER_VERSION,
            self._input(incoming, existing),
            {"reason": reason},
            "review_needed",
        )
        raise ComparisonPending(reason)

    def _repair(self, incoming, key):
        original = next(f for f in incoming["facts_json"]["facts"] if f["fact_key"] == key)
        if original.get("source_fact_key"):
            return False  # Do not recursively split a repaired fact indefinitely.
        split = AtomicSplit.model_validate(
            self.base.structured(
                AtomicSplit,
                "把这条复合事实拆成可独立命题的事实，每条一个结论，保留主语、适用条件、"
                "否定、单位及先后关系。不能机械按标点拆分，也不能补充原文没有的知识。",
                {"original": original["fact_text"], "evidence": original["evidence"]},
                policy=POLICY,
            )
        )
        if len({normalize(p) for p in split.parts}) != len(split.parts):
            return False
        coverage = SplitCoverage.model_validate(
            self.base.structured(
                SplitCoverage,
                "核对拆分是否完整保留原事实且没有新增结论，条件和否定是否保留，"
                "每条是否可以独立理解、独立判断。",
                {"original": original["fact_text"], "parts": split.parts},
                policy=POLICY,
            )
        )
        if not all(
            (
                coverage.preserves_all_claims,
                coverage.adds_no_claims,
                coverage.preserves_conditions,
                coverage.independently_testable,
            )
        ):
            return False
        if self._locators is None:
            self._locators = {x["locator_id"]: x for x in self.repo.locators(self.job)}
        refs = [
            {"locator_id": e["locator_id"], "quote": e["quote"]}
            for e in original["evidence"]
            if e["quote_verified"]
        ]
        if not refs:
            return False
        point = ExtractedPoint(
            name=incoming["name"],
            description=incoming["description"],
            importance_score=incoming.get("importance_score") or 0.5,
            facts=[
                dict(
                    fact_key=digest([key, text, MATCHER_VERSION])[:24],
                    fact_text=text,
                    evidence=refs,
                )
                for text in split.parts
            ],
        )
        verified = verify_facts(
            self.base,
            point,
            {"source_locator_ids": [e["locator_id"] for e in refs]},
            self._locators,
            self.job["material_version_id"],
        )
        if any(f["status"] != "supported" for f in verified["facts"]):
            return False
        raw = incoming["facts_json"].setdefault(
            "raw_facts", copy.deepcopy(incoming["facts_json"]["facts"])
        )
        assert raw
        for part in verified["facts"]:
            part["source_fact_key"] = key
            part["normalizer_version"] = MATCHER_VERSION
        incoming["facts_json"]["facts"] = [
            f for f in incoming["facts_json"]["facts"] if f["fact_key"] != key
        ] + verified["facts"]
        incoming["facts_json"]["schema_version"] = 2
        incoming["facts_json"].setdefault("normalizations", []).append(
            {
                "source_fact_key": key,
                "part_keys": [p["fact_key"] for p in verified["facts"]],
                "version": MATCHER_VERSION,
                "coverage": coverage.model_dump(mode="json"),
            }
        )
        self.repo.save_candidate_facts(self.job, incoming)
        return True
