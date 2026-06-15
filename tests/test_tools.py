from db_insight.db import Column, SchemaOverview
from db_insight.errors import DbInsightError
from db_insight.tools import DatabaseTools
import pytest


class FakeDb:
    def schema_overview(self) -> SchemaOverview:
        return SchemaOverview(
            table_count=4,
            schemas=["public"],
            tables={
                "public.application_config": [
                    Column("public", "application_config", "id", "uuid", False),
                    Column("public", "application_config", "updated_at", "timestamp", False),
                ],
                "public.application_config_audit": [
                    Column("public", "application_config_audit", "changed_at", "timestamp", False),
                    Column("public", "application_config_audit", "changed_by", "name", False),
                    Column("public", "application_config_audit", "operation", "text", False),
                    Column("public", "application_config_audit", "config_key", "text", True),
                ],
                "public.project_ideas": [
                    Column("public", "project_ideas", "id", "uuid", False),
                    Column("public", "project_ideas", "project_name", "text", False),
                    Column("public", "project_ideas", "project_description", "text", False),
                    Column("public", "project_ideas", "project_category", "text", False),
                    Column("public", "project_ideas", "project_industry", "text", False),
                    Column("public", "project_ideas", "is_launched", "boolean", True),
                    Column("public", "project_ideas", "is_hidden", "boolean", True),
                    Column("public", "project_ideas", "launch_date", "timestamp", True),
                    Column("public", "project_ideas", "follower_count", "integer", True),
                    Column("public", "project_ideas", "slug", "character varying", True),
                    Column("public", "project_ideas", "updated_at", "timestamp", True),
                ],
                "public.project_followers": [
                    Column("public", "project_followers", "id", "uuid", False),
                    Column("public", "project_followers", "project_id", "uuid", False),
                    Column("public", "project_followers", "wallet_address", "text", False),
                ],
            },
        )

    def schema_prompt(self, question: str | None = None, max_tables: int | None = None) -> str:
        return "\n".join(
            f"- {table}({', '.join(column.column_name for column in columns)})"
            for table, columns in self.schema_overview().tables.items()
        )


class FakeModel:
    def __init__(self, response: str = "") -> None:
        self.response = response

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self.response


def test_audit_question_uses_changed_at_and_changed_by() -> None:
    tools = DatabaseTools(FakeDb(), FakeModel(), 100)  # type: ignore[arg-type]

    sql = tools.generate_safe_sql("when was the last application_config table updated and by whom")

    assert "application_config_audit" in sql
    assert "changed_at" in sql
    assert "changed_by" in sql
    assert "updated_at" not in sql


def test_highest_project_followers_uses_project_ideas_count_column() -> None:
    tools = DatabaseTools(FakeDb(), FakeModel(), 100)  # type: ignore[arg-type]

    sql = tools.generate_safe_sql("which project has highest number of followers?")

    assert "project_ideas" in sql
    assert "follower_count" in sql
    assert "project_followers" not in sql


def test_generated_sql_is_validated_against_schema() -> None:
    tools = DatabaseTools(
        FakeDb(),
        FakeModel("select p.project_name from project_followers p order by p.follower_count desc limit 1"),
        100,
    )  # type: ignore[arg-type]

    with pytest.raises(DbInsightError, match="not in the schema"):
        tools.generate_safe_sql("list follower wallets")


def test_database_creation_time_is_not_inferred_from_app_tables() -> None:
    tools = DatabaseTools(FakeDb(), FakeModel(), 100)  # type: ignore[arg-type]

    with pytest.raises(DbInsightError, match="does not store a reliable database creation"):
        tools.generate_safe_sql("When was this db created?")


def test_table_usage_question_uses_postgres_stats_catalogs() -> None:
    tools = DatabaseTools(FakeDb(), FakeModel("select date from imaginary"), 100)  # type: ignore[arg-type]

    sql = tools.generate_safe_sql(
        "what are the most frequently used tables and queries per hour on them"
    )

    assert "pg_stat_user_tables" in sql
    assert "pg_stat_database" in sql
    assert "estimated_read_queries_per_hour" in sql


def test_project_search_uses_project_text_fields() -> None:
    tools = DatabaseTools(FakeDb(), FakeModel("select * from project_ideas"), 100)  # type: ignore[arg-type]

    sql = tools.generate_safe_sql("Give me the project in payment infrastructure")

    assert "project_ideas" in sql
    assert "project_industry" in sql
    assert "payment" in sql.lower()
    assert "infrastructure" in sql.lower()
    assert "which" not in sql.lower()


def test_project_search_filters_question_words_and_expands_terms() -> None:
    tools = DatabaseTools(FakeDb(), FakeModel("select * from project_ideas"), 100)  # type: ignore[arg-type]

    sql = tools.generate_safe_sql("which project was dedicated for farmers?")

    assert "which" not in sql.lower()
    assert "dedicated" not in sql.lower()
    assert "farmers" in sql.lower()
    assert "farmer" in sql.lower()
    assert "agriculture" in sql.lower()


def test_latest_launched_project_uses_launch_sort_not_text_search() -> None:
    tools = DatabaseTools(FakeDb(), FakeModel("select * from project_ideas"), 100)  # type: ignore[arg-type]

    sql = tools.generate_safe_sql("Latest launched project")

    assert "ILIKE '%latest%'" not in sql
    assert "ILIKE '%launched%'" not in sql
    assert "is_launched = TRUE" in sql
    assert "ORDER BY launch_date DESC NULLS LAST" in sql


def test_select_star_is_allowed_by_schema_validator() -> None:
    tools = DatabaseTools(FakeDb(), FakeModel("select * from project_ideas"), 100)  # type: ignore[arg-type]

    sql = tools.generate_safe_sql("show idea records")

    assert "SELECT *" in sql
