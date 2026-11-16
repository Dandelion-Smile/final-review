"""Bounded scheduling and process-wide resource budgets for pipeline workers."""

import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from threading import BoundedSemaphore, Lock

import psycopg

from .config import Settings
from .observe import count, observation

_lock = Lock()
_limits = {}


@lru_cache(maxsize=1)
def runtime_settings():
    return Settings()


@contextmanager
def resource_slot(kind, settings=None):
    with ExitStack() as stack:
        with observation(f"resource.{kind}.wait"):
            stack.enter_context(_resource_slot(kind, settings))
        yield


@contextmanager
def _resource_slot(kind, settings=None):
    settings = settings or runtime_settings()
    capacity = {
        "conversion": settings.material_conversion_concurrency,
        "vision": settings.material_vision_global_concurrency,
        "embedding": settings.embedding_request_concurrency,
        "polish": settings.material_note_polish_concurrency,
    }[kind]
    with _lock:
        semaphore = _limits.setdefault((kind, capacity), BoundedSemaphore(capacity))
    with semaphore:
        url = settings.database_url.get_secret_value()
        if not url:
            yield
            return
        # Advisory slots coordinate budgets across material/knowledge worker processes.
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as connection:
            acquired = None
            while acquired is None:
                for index in range(capacity):
                    key = f"pipeline-resource:{kind}:{index}"
                    if connection.execute(
                        "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (key,)
                    ).fetchone()[0]:
                        acquired = key
                        break
                if acquired is None:
                    time.sleep(0.05)
            try:
                yield
            finally:
                connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (acquired,))


def embedding_batches(embeddings, texts, *, batch_size=10, settings=None):
    """Yield ordered batches; callers checkpoint only validated results."""
    if not 1 <= batch_size <= 10:
        raise ValueError("向量请求每批必须为 1 到 10 条")
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        with resource_slot("embedding", settings):
            with observation(
                "embedding.batch",
                {"items": len(batch), "model": getattr(embeddings, "model", None)},
            ):
                count("embedding_requests")
                vectors = embeddings.embed_documents(batch)
        if len(vectors) != len(batch):
            raise ValueError("向量返回数量与输入数量不一致")
        yield start, vectors


def run_queue(claim, process, concurrency, *, once=False, poll_seconds=1, stop_event=None):
    """Claim only when a slot is free. Drain active tasks on graceful shutdown."""
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="pipeline") as pool:
        active = set()
        claimed_once = False
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            while len(active) < concurrency and not (once and claimed_once):
                job = claim()
                if job is None:
                    break
                active.add(pool.submit(process, job))
                claimed_once = True
            if not active:
                if once:
                    return
                time.sleep(poll_seconds)
                continue
            completed, active = wait(active, timeout=poll_seconds, return_when=FIRST_COMPLETED)
            for future in completed:
                try:
                    future.result()
                except Exception:
                    logging.getLogger(__name__).exception("后台任务执行异常")
            if once and not active:
                return
