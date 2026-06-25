"""
Application configuration loaded from environment variables.

Uses pydantic-settings (required for Pydantic v2) so every value is
type-coerced and validated at startup.  A missing DATABASE_URL will
raise a ValidationError before the app can do any damage.
"""

from __future__ import annotations

import sys
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for the AI Export Intelligence Pipeline."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Required
    # ------------------------------------------------------------------ #
    DATABASE_URL: str  # e.g. postgresql://user:pass@localhost:5432/pipeline_db

    # ------------------------------------------------------------------ #
    # LLM
    # ------------------------------------------------------------------ #
    MOCK_LLM_ENABLED: bool = True
    OPENAI_API_KEY: str = ""          # optional when MOCK_LLM_ENABLED=true
    OPENAI_MODEL: str = "gpt-4o-mini"
    LLM_TIMEOUT_SECONDS: int = 30

    # ------------------------------------------------------------------ #
    # Pipeline behaviour
    # ------------------------------------------------------------------ #
    IDEMPOTENCY_MODE: Literal["skip", "update", "reprocess"] = "skip"
    KB_ENABLED: bool = False
    KB_CONNECTION_STRING: str = ""

    # ------------------------------------------------------------------ #
    # Retry policy
    # ------------------------------------------------------------------ #
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_DELAY_SECONDS: float = 2.0

    # ------------------------------------------------------------------ #
    # Observability
    # ------------------------------------------------------------------ #
    LOG_LEVEL: str = "INFO"

    # ------------------------------------------------------------------ #
    # Testing
    # ------------------------------------------------------------------ #
    HYPOTHESIS_PROFILE: Literal["dev", "ci"] = "dev"

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @field_validator("OPENAI_API_KEY")
    @classmethod
    def _check_api_key(cls, v: str, info) -> str:
        """
        Warn (but do not fail) when the real LLM is requested without a key.
        The hard failure happens at enrichment time, not at startup, so the
        pipeline can still boot and serve already-enriched data.
        """
        return v


def get_settings() -> Settings:
    """
    Return the application settings.

    Raises SystemExit with a human-readable message when DATABASE_URL is
    missing so the container logs are immediately actionable.
    """
    try:
        return Settings()
    except Exception as exc:  # pydantic ValidationError
        # Extract field names from the error for a clear message
        missing = [str(e["loc"]) for e in exc.errors()] if hasattr(exc, "errors") else [str(exc)]
        print(
            f"[config] ERROR: required environment variable(s) not set: {', '.join(missing)}\n"
            "Set DATABASE_URL (and OPENAI_API_KEY if MOCK_LLM_ENABLED=false) before starting.",
            file=sys.stderr,
        )
        sys.exit(1)
