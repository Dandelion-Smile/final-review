"""Real PPT -> parsing/OCR -> LLM -> incremental knowledge proof in an isolated DB.

Run in the application image (LibreOffice/Poppler/Tesseract required). Never targets
the application's DATABASE_URL database and never deletes an existing database.
"""

import argparse
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from pptx import Presentation
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from final_review.config import Settings
from final_review.knowledge_jobs import process_knowledge_job
from final_review.knowledge_store import KnowledgeRepository
from final_review.llm import build_models, build_review_model
from final_review.material_jobs import process_material_job
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore
from final_review.rag import KnowledgeBase


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-name", default="final_review_knowledge_live")
    parser.add_argument("--database-host")
    parser.add_argument("--report", default="exports/course-knowledge-live-report.json")
    args = parser.parse_args()
    load_dotenv(override=True)
    if os.name != "nt":
        os.environ.update(
            POPPLER_EXECUTABLE="/usr/bin/pdftoppm",
            LIBREOFFICE_EXECUTABLE="/usr/bin/libreoffice",
            TESSERACT_EXECUTABLE="/usr/bin/tesseract",
            TESSDATA_PREFIX="",
        )
    settings = Settings()
    connection_args = conninfo_to_dict(settings.database_url.get_secret_value())
    if (
        not args.database_name.startswith("final_review_knowledge_live")
        or connection_args["dbname"] == args.database_name
    ):
        raise ValueError("只能使用独立的 final_review_knowledge_live* 测试数据库")
    if args.database_host:
        connection_args["host"] = args.database_host
    with psycopg.connect(make_conninfo(**connection_args), autocommit=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (args.database_name,)
        ).fetchone():
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.database_name))
            )
    connection_args["dbname"] = args.database_name
    settings = settings.model_copy(
        update={
            "database_url": settings.database_url.__class__(make_conninfo(**connection_args)),
            "material_vision_enabled": False,
        }
    )
    database_url = settings.database_url.get_secret_value()
    apply_migrations(database_url)
    model_config = next(
        c for c in settings.available_chat_models() if c.id == settings.knowledge_model_id
    )
    model = build_review_model(model_config, settings, knowledge_generation=True)
    _, embeddings = build_models(settings)
    store = PostgresStore(database_url)
    owner, course = str(uuid4()), f"live-{uuid4().hex[:10]}"
    store.bind_user(owner)
    store.knowledge_settings = settings
    store.connection.execute(
        "INSERT INTO app_users(id,email,password_hash) VALUES(%s,%s,'test')",
        (owner, f"{owner}@example.test"),
    )
    store.put(
        "course", course, dict(course_id=course, user_id=owner, name="知识库实测", status="active")
    )
    kb = KnowledgeBase(store, embeddings, settings)
    results = []

    def counts():
        return {
            table: store.connection.execute(
                sql.SQL("SELECT count(*) AS n FROM {} WHERE user_id=%s").format(
                    sql.Identifier(table)
                ),
                (owner,),
            ).fetchone()["n"]
            for table in ("knowledge_points", "knowledge_facts", "fact_evidence")
        }

    with TemporaryDirectory(prefix="knowledge-live-") as directory:

        def upload(filename, lines):
            presentation = Presentation()
            slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            slide.shapes.title.text = "HTTP无状态性"
            slide.placeholders[1].text = "\n".join(lines)
            path = Path(directory) / filename
            presentation.save(path)
            raw = path.read_bytes()
            content_hash = sha256(raw).hexdigest()
            duplicate = store.find_duplicate_material(course, filename, content_hash)
            if duplicate:
                return duplicate["document"]["document_id"], True
            document_id, job_id = uuid4().hex, uuid4().hex
            document = dict(
                document_id=document_id,
                course_id=course,
                user_id=owner,
                title=filename,
                source_type="teacher_ppt",
                source_origin="user_upload",
                chapter="HTTP",
                file_name=filename,
                content_sha256=content_hash,
                file_size=len(raw),
                file_path=str(path),
                parse_status="queued",
                chunk_count=0,
                uploaded_at=datetime.now(UTC).isoformat(),
            )
            store.create_material_job(
                document,
                dict(
                    job_id=job_id,
                    document_id=document_id,
                    course_id=course,
                    idempotency_key=job_id,
                    fingerprint=content_hash,
                ),
            )
            job = store.claim_material_job()
            process_material_job(store, kb, job, 10 * 1024 * 1024)
            parsed = store.get_material_job(job_id)
            if parsed["status"] != "succeeded":
                raise RuntimeError(f"PPT解析失败: {parsed.get('error_message')}")
            build_id = store.connection.execute(
                "SELECT job_id FROM knowledge_build_jobs WHERE user_id=%s AND document_id=%s",
                (owner, document_id),
            ).fetchone()["job_id"]
            build = KnowledgeRepository(store).claim(build_id)
            if not process_knowledge_job(store, model, embeddings, build, settings):
                raise RuntimeError("真实模型知识库构建失败，请检查任务状态")
            candidates = KnowledgeRepository(store).candidates(build)
            results.append(
                dict(
                    file=filename,
                    job_id=str(build["job_id"]),
                    counts=counts(),
                    candidates=[
                        dict(
                            name=c["name"],
                            status=c["status"],
                            matched_point_id=str(c["matched_point_id"]),
                        )
                        for c in candidates
                    ],
                )
            )
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
            return document_id, False

        upload(
            "HTTP基础.pptx",
            [
                "知识点：HTTP无状态性。",
                "HTTP协议不会自动保留两次请求之间的状态。",
                "每个HTTP请求都应独立包含服务器理解该请求所需的信息。",
            ],
        )
        upload(
            "HTTP补充.pptx",
            [
                "知识点：HTTP无状态性。",
                "HTTP不会自动记住前后两次请求的状态。",
                "每个请求需要独立提供服务器处理它所必需的信息。",
                "应用可以通过Cookie和Session在HTTP之上维护会话状态。",
            ],
        )
        assert any(c["status"] == "merged" for c in results[1]["candidates"])
        assert results[1]["counts"]["knowledge_facts"] > results[0]["counts"]["knowledge_facts"]
        assert results[1]["counts"]["fact_evidence"] > results[0]["counts"]["fact_evidence"]
        report = dict(
            model=model_config.model,
            embedding_model=settings.embedding_model,
            generated_at=datetime.now(UTC).isoformat(),
            results=results,
        )
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: real PPT parsing, semantic reuse, incremental facts and multiple evidence")
    store.close()


if __name__ == "__main__":
    main()
