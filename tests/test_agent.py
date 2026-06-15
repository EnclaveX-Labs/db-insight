from db_insight.agent import _complete_database_plan
from db_insight.tools import ToolCall


def test_complete_database_plan_adds_required_steps() -> None:
    plan = _complete_database_plan([ToolCall("discover_schema", "Need schema")])

    assert [call.name for call in plan] == [
        "discover_schema",
        "generate_safe_sql",
        "run_approved_sql",
        "summarize_results",
    ]
