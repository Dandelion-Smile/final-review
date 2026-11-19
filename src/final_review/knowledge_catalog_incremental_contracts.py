"""Independent incremental contracts; no changes to initial-catalog persistence."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .knowledge_catalog_contracts import CatalogContract, Key, PointID


class SemanticBaseline(CatalogContract):
    """Caller-owned sidecar, pinned to an immutable published version.

    Persist the successful worker's returned sidecar externally and supply it on
    the next run. Never infer historical semantic hashes from today's snapshot.
    No sidecar is required for legacy V1: changed full hashes then need review.
    """

    schema_version: Literal["catalog-semantic-baseline-v1"] = "catalog-semantic-baseline-v1"
    version_id: str
    snapshot_hash: str
    point_hashes: dict[str, str] = Field(default_factory=dict)


class ExistingMatch(CatalogContract):
    point_id: PointID
    status: Literal["matched", "unmatched", "ambiguous"]
    chapter_key: Key | None
    reason: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def definite_key_only(self):
        if (self.status == "matched") != (self.chapter_key is not None):
            raise ValueError("only a definite match may reference a chapter")
        return self


class ExistingMatches(CatalogContract):
    matches: list[ExistingMatch] = Field(min_length=1, max_length=40)


TopicKey = Annotated[str, Field(pattern=r"^t[1-9][0-9]{0,3}$")]


class NewTopic(CatalogContract):
    key: TopicKey
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    point_ids: list[PointID] = Field(min_length=1, max_length=160)


class NewTopics(CatalogContract):
    topics: list[NewTopic] = Field(max_length=40)


class TopicRelation(CatalogContract):
    target_key: str = Field(min_length=1, max_length=8)
    relation: Literal["distinct", "equivalent", "contains", "contained", "overlap", "uncertain"]


class TopicReview(CatalogContract):
    coherent: bool
    supported: bool
    independent: bool
    relations: list[TopicRelation] = Field(max_length=120)
    reason: str = Field(min_length=1, max_length=500)


class ApprovedChapter(CatalogContract):
    topic_key: TopicKey
    chapter_id: str
    title: str
    description: str
    sort_order: int = Field(ge=0)


class GrowthResult(CatalogContract):
    candidates: NewTopics
    reviews: dict[str, TopicReview] = Field(default_factory=dict)
    approved: list[ApprovedChapter] = Field(default_factory=list)


class IncrementalCallRecord(CatalogContract):
    schema_name: str
    input_hash: str
    result: dict


class IncrementalCheckpoint(CatalogContract):
    schema_version: Literal["catalog-incremental-checkpoint-v1"] = (
        "catalog-incremental-checkpoint-v1"
    )
    job_id: str
    course_id: str
    base_version_id: str
    base_revision: int = Field(ge=1)
    snapshot_hash: str
    rules_version: Literal["catalog-incremental-v1"] = "catalog-incremental-v1"
    model_config_hash: str
    worker_config_hash: str
    baseline_hash: str
    semantic_hashes: dict[str, str]
    records: dict[str, IncrementalCallRecord] = Field(default_factory=dict)
    existing_matches: list[ExistingMatch] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)
    growth: GrowthResult | None = None
    final_new_chapters: list[ApprovedChapter] | None = None
    completed_batches: dict[str, str] = Field(default_factory=dict)
    logical_calls: int = Field(default=0, ge=0)
    request_attempts_reserved: int = Field(default=0, ge=0)
    call_log: list[dict] = Field(default_factory=list)
    # LangChain starts are observable; SDK HTTP retries are NOT separately exposed.
    observed_llm_starts: int | None = None
    observed_tokens: dict[str, int] | None = None
