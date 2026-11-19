"""Strict model outputs and resumable initial-catalog checkpoint contracts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from .knowledge import Contract, normalize

Key = Annotated[str, Field(pattern=r"^c[1-9][0-9]{0,2}$")]
PointID = Annotated[str, Field(min_length=1, max_length=36)]


class CatalogContract(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class PlannedChapter(CatalogContract):
    key: Key
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    sort_order: int = Field(ge=0)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if normalize(value) in {"未分章", "未分类", "unclassified"} or not all(
            char.isprintable() for char in value
        ):
            raise ValueError("invalid chapter title")
        try:
            UUID(value)
        except ValueError:
            return value
        raise ValueError("UUID is not a chapter title")


class ChapterPlan(CatalogContract):
    chapters: list[PlannedChapter] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def unique_contiguous(self):
        for values in (
            [c.key for c in self.chapters],
            [normalize(c.title) for c in self.chapters],
        ):
            if len(set(values)) != len(values):
                raise ValueError("duplicate chapter key or normalized title")
        if sorted(c.sort_order for c in self.chapters) != list(range(len(self.chapters))):
            raise ValueError("chapter order must be contiguous from zero")
        return self


class PointAssignment(CatalogContract):
    point_id: PointID
    chapter_key: Key | None
    reason: str = Field(min_length=1, max_length=240)


class BatchAssignments(CatalogContract):
    assignments: list[PointAssignment] = Field(min_length=1, max_length=40)


class OverviewTopic(CatalogContract):
    title: str = Field(min_length=1, max_length=160)
    point_ids: list[PointID] = Field(min_length=1, max_length=40)


class TopicOverview(CatalogContract):
    topics: list[OverviewTopic] = Field(min_length=1, max_length=40)


class OverviewRecord(CatalogContract):
    input_hash: str
    overview: TopicOverview


class BatchRecord(CatalogContract):
    input_hash: str
    result: BatchAssignments


class CatalogCheckpoint(CatalogContract):
    schema_version: Literal["catalog-checkpoint-v1"] = "catalog-checkpoint-v1"
    snapshot_hash: str
    rules_version: Literal["catalog-initial-v1"] = "catalog-initial-v1"
    model_config_hash: str
    worker_config_hash: str
    calls_reserved: int = Field(default=0, ge=0)
    overviews: dict[str, OverviewRecord] = Field(default_factory=dict)
    planning: ChapterPlan | None = None
    batches: dict[str, BatchRecord] = Field(default_factory=dict)
