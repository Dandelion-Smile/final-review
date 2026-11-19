"""Deterministic growth proofs; no live model or business database access."""

import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from langchain_openai import ChatOpenAI
from test_knowledge_catalog_worker import CatalogModel, CatalogRepo, snapshot_points

from final_review.knowledge import digest
from final_review.knowledge_catalog_incremental import (
    IncrementalCatalogWorker,
    IncrementalConfig,
    chapter_keys,
    full_points,
    identify_delta,
    semantic_projection,
    validate_candidate,
)
from final_review.knowledge_catalog_incremental_contracts import (
    ExistingMatches,
    NewTopics,
    SemanticBaseline,
    TopicReview,
)
from final_review.knowledge_catalog_store import (
    CatalogAssignment,
    CatalogChapter,
    CatalogConflict,
    CatalogInvalid,
    CatalogLeaseLost,
    CatalogRepository,
    CatalogSnapshotChanged,
)
from final_review.knowledge_catalog_worker import CatalogBudgetExceeded, CatalogOutputInvalid
from final_review.llm import ReviewModel


def base_version(snapshot):
    chapter = dict(chapter_id=uuid4(), title="Docker 基础", description="Docker", sort_order=0)
    return dict(
        id=uuid4(),
        knowledge_snapshot_hash=snapshot["hash"],
        chapters=[chapter],
        assignments=[
            dict(
                point_id=p["point_id"],
                chapter_id=chapter["chapter_id"],
                classification_input_hash=p["classification_input_hash"],
                sort_order=i,
            )
            for i, p in enumerate(snapshot["points"])
        ],
    )


def rehash(snapshot):
    for point in snapshot["points"]:
        point["classification_input_hash"] = digest(point["input"])
    snapshot["hash"] = digest(snapshot["points"])


def baseline_for(version, snapshot):
    return SemanticBaseline(
        version_id=str(version["id"]),
        snapshot_hash=version["knowledge_snapshot_hash"],
        point_hashes=identify_delta(version, snapshot).semantic_hashes,
    )


def test_delta_disjoint_new_changed_unassigned_removed_carried():
    original = snapshot_points(5)
    version = base_version(original)
    version["assignments"][1]["chapter_id"] = None
    version["assignments"][2]["chapter_id"] = None
    current = snapshot_points(6)
    current["points"].pop(4)
    current["points"][2]["input"]["rows"][0]["description"] = "changed null point"
    current["points"][3]["input"]["rows"][0]["name"] = "changed assigned point"
    rehash(current)
    before = deepcopy(version)
    delta = identify_delta(version, current)
    ids = [p["point_id"] for p in original["points"]]
    assert delta.carried_points == {ids[0]}
    assert delta.unassigned_points == {ids[1]}
    assert delta.changed_points == {ids[2], ids[3]}
    assert delta.removed_points == {ids[4]}
    assert len(delta.new_points) == 1
    assert len(delta.pending) == 4
    assert delta.inherited[0].chapter_id == version["chapters"][0]["chapter_id"]
    assert delta.inherited[0].assignment_method == "carried"
    assert version == before


def test_duplicate_evidence_semantics_and_legacy_conservatism():
    old = snapshot_points(2)
    version = base_version(old)
    current = deepcopy(old)
    row = deepcopy(current["points"][0]["input"]["rows"][0])
    row.update(evidence_id=str(uuid4()), title="another source", locator={"page": 2})
    current["points"][0]["input"]["rows"].append(row)
    rehash(current)
    assert len(identify_delta(version, current).changed_points) == 1
    baseline = baseline_for(version, old)
    delta = identify_delta(version, current, baseline)
    assert not delta.changed_points and len(delta.carried_points) == 2
    assert (
        delta.inherited[0].classification_input_hash
        == current["points"][0]["classification_input_hash"]
    )


def test_semantics_use_complete_content_and_stable_normalized_fact_set():
    point = snapshot_points(1)["points"][0]
    row = point["input"]["rows"][0]
    row["description"] = "a" * 600
    row["fact_text"] = "b" * 500
    before = semantic_projection(point)
    row["description"] += "changed after excerpt"
    assert semantic_projection(point) != before
    before = semantic_projection(point)
    point["input"]["rows"] += [deepcopy(row), dict(row, fact_text="extra fact")]
    projected = semantic_projection(point)
    point["input"]["rows"].reverse()
    assert semantic_projection(point) == projected
    assert len(projected["facts"]) == 2 and projected != before


def test_baseline_must_reference_actual_history():
    snapshot = snapshot_points(1)
    version = base_version(snapshot)
    baseline = baseline_for(version, snapshot).model_copy(update={"version_id": str(uuid4())})
    with pytest.raises(CatalogInvalid):
        identify_delta(version, snapshot, baseline)


def match_payload(points, status="matched", key="c1"):
    return {
        "matches": [
            dict(point_id=p["point_id"], status=status, chapter_key=key, reason="supported topic")
            for p in points
        ]
    }


def test_matching_statuses_and_stable_keys_preserve_display_order():
    snapshot = snapshot_points(3)
    chapters = base_version(snapshot)["chapters"]
    chapters.append(dict(chapter_id=uuid4(), title="网络", description="network", sort_order=1))
    before = deepcopy(chapters)
    keys = chapter_keys(chapters)
    assert chapter_keys(list(reversed(chapters))) == keys
    points = full_points(snapshot)

    def hook(schema, data):
        assert schema is ExistingMatches
        payload = match_payload(points)
        payload["matches"][1].update(status="unmatched", chapter_key=None)
        payload["matches"][2].update(status="ambiguous", chapter_key=None)
        return payload

    worker = IncrementalCatalogWorker(None, CatalogModel(hook))
    result = worker.match_batch(points, keys)
    assert [m.status for m in result.matches] == ["matched", "unmatched", "ambiguous"]
    assert chapters == before
    assert worker.calls == 1


@pytest.mark.parametrize("corruption", ["unknown", "duplicate", "missing", "status", "extra"])
def test_invalid_matches_never_escape_bounded_validation(corruption):
    snapshot = snapshot_points(2)
    points = full_points(snapshot)
    keys = chapter_keys(base_version(snapshot)["chapters"])

    def hook(schema, data):
        payload = match_payload(points)
        if corruption == "unknown":
            payload["matches"][0]["chapter_key"] = "c80"
        elif corruption == "duplicate":
            payload["matches"][1] = payload["matches"][0]
        elif corruption == "missing":
            payload["matches"].pop()
        elif corruption == "status":
            payload["matches"][0]["status"] = "ambiguous"
        else:
            payload["matches"][0]["point_id"] = str(uuid4())
        return payload

    worker = IncrementalCatalogWorker(None, CatalogModel(hook))
    with pytest.raises(CatalogOutputInvalid):
        worker.match_batch(points, keys)
    assert worker.calls == 2


def test_complete_inputs_are_batched_and_budget_is_reserved_before_call():
    worker = IncrementalCatalogWorker(
        None,
        CatalogModel(lambda s, d: match_payload(d["points"])),
        config=IncrementalConfig(batch_size=2, max_calls=1),
    )
    points = full_points(snapshot_points(3))
    assert list(map(len, worker._groups(points))) == [2, 1]
    saved = []
    worker._save = lambda stage: saved.append((stage, worker.calls))
    worker.match_batch(points[:2], {"c1": base_version(snapshot_points(1))["chapters"][0]})
    assert saved[0] == ("model_reserved", 1)
    with pytest.raises(CatalogBudgetExceeded):
        worker.match_batch(points[2:], {})


class GrowthModel(CatalogModel):
    def __init__(self, *, hook=None, topic_count=1, coherent=True, relation="distinct"):
        super().__init__(hook)
        self.topic_count, self.coherent, self.relation = topic_count, coherent, relation
        self.memberships = {}

    def structured(self, schema, task, data, **kwargs):
        self.calls.append((schema.__name__, deepcopy(data)))
        if self.hook:
            result = self.hook(schema, data)
            if result is not None:
                return result
        if schema is ExistingMatches:
            new_key = next(
                (c["key"] for c in data["chapters"] if c["title"].startswith("虚拟化")), None
            )
            matches = []
            for p in data["points"]:
                point_key = next(
                    (
                        c["key"]
                        for c in data["chapters"]
                        if c["title"] == self.memberships.get(p["point_id"])
                    ),
                    None,
                )
                if not self.memberships:
                    point_key = new_key
                key = data["chapters"][0]["key"] if "协议" in p["name"] else new_key
                if "协议" not in p["name"]:
                    key = point_key
                matches.append(
                    dict(
                        point_id=p["point_id"],
                        status="matched" if key else "unmatched",
                        chapter_key=key,
                        reason="topic evidence",
                    )
                )
            return {"matches": matches}
        if schema is NewTopics:
            points = data.get("points")
            if points is None:
                points = [{"point_id": pid} for t in data["proposals"] for pid in t["point_ids"]]
            payload = {
                "topics": [
                    dict(
                        key=f"t{i + 1}",
                        title=f"虚拟化技术原理{i + 1}",
                        description="Hypervisor、KVM、全虚拟化和虚拟机管理",
                        point_ids=[p["point_id"] for p in points[i :: self.topic_count]],
                    )
                    for i in range(self.topic_count)
                ]
            }
            self.memberships.update(
                {pid: t["title"] for t in payload["topics"] for pid in t["point_ids"]}
            )
            return payload
        assert schema is TopicReview
        targets = [c["key"] for c in data["old_chapters"] + data["peer_candidates"]]
        return dict(
            coherent=self.coherent,
            supported=True,
            independent=True,
            relations=[dict(target_key=k, relation=self.relation) for k in targets],
            reason="members and boundaries checked",
        )


def virtualization_points(count=3):
    points = full_points(snapshot_points(count))
    for i, point in enumerate(points):
        point.update(name=f"KVM virtualization {i}", facts=[f"Hypervisor 虚拟机管理事实 {i}"])
    return points


def grow(model=None, count=3, config=None):
    worker = IncrementalCatalogWorker(None, model or GrowthModel(), config=config)
    old = base_version(snapshot_points(1))["chapters"]
    result = worker.grow_chapters(virtualization_points(count), old, job_id="test-job")
    return worker, old, result


def test_docker_to_virtualization_adds_stable_uuid_and_appends_order():
    model = GrowthModel()
    worker, old, result = grow(model)
    assert len(result.approved) == 1
    new = result.approved[0]
    assert new.title == "虚拟化技术原理1" and new.sort_order == 1
    assert new.chapter_id != str(old[0]["chapter_id"])
    repeated = worker.grow_chapters(virtualization_points(), old, job_id="test-job")
    assert repeated == result


def test_all_match_old_and_single_unmatched_do_not_create():
    worker = IncrementalCatalogWorker(None, GrowthModel())
    snapshot = snapshot_points(3)
    old = base_version(snapshot)["chapters"]
    result = worker.match_batch(full_points(snapshot), chapter_keys(old))
    assert all(m.status == "matched" for m in result.matches)
    assert not worker.grow_chapters([], old, job_id="j").approved
    assert not grow(count=1)[2].approved


@pytest.mark.parametrize(
    "coherent,relation",
    [
        (False, "distinct"),
        (True, "equivalent"),
        (True, "contained"),
        (True, "overlap"),
        (True, "uncertain"),
    ],
)
def test_incoherent_synonymous_contained_or_uncertain_topics_are_rejected(coherent, relation):
    _, _, result = grow(GrowthModel(coherent=coherent, relation=relation))
    assert not result.approved


def test_peer_duplicates_are_rejected_and_three_distinct_topics_respect_cap():
    def peers_overlap(schema, data):
        if schema is TopicReview:
            return dict(
                coherent=True,
                supported=True,
                independent=True,
                reason="peer duplicate",
                relations=[
                    dict(target_key=c["key"], relation="distinct") for c in data["old_chapters"]
                ]
                + [
                    dict(target_key=c["key"], relation="equivalent")
                    for c in data["peer_candidates"]
                ],
            )

    assert not grow(GrowthModel(hook=peers_overlap, topic_count=2), count=6)[2].approved
    _, _, result = grow(GrowthModel(topic_count=3), count=9)
    assert len(result.approved) == 2 and len(result.candidates.topics) == 3
    assert len({c.chapter_id for c in result.approved}) == 2
    assert [c.sort_order for c in result.approved] == [1, 2]


def test_normalized_old_title_duplicate_is_rejected_without_semantic_call():
    def same_name(schema, data):
        if schema is NewTopics:
            return {
                "topics": [
                    dict(
                        key="t1",
                        title=" Docker 基础 ",
                        description="docker",
                        point_ids=[p["point_id"] for p in data["points"]],
                    )
                ]
            }

    worker, _, result = grow(GrowthModel(hook=same_name))
    assert not result.approved and worker.calls == 1


@pytest.mark.parametrize("kind", ["unknown", "shared", "name", "review_missing"])
def test_malformed_topics_or_reviews_fail_with_finite_attempts(kind):
    def corrupt(schema, data):
        if schema is NewTopics and kind != "review_missing":
            ids = [p["point_id"] for p in data["points"]]
            topic = dict(key="t1", title="虚拟化", description="virtualization", point_ids=ids)
            if kind == "unknown":
                topic["point_ids"][0] = str(uuid4())
                return {"topics": [topic]}
            other = dict(topic, key="t2", title="other")
            if kind == "name":
                other.update(title=" 虚拟化 ", point_ids=[])
            return {"topics": [topic, other]}
        if schema is TopicReview and kind == "review_missing":
            return dict(
                coherent=True,
                supported=True,
                independent=True,
                relations=[],
                reason="missing target",
            )

    model = GrowthModel(hook=corrupt)
    worker = IncrementalCatalogWorker(None, model)
    with pytest.raises(CatalogOutputInvalid):
        worker.grow_chapters(
            virtualization_points(), base_version(snapshot_points(1))["chapters"], job_id="j"
        )
    assert worker.calls <= 3


def test_cross_batch_proposals_are_merged_and_soft_cap_prevents_creation():
    worker, _, result = grow(count=41, config=IncrementalConfig(batch_size=2))
    assert len(result.approved) == 1
    assert [n for n, _ in worker.model.calls].count("NewTopics") == 3
    limited, _, result = grow(config=IncrementalConfig(max_chapters=1))
    assert not result.approved and not limited.model.calls


class GrowthRepo(CatalogRepo):
    def __init__(self, count=4):
        super().__init__(count)
        old = snapshot_points(1)
        self.base = base_version(old)
        self.base["course_id"] = "course-1"
        for p in self.data["points"][1:]:
            p["input"]["rows"][0]["name"] = "KVM virtualization"
        rehash(self.data)
        self.directory = dict(current_version_id=self.base["id"], revision=1)
        self.task.update(
            mode="incremental", base_version_id=self.base["id"], snapshot_hash=self.data["hash"]
        )

    def current(self, course):
        return dict(head=deepcopy(self.directory), version=deepcopy(self.base))

    def publish(self, course, **kwargs):
        self.fence(kwargs["lease_token"])
        if self.publish_error:
            raise self.publish_error
        if kwargs["base_version_id"] != self.directory["current_version_id"]:
            raise CatalogConflict()
        if kwargs["snapshot_hash"] != self.data["hash"]:
            raise CatalogSnapshotChanged()
        CatalogRepository._validate(kwargs["chapters"], kwargs["assignments"], self.data)
        result_id = uuid4()
        self.published.append(deepcopy(kwargs))
        self.directory.update(current_version_id=result_id, revision=self.directory["revision"] + 1)
        self.task.update(status="succeeded", result_version_id=result_id)
        return dict(version_id=result_id, revision=self.directory["revision"], created=True)

    def reclaim(self):
        self.expired = False
        self.task.update(status="running", lease_token=uuid4(), attempt=self.task["attempt"] + 1)
        return deepcopy(self.task)


def test_run_complete_v2_retains_parent_and_sidecar_redelivery_is_idempotent():
    repo = GrowthRepo()
    before = deepcopy(repo.base)
    model = GrowthModel()
    worker = IncrementalCatalogWorker(repo, model, config=IncrementalConfig(batch_size=2))
    task = deepcopy(repo.task)
    assert worker.run(task)["status"] == "succeeded"
    published = repo.published[0]
    assert published["build_mode"] == "incremental"
    assert published["chapters"][0] == CatalogChapter(**before["chapters"][0])
    assert len(published["chapters"]) == 2 and len(published["assignments"]) == 4
    assert [a.assignment_method for a in published["assignments"]].count("carried") == 1
    assert repo.base == before
    assert worker.calls == 6  # 2 old batches, proposal, review, 2 residual batches
    assert all(
        before["assignments"][0]["point_id"] not in {p["point_id"] for p in data.get("points", [])}
        for _, data in model.calls
    )
    assert worker.semantic_baseline.version_id == str(repo.task["result_version_id"])
    before_calls = len(model.calls)
    assert worker.run(task)["status"] == "succeeded"
    assert len(model.calls) == before_calls and len(repo.published) == 1


def test_unassigned_removed_and_excess_topics_remain_safe():
    repo = GrowthRepo(count=10)
    worker = IncrementalCatalogWorker(repo, GrowthModel(topic_count=3))
    assert worker.run(deepcopy(repo.task))["status"] == "succeeded"
    published = repo.published[0]
    assert len(published["chapters"]) == 3
    assert sum(a.chapter_id is None for a in published["assignments"]) >= 3
    repo = GrowthRepo(count=2)
    repo.base["assignments"][0]["chapter_id"] = None
    repo.data["points"].pop(0)
    rehash(repo.data)
    repo.task["snapshot_hash"] = repo.data["hash"]
    assert (
        IncrementalCatalogWorker(repo, GrowthModel()).run(deepcopy(repo.task))["status"]
        == "succeeded"
    )
    assert len(repo.published[0]["assignments"]) == 1
    assert repo.published[0]["assignments"][0].assignment_method == "unclassified"


@pytest.mark.parametrize("failure", [CatalogConflict, CatalogSnapshotChanged])
def test_publish_conflicts_record_terminal_failure_and_preserve_parent(failure):
    repo = GrowthRepo()
    repo.publish_error = failure()
    before = deepcopy(repo.directory)
    result = IncrementalCatalogWorker(repo, GrowthModel()).run(deepcopy(repo.task))
    assert result["status"] == "failed" and result["error_code"] == failure.__name__
    assert repo.directory == before and not repo.published


def test_lease_loss_after_model_discards_output_new_owner_consumes_reserved_budget():
    repo = GrowthRepo()

    def expire(schema, data):
        repo.expired = True

    task = deepcopy(repo.task)
    with pytest.raises(CatalogLeaseLost):
        IncrementalCatalogWorker(repo, GrowthModel(hook=expire)).run(task)
    assert not repo.published and repo.task["checkpoint"]["logical_calls"] == 1
    new_task = repo.reclaim()
    worker = IncrementalCatalogWorker(repo, GrowthModel())
    with pytest.raises(CatalogLeaseLost):
        worker.run(task)
    assert worker.run(new_task)["status"] == "succeeded"
    assert worker.calls == 5  # lost matching result reruns, budget does not reset


def test_transient_failure_resumes_completed_calls_and_cumulative_budget():
    repo = GrowthRepo()

    def interrupt(schema, data):
        if schema is TopicReview:
            raise httpx.ConnectError("transient")

    settings = IncrementalConfig(call_attempts=1)
    first = IncrementalCatalogWorker(repo, GrowthModel(hook=interrupt), config=settings)
    result = first.run(deepcopy(repo.task))
    assert result["status"] == "retry_wait" and first.calls == 3
    model = GrowthModel()
    second = IncrementalCatalogWorker(repo, model, config=settings)
    assert second.run(repo.reclaim())["status"] == "succeeded"
    assert [schema for schema, _ in model.calls] == ["TopicReview", "ExistingMatches"]
    assert second.calls == 5


def test_budget_remains_exhausted_after_restart_including_uncertain_inflight_call():
    repo = GrowthRepo()

    class Crash(BaseException):
        pass

    def crash(schema, data):
        raise Crash()

    settings = IncrementalConfig(max_calls=1)
    with pytest.raises(Crash):
        IncrementalCatalogWorker(repo, GrowthModel(hook=crash), config=settings).run(
            deepcopy(repo.task)
        )
    model = GrowthModel()
    result = IncrementalCatalogWorker(repo, model, config=settings).run(repo.reclaim())
    assert result["error_code"] == "CatalogBudgetExceeded" and not model.calls
    assert not repo.published


@pytest.mark.parametrize(
    "corruption",
    [
        "schema",
        "job",
        "base",
        "snapshot",
        "config",
        "budget",
        "record",
        "batches",
        "growth",
        "scope",
    ],
)
def test_checkpoint_identity_and_reachable_results_are_revalidated(corruption):
    repo = GrowthRepo()

    def fail_review(schema, data):
        if schema is TopicReview:
            raise httpx.ConnectError("stop")

    settings = IncrementalConfig(call_attempts=1)
    IncrementalCatalogWorker(repo, GrowthModel(hook=fail_review), config=settings).run(
        deepcopy(repo.task)
    )
    cp = repo.task["checkpoint"]
    if corruption == "schema":
        cp["schema_version"] = "catalog-checkpoint-v1"
    elif corruption == "job":
        cp["job_id"] = str(uuid4())
    elif corruption == "base":
        cp["base_revision"] = 2
    elif corruption == "snapshot":
        cp["snapshot_hash"] = "another"
    elif corruption == "config":
        cp["model_config_hash"] = "changed"
    elif corruption == "budget":
        cp["logical_calls"] = 0
    elif corruption == "record":
        next(iter(cp["records"].values()))["result"] = {}
    elif corruption == "batches":
        cp["completed_batches"]["existing:0"] = "wrong"
    elif corruption == "growth":
        cp["growth"] = {"candidates": {"topics": []}, "reviews": {}, "approved": []}
    else:
        repo.base["chapters"][0]["version_id"] = uuid4()
    result = IncrementalCatalogWorker(repo, GrowthModel(), config=settings).run(repo.reclaim())
    assert result["status"] == "failed"
    assert result["error_code"] == (
        "CatalogInvalid" if corruption == "scope" else "CatalogCheckpointInvalid"
    )
    assert not repo.published


def test_unchanged_noop_does_not_create_a_version_and_wrong_mode_is_rejected():
    repo = GrowthRepo(count=1)
    worker = IncrementalCatalogWorker(repo, GrowthModel())
    assert worker.run(deepcopy(repo.task))["error_code"] == "CatalogNoChanges"
    assert not repo.published and not worker.model.calls
    job = dict(repo.task, mode="initial", base_version_id=None)
    with pytest.raises(CatalogInvalid):
        worker.run(job)


@pytest.mark.parametrize(
    "mutation",
    ["title", "description", "order", "remove", "inherited", "unknown", "duplicate", "hash"],
)
def test_final_business_guard_rejects_parent_or_coverage_corruption(mutation):
    snapshot = snapshot_points(1)
    version = base_version(snapshot)
    delta = identify_delta(version, snapshot)
    chapters = [CatalogChapter(**c) for c in version["chapters"]]
    assignments = list(delta.inherited)
    if mutation in {"title", "description", "order"}:
        key, value = ("sort_order", 1) if mutation == "order" else (mutation, "modified")
        chapters[0] = chapters[0].model_copy(update={key: value})
    elif mutation == "remove":
        chapters = []
    elif mutation == "inherited":
        assignments[0] = CatalogAssignment(
            **dict(assignments[0].model_dump(), chapter_id=None, assignment_method="unclassified")
        )
    elif mutation == "unknown":
        assignments[0] = assignments[0].model_copy(update={"point_id": uuid4()})
    elif mutation == "duplicate":
        assignments += assignments
    else:
        assignments[0] = assignments[0].model_copy(update={"classification_input_hash": "wrong"})
    with pytest.raises((CatalogInvalid, CatalogSnapshotChanged)):
        validate_candidate(version, snapshot, delta, chapters, assignments)


@pytest.mark.parametrize("request_budget", [3, 4])
def test_review_model_internal_and_sdk_retries_are_reserved_and_observed(request_budget):
    """Real ReviewModel + SDK, but every HTTP request is intercepted in memory."""
    repo = GrowthRepo(count=2)
    requests = []

    def respond(request):
        requests.append(request)
        # Each of the two structured attempts has one SDK HTTP retry.
        if len(requests) % 2:
            return httpx.Response(500, json={"error": {"message": "synthetic transient"}})
        payload = {} if len(requests) == 2 else match_payload([repo.data["points"][1]])
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-test",
                                    "type": "function",
                                    "function": {
                                        "name": "ExistingMatches",
                                        "arguments": json.dumps(payload),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="test",
                api_key="test",
                base_url="https://mock.test/v1",
                max_retries=1,
                http_client=client,
            )
        )
        worker = IncrementalCatalogWorker(
            repo, model, config=IncrementalConfig(max_request_attempts=request_budget)
        )
        result = worker.run(deepcopy(repo.task))
    if request_budget == 3:
        assert result["error_code"] == "CatalogBudgetExceeded" and not requests
    else:
        assert result["status"] == "succeeded" and len(requests) == 4
        cp = repo.task["checkpoint"]
        assert cp["logical_calls"] == 1 and cp["request_attempts_reserved"] == 4
        assert cp["observed_llm_starts"] == 2 and cp["observed_tokens"]["total_tokens"] == 20


@pytest.mark.parametrize(
    "config", [dict(min_topic_points=2), dict(max_new_chapters=3), dict(max_chapters=81)]
)
def test_initial_growth_safety_thresholds_cannot_be_relaxed(config):
    with pytest.raises(ValueError):
        IncrementalConfig(**config)


def test_ambiguous_pool_is_unclassified_and_not_used_for_theme_discovery():
    repo = GrowthRepo()
    model = GrowthModel(hook=lambda schema, data: match_payload(data["points"], "ambiguous", None))
    result = IncrementalCatalogWorker(repo, model).run(deepcopy(repo.task))
    assert result["status"] == "succeeded"
    assert len(repo.published[0]["chapters"]) == 1
    assert sum(a.assignment_method == "unclassified" for a in repo.published[0]["assignments"]) == 3
    assert [schema for schema, _ in model.calls] == ["ExistingMatches"]


def test_incomplete_model_output_never_publishes():
    repo = GrowthRepo()
    before = deepcopy(repo.directory)
    model = GrowthModel(hook=lambda schema, data: {"matches": []})
    result = IncrementalCatalogWorker(repo, model).run(deepcopy(repo.task))
    assert result["status"] == "failed" and result["error_code"] == "CatalogOutputInvalid"
    assert repo.directory == before and not repo.published and len(model.calls) == 2


def test_semantic_approval_without_three_final_matches_does_not_publish_empty_new_chapter():
    repo = GrowthRepo()

    def uncertain_final(schema, data):
        if schema is ExistingMatches and len(data["chapters"]) > 1:
            return match_payload(data["points"], "ambiguous", None)

    worker = IncrementalCatalogWorker(repo, GrowthModel(hook=uncertain_final))
    assert worker.run(deepcopy(repo.task))["status"] == "succeeded"
    assert len(repo.published[0]["chapters"]) == 1
    assert repo.task["checkpoint"]["growth"]["approved"]
    assert repo.task["checkpoint"]["final_new_chapters"] == []
    assert sum(a.chapter_id is None for a in repo.published[0]["assignments"]) == 3
