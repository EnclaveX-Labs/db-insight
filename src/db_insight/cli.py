from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from db_insight.agent import InsightAgent
from db_insight.config import load_settings
from db_insight.db import PostgresClient
from db_insight.errors import DbInsightError
from db_insight.memory import MemoryBackedPostgresClient, SchemaMemory
from db_insight.models import build_model_client

app = typer.Typer(help="Chat with Postgres safely from your local machine.")
console = Console()


def build_agent() -> InsightAgent:
    settings = load_settings()
    db = MemoryBackedPostgresClient(settings.database_url, settings.query_timeout_seconds)
    model = build_model_client(settings)
    return InsightAgent(db, model, settings.default_limit)


@app.command()
def connect(kind: str = typer.Argument("postgres")) -> None:
    """Validate the local database connection."""
    if kind != "postgres":
        raise typer.BadParameter("Only postgres is supported in this MVP.")

    try:
        settings = load_settings()
        db = PostgresClient(settings.database_url, settings.query_timeout_seconds)
        info = db.validate_readonly()
    except DbInsightError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print("[green]Connected to Postgres.[/green]")
    for key, value in info.items():
        console.print(f"{key}: {value}")


@app.command()
def schema(
    raw: bool = typer.Option(False, "--raw", help="Print raw table/column schema."),
    refresh: bool = typer.Option(False, "--refresh", help="Refresh local schema memory."),
) -> None:
    """Print a useful database schema overview."""
    try:
        settings = load_settings()
        db = MemoryBackedPostgresClient(
            settings.database_url,
            settings.query_timeout_seconds,
            prefer_memory=not refresh,
        )
        if refresh:
            db.refresh_schema_memory()
        output = db.schema_prompt() if raw else db.schema_overview_text()
        console.print(output or "[yellow]No user tables found.[/yellow]")
    except DbInsightError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def memory() -> None:
    """Show local schema memory status."""
    try:
        settings = load_settings()
        status = SchemaMemory().status(settings.database_url)
    except DbInsightError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not status["exists"]:
        console.print("[yellow]No schema memory yet.[/yellow]")
        console.print("Run: db-insight schema --refresh")
        return

    console.print("[green]Schema memory found.[/green]")
    console.print(f"path: {status['path']}")
    console.print(f"matches_database: {status['matches_database']}")
    console.print(f"captured_at: {status['captured_at']}")
    console.print(f"table_count: {status['table_count']}")


@app.command()
def catalog() -> None:
    """Show Postgres catalog stats for user tables."""
    try:
        settings = load_settings()
        db = MemoryBackedPostgresClient(settings.database_url, settings.query_timeout_seconds)
        rows = db.table_catalog()
    except DbInsightError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title="Catalog")
    for column in ("schema_name", "table_name", "approximate_rows", "seq_scan", "idx_scan"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column)) for column in ("schema_name", "table_name", "approximate_rows", "seq_scan", "idx_scan")))
    console.print(table)


@app.command()
def inspect(table_name: str) -> None:
    """Show columns, indexes, and constraints for a table."""
    try:
        settings = load_settings()
        db = MemoryBackedPostgresClient(settings.database_url, settings.query_timeout_seconds)
        details = db.table_details(table_name)
    except DbInsightError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[bold]{details['table']}[/bold]")
    console.print("\n[bold]Columns[/bold]")
    for column in details["columns"]:
        console.print(f"- {column['column_name']} {column['data_type']}")
    console.print("\n[bold]Indexes[/bold]")
    for index in details["indexes"]:
        console.print(f"- {index['indexname']}: {index['indexdef']}")
    console.print("\n[bold]Constraints[/bold]")
    for constraint in details["constraints"]:
        console.print(f"- {constraint['constraint_name']}: {constraint['definition']}")


@app.command()
def ai() -> None:
    """Validate the configured AI provider connection."""
    try:
        settings = load_settings()
        model = build_model_client(settings)
        health = model.health()
    except DbInsightError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Connected to {settings.model_provider}.[/green]")
    if "url" in health:
        console.print(f"url: {health['url']}")
    console.print(f"model: {health['model']}")
    if health.get("model_available"):
        console.print("[green]Model is available.[/green]")
    else:
        console.print("[yellow]Configured model is not available yet.[/yellow]")
        console.print(f"Run: ollama pull {health['model']}")


@app.command()
def ask(
    question: str,
    yes: bool = typer.Option(False, "--yes", "-y", help="Run generated SQL without prompt."),
) -> None:
    """Ask a question through the MCP-style tool decision flow."""
    try:
        agent = build_agent()
        decision = agent.answer_with_tools(question, approved=False)
    except DbInsightError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print("\n[bold]MCP tool plan[/bold]")
    for call in decision["plan"]:
        console.print(f"- {call['name']}: {call['reason']}")

    sql = decision["sql"]
    console.print("\n[bold]Generated SQL[/bold]")
    console.print(sql)

    if not yes and not typer.confirm("Run this read-only query?"):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit()

    try:
        rows, summary = agent.run_and_summarize(question, sql)
    except DbInsightError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print("\n[bold]Result summary[/bold]")
    console.print(summary)

    if rows:
        table = Table(title="Rows")
        for column in rows[0].keys():
            table.add_column(str(column))
        for row in rows[:20]:
            table.add_row(*(str(value) for value in row.values()))
        console.print(table)
    else:
        console.print("[yellow]No rows returned.[/yellow]")


@app.command()
def mcp() -> None:
    """Run the local MCP stdio server."""
    from db_insight.mcp_server import main

    main()
