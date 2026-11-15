"""Isolated acceptance: real PPT/PDF/rendering, offline embeddings, no model requests.

Never reads DATABASE_URL. Supply a disposable database whose name starts with
pptx_acceptance; no schema or database is deleted by this script.
"""

import argparse
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

import psycopg
from langchain_core.embeddings import Embeddings
from PIL import Image
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from final_review import observe
from final_review.config import Settings
from final_review.material_jobs import process_material_job
from final_review.material_notes import STRATEGY, initial_body, semantic_input
from final_review.material_structure import inspect_presentation
from final_review.material_text_images import classify_pages
from final_review.material_text_images_worker import image_cache_dir
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore
from final_review.rag import KnowledgeBase


class OfflineEmbeddings(Embeddings):
    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return [[1.0, 0.1, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.1, 0.0]


def evaluate(args):
    if not conninfo_to_dict(args.database_url).get("dbname", "").startswith("pptx_acceptance"):
        raise ValueError("acceptance requires a dedicated pptx_acceptance database")
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "storage/material-classification-check.json").read_text())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(
        scope="real native extraction, isolated SQL and real page rendering; offline AI",
        daily_database_changed=False,
        provider_embedding_requests=0,
        knowledge_model_requests=0,
        vision_requests=0,
        ocr_calls=0,
        samples=[],
    )
    for item in manifest:
        source = Path(item["source_file"])
        if not source.is_file():
            report["samples"].append(dict(sample=item["sample"], status="missing"))
            continue
        content = source.read_bytes()
        started = monotonic()
        pages = classify_pages(inspect_presentation(content))
        body = initial_body(pages, sha256(content).hexdigest())
        units = semantic_input(body, searchable_only=True)
        report["samples"].append(
            dict(
                sample=item["sample"],
                sha256=sha256(content).hexdigest(),
                total_pages=len(pages),
                native_seconds=round(monotonic() - started, 4),
                reliable_blocks=len(units),
                excluded_blocks=sum(not b["searchable"] for p in pages for b in p["blocks"]),
                image_targets=[p["position"] for p in pages if p["needs_page_image"]],
                status="native_checked",
            )
        )
    source = args.file or Path(manifest[-1]["source_file"])
    content = source.read_bytes()
    run_id = uuid4().hex[:10]
    directory = output / run_id
    directory.mkdir()
    path = directory / "source.pptx"
    path.write_bytes(content)
    apply_migrations(args.database_url)
    owner = str(uuid4())
    course_id = "acceptance-" + run_id
    document_id = "material-" + run_id
    with psycopg.connect(args.database_url) as connection:
        connection.execute(
            "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,'offline')",
            (owner, f"{run_id}@acceptance.test"),
        )
        connection.execute(
            "INSERT INTO courses(record_key,user_id,course_id,data) VALUES (%s,%s,%s,%s)",
            (
                course_id,
                owner,
                course_id,
                Jsonb(dict(course_id=course_id, user_id=owner, status="active", name="隔离验收")),
            ),
        )
    store = PostgresStore(args.database_url)
    store.bind_user(owner)
    settings = Settings(
        _env_file=None,
        database_url=args.database_url,
        embedding_dimensions=3,
        llm_api_key="",
        embedding_api_key="",
        material_pptx_parse_mode="text_images",
    )
    embeddings = OfflineEmbeddings()
    kb = KnowledgeBase(store, embeddings, settings)
    document = dict(
        document_id=document_id,
        course_id=course_id,
        user_id=owner,
        title="真实课件隔离验收",
        source_type="teacher_ppt",
        source_origin="user_upload",
        file_name=source.name,
        file_path=str(path),
        parse_strategy=STRATEGY,
        content_sha256=sha256(content).hexdigest(),
        parse_status="queued",
        preview_status="queued",
        chunk_count=0,
        uploaded_at=datetime.now(UTC).isoformat(),
    )
    store.create_material_job(
        document,
        dict(
            job_id=uuid4().hex,
            document_id=document_id,
            course_id=course_id,
            phase="preview",
            fingerprint=run_id,
            idempotency_key=None,
        ),
    )
    timings = {}
    try:
        for phase in ("preview", "parse", "images"):
            job = store.claim_material_job(phase)
            if not job:
                continue
            started = monotonic()
            process_material_job(store, kb, job, 50 * 1024 * 1024)
            timings[phase] = round(monotonic() - started, 4)
            state = store.get_material_job(job["job_id"])
            if state["status"] != "succeeded":
                raise RuntimeError(f"{phase}: {state.get('error_message')}")
        current = store.get("document", document_id)
        pages = store.material_pages(current)
        images = []
        cache = image_cache_dir(path, document["content_sha256"])
        for page in pages:
            if not page["needs_page_image"]:
                continue
            image = cache / f"slide-{page['position']:03d}.png"
            with Image.open(image) as bitmap:
                bitmap.verify()
            images.append(dict(page=page["position"], path=str(image), status=page["image_status"]))
        report["full_pipeline"] = dict(
            source_sha256=document["content_sha256"],
            document_id=document_id,
            total_pages=len(pages),
            images=images,
            phase_seconds=timings,
            parse_status=current["parse_status"],
            images_status=current["images_status"],
            indexed_chunks=len(store.list_material_chunks(document_id)),
            note_revision_id=store.note_head(course_id, document_id)["indexed_revision_id"],
            offline_embedding_batches=embeddings.calls,
            queued_knowledge_jobs=store.connection.execute(
                "SELECT count(*) AS n FROM knowledge_build_jobs WHERE document_id=%s",
                (document_id,),
            ).fetchone()["n"],
        )
        target = output / "report.json"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                dict(
                    report=str(target),
                    phases=timings,
                    images=len(images),
                    chunks=report["full_pipeline"]["indexed_chunks"],
                )
            )
        )
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, default=Path("storage/pptx-ti1-acceptance"))
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()

    def forbidden(*args, **kwargs):
        raise AssertionError("forbidden visual/OCR/legacy call during acceptance")

    with (
        patch.object(observe, "_client", lambda: None),
        patch("final_review.material_jobs.SlideInterpreter", forbidden),
        patch("final_review.slide_understanding.SlideInterpreter", forbidden),
        patch("final_review.material_conversion._ocr_image", forbidden),
        patch("final_review.material_jobs.convert_material", forbidden),
    ):
        evaluate(args)


if __name__ == "__main__":
    main()
