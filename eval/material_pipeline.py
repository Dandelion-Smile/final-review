"""Bounded, real-provider before/after evaluation, with Langfuse phase traces.

No changes to existing courses or material versions. Both modes receive identical
PPTX bytes. --pages creates a local evaluation subset, recorded in the report.
"""

import argparse
import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from pptx import Presentation

from final_review.config import Settings
from final_review.embeddings import build_embeddings
from final_review.material_conversion import convert_material
from final_review.material_hybrid import extract_preview, parse_pages, render_remaining
from final_review.observe import _client, flush, job_trace, observation
from final_review.rag import KnowledgeBase
from final_review.schemas import MaterialInput
from final_review.slide_understanding import SlideInterpreter, material_quality


def subset(content, selected):
    if not selected:
        return content
    presentation = Presentation(BytesIO(content))
    if min(selected) < 1 or max(selected) > len(presentation.slides):
        raise ValueError("指定页码超出资料范围")
    for index, element in reversed(list(enumerate(presentation.slides._sldIdLst, 1))):
        if index not in selected:
            presentation.part.drop_rel(element.rId)
            presentation.slides._sldIdLst.remove(element)
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def evaluate(content, mode, settings, run_id, include_images):
    document_id = f"benchmark-{run_id}-{mode}"
    job = dict(
        job_id=document_id,
        document_id=document_id,
        user_id="evaluation",
        course_id="material-pipeline-evaluation",
        phase=mode,
        attempts=1,
    )
    with TemporaryDirectory(prefix="material-eval-") as temporary:
        directory = Path(temporary)
        source = directory / "source.pptx"
        source.write_bytes(content)
        cache = directory / "analysis"
        started = monotonic()
        with job_trace("material-benchmark", job) as metrics:
            try:
                if mode == "baseline":
                    interpreter = SlideInterpreter(settings)
                    try:
                        converted = convert_material(
                            content,
                            "source.pptx",
                            50 * 1024 * 1024,
                            interpreter=interpreter,
                            cache_dir=cache,
                        )
                    finally:
                        interpreter.close()
                    metrics["basic_preview_seconds"] = None
                else:
                    pages = extract_preview(source, cache, sha256(content).hexdigest())
                    metrics["basic_preview_seconds"] = monotonic() - started
                    with observation("material.formal_parse"):
                        converted = parse_pages(
                            source,
                            cache,
                            sha256(content).hexdigest(),
                            pages,
                            lambda: SlideInterpreter(settings),
                            lambda page: None,
                            lambda stage: None,
                        )
                    indexed_text = "\n".join(x["text"] for x in converted.sections)
                    native_pages = [p for p in pages if p["route"] == "native"]
                    metrics["native_text_fidelity"] = all(
                        p["native_text"] in indexed_text
                        and (not p["notes"] or p["notes"] in indexed_text)
                        for p in native_pages
                    )
                metrics["content_ready_seconds"] = monotonic() - started
                embeddings = build_embeddings(settings)
                kb = KnowledgeBase(None, embeddings, settings)
                with observation("material.index"):
                    prepared, chunks = kb.prepare(
                        MaterialInput(
                            document_id=document_id,
                            course_id=job["course_id"],
                            title="对照验证",
                            source_type="teacher_ppt",
                            markdown=converted.markdown,
                        ),
                        sections=converted.sections,
                        plain_text=True,
                    )
                metrics["searchable_seconds"] = monotonic() - started
                metrics["chunks"] = len(chunks)
                metrics["quality"] = material_quality(converted.pages)
                if mode == "hybrid" and include_images:
                    render_remaining(
                        source, cache, sha256(content).hexdigest(), pages, lambda stage: None
                    )
                metrics["total_processing_seconds"] = monotonic() - started
                metrics["status"] = "succeeded"
                client = _client()
                if client is not None and mode == "hybrid":
                    client.create_score(
                        trace_id=metrics["trace_id"],
                        name="native_text_fidelity",
                        value=float(metrics["native_text_fidelity"]),
                    )
            except Exception as exc:
                metrics.update(
                    status="failed",
                    error=type(exc).__name__,
                    total_processing_seconds=monotonic() - started,
                )
            return dict(metrics)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--pages", help="对同一份资料抽取这些页对照，例如 6,13,14")
    parser.add_argument("--baseline", action="store_true", help="同时执行原全页视觉流程")
    parser.add_argument("--include-images", action="store_true", help="计入后台原页生成时间")
    parser.add_argument("--output", type=Path, default=Path("storage/material-pipeline-eval.json"))
    args = parser.parse_args()
    settings = Settings()
    content = args.file.read_bytes()
    original_hash = sha256(content).hexdigest()
    pages = sorted(set(int(x) for x in args.pages.split(","))) if args.pages else None
    content = subset(content, pages)
    client = _client()
    if client is not None and not client.auth_check():
        raise RuntimeError("Langfuse 项目鉴权失败")
    run_id = uuid4().hex[:12]
    report = dict(
        run_id=run_id,
        original_sha256=original_hash,
        input_sha256=sha256(content).hexdigest(),
        selected_pages=pages,
        scope="实际模型及向量请求；不发布资料、不构建课程知识库",
        results={},
    )
    try:
        for mode in ["baseline", "hybrid"] if args.baseline else ["hybrid"]:
            report["results"][mode] = evaluate(content, mode, settings, run_id, args.include_images)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(mode, json.dumps(report["results"][mode], ensure_ascii=False), flush=True)
    finally:
        flush()
    if any(r["status"] != "succeeded" for r in report["results"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
