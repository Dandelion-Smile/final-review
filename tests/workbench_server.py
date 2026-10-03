"""Disposable in-memory API for the browser workbench test."""

from threading import Thread

import uvicorn
from conftest import MemoryStore, ScriptedModel, TestEmbeddings

from final_review.agent import FinalReviewAgent
from final_review.api import create_app
from final_review.config import Settings
from final_review.material_jobs import process_material_job
from final_review.note_jobs import process_note_job
from final_review.rag import KnowledgeBase


def app():
    settings = Settings(_env_file=None, embedding_dimensions=3)
    store = MemoryStore()
    agent = FinalReviewAgent(
        store, KnowledgeBase(store, TestEmbeddings(), settings), ScriptedModel(), settings
    )
    application = create_app(settings, agent)

    def work():
        import time

        while True:
            job = store.claim_material_job()
            if job:
                try:
                    process_material_job(store, agent.kb, job,
                                         settings.max_upload_mb * 1024 * 1024)
                except KeyError:
                    pass  # /test/reset can discard a claimed test job.
            else:
                time.sleep(0.1)

    Thread(target=work, daemon=True).start()

    def note_work():
        import time

        while True:
            job = store.claim_note_job()
            if job:
                try:
                    process_note_job(store, agent, job)
                except KeyError:
                    pass  # /test/reset can discard a claimed test job.
            else:
                time.sleep(0.1)

    Thread(target=note_work, daemon=True).start()

    def reset():
        with store.lock:
            store.tables.clear()
            store.chunks.clear()
        return {"reset": True}

    application.add_api_route("/test/reset", reset, methods=["POST"])
    return application


if __name__ == "__main__":
    uvicorn.run(app(), host="127.0.0.1", port=8081)
