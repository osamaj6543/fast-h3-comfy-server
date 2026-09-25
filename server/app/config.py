"""Server configuration via environment variables / .env (prefix FASTH3_)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTH3_", env_file=".env", extra="ignore"
    )

    # Headless ComfyUI backend. Keep on localhost; never expose publicly.
    comfy_url: str = "http://127.0.0.1:8188"

    # Comma-separated API keys (X-API-Key header). Empty = open access (dev).
    api_keys: str = ""

    # Secret for signing result download URLs. Empty = derived from api_keys.
    url_signing_secret: str = ""

    data_dir: Path = Path("data")

    max_queue_size: int = 64          # max queued jobs system-wide
    max_active_per_key: int = 4       # max queued+running jobs per API key
    result_url_ttl_seconds: int = 86400
    poll_interval_seconds: float = 1.0
    log_level: str = "INFO"

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key_set)

    @property
    def signing_secret(self) -> bytes:
        if self.url_signing_secret:
            return self.url_signing_secret.encode()
        # Derive a stable-per-instance secret from the API keys.
        import hashlib

        return hashlib.sha256(("|".join(sorted(self.api_key_set)) or "dev").encode()).digest()


@lru_cache
def get_settings() -> Settings:
    return Settings()
