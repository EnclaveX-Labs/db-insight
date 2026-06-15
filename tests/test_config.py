from pathlib import Path

from db_insight.config import _docker_safe_database_url


def test_docker_safe_database_url_remaps_localhost_in_container(monkeypatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/.dockerenv")

    url = _docker_safe_database_url("postgresql://user:pass@localhost:5432/db")

    assert url == "postgresql://user:pass@host.docker.internal:5432/db"
