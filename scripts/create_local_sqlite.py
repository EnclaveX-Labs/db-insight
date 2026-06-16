from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path


DB_PATH = Path("data/abhishek_fitbit.sqlite")
USER_ID = "abhishek"


def main() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS heart_rate_samples;
            DROP TABLE IF EXISTS activity_daily;
            DROP TABLE IF EXISTS sleep_sessions;
            DROP TABLE IF EXISTS users;

            CREATE TABLE users (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              source TEXT NOT NULL
            );

            CREATE TABLE sleep_sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id TEXT NOT NULL REFERENCES users(id),
              sleep_date TEXT NOT NULL,
              sleep_start TEXT NOT NULL,
              wake_time TEXT NOT NULL,
              total_sleep_minutes INTEGER NOT NULL,
              deep_sleep_minutes INTEGER NOT NULL,
              rem_sleep_minutes INTEGER NOT NULL,
              sleep_score INTEGER NOT NULL
            );

            CREATE TABLE activity_daily (
              user_id TEXT NOT NULL REFERENCES users(id),
              activity_date TEXT NOT NULL,
              steps INTEGER NOT NULL,
              active_minutes INTEGER NOT NULL,
              calories_burned INTEGER NOT NULL,
              resting_heart_rate INTEGER NOT NULL,
              PRIMARY KEY (user_id, activity_date)
            );

            CREATE TABLE heart_rate_samples (
              user_id TEXT NOT NULL REFERENCES users(id),
              recorded_at TEXT NOT NULL,
              bpm INTEGER NOT NULL,
              zone TEXT NOT NULL,
              PRIMARY KEY (user_id, recorded_at)
            );
            """
        )
        conn.execute("INSERT INTO users VALUES (?, ?, ?)", (USER_ID, "Abhishek", "fitbit"))

        start_day = date.today() - timedelta(days=6)
        for offset in range(7):
            day = start_day + timedelta(days=offset)
            sleep_start = datetime.combine(day - timedelta(days=1), time(23, 5)) + timedelta(minutes=offset * 7)
            wake = datetime.combine(day, time(6, 50)) + timedelta(minutes=offset * 5)
            total = int((wake - sleep_start).total_seconds() // 60)
            conn.execute(
                "INSERT INTO sleep_sessions (user_id, sleep_date, sleep_start, wake_time, total_sleep_minutes, deep_sleep_minutes, rem_sleep_minutes, sleep_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (USER_ID, day.isoformat(), sleep_start.isoformat(), wake.isoformat(), total, 82 + offset, 96 - offset, 78 + offset % 5),
            )
            conn.execute(
                "INSERT INTO activity_daily VALUES (?, ?, ?, ?, ?, ?)",
                (USER_ID, day.isoformat(), 7600 + offset * 420, 42 + offset * 3, 2180 + offset * 35, 61 + offset % 4),
            )
            for hour in range(24):
                bpm = 58 + (hour in range(8, 22)) * 14 + (hour in range(17, 20)) * 18 + offset % 3
                zone = "rest" if bpm < 70 else "active" if bpm < 95 else "fat_burn"
                conn.execute(
                    "INSERT INTO heart_rate_samples VALUES (?, ?, ?, ?)",
                    (USER_ID, datetime.combine(day, time(hour)).isoformat(), bpm, zone),
                )

        assert conn.execute("SELECT COUNT(*) FROM heart_rate_samples").fetchone()[0] == 168


if __name__ == "__main__":
    main()
