"""Runtime settings, read from environment variables (and an optional .env file).

Secrets such as the Socrata app token live only in the environment. See .env.example.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: backend/src/nyc_apartment_agent/config.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # Real environment variables win over the .env file at the repo root.
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    socrata_app_token: SecretStr | None = None
    socrata_base_url: str = "https://data.cityofnewyork.us"
    geosearch_base_url: str = "https://geosearch.planninglabs.nyc"

    http_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    http_read_timeout_seconds: float = Field(default=20.0, gt=0)
    http_max_attempts: int = Field(default=3, ge=1, le=6)
    http_max_retry_after_seconds: float = Field(default=30.0, ge=0)

    # Paging for record sources (Phase 2). The cap is per lot, per source: above it the
    # result is marked incomplete_pagination instead of being silently truncated.
    socrata_page_size: int = Field(default=1000, ge=1, le=50_000)
    source_row_cap: int = Field(default=10_000, ge=1)

    # Key for the keyed digest of unredacted rows (records/redaction.py: keyed_digest).
    # Unset: a random key is made per process, so digests are not stable across runs.
    evidence_digest_key: SecretStr | None = None

    # PostgreSQL connection URL (Phase 2 Task 4). A secret: it can hold a password, so it
    # is never logged. Unset: nothing is stored and the offline suite still runs.
    database_url: SecretStr | None = None

    user_agent: str = (
        "nyc-apartment-research-agent/0.1 "
        "(+https://github.com/Sjaisw12/nyc-apartment-research-agent)"
    )

    @field_validator("socrata_app_token", "evidence_digest_key", "database_url", mode="before")
    @classmethod
    def _blank_token_is_none(cls, value: object) -> object:
        # An empty SOCRATA_APP_TOKEN= line means "no token", not an empty header.
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
