"""Disposable in-memory API for the browser workbench test."""

import uvicorn
from conftest import MemoryStore, ScriptedModel, TestEmbeddings

from final_review.agent import FinalReviewAgent
from final_review.api import create_app
from final_review.config import Settings
from final_review.rag import KnowledgeBase


def app():
    settings = Settings(_env_file=None, embedding_dimensions=3)
    store = MemoryStore()
    agent = FinalReviewAgent(
        store, KnowledgeBase(store, TestEmbeddings(), settings), ScriptedModel(), settings
    )
    return create_app(settings, agent)


if __name__ == "__main__":
    uvicorn.run(app(), host="127.0.0.1", port=8081)
