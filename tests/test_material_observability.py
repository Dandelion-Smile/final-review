"""Trace metrics use actual provider responses; exporter errors never gate parsing."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from final_review import observe
from final_review.config import Settings
from final_review.embeddings import _ObservedClient
from final_review.slide_understanding import READ_PROMPT, SlideInterpreter


def job(phase="preview"):
    return dict(job_id="j-" + phase, user_id="u", document_id="d", phase=phase)


def test_visual_generation_reports_actual_tokens_and_request_count():
    interpreter = SlideInterpreter.__new__(SlideInterpreter)
    interpreter.limit_settings = Settings(_env_file=None)
    interpreter.model = "test-vision"
    interpreter._complete_unlimited = lambda *args: SimpleNamespace(
        usage=dict(prompt_tokens=12, completion_tokens=6),
        choices=[SimpleNamespace(finish_reason="stop")],
    )
    with observe.job_trace("material-worker", job("parse")) as metrics:
        interpreter._completion(READ_PROMPT, b"image", "text")
    assert metrics["vision_requests"] == metrics["model_requests"] == 1
    assert metrics["input_tokens"] == 12 and metrics["output_tokens"] == 6
    assert "vision.understand.seconds" in metrics
    assert "resource.vision.wait.seconds" in metrics


def test_embeddings_report_provider_usage_without_counting_as_visual():
    client = _ObservedClient(
        SimpleNamespace(
            create=lambda **kwargs: dict(
                data=[dict(embedding=[1, 0, 0])],
                usage=dict(prompt_tokens=19, total_tokens=19),
            )
        ),
        "test-embedding",
    )
    with observe.job_trace("material-worker", job("parse")) as metrics:
        result = client.create(input=["原文"])
    assert result["data"][0]["embedding"] == [1, 0, 0]
    assert metrics["input_tokens"] == 19
    assert metrics["model_requests"] == metrics["vision_requests"] == 0
    assert metrics["embedding_provider_requests"] == 1


def test_preview_parse_and_knowledge_share_trace_but_users_are_isolated():
    with observe.job_trace("material-worker", job()) as preview:
        pass
    with observe.job_trace("knowledge-worker", job("knowledge")) as knowledge:
        pass
    assert preview["trace_id"] == knowledge["trace_id"]
    assert preview["trace_id"] != observe.pipeline_trace_id("other-user", "d")


def test_exporter_exit_error_never_replaces_application_error(monkeypatch):
    import sys

    class BrokenObservation:
        def __enter__(self):
            return SimpleNamespace(update=lambda **kwargs: None)

        def __exit__(self, *args):
            raise RuntimeError("exporter unavailable")

    monkeypatch.setitem(
        sys.modules,
        "langfuse",
        SimpleNamespace(propagate_attributes=lambda **kwargs: nullcontext()),
    )
    monkeypatch.setattr(
        observe,
        "_client",
        lambda: SimpleNamespace(start_as_current_observation=lambda **kwargs: BrokenObservation()),
    )
    with pytest.raises(ValueError, match="application failure"):
        with observe.job_trace("material-worker", job()):
            raise ValueError("application failure")
    with observe.job_trace("material-worker", job()):
        pass
