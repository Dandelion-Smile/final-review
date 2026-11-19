"""Persistent catalog scheduling; never runs inference during discovery.

Only the inventory query crosses user scopes. Every catalog operation explicitly
binds its owner. No migration, ingestion hook, or business-database setup occurs.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from .knowledge_catalog_store import CatalogRepository

ENVELOPE = "catalog-dispatch-v1"
LIVE = ("queued", "running", "retry_wait")


@dataclass(frozen=True)
class SchedulerConfig:
    quiet_seconds: int = 60
    max_wait_seconds: int = 300
    scan_seconds: int = 10
    page_size: int = 100

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError("scheduler limits must be positive integers")


def window(now, snapshot_hash, success_at, previous=None, *, config=None):
    """Repeated signals never push due_at; changes without success also debounce."""
    config = config or SchedulerConfig()
    old = previous or {}
    first = datetime.fromisoformat(old["first_pending_at"]) if old else now
    last = datetime.fromisoformat(old["last_change_at"]) if old else now
    previous_success = old.get("last_success_signal_at")
    success = success_at.isoformat() if success_at else None
    newer_success = success is not None and (previous_success is None or success > previous_success)
    if old and newer_success:
        # Use committed success time, clamped to the observed clock. Discovery of
        # historical input starts a fresh window rather than running immediately.
        last = max(last, min(now, max(first, success_at)))
    elif old and snapshot_hash != old["observed_snapshot_hash"]:
        last = now
    due = min(
        last + timedelta(seconds=config.quiet_seconds),
        first + timedelta(seconds=config.max_wait_seconds),
    )
    return dict(
        first_pending_at=first.isoformat(),
        last_change_at=last.isoformat(),
        last_relevant_success_at=last.isoformat(),
        last_success_signal_at=max(filter(None, [success, previous_success]), default=None),
        observed_snapshot_hash=snapshot_hash,
        due_at=due.isoformat(),
    )


def collecting_checkpoint(schedule):
    return {"schema_version": ENVELOPE, "schedule": schedule, "worker": {}}


def pristine(job):
    cp = job["checkpoint"]
    return (
        cp.get("schema_version") == ENVELOPE
        and cp.get("worker") == {}
        and not cp.get("execution_started")
        and cp.get("initial_calls_reserved", 0) == 0
        and "semantic_baseline" not in cp
        and job["result_version_id"] is None
        and job["stage"] == "collecting"
    )


@dataclass(frozen=True)
class Change:
    user_id: str
    course_id: str
    mode: str
    base_version_id: object
    snapshot_hash: str
    success_at: datetime | None


def changed_input(current, snapshot):
    """Empty initial courses need no AI; an empty incremental removes old points."""
    version = current["version"]
    if version:
        if version["knowledge_snapshot_hash"] == snapshot["hash"]:
            return None
        return "incremental", version["id"]
    return ("initial", None) if snapshot["points"] else None


class CatalogScheduler:
    def __init__(self, store, *, page_size=None, clock=None, config=None):
        self.config = config or SchedulerConfig()
        page_size = self.config.page_size if page_size is None else page_size
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self.store = store
        self.repo = CatalogRepository(store)
        self.page_size = page_size
        self.clock = clock or (lambda: datetime.now(UTC))

    def courses(self, *, successes_only=False, since=None):
        """Keyset pages, including history on every full reconciliation.

        Success timestamps are hints, never an exclusive correctness watermark.
        Archived courses remain writable under DomainService.course rules.
        """
        after_user, after_course = "00000000-0000-0000-0000-000000000000", ""
        while True:
            rows = self.store.connection.execute(
                """SELECT c.user_id,c.course_id,
                   (SELECT max(j.updated_at) FROM knowledge_build_jobs j
                    WHERE j.user_id=c.user_id AND j.course_id=c.course_id
                      AND j.status='succeeded') AS success_at
                   FROM courses c JOIN app_users u ON u.id=c.user_id
                   WHERE u.deletion_due_at IS NULL AND c.status NOT IN ('deleted','purged')
                     AND (c.user_id,c.course_id)>(%s::uuid,%s)
                     AND (NOT %s OR EXISTS (SELECT 1 FROM knowledge_build_jobs j
                       WHERE j.user_id=c.user_id AND j.course_id=c.course_id
                         AND j.status='succeeded'
                         AND (%s::timestamptz IS NULL OR j.updated_at>=%s)))
                   ORDER BY c.user_id,c.course_id LIMIT %s""",
                (after_user, after_course, successes_only, since, since, self.page_size),
            ).fetchall()
            if not rows:
                return
            yield from rows
            after_user, after_course = str(rows[-1]["user_id"]), rows[-1]["course_id"]

    def eligible(self, course, *, lock=False):
        return (
            self.store.connection.execute(
                """SELECT 1 FROM courses c JOIN app_users u ON u.id=c.user_id
               WHERE c.user_id=%s AND c.course_id=%s AND u.deletion_due_at IS NULL
                 AND c.status NOT IN ('deleted','purged')
                 AND NOT EXISTS (SELECT 1 FROM knowledge_build_jobs j
                   WHERE j.user_id=c.user_id AND j.course_id=c.course_id
                     AND j.status='running')"""
                + (" FOR SHARE OF c,u" if lock else ""),
                (self.repo.user, course),
            ).fetchone()
            is not None
        )

    def discover(self, *, successes_only=False):
        for row in self.courses(successes_only=successes_only):
            user, course = str(row["user_id"]), row["course_id"]
            token = self.store.bind_user(user)
            change = None
            try:
                if not self.eligible(course):
                    continue
                snapshot = self.repo.snapshot(course)
                identity = changed_input(self.repo.current(course), snapshot)
                if identity:
                    change = Change(user, course, *identity, snapshot["hash"], row["success_at"])
            finally:
                self.store.reset_user(token)
            if change:
                yield change

    def _lock(self, course):
        # Independent of ingestion locks. Never held during inference/publication.
        self.store.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"catalog-schedule:{self.repo.user}:{course}",),
        )

    def _success(self, course):
        return self.store.connection.execute(
            "SELECT max(updated_at) AS t FROM knowledge_build_jobs "
            "WHERE user_id=%s AND course_id=%s AND status='succeeded'",
            (self.repo.user, course),
        ).fetchone()["t"]

    def _pending(self, course):
        return self.store.connection.execute(
            "SELECT * FROM course_knowledge_catalog_jobs WHERE user_id=%s AND course_id=%s "
            "AND status IN ('queued','running','retry_wait') FOR UPDATE",
            (self.repo.user, course),
        ).fetchone()

    def _enqueue(self, course, identity, snapshot, schedule):
        job = self.repo.create_job(
            course, mode=identity[0], base_version_id=identity[1], snapshot_hash=snapshot["hash"]
        )
        # ONLY a proven pre-execution replacement can be reactivated. No arbitrary
        # failed identity, successful result, or worker checkpoint is restarted.
        revive = (
            job["status"] == "failed"
            and job["error_code"] == "SupersededBeforeExecution"
            and pristine(job)
        )
        if (job["status"] == "queued" and job["attempt"] == 0) or revive:
            job = self.store.connection.execute(
                """UPDATE course_knowledge_catalog_jobs SET status='queued',stage='collecting',
                   checkpoint=%s,next_run_at=%s,attempt=0,error_code=NULL,error_message=NULL,
                   updated_at=clock_timestamp() WHERE user_id=%s AND id=%s RETURNING *""",
                (
                    Jsonb(collecting_checkpoint(schedule)),
                    datetime.fromisoformat(schedule["due_at"]),
                    self.repo.user,
                    job["id"],
                ),
            ).fetchone()
        return job

    def reconcile(self, course):
        """Bound-user, per-course atomic scheduling; immutable snapshot identities."""
        with (
            self.store.account_file_access(str(self.repo.user)) as allowed,
            self.store.transaction(),
        ):
            self._lock(course)
            if not allowed or not self.eligible(course, lock=True):
                return None
            snapshot, current = self.repo.snapshot(course), self.repo.current(course)
            identity = changed_input(current, snapshot)
            pending = self._pending(course)
            if pending:
                if pending["status"] == "queued" and pristine(pending):
                    schedule = window(
                        self.clock(),
                        snapshot["hash"],
                        self._success(course),
                        pending["checkpoint"]["schedule"],
                        config=self.config,
                    )
                    return self.store.connection.execute(
                        """UPDATE course_knowledge_catalog_jobs SET checkpoint=%s,next_run_at=%s,
                           updated_at=clock_timestamp() WHERE user_id=%s AND id=%s RETURNING *""",
                        (
                            Jsonb(collecting_checkpoint(schedule)),
                            datetime.fromisoformat(schedule["due_at"]),
                            self.repo.user,
                            pending["id"],
                        ),
                    ).fetchone()
                return pending
            if not identity:
                return None
            schedule = window(
                self.clock(), snapshot["hash"], self._success(course), config=self.config
            )
            return self._enqueue(course, identity, snapshot, schedule)

    def scan(self, *, successes_only=False, since=None):
        """Also reconcile active tasks whose input returned to the current version."""
        scheduled = 0
        for row in self.courses(successes_only=successes_only, since=since):
            token = self.store.bind_user(str(row["user_id"]))
            try:
                if self.reconcile(row["course_id"]):
                    scheduled += 1
            finally:
                self.store.reset_user(token)
        return scheduled

    def prepare_claim(self, job):
        """Validate collecting input AFTER legitimate claim. No model calls.

        Replacements and successor creation commit together. Returning None means
        callers must claim again, since claim() cannot target a particular task.
        """
        if job["stage"] != "collecting":
            return job
        course = job["course_id"]
        with self.store.transaction():
            self._lock(course)
            eligible = self.eligible(course, lock=True)
            job = self.repo._fence(course, job["id"], job["lease_token"])
            if not pristine(job):
                raise ValueError("collecting task contains execution state")
            if not eligible:
                schedule = job["checkpoint"]["schedule"]
                return self._defer(
                    job, schedule, self.clock() + timedelta(seconds=self.config.scan_seconds)
                )
            snapshot = self.repo.snapshot(course)
            schedule = window(
                self.clock(),
                snapshot["hash"],
                self._success(course),
                job["checkpoint"]["schedule"],
                config=self.config,
            )
            if datetime.fromisoformat(schedule["due_at"]) > self.clock():
                return self._defer(job, schedule, datetime.fromisoformat(schedule["due_at"]))
            identity = changed_input(self.repo.current(course), snapshot)
            if not identity or (identity[0], identity[1], snapshot["hash"]) != (
                job["mode"],
                job["base_version_id"],
                job["snapshot_hash"],
            ):
                self.repo.fail(
                    course,
                    job["id"],
                    job["lease_token"],
                    error_code="SupersededBeforeExecution",
                    error_message="合并结束，执行前输入已被替代；未调用模型",
                )
                if identity:
                    self._enqueue(course, identity, snapshot, schedule)
                return None
            return job

    def _defer(self, job, schedule, due):
        self.store.connection.execute(
            """UPDATE course_knowledge_catalog_jobs SET status='queued',checkpoint=%s,
               next_run_at=%s,attempt=0,lease_token=NULL,lease_until=NULL,
               updated_at=clock_timestamp() WHERE user_id=%s AND id=%s""",
            (Jsonb(collecting_checkpoint(schedule)), due, self.repo.user, job["id"]),
        )
        return None


class CatalogService:
    """Standalone bounded process using the existing pipeline run_queue seam.

    Session advisory slots cap concurrent executions across service instances
    configured with the SAME capacity. Slots precede claim; none are held while
    waiting for a future next_run_at. A crashed connection releases its slot.
    """

    def __init__(self, settings, model_factory, *, stop_event=None, monotonic=None):
        import time
        from threading import Event

        from .knowledge_catalog_dispatch import CatalogDispatcher
        from .postgres import PostgresStore

        self.settings = settings
        self.model_factory = model_factory
        self.stop_event = stop_event or Event()
        self.monotonic = monotonic or time.monotonic
        self.store = PostgresStore(settings.database_url.get_secret_value())
        self.scheduler_config = SchedulerConfig(
            quiet_seconds=settings.catalog_quiet_window_seconds,
            max_wait_seconds=settings.catalog_max_wait_seconds,
            scan_seconds=settings.catalog_scan_interval_seconds,
            page_size=settings.catalog_page_size,
        )
        self.dispatcher = CatalogDispatcher(
            self.store, model_factory, scheduler_config=self.scheduler_config
        )
        self.last_scan = self.last_full = None

    def scan_if_due(self):
        now = self.monotonic()
        if self.last_scan is not None and now - self.last_scan < self.scheduler_config.scan_seconds:
            return
        full = (
            self.last_full is None
            or now - self.last_full >= self.settings.catalog_reconcile_interval_seconds
        )
        since = datetime.now(UTC) - timedelta(seconds=2 * self.scheduler_config.scan_seconds)
        self.dispatcher.scheduler.scan(successes_only=not full, since=None if full else since)
        self.last_scan = now
        if full:
            self.last_full = now

    def try_slot(self):
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(
            self.store.database_url, autocommit=True, row_factory=dict_row, connect_timeout=5
        )
        try:
            for index in range(self.settings.catalog_max_concurrency):
                if connection.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired",
                    (f"catalog-model-slot:{index}",),
                ).fetchone()["acquired"]:
                    return connection
        except BaseException:
            connection.close()
            raise
        connection.close()
        return None

    def claim(self):
        import logging

        import psycopg

        if not self.settings.catalog_scheduler_enabled or self.stop_event.is_set():
            return None
        slot = None
        try:
            self.scan_if_due()
            slot = self.try_slot()
            if slot is None:
                return None
            if self.stop_event.is_set() or not self.settings.catalog_scheduler_enabled:
                return None
            job = self.dispatcher.claim()
            if job:
                work, slot = (job, slot), None
                return work
            return None
        except (
            psycopg.OperationalError,
            psycopg.InterfaceError,
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
        ):
            logging.getLogger(__name__).warning("目录扫描遇到数据库瞬态错误；下轮重试")
            return None
        finally:
            if slot is not None:
                slot.close()

    def process(self, work):
        from .knowledge_catalog_dispatch import CatalogDispatcher
        from .postgres import PostgresStore

        job, slot = work
        store = PostgresStore(self.store.database_url)
        try:
            dispatcher = CatalogDispatcher(
                store, self.model_factory, scheduler_config=self.scheduler_config
            )
            return dispatcher.run(job)
        finally:
            store.close()
            slot.close()

    def run(self, *, once=False):
        from .pipeline_runtime import run_queue

        if not self.settings.catalog_scheduler_enabled:
            return
        # Read-only prerequisite check. This process never applies migrations.
        if not self.store.connection.execute(
            "SELECT to_regclass('public.course_knowledge_catalog_jobs') AS table_name"
        ).fetchone()["table_name"]:
            raise RuntimeError("目录表尚未部署；请在独立验收和迁移审核后再启用服务")
        run_queue(
            self.claim,
            self.process,
            self.settings.catalog_max_concurrency,
            once=once,
            poll_seconds=1,
            stop_event=self.stop_event,
        )

    def close(self):
        self.store.close()


def main():
    import argparse
    import logging
    import signal

    from .config import Settings
    from .llm import build_review_model

    parser = argparse.ArgumentParser(description="Standalone course catalog scheduler")
    parser.add_argument("--once", action="store_true", help="扫描并最多处理一次合法领取")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    if not settings.catalog_scheduler_enabled:
        logging.getLogger(__name__).info("目录自动调度已禁用")
        return
    config = next(
        (c for c in settings.available_chat_models() if c.id == settings.catalog_model_id), None
    )
    if config is None:
        raise ValueError("CATALOG_MODEL_ID 未匹配到已配置模型")
    service = CatalogService(
        settings, lambda: build_review_model(config, settings, knowledge_generation=True)
    )
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: service.stop_event.set())
    try:
        service.run(once=args.once)
    finally:
        service.close()


if __name__ == "__main__":
    main()
