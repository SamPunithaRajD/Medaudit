"""Central application settings, read from environment variables / .env via Pydantic."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Application
    app_name: str = "MedAudit AI"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_debug: bool = False
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+psycopg://medaudit:change-me@localhost:5432/medaudit"
    database_echo: bool = False
    database_pool_size: int = 5

    # Local AI
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout: float = 180.0
    ollama_max_retries: int = 2
    ollama_enabled: bool = True
    local_only_ai: bool = True
    external_llm_enabled: bool = False
    # Extra hostnames treated as "local" (e.g. a GPU box on the hospital LAN) when LOCAL_ONLY_AI=true.
    local_ai_allowed_hosts: str = ""

    # CORS / HTTP
    frontend_origins: str = Field(default="http://localhost:5173",
                                  validation_alias=AliasChoices("ALLOWED_ORIGINS", "FRONTEND_ORIGINS"))
    # Public URL of the web app, used to build activation / reset links shown to administrators.
    app_base_url: str = "http://localhost:5173"
    trusted_hosts: str = ""

    # Uploads / documents
    max_upload_size_mb: int = 100
    max_pdf_pages: int = 250
    max_zip_members: int = 300
    max_zip_uncompressed_mb: int = 1024
    max_zip_compression_ratio: float = 100.0
    max_image_pixels: int = 60_000_000
    storage_dir: str = str(BACKEND_ROOT / "storage")

    # Processing
    worker_mode: Literal["external", "embedded", "disabled"] = "external"
    worker_poll_interval: float = 2.0
    worker_concurrency: int = 1
    page_batch_size: int = 10
    page_max_retries: int = 2
    job_max_attempts: int = 3
    job_lock_timeout_seconds: int = 900
    ocr_render_dpi: int = 200
    ocr_providers: str = "pdf_text,tesseract"
    ocr_extra_provider: str = ""  # "package.module:ClassName" for handwriting-capable recognisers
    tesseract_cmd: str = ""
    tesseract_lang: str = "eng"
    ocr_page_timeout_seconds: int = 120
    ocr_low_confidence_threshold: float = 60.0
    agent_max_retries: int = 1
    agent_concurrency: int = 1
    llm_classification_enabled: bool = True
    llm_max_input_chars: int = 6000
    # Wall-clock times in case sheets are interpreted in this zone, then stored as UTC.
    hospital_timezone: str = "UTC"

    # Security
    encryption_key: str = ""  # base64 32-byte key (version v1) — or use ENCRYPTION_KEYS
    encryption_keys: str = ""  # "v1:base64key,v2:base64key"
    encryption_active_key_version: str = "v1"
    session_secret: str = ""
    audit_log_hmac_key: str = ""
    session_cookie_name: str = "medaudit_session"
    session_ttl_minutes: int = Field(default=480, validation_alias=AliasChoices("SESSION_DURATION", "SESSION_TTL_MINUTES"))
    # Initial default for the admin-editable activation code validity (hours).
    activation_token_expiry: int = Field(default=72, ge=1, le=720)
    session_idle_timeout_minutes: int = 60
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    password_min_length: int = 12
    login_rate_limit_per_minute: int = 10
    sensitive_rate_limit_per_minute: int = 30
    enforce_https: bool = False

    # Analytics / reporting
    analytics_min_cell_size: int = Field(default=5, ge=1)
    recommendation_due_days_critical: int = 7
    recommendation_due_days_high: int = 14
    recommendation_due_days_moderate: int = 30
    recommendation_due_days_low: int = 60

    @field_validator("database_url")
    @classmethod
    def _normalise_db(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = "postgresql+psycopg://" + v[len("postgres://"):]
        elif v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def is_production(self) -> bool:
        return self.app_env in ("production", "staging")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
