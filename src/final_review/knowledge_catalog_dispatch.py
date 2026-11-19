"""User-bound execution boundary and checkpoint adapter for existing workers."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from .knowledge import digest
from .knowledge_catalog_incremental import (
    RULES_VERSION,
    IncrementalCatalogWorker,
    IncrementalConfig,
    full_points,
)
from .knowledge_catalog_incremental_contracts import IncrementalCheckpoint, SemanticBaseline
from .knowledge_catalog_scheduler import ENVELOPE, CatalogScheduler
from .knowledge_catalog_store import (
    CatalogConflict,
    CatalogInvalid,
    CatalogLeaseLost,
    CatalogRepository,
    CatalogSnapshotChanged,
)
from .knowledge_catalog_worker import (
    CatalogBudgetExceeded,
    InitialCatalogWorker,
    WorkerConfig,
    _retryable,
)

DB_TRANSIENT = {
    "OperationalError",
    "InterfaceError",
    "SerializationFailure",
    "DeadlockDetected",
    "LockNotAvailable",
    "QueryCanceled",
    "AdminShutdown",
    "CrashShutdown",
    "CannotConnectNow",
}
MODEL_TRANSIENT = {
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "ReadError",
    "RemoteProtocolError",
}


def semantic_hashes(snapshot):
    return {
        p["point_id"]: digest({k: v for k, v in p.items() if k != "point_id"})
        for p in full_points(snapshot)
    }


def scope_allowed(store, course, *, lock=False):
    return (
        store.connection.execute(
            "SELECT 1 FROM courses c JOIN app_users u ON u.id=c.user_id "
            "WHERE c.user_id=%s AND c.course_id=%s AND u.deletion_due_at IS NULL "
            "AND c.status NOT IN ('deleted','purged')" + (" FOR SHARE OF c,u" if lock else ""),
            (store._user(), course),
        ).fetchone()
        is not None
    )


def unpack(checkpoint):
    if checkpoint.get("schema_version") == ENVELOPE:
        return checkpoint, checkpoint["worker"]
    # Existing worker checkpoints remain independently readable.
    return {"schema_version": ENVELOPE, "worker": checkpoint}, checkpoint


def initial_reservations(envelope, worker, limit):
    counts = (worker.get("calls_reserved", 0), envelope.get("initial_calls_reserved", 0))
    if any(type(count) is not int or count < 0 for count in counts):
        raise CatalogConflict("目录任务预算记录无效")
    if "calls_reserved" in worker:
        return max(counts)
    if "initial_calls_reserved" in envelope:
        # Legacy ledger counted structured() calls, each with up to two requests.
        return 2 * envelope["initial_calls_reserved"]
    # A standalone legacy checkpoint has no durable invocation history.
    return limit if worker else 0


class DispatchRepository(CatalogRepository):
    """Keep worker protocols strict; all writes retain Repository fencing."""

    def __init__(self, store, *, config=None):
        super().__init__(store)
        self.config = config or DispatchConfig()

    def job(self, course_id, job_id):
        row = super().job(course_id, job_id)
        if row:
            envelope, worker = unpack(row["checkpoint"])
            if row["mode"] == "initial" and worker:
                worker = {
                    **worker,
                    "calls_reserved": initial_reservations(
                        envelope, worker, self.config.initial.max_calls
                    ),
                }
            row = {**row, "checkpoint": worker}
        return row

    def checkpoint(self, course_id, job_id, lease_token, *, stage, checkpoint, lease_seconds=900):
        with self.store.transaction():
            row = self._fence(course_id, job_id, lease_token)
            if not scope_allowed(self.store, course_id):
                raise CatalogInvalid("course or account unavailable")
            envelope, prior = unpack(row["checkpoint"])
            if row["mode"] == "initial" and (
                stage == "model_call" or "calls_reserved" in checkpoint
            ):
                limit = self.config.initial.max_calls
                reserved = initial_reservations(envelope, prior, limit)
                if stage == "model_call":
                    if reserved >= limit:
                        raise CatalogBudgetExceeded("cumulative initial call budget exhausted")
                    reserved += 1
                envelope = {**envelope, "initial_calls_reserved": reserved}
            envelope = {**envelope, "worker": checkpoint, "execution_started": True}
            result = super().checkpoint(
                course_id,
                job_id,
                lease_token,
                stage=stage,
                checkpoint=envelope,
                lease_seconds=lease_seconds,
            )
            return {**result, "checkpoint": checkpoint}

    def retry_failed(self, course, job_id):
        """Requeue the same fenced identity, preserving checkpoints and all limits."""
        with self.store.transaction():
            self.connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"catalog-schedule:{self.user}:{course}",),
            )
            self.connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"knowledge:{self.user}:{course}",),
            )
            row = self.connection.execute(
                "SELECT * FROM course_knowledge_catalog_jobs "
                "WHERE user_id=%s AND course_id=%s AND id=%s FOR UPDATE",
                (self.user, course, job_id),
            ).fetchone()
            if not row:
                return None
            if not scope_allowed(self.store, course, lock=True):
                raise CatalogConflict("课程或账户不可用")
            envelope, worker = unpack(row["checkpoint"])
            if row["status"] != "failed":
                if envelope.get("manual_retry") and row["status"] in {
                    "queued",
                    "running",
                    "retry_wait",
                    "succeeded",
                }:
                    return row
                raise CatalogConflict("仅失败目录任务可以重试")
            head = self.head(course)
            version_id = head["current_version_id"] if head else None
            if version_id != row["base_version_id"] or (
                self.snapshot(course)["hash"] != row["snapshot_hash"]
            ):
                raise CatalogConflict("目录版本或知识快照已变化")
            if row["mode"] == "initial":
                config = self.config.initial
                exhausted = (
                    initial_reservations(envelope, worker, config.max_calls) >= config.max_calls
                )
            else:
                config = self.config.incremental
                exhausted = (
                    worker.get("logical_calls", 0) >= config.max_calls
                    or worker.get("request_attempts_reserved", 0) >= config.max_request_attempts
                )
            if exhausted or row["attempt"] >= config.max_job_attempts:
                raise CatalogConflict("目录任务预算或执行次数已耗尽")
            if row["error_code"] in {"CatalogNoChanges", "SupersededBeforeExecution"}:
                raise CatalogConflict("该目录任务已结束或被取代")
            try:
                return self.connection.execute(
                    """UPDATE course_knowledge_catalog_jobs SET status='queued',
                       stage='manual_retry',next_run_at=clock_timestamp(),checkpoint=%s,
                       error_code=NULL,error_message=NULL,updated_at=clock_timestamp()
                       WHERE user_id=%s AND course_id=%s AND id=%s RETURNING *""",
                    (Jsonb({**envelope, "manual_retry": True}), self.user, course, job_id),
                ).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise CatalogConflict("课程已有有效目录任务") from exc

    def baseline(self, course, version):
        """Read only a successful task pinned to this immutable version.

        Legacy incremental checkpoints contain enough data for recovery. Initial
        legacy checkpoints have no semantic hashes: conservative review is safe.
        Invalid/missing policy or scope means no baseline, never fabricated hashes.
        """
        if not version:
            return None
        row = self.connection.execute(
            """SELECT * FROM course_knowledge_catalog_jobs WHERE user_id=%s AND course_id=%s
               AND status='succeeded' AND result_version_id=%s AND snapshot_hash=%s
               ORDER BY updated_at DESC,id DESC LIMIT 1""",
            (self.user, course, version["id"], version["knowledge_snapshot_hash"]),
        ).fetchone()
        if not row:
            return None
        try:
            envelope, worker = unpack(row["checkpoint"])
            if "semantic_baseline" in envelope:
                if (
                    envelope.get("semantic_rules_version") != RULES_VERSION
                    or envelope.get("publication_rules_version") != version["rules_version"]
                ):
                    return None
                baseline = SemanticBaseline.model_validate(envelope["semantic_baseline"])
            else:
                cp = IncrementalCheckpoint.model_validate(worker)
                if (
                    cp.job_id != str(row["id"])
                    or cp.course_id != course
                    or cp.snapshot_hash != row["snapshot_hash"]
                    or cp.base_version_id != str(row["base_version_id"])
                    or version["rules_version"] != cp.rules_version
                ):
                    return None
                baseline = SemanticBaseline(
                    version_id=str(version["id"]),
                    snapshot_hash=row["snapshot_hash"],
                    point_hashes=cp.semantic_hashes,
                )
            if (
                baseline.version_id != str(version["id"])
                or baseline.snapshot_hash != version["knowledge_snapshot_hash"]
                or set(baseline.point_hashes)
                != {str(a["point_id"]) for a in version["assignments"]}
            ):
                return None
            return baseline
        except (ValueError, KeyError, TypeError):
            return None

    def publish(self, course_id, **kwargs):
        # Compute outside publication locks, but only from the exact frozen input.
        snapshot = self.snapshot(course_id)
        if snapshot["hash"] != kwargs["snapshot_hash"]:
            raise CatalogSnapshotChanged("dispatch input changed before publication")
        hashes = semantic_hashes(snapshot)
        with self.store.transaction():
            result = super().publish(course_id, **kwargs)
            # super() took ingestion -> tables -> course -> head -> task locks.
            # Guard cancellation after that order; rollback publication if invalid.
            if not scope_allowed(self.store, course_id, lock=True):
                raise CatalogInvalid("course or account unavailable at publication")
            row = super().job(course_id, kwargs["job_id"])
            envelope, _ = unpack(row["checkpoint"])
            baseline = SemanticBaseline(
                version_id=str(result["version_id"]),
                snapshot_hash=snapshot["hash"],
                point_hashes=hashes,
            )
            envelope = {
                **envelope,
                "semantic_baseline": baseline.model_dump(mode="json"),
                "semantic_rules_version": RULES_VERSION,
                "publication_rules_version": kwargs["rules_version"],
            }
            self.connection.execute(
                """UPDATE course_knowledge_catalog_jobs SET checkpoint=%s
                   WHERE user_id=%s AND course_id=%s AND id=%s AND status='succeeded'
                     AND result_version_id=%s""",
                (Jsonb(envelope), self.user, course_id, row["id"], result["version_id"]),
            )
            return result

    def fail(self, course_id, job_id, lease_token, *, error_code, error_message, retry_at=None):
        with self.store.transaction():
            row = self._fence(course_id, job_id, lease_token)
            config = self.config.initial if row["mode"] == "initial" else self.config.incremental
            transient = retry_at is not None or error_code in DB_TRANSIENT | MODEL_TRANSIENT
            retry_at = (
                datetime.now(UTC)
                + timedelta(
                    seconds=min(config.retry_seconds * 2 ** min(row["attempt"] - 1, 10), 900)
                )
                if transient and row["attempt"] < config.max_job_attempts
                else None
            )
            result = super().fail(
                course_id,
                job_id,
                lease_token,
                error_code=error_code,
                error_message=error_message,
                retry_at=retry_at,
            )
            if error_code == "CatalogNoChanges":
                self.connection.execute(
                    "UPDATE course_knowledge_catalog_jobs SET stage='no_changes' "
                    "WHERE user_id=%s AND course_id=%s AND id=%s",
                    (self.user, course_id, job_id),
                )
            return result


@dataclass(frozen=True)
class DispatchConfig:
    initial: WorkerConfig = WorkerConfig()
    incremental: IncrementalConfig = IncrementalConfig()


class CatalogDispatcher:
    def __init__(self, store, model_factory, *, config=None, scheduler_config=None):
        self.store, self.model_factory = store, model_factory
        self.config = config or DispatchConfig()
        self.scheduler = CatalogScheduler(store, config=scheduler_config)

    def scope_allowed(self, course):
        return scope_allowed(self.store, course)

    def claim(self):
        """Inventory pending owners; claim only inside each owner's ContextVar.

        Do not assume that the returned course is the one discovered by a scan.
        Each actual row is checked again inside run(), including legacy jobs.
        Unavailable owners/courses are claimed solely to finish their existing
        tasks as failed, without constructing a model or creating new work.
        """
        after = "00000000-0000-0000-0000-000000000000"
        while True:
            users = self.store.connection.execute(
                """SELECT u.id FROM app_users u WHERE u.id>%s::uuid AND EXISTS (
                     SELECT 1 FROM course_knowledge_catalog_jobs j JOIN courses c
                       ON c.user_id=j.user_id AND c.course_id=j.course_id
                     WHERE j.user_id=u.id AND
                       ((j.status IN ('queued','retry_wait') AND next_run_at<=clock_timestamp())
                        OR (j.status='running' AND lease_until<=clock_timestamp())))
                   ORDER BY u.id LIMIT %s""",
                (after, self.scheduler.page_size),
            ).fetchall()
            if not users:
                return None
            for user in users:
                token = self.store.bind_user(str(user["id"]))
                try:
                    lease = max(
                        self.config.initial.lease_seconds, self.config.incremental.lease_seconds
                    )
                    job = self.scheduler.repo.claim(lease_seconds=lease)
                    if job:
                        return job
                finally:
                    self.store.reset_user(token)
            after = str(users[-1]["id"])

    def run(self, job):
        """Call in the execution thread/process; never inherit user context."""
        token = self.store.bind_user(str(job["user_id"]))
        try:
            # Existing account-purge protocol: session lock, no long transaction.
            with self.store.account_file_access(str(job["user_id"])) as allowed:
                return self._run(job, allowed=allowed)
        finally:
            self.store.reset_user(token)

    def _run(self, job, *, allowed):
        repo = DispatchRepository(self.store, config=self.config)
        try:
            if not allowed or not self.scope_allowed(job["course_id"]):
                raise CatalogInvalid("course or account unavailable")
            job = self.scheduler.prepare_claim(job)
            if job is None:
                return None
            if not self.scope_allowed(job["course_id"]):
                raise CatalogInvalid("course or account unavailable")
            current = repo.current(job["course_id"])
            version = current["version"]
            if (version["id"] if version else None) != job["base_version_id"]:
                raise CatalogConflict("claimed task base no longer current")
            if job["mode"] == "initial":
                worker_type, config = InitialCatalogWorker, self.config.initial
            elif job["mode"] == "incremental":
                worker_type, config = IncrementalCatalogWorker, self.config.incremental
            else:
                raise CatalogInvalid("unknown catalog mode")
            if job["attempt"] > config.max_job_attempts:
                raise CatalogInvalid("execution attempt limit exceeded")
            repo.checkpoint(
                job["course_id"],
                job["id"],
                job["lease_token"],
                stage="starting",
                checkpoint=unpack(job["checkpoint"])[1],
                lease_seconds=config.lease_seconds,
            )
            # A new model + worker per task; no shared counters or callbacks.
            worker = worker_type(repo, self.model_factory(), config=config)
            worker_job = {**job, "checkpoint": unpack(job["checkpoint"])[1]}
            if job["mode"] == "incremental":
                worker.run(worker_job, semantic_baseline=repo.baseline(job["course_id"], version))
            else:
                worker.run(worker_job)
            return self.scheduler.repo.job(job["course_id"], job["id"])
        except CatalogLeaseLost:
            return None  # A stale owner must not checkpoint, publish, or fail.
        except Exception as exc:
            if job is None:
                raise
            try:
                return repo.fail(
                    job["course_id"],
                    job["id"],
                    job["lease_token"],
                    error_code=type(exc).__name__,
                    error_message=f"目录执行失败（{type(exc).__name__}）",
                    retry_at=datetime.now(UTC) if _retryable(exc) else None,
                )
            except (CatalogLeaseLost, psycopg.OperationalError, psycopg.InterfaceError):
                # Lost database connections recover through claim's expired lease.
                return None
