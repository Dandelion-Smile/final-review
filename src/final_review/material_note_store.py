"""PostgreSQL transactions for reading notes and independent material jobs."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from .material_notes import STRATEGY, NoteConflict, digest, initial_body, input_hash, validate_body


class MaterialNoteStore:
    def note_head(self, course_id, document_id, *, lock=False):
        return self.connection.execute(
            "SELECT * FROM material_note_heads WHERE user_id=%s AND course_id=%s "
            "AND document_id=%s" + (" FOR UPDATE" if lock else ""),
            (self._user(), course_id, document_id),
        ).fetchone()

    def note_revision(self, course_id, document_id, revision_id):
        return self.connection.execute(
            "SELECT revision_id,revision_no,parent_revision_id,schema_version,body,input_hash,"
            "edited_by,created_at FROM material_note_revisions "
            "WHERE user_id=%s AND course_id=%s AND document_id=%s AND revision_id=%s",
            (self._user(), course_id, document_id, revision_id),
        ).fetchone()

    def read_material_note(self, course_id, document_id):
        # A single snapshot avoids mixing heads and revisions from concurrent saves.
        with self.transaction():
            head = self.note_head(course_id, document_id, lock=True)
            if not head:
                return None
            revision = self.note_revision(course_id, document_id, head["latest_saved_revision_id"])
            indexed = (
                self.note_revision(course_id, document_id, head["indexed_revision_id"])
                if head["indexed_revision_id"]
                else None
            )
            pending = revision["revision_id"] != head["initial_revision_id"]
            initial = self.note_revision(course_id, document_id, head["initial_revision_id"])
            return dict(
                revision=revision,
                latest_saved_revision_id=revision["revision_id"],
                initial_revision_id=head["initial_revision_id"],
                indexed_revision_id=head["indexed_revision_id"],
                knowledge_revision_id=head["knowledge_revision_id"],
                text_generation=head["text_generation"],
                sync_status="pending_integration"
                if pending and revision["input_hash"] != initial["input_hash"]
                else "current"
                if indexed and indexed["input_hash"] == revision["input_hash"]
                else "indexing",
            )

    def material_note_summary(self, document):
        """Lightweight polling: never return the document body or filesystem paths."""
        course_id, document_id = document["course_id"], document["document_id"]
        head = self.note_head(course_id, document_id)
        if not head:
            return dict(
                note_status=document.get("preview_status", "queued"),
                parse_error=document.get("preview_error"),
            )
        revisions = self.connection.execute(
            "SELECT revision_id,revision_no FROM material_note_revisions "
            "WHERE user_id=%s AND course_id=%s AND document_id=%s AND revision_id=ANY(%s)",
            (
                self._user(),
                course_id,
                document_id,
                [
                    head[key]
                    for key in (
                        "latest_saved_revision_id",
                        "indexed_revision_id",
                        "knowledge_revision_id",
                    )
                    if head[key]
                ],
            ),
        ).fetchall()
        numbers = {r["revision_id"]: r["revision_no"] for r in revisions}
        knowledge = self.connection.execute(
            "SELECT status,stage,error_message FROM knowledge_build_jobs "
            "WHERE user_id=%s AND course_id=%s AND document_id=%s AND material_version_id=%s "
            "ORDER BY created_at DESC LIMIT 1",
            (self._user(), course_id, document_id, document.get("material_version_id")),
        ).fetchone()
        polish = None
        if head.get("polish_job_id"):
            polish = self.connection.execute(
                "SELECT stage,batches_done,batches_total,error_code,error_message "
                "FROM material_note_polish_jobs WHERE job_id=%s AND user_id=%s",
                (head["polish_job_id"], self._user()),
            ).fetchone()
        candidate_revision_id = head.get("candidate_revision_id")
        if candidate_revision_id and candidate_revision_id not in numbers:
            row = self.connection.execute(
                "SELECT revision_no FROM material_note_revisions WHERE user_id=%s "
                "AND course_id=%s AND document_id=%s AND revision_id=%s",
                (self._user(), course_id, document_id, candidate_revision_id),
            ).fetchone()
            if row:
                numbers[candidate_revision_id] = row["revision_no"]
        return dict(
            note_status="ready",
            latest_saved_revision_id=head["latest_saved_revision_id"],
            indexed_revision_id=head["indexed_revision_id"],
            knowledge_revision_id=head["knowledge_revision_id"],
            revision_numbers=numbers,
            index_status=document.get("parse_status", "queued"),
            index_error=document.get("parse_error"),
            knowledge_status=(
                knowledge["status"] if knowledge else document.get("knowledge_status", "waiting")
            ),
            knowledge_error=knowledge["error_message"] if knowledge else None,
            images_status=document.get("images_status", "queued"),
            polish_status=head.get("polish_status", "none"),
            polish_stage=polish["stage"] if polish else None,
            polish_batches_done=polish["batches_done"] if polish else None,
            polish_batches_total=polish["batches_total"] if polish else None,
            polish_error=(polish["error_message"] if polish else None)
            or document.get("polish_error"),
            candidate_revision_id=candidate_revision_id,
        )

    def _insert_note_revision(self, document, revision_id, number, parent, body):
        self.connection.execute(
            "INSERT INTO material_note_revisions(user_id,course_id,document_id,revision_id,"
            "revision_no,parent_revision_id,body,input_hash,edited_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                self._user(),
                document["course_id"],
                document["document_id"],
                revision_id,
                number,
                parent,
                Jsonb(body),
                input_hash(body),
                self._user(),
            ),
        )

    def save_material_note(self, document, base_revision_id, key, body):
        request_hash = digest([base_revision_id, body])
        course_id, document_id = document["course_id"], document["document_id"]
        with self.transaction():
            course = self.connection.execute(
                "SELECT status FROM courses WHERE user_id=%s AND course_id=%s FOR SHARE",
                (self._user(), course_id),
            ).fetchone()
            if not course or course["status"] != "active":
                raise ValueError("课程当前不可编辑")
            # Lock order matches worker: document before head.
            current = self.connection.execute(
                "SELECT data FROM documents WHERE user_id=%s AND course_id=%s "
                "AND document_id=%s FOR UPDATE",
                (self._user(), course_id, document_id),
            ).fetchone()
            if not current or current["data"].get("parse_status") == "deleted":
                raise ValueError("资料已删除")
            head = self.note_head(course_id, document_id, lock=True)
            if not head:
                raise ValueError("资料笔记尚未就绪")
            saved = self.connection.execute(
                "SELECT * FROM material_note_save_requests WHERE user_id=%s AND course_id=%s "
                "AND document_id=%s AND idempotency_key=%s",
                (self._user(), course_id, document_id, key),
            ).fetchone()
            if saved:
                if saved["request_hash"] != request_hash:
                    raise NoteConflict("相同幂等键对应不同修改", head["latest_saved_revision_id"])
                return dict(revision_id=saved["revision_id"], sync_status=saved["sync_status"])
            if base_revision_id != head["latest_saved_revision_id"]:
                raise NoteConflict(
                    "笔记已在其他页面保存，请对比最新版本", head["latest_saved_revision_id"]
                )
            if (
                head.get("polish_status") in {"queued", "running"}
                and base_revision_id == head["initial_revision_id"]
            ):
                # latest_revision_id stays None so clients show the message
                # instead of the cross-page conflict dialog.
                raise NoteConflict("笔记正在根据原文整理，完成后即可编辑", None)
            prior = self.note_revision(course_id, document_id, base_revision_id)
            normalized = validate_body(body, prior["body"])
            changed_text = input_hash(normalized) != prior["input_hash"]
            revision_id = base_revision_id
            if normalized != prior["body"]:
                revision_id = uuid4().hex
                sequence = self.connection.execute(
                    "SELECT COALESCE(max(revision_no),0)+1 AS n FROM material_note_revisions "
                    "WHERE user_id=%s AND course_id=%s AND document_id=%s",
                    (self._user(), course_id, document_id),
                ).fetchone()["n"]
                self._insert_note_revision(
                    document, revision_id, sequence, base_revision_id, normalized
                )
                self.connection.execute(
                    "UPDATE material_note_heads SET latest_saved_revision_id=%s,"
                    "save_sequence=%s,text_generation=text_generation+%s,"
                    "updated_at=now() WHERE user_id=%s AND course_id=%s AND document_id=%s",
                    (
                        revision_id,
                        sequence,
                        int(changed_text),
                        self._user(),
                        course_id,
                        document_id,
                    ),
                )
            initial = self.note_revision(course_id, document_id, head["initial_revision_id"])
            status = (
                "pending_integration"
                if input_hash(normalized) != initial["input_hash"]
                else "unchanged"
            )
            self.connection.execute(
                "INSERT INTO material_note_save_requests(user_id,course_id,document_id,"
                "idempotency_key,request_hash,revision_id,sync_status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (self._user(), course_id, document_id, key, request_hash, revision_id, status),
            )
            return dict(revision_id=revision_id, sync_status=status)

    def _insert_child_material_job(
        self, document, phase, revision_id=None, body=None, pages=None, strategy=None
    ):
        strategy = strategy or STRATEGY
        source_hash = document["content_sha256"]
        internal_key = digest([source_hash, strategy, phase, revision_id, pages])
        return self.connection.execute(
            "INSERT INTO material_jobs(job_id,user_id,course_id,document_id,fingerprint,status,"
            "phase,purpose,source_hash,parse_strategy,target_revision_id,text_generation,"
            "input_hash,target_pages,internal_key) "
            "VALUES (%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(user_id,document_id,internal_key) WHERE internal_key IS NOT NULL "
            "DO UPDATE SET internal_key=EXCLUDED.internal_key RETURNING job_id",
            (
                uuid4().hex,
                self._user(),
                document["course_id"],
                document["document_id"],
                internal_key,
                phase,
                "text" if phase == "parse" else "images",
                source_hash,
                strategy,
                revision_id,
                1 if revision_id else None,
                input_hash(body) if body else None,
                Jsonb(pages or []),
                internal_key,
            ),
        ).fetchone()["job_id"]

    def handoff_text_images_preview(self, job, pages, preview):
        with self.transaction(), self.connection.cursor() as cursor:
            document = self._lock_material_lease(cursor, job)
            if (
                not document
                or job["phase"] != "preview"
                or document.get("parse_strategy") != STRATEGY
            ):
                return False
            for page in pages:
                self._save_material_page(cursor, {**document, "preview_strategy": STRATEGY}, page)
            body = initial_body(pages, document["content_sha256"])
            revision_id = uuid4().hex
            self._insert_note_revision(document, revision_id, 1, None, body)
            cursor.execute(
                "INSERT INTO material_note_heads(user_id,course_id,document_id,"
                "latest_saved_revision_id,initial_revision_id,save_sequence) "
                "VALUES (%s,%s,%s,%s,%s,1)",
                (self._user(), job["course_id"], job["document_id"], revision_id, revision_id),
            )
            targets = [p["position"] for p in pages if p["needs_page_image"]]
            self._insert_child_material_job(document, "parse", revision_id, body)
            if targets:
                self._insert_child_material_job(document, "images", pages=targets)
            cursor.execute(
                "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s",
                (
                    Jsonb(
                        {
                            **preview,
                            "preview_strategy": STRATEGY,
                            "note_status": "ready",
                            "parse_status": "queued",
                            "images_status": "queued" if targets else "not_required",
                        }
                    ),
                    self._user(),
                    job["document_id"],
                ),
            )
            cursor.execute(
                "UPDATE material_jobs SET status='succeeded',lease_until=NULL,"
                "finished_at=now() WHERE job_id=%s",
                (job["job_id"],),
            )
            return True

    def text_image_job_source(self, job):
        with self.transaction(), self.connection.cursor() as cursor:
            document = self._lock_material_lease(cursor, job)
            if (
                not document
                or document.get("parse_strategy") != STRATEGY
                or job.get("parse_strategy") != STRATEGY
                or job.get("source_hash") != document["content_sha256"]
            ):
                return None
            if job["phase"] == "parse":
                head = self.note_head(job["course_id"], job["document_id"], lock=True)
                if (
                    not head
                    or job["target_revision_id"] != head["initial_revision_id"]
                    or job["text_generation"] != head["processing_generation"]
                ):
                    return None
                revision = self.note_revision(
                    job["course_id"], job["document_id"], job["target_revision_id"]
                )
                if revision["input_hash"] != job["input_hash"]:
                    return None
                return revision
            return document

    def publish_text_images(self, job, prepared, chunks):
        with self.transaction():
            if not self.text_image_job_source(job):
                return False
            # Read again under the document lock: retain image/metadata changes.
            current = self.get("document", job["document_id"])
            owned = {
                key: value
                for key, value in prepared.items()
                if key
                in {
                    "cleaned_markdown",
                    "chunk_count",
                    "material_version_id",
                    "parse_status",
                    "parse_metrics",
                    "processing_pipeline",
                    "analysis_pages",
                    "quality_status",
                    "note_revision_id",
                    "knowledge_status",
                    "text_input_hash",
                }
            }
            current.update(owned)
            current.pop("parse_error", None)
            # The existing atomic publisher persists versions/locators and enqueues knowledge.
            # Its text-images branch must neither hand off images nor replace other fields.
            if not self.publish_material_job(
                job["job_id"], job["attempts"], current, chunks, lease_token=job["lease_token"]
            ):
                return False
            self.connection.execute(
                "UPDATE material_note_heads SET indexed_revision_id=%s,updated_at=now() "
                "WHERE user_id=%s AND course_id=%s AND document_id=%s",
                (job["target_revision_id"], self._user(), job["course_id"], job["document_id"]),
            )
            return True

    def checkpoint_page_image(self, job, number, *, error=None, cache_key=None):
        with self.transaction(), self.connection.cursor() as cursor:
            if not self.text_image_job_source(job) or number not in job["target_pages"]:
                return False
            patch = dict(
                image_status="failed" if error else "ready",
                image_error=error,
                image_cache_key=cache_key,
            )
            cursor.execute(
                "UPDATE material_page_results SET data=data || %s,updated_at=now() "
                "WHERE user_id=%s AND document_id=%s AND source_hash=%s "
                "AND strategy_version=%s AND page_number=%s",
                (
                    Jsonb(patch),
                    self._user(),
                    job["document_id"],
                    job["source_hash"],
                    STRATEGY,
                    number,
                ),
            )
            return cursor.rowcount == 1

    def finish_text_images(self, job):
        with self.transaction():
            document = self.text_image_job_source(job)
            if not document:
                return False
            pages = self.material_pages(document)
            targets = [p for p in pages if p["position"] in job["target_pages"]]
            if len(targets) != len(job["target_pages"]):
                return False
            complete = all(p.get("image_status") == "ready" for p in targets)
            state = (
                "ready"
                if complete
                else "partial"
                if any(p.get("image_status") == "ready" for p in targets)
                else "failed"
            )
            self.connection.execute(
                "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s",
                (Jsonb(dict(images_status=state)), self._user(), job["document_id"]),
            )
            self.connection.execute(
                "UPDATE material_jobs SET status=%s,lease_until=NULL,finished_at=now(),"
                "error_code=%s,error_message=%s WHERE job_id=%s",
                (
                    "succeeded" if complete else "failed",
                    None if complete else "image_failed",
                    None if complete else "部分原页图片生成失败，可重试",
                    job["job_id"],
                ),
            )
            return True

    def retry_page_images(self, document):
        with self.transaction():
            # Same job-before-document lock order as worker claims/checkpoints.
            row = self.connection.execute(
                "SELECT * FROM material_jobs WHERE user_id=%s AND document_id=%s "
                "AND parse_strategy=%s AND phase='images' FOR UPDATE",
                (self._user(), document["document_id"], STRATEGY),
            ).fetchone()
            if not row:
                return dict(status="not_required")
            current = self.connection.execute(
                "SELECT data FROM documents WHERE user_id=%s AND document_id=%s FOR UPDATE",
                (self._user(), document["document_id"]),
            ).fetchone()
            if not current or current["data"].get("parse_status") == "deleted":
                raise ValueError("资料已删除")
            if row["status"] == "failed":
                self.connection.execute(
                    "UPDATE material_jobs SET status='queued',phase_attempts=0,"
                    "lease_token=NULL,available_at=now(),finished_at=NULL,error_code=NULL,"
                    "error_message=NULL WHERE job_id=%s",
                    (row["job_id"],),
                )
                self.connection.execute(
                    "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s",
                    (Jsonb(dict(images_status="queued")), self._user(), document["document_id"]),
                )
            return dict(
                job_id=row["job_id"],
                status="queued" if row["status"] == "failed" else row["status"],
            )

    # ------------------------------------------------------------------
    # text-polish-v1: original snapshot + model-polished reading notes.
    # ------------------------------------------------------------------

    def handoff_text_polish_preview(
        self, job, pages, preview, metrics, *, model_config, input_hash, prompt_version
    ):
        """Commit snapshot pages, initial revision, head and both child jobs."""
        from .material_text_source import STRATEGY as POLISH

        with self.transaction(), self.connection.cursor() as cursor:
            document = self._lock_material_lease(cursor, job)
            if (
                not document
                or job["phase"] != "preview"
                or document.get("parse_strategy") != POLISH
            ):
                return False
            for page in pages:
                self._save_material_page(cursor, {**document, "preview_strategy": POLISH}, page)
            body = initial_body(pages, document["content_sha256"])
            revision_id = uuid4().hex
            self._insert_note_revision(document, revision_id, 1, None, body)
            polish_job_id = uuid4().hex
            cursor.execute(
                "INSERT INTO material_note_polish_jobs(job_id,user_id,course_id,document_id,"
                "source_hash,snapshot_revision_id,generation,purpose,model_config,input_hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,1,'initial',%s,%s) "
                "ON CONFLICT (user_id,document_id,generation) DO NOTHING",
                (
                    polish_job_id,
                    self._user(),
                    job["course_id"],
                    job["document_id"],
                    document["content_sha256"],
                    revision_id,
                    Jsonb(model_config),
                    input_hash,
                ),
            )
            cursor.execute(
                "INSERT INTO material_note_heads(user_id,course_id,document_id,"
                "latest_saved_revision_id,initial_revision_id,save_sequence,polish_status,"
                "polish_job_id,polish_generation) VALUES (%s,%s,%s,%s,%s,1,'queued',%s,1) "
                "ON CONFLICT (user_id,course_id,document_id) DO UPDATE SET "
                "polish_status='queued',polish_job_id=EXCLUDED.polish_job_id,"
                "polish_generation=material_note_heads.polish_generation+1,updated_at=now()",
                (
                    self._user(),
                    job["course_id"],
                    job["document_id"],
                    revision_id,
                    revision_id,
                    polish_job_id,
                ),
            )
            self._insert_child_material_job(document, "parse", revision_id, body, strategy=POLISH)
            cursor.execute(
                "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s",
                (
                    Jsonb(
                        {
                            **preview,
                            "preview_strategy": POLISH,
                            "note_status": "ready",
                            "parse_status": "queued",
                            "polish_status": "queued",
                        }
                    ),
                    self._user(),
                    job["document_id"],
                ),
            )
            cursor.execute(
                "UPDATE material_jobs SET status='succeeded',lease_until=NULL,"
                "finished_at=now() WHERE job_id=%s",
                (job["job_id"],),
            )
            return True

    def text_polish_job_source(self, job):
        from .material_text_source import STRATEGY as POLISH

        with self.transaction(), self.connection.cursor() as cursor:
            document = self._lock_material_lease(cursor, job)
            if (
                not document
                or document.get("parse_strategy") != POLISH
                or job.get("parse_strategy") != POLISH
                or job.get("source_hash") != document["content_sha256"]
            ):
                return None
            if job["phase"] == "parse":
                head = self.note_head(job["course_id"], job["document_id"], lock=True)
                if (
                    not head
                    or job["target_revision_id"] != head["initial_revision_id"]
                    or job["text_generation"] != head["processing_generation"]
                ):
                    return None
                revision = self.note_revision(
                    job["course_id"], job["document_id"], job["target_revision_id"]
                )
                if revision["input_hash"] != job["input_hash"]:
                    return None
                return revision
            return document

    def publish_text_polish(self, job, prepared, chunks):
        with self.transaction():
            if not self.text_polish_job_source(job):
                return False
            current = self.get("document", job["document_id"])
            owned = {
                key: value
                for key, value in prepared.items()
                if key
                in {
                    "cleaned_markdown",
                    "chunk_count",
                    "material_version_id",
                    "parse_status",
                    "parse_metrics",
                    "processing_pipeline",
                    "quality_status",
                    "note_revision_id",
                    "knowledge_status",
                    "text_input_hash",
                }
            }
            current.update(owned)
            current.pop("parse_error", None)
            if not self.publish_material_job(
                job["job_id"], job["attempts"], current, chunks, lease_token=job["lease_token"]
            ):
                return False
            self.connection.execute(
                "UPDATE material_note_heads SET indexed_revision_id=%s,updated_at=now() "
                "WHERE user_id=%s AND course_id=%s AND document_id=%s",
                (job["target_revision_id"], self._user(), job["course_id"], job["document_id"]),
            )
            return True

    def claim_polish_job(self) -> dict | None:
        """Claim one due polish job; expired leases fail without touching notes."""
        with self.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE material_note_polish_jobs AS j SET status='cancelled',"
                    "error_code='source_removed',error_message='资料已删除',lease_until=NULL,"
                    "finished_at=now() FROM documents AS d "
                    "WHERE j.document_id=d.document_id AND j.user_id=d.user_id "
                    "AND d.data->>'parse_status'='deleted' AND j.status IN ('queued','running')"
                )
                cursor.execute(
                    "UPDATE material_note_polish_jobs SET status='failed',"
                    "error_code='lease_expired',error_message='笔记整理多次中断，请重试',"
                    "finished_at=now(),lease_until=NULL WHERE status='running' "
                    "AND lease_until<now() AND phase_attempts>=max_attempts "
                    "RETURNING user_id,document_id"
                )
                for expired in cursor.fetchall():
                    cursor.execute(
                        "UPDATE material_note_heads SET polish_status='failed',"
                        "updated_at=now() WHERE user_id=%s AND document_id=%s "
                        "AND polish_status IN ('queued','running')",
                        (expired["user_id"], expired["document_id"]),
                    )
                    cursor.execute(
                        "UPDATE documents SET data=data || jsonb_build_object("
                        "'polish_status','failed') WHERE record_key=%s AND user_id=%s "
                        "AND data->>'parse_status'<>'deleted'",
                        (expired["document_id"], expired["user_id"]),
                    )
                cursor.execute(
                    "SELECT * FROM material_note_polish_jobs j WHERE "
                    "((status='queued' AND available_at<=now()) OR "
                    "(status='running' AND lease_until<now() AND phase_attempts<max_attempts)) "
                    "AND EXISTS (SELECT 1 FROM app_users u WHERE u.id=j.user_id "
                    "AND u.deletion_due_at IS NULL) "
                    "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                row = cursor.fetchone()
                if not row:
                    return None
                cursor.execute(
                    "UPDATE material_note_polish_jobs SET status='running',"
                    "attempts=attempts+1,phase_attempts=phase_attempts+1,lease_token=%s,"
                    "stage='claimed',started_at=now(),finished_at=NULL,error_code=NULL,"
                    "error_message=NULL,lease_until=now()+interval '15 minutes' "
                    "WHERE job_id=%s RETURNING *",
                    (uuid4().hex, row["job_id"]),
                )
                claimed = cursor.fetchone()
                cursor.execute(
                    "UPDATE material_note_heads SET polish_status='running',updated_at=now() "
                    "WHERE user_id=%s AND document_id=%s AND polish_generation=%s "
                    "AND polish_status IN ('queued','running')",
                    (row["user_id"], row["document_id"], row["generation"]),
                )
                return {
                    key: value.isoformat() if hasattr(value, "isoformat") else value
                    for key, value in claimed.items()
                }

    def renew_polish_job(self, job_id, attempt, lease_token=None) -> bool:
        return bool(
            self.connection.execute(
                "UPDATE material_note_polish_jobs SET lease_until=now()+interval '15 minutes' "
                "WHERE job_id=%s AND status='running' AND attempts=%s AND lease_until>now() "
                "AND (%s::text IS NULL OR lease_token=%s) RETURNING job_id",
                (job_id, attempt, lease_token, lease_token),
            ).fetchone()
        )

    def update_polish_job(
        self,
        job_id,
        attempt,
        *,
        stage=None,
        batches_done=None,
        batches_total=None,
        metrics=None,
        lease_token=None,
    ) -> bool:
        assignments, values = ["lease_until=now()+interval '15 minutes'"], []
        if stage is not None:
            assignments.append("stage=%s")
            values.append(stage)
        if batches_done is not None:
            assignments.append("batches_done=%s")
            values.append(batches_done)
        if batches_total is not None:
            assignments.append("batches_total=%s")
            values.append(batches_total)
        if metrics:
            assignments.append("metrics=metrics || %s")
            values.append(Jsonb(metrics))
        values.extend([job_id, attempt])
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE material_note_polish_jobs SET "
                + ",".join(assignments)
                + " WHERE job_id=%s AND status='running' AND attempts=%s AND lease_until>now() "
                "AND (%s::text IS NULL OR lease_token=%s) RETURNING job_id",
                (*values, lease_token, lease_token),
            )
            return cursor.fetchone() is not None

    def polish_job_context(self, job):
        """Load document and snapshot pages under the job lease."""
        from .material_text_source import source_units

        with self.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,attempts,lease_token,lease_until>now() AS lease_valid "
                "FROM material_note_polish_jobs WHERE job_id=%s AND user_id=%s FOR UPDATE",
                (job["job_id"], self._user()),
            )
            row = cursor.fetchone()
            if not self._valid_polish_lease(row, job):
                return None
            cursor.execute(
                "SELECT data FROM documents WHERE document_id=%s AND user_id=%s FOR UPDATE",
                (job["document_id"], self._user()),
            )
            document = cursor.fetchone()
            if not document or document["data"].get("parse_status") == "deleted":
                return None
            document = document["data"]
            head = self.note_head(job["course_id"], job["document_id"], lock=True)
            if (
                document.get("content_sha256") != job["source_hash"]
                or not head
                or head["polish_generation"] != job["generation"]
                or head["initial_revision_id"] != job["snapshot_revision_id"]
            ):
                return None
            pages = self.material_pages(document)
            units = source_units(pages, document["content_sha256"])
            return dict(document=document, pages=pages, units=units)

    @staticmethod
    def _valid_polish_lease(row, job):
        return bool(
            row
            and row["status"] == "running"
            and row["attempts"] == job["attempts"]
            and row.get("lease_valid")
            and row.get("lease_token") == job.get("lease_token")
        )

    def prepare_polish_batches(self, job, batches):
        """Materialize reviewed cache entries into this generation before reindexing."""
        with self.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,attempts,lease_token,lease_until>now() AS lease_valid "
                "FROM material_note_polish_jobs WHERE job_id=%s AND user_id=%s FOR UPDATE",
                (job["job_id"], self._user()),
            )
            if not self._valid_polish_lease(cursor.fetchone(), job):
                return None
            cursor.execute(
                "SELECT cache_key,result,review FROM material_note_polish_batches "
                "WHERE user_id=%s AND cache_key=ANY(%s) AND status='done' "
                "ORDER BY updated_at DESC",
                (self._user(), [batch["cache_key"] for batch in batches]),
            )
            cache = {}
            for row in cursor.fetchall():
                if (
                    row["review"]
                    and row["review"].get("supported")
                    and not row["review"].get("issues")
                ):
                    cache.setdefault(row["cache_key"], row)
            rows = []
            for index, batch in enumerate(batches):
                cached = cache.get(batch["cache_key"])
                result, review = (cached["result"], cached["review"]) if cached else (None, None)
                status = "done" if cached else "pending"
                block_ids = [unit["id"] for unit in batch["units"]]
                cursor.execute(
                    "INSERT INTO material_note_polish_batches(user_id,job_id,batch_index,"
                    "cache_key,block_ids,status,result,review) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (job_id,batch_index) DO UPDATE SET cache_key=EXCLUDED.cache_key,"
                    "block_ids=EXCLUDED.block_ids,status=EXCLUDED.status,result=EXCLUDED.result,"
                    "review=EXCLUDED.review,error=NULL,updated_at=now()",
                    (
                        self._user(),
                        job["job_id"],
                        index,
                        batch["cache_key"],
                        Jsonb(block_ids),
                        status,
                        Jsonb(result) if result else None,
                        Jsonb(review) if review else None,
                    ),
                )
                rows.append(
                    dict(
                        batch_index=index,
                        cache_key=batch["cache_key"],
                        block_ids=block_ids,
                        status=status,
                        result=result,
                        review=review,
                    )
                )
            cursor.execute(
                "UPDATE material_note_polish_batches SET status='superseded' "
                "WHERE job_id=%s AND batch_index>=%s",
                (job["job_id"], len(batches)),
            )
            cursor.execute(
                "UPDATE material_note_polish_jobs SET batches_total=%s,stage='batches',"
                "batches_done=%s WHERE job_id=%s",
                (len(batches), sum(row["status"] == "done" for row in rows), job["job_id"]),
            )
            return rows

    def checkpoint_polish_batch(self, job, batch_index, *, result=None, review=None, error=None):
        with self.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,attempts,lease_token,lease_until>now() AS lease_valid "
                "FROM material_note_polish_jobs WHERE job_id=%s "
                "AND user_id=%s FOR UPDATE",
                (job["job_id"], self._user()),
            )
            row = cursor.fetchone()
            if not self._valid_polish_lease(row, job):
                return False
            status = "done" if result is not None else "failed"
            cursor.execute(
                "UPDATE material_note_polish_batches SET status=%s,"
                "result=COALESCE(%s,result),review=COALESCE(%s,review),error=%s,"
                "attempts=attempts+1,updated_at=now() WHERE job_id=%s AND batch_index=%s",
                (
                    status,
                    Jsonb(result) if result is not None else None,
                    Jsonb(review) if review is not None else None,
                    error,
                    job["job_id"],
                    batch_index,
                ),
            )
            if cursor.rowcount != 1:
                return False
            if status == "done":
                cursor.execute(
                    "UPDATE material_note_polish_jobs SET "
                    "batches_done=(SELECT count(*) FROM material_note_polish_batches "
                    "WHERE job_id=%s AND status='done') WHERE job_id=%s",
                    (job["job_id"], job["job_id"]),
                )
            return True

    def load_polish_batches(self, job):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT batch_index,cache_key,block_ids,status,result,review,attempts,error "
                "FROM material_note_polish_batches WHERE job_id=%s ORDER BY batch_index",
                (job["job_id"],),
            )
            return [dict(row) for row in cursor.fetchall()]

    def publish_polished_note(self, job, body, metrics):
        """Atomically publish the polished note as the latest revision or a candidate."""
        with self.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,attempts,lease_token,lease_until>now() AS lease_valid "
                "FROM material_note_polish_jobs WHERE job_id=%s AND user_id=%s FOR UPDATE",
                (job["job_id"], self._user()),
            )
            row = cursor.fetchone()
            if not self._valid_polish_lease(row, job):
                return False
            cursor.execute(
                "SELECT data FROM documents WHERE document_id=%s AND user_id=%s FOR UPDATE",
                (job["document_id"], self._user()),
            )
            document = cursor.fetchone()
            if not document or document["data"].get("parse_status") == "deleted":
                return False
            document = document["data"]
            head = self.note_head(job["course_id"], job["document_id"], lock=True)
            if (
                not head
                or document.get("content_sha256") != job["source_hash"]
                or head["initial_revision_id"] != job["snapshot_revision_id"]
                or head["polish_generation"] != job["generation"]
            ):
                return False
            revision_id = uuid4().hex
            sequence = cursor.execute(
                "SELECT COALESCE(max(revision_no),0)+1 AS n FROM material_note_revisions "
                "WHERE user_id=%s AND course_id=%s AND document_id=%s",
                (self._user(), job["course_id"], job["document_id"]),
            ).fetchone()["n"]
            self._insert_note_revision(
                document,
                revision_id,
                sequence,
                head["latest_saved_revision_id"],
                body,
            )
            untouched = (
                head["save_sequence"] == 1
                and head["latest_saved_revision_id"] == head["initial_revision_id"]
            )
            if untouched:
                cursor.execute(
                    "UPDATE material_note_heads SET latest_saved_revision_id=%s,"
                    "save_sequence=%s,polish_status='ready',polish_job_id=%s,"
                    "updated_at=now() WHERE user_id=%s AND course_id=%s AND document_id=%s",
                    (
                        revision_id,
                        sequence,
                        job["job_id"],
                        self._user(),
                        job["course_id"],
                        job["document_id"],
                    ),
                )
                document_polish = "ready"
            else:
                cursor.execute(
                    "UPDATE material_note_heads SET candidate_revision_id=%s,"
                    "polish_status='candidate',polish_job_id=%s,"
                    "save_sequence=%s,updated_at=now() "
                    "WHERE user_id=%s AND course_id=%s AND document_id=%s",
                    (
                        revision_id,
                        job["job_id"],
                        sequence,
                        self._user(),
                        job["course_id"],
                        job["document_id"],
                    ),
                )
                document_polish = "candidate"
            cursor.execute(
                "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s",
                (
                    Jsonb(dict(polish_status=document_polish)),
                    self._user(),
                    job["document_id"],
                ),
            )
            cursor.execute(
                "UPDATE material_note_polish_jobs SET status='succeeded',stage='published',"
                "lease_until=NULL,finished_at=now(),metrics=metrics || %s WHERE job_id=%s",
                (Jsonb(metrics), job["job_id"]),
            )
            return dict(revision_id=revision_id, published_as=document_polish)

    def finish_polish_not_required(self, job, metrics):
        """No native text at all: finish without any model call."""
        with self.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,attempts,lease_token,lease_until>now() AS lease_valid "
                "FROM material_note_polish_jobs WHERE job_id=%s "
                "AND user_id=%s FOR UPDATE",
                (job["job_id"], self._user()),
            )
            row = cursor.fetchone()
            if not self._valid_polish_lease(row, job):
                return False
            cursor.execute(
                "UPDATE material_note_heads SET polish_status='not_required',updated_at=now() "
                "WHERE user_id=%s AND course_id=%s AND document_id=%s AND polish_generation=%s "
                "AND polish_status IN ('queued','running')",
                (self._user(), job["course_id"], job["document_id"], job["generation"]),
            )
            cursor.execute(
                "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s "
                "AND data->>'parse_status'<>'deleted'",
                (Jsonb(dict(polish_status="not_required")), self._user(), job["document_id"]),
            )
            cursor.execute(
                "UPDATE material_note_polish_jobs SET status='succeeded',stage='not_required',"
                "lease_until=NULL,finished_at=now(),metrics=metrics || %s WHERE job_id=%s",
                (Jsonb(metrics), job["job_id"]),
            )
            return True

    def fail_polish_job(self, job, *, code, message, retry):
        with self.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,attempts,lease_token,lease_until>now() AS lease_valid "
                "FROM material_note_polish_jobs WHERE job_id=%s "
                "AND user_id=%s FOR UPDATE",
                (job["job_id"], self._user()),
            )
            row = cursor.fetchone()
            if not self._valid_polish_lease(row, job):
                return False
            status = "queued" if retry else "failed"
            cursor.execute(
                "UPDATE material_note_polish_jobs SET status=%s,error_code=%s,error_message=%s,"
                "lease_until=NULL,available_at=CASE WHEN %s THEN now()+interval '5 seconds' "
                "ELSE available_at END,finished_at=CASE WHEN %s THEN NULL ELSE now() END "
                "WHERE job_id=%s",
                (status, code, message, retry, retry, job["job_id"]),
            )
            cursor.execute(
                "UPDATE material_note_heads SET polish_status=%s,updated_at=now() "
                "WHERE user_id=%s AND course_id=%s AND document_id=%s AND polish_generation=%s "
                "AND polish_status IN ('queued','running')",
                (
                    "queued" if retry else "failed",
                    self._user(),
                    job["course_id"],
                    job["document_id"],
                    job["generation"],
                ),
            )
            cursor.execute(
                "UPDATE documents SET data=data || jsonb_build_object('polish_status',%s::text) "
                "WHERE record_key=%s AND user_id=%s AND data->>'parse_status'<>'deleted'",
                ("queued" if retry else "failed", job["document_id"], self._user()),
            )
            return True

    def retry_polish(self, document):
        """Requeue the latest failed polish job for this document."""
        with self.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM material_note_polish_jobs WHERE user_id=%s AND document_id=%s "
                "ORDER BY generation DESC LIMIT 1 FOR UPDATE",
                (self._user(), document["document_id"]),
            )
            row = cursor.fetchone()
            if not row:
                return dict(status="not_required")
            head = self.note_head(document["course_id"], document["document_id"], lock=True)
            if not head:
                return dict(status="not_required")
            if head["polish_status"] == "not_required":
                raise ValueError("此资料没有可提取的原生文字，无法生成润色笔记")
            if row["status"] != "failed":
                return dict(status=row["status"], job_id=row["job_id"])
            cursor.execute(
                "UPDATE material_note_polish_jobs SET status='queued',phase_attempts=0,"
                "lease_token=NULL,available_at=now(),finished_at=NULL,error_code=NULL,"
                "error_message=NULL WHERE job_id=%s",
                (row["job_id"],),
            )
            cursor.execute(
                "UPDATE material_note_heads SET polish_status='queued',updated_at=now() "
                "WHERE user_id=%s AND course_id=%s AND document_id=%s",
                (self._user(), document["course_id"], document["document_id"]),
            )
            cursor.execute(
                "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s",
                (Jsonb(dict(polish_status="queued")), self._user(), document["document_id"]),
            )
            return dict(status="queued", job_id=row["job_id"])

    def regenerate_polish(self, document, *, model_config, prompt_version):
        """Enqueue a fresh polish generation for an existing note (reuses parsed text)."""
        from .material_text_source import classify_blocks, source_units, units_input_hash

        with self.transaction(), self.connection.cursor() as cursor:
            current = cursor.execute(
                "SELECT data FROM documents WHERE user_id=%s AND document_id=%s FOR UPDATE",
                (self._user(), document["document_id"]),
            ).fetchone()
            if not current or current["data"].get("parse_status") == "deleted":
                raise ValueError("资料已删除")
            document = current["data"]
            head = self.note_head(document["course_id"], document["document_id"], lock=True)
            if not head:
                raise ValueError("资料笔记尚未就绪")
            pages = self.material_pages(document)
            if pages and any("searchable" not in b for p in pages for b in p["blocks"]):
                pages = classify_blocks(pages)
            units = source_units(pages, document["content_sha256"])
            input_hash = units_input_hash(units, model_config, prompt_version)
            generation = head["polish_generation"] + 1
            job_id = uuid4().hex
            cursor.execute(
                "INSERT INTO material_note_polish_jobs(job_id,user_id,course_id,document_id,"
                "source_hash,snapshot_revision_id,generation,purpose,model_config,input_hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'regenerate',%s,%s)",
                (
                    job_id,
                    self._user(),
                    document["course_id"],
                    document["document_id"],
                    document["content_sha256"],
                    head["initial_revision_id"],
                    generation,
                    Jsonb(model_config),
                    input_hash,
                ),
            )
            cursor.execute(
                "UPDATE material_note_heads SET polish_status='queued',polish_job_id=%s,"
                "polish_generation=%s,candidate_revision_id=NULL,updated_at=now() "
                "WHERE user_id=%s AND course_id=%s AND document_id=%s",
                (job_id, generation, self._user(), document["course_id"], document["document_id"]),
            )
            cursor.execute(
                "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s",
                (Jsonb(dict(polish_status="queued")), self._user(), document["document_id"]),
            )
            return dict(status="queued", job_id=job_id, generation=generation)

    def apply_polish_candidate(
        self, course_id, document_id, *, base_revision_id, candidate_revision_id
    ):
        """Promote the stored candidate revision to the current reading note."""
        with self.transaction(), self.connection.cursor() as cursor:
            head = self.note_head(course_id, document_id, lock=True)
            if not head or not head["candidate_revision_id"]:
                raise ValueError("当前没有可应用的润色版本")
            if (
                head["latest_saved_revision_id"] != base_revision_id
                or head["candidate_revision_id"] != candidate_revision_id
            ):
                raise NoteConflict(
                    "笔记或候选已更新，请重新查看后应用", head["latest_saved_revision_id"]
                )
            candidate = self.note_revision(course_id, document_id, head["candidate_revision_id"])
            if not candidate:
                raise ValueError("润色版本不存在")
            cursor.execute(
                "UPDATE material_note_heads SET latest_saved_revision_id=%s,"
                "save_sequence=GREATEST(save_sequence,%s),polish_status='applied',"
                "candidate_revision_id=NULL,updated_at=now() WHERE user_id=%s AND course_id=%s "
                "AND document_id=%s",
                (
                    candidate["revision_id"],
                    candidate["revision_no"],
                    self._user(),
                    course_id,
                    document_id,
                ),
            )
            cursor.execute(
                "UPDATE documents SET data=data || %s WHERE user_id=%s AND document_id=%s",
                (Jsonb(dict(polish_status="applied")), self._user(), document_id),
            )
            return dict(revision_id=candidate["revision_id"], revision_no=candidate["revision_no"])
