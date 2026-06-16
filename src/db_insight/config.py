from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from db_insight.errors import DbInsightError
from dotenv import load_dotenv
import os

DEFAULT_OLLAMA_MODEL = "gemma3:latest"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


@dataclass(frozen=True)
class Settings:
    database_url: str
    ollama_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_OLLAMA_MODEL
    query_timeout_seconds: int = 20
    default_limit: int = 100


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or ".env")

    database_url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URI")
    if not database_url:
        raise DbInsightError("DATABASE_URL or DATABASE_URI is required in .env or the environment.")

    database_url = _docker_safe_database_url(database_url)
    parsed_url = urlparse(database_url)
    if parsed_url.scheme in {"sqlite", "sqlite3"}:
        return Settings(
            database_url=database_url,
            ollama_url=os.getenv("DB_INSIGHT_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/"),
            model=os.getenv("DB_INSIGHT_MODEL", DEFAULT_OLLAMA_MODEL),
            query_timeout_seconds=int(os.getenv("DB_INSIGHT_QUERY_TIMEOUT_SECONDS", "20")),
            default_limit=int(os.getenv("DB_INSIGHT_DEFAULT_LIMIT", "100")),
        )
    if parsed_url.scheme not in {"postgresql", "postgres"}:
        raise DbInsightError("DATABASE_URL must start with postgresql://, postgres://, or sqlite://.")
    if not parsed_url.hostname:
        raise DbInsightError("DATABASE_URL is missing a hostname.")
    if any(char in parsed_url.hostname for char in ("$", " ", "\n", "\r", "\t")):
        raise DbInsightError(
            "DATABASE_URL has an invalid hostname. Check that the connection string in .env "
            "is complete and quote it if it contains special characters."
        )

    return Settings(
        database_url=database_url,
        ollama_url=os.getenv("DB_INSIGHT_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/"),
        model=os.getenv("DB_INSIGHT_MODEL", DEFAULT_OLLAMA_MODEL),
        query_timeout_seconds=int(os.getenv("DB_INSIGHT_QUERY_TIMEOUT_SECONDS", "20")),
        default_limit=int(os.getenv("DB_INSIGHT_DEFAULT_LIMIT", "100")),
    )


def _docker_safe_database_url(database_url: str) -> str:
    if not Path("/.dockerenv").exists():
        return database_url

    parsed = urlparse(database_url)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return database_url

    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{userinfo}host.docker.internal{port}"
    return urlunparse(parsed._replace(netloc=netloc))
