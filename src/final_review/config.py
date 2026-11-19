from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatModelConfig(BaseModel):
    """A browser-chat provider configuration, kept entirely on the server."""

    id: str = Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    label: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: SecretStr
    grounded: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    xpeach_api_key: SecretStr = SecretStr("")
    xpeach_base_url: str = "https://fast.xpeach.codes/v1"
    xpeach_chat_model: str = ""
    xpeach_chat_label: str = ""
    chat_models: list[ChatModelConfig] = Field(default_factory=list)
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1, le=16384)
    surreal_url: str = "http://127.0.0.1:8000"
    surreal_namespace: str = "final_review"
    surreal_database: str = "review"
    surreal_username: str = "root"
    surreal_password: SecretStr = SecretStr("change-me")
    # Local, self-managed PostgreSQL. It contains both business data and auth.
    database_url: SecretStr = SecretStr("")
    auth_mode: str = "database"
    auth_cookie_name: str = "final_review_access"
    auth_cookie_secure: bool = True
    auth_cookie_domain: str = ""
    auth_session_days: int = Field(default=30, ge=1, le=365)
    auth_email_code_secret: SecretStr = SecretStr("")
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: str = "starttls"
    smtp_sender: str = ""
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    api_token: SecretStr = SecretStr("")
    uploads_dir: str = "uploads"
    exports_dir: str = "exports"
    export_pandoc_executable: str = ""
    export_chromium_executable: str = ""
    poppler_executable: str = ""
    libreoffice_executable: str = ""
    tesseract_executable: str = ""
    tessdata_prefix: str = ""
    material_vision_enabled: bool = False
    material_vision_model_id: str = "gemini-3-flash"
    material_vision_model: str = ""
    material_vision_timeout: float = Field(default=90, gt=0, le=300)
    material_vision_repairs: int = Field(default=1, ge=0, le=2)
    material_vision_concurrency: int = Field(default=4, ge=1, le=4)
    material_file_concurrency: int = Field(default=5, ge=1, le=5)
    material_hybrid_enabled: bool = True
    material_pptx_parse_mode: Literal["hybrid_visual", "text_images", "text_only_polished"] = (
        "hybrid_visual"
    )
    # Cap for a saved text-polish-v1 task only. Keep in sync with
    # material_conversion.MATERIAL_TEXT_MAX_SLIDES_MIN/MAX. Visual paths use
    # MAX_PRESENTATION_SLIDES (80) and do not read this value.
    material_text_max_slides: int = Field(default=120, ge=1, le=500)
    material_note_edit_enabled: bool = False
    material_preview_concurrency: int = Field(default=2, ge=1, le=4)
    material_image_concurrency: int = Field(default=1, ge=1, le=2)
    material_conversion_concurrency: int = Field(default=2, ge=1, le=5)
    material_vision_global_concurrency: int = Field(default=4, ge=1, le=16)
    embedding_request_concurrency: int = Field(default=2, ge=1, le=8)
    knowledge_task_concurrency: int = Field(default=2, ge=1, le=4)
    knowledge_model_id: str = "default"
    # Standalone catalog process: opt in only after migration/environment review.
    catalog_scheduler_enabled: bool = False
    catalog_scan_interval_seconds: int = Field(default=10, ge=1, le=3600)
    catalog_reconcile_interval_seconds: int = Field(default=60, ge=1, le=86400)
    catalog_quiet_window_seconds: int = Field(default=60, ge=1, le=3600)
    catalog_max_wait_seconds: int = Field(default=300, ge=1, le=86400)
    catalog_max_concurrency: int = Field(default=1, ge=1, le=4)
    catalog_page_size: int = Field(default=100, ge=1, le=1000)
    catalog_model_id: str = "default"
    knowledge_model_timeout: float = Field(default=180, gt=0, le=600)
    knowledge_max_output_tokens: int = Field(default=8192, ge=2048, le=32768)
    knowledge_extractor_version: str = "course-knowledge-v1"
    knowledge_block_chars: int = Field(default=10000, ge=2000, le=30000)
    knowledge_block_pages: int = Field(default=4, ge=1, le=20)
    knowledge_top_k: int = Field(default=5, ge=1, le=20)
    knowledge_min_similarity: float = Field(default=0.65, ge=-1, le=1)
    # Text-polish strategy: one text model turns the immutable native-text snapshot
    # into a continuous, editable reading note. Never uses the vision/OCR stack.
    material_note_polish_model_id: str = "default"
    material_note_polish_model: str = ""
    material_note_polish_timeout: float = Field(default=180, gt=0, le=600)
    material_note_polish_max_output_tokens: int = Field(default=16384, ge=2048, le=65536)
    material_note_polish_concurrency: int = Field(default=3, ge=1, le=6)
    material_note_polish_batch_blocks: int = Field(default=24, ge=6, le=60)
    material_note_polish_prompt_version: str = "material-learning-v2"
    material_note_polish_review_mode: Literal["always", "on_check_failed"] = "always"
    material_note_polish_max_attempts: int = Field(default=3, ge=1, le=5)
    max_upload_mb: int = Field(default=10, ge=1, le=50)
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_candidates: int = Field(default=20, ge=1, le=100)
    min_similarity: float = Field(default=0.25, ge=-1, le=1)
    max_repairs: int = Field(default=1, ge=0, le=3)
    model_timeout: float = Field(default=60, gt=0)
    note_model_timeout: float = Field(default=120, gt=0)
    note_model_max_retries: int = Field(default=1, ge=0, le=3)
    fast_quiz_timeout: float = Field(default=30, gt=0, le=90)
    quiz_model_timeout: float = Field(default=120, gt=0, le=300)
    quiz_max_output_tokens: int = Field(default=16384, ge=2048, le=65536)
    quiz_choice_batch_size: int = Field(default=5, ge=1, le=6)
    quiz_written_batch_size: int = Field(default=2, ge=1, le=3)
    quiz_max_repairs: int = Field(default=2, ge=0, le=2)
    quiz_max_plan_changes: int = Field(default=3, ge=0, le=3)
    quiz_max_question_retries: int = Field(default=3, ge=0, le=3)

    @model_validator(mode="after")
    def check_candidates(self):
        if self.retrieval_candidates < self.top_k:
            raise ValueError("RETRIEVAL_CANDIDATES 必须 >= TOP_K")
        return self

    @field_validator("material_text_max_slides", mode="before")
    @classmethod
    def material_text_max_slides_is_int(cls, value):
        # bool is an int subclass; reject it before Pydantic coerces True to 1.
        if isinstance(value, bool):
            raise ValueError("MATERIAL_TEXT_MAX_SLIDES 必须是 1 到 500 的整数")
        return value

    @field_validator("chat_models")
    @classmethod
    def unique_chat_model_ids(cls, models: list[ChatModelConfig]):
        if len({model.id for model in models}) != len(models):
            raise ValueError("CHAT_MODELS 中的 id 不能重复")
        return models

    def available_chat_models(self) -> list[ChatModelConfig]:
        """Return only configured providers; secret values never leave this process."""
        if self.chat_models:
            models = self.chat_models
            xpeach_model_ids = {
                "codex-auto-review",
                "glm-5.3",
                "glm-5.3-flash",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-6-astra",
            }
            if (
                self.xpeach_api_key.get_secret_value()
                and self.xpeach_chat_model
                and any(model.id in xpeach_model_ids for model in models)
            ):
                models = [model for model in models if model.id not in xpeach_model_ids]
                models.append(
                    ChatModelConfig(
                        id=self.xpeach_chat_model,
                        label=self.xpeach_chat_label or self.xpeach_chat_model,
                        model=self.xpeach_chat_model,
                        base_url=self.xpeach_base_url,
                        api_key=self.xpeach_api_key,
                        grounded=False,
                    )
                )
            return [model for model in models if model.api_key.get_secret_value()]

        models = []
        if self.deepseek_api_key.get_secret_value():
            models.append(
                ChatModelConfig(
                    id="deepseek",
                    label="DeepSeek",
                    model=self.deepseek_model,
                    base_url=self.deepseek_base_url,
                    api_key=self.deepseek_api_key,
                )
            )
        if self.llm_api_key.get_secret_value():
            models.append(
                ChatModelConfig(
                    id="default",
                    label=self.llm_model,
                    model=self.llm_model,
                    base_url=self.llm_base_url,
                    api_key=self.llm_api_key,
                )
            )
        if self.xpeach_api_key.get_secret_value() and self.xpeach_chat_model:
            models.append(
                ChatModelConfig(
                    id=self.xpeach_chat_model,
                    label=self.xpeach_chat_label or self.xpeach_chat_model,
                    model=self.xpeach_chat_model,
                    base_url=self.xpeach_base_url,
                    api_key=self.xpeach_api_key,
                    grounded=False,
                )
            )
        return models

    def get_chat_model(self, model_id: str | None) -> ChatModelConfig:
        models = self.available_chat_models()
        if not models:
            raise ValueError("尚未配置可用聊天模型")
        if model_id is None:
            return models[0]
        for model in models:
            if model.id == model_id:
                return model
        raise ValueError("所选模型不可用")
