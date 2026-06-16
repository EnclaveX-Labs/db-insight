from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import unquote, urlparse

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised when only SQLite support is installed.
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

from db_insight.errors import DbInsightError


@dataclass(frozen=True)
class Column:
    table_schema: str
    table_name: str
    column_name: str
    data_type: str
    is_nullable: bool


@dataclass(frozen=True)
class SchemaOverview:
    table_count: int
    schemas: list[str]
    tables: dict[str, list[Column]]

    @property
    def audit_tables(self) -> list[str]:
        return [
            table
            for table in self.tables
            if "audit" in table.lower() or any(col.column_name == "changed_at" for col in self.tables[table])
        ]

    @property
    def update_tracked_tables(self) -> list[str]:
        return [
            table
            for table, columns in self.tables.items()
            if any(column.column_name in {"updated_at", "changed_at"} for column in columns)
        ]

    def has_table(self, table_name: str) -> bool:
        return self.resolve_table(table_name) is not None

    def resolve_table(self, table_name: str) -> str | None:
        normalized = table_name.lower()
        for table in self.tables:
            schema, _, name = table.partition(".")
            if normalized in {table.lower(), name.lower(), f"{schema}.{name}".lower()}:
                return table
        return None

    def has_column(self, table_name: str, column_name: str) -> bool:
        table = self.resolve_table(table_name)
        if not table:
            return False
        return any(column.column_name == column_name for column in self.tables[table])


class PostgresClient:
    sql_dialect = "postgres"

    def __init__(self, database_url: str, timeout_seconds: int = 20) -> None:
        self.database_url = database_url
        self.timeout_seconds = timeout_seconds

    def connect(self) -> psycopg.Connection:
        if psycopg is None or dict_row is None:
            raise DbInsightError("Postgres support requires installing psycopg.")
        try:
            conn = psycopg.connect(self.database_url, row_factory=dict_row)
            conn.execute(f"SET statement_timeout = {self.timeout_seconds * 1000}")
            conn.execute("SET default_transaction_read_only = on")
            conn.commit()
            return conn
        except psycopg.OperationalError as exc:
            raise DbInsightError(
                "Could not connect to Postgres. Check DATABASE_URL in .env, network access, "
                "and whether the database allows connections from this machine."
            ) from exc

    def validate_readonly(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                select
                  current_database() as database,
                  current_user as user,
                  pg_is_in_recovery() as is_replica,
                  current_setting('transaction_read_only') as transaction_read_only
                """
            ).fetchone()
            return dict(row or {})

    def discover_schema(self, include_extensions: bool = False) -> list[Column]:
        excluded_schemas = "('pg_catalog', 'information_schema')"
        if not include_extensions:
            excluded_schemas = "('pg_catalog', 'information_schema', 'extensions')"

        with self.connect() as conn:
            rows = conn.execute(
                f"""
                select table_schema, table_name, column_name, data_type, is_nullable
                from information_schema.columns
                where table_schema not in {excluded_schemas}
                order by table_schema, table_name, ordinal_position
                """
            ).fetchall()
        return [
            Column(
                table_schema=row["table_schema"],
                table_name=row["table_name"],
                column_name=row["column_name"],
                data_type=row["data_type"],
                is_nullable=row["is_nullable"] == "YES",
            )
            for row in rows
        ]

    def schema_overview(self) -> SchemaOverview:
        tables: dict[str, list[Column]] = {}
        for column in self.discover_schema():
            key = f"{column.table_schema}.{column.table_name}"
            tables.setdefault(key, []).append(column)

        return SchemaOverview(
            table_count=len(tables),
            schemas=sorted({column.table_schema for columns in tables.values() for column in columns}),
            tables=tables,
        )

    def schema_prompt(self, question: str | None = None, max_tables: int | None = None) -> str:
        overview = self.schema_overview()
        tables = overview.tables

        if question:
            tables = self.relevant_tables(question, max_tables=max_tables or 8)
        elif max_tables:
            tables = dict(list(tables.items())[:max_tables])

        lines: list[str] = []
        for table, columns in tables.items():
            rendered_columns = ", ".join(
                f"{column.column_name} {column.data_type}{' null' if column.is_nullable else ''}"
                for column in columns
            )
            lines.append(f"- {table}({rendered_columns})")
        return "\n".join(lines)

    def relevant_tables(self, question: str, max_tables: int = 8) -> dict[str, list[Column]]:
        overview = self.schema_overview()
        tokens = {token for token in _tokenize(question) if len(token) >= 3}
        scored: list[tuple[int, str, list[Column]]] = []

        for table, columns in overview.tables.items():
            haystack = {token for token in _tokenize(table) if len(token) >= 3}
            for column in columns:
                haystack.update(token for token in _tokenize(column.column_name) if len(token) >= 3)

            score = len(tokens & haystack)
            if any(token in table.lower() for token in tokens):
                score += 3
            if "last" in tokens and any(
                column.column_name in {"updated_at", "changed_at", "created_at"} for column in columns
            ):
                score += 1
            if score:
                scored.append((score, table, columns))

        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = scored[:max_tables] or [(0, table, columns) for table, columns in list(overview.tables.items())[:max_tables]]
        return {table: columns for _, table, columns in selected}

    def schema_overview_text(self) -> str:
        overview = self.schema_overview()
        lines = [
            f"Database overview: {overview.table_count} user tables across {len(overview.schemas)} schema(s).",
            f"Schemas: {', '.join(overview.schemas) or 'none'}",
        ]
        if overview.audit_tables:
            lines.append("Audit/change tables: " + ", ".join(overview.audit_tables[:10]))
        if overview.update_tracked_tables:
            lines.append("Tables with update/change timestamps: " + ", ".join(overview.update_tracked_tables[:20]))

        lines.append("")
        lines.append("Tables:")
        for table, columns in overview.tables.items():
            important_columns = [
                column.column_name
                for column in columns
                if column.column_name
                in {
                    "id",
                    "created_at",
                    "updated_at",
                    "changed_at",
                    "changed_by",
                    "operation",
                    "config_key",
                    "wallet_address",
                    "status",
                }
            ]
            if not important_columns:
                important_columns = [column.column_name for column in columns[:6]]
            lines.append(f"- {table}: {len(columns)} columns; key fields: {', '.join(important_columns)}")
        return "\n".join(lines)

    def table_catalog(self) -> list[dict[str, Any]]:
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    select
                      n.nspname as schema_name,
                      c.relname as table_name,
                      greatest(c.reltuples::bigint, 0) as approximate_rows,
                      obj_description(c.oid) as description,
                      s.seq_scan,
                      s.idx_scan,
                      s.n_tup_ins,
                      s.n_tup_upd,
                      s.n_tup_del,
                      s.last_vacuum,
                      s.last_autovacuum,
                      s.last_analyze,
                      s.last_autoanalyze
                    from pg_class c
                    join pg_namespace n on n.oid = c.relnamespace
                    left join pg_stat_user_tables s on s.relid = c.oid
                    where c.relkind in ('r', 'p')
                      and n.nspname not in ('pg_catalog', 'information_schema', 'extensions')
                    order by n.nspname, c.relname
                    """
                ).fetchall()
            return [dict(row) for row in rows]
        except psycopg.Error as exc:
            raise DbInsightError(f"Could not read Postgres catalog: {exc}") from exc

    def table_details(self, table_name: str) -> dict[str, Any]:
        overview = self.schema_overview()
        resolved = overview.resolve_table(table_name)
        if not resolved:
            raise DbInsightError(f"Table '{table_name}' is not in the schema memory/catalog.")

        schema_name, _, name = resolved.partition(".")
        try:
            with self.connect() as conn:
                indexes = conn.execute(
                    """
                    select indexname, indexdef
                    from pg_indexes
                    where schemaname = %s and tablename = %s
                    order by indexname
                    """,
                    (schema_name, name),
                ).fetchall()
                constraints = conn.execute(
                    """
                    select conname as constraint_name, contype as constraint_type,
                           pg_get_constraintdef(oid) as definition
                    from pg_constraint
                    where conrelid = %s::regclass
                    order by conname
                    """,
                    (resolved,),
                ).fetchall()
            return {
                "table": resolved,
                "columns": [column.__dict__ for column in overview.tables[resolved]],
                "indexes": [dict(row) for row in indexes],
                "constraints": [dict(row) for row in constraints],
            }
        except psycopg.Error as exc:
            raise DbInsightError(f"Could not read table details for '{resolved}': {exc}") from exc

    def explain_query(self, sql: str) -> dict[str, Any]:
        try:
            with self.connect() as conn:
                with conn.transaction():
                    row = conn.execute(f"EXPLAIN (FORMAT JSON, VERBOSE, COSTS TRUE) {sql}").fetchone()
            return {"plan": row["QUERY PLAN"] if row else None}
        except psycopg.Error as exc:
            raise DbInsightError(f"Postgres could not explain the query: {exc}") from exc

    def run_query(self, sql: str) -> list[dict[str, Any]]:
        try:
            with self.connect() as conn:
                with conn.transaction():
                    rows = conn.execute(sql).fetchall()
            return [dict(row) for row in rows]
        except psycopg.errors.UndefinedColumn as exc:
            raise DbInsightError(
                "The generated SQL referenced a column that does not exist. "
                "Try the question again or inspect the generated SQL before approving."
            ) from exc
        except psycopg.Error as exc:
            raise DbInsightError(f"Postgres rejected the query: {exc}") from exc


class SQLiteClient:
    sql_dialect = "sqlite"

    def __init__(self, database_url: str, timeout_seconds: int = 20) -> None:
        self.database_url = database_url
        self.timeout_seconds = timeout_seconds
        self.path = _sqlite_path(database_url)

    def connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(f"file:{Path(self.path).as_posix()}?mode=ro", timeout=self.timeout_seconds, uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            raise DbInsightError(f"Could not connect to SQLite database at {self.path}.") from exc

    def validate_readonly(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("PRAGMA query_only").fetchone()
        return {"database": str(self.path), "query_only": bool(row[0]) if row else True}

    def discover_schema(self, include_extensions: bool = False) -> list[Column]:
        del include_extensions
        with self.connect() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            columns: list[Column] = []
            for table in tables:
                table_name = table["name"]
                for row in conn.execute(f"PRAGMA table_info({_quote_sqlite_identifier(table_name)})"):
                    columns.append(
                        Column(
                            table_schema="main",
                            table_name=table_name,
                            column_name=row["name"],
                            data_type=row["type"] or "unknown",
                            is_nullable=not bool(row["notnull"]),
                        )
                    )
        return columns

    def schema_overview(self) -> SchemaOverview:
        tables: dict[str, list[Column]] = {}
        for column in self.discover_schema():
            key = f"{column.table_schema}.{column.table_name}"
            tables.setdefault(key, []).append(column)
        return SchemaOverview(table_count=len(tables), schemas=["main"] if tables else [], tables=tables)

    def schema_prompt(self, question: str | None = None, max_tables: int | None = None) -> str:
        return PostgresClient.schema_prompt(self, question, max_tables)  # type: ignore[arg-type]

    def relevant_tables(self, question: str, max_tables: int = 8) -> dict[str, list[Column]]:
        return PostgresClient.relevant_tables(self, question, max_tables)  # type: ignore[arg-type]

    def schema_overview_text(self) -> str:
        return PostgresClient.schema_overview_text(self)  # type: ignore[arg-type]

    def table_catalog(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name AS table_name, type, sql FROM sqlite_master "
                "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [{"schema_name": "main", **dict(row)} for row in rows]

    def table_details(self, table_name: str) -> dict[str, Any]:
        overview = self.schema_overview()
        resolved = overview.resolve_table(table_name)
        if not resolved:
            raise DbInsightError(f"Table '{table_name}' is not in the schema memory/catalog.")
        _, _, name = resolved.partition(".")
        with self.connect() as conn:
            indexes = conn.execute(f"PRAGMA index_list({_quote_sqlite_identifier(name)})").fetchall()
        return {
            "table": resolved,
            "columns": [column.__dict__ for column in overview.tables[resolved]],
            "indexes": [dict(row) for row in indexes],
            "constraints": [],
        }

    def explain_query(self, sql: str) -> dict[str, Any]:
        try:
            with self.connect() as conn:
                rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
            return {"plan": [dict(row) for row in rows]}
        except sqlite3.Error as exc:
            raise DbInsightError(f"SQLite could not explain the query: {exc}") from exc

    def run_query(self, sql: str) -> list[dict[str, Any]]:
        try:
            with self.connect() as conn:
                rows = conn.execute(sql).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise DbInsightError(f"SQLite rejected the query: {exc}") from exc


def build_db_client(database_url: str, timeout_seconds: int = 20):
    if urlparse(database_url).scheme in {"sqlite", "sqlite3"}:
        return SQLiteClient(database_url, timeout_seconds)

    from db_insight.memory import MemoryBackedPostgresClient

    return MemoryBackedPostgresClient(database_url, timeout_seconds)


def _sqlite_path(database_url: str) -> str:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"sqlite", "sqlite3"}:
        raise DbInsightError("SQLite DATABASE_URL must start with sqlite:// or sqlite3://.")
    if parsed.netloc and parsed.netloc != "localhost":
        raise DbInsightError("SQLite DATABASE_URL must point to a local file.")
    path = unquote(parsed.path)
    if not path:
        raise DbInsightError("SQLite DATABASE_URL is missing a file path.")
    if path.startswith("//"):
        path = path[1:]
    return str(Path(path))


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _tokenize(value: str) -> list[str]:
    import re

    return re.findall(r"[a-zA-Z0-9]+", value.lower().replace("_", " "))
