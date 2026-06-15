from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from db_insight.agent import InsightAgent
from db_insight.config import load_settings
from db_insight.memory import MemoryBackedPostgresClient, SchemaMemory
from db_insight.models import build_model_client
from db_insight.tools import DatabaseTools

mcp = FastMCP("db-insight")


def agent() -> InsightAgent:
    settings = load_settings()
    db = MemoryBackedPostgresClient(settings.database_url, settings.query_timeout_seconds)
    model = build_model_client(settings)
    return InsightAgent(db, model, settings.default_limit)


def tools() -> DatabaseTools:
    settings = load_settings()
    db = MemoryBackedPostgresClient(settings.database_url, settings.query_timeout_seconds)
    model = build_model_client(settings)
    return DatabaseTools(db, model, settings.default_limit)


@mcp.tool()
def plan_tool_calls(question: str) -> dict:
    """Ask the LLM to decide which MCP tools are needed for a user question."""
    planned = agent().plan_tools(question)
    return {"calls": [{"name": call.name, "reason": call.reason} for call in planned]}


@mcp.tool()
def discover_schema() -> str:
    """Return a concise database overview with important tables and timestamp fields."""
    return tools().discover_schema()


@mcp.tool()
def refresh_schema_memory() -> dict:
    """Refresh and persist the local schema memory for this database."""
    settings = load_settings()
    db = MemoryBackedPostgresClient(
        settings.database_url,
        settings.query_timeout_seconds,
        prefer_memory=False,
    )
    overview = db.refresh_schema_memory()
    return {"table_count": overview.table_count, "schemas": overview.schemas}


@mcp.tool()
def schema_memory_status() -> dict:
    """Return local schema memory status for this database."""
    settings = load_settings()
    return SchemaMemory().status(settings.database_url)


@mcp.tool()
def discover_relevant_schema(question: str) -> str:
    """Return table/column metadata most relevant to a user question."""
    return tools().discover_relevant_schema(question)


@mcp.tool()
def catalog_overview() -> list[dict]:
    """Return Postgres catalog stats for user tables, including approximate rows and scan counts."""
    return tools().catalog_overview()


@mcp.tool()
def inspect_table(table_name: str) -> dict:
    """Return columns, indexes, and constraints for a table."""
    return tools().inspect_table(table_name)


@mcp.tool()
def explain_sql(sql: str) -> dict:
    """Return a safe EXPLAIN plan for SELECT-only SQL."""
    return tools().explain_sql(sql)


@mcp.tool()
def generate_safe_sql(question: str) -> str:
    """Generate SELECT-only SQL for a natural-language question."""
    return tools().generate_safe_sql(question)


@mcp.tool()
def run_approved_sql(question: str, sql: str) -> dict:
    """Run already-approved SELECT-only SQL and summarize masked results."""
    toolset = tools()
    rows = toolset.run_approved_sql(sql)
    summary = toolset.summarize_results(question, sql, rows)
    return {"rows": rows, "summary": summary}


@mcp.tool()
def ask_database(question: str, approved: bool = False) -> dict:
    """Let the LLM plan MCP tool calls, generate safe SQL, and optionally run it."""
    return agent().answer_with_tools(question, approved=approved)


def main() -> None:
    mcp.run(transport="stdio")
