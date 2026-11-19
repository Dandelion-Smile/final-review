"""Internal incremental catalog engine; never scans queues or edits old chapters."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.callbacks import BaseCallbackHandler
from psycopg.pq import TransactionStatus
from pydantic import ValidationError

from .knowledge import POLICY, digest, normalize
from .knowledge_catalog_contracts import PlannedChapter
from .knowledge_catalog_incremental_contracts import (
    ApprovedChapter,
    ExistingMatches,
    GrowthResult,
    IncrementalCallRecord,
    IncrementalCheckpoint,
    NewTopics,
    SemanticBaseline,
    TopicReview,
)
from .knowledge_catalog_store import (
    SNAPSHOT_CONTRACT,
    CatalogAssignment,
    CatalogChapter,
    CatalogConflict,
    CatalogInvalid,
    CatalogLeaseLost,
    CatalogRepository,
    CatalogSnapshotChanged,
)
from .knowledge_catalog_worker import (
    CatalogBudgetExceeded,
    CatalogCheckpointInvalid,
    CatalogOutputInvalid,
    _retryable,
    model_identity,
)
from .llm import ModelError, ReviewModel

RULES_VERSION = "catalog-incremental-v1"


class CatalogNoChanges(CatalogInvalid):
    """No publish needed; scheduler should avoid creating this unchanged task."""


def semantic_projection(point: dict) -> dict:
    """Use COMPLETE snapshot rows, never the excerpted compact_points projection."""
    rows = point["input"]["rows"]
    if point["input"]["contract"] != SNAPSHOT_CONTRACT or not rows:
        raise CatalogInvalid("complete catalog input rows are required")
    names = {normalize(row["name"]) for row in rows}
    descriptions = {normalize(row["description"] or "") for row in rows}
    if len(names) != 1 or len(descriptions) != 1:
        raise CatalogInvalid("inconsistent point fields in snapshot")
    return {
        "contract": "catalog-semantic-projection-v1",
        "name": names.pop(),
        "description": descriptions.pop(),
        "facts": sorted({normalize(row["fact_text"]) for row in rows}),
    }


def full_points(snapshot: dict) -> list[dict]:
    """Complete semantic model inputs, with stable IDs and no source hints."""
    if snapshot["contract"] != SNAPSHOT_CONTRACT:
        raise CatalogInvalid("unsupported snapshot contract")
    points = [
        {"point_id": str(p["point_id"]), **semantic_projection(p)}
        for p in sorted(snapshot["points"], key=lambda p: str(p["point_id"]))
    ]
    if len({p["point_id"] for p in points}) != len(points):
        raise CatalogInvalid("duplicate visible point")
    return points


@dataclass(frozen=True)
class IncrementalDelta:
    """Disjoint sets: new > changed > unassigned > carried; removed is historical."""

    new_points: frozenset[str]
    changed_points: frozenset[str]
    unassigned_points: frozenset[str]
    removed_points: frozenset[str]
    carried_points: frozenset[str]
    inherited: tuple[CatalogAssignment, ...]
    semantic_hashes: dict[str, str]

    @property
    def pending(self) -> frozenset[str]:
        return self.new_points | self.changed_points | self.unassigned_points


def identify_delta(
    version: dict, snapshot: dict, baseline: SemanticBaseline | None = None
) -> IncrementalDelta:
    """Pure read-only Step 1. No repository writes or model dependency."""
    points = {p["point_id"]: p for p in full_points(snapshot)}
    hashes = {
        key: digest({k: v for k, v in p.items() if k != "point_id"}) for key, p in points.items()
    }
    current = {str(p["point_id"]): p for p in snapshot["points"]}
    old = {str(a["point_id"]): a for a in version["assignments"]}
    if len(old) != len(version["assignments"]):
        raise CatalogInvalid("duplicate historical assignment")
    if baseline is not None and (
        baseline.version_id != str(version["id"])
        or baseline.snapshot_hash != version["knowledge_snapshot_hash"]
        or set(baseline.point_hashes) != set(old)
    ):
        raise CatalogInvalid("semantic baseline is not pinned to the base version")
    new, changed, unassigned, carried = set(current) - set(old), set(), set(), set()
    inherited = []
    chapter_ids = {str(c["chapter_id"]) for c in version["chapters"]}
    for key in sorted(set(current) & set(old)):
        previous, point = old[key], current[key]
        chapter = previous["chapter_id"]
        if chapter is not None and str(chapter) not in chapter_ids:
            raise CatalogInvalid("historical assignment references another version")
        full_changed = previous["classification_input_hash"] != point["classification_input_hash"]
        semantic_same = baseline is not None and baseline.point_hashes[key] == hashes[key]
        if full_changed and not semantic_same:
            changed.add(key)
        elif chapter is None:
            unassigned.add(key)
        else:
            carried.add(key)
            inherited.append(
                CatalogAssignment(
                    point_id=UUID(key),
                    chapter_id=chapter,
                    classification_input_hash=point["classification_input_hash"],
                    assignment_method="carried",
                    reason="unchanged semantic input",
                    sort_order=previous["sort_order"],
                )
            )
    return IncrementalDelta(
        frozenset(new),
        frozenset(changed),
        frozenset(unassigned),
        frozenset(set(old) - set(current)),
        frozenset(carried),
        tuple(inherited),
        hashes,
    )


@dataclass(frozen=True)
class IncrementalConfig:
    batch_size: int = 40
    input_chars: int = 60_000
    max_calls: int = 48
    max_request_attempts: int = 192
    call_attempts: int = 2
    max_job_attempts: int = 3
    lease_seconds: int = 900
    retry_seconds: int = 30
    min_topic_points: int = 3
    max_new_chapters: int = 2
    max_chapters: int = 80

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in asdict(self).values()):
            raise ValueError("incremental limits must be positive integers")
        if self.batch_size > 40 or self.call_attempts > 3 or self.max_chapters > 80:
            raise ValueError("incremental limits exceed contract bounds")
        if not 3 <= self.min_topic_points <= 160 or self.max_new_chapters > 2:
            raise ValueError("new topics need at least three points; at most two new chapters")


def chapter_keys(chapters: list[dict]) -> dict[str, dict]:
    """Stable UUID order for keys; database display order is never rewritten."""
    ids = [str(c["chapter_id"]) for c in chapters]
    if len(set(ids)) != len(ids) or len(chapters) > 80:
        raise CatalogInvalid("invalid fixed chapter set")
    return {
        f"c{i + 1}": c for i, c in enumerate(sorted(chapters, key=lambda c: str(c["chapter_id"])))
    }


def validate_matches(result: ExistingMatches, points: list[dict], chapters: dict):
    ids = [item.point_id for item in result.matches]
    if len(ids) != len(set(ids)) or set(ids) != {p["point_id"] for p in points}:
        raise CatalogOutputInvalid("match IDs must exactly cover the batch")
    if any(a.chapter_key is not None and a.chapter_key not in chapters for a in result.matches):
        raise CatalogOutputInvalid("unknown fixed chapter key")


def validate_topics(result: NewTopics, points: list[dict]):
    allowed = {p["point_id"] for p in points}
    members = [pid for topic in result.topics for pid in topic.point_ids]
    if len(set(members)) != len(members) or not set(members) <= allowed:
        raise CatalogOutputInvalid("new topics require unique unmatched member IDs")
    keys = [topic.key for topic in result.topics]
    names = [normalize(topic.title) for topic in result.topics]
    if len(keys) != len(set(keys)) or len(names) != len(set(names)):
        raise CatalogOutputInvalid("duplicate new topic key or title")
    for topic in result.topics:
        PlannedChapter(key="c1", title=topic.title, description=topic.description, sort_order=0)


def validate_review(result: TopicReview, targets: set[str]):
    keys = [r.target_key for r in result.relations]
    if len(keys) != len(set(keys)) or set(keys) != targets:
        raise CatalogOutputInvalid("review must explicitly compare every old and peer chapter")


def validate_candidate(version, snapshot, delta, chapters, assignments):
    """Business protection missing from Repository: preserve every parent chapter."""
    expected_old = {
        str(c["chapter_id"]): CatalogChapter(**{k: c[k] for k in CatalogChapter.model_fields})
        for c in version["chapters"]
    }
    actual = {str(c.chapter_id): c for c in chapters}
    if len(actual) != len(chapters) or any(actual.get(k) != c for k, c in expected_old.items()):
        raise CatalogInvalid("all parent chapters must be preserved verbatim")
    names = [normalize(c.title) for c in chapters]
    if len(set(names)) != len(names) or any(not name for name in names):
        raise CatalogInvalid("duplicate or blank chapter title")
    if sorted(c.sort_order for c in chapters) != list(range(len(chapters))):
        raise CatalogInvalid("chapter order must remain contiguous from zero")
    expected = {p["point_id"]: p["classification_input_hash"] for p in snapshot["points"]}
    found = {str(a.point_id): a for a in assignments}
    if len(found) != len(assignments) or set(found) != set(expected):
        raise CatalogInvalid("V2 must cover exactly all currently visible points")
    for key, assignment in found.items():
        if assignment.classification_input_hash != expected[key]:
            raise CatalogSnapshotChanged("assignment uses an obsolete full input hash")
        if assignment.chapter_id is not None and str(assignment.chapter_id) not in actual:
            raise CatalogInvalid("assignment references another course or version")
        if (assignment.chapter_id is None) != (assignment.assignment_method == "unclassified"):
            raise CatalogInvalid("unassigned points must remain explicitly unclassified")
    for inherited in delta.inherited:
        if found[str(inherited.point_id)] != inherited:
            raise CatalogInvalid("unchanged classified points must keep their inherited assignment")


class _UsageObserver(BaseCallbackHandler):
    """Counts visible LangChain starts and tokens, never claims exact HTTP counts."""

    def __init__(self):
        self.starts = 0
        self.tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def on_chat_model_start(self, *args, **kwargs):
        self.starts += 1

    def on_llm_end(self, response, **kwargs):
        for group in response.generations:
            for generation in group:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                for key in self.tokens:
                    self.tokens[key] += int((usage or {}).get(key, 0))


class IncrementalCatalogWorker:
    """Accept an explicitly claimed incremental task; never claim/create internally."""

    def __init__(
        self, repository: CatalogRepository, model, *, config: IncrementalConfig | None = None
    ):
        self.repo, self.model = repository, model
        self.config = config or IncrementalConfig()
        self.identity = model_identity(model)
        self.calls = 0
        self.request_attempts_reserved = 0
        self.call_log: list[dict] = []
        self._save = lambda stage: None
        self._records = None
        self._used_records: set[str] = set()
        self._observer = None
        self.semantic_baseline: SemanticBaseline | None = None
        self._request_multiplier = 0
        if isinstance(model, ReviewModel):
            retries = getattr(model.model, "max_retries", None)
            if type(retries) is not int or retries < 0:
                raise CatalogInvalid("ReviewModel requires a finite SDK retry count")
            # ReviewModel.structured retries parsing at most twice for these schemas.
            # This is a conservative reservation, NOT a measured network count.
            self._request_multiplier = 2 * (1 + retries)
            callbacks = getattr(model.model, "callbacks", None)
            if hasattr(model.model, "model_copy") and (
                callbacks is None or isinstance(callbacks, list)
            ):
                self._observer = _UsageObserver()
                client = model.model.model_copy(
                    update={"callbacks": [*(callbacks or []), self._observer]}
                )
                self.model = ReviewModel(client)

    def _groups(self, points, *, capacity=None):
        groups, group = [], []
        for point in points:
            candidate = [*group, point]
            if (
                len(candidate) > (capacity or self.config.batch_size)
                or len(json.dumps(candidate, ensure_ascii=False)) > self.config.input_chars // 2
            ):
                if not group:
                    raise CatalogBudgetExceeded("one complete point exceeds input budget")
                groups.append(group)
                group = []
            group.append(point)
            if len(json.dumps(group, ensure_ascii=False)) > self.config.input_chars // 2:
                raise CatalogBudgetExceeded("one complete point exceeds input budget")
        if group:
            groups.append(group)
        return groups

    def _call(self, schema, task, data, validate=lambda result: None):
        if len(json.dumps(data, ensure_ascii=False)) > self.config.input_chars:
            raise CatalogBudgetExceeded("model input exceeds configured character limit")
        input_hash = digest(dict(schema=schema.__name__, task=task, data=data))
        self._used_records.add(input_hash)
        if self._records is not None and input_hash in self._records:
            cached = self._records[input_hash]
            try:
                if cached.input_hash != input_hash or cached.schema_name != schema.__name__:
                    raise ValueError("call identity mismatch")
                result = schema.model_validate(cached.result)
                validate(result)
                return result
            except (ValueError, TypeError, KeyError) as exc:
                raise CatalogCheckpointInvalid("invalid incremental call record") from exc
        for attempt in range(self.config.call_attempts):
            if (
                self.calls >= self.config.max_calls
                or self.request_attempts_reserved + self._request_multiplier
                > self.config.max_request_attempts
            ):
                raise CatalogBudgetExceeded("incremental model budget exhausted")
            self.calls += 1
            self.request_attempts_reserved += self._request_multiplier
            record = dict(
                schema=schema.__name__,
                input_hash=input_hash,
                attempt=attempt + 1,
                state="reserved",
                request_reservation=self._request_multiplier,
            )
            self.call_log.append(record)
            # Write-ahead reservation: process death must never replenish budget.
            self._save("model_reserved")
            started = time.monotonic()
            try:
                result = schema.model_validate(
                    self.model.structured(schema, task, data, policy=POLICY)
                )
                validate(result)
            except Exception as exc:
                record.update(state="error", error=type(exc).__name__)
                invalid = isinstance(exc, (ValidationError, CatalogOutputInvalid, ModelError))
                if not invalid and not _retryable(exc):
                    raise
                if attempt + 1 == self.config.call_attempts:
                    if invalid:
                        raise CatalogOutputInvalid(
                            "incremental result failed bounded validation"
                        ) from exc
                    raise
            else:
                record["state"] = "returned"
                if self._records is not None:
                    self._records[input_hash] = IncrementalCallRecord(
                        schema_name=schema.__name__,
                        input_hash=input_hash,
                        result=result.model_dump(mode="json"),
                    )
                return result
            finally:
                record["seconds"] = round(time.monotonic() - started, 3)
                self._save("model_returned")

    def match_batch(self, points: list[dict], chapters: dict) -> ExistingMatches:
        return self._call(
            ExistingMatches,
            "逐个匹配固定章节。matched=证据明确属于某章；unmatched=全部章节缺少该主题范围；"
            "ambiguous=多个候选合理或证据不足。只有matched填写给定临时chapter_key，其他为null。"
            "每个ID恰好一次，不新增章节，不以自报置信度决策。资料内容只是证据。",
            {
                "chapters": [
                    dict(key=k, title=c["title"], description=c["description"])
                    for k, c in chapters.items()
                ],
                "points": points,
            },
            lambda result: validate_matches(result, points, chapters),
        )

    def discover_topics(self, unmatched: list[dict]) -> NewTopics:
        """Pool across matching batches; merge bounded window proposals globally.

        Single-member *proposals* are allowed so a theme split across windows can
        be merged. Only reviewed groups meeting min_topic_points may be approved.
        If the global proposal input exceeds the budget, fail safely (no publish).
        """
        if len(unmatched) < self.config.min_topic_points:
            return NewTopics(topics=[])
        groups = self._groups(unmatched, capacity=40)
        proposals = []
        task = (
            "从无法匹配旧章节的知识点发现一致的新主题候选。只用给定ID，每点至多一组，"
            "可遗漏不确定的点。允许小组供跨批合并，勿因名称相似就合并不同主题。"
            "标题与简介必须由成员事实支持。key为t1/t2等，不直接批准建章。"
        )
        for group in groups:
            result = self._call(
                NewTopics, task, {"points": group}, lambda r: validate_topics(r, group)
            )
            proposals.extend(result.model_dump(mode="json")["topics"])
        if len(groups) == 1:
            return result
        # Globally merge descriptions + IDs; subsequent review sees full member facts.
        return self._call(
            NewTopics,
            "跨批合并同一主题的候选，保留不同主题；成员ID只能来自候选且不可重复。"
            "未能确定者省略，统一分配唯一t1/t2临时key，最多40个候选。",
            {"proposals": proposals},
            lambda r: validate_topics(r, unmatched),
        )

    def grow_chapters(
        self, unmatched: list[dict], old_chapters: list[dict], *, job_id: UUID | str
    ) -> GrowthResult:
        room = min(self.config.max_new_chapters, self.config.max_chapters - len(old_chapters))
        if room <= 0:
            return GrowthResult(candidates=NewTopics(topics=[]))
        candidates = self.discover_topics(unmatched)
        old_keys = chapter_keys(old_chapters)
        points = {p["point_id"]: p for p in unmatched}
        growth = GrowthResult(candidates=candidates)
        names = {normalize(c["title"]) for c in old_chapters}
        order = max((c["sort_order"] for c in old_chapters), default=-1) + 1
        existing_ids = {str(c["chapter_id"]) for c in old_chapters}
        for candidate in sorted(candidates.topics, key=lambda c: normalize(c.title)):
            if len(candidate.point_ids) < self.config.min_topic_points:
                continue
            if normalize(candidate.title) in names:
                continue
            peers = [c.model_dump(mode="json") for c in candidates.topics if c != candidate]
            targets = set(old_keys) | {c["key"] for c in peers}
            review = self._call(
                TopicReview,
                "审查候选是否有一致主题，标题简介是否由全部成员支持，是否构成独立章节。"
                "逐一比较所有旧章节和其他候选：distinct仅用于边界明确独立；同义用equivalent；"
                "包含/被包含用contains/contained；边界重叠用overlap；不确定用uncertain。"
                "旧章节的局部知识集合不能判为独立主题，不能为减少未分章数量放宽标准。",
                {
                    "candidate": candidate.model_dump(mode="json"),
                    "members": [points[pid] for pid in candidate.point_ids],
                    "old_chapters": [
                        dict(key=k, title=c["title"], description=c["description"])
                        for k, c in old_keys.items()
                    ],
                    "peer_candidates": peers,
                },
                lambda r: validate_review(r, targets),
            )
            growth.reviews[candidate.key] = review
            if (
                not all((review.coherent, review.supported, review.independent))
                or any(r.relation != "distinct" for r in review.relations)
                or len(growth.approved) >= room
            ):
                continue
            chapter_id = str(
                uuid5(NAMESPACE_URL, f"catalog-growth:{job_id}:{digest(candidate.model_dump())}")
            )
            if chapter_id in existing_ids:
                raise CatalogInvalid("new chapter UUID collision")
            growth.approved.append(
                ApprovedChapter(
                    topic_key=candidate.key,
                    chapter_id=chapter_id,
                    title=candidate.title,
                    description=candidate.description,
                    sort_order=order + len(growth.approved),
                )
            )
            existing_ids.add(chapter_id)
        return growth

    def _checkpoint(self, job, head, version, snapshot, delta, baseline):
        identity = dict(
            job_id=str(job["id"]),
            course_id=job["course_id"],
            base_version_id=str(version["id"]),
            base_revision=head["revision"],
            snapshot_hash=snapshot["hash"],
            rules_version=RULES_VERSION,
            model_config_hash=digest(self.identity),
            worker_config_hash=digest(asdict(self.config)),
            baseline_hash=digest(baseline.model_dump() if baseline else None),
            semantic_hashes=delta.semantic_hashes,
        )
        if not job["checkpoint"]:
            return IncrementalCheckpoint(**identity)
        try:
            cp = IncrementalCheckpoint.model_validate(job["checkpoint"])
            if any(getattr(cp, key) != value for key, value in identity.items()):
                raise ValueError("checkpoint identity changed")
            if (
                cp.logical_calls != len(cp.call_log)
                or cp.logical_calls > self.config.max_calls
                or cp.request_attempts_reserved > self.config.max_request_attempts
                or cp.request_attempts_reserved != cp.logical_calls * self._request_multiplier
                or len(cp.records) > cp.logical_calls
            ):
                raise ValueError("invalid cumulative budget")
            for record in cp.call_log:
                if record.get("request_reservation") != self._request_multiplier or record.get(
                    "state"
                ) not in {"reserved", "error", "returned"}:
                    raise ValueError("invalid call accounting")
            return cp
        except (ValueError, TypeError, KeyError) as exc:
            raise CatalogCheckpointInvalid("incompatible incremental checkpoint") from exc

    def _match_groups(self, points, chapters, cp, phase):
        matches, completed = [], {}
        for index, group in enumerate(self._groups(points)):
            key = f"{phase}:{index}"
            input_hash = digest(dict(points=group, chapters=chapters))
            if key in cp.completed_batches and cp.completed_batches[key] != input_hash:
                raise CatalogCheckpointInvalid("incremental batch input changed")
            result = self.match_batch(group, chapters)
            matches.extend(result.matches)
            completed[key] = input_hash
            cp.completed_batches[key] = input_hash
            self._save("matching")
        return matches, completed

    @staticmethod
    def _check_scope(version, course):
        """Repository is user-bound; reject malformed cross-scope parent rows too."""
        if version.get("course_id", course) != course:
            raise CatalogInvalid("base version belongs to another course")
        for row in version["chapters"] + version["assignments"]:
            if (
                row.get("course_id", course) != course
                or str(row.get("version_id", version["id"])) != str(version["id"])
                or row.get("user_id", version.get("user_id")) != version.get("user_id")
            ):
                raise CatalogInvalid("parent rows cross user, course or version boundaries")

    def _assemble(self, job, version, snapshot, delta, cp):
        points = [p for p in full_points(snapshot) if p["point_id"] in delta.pending]
        old_chapters = [{k: c[k] for k in CatalogChapter.model_fields} for c in version["chapters"]]
        old_keys = chapter_keys(old_chapters)
        matches, completed = self._match_groups(points, old_keys, cp, "existing")
        unmatched = [m.point_id for m in matches if m.status == "unmatched"]
        if cp.existing_matches and cp.existing_matches != matches:
            raise CatalogCheckpointInvalid("old matches changed")
        if cp.unmatched and cp.unmatched != unmatched:
            raise CatalogCheckpointInvalid("unmatched set changed")
        cp.existing_matches, cp.unmatched = matches, unmatched
        self._save("existing_matched")
        growth = self.grow_chapters(
            [p for p in points if p["point_id"] in set(unmatched)], old_chapters, job_id=job["id"]
        )
        if cp.growth is not None and cp.growth != growth:
            raise CatalogCheckpointInvalid("new topic decisions changed")
        cp.growth = growth
        self._save("topics_reviewed")
        added = [
            dict(
                chapter_id=c.chapter_id,
                title=c.title,
                description=c.description,
                sort_order=c.sort_order,
            )
            for c in growth.approved
        ]
        all_chapters = old_chapters + added
        fixed_keys = chapter_keys(all_chapters)
        resolved = {
            m.point_id: (old_keys[m.chapter_key]["chapter_id"], m.reason)
            for m in matches
            if m.status == "matched"
        }
        residual = [p for p in points if p["point_id"] not in resolved]
        # Re-evaluate only when the fixed set gained approved chapters.
        if added and residual:
            final, more_completed = self._match_groups(residual, fixed_keys, cp, "residual")
            completed.update(more_completed)
            for match in final:
                if match.status == "matched":
                    chapter = fixed_keys[match.chapter_key]["chapter_id"]
                    approved_members = {
                        c.chapter_id: set(
                            next(
                                t.point_ids
                                for t in growth.candidates.topics
                                if t.key == c.topic_key
                            )
                        )
                        for c in growth.approved
                    }
                    # Rejected/excess candidates must not leak into another new chapter.
                    if (
                        str(chapter) in approved_members
                        and match.point_id not in approved_members[str(chapter)]
                    ):
                        continue
                    resolved[match.point_id] = (chapter, match.reason)
        # A semantic proposal alone is insufficient: require three reliable
        # final assignments too. Rejected members remain explicitly unclassified.
        retained = [
            c
            for c in growth.approved
            if sum(str(chapter) == c.chapter_id for chapter, _ in resolved.values())
            >= self.config.min_topic_points
        ]
        start_order = max((c["sort_order"] for c in old_chapters), default=-1) + 1
        retained = [
            c.model_copy(update={"sort_order": start_order + i}) for i, c in enumerate(retained)
        ]
        if cp.final_new_chapters is not None and cp.final_new_chapters != retained:
            raise CatalogCheckpointInvalid("final new chapter support changed")
        cp.final_new_chapters = retained
        removed_ids = {c.chapter_id for c in growth.approved} - {c.chapter_id for c in retained}
        resolved = {
            pid: value for pid, value in resolved.items() if str(value[0]) not in removed_ids
        }
        added = [{k: getattr(c, k) for k in CatalogChapter.model_fields} for c in retained]
        all_chapters = old_chapters + added
        if cp.completed_batches != completed or set(cp.records) != self._used_records:
            raise CatalogCheckpointInvalid("unreachable or incomplete checkpoint records")
        hashes = {p["point_id"]: p["classification_input_hash"] for p in snapshot["points"]}
        assignments = list(delta.inherited)
        for index, point in enumerate(points):
            key = point["point_id"]
            chapter, reason = resolved.get(key, (None, "no reliable fixed chapter match"))
            assignments.append(
                CatalogAssignment(
                    point_id=key,
                    chapter_id=chapter,
                    classification_input_hash=hashes[key],
                    assignment_method="llm" if chapter else "unclassified",
                    reason=reason,
                    sort_order=index,
                )
            )
        chapters = [CatalogChapter(**c) for c in all_chapters]
        if len(added) > self.config.max_new_chapters or len(chapters) > self.config.max_chapters:
            raise CatalogInvalid("chapter count exceeds incremental limits")
        validate_candidate(version, snapshot, delta, chapters, assignments)
        self._save("assembled")
        return chapters, assignments

    def run(self, job: dict, *, semantic_baseline: SemanticBaseline | None = None) -> dict:
        """Run one legitimately claimed task and return its persisted job row.

        Lease loss raises, since a stale owner cannot record failure. Other errors
        use fail(); only transient transport/provider errors enter retry_wait.
        Successful call outputs resume at call boundaries, never mid-request.
        The caller owns persistence of self.semantic_baseline after success.
        """
        if (
            isinstance(self.repo, CatalogRepository)
            and self.repo.connection.info.transaction_status != TransactionStatus.IDLE
        ):
            raise CatalogInvalid("incremental run must start outside a database transaction")
        if job["mode"] != "incremental" or job["base_version_id"] is None:
            raise CatalogInvalid("incremental worker requires an incremental job with a base")
        course = job["course_id"]
        stored = self.repo.job(course, job["id"])
        if not stored:
            raise CatalogLeaseLost("claimed job does not exist in this user scope")
        if any(
            str(stored[k]) != str(job[k])
            for k in ("id", "course_id", "mode", "base_version_id", "snapshot_hash")
        ):
            raise CatalogInvalid("claimed task identity differs from stored task")
        if stored["status"] == "succeeded":
            cp = IncrementalCheckpoint.model_validate(stored["checkpoint"])
            self.calls, self.call_log = cp.logical_calls, cp.call_log
            self.request_attempts_reserved = cp.request_attempts_reserved
            self.semantic_baseline = SemanticBaseline(
                version_id=str(stored["result_version_id"]),
                snapshot_hash=stored["snapshot_hash"],
                point_hashes=cp.semantic_hashes,
            )
            return stored
        if stored["status"] != "running" or stored["lease_token"] != job["lease_token"]:
            raise CatalogLeaseLost("claimed incremental job is no longer owned")
        job = stored
        self.semantic_baseline = None
        self._used_records = set()
        try:
            if job["attempt"] > self.config.max_job_attempts:
                raise CatalogInvalid("incremental job execution attempt limit exceeded")
            current = self.repo.current(course)
            head, version = current["head"], current["version"]
            if (
                not head
                or not version
                or version["id"] != job["base_version_id"]
                or head["current_version_id"] != version["id"]
            ):
                raise CatalogConflict("incremental base is no longer current")
            self._check_scope(version, course)
            snapshot = self.repo.snapshot(course)
            if snapshot["hash"] != job["snapshot_hash"]:
                raise CatalogSnapshotChanged("incremental job snapshot is stale")
            delta = identify_delta(version, snapshot, semantic_baseline)
            if snapshot["hash"] == version["knowledge_snapshot_hash"] and not delta.pending:
                raise CatalogNoChanges("unchanged directory needs no new version")
            cp = self._checkpoint(job, head, version, snapshot, delta, semantic_baseline)
            self.calls, self.call_log = cp.logical_calls, cp.call_log
            self.request_attempts_reserved = cp.request_attempts_reserved
            self._records = cp.records
            previous_starts, previous_tokens = cp.observed_llm_starts or 0, cp.observed_tokens or {}
            if self._observer:
                self._observer.starts = 0
                self._observer.tokens = dict.fromkeys(self._observer.tokens, 0)

            def save(stage):
                cp.logical_calls = self.calls
                cp.request_attempts_reserved = self.request_attempts_reserved
                if self._observer:
                    cp.observed_llm_starts = previous_starts + self._observer.starts
                    cp.observed_tokens = {
                        k: previous_tokens.get(k, 0) + v for k, v in self._observer.tokens.items()
                    }
                self.repo.checkpoint(
                    course,
                    job["id"],
                    job["lease_token"],
                    stage=stage,
                    checkpoint=cp.model_dump(mode="json"),
                    lease_seconds=self.config.lease_seconds,
                )

            self._save = save
            self._save("snapshot")
            chapters, assignments = self._assemble(job, version, snapshot, delta, cp)
            self._save("publishing")
            result = self.repo.publish(
                course,
                base_version_id=version["id"],
                base_revision=cp.base_revision,
                snapshot_hash=snapshot["hash"],
                chapters=chapters,
                assignments=assignments,
                build_mode="incremental",
                rules_version=RULES_VERSION,
                model_config={**self.identity, "incremental_config": asdict(self.config)},
                job_id=job["id"],
                lease_token=job["lease_token"],
            )
            self.semantic_baseline = SemanticBaseline(
                version_id=str(result["version_id"]),
                snapshot_hash=snapshot["hash"],
                point_hashes=delta.semantic_hashes,
            )
            return self.repo.job(course, job["id"])
        except CatalogLeaseLost:
            raise
        except Exception as exc:
            retry = _retryable(exc) and job["attempt"] < self.config.max_job_attempts
            return self.repo.fail(
                course,
                job["id"],
                job["lease_token"],
                error_code=type(exc).__name__,
                error_message=f"增量目录构建失败（{type(exc).__name__}）；旧版本保留",
                retry_at=(datetime.now(UTC) + timedelta(seconds=self.config.retry_seconds))
                if retry
                else None,
            )
        finally:
            self._save = lambda stage: None
            self._records = None
