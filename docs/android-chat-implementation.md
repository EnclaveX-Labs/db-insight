# Android Health Chat Implementation

This guide describes the production shape for an Android health-ring app where user data is stored locally in SQLite, synced to a Python backend, and answered by a model through a chat UI.

## Goal

The user should be able to ask questions like:

```text
What is my current blood pressure vs on sunny day?
```

The app should answer using the user's real health-ring data, without asking the user to run MCP, install a model, or manage database setup.

## Recommended Architecture

```text
Health ring
  -> Android app
  -> local SQLite cache
  -> sync API
  -> Python backend
  -> backend database
  -> context builder
  -> model API / model workers
  -> answer API
  -> Android chat UI
```

Use MCP for developer/admin tooling, not the Android hot path.

```text
Production:
Android -> FastAPI -> internal tools -> model

Developer/admin:
MCP -> same internal tools
```

## Android Responsibilities

The Android app should:

- Pair with the health ring.
- Store raw readings locally in SQLite.
- Sync new readings to the backend.
- Send user chat messages to the backend.
- Render assistant responses.
- Keep working offline for data capture.

The Android app should not:

- Run a large model.
- Run an MCP server.
- Generate arbitrary SQL from user messages.
- Send the whole SQLite database for every chat message.

## Local SQLite Storage

SQLite is the device cache and offline store.

Example tables:

```sql
CREATE TABLE health_readings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  metric_type TEXT NOT NULL,
  value_1 REAL NOT NULL,
  value_2 REAL,
  unit TEXT NOT NULL,
  measured_at TEXT NOT NULL,
  source_device_id TEXT NOT NULL,
  synced_at TEXT
);

CREATE TABLE weather_snapshots (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  condition TEXT NOT NULL,
  temperature_c REAL,
  humidity_percent REAL,
  recorded_at TEXT NOT NULL,
  synced_at TEXT
);

CREATE INDEX idx_health_readings_sync ON health_readings(synced_at);
CREATE INDEX idx_health_readings_metric_time ON health_readings(metric_type, measured_at);
CREATE INDEX idx_weather_time ON weather_snapshots(recorded_at);
```

For blood pressure:

```text
metric_type = "blood_pressure"
value_1 = systolic
value_2 = diastolic
unit = "mmHg"
```

When creating the SQLite DB, enable WAL:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
```

## Data Sync

The app should periodically sync unsynced rows.

```text
Android SQLite
  -> collect rows where synced_at is null
  -> POST /v1/sync/health-readings
  -> backend stores rows
  -> backend returns accepted IDs
  -> Android marks synced_at
```

Request:

```json
{
  "device_id": "ring_123",
  "readings": [
    {
      "id": "reading_001",
      "metric_type": "blood_pressure",
      "value_1": 122,
      "value_2": 78,
      "unit": "mmHg",
      "measured_at": "2026-06-16T09:10:00Z"
    }
  ]
}
```

Response:

```json
{
  "accepted_ids": ["reading_001"],
  "rejected": []
}
```

Keep sync idempotent. The backend should accept the same `id` twice without duplicating data.

## Chat API

Android sends the user message to the backend.

```http
POST /v1/chat
Authorization: Bearer <access_token>
Content-Type: application/json
```

Request:

```json
{
  "conversation_id": "conv_123",
  "message": "What is my current blood pressure vs on sunny day?",
  "timezone": "Asia/Kolkata"
}
```

Response:

```json
{
  "conversation_id": "conv_123",
  "message_id": "msg_456",
  "answer": "Your latest blood pressure is 122/78 mmHg. On sunny days over the last 90 days, your average was 118/76 mmHg across 18 readings. So today is slightly higher than your sunny-day average.",
  "facts": {
    "latest_blood_pressure": "122/78",
    "sunny_day_average_blood_pressure": "118/76",
    "sample_count": 18,
    "date_range": "last 90 days"
  }
}
```

The app renders `answer`. The `facts` object is optional but useful for debugging and explainability.

## Backend Responsibilities

The Python backend should:

- Authenticate the user.
- Store synced readings.
- Build compact health context for the question.
- Call the model with only the needed context.
- Return the final answer.
- Rate-limit and queue model calls.

The backend should not send the whole user database to the model.

## Backend Data Model

Use Postgres for production multi-user storage.

Minimal table:

```sql
CREATE TABLE health_readings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  metric_type TEXT NOT NULL,
  value_1 DOUBLE PRECISION NOT NULL,
  value_2 DOUBLE PRECISION,
  unit TEXT NOT NULL,
  measured_at TIMESTAMPTZ NOT NULL,
  source_device_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_health_user_metric_time
  ON health_readings(user_id, metric_type, measured_at DESC);
```

Weather can be stored directly or resolved server-side from location/time:

```sql
CREATE TABLE weather_snapshots (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  condition TEXT NOT NULL,
  temperature_c DOUBLE PRECISION,
  humidity_percent DOUBLE PRECISION,
  recorded_at TIMESTAMPTZ NOT NULL
);
```

## Context Builder

Do not let the model freely query everything first. Build the obvious context in code.

Example:

```python
def build_context(user_id: str, question: str) -> dict:
    if "blood pressure" in question.lower() and "sunny" in question.lower():
        return {
            "latest_blood_pressure": get_latest_blood_pressure(user_id),
            "sunny_day_average_blood_pressure": get_bp_average_for_weather(user_id, "sunny", days=90),
        }
    return {
        "recent_health_summary": get_recent_health_summary(user_id, days=30),
    }
```

Then call the model:

```python
def answer_chat(user_id: str, message: str) -> dict:
    context = build_context(user_id, message)
    answer = call_model(message=message, context=context)
    return {"answer": answer, "facts": context}
```

This is faster, safer, and easier to scale than dynamic SQL for every mobile request.

## Tool Layer

Keep tools as plain Python functions first:

```python
def get_latest_blood_pressure(user_id: str) -> dict:
    ...

def get_bp_average_for_weather(user_id: str, condition: str, days: int) -> dict:
    ...

def get_recent_sleep_summary(user_id: str, days: int) -> dict:
    ...
```

FastAPI uses these tools directly.

MCP can wrap the same tools later:

```text
core/tools.py
  -> used by FastAPI
  -> wrapped by MCP for developer/admin clients
```

## Model Scaling

For real users, use a managed model API first.

```text
FastAPI
  -> concurrency limit
  -> managed LLM API
```

Add a queue when traffic grows:

```text
FastAPI
  -> Redis / queue
  -> model workers
  -> model provider
```

At minimum, protect the model with a semaphore:

```python
model_slots = asyncio.Semaphore(10)

async def safe_model_call(payload):
    async with model_slots:
        return await call_model(payload)
```

## Privacy And Safety

Health data is sensitive.

Required basics:

- TLS for all APIs.
- Auth on every request.
- Per-user authorization checks before every query.
- Encryption at rest for backend storage.
- No raw health data in logs.
- Short model prompts with only needed facts.
- User consent for cloud processing.
- Clear delete/export flows.

For health advice, the assistant should avoid diagnosis and recommend professional care for concerning readings.

## When To Use MCP

Use MCP when:

- Developers need to inspect a database with natural language.
- Admin tools need dynamic database/tool access.
- You want Claude/Cursor/Codex-style clients to call your tools.
- You are prototyping new tool behavior.

Do not require MCP for:

- Android app chat.
- Normal user data sync.
- Every production model request.

## First Version Checklist

1. Android stores ring readings in SQLite.
2. Android syncs unsynced readings to FastAPI.
3. Backend stores readings in Postgres.
4. Android sends chat messages to `/v1/chat`.
5. Backend builds context with plain Python tools.
6. Backend calls managed model API.
7. Backend returns answer plus compact facts.
8. Add MCP only as a developer/admin wrapper around the same tools.

