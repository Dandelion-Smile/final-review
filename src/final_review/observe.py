"""Best-effort Langfuse tracing and local timings for background pipelines."""

import logging
import os
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from hashlib import sha256
from threading import Lock

_bound = ContextVar("pipeline_trace", default=None)
_client_state = {"ready": False, "client": None}
logger = logging.getLogger(__name__)


class _Span:
    def __init__(self, span):
        self._span = span

    def update(self, **kwargs):
        if self._span is None:
            return
        try:
            self._span.update(**kwargs)
        except Exception:
            return


def _client():
    if _client_state["ready"]:
        return _client_state["client"]
    _client_state["ready"] = True
    from dotenv import load_dotenv

    load_dotenv(override=False)
    if not (
        os.getenv("LANGFUSE_PUBLIC_KEY")
        and os.getenv("LANGFUSE_SECRET_KEY")
        and (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST"))
    ):
        return None
    try:
        from langfuse import get_client
    except ImportError:
        return None
    try:
        _client_state["client"] = get_client()
    except Exception:
        _client_state["client"] = None
    return _client_state["client"]


def start_note_trace(session_id: str, job_id: str):
    return _bound.set(
        dict(
            session_id=session_id,
            job_id=job_id,
            tags=["note-worker"],
            metadata={},
            metrics={},
            lock=Lock(),
        )
    )


def finish_note_trace(token) -> None:
    _bound.reset(token)
    flush()


def flush():
    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        return


def pipeline_trace_id(user_id, document_id):
    return sha256(f"material:{user_id}:{document_id}".encode()).hexdigest()[:32]


@contextmanager
def job_trace(kind, job):
    document_id = job["document_id"]
    trace_id = pipeline_trace_id(job["user_id"], document_id)
    metrics = {
        "trace_id": trace_id,
        "model_requests": 0,
        "vision_requests": 0,
        "embedding_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "phase": job.get("phase") or kind,
    }
    metadata = dict(
        document_id=document_id,
        course_id=job.get("course_id"),
        phase=job.get("phase"),
        attempt=job.get("attempts"),
    )
    token = _bound.set(
        dict(
            session_id=f"material-{document_id}",
            job_id=str(job["job_id"]),
            trace_id=trace_id,
            tags=[kind],
            metadata=metadata,
            metrics=metrics,
            lock=Lock(),
        )
    )
    try:
        with observation(kind, metadata, root=True) as span:
            yield metrics
            span.update(output=dict(metrics))
            if metrics.get("status") == "failed":
                span.update(level="ERROR", status_message="阶段失败；详见子步骤及任务错误")
            elif metrics.get("status") == "lease_lost":
                span.update(level="WARNING", status_message="租约已失效，未发布结果")
    finally:
        _bound.reset(token)


def count(name, value=1):
    bound = _bound.get()
    if bound:
        with bound["lock"]:
            bound["metrics"][name] = bound["metrics"].get(name, 0) + value


def record_usage(span, usage):
    """Only report provider tokens; do not estimate missing usage or cache hits."""
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    usage = usage or {}
    details = {}
    for dest, aliases in {
        "input": ("input_tokens", "prompt_tokens"),
        "output": ("output_tokens", "completion_tokens"),
    }.items():
        for key in aliases:
            if isinstance(usage.get(key), int):
                details[dest] = usage[key]
                count(dest + "_tokens", usage[key])
                break
    if details:
        span.update(usage_details=details)
    else:
        span.update(metadata={"usage_available": False})


@contextmanager
def observation(name: str, metadata: dict | None = None, *, as_type="span", model=None, root=False):
    bound = _bound.get()
    started = time.monotonic()
    stack = ExitStack()
    span = None
    if bound:
        client = _client()
        if client is not None:
            try:
                from langfuse import propagate_attributes

                stack.enter_context(
                    propagate_attributes(
                        session_id=bound["session_id"],
                        tags=bound["tags"],
                    )
                )
                options = dict(
                    name=name,
                    as_type=as_type,
                    metadata={
                        "job_id": bound["job_id"],
                        **bound["metadata"],
                        **(metadata or {}),
                    },
                )
                if model is not None:
                    options["model"] = model
                if root and bound.get("trace_id"):
                    options["trace_context"] = {"trace_id": bound["trace_id"]}
                span = stack.enter_context(client.start_as_current_observation(**options))
            except Exception:
                try:
                    stack.close()
                except Exception:
                    pass
                stack = ExitStack()
    wrapper = _Span(span)
    if as_type == "generation":
        count("model_requests")
    try:
        yield wrapper
    except BaseException as exc:
        wrapper.update(level="ERROR", status_message=type(exc).__name__)
        count(name + ".errors")
        raise
    finally:
        count(name + ".seconds", round(time.monotonic() - started, 6))
        try:
            stack.close()
        except Exception:
            logger.debug("Langfuse span close unavailable", exc_info=True)
