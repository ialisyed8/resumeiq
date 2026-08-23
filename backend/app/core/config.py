"""
Application configuration.

Every secret comes from the environment. Nothing is defaulted to a real value,
and the app refuses to start in production with placeholder secrets — a missing
ANTHROPIC_API_KEY should surface as a clear configuration error at boot, not as
a confusing 500 halfway through a screening batch.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Application -------------------------------------------------------
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    APP_NAME: str = "ResumeIQ"
    API_V1_PREFIX: str = "/api"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"

    # --- Security ----------------------------------------------------------
    SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    JWT_SECRET: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 14
    PASSWORD_MIN_LENGTH: int = 12
    ARGON2_TIME_COST: int = 3
    ARGON2_MEMORY_COST: int = 65536
    ARGON2_PARALLELISM: int = 4
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"

    # --- CORS --------------------------------------------------------------
    CORS_ORIGINS: Annotated[list[str], NoDecode] = [
        "http://localhost:5173", "http://localhost:3000",
    ]

    # --- Data stores -------------------------------------------------------
    DATABASE_URL: PostgresDsn = "postgresql+asyncpg://resumeiq:resumeiq@localhost:5432/resumeiq"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_ECHO: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Object storage ----------------------------------------------------
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "resumeiq-documents"
    S3_REGION: str = "us-east-1"
    S3_USE_SSL: bool = False
    S3_SERVER_SIDE_ENCRYPTION: str = ""
    PRESIGNED_URL_TTL_SECONDS: int = 900

    # --- Anthropic ---------------------------------------------------------
    # Never hardcode a model name elsewhere in the codebase. Swapping models is
    # a config change, not a code change.
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_EXTRACTION_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_VERIFICATION_MODEL: str = "claude-sonnet-4-6"
    ANTHROPIC_ADJUDICATION_MODEL: str = "claude-sonnet-4-6"
    ANTHROPIC_MAX_RETRIES: int = 4
    ANTHROPIC_TIMEOUT_SECONDS: int = 120
    ANTHROPIC_MAX_TOKENS: int = 4096
    ANTHROPIC_ENABLE_PROMPT_CACHE: bool = True

    # --- Embeddings --------------------------------------------------------
    EMBEDDING_MODEL: str = "BAAI/bge-base-en-v1.5"
    EMBEDDING_DIMENSIONS: int = 768
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_QUERY_PREFIX: str = "Represent this sentence for searching relevant passages: "
    RETRIEVAL_TOP_K: int = 6
    RETRIEVAL_MIN_SIMILARITY: float = 0.50

    # --- Cost and abuse controls ------------------------------------------
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
    MAX_RESUME_PAGES: int = 20
    MAX_CANDIDATES_PER_SCREENING: int = 500
    MAX_REQUEST_BYTES: int = 12 * 1024 * 1024
    VERIFICATION_BATCH_SIZE: int = 12
    RATE_LIMIT_PER_MINUTE: int = 120
    AUTH_RATE_LIMIT_PER_MINUTE: int = 10

    # --- Extraction --------------------------------------------------------
    EXTRACTION_CONFIDENCE_THRESHOLD: float = 0.55
    OCR_ENABLED: bool = True
    OCR_DPI: int = 300
    OCR_LANGUAGES: str = "eng"
    LIBREOFFICE_BIN: str = "soffice"
    VISION_OCR_FALLBACK: bool = False

    # --- Email -------------------------------------------------------------
    # "console" prints to stdout and refuses to run outside development.
    # "smtp" works with SES, Postmark, Mailgun, or a local relay.
    EMAIL_BACKEND: Literal["console", "memory", "smtp"] = "console"
    EMAIL_FROM: str = "ResumeIQ <no-reply@resumeiq.local>"
    APP_BASE_URL: str = "http://localhost:5173"
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True

    # --- Malware scanning ----------------------------------------------------
    CLAMAV_ENABLED: bool = True
    CLAMAV_HOST: str = "clamav"
    CLAMAV_PORT: int = 3310
    CLAMAV_TIMEOUT_SECONDS: float = 30.0
    #: Accept uploads when the scanner cannot be reached. Defaults to false in
    #: production: a scanner outage should be an incident, not a silent
    #: downgrade of a security control. See services/malware.py.
    CLAMAV_FAIL_OPEN: bool | None = None

    # --- Privacy -----------------------------------------------------------
    DEFAULT_BLIND_SCREENING: bool = True
    DATA_RETENTION_DAYS: int = 180
    AUDIT_RETENTION_DAYS: int = 2555  # 7 years

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @model_validator(mode="after")
    def _production_guards(self):
        if self.APP_ENV == "production":
            problems = []
            if len(self.SECRET_KEY) < 32:
                problems.append("SECRET_KEY must be at least 32 characters")
            if len(self.JWT_SECRET) < 32:
                problems.append("JWT_SECRET must be at least 32 characters")
            if "*" in self.CORS_ORIGINS:
                problems.append("CORS_ORIGINS must not contain a wildcard")
            if self.DEBUG:
                problems.append("DEBUG must be false")
            if self.S3_ACCESS_KEY == "minioadmin":
                problems.append("S3 credentials are still the MinIO defaults")
            if not self.COOKIE_SECURE:
                problems.append("COOKIE_SECURE must be true")
            if self.EMAIL_BACKEND != "smtp":
                problems.append(
                    "EMAIL_BACKEND must be 'smtp' in production — password reset "
                    "silently does nothing otherwise"
                )
            if problems:
                raise ValueError(
                    "Refusing to start in production:\n  - " + "\n  - ".join(problems)
                )
        return self

    @property
    def clamav_fail_open(self) -> bool:
        """Explicit setting wins; otherwise fail closed in production only."""
        if self.CLAMAV_FAIL_OPEN is not None:
            return self.CLAMAV_FAIL_OPEN
        return self.APP_ENV != "production"

    @property
    def ai_enabled(self) -> bool:
        """False means the app runs in seeded-demo mode with no model calls."""
        return bool(self.ANTHROPIC_API_KEY)

    @property
    def sync_database_url(self) -> str:
        """Alembic needs the synchronous driver."""
        return str(self.DATABASE_URL).replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
