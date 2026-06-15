from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from db_insight.db import Column, PostgresClient, SchemaOverview

MEMORY_DIR = Path(".db-insight")
SCHEMA_MEMORY_FILE = MEMORY_DIR / "schema_memory.json"


def database_fingerprint(database_url: str) -> str:
    return hashlib.sha256(database_url.encode("utf-8")).hexdigest()[:16]


class SchemaMemory:
    def __init__(self, path: Path = SCHEMA_MEMORY_FILE) -> None:
        self.path = path

    def load(self, database_url: str) -> SchemaOverview | None:
        if not self.path.exists():
            return None

        payload = json.loads(self.path.read_text())
        if payload.get("database_fingerprint") != database_fingerprint(database_url):
            return None

        tables: dict[str, list[Column]] = {}
        for table, columns in payload.get("tables", {}).items():
            tables[table] = [Column(**column) for column in columns]

        return SchemaOverview(
            table_count=payload.get("table_count", len(tables)),
            schemas=payload.get("schemas", []),
            tables=tables,
        )

    def save(self, database_url: str, overview: SchemaOverview) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "database_fingerprint": database_fingerprint(database_url),
            "captured_at": datetime.now(UTC).isoformat(),
            "table_count": overview.table_count,
            "schemas": overview.schemas,
            "tables": {
                table: [asdict(column) for column in columns]
                for table, columns in overview.tables.items()
            },
        }
        self.path.write_text(json.dumps(payload, indent=2, default=str))

    def status(self, database_url: str) -> dict:
        if not self.path.exists():
            return {"exists": False, "path": str(self.path)}

        payload = json.loads(self.path.read_text())
        matches = payload.get("database_fingerprint") == database_fingerprint(database_url)
        return {
            "exists": True,
            "path": str(self.path),
            "matches_database": matches,
            "captured_at": payload.get("captured_at"),
            "table_count": payload.get("table_count"),
        }


class MemoryBackedPostgresClient(PostgresClient):
    def __init__(
        self,
        database_url: str,
        timeout_seconds: int = 20,
        memory: SchemaMemory | None = None,
        prefer_memory: bool = True,
    ) -> None:
        super().__init__(database_url, timeout_seconds)
        self.memory = memory or SchemaMemory()
        self.prefer_memory = prefer_memory

    def schema_overview(self) -> SchemaOverview:
        if self.prefer_memory:
            overview = self.memory.load(self.database_url)
            if overview:
                return overview

        overview = super().schema_overview()
        self.memory.save(self.database_url, overview)
        return overview

    def refresh_schema_memory(self) -> SchemaOverview:
        overview = super().schema_overview()
        self.memory.save(self.database_url, overview)
        return overview
