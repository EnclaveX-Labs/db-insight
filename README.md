# db-insight

Local-first Postgres insight CLI and MCP server.

Positioning: connect a read-only replica, analytics database, or staging database. Keep
credentials on your machine while the tool discovers schema, generates safe SQL, previews
it for approval, runs the query, and summarizes the result.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

Create `.env`:

```bash
DATABASE_URL=postgresql://readonly:password@localhost:5432/app_db
DB_INSIGHT_MODEL_PROVIDER=ollama
DB_INSIGHT_MODEL=gemma3:latest
DB_INSIGHT_OLLAMA_URL=http://localhost:11434
```

If your provider gives a URL with special characters, wrap it in quotes:

```bash
DATABASE_URL="postgresql://readonly:password@host.example.com:5432/app_db"
```

`DB_INSIGHT_MODEL` is configurable so you can use the best Gemma/Ollama tag available on
your machine.

To try Gemini instead:

```bash
GEMINI_API_KEY=your-key
DB_INSIGHT_MODEL_PROVIDER=gemini
DB_INSIGHT_GEMINI_MODEL=gemini-2.5-pro
```

If `GEMINI_API_KEY` is present and `DB_INSIGHT_MODEL_PROVIDER` is omitted, Gemini is used
automatically.

## CLI

```bash
db-insight connect postgres
db-insight schema --refresh
db-insight ask "Why did revenue drop last week?"
```

By default, `ask` uses the same MCP-style flow as the server:

```text
user question
→ LLM plans tool calls
→ discover_schema / schema memory
→ generate_safe_sql
→ preview SQL for approval
→ run_approved_sql
→ summarize_results
```

The user-facing CLI previews generated SQL and asks before running it.

`db-insight schema --refresh` stores a local schema memory snapshot in
`.db-insight/schema_memory.json`. Later questions reuse that schema picture so the model
does not need to rediscover the same database structure every time. The memory contains
metadata only, not table rows.

## MCP stdio server

```bash
db-insight mcp
```

This exposes safe local tools over stdio for MCP clients:

- `plan_tool_calls`
- `discover_schema`
- `refresh_schema_memory`
- `schema_memory_status`
- `catalog_overview`
- `inspect_table`
- `explain_sql`
- `generate_safe_sql`
- `run_approved_sql`
- `ask_database`

In a full MCP client, the LLM decides which tools to call. `ask_database` is a
convenience tool that performs that decision loop inside this local server while
still requiring explicit approval before execution.
