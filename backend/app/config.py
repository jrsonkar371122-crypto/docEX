"""Application configuration loaded from environment / .env via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central, typed configuration. Every value is overridable via .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- PostgreSQL ----
    postgres_db: str = "documind"
    postgres_user: str = "documind"
    postgres_password: str = "changeme"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # ---- Redis ----
    redis_url: str = "redis://redis:6379/0"

    # ---- Auth / JWT ----
    secret_key: str = "change-this-to-a-random-64-char-string"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # ---- Ollama ----
    ollama_host: str = "http://ollama:11434"

    # ---- Chat / text LLM ----
    llm_model: str = "gemma3:27b"
    llm_fallback_model: str = "gemma3:12b"
    llm_ctx_tokens: int = 32768
    llm_max_new_tokens: int = 2048

    # ---- Vision model (VLM) — fully generic, decoupled from the chat LLM ----
    vlm_enabled: bool = True
    vlm_model: str = "llava:13b"
    vlm_max_new_tokens: int = 1024

    # ---- Embeddings ----
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    # ---- Storage ----
    upload_dir: str = "/app/uploads"
    image_dir: str = "/app/images"
    max_upload_mb: int = 100

    # ---- CORS ----
    allowed_origin: str = "http://localhost"

    # ---- Celery ----
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # ---- Retrieval / generation tuning ----
    retrieval_vector_topk: int = 20
    retrieval_final_topk: int = 5
    mmr_lambda: float = 0.5
    history_turns: int = 10
    rag_temperature: float = 0.1
    title_temperature: float = 0.3

    # ---- Rate limiting ----
    login_max_attempts: int = 10
    login_window_seconds: int = 15 * 60

    max_upload_bytes: int = Field(default=0, exclude=True)

    @computed_field  # type: ignore[misc]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy DSN (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def sync_database_url(self) -> str:
        """Sync DSN (psycopg) used by Alembic migrations and Celery workers."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
