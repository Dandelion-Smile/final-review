"""PostgreSQL persistence for independent course knowledge build jobs."""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from .config import Settings
from .knowledge import digest, normalize
from .source_locators import material_version


class LeaseLost(RuntimeError):
    pass


class SnapshotChanged(RuntimeError):
    pass


class SourceUnavailable(ValueError):
    pass


def safe_error(exc):
    name = type(exc).__name__
    if "Timeout" in name:
        return "模型请求超时；已保存的提取结果和语义块会在重试时复用"
    if name == "ModelOutputLimitError":
        return "模型输出达到长度上限，请减小语义块或提高知识库输出上限"
    if isinstance(exc, SourceUnavailable):
        return "资料或模型配置已变化，请重新构建"
    return f"知识库构建失败（{name}），可重试；已保存的结果会保留"


def build_config(settings):
    models = settings.available_chat_models()
    model = next((x for x in models if x.id == settings.knowledge_model_id), None)
    return dict(
        model_id=settings.knowledge_model_id,
        model=model.model if model else None,
        base_url=model.base_url if model else None,
        embedding_model=settings.embedding_model,
        embedding_base_url=settings.embedding_base_url,
        embedding_dimensions=settings.embedding_dimensions,
        block_chars=settings.knowledge_block_chars,
        block_pages=settings.knowledge_block_pages,
        top_k=settings.knowledge_top_k,
        min_similarity=settings.knowledge_min_similarity,
        policy_version=settings.knowledge_extractor_version,
    )


def embedding_config(config):
    return {
        key: config[key]
        for key in ("embedding_model", "embedding_base_url", "embedding_dimensions")
    }


def enqueue(cursor, document, settings=None):
    if document.get("knowledge_status") == "no_reliable_text":
        return dict(status="skipped", reason="no_reliable_text")
    settings = settings or Settings()
    version = material_version(document)
    config = build_config(settings)
    strategy = f"{settings.knowledge_extractor_version}:{digest(config)[:12]}"
    cursor.execute(
        """INSERT INTO knowledge_build_jobs(user_id,course_id,document_id,material_version_id,
           extractor_version,config_json) VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT(user_id,material_version_id,extractor_version)
           DO UPDATE SET extractor_version=EXCLUDED.extractor_version RETURNING *""",
        (
            document["user_id"],
            document["course_id"],
            document["document_id"],
            version["material_version_id"],
            strategy,
            Jsonb(config),
        ),
    )
    return cursor.fetchone()


class KnowledgeRepository:
    def __init__(self, store):
        self.store = store

    @property
    def connection(self):
        return self.store.connection

    @property
    def user(self):
        return self.store._user()

    def enqueue_ready(self, course_id, document_id, settings):
        """Cover reused uploads parsed before knowledge builds were installed."""
        with self.store.transaction():
            row = self.connection.execute(
                "SELECT data FROM documents WHERE user_id=%s AND course_id=%s "
                "AND document_id=%s FOR SHARE",
                (self.user, course_id, document_id),
            ).fetchone()
            if row is None or row["data"].get("parse_status") != "ready":
                raise SourceUnavailable("资料尚未解析完成或已删除")
            self.store.ensure_material_source(row["data"])
            with self.connection.cursor() as cursor:
                return enqueue(cursor, row["data"], settings)

    def claim(self, job_id=None):
        with self.store.transaction():
            self.connection.execute(
                """UPDATE knowledge_build_jobs SET status='failed',error_code='lease_expired',
                   error_message='任务租约过期，请重试',lease_until=NULL,finished_at=now(),
                   updated_at=now() WHERE status='running' AND lease_until<now()
                   AND attempts>=max_attempts"""
            )
            return self.connection.execute(
                """WITH next_job AS (
                     SELECT job_id FROM knowledge_build_jobs
                     WHERE (status='queued' AND available_at<=now() OR
                            status='running' AND lease_until<now()) AND attempts<max_attempts
                     AND (%s::uuid IS NULL OR job_id=%s)
                     ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                   UPDATE knowledge_build_jobs j SET status='running',attempts=attempts+1,
                     lease_until=now()+interval '15 minutes',started_at=now(),finished_at=NULL,
                     error_code=NULL,error_message=NULL,updated_at=now()
                   FROM next_job WHERE j.job_id=next_job.job_id RETURNING j.*""",
                (job_id, job_id),
            ).fetchone()

    def fence(self, job):
        row = self.connection.execute(
            """SELECT job_id FROM knowledge_build_jobs WHERE job_id=%s AND user_id=%s
               AND status='running' AND attempts=%s AND lease_until>now() FOR UPDATE""",
            (job["job_id"], self.user, job["attempts"]),
        ).fetchone()
        if row is None:
            raise LeaseLost("知识库构建任务租约已失效")

    def progress(self, job, stage=None):
        return bool(
            self.connection.execute(
                """UPDATE knowledge_build_jobs SET stage=coalesce(%s,stage),
               lease_until=now()+interval '15 minutes',updated_at=now()
               WHERE job_id=%s AND user_id=%s AND attempts=%s AND status='running'
               AND lease_until>now() RETURNING job_id""",
                (stage, job["job_id"], self.user, job["attempts"]),
            ).fetchone()
        )

    def fail(self, job, exc, *, retry=True):
        retry = retry and job["attempts"] < job["max_attempts"]
        self.connection.execute(
            """UPDATE knowledge_build_jobs SET status=%s,error_code=%s,error_message=%s,
               available_at=now()+interval '5 seconds',lease_until=NULL,updated_at=now(),
               finished_at=CASE WHEN %s THEN NULL ELSE now() END
               WHERE job_id=%s AND user_id=%s AND attempts=%s AND status='running'""",
            (
                "queued" if retry else "failed",
                type(exc).__name__,
                safe_error(exc),
                retry,
                job["job_id"],
                self.user,
                job["attempts"],
            ),
        )

    def retry(self, course, job_id):
        return self.connection.execute(
            """UPDATE knowledge_build_jobs SET status='queued',attempts=0,stage=NULL,
               error_code=NULL,error_message=NULL,lease_until=NULL,available_at=now(),
               finished_at=NULL,updated_at=now() WHERE job_id=%s AND user_id=%s AND course_id=%s
               AND status='failed' RETURNING *""",
            (job_id, self.user, course),
        ).fetchone()

    def source(self, job, *, lock=False):
        course = self.connection.execute(
            "SELECT status FROM courses WHERE user_id=%s AND course_id=%s"
            + (" FOR SHARE" if lock else ""),
            (self.user, job["course_id"]),
        ).fetchone()
        row = self.connection.execute(
            "SELECT data FROM documents WHERE user_id=%s AND course_id=%s AND document_id=%s"
            + (" FOR SHARE" if lock else ""),
            (self.user, job["course_id"], job["document_id"]),
        ).fetchone()
        if (
            not course
            or course["status"] in {"deleted", "purged"}
            or not row
            or row["data"].get("parse_status") != "ready"
            or material_version(row["data"])["material_version_id"] != job["material_version_id"]
        ):
            raise SourceUnavailable("资料已删除、版本已变化或尚未解析完成")
        return row["data"]

    def locators(self, job):
        return self.connection.execute(
            """SELECT * FROM material_locators WHERE user_id=%s AND course_id=%s
               AND material_version_id=%s AND locator_kind='chunk' ORDER BY ordinal,locator_id""",
            (self.user, job["course_id"], job["material_version_id"]),
        ).fetchall()

    def blocks(self, job, blocks):
        with self.store.transaction():
            self.fence(job)
            for block in blocks:
                self.connection.execute(
                    """INSERT INTO material_extraction_blocks(user_id,course_id,build_job_id,
                       block_index,title,content,page_start,page_end,source_locator_ids,content_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(build_job_id,block_index) DO NOTHING""",
                    (
                        self.user,
                        job["course_id"],
                        job["job_id"],
                        block["block_index"],
                        block["title"],
                        block["content"],
                        block["page_start"],
                        block["page_end"],
                        Jsonb(block["source_locator_ids"]),
                        block["content_hash"],
                    ),
                )
        return self.connection.execute(
            "SELECT * FROM material_extraction_blocks WHERE build_job_id=%s AND user_id=%s "
            "ORDER BY block_index",
            (job["job_id"], self.user),
        ).fetchall()

    def save_block(self, job, block, candidates, *, complete=True):
        with self.store.transaction():
            self.fence(job)
            for index, candidate in enumerate(candidates):
                index = candidate.get("candidate_index", index)
                self.connection.execute(
                    """INSERT INTO knowledge_candidates(user_id,course_id,build_job_id,block_id,
                       candidate_index,name,normalized_name,description,importance_score,
                       facts_json,embedding,status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)
                       ON CONFLICT(block_id,candidate_index) DO UPDATE SET
                         facts_json=EXCLUDED.facts_json,embedding=EXCLUDED.embedding,
                         status=EXCLUDED.status,updated_at=now()""",
                    (
                        self.user,
                        job["course_id"],
                        job["job_id"],
                        block["id"],
                        index,
                        candidate["name"],
                        candidate["normalized_name"],
                        candidate["description"],
                        candidate["importance_score"],
                        Jsonb(candidate["facts_json"]),
                        (
                            self.store._vector(candidate["embedding"])
                            if candidate.get("embedding") is not None
                            else None
                        ),
                        candidate["status"],
                    ),
                )
            self.connection.execute(
                """UPDATE material_extraction_blocks SET extract_status=%s,
                   error_message=NULL,updated_at=now() WHERE id=%s AND user_id=%s""",
                ("succeeded" if complete else "pending", block["id"], self.user),
            )

    def save_candidate_facts(self, job, candidate):
        with self.store.transaction():
            self.fence(job)
            self.connection.execute(
                "UPDATE knowledge_candidates SET facts_json=%s,updated_at=now() "
                "WHERE id=%s AND user_id=%s AND build_job_id=%s",
                (Jsonb(candidate["facts_json"]), candidate["id"], self.user, job["job_id"]),
            )

    def match_decision(self, job, candidate, target, phase, fingerprint, version):
        row = self.connection.execute(
            """SELECT decision_json FROM knowledge_match_decisions WHERE user_id=%s
               AND build_job_id=%s AND candidate_id=%s AND phase=%s AND target_key=%s
               AND input_hash=%s AND matcher_version=%s AND status='succeeded'""",
            (
                self.user,
                job["job_id"],
                candidate["id"],
                phase,
                str(target["id"]),
                fingerprint,
                version,
            ),
        ).fetchone()
        return row["decision_json"] if row else None

    def record_match(
        self,
        job,
        candidate,
        target,
        phase,
        fingerprint,
        version,
        input_data,
        decision,
        status,
        error=None,
    ):
        with self.store.transaction():
            self.fence(job)
            target_candidate = target["id"] if phase == "dedup_internal" else None
            target_point = (
                target["id"] if phase == "matching" and not target.get("_planned") else None
            )
            self.connection.execute(
                """INSERT INTO knowledge_match_decisions(user_id,course_id,build_job_id,
                   candidate_id,phase,target_key,target_candidate_id,target_point_id,input_hash,
                   matcher_version,input_json,decision_json,status,error_code,attempt_history)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(build_job_id,candidate_id,phase,target_key,
                               input_hash,matcher_version)
                   DO UPDATE SET decision_json=EXCLUDED.decision_json,status=EXCLUDED.status,
                   error_code=EXCLUDED.error_code,attempts=knowledge_match_decisions.attempts+1,
                   attempt_history=knowledge_match_decisions.attempt_history
                                   || EXCLUDED.attempt_history,
                   updated_at=now()""",
                (
                    self.user,
                    job["course_id"],
                    job["job_id"],
                    candidate["id"],
                    phase,
                    str(target["id"]),
                    target_candidate,
                    target_point,
                    fingerprint,
                    version,
                    Jsonb(input_data),
                    Jsonb(decision),
                    status,
                    error,
                    Jsonb([{"status": status, "error_code": error, "decision": decision}]),
                ),
            )

    def block_candidates(self, job, block):
        return self.vectors(
            self.connection.execute(
                "SELECT * FROM knowledge_candidates WHERE user_id=%s AND build_job_id=%s "
                "AND block_id=%s ORDER BY candidate_index",
                (self.user, job["job_id"], block["id"]),
            ).fetchall()
        )

    def fail_block(self, job, block, exc=None):
        with self.store.transaction():
            self.fence(job)
            self.connection.execute(
                """UPDATE material_extraction_blocks SET extract_status='failed',
                   error_message=%s,updated_at=now()
                   WHERE id=%s AND user_id=%s""",
                (safe_error(exc), block["id"], self.user),
            )

    @staticmethod
    def vectors(rows):
        for row in rows:
            if row.get("embedding") is not None:
                row["embedding"] = json.loads(row["embedding"])
        return rows

    def candidates(self, job):
        return self.vectors(
            self.connection.execute(
                """SELECT c.* FROM knowledge_candidates c JOIN material_extraction_blocks b
               ON c.block_id=b.id WHERE c.user_id=%s AND c.build_job_id=%s
               ORDER BY b.block_index,c.candidate_index""",
                (self.user, job["job_id"]),
            ).fetchall()
        )

    def save_internal(self, job, candidates):
        with self.store.transaction():
            self.fence(job)
            for c in candidates:
                self.connection.execute(
                    """UPDATE knowledge_candidates SET facts_json=%s,status=%s,
                       matched_candidate_id=%s,match_relation=%s,match_method=%s,match_score=%s,
                       match_reason=%s,updated_at=now() WHERE id=%s AND user_id=%s
                       AND build_job_id=%s""",
                    (
                        Jsonb(c["facts_json"]),
                        c["status"],
                        c.get("matched_candidate_id"),
                        c.get("match_relation"),
                        c.get("match_method"),
                        c.get("match_score"),
                        c.get("match_reason"),
                        c["id"],
                        self.user,
                        job["job_id"],
                    ),
                )

    def _snapshot_rows(self, course):
        return self.connection.execute(
            """SELECT p.*,coalesce((SELECT jsonb_agg(jsonb_build_object('fact_key',f.id::text,
                   'fact_text',f.fact_text,'normalized_text',f.normalized_text,'status',f.status,
                   'updated_at',f.updated_at,'evidence','[]'::jsonb) ORDER BY f.id)
                 FROM knowledge_facts f WHERE f.user_id=p.user_id AND f.course_id=p.course_id
                   AND f.point_id=p.id AND f.status<>'inactive'),'[]'::jsonb) AS facts
               FROM knowledge_points p WHERE p.user_id=%s AND p.course_id=%s AND p.status='active'
               ORDER BY p.id""",
            (self.user, course),
        ).fetchall()

    def snapshot(self, course):
        # A single SQL statement gives a consistent MVCC snapshot of points and facts.
        rows = self._snapshot_rows(course)
        fingerprint = digest(rows)
        self.vectors(rows)
        for point in rows:
            point["facts_json"] = {"schema_version": 1, "facts": point.pop("facts")}
        return fingerprint, rows

    def publish(self, job, fingerprint, plan):
        with self.store.transaction():
            self.connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"knowledge:{self.user}:{job['course_id']}",),
            )
            self.fence(job)
            self.source(job, lock=True)
            if digest(self._snapshot_rows(job["course_id"])) != fingerprint:
                raise SnapshotChanged("课程知识库已被其他任务更新，重新匹配")
            self.progress(job, "merging")
            for entry in plan:
                self._publish_candidate(job, entry)
            self.connection.execute(
                "UPDATE material_note_heads h SET knowledge_revision_id=h.indexed_revision_id,"
                "updated_at=now() FROM documents d WHERE h.user_id=d.user_id "
                "AND h.course_id=d.course_id AND h.document_id=d.document_id "
                "AND h.user_id=%s AND h.course_id=%s AND h.document_id=%s "
                "AND d.data->>'material_version_id'=%s",
                (self.user, job["course_id"], job["document_id"], job["material_version_id"]),
            )
            self.connection.execute(
                """UPDATE knowledge_build_jobs SET status='succeeded',stage='merging',
                   lease_until=NULL,finished_at=now(),updated_at=now()
                   WHERE job_id=%s AND user_id=%s AND attempts=%s""",
                (job["job_id"], self.user, job["attempts"]),
            )

    def _publish_candidate(self, job, entry):
        candidate, action = entry["candidate"], entry["action"]
        point_id = entry["point"]["id"] if entry["point"] else None
        if action == "new":
            point_id = entry["new_point_id"]
            data = dict(
                id=str(point_id),
                name=candidate["name"],
                description=candidate["description"],
                user_id=str(self.user),
                course_id=job["course_id"],
                status="active",
                embedding_config=embedding_config(job["config_json"]),
            )
            self.connection.execute(
                """INSERT INTO knowledge_points(record_key,id,user_id,course_id,created_at,data,
                   name,normalized_name,description,embedding,importance_score,status)
                   VALUES (%s,%s,%s,%s,now(),%s,%s,%s,%s,%s::vector,%s,'active')""",
                (
                    str(point_id),
                    point_id,
                    self.user,
                    job["course_id"],
                    Jsonb(data),
                    candidate["name"],
                    candidate["normalized_name"],
                    candidate["description"],
                    self.store._vector(candidate["embedding"]),
                    candidate["importance_score"],
                ),
            )
        elif action == "merged":
            self.connection.execute(
                """UPDATE knowledge_points SET updated_at=now(),importance_score=
                   greatest(importance_score,%s) WHERE user_id=%s AND course_id=%s AND id=%s""",
                (candidate["importance_score"], self.user, job["course_id"], point_id),
            )
        if point_id:
            matches = {p.incoming_key: p.existing_key for p in entry["pairs"]}
            for fact in candidate["facts_json"]["facts"]:
                if fact["status"] != "supported":
                    continue
                existing = matches.get(fact["fact_key"])
                if existing:
                    row = self.connection.execute(
                        """UPDATE knowledge_facts SET status='supported',updated_at=now()
                           WHERE id=%s AND user_id=%s AND course_id=%s AND point_id=%s
                           RETURNING id""",
                        (existing, self.user, job["course_id"], point_id),
                    ).fetchone()
                    if not row:
                        raise SnapshotChanged("事实匹配目标已变化")
                else:
                    row = self.connection.execute(
                        """INSERT INTO knowledge_facts(id,user_id,course_id,point_id,fact_text,
                           normalized_text,fact_hash,status)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,'supported')
                           ON CONFLICT(user_id,course_id,point_id,fact_hash) DO UPDATE SET
                           status='supported',updated_at=now() RETURNING id""",
                        (
                            entry["new_fact_ids"][fact["fact_key"]],
                            self.user,
                            job["course_id"],
                            point_id,
                            fact["fact_text"],
                            normalize(fact["fact_text"]),
                            digest(normalize(fact["fact_text"])),
                        ),
                    ).fetchone()
                for evidence in fact["evidence"]:
                    if not evidence["quote_verified"]:
                        continue
                    self.connection.execute(
                        """INSERT INTO fact_evidence(user_id,course_id,fact_id,material_version_id,
                           locator_id,quote,quote_hash,quote_verified,support_status,verification_method)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s,%s)
                           ON CONFLICT(user_id,course_id,fact_id,material_version_id,
                                       locator_id,quote_hash)
                           DO UPDATE SET quote_verified=true,support_status=EXCLUDED.support_status,
                           verification_method=EXCLUDED.verification_method,updated_at=now()""",
                        (
                            self.user,
                            job["course_id"],
                            row["id"],
                            evidence["material_version_id"],
                            evidence["locator_id"],
                            evidence["quote"],
                            digest(evidence["quote"]),
                            evidence["support_status"],
                            evidence["verification_method"],
                        ),
                    )
        self.connection.execute(
            """UPDATE knowledge_candidates SET status=%s,matched_point_id=%s,
               match_relation=%s,match_method='llm',match_score=%s,match_reason=%s,updated_at=now()
               WHERE id=%s AND user_id=%s AND build_job_id=%s""",
            (
                action,
                point_id,
                entry.get("relation"),
                entry.get("score"),
                entry["reason"],
                candidate["id"],
                self.user,
                job["job_id"],
            ),
        )
