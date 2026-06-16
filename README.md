# db-insight

Local-first SQL insight CLI and MCP server for Postgres and SQLite.

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

Postgres:

```bash
DATABASE_URL=postgresql://readonly:password@localhost:5432/app_db
DB_INSIGHT_MODEL=gemma3:latest
DB_INSIGHT_OLLAMA_URL=http://localhost:11434
```

SQLite:

```bash
DATABASE_URL=sqlite:///absolute/path/to/app.sqlite
DB_INSIGHT_MODEL=gemma3:latest
DB_INSIGHT_OLLAMA_URL=http://localhost:11434
```

If your provider gives a URL with special characters, wrap it in quotes:

```bash
DATABASE_URL="postgresql://readonly:password@host.example.com:5432/app_db"
```

`DB_INSIGHT_MODEL` is configurable so you can use the best Gemma/Ollama tag available on
your machine.

## CLI

```bash
db-insight connect
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

## Remote MCP server

For team use, run the same server over HTTP:

```bash
DB_INSIGHT_MCP_HOST=0.0.0.0 \
DB_INSIGHT_MCP_PORT=8000 \
db-insight mcp --transport streamable-http
```

The HTTP MCP endpoint is:

```text
http://your-host:8000/mcp
```

Put it behind your existing auth layer, VPN, or private network. Do not expose a
database-backed MCP server directly to the public internet.

## Docker

Build the image:

```bash
docker build -t db-insight:latest .
```

Or use the published image:

```bash
docker pull ghcr.io/enclavex-labs/db-insight:latest
```

Install Gemma on the host Docker server:

```bash
ollama pull gemma3:latest
```

Configure your MCP client to launch the container over stdio.

Postgres:

```json
{
  "mcpServers": {
    "db-insight": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-e",
        "DATABASE_URL",
        "-e",
        "DB_INSIGHT_MODEL",
        "-e",
        "DB_INSIGHT_OLLAMA_URL",
        "ghcr.io/enclavex-labs/db-insight:latest"
      ],
      "env": {
        "DATABASE_URL": "postgresql://readonly:password@host.docker.internal:5432/app_db",
        "DB_INSIGHT_MODEL": "gemma3:latest",
        "DB_INSIGHT_OLLAMA_URL": "http://host.docker.internal:11434"
      }
    }
  }
}
```

SQLite:

```json
{
  "mcpServers": {
    "db-insight": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-v",
        "/absolute/path/to/data:/data:ro",
        "-e",
        "DATABASE_URL",
        "-e",
        "DB_INSIGHT_MODEL",
        "-e",
        "DB_INSIGHT_OLLAMA_URL",
        "ghcr.io/enclavex-labs/db-insight:latest"
      ],
      "env": {
        "DATABASE_URL": "sqlite:////data/app.sqlite",
        "DB_INSIGHT_MODEL": "gemma3:latest",
        "DB_INSIGHT_OLLAMA_URL": "http://host.docker.internal:11434"
      }
    }
  }
}
```

For VS Code/Copilot, use the same body under `servers` instead of `mcpServers`.

Each user fills `DATABASE_URL` with their own Postgres connection string or SQLite
file URL. For SQLite in Docker, mount the folder containing the database to `/data`
and use four slashes: `sqlite:////data/app.sqlite`.

For Postgres, if `DATABASE_URL` uses `localhost` or `127.0.0.1`, the Docker image
remaps it to `host.docker.internal` automatically.

If you need a long-running shared HTTP endpoint instead, run:

```bash
docker run --rm -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL=postgresql://readonly:password@host.docker.internal:5432/app_db \
  -e DB_INSIGHT_OLLAMA_URL=http://host.docker.internal:11434 \
  ghcr.io/enclavex-labs/db-insight:latest db-insight mcp --transport streamable-http
```
