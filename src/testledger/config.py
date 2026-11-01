"""Runtime settings, read from environment variables prefixed with TESTLEDGER_."""

from __future__ import annotations

import hashlib
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TESTLEDGER_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./testledger.db"
    # SHA-256 hex digests of accepted API keys, comma separated. Raw keys never live in config.
    api_key_hashes: str = ""
    max_upload_bytes: int = Field(default=5_000_000, gt=0)
    log_level: str = "INFO"

    # Flaky-detection policy
    flaky_min_runs: int = Field(default=5, ge=2)
    flaky_threshold: float = Field(default=0.30, gt=0.0, le=1.0)
    release_after_passes: int = Field(default=10, ge=1)
    history_window: int = Field(default=50, ge=2, le=1000)

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    def accepted_key_hashes(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.api_key_hashes.split(",") if h.strip())


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
