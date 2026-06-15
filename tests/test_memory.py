from db_insight.db import Column, SchemaOverview
from db_insight.memory import SchemaMemory


def test_schema_memory_round_trip(tmp_path) -> None:
    memory = SchemaMemory(tmp_path / "schema_memory.json")
    overview = SchemaOverview(
        table_count=1,
        schemas=["public"],
        tables={
            "public.users": [
                Column("public", "users", "id", "uuid", False),
                Column("public", "users", "created_at", "timestamp", True),
            ]
        },
    )

    memory.save("postgres://example/db", overview)
    loaded = memory.load("postgres://example/db")

    assert loaded is not None
    assert loaded.table_count == 1
    assert loaded.has_column("users", "created_at")


def test_schema_memory_ignores_other_database(tmp_path) -> None:
    memory = SchemaMemory(tmp_path / "schema_memory.json")
    overview = SchemaOverview(table_count=0, schemas=[], tables={})

    memory.save("postgres://example/db1", overview)

    assert memory.load("postgres://example/db2") is None
