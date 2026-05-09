#!/usr/bin/env python3
"""
migrate.py — Apply the moonshine Postgres schema.

Usage:
    export MOONSHINE_PG_DSN="postgresql://user:pass@localhost/moonshine"
    python adapters/postgres/migrate.py

Options:
    --dry-run   Print SQL without executing
    --reset     DROP SCHEMA moonshine CASCADE before re-creating (destructive!)
"""

import argparse
import os
import sys
from pathlib import Path

SCHEMA_SQL = Path(__file__).parent / "schema.sql"


def main():
    parser = argparse.ArgumentParser(description="Apply moonshine Postgres schema")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL only")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate schema")
    args = parser.parse_args()

    dsn = os.environ.get("MOONSHINE_PG_DSN", "")
    if not dsn:
        print("Error: MOONSHINE_PG_DSN is not set.", file=sys.stderr)
        sys.exit(1)

    sql = SCHEMA_SQL.read_text()

    if args.reset:
        schema = os.environ.get("MOONSHINE_PG_SCHEMA", "moonshine")
        reset_sql = f"DROP SCHEMA IF EXISTS {schema} CASCADE;\n"
        sql = reset_sql + sql

    if args.dry_run:
        print(sql)
        return

    try:
        import psycopg2
    except ImportError:
        try:
            import psycopg as psycopg2  # type: ignore[no-redef]
        except ImportError:
            print("Error: psycopg2 or psycopg3 required.", file=sys.stderr)
            sys.exit(1)

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.close()
    print("Schema applied successfully.")


if __name__ == "__main__":
    main()
