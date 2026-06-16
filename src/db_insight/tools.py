from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import re
import sqlglot
from sqlglot import exp

from db_insight.db import PostgresClient, SQLiteClient
from db_insight.errors import DbInsightError
from db_insight.models import ModelClient
from db_insight.safety import UnsafeSqlError, mask_pii_rows, validate_select_only

ToolName = Literal[
    "validate_connection",
    "discover_schema",
    "generate_safe_sql",
    "run_approved_sql",
    "explain_sql",
    "inspect_table",
    "catalog_overview",
    "summarize_results",
]


@dataclass(frozen=True)
class ToolCall:
    name: ToolName
    reason: str


class DatabaseTools:
    """MCP-style tool implementations used by both CLI and MCP server."""

    def __init__(self, db: PostgresClient | SQLiteClient, model: ModelClient, default_limit: int) -> None:
        self.db = db
        self.model = model
        self.default_limit = default_limit

    def validate_connection(self) -> dict[str, Any]:
        return self.db.validate_readonly()

    def discover_schema(self) -> str:
        return self.db.schema_overview_text()

    def discover_relevant_schema(self, question: str) -> str:
        return self.db.schema_prompt(question=question, max_tables=8)

    def catalog_overview(self) -> list[dict[str, Any]]:
        return self.db.table_catalog()

    def inspect_table(self, table_name: str) -> dict[str, Any]:
        return self.db.table_details(table_name)

    def explain_sql(self, sql: str) -> dict[str, Any]:
        try:
            safe_sql = validate_select_only(sql, self.default_limit, self.db.sql_dialect)
        except UnsafeSqlError as exc:
            raise DbInsightError(f"SQL was blocked by the safety validator: {exc}") from exc
        return self.db.explain_query(safe_sql)

    def generate_safe_sql(self, question: str, schema: str | None = None) -> str:
        from db_insight.agent import SQL_SYSTEM_PROMPT

        unsupported_answer = self._unsupported_metadata_answer(question)
        if unsupported_answer:
            raise DbInsightError(unsupported_answer)

        deterministic_sql = self._deterministic_sql(question)
        if deterministic_sql:
            safe_sql = validate_select_only(deterministic_sql, self.default_limit, self.db.sql_dialect)
            if not self._is_trusted_catalog_sql(deterministic_sql):
                self._validate_sql_against_schema(safe_sql)
            return safe_sql

        relevant_schema = schema or self.discover_relevant_schema(question)
        prompt = f"""Relevant schema:
{relevant_schema}

Question:
{question}

Rules:
- Return exactly one {self.db.sql_dialect} SELECT query.
- You MUST use only tables and columns listed in Relevant schema.
- Do not infer columns from table names.
- If the question asks for a count/ranking, check whether the count exists as a column on an entity table before using an event table.
- Prefer audit tables when the question asks "by whom", "changed by", or "updated by".
- For "last updated", order by updated_at or changed_at descending and limit 1.
- If a direct audit table exists, use it to identify the actor.
- Do not return markdown or explanation.
"""
        raw = self.model.generate(prompt, system=SQL_SYSTEM_PROMPT)

        from db_insight.models import extract_sql

        try:
            safe_sql = validate_select_only(extract_sql(raw), self.default_limit, self.db.sql_dialect)
            self._validate_sql_against_schema(safe_sql)
            return safe_sql
        except UnsafeSqlError as exc:
            raise DbInsightError(f"The model did not produce safe runnable SQL: {exc}") from exc

    def run_approved_sql(self, sql: str) -> list[dict[str, Any]]:
        try:
            safe_sql = validate_select_only(sql, self.default_limit, self.db.sql_dialect)
        except UnsafeSqlError as exc:
            raise DbInsightError(f"SQL was blocked by the safety validator: {exc}") from exc
        return mask_pii_rows(self.db.run_query(safe_sql))

    def summarize_results(self, question: str, sql: str, rows: list[dict[str, Any]]) -> str:
        from db_insight.agent import SUMMARY_SYSTEM_PROMPT
        from db_insight.models import rows_for_prompt

        prompt = f"""Question:
{question}

SQL:
{sql}

Rows:
{rows_for_prompt(rows)}
"""
        return self.model.generate(prompt, system=SUMMARY_SYSTEM_PROMPT)

    def _deterministic_sql(self, question: str) -> str | None:
        return (
            self._table_usage_sql(question)
            or self._highest_project_followers_sql(question)
            or self._project_search_sql(question)
            or self._audit_question_sql(question)
        )

    def _unsupported_metadata_answer(self, question: str) -> str | None:
        normalized = question.lower().replace("_", " ")
        asks_database = any(term in normalized for term in ("db", "database"))
        asks_created = any(term in normalized for term in ("created", "creation", "created at"))
        if asks_database and asks_created:
            return (
                "Postgres does not store a reliable database creation timestamp in normal "
                "system catalogs. I will not infer the database creation date from application "
                "tables like campaigns or projects. Ask for an approximation, such as "
                "'what is the earliest created_at in user tables?', if that is what you want."
            )
        return None

    def _highest_project_followers_sql(self, question: str) -> str | None:
        normalized = question.lower().replace("_", " ")
        asks_project = "project" in normalized
        asks_followers = "follower" in normalized or "followers" in normalized
        asks_highest = any(word in normalized for word in ("highest", "most", "max", "maximum", "top"))
        if not (asks_project and asks_followers and asks_highest):
            return None

        overview = self.db.schema_overview()
        if overview.has_column("project_ideas", "follower_count"):
            return (
                "SELECT project_name, follower_count, id, slug "
                "FROM public.project_ideas "
                "ORDER BY follower_count DESC NULLS LAST LIMIT 1"
            )
        if overview.has_table("project_followers") and overview.has_table("project_ideas"):
            return (
                "SELECT p.project_name, COUNT(f.id) AS follower_count, p.id, p.slug "
                "FROM public.project_ideas AS p "
                "LEFT JOIN public.project_followers AS f ON f.project_id = p.id "
                "GROUP BY p.id, p.project_name, p.slug "
                "ORDER BY follower_count DESC LIMIT 1"
            )
        return None

    def _project_search_sql(self, question: str) -> str | None:
        normalized = question.lower().replace("_", " ")
        if "project" not in normalized:
            return None

        overview = self.db.schema_overview()
        if not overview.has_table("project_ideas"):
            return None

        latest_launch_sql = self._latest_project_sql(normalized, overview)
        if latest_launch_sql:
            return latest_launch_sql

        searchable_columns = [
            column
            for column in (
                "project_name",
                "project_description",
                "project_category",
                "project_industry",
                "company_name",
                "token_name",
                "token_symbol",
            )
            if overview.has_column("project_ideas", column)
        ]
        if not searchable_columns:
            return None

        stop_words = {
            "give",
            "show",
            "list",
            "find",
            "me",
            "which",
            "what",
            "who",
            "when",
            "where",
            "was",
            "were",
            "is",
            "are",
            "be",
            "been",
            "latest",
            "recent",
            "newest",
            "launched",
            "launch",
            "dedicated",
            "related",
            "about",
            "the",
            "a",
            "an",
            "in",
            "on",
            "for",
            "with",
            "project",
            "projects",
        }
        terms = [
            token
            for token in re.findall(r"[a-z0-9]+", normalized)
            if len(token) >= 3 and token not in stop_words
        ]
        terms = _expand_search_terms(terms)
        if not terms:
            return None

        match_expressions = []
        for term in terms[:6]:
            escaped = term.replace("'", "''")
            match_expressions.extend(f"{column} ILIKE '%{escaped}%'" for column in searchable_columns)

        selected_columns = [
            column
            for column in (
                "id",
                "project_name",
                "project_description",
                "project_category",
                "project_industry",
                "follower_count",
                "like_count",
                "slug",
                "created_at",
                "updated_at",
            )
            if overview.has_column("project_ideas", column)
        ]
        order_by = " ORDER BY updated_at DESC NULLS LAST" if overview.has_column("project_ideas", "updated_at") else ""
        relevance_score = " + ".join(f"CASE WHEN {expression} THEN 1 ELSE 0 END" for expression in match_expressions)
        if relevance_score:
            order_by = f" ORDER BY relevance_score DESC{', updated_at DESC NULLS LAST' if overview.has_column('project_ideas', 'updated_at') else ''}"
        return (
            f"SELECT {', '.join(selected_columns)}, ({relevance_score}) AS relevance_score "
            "FROM public.project_ideas "
            f"WHERE {' OR '.join(match_expressions)} "
            f"{order_by} LIMIT 20"
        )

    def _latest_project_sql(self, normalized_question: str, overview) -> str | None:
        asks_latest = any(word in normalized_question for word in ("latest", "newest", "recent"))
        asks_launch = "launch" in normalized_question or "launched" in normalized_question
        if not (asks_latest and asks_launch):
            return None

        selected_columns = [
            column
            for column in (
                "id",
                "project_name",
                "project_description",
                "project_category",
                "project_industry",
                "is_launched",
                "launched_token_id",
                "launch_date",
                "created_at",
                "updated_at",
                "slug",
            )
            if overview.has_column("project_ideas", column)
        ]
        filters = []
        if overview.has_column("project_ideas", "is_launched"):
            filters.append("is_launched = true")
        if overview.has_column("project_ideas", "is_hidden"):
            filters.append("is_hidden = false")

        for order_column in ("launch_date", "updated_at", "created_at"):
            if overview.has_column("project_ideas", order_column):
                return (
                    f"SELECT {', '.join(selected_columns)} "
                    "FROM public.project_ideas "
                    f"{'WHERE ' + ' AND '.join(filters) if filters else ''} "
                    f"ORDER BY {order_column} DESC NULLS LAST LIMIT 20"
                )
        return None

    def _table_usage_sql(self, question: str) -> str | None:
        normalized = question.lower().replace("_", " ")
        asks_tables = "table" in normalized or "tables" in normalized
        asks_usage = any(
            phrase in normalized
            for phrase in (
                "frequently used",
                "most used",
                "highest usage",
                "table usage",
                "queries per hour",
                "query per hour",
            )
        )
        if not (asks_tables and asks_usage):
            return None

        return """
WITH db_stats AS (
  SELECT stats_reset
  FROM pg_stat_database
  WHERE datname = current_database()
),
table_usage AS (
  SELECT
    schemaname,
    relname AS table_name,
    seq_scan + idx_scan AS estimated_read_queries,
    n_tup_ins + n_tup_upd + n_tup_del AS write_operations,
    db_stats.stats_reset,
    EXTRACT(EPOCH FROM (now() - db_stats.stats_reset)) / 3600.0 AS tracked_hours
  FROM pg_stat_user_tables
  CROSS JOIN db_stats
)
SELECT
  schemaname,
  table_name,
  estimated_read_queries,
  write_operations,
  ROUND(
    estimated_read_queries / NULLIF(tracked_hours, 0),
    2
  ) AS estimated_read_queries_per_hour,
  stats_reset
FROM table_usage
ORDER BY estimated_read_queries DESC
LIMIT 20
"""

    def _audit_question_sql(self, question: str) -> str | None:
        normalized = question.lower().replace("_", " ")
        asks_actor = any(phrase in normalized for phrase in ("by whom", "who", "changed by", "updated by"))
        asks_last_update = "last" in normalized and any(
            word in normalized for word in ("updated", "changed", "modified")
        )
        if not (asks_actor and asks_last_update):
            return None

        overview = self.db.schema_overview()
        for table, columns in overview.tables.items():
            if not table.endswith("_audit"):
                continue

            schema_name, _, table_name = table.partition(".")
            base_name = table_name.removesuffix("_audit")
            if base_name.replace("_", " ") not in normalized and base_name not in question.lower():
                continue

            column_names = {column.column_name for column in columns}
            if not {"changed_at", "changed_by"}.issubset(column_names):
                continue

            selected = ["changed_at", "changed_by"]
            for optional in ("operation", "row_id", "config_key", "table_name"):
                if optional in column_names:
                    selected.append(optional)

            rendered_columns = ", ".join(selected)
            return (
                f"SELECT {rendered_columns} FROM {schema_name}.{table_name} "
                "ORDER BY changed_at DESC LIMIT 1"
            )

        return None

    def _validate_sql_against_schema(self, sql: str) -> None:
        overview = self.db.schema_overview()
        expression = sqlglot.parse_one(sql, read="postgres")
        alias_to_table: dict[str, str] = {}
        select_aliases = {
            alias.alias
            for alias in expression.find_all(exp.Alias)
            if alias.alias
        }

        for table in expression.find_all(exp.Table):
            table_name = table.name
            resolved = overview.resolve_table(table_name)
            if not resolved:
                raise DbInsightError(
                    f"The model tried to use table '{table_name}', but that table is not in the schema."
                )
            alias_to_table[table.alias_or_name] = resolved
            alias_to_table[table_name] = resolved

        for column in expression.find_all(exp.Column):
            column_name = column.name
            if column_name == "*" or column_name in select_aliases:
                continue
            table_alias = column.table
            if not table_alias:
                candidate_tables = [
                    table
                    for table in set(alias_to_table.values())
                    if any(schema_column.column_name == column_name for schema_column in overview.tables[table])
                ]
                if not candidate_tables:
                    raise DbInsightError(
                        f"The model tried to use column '{column_name}', but it is not present "
                        "on the selected schema tables."
                    )
                continue

            resolved_table = alias_to_table.get(table_alias)
            if not resolved_table:
                raise DbInsightError(
                    f"The model used alias/table '{table_alias}', but it is not mapped to a real table."
                )
            if not any(schema_column.column_name == column_name for schema_column in overview.tables[resolved_table]):
                raise DbInsightError(
                    f"The model tried to use column '{column_name}' on '{resolved_table}', "
                    "but that column is not in the schema."
                )

    def _is_trusted_catalog_sql(self, sql: str) -> bool:
        lowered = sql.lower()
        if "pg_stat_user_tables" in lowered and "pg_stat_database" in lowered:
            return True

        expression = sqlglot.parse_one(sql, read="postgres")
        allowed_catalog_tables = {"pg_stat_database", "pg_stat_user_tables"}
        for table in expression.find_all(exp.Table):
            if table.name not in allowed_catalog_tables:
                return False
        return True


def _expand_search_terms(terms: list[str]) -> list[str]:
    expanded: list[str] = []
    for term in terms:
        candidates = [term]
        if term.endswith("ers") and len(term) > 4:
            candidates.append(term[:-1])
        elif term.endswith("s") and len(term) > 4:
            candidates.append(term[:-1])
        if term == "farmer":
            candidates.extend(["farm", "agriculture", "agri"])
        if term == "farmers":
            candidates.extend(["farmer", "farm", "agriculture", "agri"])

        for candidate in candidates:
            if candidate not in expanded:
                expanded.append(candidate)
    return expanded
