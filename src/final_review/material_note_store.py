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
            prior = self.note_revision(course_id, document_id, base_revision_id)
            normalized = validate_body(body, prior["body"])
            changed_text = input_hash(normalized) != prior["input_hash"]
            revision_id = base_revision_id
            if normalized != prior["body"]:
                revision_id = uuid4().hex
                self._insert_note_revision(
                    document, revision_id, head["save_sequence"] + 1, base_revision_id, normalized
                )
                self.connection.execute(
                    "UPDATE material_note_heads SET latest_saved_revision_id=%s,"
                    "save_sequence=save_sequence+1,text_generation=text_generation+%s,"
                    "updated_at=now() WHERE user_id=%s AND course_id=%s AND document_id=%s",
                    (revision_id, int(changed_text), self._user(), course_id, document_id),
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

    def _insert_child_material_job(self, document, phase, revision_id=None, body=None, pages=None):
        source_hash = document["content_sha256"]
        internal_key = digest([source_hash, STRATEGY, phase, revision_id, pages])
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
                STRATEGY,
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
