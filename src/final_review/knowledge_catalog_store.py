"""Immutable course catalogs, independent of knowledge ingestion and workers.

Snapshot contract catalog-input-v1 includes exactly the points visible to the
existing knowledge tree (active, supported fact, verified supported evidence,
ready source document). Its explicit input contains point fields, supported
facts, evidence and source metadata; embeddings and timestamps are not classifier
inputs. Workers must classify ``point['input']`` and pass its returned hash.
All model work belongs outside publish().
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .knowledge import digest, normalize

SNAPSHOT_CONTRACT = "catalog-input-v1"
Mode = Literal["initial", "incremental"]


class CatalogConflict(RuntimeError):
    """The base directory/revision or an active job no longer matches."""


class CatalogSnapshotChanged(RuntimeError):
    """The visible set or classifier input has changed; take a new snapshot."""


class CatalogLeaseLost(RuntimeError):
    """The task is not running with this token and an unexpired lease."""


class CatalogInvalid(ValueError):
    """Invalid course, incomplete assignments or invalid catalog structure."""


class CatalogChapter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chapter_id: UUID
    title: str = Field(min_length=1)
    description: str = ""
    sort_order: int = Field(ge=0)


class CatalogAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    point_id: UUID
    chapter_id: UUID | None = None
    classification_input_hash: str = Field(min_length=1)
    assignment_method: Literal["llm", "carried", "unclassified"]
    reason: str = ""
    sort_order: int = Field(ge=0)


class CatalogRepository:
    """User-scoped like KnowledgeRepository; bind PostgresStore before use.

    Database constraint exceptions deliberately retain their psycopg type,
    SQLSTATE and constraint name. Returned database records are dictionaries.
    """

    def __init__(self, store):
        self.store = store

    @property
    def connection(self):
        return self.store.connection

    @property
    def user(self):
        return self.store._user()

    def head(self, course_id: str) -> dict | None:
        return self.connection.execute(
            "SELECT * FROM course_knowledge_catalog_heads WHERE user_id=%s AND course_id=%s",
            (self.user, course_id),
        ).fetchone()

    def version(self, course_id: str, version_id: UUID | str) -> dict | None:
        """Read immutable history, including its chapters and assignments."""
        row = self.connection.execute(
            "SELECT * FROM course_knowledge_catalog_versions "
            "WHERE user_id=%s AND course_id=%s AND id=%s",
            (self.user, course_id, version_id),
        ).fetchone()
        if row is None:
            return None
        row["chapters"] = self.connection.execute(
            "SELECT * FROM course_knowledge_catalog_chapters "
            "WHERE user_id=%s AND course_id=%s AND version_id=%s ORDER BY sort_order,chapter_id",
            (self.user, course_id, version_id),
        ).fetchall()
        row["assignments"] = self.connection.execute(
            "SELECT * FROM course_knowledge_catalog_assignments "
            "WHERE user_id=%s AND course_id=%s AND version_id=%s "
            "ORDER BY chapter_id NULLS LAST,sort_order,point_id",
            (self.user, course_id, version_id),
        ).fetchall()
        return row

    def current(self, course_id: str) -> dict:
        """Empty state is {head: None, version: None}; pin reads to one head."""
        head = self.head(course_id)
        return {
            "head": head,
            "version": (
                self.version(course_id, head["current_version_id"])
                if head and head["current_version_id"]
                else None
            ),
        }

    def snapshot(self, course_id: str) -> dict:
        """One SQL/MVCC snapshot; complete, deterministic classifier inputs.

        Return {contract, hash, points: [{point_id, input,
        classification_input_hash}]}. Pass the hash and every point hash back
        to publish; even unclassified points require an assignment.
        """
        rows = self.connection.execute(
            """SELECT p.id,p.name,p.normalized_name,p.description,p.parent_id,
                 p.importance_score,f.id AS fact_id,f.fact_text,f.normalized_text,
                 e.id AS evidence_id,e.quote,e.verification_method,
                 v.material_version_id,v.document_id,e.locator_id,l.data AS locator,
                 l.ordinal,d.data->>'chapter' AS chapter,
                 coalesce(d.data->>'title',d.data->>'file_name',v.document_id) AS title,
                 d.data->>'source_type' AS source_type
               FROM knowledge_points p
               JOIN knowledge_facts f ON f.user_id=p.user_id AND f.course_id=p.course_id
                 AND f.point_id=p.id AND f.status='supported'
               JOIN fact_evidence e ON e.user_id=f.user_id AND e.course_id=f.course_id
                 AND e.fact_id=f.id AND e.support_status='supported' AND e.quote_verified
               JOIN material_versions v ON v.user_id=e.user_id AND v.course_id=e.course_id
                 AND v.material_version_id=e.material_version_id
               JOIN documents d ON d.user_id=v.user_id AND d.course_id=v.course_id
                 AND d.document_id=v.document_id AND d.data->>'parse_status'='ready'
               JOIN material_locators l ON l.user_id=e.user_id AND l.course_id=e.course_id
                 AND l.material_version_id=e.material_version_id AND l.locator_id=e.locator_id
               WHERE p.user_id=%s AND p.course_id=%s AND p.status='active'
               ORDER BY p.id,f.id,e.id""",
            (self.user, course_id),
        ).fetchall()
        grouped = {}
        for row in rows:
            # Store JSON-compatible values so hashing and model serialization agree.
            item = {
                key: str(value) if isinstance(value, UUID) else value for key, value in row.items()
            }
            grouped.setdefault(item["id"], []).append(item)
        points = [
            {
                "point_id": point_id,
                "input": {"contract": SNAPSHOT_CONTRACT, "rows": items},
                "classification_input_hash": digest({"contract": SNAPSHOT_CONTRACT, "rows": items}),
            }
            for point_id, items in grouped.items()
        ]
        return {
            "contract": SNAPSHOT_CONTRACT,
            "hash": digest(
                {
                    "contract": SNAPSHOT_CONTRACT,
                    "user_id": str(self.user),
                    "course_id": course_id,
                    "points": [(p["point_id"], p["classification_input_hash"]) for p in points],
                }
            ),
            "points": points,
        }

    def publish(
        self,
        course_id: str,
        *,
        base_version_id: UUID | str | None,
        base_revision: int,
        snapshot_hash: str,
        chapters: list[CatalogChapter],
        assignments: list[CatalogAssignment],
        build_mode: Mode,
        rules_version: str,
        model_config: dict,
        job_id: UUID | str | None = None,
        lease_token: UUID | str | None = None,
    ) -> dict:
        """Atomically publish; return {version_id, revision, created}.

        Lock order: ingestion's course advisory lock, snapshot tables (SHARE),
        course row (SHARE), catalog head (UPDATE), optional task (UPDATE).
        SHARE table locks cover phantoms/source mutations by existing writers
        which do not use the advisory lock. They briefly block writes across
        courses; no inference, network access or callbacks occur in this scope.
        Use the store's default READ COMMITTED isolation and a short transaction.
        """
        chapters = [CatalogChapter.model_validate(c) for c in chapters]
        assignments = [CatalogAssignment.model_validate(a) for a in assignments]
        base_version_id = UUID(str(base_version_id)) if base_version_id else None
        if base_revision < 0 or build_mode not in ("initial", "incremental"):
            raise CatalogInvalid("无效修订号或构建方式")
        if (build_mode == "initial") != (base_version_id is None) or not rules_version.strip():
            raise CatalogInvalid("构建方式必须与基础版本一致，规则版本不能为空")
        if not isinstance(model_config, dict) or ((job_id is None) != (lease_token is None)):
            raise CatalogInvalid("模型配置必须为对象，任务 ID 和租约令牌必须同时提供")
        fingerprint = digest(
            {
                "base_version_id": base_version_id,
                "base_revision": base_revision,
                "snapshot_hash": snapshot_hash,
                "build_mode": build_mode,
                "rules_version": rules_version,
                "model_config": model_config,
                "chapters": [
                    c.model_dump(mode="json")
                    for c in sorted(chapters, key=lambda c: str(c.chapter_id))
                ],
                "assignments": [
                    a.model_dump(mode="json")
                    for a in sorted(assignments, key=lambda a: str(a.point_id))
                ],
            }
        )
        with self.store.transaction():
            if (
                self.connection.execute("SHOW transaction_isolation").fetchone()[
                    "transaction_isolation"
                ]
                != "read committed"
            ):
                raise CatalogInvalid("目录发布要求 READ COMMITTED 事务隔离级别")
            self.connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"knowledge:{self.user}:{course_id}",),
            )
            self.connection.execute(
                "LOCK TABLE knowledge_points,knowledge_facts,fact_evidence,documents,"
                "material_versions,material_locators IN SHARE MODE"
            )
            course = self.connection.execute(
                "SELECT status FROM courses WHERE user_id=%s AND course_id=%s FOR SHARE",
                (self.user, course_id),
            ).fetchone()
            if not course or course["status"] in {"deleted", "purged"}:
                raise CatalogInvalid("课程不存在或已删除")
            self.connection.execute(
                "INSERT INTO course_knowledge_catalog_heads(user_id,course_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING",
                (self.user, course_id),
            )
            head = self.connection.execute(
                "SELECT * FROM course_knowledge_catalog_heads "
                "WHERE user_id=%s AND course_id=%s FOR UPDATE",
                (self.user, course_id),
            ).fetchone()
            current = (
                self.version(course_id, head["current_version_id"])
                if (head["current_version_id"])
                else None
            )
            duplicate = bool(current and current["publication_hash"] == fingerprint)
            if not duplicate and (
                head["current_version_id"] != base_version_id or head["revision"] != base_revision
            ):
                raise CatalogConflict("目录基础版本或修订号已变化")
            snapshot = self.snapshot(course_id)
            if snapshot["hash"] != snapshot_hash:
                raise CatalogSnapshotChanged("课程可展示知识或分类输入已变化")
            self._validate(chapters, assignments, snapshot)
            job = None
            if job_id is not None:
                job = self.connection.execute(
                    "SELECT * FROM course_knowledge_catalog_jobs "
                    "WHERE user_id=%s AND course_id=%s AND id=%s FOR UPDATE",
                    (self.user, course_id, job_id),
                ).fetchone()
                if not job or (job["base_version_id"], job["snapshot_hash"], job["mode"]) != (
                    base_version_id,
                    snapshot_hash,
                    build_mode,
                ):
                    raise CatalogConflict("目录任务与发布输入不一致")
                # A completed identical task may be safely acknowledged without a write.
                if not (
                    duplicate
                    and job["status"] == "succeeded"
                    and job["result_version_id"] == head["current_version_id"]
                ):
                    self._fence(course_id, job_id, lease_token)
            if duplicate:
                version_id = head["current_version_id"]
            else:
                version_id = uuid4()
                self.connection.execute(
                    """INSERT INTO course_knowledge_catalog_versions(id,user_id,course_id,
                       parent_version_id,build_mode,knowledge_snapshot_hash,publication_hash,
                       rules_version,model_config,chapter_count,point_count)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        version_id,
                        self.user,
                        course_id,
                        base_version_id,
                        build_mode,
                        snapshot_hash,
                        fingerprint,
                        rules_version,
                        Jsonb(model_config),
                        len(chapters),
                        len(assignments),
                    ),
                )
                for chapter in chapters:
                    self.connection.execute(
                        """INSERT INTO course_knowledge_catalog_chapters(version_id,chapter_id,
                           user_id,course_id,title,normalized_title,description,sort_order)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            version_id,
                            chapter.chapter_id,
                            self.user,
                            course_id,
                            chapter.title,
                            normalize(chapter.title),
                            chapter.description,
                            chapter.sort_order,
                        ),
                    )
                for assignment in assignments:
                    self.connection.execute(
                        """INSERT INTO course_knowledge_catalog_assignments(version_id,user_id,
                           course_id,point_id,chapter_id,classification_input_hash,assignment_method,
                           reason,sort_order) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            version_id,
                            self.user,
                            course_id,
                            assignment.point_id,
                            assignment.chapter_id,
                            assignment.classification_input_hash,
                            assignment.assignment_method,
                            assignment.reason,
                            assignment.sort_order,
                        ),
                    )
                head = self.connection.execute(
                    """UPDATE course_knowledge_catalog_heads SET current_version_id=%s,
                       revision=revision+1,updated_at=now() WHERE user_id=%s AND course_id=%s
                       RETURNING *""",
                    (version_id, self.user, course_id),
                ).fetchone()
            if job and job["status"] != "succeeded":
                # Recheck wall-clock expiry after all writes, not transaction-start now().
                self._fence(course_id, job_id, lease_token)
                self.connection.execute(
                    """UPDATE course_knowledge_catalog_jobs
                       SET status='succeeded',stage='published',
                       result_version_id=%s,lease_token=NULL,lease_until=NULL,error_code=NULL,
                       error_message=NULL,updated_at=clock_timestamp() WHERE id=%s""",
                    (version_id, job_id),
                )
            return {
                "version_id": version_id,
                "revision": head["revision"],
                "created": not duplicate,
            }

    @staticmethod
    def _validate(chapters, assignments, snapshot):
        chapter_ids = {c.chapter_id for c in chapters}
        names = [normalize(c.title) for c in chapters]
        if (
            len(chapter_ids) != len(chapters)
            or len(set(names)) != len(names)
            or any(not n for n in names)
            or len({c.sort_order for c in chapters}) != len(chapters)
        ):
            raise CatalogInvalid("章节 ID、规范化名称或顺序重复，或标题无效")
        expected = {p["point_id"]: p["classification_input_hash"] for p in snapshot["points"]}
        if len({a.point_id for a in assignments}) != len(assignments) or {
            str(a.point_id) for a in assignments
        } != set(expected):
            raise CatalogInvalid("知识点必须完整覆盖当前可展示集合且不得重复或跨课程")
        for assignment in assignments:
            if assignment.chapter_id is not None and assignment.chapter_id not in chapter_ids:
                raise CatalogInvalid("知识点引用了本版本以外的章节")
            if (assignment.chapter_id is None) != (assignment.assignment_method == "unclassified"):
                raise CatalogInvalid("未归类知识点必须使用 unclassified 方法")
            if assignment.classification_input_hash != expected[str(assignment.point_id)]:
                raise CatalogSnapshotChanged("知识点分类输入哈希已变化")

    def create_job(
        self,
        course_id: str,
        *,
        mode: Mode,
        base_version_id: UUID | str | None,
        snapshot_hash: str,
    ) -> dict:
        """Idempotent by (user, course, mode, base version, snapshot), even terminal.

        A different nonterminal job raises CatalogConflict. Retry policy must
        choose retry_wait before marking a task terminal; terminal jobs remain
        history and are never silently restarted by this method.
        """
        base_version_id = UUID(str(base_version_id)) if base_version_id else None
        with self.store.transaction():
            row = self.connection.execute(
                """INSERT INTO course_knowledge_catalog_jobs(user_id,course_id,mode,
                   base_version_id,snapshot_hash) VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING RETURNING *""",
                (self.user, course_id, mode, base_version_id, snapshot_hash),
            ).fetchone()
            if row:
                return row
            row = self.connection.execute(
                "SELECT * FROM course_knowledge_catalog_jobs WHERE user_id=%s AND course_id=%s "
                "AND mode=%s AND base_version_id IS NOT DISTINCT FROM %s::uuid "
                "AND snapshot_hash=%s FOR UPDATE",
                (self.user, course_id, mode, base_version_id, snapshot_hash),
            ).fetchone()
            if not row or (row["mode"], row["base_version_id"], row["snapshot_hash"]) != (
                mode,
                base_version_id,
                snapshot_hash,
            ):
                raise CatalogConflict("本课程已有不同的目录任务；请重新读取任务状态")
            return row

    def job(self, course_id: str, job_id: UUID | str) -> dict | None:
        return self.connection.execute(
            "SELECT * FROM course_knowledge_catalog_jobs "
            "WHERE user_id=%s AND course_id=%s AND id=%s",
            (self.user, course_id, job_id),
        ).fetchone()

    def claim(self, *, lease_seconds: int = 900) -> dict | None:
        """Claim one task of the bound user; expired leases get a fresh token."""
        if lease_seconds <= 0:
            raise CatalogInvalid("租约时长必须为正数")
        with self.store.transaction():
            return self.connection.execute(
                """WITH candidate AS (
                     SELECT id FROM course_knowledge_catalog_jobs WHERE user_id=%s AND
                     ((status IN ('queued','retry_wait') AND next_run_at<=clock_timestamp()) OR
                      (status='running' AND lease_until<=clock_timestamp()))
                     ORDER BY next_run_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                   UPDATE course_knowledge_catalog_jobs j SET status='running',attempt=attempt+1,
                     lease_token=%s,lease_until=clock_timestamp()+%s*interval '1 second',
                     updated_at=clock_timestamp(),error_code=NULL,error_message=NULL
                   FROM candidate c WHERE j.id=c.id RETURNING j.*""",
                (self.user, uuid4(), lease_seconds),
            ).fetchone()

    def _fence(self, course_id, job_id, lease_token):
        row = self.connection.execute(
            "SELECT * FROM course_knowledge_catalog_jobs WHERE user_id=%s AND course_id=%s "
            "AND id=%s AND status='running' AND lease_token=%s "
            "AND lease_until>clock_timestamp() FOR UPDATE",
            (self.user, course_id, job_id, lease_token),
        ).fetchone()
        if row is None:
            raise CatalogLeaseLost("目录任务租约已失效")
        return row

    def checkpoint(
        self,
        course_id: str,
        job_id: UUID | str,
        lease_token: UUID | str,
        *,
        stage: str,
        checkpoint: dict,
        lease_seconds: int = 900,
    ) -> dict:
        if lease_seconds <= 0 or not isinstance(checkpoint, dict):
            raise CatalogInvalid("检查点必须为对象，租约时长必须为正数")
        with self.store.transaction():
            self._fence(course_id, job_id, lease_token)
            return self.connection.execute(
                """UPDATE course_knowledge_catalog_jobs SET stage=%s,checkpoint=%s,
                   lease_until=clock_timestamp()+%s*interval '1 second',
                   updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
                (stage, Jsonb(checkpoint), lease_seconds, job_id),
            ).fetchone()

    def fail(
        self,
        course_id: str,
        job_id: UUID | str,
        lease_token: UUID | str,
        *,
        error_code: str,
        error_message: str,
        retry_at: datetime | None = None,
    ) -> dict:
        """Caller chooses retry policy; no scanner, worker or automatic backoff."""
        with self.store.transaction():
            self._fence(course_id, job_id, lease_token)
            return self.connection.execute(
                """UPDATE course_knowledge_catalog_jobs SET status=%s,error_code=%s,
                   error_message=%s,next_run_at=coalesce(%s,next_run_at),lease_token=NULL,
                   lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
                (
                    "retry_wait" if retry_at else "failed",
                    error_code,
                    error_message,
                    retry_at,
                    job_id,
                ),
            ).fetchone()
