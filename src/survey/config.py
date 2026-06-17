"""Typed application configuration loaded from the environment.

Fail-fast: a missing required setting raises at construction time, so the app
never boots in a half-configured state (Invariant: config is fixed, not guessed).
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(..., description="PostgreSQL connection URL.")
    source_dir: str | None = Field(
        default=None,
        description=(
            "Writable upload directory. User uploads are saved here as timestamped "
            "CSVs and the newest wins; the bundled sample is never written here. The "
            "live dataset is the newest upload if any, otherwise the bundled sample."
        ),
    )
    bundled_sample: str | None = Field(
        default=None,
        description=(
            "Path to the read-only bundled sample CSV: the fallback ingested when the "
            "upload directory is empty, and what the reset-to-sample action restores."
        ),
    )
    min_reliable_n: int = Field(
        default=30,
        ge=1,
        description="Cross-tab cells with n below this are flagged low reliability.",
    )
    llm_api_key: str | None = Field(
        default=None,
        description="Optional key for the out-of-core LLM report; absent disables it.",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, constructed once.

    The pydantic mypy plugin understands that BaseSettings fields are populated
    from the environment, so no constructor arguments are needed here.
    """
    return Settings()
