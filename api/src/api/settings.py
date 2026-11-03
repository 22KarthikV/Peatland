"""Runtime configuration read from the environment (no hidden global state)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql+psycopg://peatwatch:peatwatch@localhost:5432/peatwatch"
    aws_endpoint_url: str | None = None
    aws_region: str = "eu-west-2"
    data_bucket: str = "peatwatch-data"
    artifacts_bucket: str = "peatwatch-artifacts"
    queue_name: str = "peatwatch-jobs"
    # Comma-separated SHA-256 hex digests of the accepted keys; never plain keys (openapi.yaml).
    api_keys: str = ""
    # Optional Secrets Manager secret of comma-separated plaintext keys (hashed on load).
    api_key_secret_id: str = ""
    # Comma-separated browser origins allowed to call the API (the web app).
    cors_origins: str = "http://localhost:5173"
    rate_limit_job_per_minute: int = Field(default=10, ge=1)
    rate_limit_default_per_minute: int = Field(default=120, ge=1)

    # Stale-run reaper (queue.md item 3): fail `running` runs with no heartbeat for this long.
    reaper_stale_after_s: float = Field(default=900.0, gt=0)
    # How often the worker's poll loop runs the reaper.
    reaper_interval_s: float = Field(default=60.0, gt=0)

    @property
    def api_key_hashes(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.api_keys.split(",") if h.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
