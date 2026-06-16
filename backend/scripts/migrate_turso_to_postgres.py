from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import psycopg
except ImportError as exc:
    raise SystemExit(
        "Missing migration dependencies. Run: backend\\.venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt"
    ) from exc

from app.storage import LibsqlConnection, PostgresConnection, init_db


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

CONFLICT_COLUMNS = {
    "events": ["id"],
    "event_details_cache": ["event_id"],
    "provider_payload_cache": ["provider", "cache_key"],
    "provider_fetches": ["provider_name", "cache_key"],
    "users": ["id"],
    "user_sessions": ["token_hash"],
    "oauth_accounts": ["provider", "subject"],
    "oauth_states": ["state_hash"],
    "user_follows": ["user_id", "entity_id"],
    "user_settings": ["user_id"],
    "user_ui_state": ["user_id"],
    "push_subscriptions": ["id"],
    "notification_rules": ["id"],
    "sent_notifications": ["user_id", "event_id", "rule_id", "scheduled_for"],
    "entities": ["id"],
    "entity_aliases": ["entity_id", "alias"],
    "entity_provider_bindings": ["entity_id", "provider", "binding_type", "binding_value"],
    "event_provider_bindings": ["event_id", "provider", "binding_type"],
}


def main() -> None:
    turso_url = os.getenv("SOURCE_TURSO_DATABASE_URL", "").strip() or os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token = os.getenv("SOURCE_TURSO_AUTH_TOKEN", "").strip() or os.getenv("TURSO_AUTH_TOKEN", "").strip()
    target_url = os.getenv("TARGET_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()

    if not turso_url or not turso_token:
        raise SystemExit("Set SOURCE_TURSO_DATABASE_URL and SOURCE_TURSO_AUTH_TOKEN, or TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.")
    if not target_url:
        raise SystemExit("Set TARGET_DATABASE_URL or DATABASE_URL to the target Neon/Postgres connection string.")

    source = LibsqlConnection(turso_url, turso_token)
    target = PostgresConnection(target_url)

    try:
        init_db(target)
        target.commit()

        copied_total = 0
        for table in TABLES:
            copied = copy_table(source, target.connection, table)
            copied_total += copied
            print(f"{table}: {copied}")
        target.commit()
        print(f"Migration complete. Rows copied: {copied_total}")
    finally:
        source.close()
        target.close()


def copy_table(source: LibsqlConnection, target: psycopg.Connection[Any], table: str) -> int:
    rows = source.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0

    columns = list(rows[0].keys())
    conflict_columns = CONFLICT_COLUMNS[table]
    sql = build_upsert_sql(table, columns, conflict_columns)

    with target.cursor() as cursor:
        cursor.executemany(sql, [tuple(normalize_value(row[column]) for column in columns) for row in rows])
    target.commit()
    return len(rows)


def build_upsert_sql(table: str, columns: list[str], conflict_columns: list[str]) -> str:
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    placeholders = ", ".join("%s" for _ in columns)
    conflict_sql = ", ".join(quote_identifier(column) for column in conflict_columns)
    update_columns = [column for column in columns if column not in conflict_columns]
    if update_columns:
        update_sql = ", ".join(
            f"{quote_identifier(column)} = EXCLUDED.{quote_identifier(column)}" for column in update_columns
        )
        conflict_action = f"DO UPDATE SET {update_sql}"
    else:
        conflict_action = "DO NOTHING"
    return f"INSERT INTO {quote_identifier(table)} ({column_sql}) VALUES ({placeholders}) ON CONFLICT ({conflict_sql}) {conflict_action}"


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def normalize_value(value: Any) -> Any:
    if isinstance(value, memoryview):
        return value.tobytes()
    return value


if __name__ == "__main__":
    main()
