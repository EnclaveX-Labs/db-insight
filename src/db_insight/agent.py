from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from db_insight.db import PostgresClient, SQLiteClient
from db_insight.models import ModelClient
from db_insight.tools import DatabaseTools, ToolCall


SQL_SYSTEM_PROMPT = """You are a senior analytics engineer.
Generate one safe SELECT query that answers the user's question.
Use only tables and columns present in the schema.
Return SQL only. Do not explain it.
"""

SUMMARY_SYSTEM_PROMPT = """You explain query results clearly and briefly.
Mention uncertainty when the result is not enough to answer the question.
"""

DECISION_SYSTEM_PROMPT = """You are an MCP tool planner for a local database assistant.
Choose the safest useful tool sequence for the user's request.

Available tools:
- validate_connection: verify local read-only database access.
- discover_schema: inspect database tables and columns.
- generate_safe_sql: create SELECT-only SQL for a natural-language question.
- run_approved_sql: execute SQL only after approval.
- summarize_results: explain returned rows.

Return only JSON with this shape:
{"calls":[{"name":"discover_schema","reason":"Need schema before SQL generation"}]}
"""

REQUIRED_DATABASE_FLOW = [
    ToolCall("discover_schema", "Need relevant schema context before generating SQL."),
    ToolCall("generate_safe_sql", "Need a safe read-only query for the question."),
    ToolCall("run_approved_sql", "Need database rows to answer the question."),
    ToolCall("summarize_results", "Need to explain the result to the user."),
]


class InsightAgent:
    def __init__(self, db: PostgresClient | SQLiteClient, model: ModelClient, default_limit: int) -> None:
        self.tools = DatabaseTools(db, model, default_limit)
        self.model = model
        self.default_limit = default_limit

    def plan_tools(self, question: str) -> list[ToolCall]:
        prompt = f"""User request:
{question}

Plan the MCP tool calls. For database questions, include discover_schema,
generate_safe_sql, run_approved_sql, and summarize_results.
"""
        raw = self.model.generate(prompt, system=DECISION_SYSTEM_PROMPT)
        calls = _parse_tool_plan(raw)
        return _complete_database_plan(calls)

    def answer_with_tools(
        self,
        question: str,
        approved: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan_tools(question)
        schema = ""
        sql = ""
        rows: list[dict[str, Any]] = []
        summary = ""

        for call in plan:
            if call.name == "validate_connection":
                self.tools.validate_connection()
            elif call.name == "discover_schema":
                schema = self.tools.discover_relevant_schema(question)
            elif call.name == "generate_safe_sql":
                sql = self.tools.generate_safe_sql(question, schema=schema or None)
            elif call.name == "run_approved_sql":
                if not approved:
                    return {
                        "status": "approval_required",
                        "plan": [asdict(item) for item in plan],
                        "sql": sql,
                        "message": "SQL was generated safely, but execution needs user approval.",
                    }
                rows = self.tools.run_approved_sql(sql)
            elif call.name == "summarize_results":
                summary = self.tools.summarize_results(question, sql, rows)

        return {
            "status": "complete",
            "plan": [asdict(item) for item in plan],
            "sql": sql,
            "rows": rows,
            "summary": summary,
        }

    def generate_sql(self, question: str) -> str:
        return self.tools.generate_safe_sql(question)

    def run_and_summarize(self, question: str, sql: str) -> tuple[list[dict], str]:
        rows = self.tools.run_approved_sql(sql)
        summary = self.tools.summarize_results(question, sql, rows)
        return rows, summary


def _parse_tool_plan(raw: str) -> list[ToolCall]:
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        payload = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return []

    allowed = {
        "validate_connection",
        "discover_schema",
        "generate_safe_sql",
        "run_approved_sql",
        "summarize_results",
    }
    calls: list[ToolCall] = []
    for item in payload.get("calls", []):
        name = item.get("name")
        if name in allowed:
            calls.append(ToolCall(name=name, reason=str(item.get("reason", ""))))
    return calls


def _complete_database_plan(calls: list[ToolCall]) -> list[ToolCall]:
    if not calls:
        return REQUIRED_DATABASE_FLOW

    by_name = {call.name: call for call in calls}
    completed: list[ToolCall] = []
    if "validate_connection" in by_name:
        completed.append(by_name["validate_connection"])

    for required in REQUIRED_DATABASE_FLOW:
        completed.append(by_name.get(required.name, required))
    return completed
