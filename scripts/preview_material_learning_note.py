"""Generate a real-model candidate to local files without changing a saved note."""

import argparse
import json
from pathlib import Path

from final_review.config import Settings
from final_review.material_note_polish_jobs import _process_polish_job
from final_review.material_text_source import source_units
from final_review.postgres import PostgresStore


def markdown(node):
    kind = node.get("type")
    if kind == "text":
        text = node["text"]
        for mark in node.get("marks", []):
            if mark["type"] == "bold":
                text = f"**{text}**"
            elif mark["type"] == "code":
                text = f"`{text}`"
        return text
    children = node.get("content", [])
    if kind == "heading":
        return "#" * node["attrs"]["level"] + " " + "".join(map(markdown, children)) + "\n\n"
    if kind == "codeBlock":
        return "```\n" + "".join(c["text"] for c in children) + "\n```\n\n"
    if kind in {"paragraph", "tableCell", "tableHeader"}:
        return "".join(map(markdown, children)).strip() + ("\n\n" if kind == "paragraph" else "")
    if kind == "table":
        rows = [[markdown(cell).replace("|", "\\|") for cell in row["content"]] for row in children]
        lines = [
            "| " + " | ".join(rows[0]) + " |",
            "| " + " | ".join("---" for _ in rows[0]) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
        return "\n".join(lines) + "\n\n"
    if kind in {"bulletList", "orderedList"}:
        return (
            "".join(
                (f"{i + 1}. " if kind == "orderedList" else "- ") + markdown(child).strip() + "\n"
                for i, child in enumerate(children)
            )
            + "\n"
        )
    return "".join(map(markdown, children))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-id", help="可选：对照另一已配置的文本模型，不修改默认配置")
    parser.add_argument("--model-name", help="可选：使用同一提供商已支持的模型，不修改默认配置")
    args = parser.parse_args()
    settings = Settings()
    if args.model_id:
        settings.material_note_polish_model_id = args.model_id
    if args.model_name:
        selected = settings.get_chat_model(settings.material_note_polish_model_id)
        replacement = selected.model_copy(update={"model": args.model_name})
        settings.chat_models = [replacement] + [
            m for m in settings.chat_models if m.id != selected.id
        ]
    store = PostgresStore(settings.database_url.get_secret_value())
    try:
        row = store.connection.execute(
            "SELECT data,user_id::text AS owner FROM documents WHERE document_id=%s",
            (args.document_id,),
        ).fetchone()
        if not row:
            raise ValueError("资料不存在")
        store.bind_user(row["owner"])
        document = row["data"]
        units = source_units(store.material_pages(document), document["content_sha256"])
    finally:
        store.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.output_dir / "checkpoint.json"
    # Preserve review diagnostics locally; no credentials are included in these payloads.
    from itertools import count

    from final_review import material_note_polish_jobs as worker

    original_builder = worker.build_polish_review_model
    sequence = count()

    class TracedModel:
        def __init__(self):
            self.model = original_builder(settings)

        def structured(self, schema, task, data):
            index = next(sequence)
            try:
                value = self.model.structured(schema, task, data)
            except Exception as exc:
                cause = exc.__cause__
                issues = (
                    cause.errors(include_input=False) if hasattr(cause, "errors") else str(cause)
                )
                (args.output_dir / f"model-{index:03d}-{schema.__name__}-error.json").write_text(
                    json.dumps(dict(issues=issues), ensure_ascii=False, indent=2), encoding="utf-8"
                )
                raise
            (args.output_dir / f"model-{index:03d}-{schema.__name__}.json").write_text(
                json.dumps(dict(input=data, output=value), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return value

    worker.build_polish_review_model = lambda _: TracedModel()

    class PreviewStore:
        failure = None
        published = False

        def polish_job_context(self, job):
            return dict(document=document, units=units)

        def update_polish_job(self, *args_, **kwargs):
            if kwargs.get("metrics", {}).get("outline_checkpoint"):
                checkpoint_path.write_text(
                    json.dumps(kwargs["metrics"], ensure_ascii=False), encoding="utf-8"
                )
            print(
                json.dumps({k: v for k, v in kwargs.items() if k != "metrics"}, ensure_ascii=True),
                flush=True,
            )
            return True

        def load_polish_batches(self, job):
            previous = args.output_dir / "batches.json"
            if not previous.is_file():
                return []
            rows = []
            for index, batch in enumerate(json.loads(previous.read_text(encoding="utf-8"))):
                path = args.output_dir / f"batch-{index:03d}.json"
                if path.is_file():
                    value = json.loads(path.read_text(encoding="utf-8"))
                    rows.append(
                        dict(
                            cache_key=batch["cache_key"],
                            status="done" if value.get("result") else "failed",
                            **value,
                        )
                    )
            return rows

        def prepare_polish_batches(self, job, batches):
            cached = {row["cache_key"]: row for row in self.load_polish_batches(job)}
            (args.output_dir / "batches.json").write_text(
                json.dumps(batches, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return [
                dict(
                    batch_index=i,
                    status=cached.get(b["cache_key"], {}).get("status", "pending"),
                    result=cached.get(b["cache_key"], {}).get("result"),
                    review=cached.get(b["cache_key"], {}).get("review"),
                )
                for i, b in enumerate(batches)
            ]

        def checkpoint_polish_batch(self, job, index, **kwargs):
            (args.output_dir / f"batch-{index:03d}.json").write_text(
                json.dumps(kwargs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True

        def publish_polished_note(self, job, body, metrics):
            (args.output_dir / "note.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (args.output_dir / "note.md").write_text(markdown(body), encoding="utf-8")
            (args.output_dir / "metrics.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.published = True
            return dict(published_as="local_candidate")

        def fail_polish_job(self, job, **kwargs):
            self.failure = kwargs
            (args.output_dir / "failure.json").write_text(
                json.dumps(kwargs, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    preview = PreviewStore()
    job = dict(
        user_id=row["owner"],
        document_id=document["document_id"],
        job_id="local-preview",
        attempts=1,
        phase_attempts=1,
        max_attempts=1,
        purpose="preview",
        generation=1,
        metrics=json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.is_file()
        else {},
    )
    _process_polish_job(preview, job, settings, None, {})
    if not preview.published:
        print(json.dumps(preview.failure, ensure_ascii=True), flush=True)
        return 1
    print("CANDIDATE_READY", str(args.output_dir.resolve()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
