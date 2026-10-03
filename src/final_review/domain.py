"""M0 domain contracts: lifecycle, immutable revisions and destructive-action guards."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from .source_locators import material_version
from .storage import Store, stable_key


class DomainNotFound(KeyError):
    pass


class DomainConflict(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class DomainService:
    """Business rules independent from FastAPI and usable with the test Store seam."""

    def __init__(self, store: Store, user_id: str):
        self.store = store
        self.user_id = user_id

    def _owned(self, table: str, key: str) -> dict:
        item = self.store.get(table, key)
        if item is None or item.get("user_id") != self.user_id:
            raise DomainNotFound(key)
        return item

    def course(self, course_id: str, *, writable: bool = False) -> dict:
        course = self._owned("course", course_id)
        if writable and course.get("status", "active") in {"deleted", "purged"}:
            raise DomainConflict("课程已删除，不能继续修改")
        return course

    def _put(self, table: str, key: str, item: dict) -> dict:
        item["updated_at"] = _now()
        self.store.put(table, key, item)
        return item

    def _transaction(self):
        transaction = getattr(self.store, "transaction", None)
        return transaction() if transaction else nullcontext()

    def _audit(self, course_id: str, action: str, resource_type: str, resource_id: str) -> None:
        now = _now()
        self.store.put(
            "audit_event",
            _id("audit"),
            {
                "user_id": self.user_id,
                "course_id": course_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "created_at": now,
            },
        )

    def update_course(self, course_id: str, changes: dict, expected_updated_at: str) -> dict:
        course = self.course(course_id, writable=True)
        if course.get("updated_at") != expected_updated_at:
            raise DomainConflict("课程已被其他操作更新")
        course.update({key: value for key, value in changes.items() if value is not None})
        return self._put("course", course_id, course)

    def archive_course(self, course_id: str) -> dict:
        course = self.course(course_id, writable=True)
        if course.get("status", "active") != "active":
            raise DomainConflict("仅可归档活动课程")
        course.update({"status": "archived", "archived_at": _now()})
        self._audit(course_id, "course.archived", "course", course_id)
        return self._put("course", course_id, course)

    def restore_course(self, course_id: str) -> dict:
        course = self._owned("course", course_id)
        status = course.get("status", "active")
        if status == "archived":
            course.update({"status": "active", "archived_at": None})
        elif status == "deleted":
            if datetime.fromisoformat(course["purge_after"]) <= datetime.now(UTC):
                raise DomainConflict("课程回收期已结束")
            course.update(
                {
                    "status": course.get("status_before_delete", "active"),
                    "deleted_at": None,
                    "purge_after": None,
                }
            )
        else:
            raise DomainConflict("当前课程不能恢复")
        self._audit(course_id, "course.restored", "course", course_id)
        return self._put("course", course_id, course)

    def _impact(self, course_id: str) -> dict[str, int]:
        return {
            "materials": len(self.store.scan("document", {"course_id": course_id})),
            "exams": len(self.store.scan("exam", {"course_id": course_id})),
            "learning_assets": len(self.store.scan("learning_asset", {"course_id": course_id})),
            "attempts": len(self.store.scan("attempt", {"course_id": course_id})),
        }

    def _confirmation(
        self, course_id: str, action: str, resource_type: str, resource_id: str, payload: dict
    ) -> dict:
        now = datetime.now(UTC)
        payload_hash = sha256(repr(sorted(payload.items())).encode()).hexdigest()
        confirmation = {
            "confirmation_id": _id("confirm"),
            "user_id": self.user_id,
            "course_id": course_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "payload_hash": payload_hash,
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "created_at": now.isoformat(),
            "consumed_at": None,
        }
        self.store.put("confirmation", confirmation["confirmation_id"], confirmation)
        return confirmation

    def deletion_preview(self, course_id: str) -> dict:
        self.course(course_id, writable=True)
        impact = self._impact(course_id)
        confirmation = self._confirmation(course_id, "course.delete", "course", course_id, impact)
        return {
            "impact": impact,
            "confirmation_id": confirmation["confirmation_id"],
            "expires_at": confirmation["expires_at"],
        }

    def _consume(
        self,
        confirmation_id: str,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        payload: dict,
    ) -> None:
        locked_get = getattr(self.store, "get_for_update", None)
        confirmation = (
            locked_get("confirmation", confirmation_id)
            if locked_get
            else self._owned("confirmation", confirmation_id)
        )
        if confirmation is None or confirmation.get("user_id") != self.user_id:
            raise DomainNotFound(confirmation_id)
        if confirmation.get("consumed_at") or confirmation.get("action") != action:
            raise DomainConflict("确认令牌无效或已使用")
        if (
            confirmation.get("resource_type") != resource_type
            or confirmation.get("resource_id") != resource_id
        ):
            raise DomainConflict("确认令牌不属于此资源")
        if datetime.fromisoformat(confirmation["expires_at"]) <= datetime.now(UTC):
            raise DomainConflict("确认令牌已过期")
        actual = sha256(repr(sorted(payload.items())).encode()).hexdigest()
        if actual != confirmation.get("payload_hash"):
            raise DomainConflict("影响范围已变化，请重新确认")
        confirmation["consumed_at"] = _now()
        self._put("confirmation", confirmation_id, confirmation)

    def delete_course(self, course_id: str, confirmation_id: str) -> dict:
        with self._transaction():
            course = self.course(course_id, writable=True)
            impact = self._impact(course_id)
            self._consume(
                confirmation_id,
                action="course.delete",
                resource_type="course",
                resource_id=course_id,
                payload=impact,
            )
            now = datetime.now(UTC)
            course.update(
                {
                    "status_before_delete": course.get("status", "active"),
                    "status": "deleted",
                    "deleted_at": now.isoformat(),
                    "purge_after": (now + timedelta(days=30)).isoformat(),
                    "deletion_confirmed_at": now.isoformat(),
                }
            )
            self._audit(course_id, "course.deleted", "course", course_id)
            return self._put("course", course_id, course)

    def create_exam(self, course_id: str, payload: dict) -> dict:
        self.course(course_id, writable=True)
        now = _now()
        exam = {
            "exam_id": _id("exam"),
            "course_id": course_id,
            "user_id": self.user_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            **payload,
        }
        self.store.put("exam", exam["exam_id"], exam)
        return exam

    def list_exams(self, course_id: str) -> list[dict]:
        self.course(course_id)
        return [
            row
            for row in self.store.scan("exam", {"course_id": course_id})
            if row.get("status") != "deleted"
        ]

    def exam(self, exam_id: str) -> dict:
        exam = self._owned("exam", exam_id)
        self.course(exam["course_id"])
        return exam

    def update_exam(self, exam_id: str, payload: dict, expected_updated_at: str) -> dict:
        exam = self.exam(exam_id)
        if exam.get("status") != "active" or exam.get("updated_at") != expected_updated_at:
            raise DomainConflict("考试已变更或不可编辑")
        exam.update(payload)
        return self._put("exam", exam_id, exam)

    def archive_exam(self, exam_id: str) -> dict:
        exam = self.exam(exam_id)
        if exam.get("status") != "active":
            raise DomainConflict("仅可归档活动考试")
        exam["status"] = "archived"
        self._audit(exam["course_id"], "exam.archived", "exam", exam_id)
        return self._put("exam", exam_id, exam)

    def _material_version(self, document: dict) -> dict:
        version = material_version({**document, "user_id": self.user_id})
        key = version["material_version_id"]
        existing = self.store.get("material_version", key)
        if existing:
            return existing
        version["created_at"] = version.get("created_at") or _now()
        self.store.put("material_version", key, version)
        return version

    def create_asset(self, course_id: str, payload: dict) -> dict:
        self.course(course_id, writable=True)
        now = _now()
        asset_id, revision_id = _id("asset"), _id("revision")
        asset = {
            "asset_id": asset_id,
            "course_id": course_id,
            "user_id": self.user_id,
            "asset_type": payload["asset_type"],
            "title": payload["title"],
            "status": "draft",
            "current_revision_id": None,
            "created_at": now,
            "updated_at": now,
        }
        revision = {
            "revision_id": revision_id,
            "asset_id": asset_id,
            "course_id": course_id,
            "user_id": self.user_id,
            "revision_no": 1,
            "state": "draft",
            "title": payload["title"],
            "markdown": payload["markdown"],
            "source_document_ids": payload["source_document_ids"],
            "based_on_revision_id": None,
            "created_at": now,
            "updated_at": now,
        }
        self.store.put("learning_asset", asset_id, asset)
        self.store.put("asset_revision", revision_id, revision)
        self._references(revision, payload["source_document_ids"])
        return {"asset": asset, "revision": revision}

    def create_note_draft(self, course_id: str, payload: dict, run_id: str) -> dict:
        """Persist one validated generated note and its point-level references."""
        self.course(course_id, writable=True)
        asset_id = "asset-" + stable_key(self.user_id, course_id, run_id)[:32]
        revision_id = "revision-" + stable_key(asset_id, "1")[:32]
        with self._transaction():
            existing = self.store.get("learning_asset", asset_id)
            if existing:
                return {"asset": existing, "revision": self._owned("asset_revision", revision_id)}
            now = _now()
            asset = {
                "asset_id": asset_id,
                "course_id": course_id,
                "user_id": self.user_id,
                "asset_type": "note",
                "title": payload["title"],
                "status": "draft",
                "current_revision_id": None,
                "created_at": now,
                "updated_at": now,
            }
            revision = {
                "revision_id": revision_id,
                "asset_id": asset_id,
                "course_id": course_id,
                "user_id": self.user_id,
                "revision_no": 1,
                "state": "draft",
                "title": payload["title"],
                "markdown": payload["markdown"],
                "note_type": payload["note_type"],
                "points": payload["points"],
                "coverage": payload.get("coverage"),
                "source_document_ids": sorted(
                    {
                        ref["document_id"]
                        for point in payload["points"]
                        for ref in point["references"]
                    }
                ),
                "based_on_revision_id": None,
                "created_at": now,
                "updated_at": now,
            }
            self.store.put("learning_asset", asset_id, asset)
            self.store.put("asset_revision", revision_id, revision)
            locked_get = getattr(self.store, "get_for_update", None)
            for point in payload["points"]:
                for ref in point["references"]:
                    document = (
                        locked_get("document", ref["document_id"])
                        if locked_get
                        else self.store.get("document", ref["document_id"])
                    )
                    if (
                        document is None
                        or document.get("user_id") != self.user_id
                        or document.get("course_id") != course_id
                        or document.get("parse_status") != "ready"
                    ):
                        raise DomainConflict("来源资料已不可用")
                    version = self.store.ensure_material_source(document)
                    chunks = {
                        row["chunk_id"]: row
                        for row in self.store.list_material_chunks(ref["document_id"])
                    }
                    if (
                        ref["chunk_id"] not in chunks
                        or ref["quote"] not in chunks[ref["chunk_id"]]["content"]
                    ):
                        raise DomainConflict("来源片段已变化")
                    reference = {
                        "source_reference_id": _id("source"),
                        "course_id": course_id,
                        "user_id": self.user_id,
                        "revision_id": revision_id,
                        "asset_revision_id": revision_id,
                        "document_id": ref["document_id"],
                        "material_version_id": version["material_version_id"],
                        "locator_id": ref["chunk_id"],
                        "point_id": point["point_id"],
                        "quote": ref["quote"],
                        "created_at": now,
                    }
                    self.store.put("source_reference", reference["source_reference_id"], reference)
            return {"asset": asset, "revision": revision}

    def note_draft(self, asset_id: str, revision_id: str) -> dict:
        asset = self._owned("learning_asset", asset_id)
        revision = self._owned("asset_revision", revision_id)
        if (
            asset.get("asset_type") != "note"
            or asset.get("status") != "draft"
            or revision.get("asset_id") != asset_id
            or revision.get("state") != "draft"
        ):
            raise DomainNotFound(revision_id)
        references = [
            row
            for row in self.store.scan("source_reference", {"course_id": asset["course_id"]})
            if row.get("revision_id") == revision_id
        ]
        return {"asset": asset, "revision": revision, "references": references}

    def _references(self, revision: dict, document_ids: list[str]) -> None:
        with self._transaction():
            locked_get = getattr(self.store, "get_for_update", None)
            for document_id in sorted(document_ids):
                document = (
                    locked_get("document", document_id)
                    if locked_get
                    else self._owned("document", document_id)
                )
                if (
                    document is None
                    or document.get("user_id") != self.user_id
                    or document.get("course_id") != revision["course_id"]
                    or document.get("parse_status") != "ready"
                ):
                    raise DomainConflict("来源资料不可用于该资产")
                version = self._material_version(document)
                reference = {
                    "source_reference_id": _id("source"),
                    "course_id": revision["course_id"],
                    "user_id": self.user_id,
                    "revision_id": revision["revision_id"],
                    "asset_revision_id": revision["revision_id"],
                    "document_id": document_id,
                    "material_version_id": version["material_version_id"],
                    "locator_id": "document",
                    "created_at": _now(),
                }
                self.store.put("source_reference", reference["source_reference_id"], reference)

    def create_revision(self, asset_id: str, payload: dict) -> dict:
        asset = self._owned("learning_asset", asset_id)
        self.course(asset["course_id"], writable=True)
        base = self._owned("asset_revision", payload["base_revision_id"])
        if base.get("asset_id") != asset_id:
            raise DomainConflict("不能跨资产编辑版本")
        revisions = self.store.scan("asset_revision", {"course_id": asset["course_id"]})
        number = (
            max(
                (row["revision_no"] for row in revisions if row.get("asset_id") == asset_id),
                default=0,
            )
            + 1
        )
        revision = {
            "revision_id": _id("revision"),
            "asset_id": asset_id,
            "course_id": asset["course_id"],
            "user_id": self.user_id,
            "revision_no": number,
            "state": "draft",
            "title": payload["title"],
            "markdown": payload["markdown"],
            "source_document_ids": payload["source_document_ids"],
            "based_on_revision_id": base["revision_id"],
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.store.put("asset_revision", revision["revision_id"], revision)
        self._references(revision, payload["source_document_ids"])
        return revision

    def confirm_revision(self, asset_id: str, revision_id: str) -> dict:
        with self._transaction():
            asset, revision = (
                self._owned("learning_asset", asset_id),
                self._owned("asset_revision", revision_id),
            )
            if revision.get("asset_id") != asset_id or revision.get("state") != "draft":
                raise DomainConflict("只能确认该资产的草稿版本")
            references = [
                row
                for row in self.store.scan("source_reference", {"course_id": asset["course_id"]})
                if row.get("revision_id") == revision_id
            ]
            locked_get = getattr(self.store, "get_for_update", None)
            for document_id in sorted({row["document_id"] for row in references}):
                document = (
                    locked_get("document", document_id)
                    if locked_get
                    else self._owned("document", document_id)
                )
                if (
                    document is None
                    or document.get("user_id") != self.user_id
                    or document.get("parse_status") != "ready"
                ):
                    raise DomainConflict("来源资料已不可用，不能确认该版本")
            previous = asset.get("current_revision_id")
            revision.update({"state": "confirmed", "confirmed_at": _now()})
            self._put("asset_revision", revision_id, revision)
            asset.update({"status": "confirmed", "current_revision_id": revision_id})
            saved_asset = self._put("learning_asset", asset_id, asset)
            if previous:
                old = self._owned("asset_revision", previous)
                old["state"] = "superseded"
                self._put("asset_revision", previous, old)
            self._audit(asset["course_id"], "asset.confirmed", "learning_asset", asset_id)
            return {"asset": saved_asset, "revision": revision}

    def archive_asset(self, asset_id: str) -> dict:
        asset = self._owned("learning_asset", asset_id)
        if asset.get("status") != "confirmed":
            raise DomainConflict("仅可归档已确认资产")
        asset["status"] = "archived"
        self._audit(asset["course_id"], "asset.archived", "learning_asset", asset_id)
        return self._put("learning_asset", asset_id, asset)

    def purge_expired_courses(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        purged = []
        for course in self.store.scan("course", {}):
            if (
                course.get("status") != "deleted"
                or datetime.fromisoformat(course["purge_after"]) > now
            ):
                continue
            course["status"] = "purged"
            self._put("course", course["course_id"], course)
            self._audit(course["course_id"], "course.purged", "course", course["course_id"])
            purged.append(course["course_id"])
        return purged

    def update_material(self, document_id: str, course_id: str, changes: dict) -> dict:
        self.course(course_id, writable=True)
        document = self._owned("document", document_id)
        if document.get("course_id") != course_id:
            raise DomainNotFound(document_id)
        if document.get("parse_status") not in {"ready", "failed"}:
            raise DomainConflict("资料处理中或已删除，暂不能编辑")
        updated = self.store.update_material_metadata(document_id, changes)
        if updated is None:
            raise DomainConflict("资料状态或内容已变化，请刷新后重试")
        self._audit(course_id, "material.updated", "material", document_id)
        return updated

    def material_deletion_preview(self, document_id: str, course_id: str | None = None) -> dict:
        document = self._owned("document", document_id)
        if course_id is not None and document.get("course_id") != course_id:
            raise DomainNotFound(document_id)
        self.course(document["course_id"], writable=True)
        if document.get("parse_status") == "deleted":
            raise DomainConflict("资料已删除")
        blocking = self._blocking_references(document)
        payload = self._material_delete_impact(blocking)
        confirmation = self._confirmation(
            document["course_id"], "material.delete", "material", document_id, payload
        )
        return {
            "blocking_references": len(blocking),
            "affected_assets": len({row["asset_id"] for row in self._reference_assets(blocking)}),
            "retain_source_snapshot_allowed": bool(blocking),
            "confirmation_id": confirmation["confirmation_id"],
            "expires_at": confirmation["expires_at"],
        }

    def _reference_assets(self, references: list[dict]) -> list[dict]:
        return [self._owned("asset_revision", row["revision_id"]) for row in references]

    @staticmethod
    def _material_delete_impact(references: list[dict]) -> dict:
        return {"reference_ids": sorted(row["source_reference_id"] for row in references)}

    def _blocking_references(self, document: dict) -> list[dict]:
        blocking = []
        for reference in self.store.scan("source_reference", {"course_id": document["course_id"]}):
            if reference.get("document_id") != document["document_id"]:
                continue
            revision = self._owned("asset_revision", reference["revision_id"])
            if revision.get("state") in {"confirmed", "superseded", "archived"}:
                blocking.append(reference)
        return blocking

    def delete_material(self, document_id: str, confirmation_id: str, mode: str) -> dict:
        with self._transaction():
            locked_get = getattr(self.store, "get_for_update", None)
            document = (
                locked_get("document", document_id)
                if locked_get
                else self._owned("document", document_id)
            )
            if document is None or document.get("user_id") != self.user_id:
                raise DomainNotFound(document_id)
            self.course(document["course_id"], writable=True)
            if document.get("parse_status") == "deleted":
                raise DomainConflict("资料已删除")
            blocking = self._blocking_references(document)
            payload = self._material_delete_impact(blocking)
            self._consume(
                confirmation_id,
                action="material.delete",
                resource_type="material",
                resource_id=document_id,
                payload=payload,
            )
            if blocking and mode != "retain_source_snapshot":
                raise DomainConflict("资料仍被正式资产引用")
            if blocking:
                for reference in blocking:
                    version = self._owned("material_version", reference["material_version_id"])
                    snapshot = {
                        "snapshot_id": _id("snapshot"),
                        "course_id": document["course_id"],
                        "user_id": self.user_id,
                        "document_id": document_id,
                        "revision_id": reference["revision_id"],
                        "material_version_id": reference["material_version_id"],
                        "locator_id": reference.get("locator_id", "document"),
                        "display_file_name": version.get(
                            "file_name", document.get("file_name", document["title"])
                        ),
                        "source_type": version.get("source_type", document["source_type"]),
                        "source_origin": version.get(
                            "source_origin", document.get("source_origin", "legacy_upload")
                        ),
                        "excerpt": document.get("cleaned_markdown", "")[:1500],
                        "created_at": _now(),
                    }
                    self.store.put("source_snapshot", snapshot["snapshot_id"], snapshot)
            self.store.deindex_document(document_id)
            document.update({"parse_status": "deleted", "deleted_at": _now()})
            self._put("document", document_id, document)
            self._audit(document["course_id"], "material.deleted", "material", document_id)
            return {
                "deleted": True,
                "retained_source_snapshot": bool(blocking),
            }
