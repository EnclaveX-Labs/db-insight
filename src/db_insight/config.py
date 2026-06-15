from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from db_insight.errors import DbInsightError
from dotenv import load_dotenv
import os

DEFAULT_OLLAMA_MODEL = "gemma3:latest"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"


@dataclass(frozen=True)
class Settings:
    database_url: str
    model_provider: str = "ollama"
    ollama_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_OLLAMA_MODEL
    gemini_api_key: str | None = None
    gemini_model: str = DEFAULT_GEMINI_MODEL
    query_timeout_seconds: int = 20
    default_limit: int = 100


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or ".env")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise DbInsightError("DATABASE_URL is required in .env or the environment.")

    parsed_url = urlparse(database_url)
    if parsed_url.scheme not in {"postgresql", "postgres"}:
        raise DbInsightError("DATABASE_URL must start with postgresql:// or postgres://.")
    if not parsed_url.hostname:
        raise DbInsightError("DATABASE_URL is missing a hostname.")
    if any(char in parsed_url.hostname for char in ("$", " ", "\n", "\r", "\t")):
        raise DbInsightError(
            "DATABASE_URL has an invalid hostname. Check that the connection string in .env "
            "is complete and quote it if it contains special characters."
        )

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    model_provider = os.getenv("DB_INSIGHT_MODEL_PROVIDER")
    if not model_provider:
        model_provider = "gemini" if gemini_api_key else "ollama"

    return Settings(
        database_url=database_url,
        model_provider=model_provider.lower(),
        ollama_url=os.getenv("DB_INSIGHT_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/"),
        model=os.getenv("DB_INSIGHT_MODEL", DEFAULT_OLLAMA_MODEL),
        gemini_api_key=gemini_api_key,
        gemini_model=os.getenv("DB_INSIGHT_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        query_timeout_seconds=int(os.getenv("DB_INSIGHT_QUERY_TIMEOUT_SECONDS", "20")),
        default_limit=int(os.getenv("DB_INSIGHT_DEFAULT_LIMIT", "100")),
    )
