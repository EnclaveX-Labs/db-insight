import sqlite3

from db_insight.db import SQLiteClient, build_db_client


def test_sqlite_client_reads_schema_and_rows(tmp_path) -> None:
    path = tmp_path / "ring.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("create table sleep_sessions (id integer primary key, sleep_start text)")
        conn.execute("insert into sleep_sessions (sleep_start) values ('23:15')")

    db = build_db_client(f"sqlite:///{path}")

    assert isinstance(db, SQLiteClient)
    assert "main.sleep_sessions" in db.schema_overview().tables
    assert db.run_query("select sleep_start from sleep_sessions") == [{"sleep_start": "23:15"}]
