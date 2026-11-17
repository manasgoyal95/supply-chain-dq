"""Infrastructure settings from environment variables (prefix SCDQ_). Only used by the FastAPI server."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCDQ_", env_file=".env", extra="ignore")

    app_name: str = "Supply Chain Data Quality"
    environment: str = "local"
    database_url: str = f"sqlite:///{BACKEND_DIR / 'scdq.db'}"
    config_dir: Path = BACKEND_DIR / "config"
    static_dir: Path = BACKEND_DIR / "static"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    max_upload_mb: int = 50
    log_level: str = "INFO"
    log_json: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
