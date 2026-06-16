from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import libsql
    import psycopg
    from psycopg.rows import dict_row
except ImportError as exc:
    raise SystemExit(
        "Missing migration dependencies. Run: backend\\.venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt"
    ) from exc

from app.storage import LibsqlConnection, init_db


TABLES = [
    "events",
    "event_details_cache",
    "provider_payload_cache",
    "provider_fetches",
    "users",
    "user_sessions",
    "oauth_accounts",
    "oauth_states",
    "user_follows",
    "user_settings",
    "user_ui_state",
    "push_subscriptions",
    "notification_rules",
    "sent_notifications",
    "entities",
    "entity_aliases",
    "entity_provider_bindings",
    "event_provider_bindings",
]


def main() -> None:
    source_url = os.getenv("SOURCE_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    turso_url = os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()

    if not source_url:
        raise SystemExit("Set SOURCE_DATABASE_URL or DATABASE_URL to the old Neon/Postgres connection string.")
    if not turso_url or not turso_token:
        raise SystemExit("Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN for the target Turso database.")

    target = LibsqlConnection(turso_url, turso_token)

    try:
        init_db(target)
        copied_total = 0
        for table in TABLES:
            copied = copy_table(source_url, target, table)
            copied_total += copied
            print(f"{table}: {copied}")
        target.commit()
        print(f"Migration complete. Rows copied: {copied_total}")
    finally:
        target.close()


def copy_table(source_url: str, target: LibsqlConnection, table: str) -> int:
    rows = read_source_table(source_url, table)
    if not rows:
        return 0

    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    sql = f"INSERT OR REPLACE INTO {table} ({column_sql}) VALUES ({placeholders})"

    for row in rows:
        target.execute(sql, [normalize_value(row[column]) for column in columns])
    target.commit()
    return len(rows)


def read_source_table(source_url: str, table: str) -> list[dict[str, Any]]:
    for attempt in range(1, 4):
        source = None
        try:
            # Neon may terminate long-lived connections when a free project is
            # throttled or restarting. Keep source connections short: read one
            # table, close the connection, then write that table to Turso.
            source = psycopg.connect(source_url, row_factory=dict_row, autocommit=True)
            return list(source.execute(f"SELECT * FROM {table}").fetchall())
        except psycopg.Error:
            if attempt == 3:
                raise
            time.sleep(attempt * 2)
        finally:
            if source is not None:
                source.close()
    return []


def normalize_value(value: Any) -> Any:
    if isinstance(value, memoryview):
        return value.tobytes()
    return value


if __name__ == "__main__":
    main()
