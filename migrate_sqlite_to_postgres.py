#!/usr/bin/env python3
"""Copy a SQLite database into PostgreSQL without deleting the SQLite source.

Usage:
  SQLITE_PATH=bot_data.db DATABASE_URL=... python migrate_sqlite_to_postgres.py

This is a one-time data-copy helper only. It does NOT switch SamiBOT's runtime
database connections; that requires a separately tested application migration.
Run it against a backup first, and do not point it at production until verified.
"""
import os
import sqlite3
import sys
from urllib.parse import urlparse, urlunparse

import psycopg2
from psycopg2 import sql


def pg_type(sqlite_type):
    t = (sqlite_type or "").upper()
    if "INT" in t:
        return "BIGINT"
    if any(x in t for x in ("CHAR", "CLOB", "TEXT")) or not t:
        return "TEXT"
    if "BLOB" in t:
        return "BYTEA"
    if any(x in t for x in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE PRECISION"
    if "BOOL" in t:
        return "BOOLEAN"
    if any(x in t for x in ("NUM", "DEC")):
        return "NUMERIC"
    return "TEXT"


def main():
    sqlite_path = os.environ.get("SQLITE_PATH", "bot_data.db")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("ERROR: DATABASE_URL is required.")
    if not os.path.isfile(sqlite_path):
        sys.exit(f"ERROR: SQLite source file not found: {sqlite_path}")

    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    tables = src.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """).fetchall()
    if not tables:
        sys.exit("ERROR: No user tables found in the SQLite source.")

    dst = psycopg2.connect(database_url)
    try:
        with dst:
            with dst.cursor() as cur:
                for table_row in tables:
                    table = table_row["name"]
                    columns = src.execute(
                        f"PRAGMA table_info({quote_sqlite_identifier(table)})"
                    ).fetchall()
                    if not columns:
                        continue

                    definitions = []
                    pk_columns = [c["name"] for c in columns if c["pk"]]
                    for col in columns:
                        name = col["name"]
                        typ = pg_type(col["type"])
                        definition = sql.SQL("{} {}").format(
                            sql.Identifier(name), sql.SQL(typ)
                        )
                        if col["notnull"]:
                            definition += sql.SQL(" NOT NULL")
                        if col["dflt_value"] is not None:
                            default = str(col["dflt_value"])
                            # Preserve only simple SQL literals/numeric defaults;
                            # complex SQLite expressions need manual review.
                            if default.upper() in ("NULL", "TRUE", "FALSE") or (
                                default.startswith("'") and default.endswith("'")
                            ) or default.replace(".", "", 1).lstrip("-").isdigit():
                                definition += sql.SQL(" DEFAULT ") + sql.SQL(default)
                        definitions.append(definition)

                    if pk_columns:
                        definitions.append(
                            sql.SQL("PRIMARY KEY ({})").format(
                                sql.SQL(", ").join(map(sql.Identifier, pk_columns))
                            )
                        )
                    cur.execute(
                        sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                            sql.Identifier(table), sql.SQL(", ").join(definitions)
                        )
                    )

                    # Copy rows idempotently: if rerun, existing primary-key rows
                    # are not overwritten. Review destination counts before cutover.
                    rows = src.execute(
                        f"SELECT * FROM {quote_sqlite_identifier(table)}"
                    ).fetchall()
                    names = [c["name"] for c in columns]
                    if not rows:
                        continue
                    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(map(sql.Identifier, names)),
                        sql.SQL(", ").join(sql.Placeholder() for _ in names),
                    )
                    if pk_columns:
                        insert += sql.SQL(" ON CONFLICT ({}) DO NOTHING").format(
                            sql.SQL(", ").join(map(sql.Identifier, pk_columns))
                        )
                    for row in rows:
                        cur.execute(insert, [row[name] for name in names])

                    # Set serial/identity-like integer primary keys forward if needed.
                    if len(pk_columns) == 1:
                        pk = next(c for c in columns if c["name"] == pk_columns[0])
                        if "INT" in (pk["type"] or "").upper():
                            cur.execute(
                                "SELECT setval(pg_get_serial_sequence(%s, %s), "
                                "COALESCE((SELECT MAX(" + '"' + pk_columns[0].replace('"', '""') +
                                '"' + ") FROM " + '"' + table.replace('"', '""') +
                                '"' + "), 1), true) "
                                "WHERE pg_get_serial_sequence(%s, %s) IS NOT NULL",
                                (table, pk_columns[0], table, pk_columns[0]),
                            )
        print(f"Copied {len(tables)} SQLite tables into PostgreSQL.")
        print("SQLite source was opened read-only and was not modified.")
        print("IMPORTANT: verify row counts and app compatibility before cutover.")
    finally:
        dst.close()
        src.close()


def quote_sqlite_identifier(value):
    return '"' + value.replace('"', '""') + '"'


if __name__ == "__main__":
    main()
