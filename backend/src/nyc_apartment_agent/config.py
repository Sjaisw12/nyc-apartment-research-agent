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
    # A separate login for the retention job (storage/retention.py) that holds ONLY the
    # evidence_retention role. A secret for the same reason; never logged.
    retention_database_url: SecretStr | None = None
    # Raw evidence is held this long after it was fetched, then reduced to a tombstone.
    # D15 sets 180 days as the maximum; the database refuses anything longer.
    evidence_retention_days: int = Field(default=180, ge=1, le=180)

    # ---- Phase 3: investigation workflow and optional LLM analysis -------------------
    # "demo" replays the committed, sanitized fixtures (no network, no keys); "live" calls
    # GeoSearch and NYC Open Data.
    data_mode: str = Field(default="demo", pattern="^(demo|live)$")
    investigation_max_seconds: float = Field(default=90.0, gt=0, le=600)
    # Most HTTP requests one live investigation may make (resolver + every source).
    investigation_max_requests: int = Field(default=150, ge=1, le=2000)
    # LLM analysis is OFF unless explicitly enabled AND a key for the provider is set.
    llm_enabled: bool = False
    llm_provider: str = Field(default="anthropic", pattern="^(anthropic|openai)$")
    llm_model: str | None = None  # provider default when unset
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_max_calls: int = Field(default=2, ge=0, le=2)
    llm_max_output_tokens: int = Field(default=900, ge=64, le=4000)
    llm_max_context_chars: int = Field(default=24_000, ge=1000, le=200_000)
    llm_timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    # Spend protection: a process-wide cap on LLM calls (resets on restart).
    llm_max_calls_per_process: int = Field(default=200, ge=0)
    # Comma-separated origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    investigate_rate_per_minute: int = Field(default=20, ge=1, le=10_000)

    user_agent: str = (
        "nyc-apartment-research-agent/0.1 "
        "(+https://github.com/Sjaisw12/nyc-apartment-research-agent)"
    )

    @field_validator(
        "socrata_app_token",
        "evidence_digest_key",
        "database_url",
        "retention_database_url",
        "anthropic_api_key",
        "openai_api_key",
        "llm_model",
        mode="before",
    )
    @classmethod
    def _blank_token_is_none(cls, value: object) -> object:
        # An empty SOCRATA_APP_TOKEN= line means "no token", not an empty header.
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
