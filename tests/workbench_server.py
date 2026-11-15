"""Disposable in-memory API for the browser workbench test."""

from threading import Thread
from types import SimpleNamespace

import uvicorn
from conftest import MemoryStore, ScriptedModel, TestEmbeddings

from final_review.agent import FinalReviewAgent
from final_review.api import create_app
from final_review.config import ChatModelConfig, Settings
from final_review.export_jobs import process_export_job
from final_review.material_jobs import process_material_job
from final_review.note_jobs import process_note_job
from final_review.quiz_jobs import process_quiz_job
from final_review.rag import KnowledgeBase

COMPLETE_NOTE_REQUEST = "用 TCP 讲义生成一份笔记，整理成 12 分钟的考点清单"


def app():
    from tempfile import mkdtemp

    settings = Settings(_env_file=None, embedding_dimensions=3, material_vision_enabled=False)
    settings.exports_dir = mkdtemp(prefix="final_review_e2e_exports_")
    settings.chat_models = [
        ChatModelConfig(
            id=model_id,
            label=label,
            model=model_id,
            base_url="https://example.invalid/v1",
            api_key="test-key",
        )
        for model_id, label in [
            ("review", "Review"),
            ("deepseek", "DeepSeek"),
            ("gemini", "Gemini 3 Flash"),
        ]
    ]

    class TestCompletions:
        def create(self, **kwargs):
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("判断用户当前消息的意图"):
                import json

                message = json.loads(messages[-1]["content"])["message"]
                intent = (
                    "note"
                    if any(
                        term in message
                        for term in (
                            "生成笔记",
                            "生成一份笔记",
                            "可背诵的资料",
                            "考前总结手记",
                        )
                    )
                    or ("生成" in message and "笔记" in message)
                    else "ask"
                )
                content = '{"intent":"' + intent + '"}'
                if message == COMPLETE_NOTE_REQUEST:
                    content = json.dumps({"intent": "note", "chapter": "TCP"})
                if message == "帮我测测TCP掌握得怎么样，8道选择题和2道简答，基础难度":
                    content = json.dumps(
                        {
                            "intent": "quiz",
                            "quiz_mode": "draft",
                            "quiz_input": {
                                "chapter": "TCP",
                                "difficulty": "basic",
                                "blueprint": [
                                    {"question_type": "choice", "question_count": 8},
                                    {"question_type": "short_answer", "question_count": 2},
                                ],
                            },
                        }
                    )
            else:
                content = "可以继续聊天"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    class TestOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=TestCompletions())

    class WorkbenchModel(ScriptedModel):
        def quiz_draft(self, context, plan):
            draft = super().quiz_draft(context, plan)
            for question in draft["questions"]:
                if question["question_type"] == "fill_blank":
                    question.update(
                        stem=f"{question['stem']} 按顺序填空：协议____，握手次数____。",
                        accepted_answers=[],
                        blank_answers=[["TCP"], ["3", "三"]],
                        reference_answer="第 1 空 TCP；第 2 空 3",
                    )
            return draft

        def generate_quiz_variant(self, question):
            return {
                "id": "variant",
                "order": 1,
                "stem": "收到 SYN-ACK 后，客户端完成建立连接还需要哪些操作？",
                "knowledge_point": question["knowledge_point"],
                "question_type": "multiple_choice",
                "options": ["发送 ACK", "确认服务器序列号", "发送 FIN"],
                "correct_options": [0, 1],
                "score": question["score"],
                "reference_answer": "A、B",
                "explanation": "客户端确认服务器序列号。",
                "core_point": "序列号同步",
                "must_include": ["确认序列号"],
                "common_mistakes": ["发送 FIN"],
                "scoring_tips": "选出两项确认操作。",
                "provenance": "ai_supplement",
                "citations": [],
            }

        def note_request(self, request):
            config = super().note_request(request)
            if request["message"] == COMPLETE_NOTE_REQUEST:
                config.update(note_type="key_points", duration_minutes=12)
            return config

    import importlib

    importlib.import_module("final_review.api").OpenAI = TestOpenAI
    store = MemoryStore()
    agent = FinalReviewAgent(
        store, KnowledgeBase(store, TestEmbeddings(), settings), WorkbenchModel(), settings
    )
    application = create_app(settings, agent)

    def work():
        import time

        while True:
            job = store.claim_material_job()
            if job:
                try:
                    process_material_job(store, agent.kb, job, settings.max_upload_mb * 1024 * 1024)
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

    def quiz_work():
        import time

        while True:
            job = store.claim_quiz_job()
            if job:
                process_quiz_job(store, agent.model, settings, job)
            else:
                time.sleep(0.1)

    Thread(target=quiz_work, daemon=True).start()

    def export_work():
        import time

        while True:
            job = store.claim_export_job()
            if job:
                process_export_job(store, settings, job)
            else:
                time.sleep(0.1)

    Thread(target=export_work, daemon=True).start()

    def reset():
        with store.lock:
            store.tables.clear()
            store.chunks.clear()
        return {"reset": True}

    application.add_api_route("/test/reset", reset, methods=["POST"])
    return application


if __name__ == "__main__":
    uvicorn.run(app(), host="127.0.0.1", port=8081)
