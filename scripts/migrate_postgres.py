"""Copy Price Lenses data between PostgreSQL databases without exposing DSNs.

Required environment variables (loaded from .env when present):
  PL_LOCAL_DATABASE_URL  source database
  PL_NEON_DIRECT_URL     empty destination database

The destination schema is bootstrapped with the application's idempotent schema,
but data migration refuses to run if any application table already contains rows.
"""
from __future__ import annotations

import os
from collections.abc import Iterable

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Jsonb

from price_lenses.db import Database


TABLES = (
    "products",
    "search_runs",
    "offers",
    "offer_details",
    "offer_promos",
    "canonical_products",
    "price_history",
    "price_behavior_corpus",
)


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _counts(con: psycopg.Connection) -> dict[str, int]:
    return {
        table: con.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()[0]
        for table in TABLES
    }


def _columns(con: psycopg.Connection, table: str) -> list[tuple[str, str]]:
    return con.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        [table],
    ).fetchall()


def _adapt_row(row: Iterable[object], column_types: list[str]) -> list[object]:
    return [Jsonb(value) if kind == "jsonb" and value is not None else value
            for value, kind in zip(row, column_types)]


def _integrity(con: psycopg.Connection) -> dict[str, int]:
    return {
        "orphan_offers": con.execute(
            """
            SELECT count(*) FROM offers o
            LEFT JOIN products p ON p.product_id = o.product_id
            LEFT JOIN search_runs r ON r.run_id = o.run_id
            WHERE p.product_id IS NULL OR r.run_id IS NULL
            """
        ).fetchone()[0],
        "orphan_details": con.execute(
            """
            SELECT count(*) FROM offer_details d
            LEFT JOIN offers o ON o.offer_id = d.offer_id
            WHERE o.offer_id IS NULL
            """
        ).fetchone()[0],
        "orphan_promotions": con.execute(
            """
            SELECT count(*) FROM offer_promos p
            LEFT JOIN offers o ON o.offer_id = p.offer_id
            WHERE o.offer_id IS NULL
            """
        ).fetchone()[0],
    }


def main() -> None:
    load_dotenv()
    source_url = _required("PL_LOCAL_DATABASE_URL")
    target_url = _required("PL_NEON_DIRECT_URL")
    if source_url == target_url:
        raise SystemExit("Source and destination database URLs must be different")

    # This only creates missing tables, indexes, and the latest_offers view.
    Database(target_url).close()

    with psycopg.connect(source_url) as source, psycopg.connect(target_url) as target:
        source_counts = _counts(source)
        target_before = _counts(target)
        if any(target_before.values()):
            details = ", ".join(f"{name}={count}" for name, count in target_before.items() if count)
            raise SystemExit(f"Destination is not empty; migration refused ({details})")

        with target.transaction():
            for table in TABLES:
                columns = _columns(source, table)
                if not columns:
                    raise SystemExit(f"Source table is missing: {table}")
                names = [name for name, _ in columns]
                kinds = [kind for _, kind in columns]
                select_query = sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(", ").join(map(sql.Identifier, names)), sql.Identifier(table)
                )
                rows = source.execute(select_query).fetchall()
                if not rows:
                    continue
                insert_query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(map(sql.Identifier, names)),
                    sql.SQL(", ").join(sql.Placeholder() for _ in names),
                )
                with target.cursor() as cursor:
                    cursor.executemany(insert_query, [_adapt_row(row, kinds) for row in rows])

        target_counts = _counts(target)
        if source_counts != target_counts:
            raise SystemExit(
                f"Count verification failed: source={source_counts}, destination={target_counts}"
            )
        integrity = _integrity(target)
        if any(integrity.values()):
            raise SystemExit(f"Referential-integrity verification failed: {integrity}")

    print("Migration completed and verified.")
    for table, count in source_counts.items():
        print(f"{table}: {count}")
    for check, count in integrity.items():
        print(f"{check}: {count}")


if __name__ == "__main__":
    main()
