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
    from psycopg.rows import dict_row
except ImportError as exc:
    raise SystemExit(
        "Missing migration dependencies. Run: backend\\.venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt"
    ) from exc

from app.storage import PostgresConnection, init_db
from scripts.migrate_turso_to_postgres import CONFLICT_COLUMNS, TABLES, build_upsert_sql, normalize_value


def main() -> None:
    source_url = os.getenv("SOURCE_DATABASE_URL", "").strip()
    target_url = os.getenv("TARGET_DATABASE_URL", "").strip()

    if not source_url:
        raise SystemExit("Set SOURCE_DATABASE_URL to the source Neon/Postgres connection string.")
    if not target_url:
        raise SystemExit("Set TARGET_DATABASE_URL to the target Neon/Postgres connection string.")
    if source_url == target_url:
        raise SystemExit("SOURCE_DATABASE_URL and TARGET_DATABASE_URL must be different.")

    source = psycopg.connect(source_url, row_factory=dict_row, autocommit=True)
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


def copy_table(source: psycopg.Connection[Any], target: psycopg.Connection[Any], table: str) -> int:
    rows = list(source.execute(f"SELECT * FROM {table}").fetchall())
    if not rows:
        return 0

    columns = list(rows[0].keys())
    sql = build_upsert_sql(table, columns, CONFLICT_COLUMNS[table])

    with target.cursor() as cursor:
        cursor.executemany(sql, [tuple(normalize_value(row[column]) for column in columns) for row in rows])
    target.commit()
    return len(rows)


if __name__ == "__main__":
    main()
