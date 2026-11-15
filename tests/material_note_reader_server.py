"""Local browser QA with real PostgreSQL, offline models and a delayed image queue.

Not part of production. Requires an explicitly named pptx_reader database.
"""

import argparse
from pathlib import Path
from threading import Event, Thread

import psycopg
import uvicorn
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from test_course_knowledge import KnowledgeEmbeddings, KnowledgeModel

from final_review import observe
from final_review.api import create_app
from final_review.auth import DatabaseAuth
from final_review.config import Settings
from final_review.knowledge_jobs import process_knowledge_job
from final_review.knowledge_store import KnowledgeRepository
from final_review.material_jobs import process_material_job
from final_review.migrations import apply_migrations
from final_review.postgres import PostgresStore
from final_review.rag import KnowledgeBase


def app(database_url):
    if not conninfo_to_dict(database_url).get("dbname", "").startswith("pptx_reader"):
        raise ValueError("Browser QA requires an isolated pptx_reader database")
    apply_migrations(database_url)
    observe._client = lambda: None
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        auth_mode="database",
        auth_cookie_secure=False,
        api_token="",
        llm_api_key="",
        embedding_api_key="",
        embedding_dimensions=3,
        material_pptx_parse_mode="text_images",
        material_note_edit_enabled=True,
        uploads_dir=str(Path("storage/step4-browser/uploads").resolve()),
        exports_dir=str(Path("storage/step4-browser/exports").resolve()),
    )
    auth = DatabaseAuth(settings)
    auth.setup()
    try:
        row = auth.store.connection.execute(
            "SELECT id FROM app_users WHERE email='step4@local.test'"
        ).fetchone()
        owner = str(row["id"]) if row else auth.sign_up("step4@local.test", "Step4LocalTest!")[0].id
    finally:
        auth.close()
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "INSERT INTO courses(record_key,user_id,course_id,data) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT(record_key) DO NOTHING",
            (
                "step4-reader",
                owner,
                "step4-reader",
                Jsonb(
                    dict(
                        course_id="step4-reader",
                        user_id=owner,
                        name="资料笔记验收",
                        status="active",
                        updated_at="2026-11-15T00:00:00+00:00",
                    )
                ),
            ),
        )
    application = create_app(settings)
    images_released = Event()

    def queue(phase):
        store = PostgresStore(database_url)
        kb = KnowledgeBase(store, KnowledgeEmbeddings(), settings)
        while True:
            if phase == "images" and not images_released.wait(0.1):
                continue
            job = store.claim_material_job(phase)
            if job:
                process_material_job(store, kb, job, settings.max_upload_mb * 1024 * 1024)
            else:
                Event().wait(0.1)

    def knowledge():
        store = PostgresStore(database_url)
        while True:
            job = KnowledgeRepository(store).claim()
            if job:
                process_knowledge_job(store, KnowledgeModel(), KnowledgeEmbeddings(), job, settings)
            else:
                Event().wait(0.1)

    for phase in ("preview", "parse", "images"):
        Thread(target=queue, args=(phase,), daemon=True).start()
    Thread(target=knowledge, daemon=True).start()

    @application.post("/test/images/release")
    def release_images():
        images_released.set()
        return dict(released=True)

    return application


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    uvicorn.run(app(args.database_url), host="127.0.0.1", port=8082)
