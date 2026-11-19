"""Deterministic model doubles: bounded planning, coverage, recovery and fencing."""

import logging
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from final_review.knowledge import digest
from final_review.knowledge_catalog_contracts import (
    BatchAssignments,
    CatalogCheckpoint,
    ChapterPlan,
    TopicOverview,
)
from final_review.knowledge_catalog_store import (
    SNAPSHOT_CONTRACT,
    CatalogConflict,
    CatalogInvalid,
    CatalogLeaseLost,
    CatalogRepository,
    CatalogSnapshotChanged,
)
from final_review.knowledge_catalog_worker import (
    InitialCatalogWorker,
    WorkerConfig,
    compact_points,
    model_identity,
    validate_batch,
)
from final_review.llm import ModelError, ModelOutputLimitError, ReviewModel


def plan_payload():
    return {
        "chapters": [
            {
                "key": "c1",
                "title": "网络基础",
                "description": "网络协议与通信机制",
                "sort_order": 0,
            }
        ]
    }


class CatalogModel:
    def __init__(self, hook=None):
        self.model = SimpleNamespace(
            model_name="deterministic-test", openai_api_base="https://test"
        )
        self.hook = hook
        self.calls = []

    def structured(self, schema, task, data, **kwargs):
        self.calls.append((schema.__name__, deepcopy(data)))
        if self.hook:
            result = self.hook(schema, data)
            if result is not None:
                return result
        if schema is ChapterPlan:
            return plan_payload()
        if schema is TopicOverview:
            return {
                "topics": [
                    {"title": "网络主题", "point_ids": [p["point_id"] for p in data["points"]]}
                ]
            }
        assert schema is BatchAssignments
        return {
            "assignments": [
                {"point_id": p["point_id"], "chapter_key": "c1", "reason": "协议主题"}
                for p in data["points"]
            ]
        }


def snapshot_points(count=25):
    points = []
    for index in range(count):
        point_id = str(UUID(int=index + 1))
        value = {
            "contract": SNAPSHOT_CONTRACT,
            "rows": [
                {
                    "id": point_id,
                    "name": f"协议{index}",
                    "description": "解释",
                    "fact_text": f"受支持事实{index}",
                    "chapter": "来源PPT章节不能作为课程章节",
                }
            ],
        }
        points.append(
            {"point_id": point_id, "input": value, "classification_input_hash": digest(value)}
        )
    return {"contract": SNAPSHOT_CONTRACT, "hash": digest(points), "points": points}


class CatalogRepo:
    """Store seam double; real PostgreSQL invariants live in the integration suite."""

    def __init__(self, count=25):
        self.data = snapshot_points(count)
        self.task = dict(
            id=uuid4(),
            course_id="course-1",
            mode="initial",
            base_version_id=None,
            snapshot_hash=self.data["hash"],
            checkpoint={},
            status="running",
            lease_token=uuid4(),
            attempt=1,
        )
        self.directory = None
        self.published = []
        self.expired = False
        self.publish_error = None
        self.saved = []

    def job(self, course, job_id):
        return deepcopy(self.task)

    def head(self, course):
        return self.directory

    def snapshot(self, course):
        return deepcopy(self.data)

    def fence(self, token):
        if self.expired or self.task["status"] != "running" or token != self.task["lease_token"]:
            raise CatalogLeaseLost()

    def checkpoint(self, course, job_id, token, **kwargs):
        self.fence(token)
        self.task["checkpoint"] = deepcopy(kwargs["checkpoint"])
        self.saved.append(deepcopy(kwargs))

    def fail(self, course, job_id, token, **kwargs):
        self.fence(token)
        self.task.update(status="retry_wait" if kwargs["retry_at"] else "failed", **kwargs)
        return deepcopy(self.task)

    def publish(self, course, **kwargs):
        self.fence(kwargs["lease_token"])
        if self.publish_error:
            raise self.publish_error
        if kwargs["snapshot_hash"] != self.data["hash"]:
            raise CatalogSnapshotChanged()
        CatalogRepository._validate(kwargs["chapters"], kwargs["assignments"], self.data)
        self.published.append(kwargs)
        self.directory = {"current_version_id": uuid4(), "revision": 1}
        self.task.update(status="succeeded", result_version_id=self.directory["current_version_id"])

    def reclaim(self):
        self.task.update(status="running", lease_token=uuid4(), attempt=self.task["attempt"] + 1)
        self.expired = False
        return deepcopy(self.task)


def worker(repo=None, model=None, **limits):
    return InitialCatalogWorker(
        repo or CatalogRepo(), model or CatalogModel(), config=WorkerConfig(**limits)
    )


def test_small_planning_is_read_only_and_excludes_source_chapters():
    repo = CatalogRepo()
    model = CatalogModel()
    result = worker(repo, model).plan_chapters(compact_points(repo.data))
    assert result.chapters[0].key == "c1"
    assert len(model.calls) == 1
    assert "来源PPT章节" not in str(model.calls)
    assert repo.saved == repo.published == []


@pytest.mark.parametrize(
    "damage",
    [
        "empty",
        "blank",
        "duplicate",
        "key",
        "order",
        "bool_order",
        "reserved",
        "uuid",
        "extra",
        "description",
        "newline",
    ],
)
def test_invalid_plans(damage):
    payload = plan_payload()
    chapter = payload["chapters"][0]
    if damage == "empty":
        payload["chapters"] = []
    elif damage == "blank":
        chapter["title"] = "  "
    elif damage == "duplicate":
        payload["chapters"].append(
            {**chapter, "key": "c2", "title": " 网络 基础 ", "sort_order": 1}
        )
    elif damage == "key":
        chapter["key"] = str(uuid4())
    elif damage == "order":
        chapter["sort_order"] = 1
    elif damage == "bool_order":
        chapter["sort_order"] = False
    elif damage == "reserved":
        chapter["title"] = "未分章"
    elif damage == "uuid":
        chapter["title"] = str(uuid4())
    elif damage == "extra":
        chapter["subsections"] = []
    elif damage == "description":
        chapter["description"] = None
    else:
        chapter["title"] = "章节\n注入"
    with pytest.raises(ValidationError):
        ChapterPlan.model_validate(payload)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unknown", "chapter", "type", "extra"])
def test_bad_batch_is_retried_then_terminal_without_publication(damage):
    repo = CatalogRepo(3)

    def hook(schema, data):
        if schema is not BatchAssignments:
            return None
        items = [
            {"point_id": p["point_id"], "chapter_key": "c1", "reason": "依据"}
            for p in data["points"]
        ]
        if damage == "missing":
            items.pop()
        elif damage == "duplicate":
            items[-1] = items[0]
        elif damage == "unknown":
            items[0]["point_id"] = str(uuid4())
        elif damage == "chapter":
            items[0]["chapter_key"] = "c2"
        elif damage == "type":
            items[0]["chapter_key"] = 1
        else:
            items[0]["new_chapter"] = "新章节"
        return {"assignments": items}

    model = CatalogModel(hook)
    result = worker(repo, model).run(repo.task)
    assert result["status"] == "failed"
    assert result["error_code"] == "CatalogOutputInvalid"
    assert [name for name, _ in model.calls].count("BatchAssignments") == 2
    assert not repo.published
    expected = {
        "missing": "诊断=missing_point_id；缺失=1；重复=0；未知=0",
        "duplicate": "诊断=missing_point_id；缺失=1；重复=1；未知=0",
        "unknown": "诊断=missing_point_id；缺失=1；重复=0；未知=1",
        "chapter": "诊断=invalid_chapter_key",
        "type": "诊断=schema_or_field",
        "extra": "诊断=schema_or_field",
    }[damage]
    prefix = "目录构建失败（CatalogOutputInvalid）；合法检查点已保留；"
    assert result["error_message"] == prefix + expected
    assert "input" not in result["error_message"]


def test_582_points_all_participate_once_in_global_overview_and_classification():
    repo, model = CatalogRepo(582), CatalogModel()
    result = worker(repo, model).run(repo.task)
    assert result["status"] == "succeeded"
    assert len(model.calls) == 31  # 15 overviews, one global plan, 15 classifications
    for schema in ("TopicOverview", "BatchAssignments"):
        ids = [
            p["point_id"] for name, data in model.calls if name == schema for p in data["points"]
        ]
        assert len(ids) == len(set(ids)) == 582
    global_plan = [data for name, data in model.calls if name == "ChapterPlan"][0]
    assert len(global_plan["overviews"]) == 15
    published = repo.published[0]
    assert len(published["assignments"]) == 582
    assert published["base_revision"] == 0 and published["build_mode"] == "initial"
    assert all(a.assignment_method == "llm" for a in published["assignments"])
    assert published["model_config"] == model_identity(model)


def test_explicit_null_is_preserved():
    repo = CatalogRepo(1)
    model = CatalogModel(
        lambda schema, data: (
            {
                "assignments": [
                    {
                        "point_id": data["points"][0]["point_id"],
                        "chapter_key": None,
                        "reason": "无法匹配",
                    }
                ]
            }
            if schema is BatchAssignments
            else None
        )
    )
    assert worker(repo, model).run(repo.task)["status"] == "succeeded"
    item = repo.published[0]["assignments"][0]
    assert item.chapter_id is None and item.assignment_method == "unclassified"


def interrupted_repo():
    repo = CatalogRepo(85)
    failed_ids = {p["point_id"] for p in compact_points(repo.data)[40:80]}

    def hook(schema, data):
        if schema is BatchAssignments and data["points"][0]["point_id"] in failed_ids:
            raise httpx.ConnectError("contains-SECRET-API-KEY")

    model = CatalogModel(hook)
    result = worker(repo, model, call_attempts=1).run(repo.task)
    assert result["status"] == "retry_wait"
    assert "SECRET" not in str(result)
    assert set(result["checkpoint"]["batches"]) == {"0"}
    return repo


def test_retry_reuses_planning_and_completed_batches():
    repo = interrupted_repo()
    model = CatalogModel()
    result = worker(repo, model, call_attempts=1).run(repo.reclaim())
    assert result["status"] == "succeeded"
    assert [name for name, _ in model.calls] == ["BatchAssignments", "BatchAssignments"]


@pytest.mark.parametrize(
    "damage",
    [
        "schema",
        "snapshot",
        "rules",
        "model",
        "config",
        "batch_hash",
        "batch_result",
        "plan",
        "overview",
        "unknown_batch",
        "missing_overview",
        "missing_plan",
    ],
)
def test_incompatible_checkpoint_is_not_reused(damage):
    repo = interrupted_repo()
    cp = repo.task["checkpoint"]
    if damage in {"schema", "snapshot", "rules", "model", "config"}:
        field = {
            "schema": "schema_version",
            "snapshot": "snapshot_hash",
            "rules": "rules_version",
            "model": "model_config_hash",
            "config": "worker_config_hash",
        }[damage]
        cp[field] = "changed"
    elif damage == "batch_hash":
        cp["batches"]["0"]["input_hash"] = "changed"
    elif damage == "batch_result":
        cp["batches"]["0"]["result"]["assignments"].pop()
    elif damage == "plan":
        cp["planning"]["chapters"][0]["sort_order"] = 3
    elif damage == "overview":
        cp["overviews"]["0"]["overview"]["topics"][0]["point_ids"].pop()
    elif damage == "unknown_batch":
        cp["batches"]["99"] = cp["batches"]["0"]
    elif damage == "missing_overview":
        cp["overviews"].pop("0")
    else:
        cp["planning"] = None
    model = CatalogModel()
    result = worker(repo, model, call_attempts=1).run(repo.reclaim())
    assert result["status"] == "failed" and result["error_code"] == "CatalogCheckpointInvalid"
    assert model.calls == repo.published == []


def test_overview_omission_blocks_global_planning():
    repo, model = (
        CatalogRepo(41),
        CatalogModel(
            lambda schema, data: (
                {
                    "topics": [
                        {
                            "title": "遗漏主题",
                            "point_ids": [data["points"][0]["point_id"]],
                        }
                    ]
                }
                if schema is TopicOverview
                else None
            )
        ),
    )
    assert worker(repo, model).run(repo.task)["status"] == "failed"
    assert [name for name, _ in model.calls] == ["TopicOverview", "TopicOverview"]


@pytest.mark.parametrize("when", ["before", "during_model", "before_publish"])
def test_lease_loss_stops_without_failure_write(when):
    repo = CatalogRepo(1)
    if when == "before":
        repo.expired = True

    def hook(schema, data):
        if when == "during_model":
            repo.expired = True

    if when == "before_publish":
        repo.publish_error = CatalogLeaseLost()
    with pytest.raises(CatalogLeaseLost):
        worker(repo, CatalogModel(hook)).run(repo.task)
    assert repo.task["status"] == "running" and not repo.published


@pytest.mark.parametrize("error", [CatalogSnapshotChanged, CatalogConflict, CatalogInvalid])
def test_publish_rejection_has_no_partial_directory(error):
    repo = CatalogRepo(1)
    repo.publish_error = error("reject")
    result = worker(repo).run(repo.task)
    assert result["status"] == "failed" and result["error_code"] == error.__name__
    assert repo.directory is None and not repo.published


def test_changed_snapshot_before_and_during_run():
    for before in (True, False):
        repo = CatalogRepo(1)
        if before:
            repo.data["hash"] = "changed"

        def hook(schema, data):
            if not before:
                repo.data["hash"] = "changed"

        model = CatalogModel(hook)
        result = worker(repo, model).run(repo.task)
        assert result["error_code"] == "CatalogSnapshotChanged"
        assert not repo.published
        if before:
            assert not model.calls


def test_attempt_limit_terminates_retry():
    repo = CatalogRepo(1)
    repo.task["attempt"] = 3

    def broken(schema, data):
        raise httpx.ConnectError("network")

    assert worker(repo, CatalogModel(broken), call_attempts=1).run(repo.task)["status"] == "failed"
    repo = CatalogRepo(1)
    repo.task["attempt"] = 4
    model = CatalogModel()
    assert worker(repo, model).run(repo.task)["status"] == "failed"
    assert model.calls == []


def test_successful_redelivery_is_noop():
    repo, model = CatalogRepo(1), CatalogModel()
    job = deepcopy(repo.task)
    instance = worker(repo, model)
    assert instance.run(job)["status"] == "succeeded"
    assert instance.run(job)["status"] == "succeeded"
    assert len(repo.published) == 1 and len(model.calls) == 2


def test_published_course_rejects_initial_and_wrong_modes_are_untouched():
    repo, model = CatalogRepo(1), CatalogModel()
    repo.directory = {"current_version_id": uuid4(), "revision": 1}
    assert worker(repo, model).run(repo.task)["error_code"] == "CatalogConflict"
    assert not model.calls
    repo = CatalogRepo(1)
    repo.task["mode"] = "incremental"
    with pytest.raises(CatalogInvalid):
        worker(repo).run(repo.task)
    assert not repo.saved


def test_budget_is_bounded_and_large_single_points_are_not_dropped():
    repo = CatalogRepo(1)
    assert worker(repo, max_calls=1).run(repo.task)["error_code"] == "CatalogBudgetExceeded"
    repo = CatalogRepo(1)
    repo.data["points"][0]["input"]["rows"][0]["name"] = "名" * 1000
    assert worker(repo, input_chars=1000).run(repo.task)["error_code"] == "CatalogBudgetExceeded"


def test_transient_call_recovers_current_batch_only(monkeypatch):
    monkeypatch.setattr("final_review.knowledge_catalog_worker.time.sleep", lambda _: None)
    repo = CatalogRepo(2)
    attempts = 0

    def hook(schema, data):
        nonlocal attempts
        if schema is BatchAssignments:
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("temporary")

    model = CatalogModel(hook)
    assert worker(repo, model).run(repo.task)["status"] == "succeeded"
    assert [name for name, _ in model.calls] == [
        "ChapterPlan",
        "BatchAssignments",
        "BatchAssignments",
    ]


def test_model_identity_tracks_runtime_options_without_secrets():
    model = CatalogModel()
    original = model_identity(model)
    model.model.max_tokens = 500
    model.model.openai_api_key = "SECRET"
    assert model_identity(model) != original
    assert "SECRET" not in str(model_identity(model))
    model.model.openai_api_base = "https://test?key=SECRET"
    with pytest.raises(ValueError):
        model_identity(model)


@pytest.mark.parametrize("limits", [{"batch_size": 41}, {"max_calls": 0}, {"call_attempts": 4}])
def test_invalid_worker_limits(limits):
    with pytest.raises(ValueError):
        WorkerConfig(**limits)


def test_batch_validator_accepts_model_order_but_publication_order_is_stable():
    repo = CatalogRepo(3)
    points = compact_points(repo.data)
    result = BatchAssignments.model_validate(
        {
            "assignments": [
                {"point_id": p["point_id"], "chapter_key": "c1", "reason": "依据"}
                for p in reversed(points)
            ]
        }
    )
    validate_batch(result, points, ChapterPlan.model_validate(plan_payload()))
    model = CatalogModel(lambda schema, data: result if schema is BatchAssignments else None)
    assert worker(repo, model).run(repo.task)["status"] == "succeeded"
    assert [str(a.point_id) for a in repo.published[0]["assignments"]] == [
        p["point_id"] for p in points
    ]


def _checkpoint_keys(repo):
    checkpoint = repo.saved[-1]["checkpoint"]
    CatalogCheckpoint.model_validate(checkpoint)
    assert set(checkpoint) == set(CatalogCheckpoint.model_fields)
    assert checkpoint["schema_version"] == "catalog-checkpoint-v1"
    return checkpoint


def test_pure_duplicate_and_unknown_ids_are_distinguished():
    repo = CatalogRepo(3)

    def duplicate(schema, data):
        if schema is not BatchAssignments:
            return None
        items = [
            {"point_id": point["point_id"], "chapter_key": "c1", "reason": "依据"}
            for point in data["points"]
        ]
        items.append(items[0])
        return {"assignments": items}

    result = worker(repo, CatalogModel(duplicate)).run(repo.task)
    assert result["error_message"].endswith("诊断=duplicate_point_id；缺失=0；重复=1；未知=0")
    assert not repo.published

    repo = CatalogRepo(3)
    unknown_id = str(uuid4())

    def unknown(schema, data):
        if schema is not BatchAssignments:
            return None
        items = [
            {"point_id": point["point_id"], "chapter_key": "c1", "reason": "依据"}
            for point in data["points"]
        ]
        items.append({**items[0], "point_id": unknown_id})
        return {"assignments": items}

    result = worker(repo, CatalogModel(unknown)).run(repo.task)
    assert result["error_message"].endswith("诊断=unknown_point_id；缺失=0；重复=0；未知=1")
    assert unknown_id not in result["error_message"]
    assert not repo.published


def test_chapter_order_truncation_and_plan_size_are_distinct(caplog):
    caplog.set_level(logging.WARNING, logger="final_review.knowledge_catalog_worker")
    repo = CatalogRepo(1)

    def bad_order(schema, data):
        if schema is not ChapterPlan:
            return None
        payload = plan_payload()
        payload["chapters"][0]["sort_order"] = 1
        return payload

    result = worker(repo, CatalogModel(bad_order)).run(repo.task)
    assert result["error_message"].endswith("诊断=invalid_chapter_order")
    assert "缺失=" not in result["error_message"]
    assert _checkpoint_keys(repo)["planning"] is None
    assert not repo.published

    def truncated(schema, data):
        raise ModelOutputLimitError("模型输出达到长度上限，请缩小生成批次后重试")

    repo = CatalogRepo(1)
    model = CatalogModel(truncated)
    result = worker(repo, model).run(repo.task)
    assert [name for name, _ in model.calls].count("ChapterPlan") == 2
    assert result["error_message"].endswith("诊断=truncated_output")
    assert "长度上限" not in result["error_message"]
    assert not repo.published

    repo = CatalogRepo(1)

    def too_many(schema, data):
        if schema is not ChapterPlan:
            return None
        return {
            "chapters": [
                {"key": "c1", "title": "甲", "description": "说明", "sort_order": 0},
                {"key": "c2", "title": "乙", "description": "说明", "sort_order": 1},
            ]
        }

    result = worker(repo, CatalogModel(too_many)).run(repo.task)
    assert result["error_message"].endswith("诊断=other_contract")
    assert not repo.published
    assert "catalog output rejected" in caplog.text
    assert "category=invalid_chapter_order" in caplog.text
    assert "category=truncated_output" in caplog.text
    assert "category=other_contract" in caplog.text


def test_wrapped_schema_error_omits_raw_model_input(caplog):
    caplog.set_level(logging.WARNING, logger="final_review.knowledge_catalog_worker")
    sentinel = "RAW-INPUT-SENTINEL-not-for-logs"
    repo = CatalogRepo(1)

    def hook(schema, data):
        if schema is not ChapterPlan:
            return None
        try:
            ChapterPlan.model_validate(
                {
                    "chapters": [
                        {
                            "key": "c1",
                            "title": sentinel,
                            "description": "说明",
                            "sort_order": 0,
                            "leaked": sentinel,
                        }
                    ]
                }
            )
        except ValidationError as exc:
            raise ModelError("模型连续返回无法解析的结构化内容") from exc
        return None

    model = CatalogModel(hook)
    instance = worker(repo, model)
    result = instance.run(repo.task)
    assert result["error_message"].endswith("诊断=schema_or_field")
    assert sentinel not in result["error_message"]
    assert sentinel not in caplog.text
    assert "'input'" not in caplog.text
    diagnosis = instance.call_log[-1]["diagnosis"]
    assert diagnosis["category"] == "schema_or_field"
    assert diagnosis["stage"] == "planning"
    assert all(set(item) == {"loc", "type"} for item in diagnosis["field_errors"])
    assert sentinel not in str(instance.call_log)
    assert sentinel not in str(_checkpoint_keys(repo))
    assert [name for name, _ in model.calls].count("ChapterPlan") == 2
    assert not repo.published


def test_provider_exception_text_is_not_stored(monkeypatch, caplog):
    monkeypatch.setattr("final_review.knowledge_catalog_worker.time.sleep", lambda _: None)
    caplog.set_level(logging.WARNING, logger="final_review.knowledge_catalog_worker")
    secret = "sk-provider-secret-should-not-leak"
    repo = CatalogRepo(1)

    def hook(schema, data):
        raise httpx.ConnectError(secret)

    result = worker(repo, CatalogModel(hook), call_attempts=2).run(repo.task)
    assert result["error_code"] == "ConnectError"
    assert result["status"] == "retry_wait"
    assert secret not in result["error_message"]
    assert secret not in caplog.text
    assert result["error_message"] == "目录构建失败（ConnectError）；合法检查点已保留"
    assert not repo.published


def test_overview_coverage_failure_is_diagnosed_without_publication():
    repo = CatalogRepo(41)

    def hook(schema, data):
        if schema is not TopicOverview:
            return None
        ids = [point["point_id"] for point in data["points"]]
        if "existing_titles" in data:
            ids = [repo.data["points"][0]["point_id"]]
            return {"topics": [{"title": "主题", "point_ids": ids}]}
        return {"topics": [{"title": "主题", "point_ids": ids[:-1]}]}

    model = CatalogModel(hook)
    result = worker(repo, model).run(repo.task)
    assert result["error_message"].endswith("诊断=missing_point_id；缺失=1；重复=0；未知=1")
    assert [name for name, _ in model.calls] == ["TopicOverview", "TopicOverview"]
    assert _checkpoint_keys(repo)["overviews"] == {}
    assert not repo.published


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unknown"])
@pytest.mark.parametrize("repair", [True, False])
def test_overview_correction_receives_context_and_stops_after_one(damage, repair):
    repo = CatalogRepo(108)
    bad_group = None

    def hook(schema, data):
        nonlocal bad_group
        if schema is not TopicOverview:
            return
        points = data["points"]
        if "existing_titles" in data:
            assert damage == "missing" and points == bad_group[-1:]
            assert data["existing_titles"] == ["Group"]
            selected = points if repair else bad_group[:1]
            return {"topics": [{"title": "Group", "point_ids": [selected[0]["point_id"]]}]}
        if bad_group is None:
            bad_group = points
        if points != bad_group:
            return
        ids = [p["point_id"] for p in points]
        if "coverage_errors" in data:
            assert data["coverage_errors"][damage]["count"] == 1
            if repair:
                return
        if damage == "missing":
            ids.pop()
        elif damage == "duplicate":
            ids[-1] = ids[0]
        else:
            ids[-1] = str(UUID(int=999))
        return {"topics": [{"title": "Group", "point_ids": ids}]}

    model = CatalogModel(hook)
    result = worker(repo, model, batch_size=37, call_attempts=3).run(repo.task)
    if damage == "missing":
        assert model.calls[1][1]["points"] == bad_group[-1:]
    else:
        assert model.calls[1][1]["points"] == bad_group
        assert "coverage_errors" in model.calls[1][1]
    if repair:
        assert result["status"] == "succeeded"
        assert len(repo.published[0]["assignments"]) == 108
    else:
        assert len(model.calls) == 2
        assert result["error_code"] == "CatalogOutputInvalid"
        assert result["checkpoint"]["overviews"] == {}
        assert not repo.published


@pytest.mark.parametrize("max_calls", [1, 2])
def test_structured_parser_retries_reserve_before_each_http_request(max_calls):
    from langchain_openai import ChatOpenAI

    repo = CatalogRepo(3)
    observed = []

    def invalid(request):
        observed.append(repo.task["checkpoint"]["calls_reserved"])
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "model": "mock",
                "object": "chat.completion",
                "created": 1,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(invalid)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="mock",
                api_key="mock",
                base_url="https://mock.test",
                max_retries=0,
                http_client=client,
            )
        )
        result = worker(repo, model, max_calls=max_calls, call_attempts=1).run(repo.task)
        assert observed == list(range(1, max_calls + 1))
        assert result["error_code"] == (
            "CatalogBudgetExceeded" if max_calls == 1 else "CatalogOutputInvalid"
        )
        assert (
            worker(repo, model, max_calls=max_calls, call_attempts=1).run(repo.reclaim())[
                "error_code"
            ]
            == "CatalogBudgetExceeded"
        )
        assert len(observed) == max_calls and not repo.published


@pytest.mark.parametrize(
    "budget, code", [(1, "CatalogBudgetExceeded"), (10, "CatalogOutputInvalid")]
)
def test_overview_repair_cannot_repeat_on_parser_failure(budget, code):
    from langchain_openai import ChatOpenAI
    from test_llm import completion

    repo = CatalogRepo(41)
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return completion(
                "TopicOverview",
                {"topics": [{"title": "topic", "point_ids": [repo.data["points"][0]["point_id"]]}]},
            )
        assert b"coverage_errors" in request.content
        return completion("TopicOverview", {})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model = ReviewModel(
            ChatOpenAI(
                model="mock",
                api_key="mock",
                max_retries=0,
                http_client=client,
                base_url="https://mock.test",
            )
        )
        result = worker(repo, model, max_calls=budget).run(repo.task)
    assert result["status"] == "failed" and result["error_code"] == code
    assert len(requests) == result["checkpoint"]["calls_reserved"] == min(budget, 2)
    assert result["checkpoint"]["overviews"] == {} and not repo.published


def test_overview_timeout_and_repair_failure_keep_prior_group_on_resume():
    repo = CatalogRepo(6)
    def timeout(schema, data):
        if schema is TopicOverview and len(model.calls) == 2:
            raise httpx.ReadTimeout("offline timeout")

    model = CatalogModel(timeout)
    limits = {"call_attempts": 1, "batch_size": 3}
    timed_out = worker(repo, model, **limits).run(repo.task)
    assert (timed_out["status"], timed_out["error_code"]) == ("retry_wait", "ReadTimeout")
    first = timed_out["checkpoint"]["overviews"]
    assert set(first) == {"0"} and timed_out["checkpoint"]["calls_reserved"] == 2

    def omit(schema, data):
        assert schema is TopicOverview
        return {"topics": [{"title": "topic", "point_ids": [data["points"][0]["point_id"]]}]}

    failed = worker(repo, CatalogModel(omit), **limits).run(repo.reclaim())
    assert (failed["status"], failed["error_code"]) == ("failed", "CatalogOutputInvalid")
    assert failed["checkpoint"]["overviews"] == first
    assert failed["checkpoint"]["calls_reserved"] == 4 and not repo.published
    recovered = CatalogModel()
    done = worker(repo, recovered, **limits).run(repo.reclaim())
    assert done["status"] == "succeeded" and done["checkpoint"]["calls_reserved"] == 8
    assert done["checkpoint"]["overviews"]["0"] == first["0"]
    assert [name for name, _ in recovered.calls].count("TopicOverview") == 1
