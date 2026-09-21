"""PostgreSQL storage. Offers are append-only, providing an audit trail and price history."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

import psycopg
from psycopg.types.json import Jsonb

from .models import Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id VARCHAR PRIMARY KEY, canonical_title TEXT, brand VARCHAR,
    asin VARCHAR, gtin VARCHAR, created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS search_runs (
    run_id VARCHAR PRIMARY KEY, query TEXT NOT NULL, provider VARCHAR NOT NULL,
    status VARCHAR NOT NULL, error TEXT, result_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS offers (
    offer_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL REFERENCES search_runs(run_id),
    product_id VARCHAR NOT NULL REFERENCES products(product_id),
    provider VARCHAR NOT NULL, marketplace VARCHAR NOT NULL, external_id VARCHAR,
    title TEXT NOT NULL, url TEXT, price NUMERIC(14,2), currency VARCHAR(3),
    original_price NUMERIC(14,2), rating NUMERIC(4,2), review_count BIGINT,
    seller_name VARCHAR, seller_rating NUMERIC(4,2), availability VARCHAR,
    shipping TEXT, offers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb, fetched_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offers_product ON offers(product_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_offers_run ON offers(run_id);
CREATE TABLE IF NOT EXISTS offer_details (
    offer_id VARCHAR PRIMARY KEY REFERENCES offers(offer_id) ON DELETE CASCADE,
    seller_id VARCHAR, price_with_offers NUMERIC(14,2), is_assured BOOLEAN,
    cod_available BOOLEAN, no_cost_emi BOOLEAN, return_policy TEXT,
    delivery_by TEXT, warranty TEXT, item_condition VARCHAR
);
CREATE TABLE IF NOT EXISTS offer_promos (
    promo_id VARCHAR PRIMARY KEY,
    offer_id VARCHAR NOT NULL REFERENCES offers(offer_id) ON DELETE CASCADE,
    promo_type VARCHAR NOT NULL, bank VARCHAR, card_type VARCHAR,
    description TEXT NOT NULL, amount NUMERIC(14,2), percent NUMERIC(7,3),
    is_emi BOOLEAN NOT NULL DEFAULT FALSE, source VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_promos_offer ON offer_promos(offer_id);
CREATE TABLE IF NOT EXISTS canonical_products (
    canonical_id VARCHAR PRIMARY KEY, asin VARCHAR, ean VARCHAR, upc VARCHAR,
    mpn VARCHAR, brand VARCHAR, model VARCHAR, title TEXT, category VARCHAR,
    history_url TEXT, created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS price_history (
    canonical_id VARCHAR NOT NULL, recorded_on DATE NOT NULL,
    price NUMERIC(14,2) NOT NULL, currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    source VARCHAR NOT NULL, fetched_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_id, recorded_on, source)
);
CREATE INDEX IF NOT EXISTS idx_price_history_product
    ON price_history(canonical_id, recorded_on DESC);
CREATE TABLE IF NOT EXISTS price_behavior_corpus (
    chunk_id VARCHAR PRIMARY KEY, canonical_id VARCHAR NOT NULL,
    section VARCHAR NOT NULL, text TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL
);
CREATE OR REPLACE VIEW latest_offers AS
SELECT offer_id, run_id, product_id, provider, marketplace, external_id, title, url,
       price, currency, original_price, rating, review_count, seller_name, seller_rating,
       availability, shipping, offers_json, raw_json, fetched_at
FROM (
    SELECT o.*, ROW_NUMBER() OVER (
        PARTITION BY product_id, marketplace, COALESCE(seller_name, '')
        ORDER BY fetched_at DESC, offer_id DESC
    ) AS rn
    FROM offers o
) ranked
WHERE rn = 1;
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _native(value):
    """Keep the service/UI contract while PostgreSQL uses exact NUMERIC values."""
    return float(value) if isinstance(value, Decimal) else value


class Database:
    def __init__(self, database_url: str, *, schema: str = "public"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
            raise ValueError("Invalid PostgreSQL schema name")
        self.database_url = database_url
        self.schema = schema
        self.con = psycopg.connect(database_url, autocommit=True)
        if schema != "public":
            self.con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        self.con.execute(f'SET search_path TO "{schema}"')
        self.con.execute(SCHEMA)

    def close(self) -> None:
        self.con.close()

    def start_run(self, query: str, provider: str) -> str:
        run_id = uuid.uuid4().hex
        self.con.execute(
            "INSERT INTO search_runs VALUES (%s, %s, %s, 'running', NULL, 0, %s)",
            [run_id, query, provider, _now()])
        return run_id

    def finish_run(self, run_id: str, count: int, error: str | None = None,
                   status: str | None = None) -> None:
        self.con.execute(
            "UPDATE search_runs SET status=%s, error=%s, result_count=%s WHERE run_id=%s",
            [status or ("error" if error else "ok"), error, count, run_id])

    def known_products(self) -> dict[str, str]:
        rows = self.con.execute("SELECT product_id, canonical_title FROM products").fetchall()
        from .normalize import normalize_title
        return {pid: normalize_title(t or "") for pid, t in rows}

    def upsert_product(self, product_id: str, offer: Offer) -> None:
        self.con.execute(
            """
            INSERT INTO products (product_id, canonical_title, brand, asin, gtin, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (product_id) DO UPDATE SET
                brand = COALESCE(products.brand, EXCLUDED.brand),
                gtin = COALESCE(products.gtin, EXCLUDED.gtin),
                asin = COALESCE(products.asin, EXCLUDED.asin)
            """,
            [product_id, offer.title, offer.brand, offer.asin, offer.gtin, _now()])

    def insert_offers(self, run_id: str, rows: Iterable[tuple[str, Offer]]) -> int:
        now, n = _now(), 0
        with self.con.transaction():
            for product_id, o in rows:
                offer_id = uuid.uuid4().hex
                self.con.execute(
                    """
                    INSERT INTO offers (
                        offer_id, run_id, product_id, provider, marketplace, external_id,
                        title, url, price, currency, original_price, rating, review_count,
                        seller_name, seller_rating, availability, shipping, offers_json,
                        raw_json, fetched_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    [offer_id, run_id, product_id, o.provider, o.marketplace, o.external_id,
                     o.title, o.url, o.price, o.currency, o.original_price, o.rating,
                     o.review_count, o.seller_name, o.seller_rating, o.availability,
                     o.shipping, Jsonb(o.offers),
                     Jsonb(o.raw, dumps=lambda value: json.dumps(value, default=str)), now])
                if any(v is not None for v in (
                    o.seller_id, o.price_with_offers, o.is_assured, o.cod_available,
                    o.no_cost_emi, o.return_policy, o.delivery_by, o.warranty, o.condition,
                )):
                    self.con.execute(
                        "INSERT INTO offer_details VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [offer_id, o.seller_id, o.price_with_offers, o.is_assured,
                         o.cod_available, o.no_cost_emi, o.return_policy, o.delivery_by,
                         o.warranty, o.condition])
                for p in o.promos:
                    self.con.execute(
                        "INSERT INTO offer_promos VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [uuid.uuid4().hex, offer_id, p.promo_type, p.bank, p.card_type,
                         p.description, p.amount, p.percent, p.is_emi, p.source])
                n += 1
        return n

    def compare(self, query: str, limit: int = 50):
        rows = self.con.execute(
            """
            SELECT o.product_id, o.title, o.marketplace, o.seller_name, o.price, o.currency,
                   o.original_price, o.rating, o.review_count, o.availability, o.shipping,
                   o.offers_json, o.url, o.provider
            FROM latest_offers o
            WHERE o.product_id IN (
                SELECT DISTINCT f.product_id FROM offers f
                JOIN search_runs r ON r.run_id = f.run_id
                WHERE lower(r.query) = lower(%s)
            )
            ORDER BY o.product_id, o.price NULLS LAST LIMIT %s
            """, [query, limit]).fetchall()
        return [tuple(_native(v) for v in row) for row in rows]

    def price_history(self, product_id: str):
        rows = self.con.execute(
            "SELECT fetched_at, marketplace, seller_name, price, currency "
            "FROM offers WHERE product_id = %s ORDER BY fetched_at", [product_id]).fetchall()
        return [tuple(_native(v) for v in row) for row in rows]

    def _rows(self, sql: str, params: list | None = None) -> list[dict]:
        cur = self.con.execute(sql, params or [])
        cols = [d.name for d in cur.description]
        return [{k: _native(v) for k, v in zip(cols, row)} for row in cur.fetchall()]

    def recent_queries(self, limit: int = 25) -> list[dict]:
        return self._rows(
            "SELECT query, max(started_at) AS last_run, sum(result_count) AS offers "
            "FROM search_runs GROUP BY query ORDER BY last_run DESC LIMIT %s", [limit])

    def offers_for_query(self, query: str) -> list[dict]:
        return self._rows(
            """
            SELECT o.offer_id, o.product_id, p.canonical_title AS product_title, p.asin,
                   o.title, o.marketplace, o.seller_name, o.seller_rating, o.price, o.currency,
                   o.original_price, o.rating, o.review_count, o.availability,
                   o.shipping, o.offers_json, o.url, o.provider, o.fetched_at,
                   d.seller_id, d.price_with_offers, d.is_assured, d.cod_available,
                   d.no_cost_emi, d.return_policy, d.delivery_by, d.warranty
            FROM latest_offers o JOIN products p ON p.product_id = o.product_id
            LEFT JOIN offer_details d ON d.offer_id = o.offer_id
            WHERE o.product_id IN (
                SELECT DISTINCT f.product_id FROM offers f
                JOIN search_runs r ON r.run_id = f.run_id
                WHERE lower(r.query) = lower(%s)
            ) ORDER BY o.price NULLS LAST
            """, [query])

    def promos_for_query(self, query: str) -> list[dict]:
        return self._rows(
            """
            SELECT o.offer_id, o.product_id, p.canonical_title AS product_title,
                   o.external_id, o.url, o.provider, o.marketplace, o.seller_name, o.price,
                   pr.promo_type, pr.bank, pr.card_type, pr.description, pr.amount, pr.percent,
                   pr.is_emi, pr.source
            FROM latest_offers o
            JOIN products p ON p.product_id = o.product_id
            JOIN offer_promos pr ON pr.offer_id = o.offer_id
            WHERE o.product_id IN (
                SELECT DISTINCT f.product_id FROM offers f
                JOIN search_runs r ON r.run_id = f.run_id
                WHERE lower(r.query) = lower(%s)
            ) ORDER BY o.price NULLS LAST, pr.promo_type, pr.amount DESC NULLS LAST
            """, [query])

    def history_for_product(self, product_id: str) -> list[dict]:
        return self._rows(
            "SELECT fetched_at, marketplace, seller_name, price, currency "
            "FROM offers WHERE product_id = %s AND price IS NOT NULL ORDER BY fetched_at",
            [product_id])

    def upsert_canonical_product(self, p: dict) -> None:
        self.con.execute(
            """
            INSERT INTO canonical_products VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (canonical_id) DO UPDATE SET
                asin=EXCLUDED.asin, ean=EXCLUDED.ean, upc=EXCLUDED.upc,
                mpn=EXCLUDED.mpn, brand=EXCLUDED.brand, model=EXCLUDED.model,
                title=EXCLUDED.title, category=EXCLUDED.category,
                history_url=EXCLUDED.history_url
            """,
            [p["canonical_id"], p.get("asin"), p.get("ean"), p.get("upc"), p.get("mpn"),
             p.get("brand"), p.get("model"), p.get("title"), p.get("category"),
             p.get("history_url"), _now()])

    def get_canonical_product(self, canonical_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM canonical_products WHERE canonical_id = %s", [canonical_id])
        return rows[0] if rows else None

    def save_price_history(self, canonical_id: str, points, source: str,
                           currency: str = "INR") -> int:
        now = _now()
        rows = [[canonical_id, day, price, currency, source, now] for day, price in points]
        if rows:
            with self.con.transaction():
                self.con.executemany(
                    """
                    INSERT INTO price_history VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (canonical_id, recorded_on, source) DO UPDATE SET
                        price=EXCLUDED.price, currency=EXCLUDED.currency,
                        fetched_at=EXCLUDED.fetched_at
                    """, rows)
        return len(rows)

    def get_price_series(self, canonical_id: str, as_of=None) -> list[tuple]:
        sql, params = "SELECT recorded_on, price FROM price_history WHERE canonical_id = %s", [canonical_id]
        if as_of is not None:
            sql, params = sql + " AND recorded_on <= %s", params + [as_of]
        return [(day, float(price)) for day, price in self.con.execute(
            sql + " ORDER BY recorded_on", params).fetchall()]

    def price_point_exists(self, canonical_id: str, day) -> bool:
        return self.con.execute(
            "SELECT 1 FROM price_history WHERE canonical_id = %s AND recorded_on = %s LIMIT 1",
            [canonical_id, day]).fetchone() is not None

    def save_corpus_chunks(self, canonical_id: str, chunks: list[dict]) -> None:
        with self.con.transaction():
            self.con.execute("DELETE FROM price_behavior_corpus WHERE canonical_id = %s", [canonical_id])
            now = _now()
            for c in chunks:
                self.con.execute(
                    "INSERT INTO price_behavior_corpus VALUES (%s,%s,%s,%s,%s,%s)",
                    [c["chunk_id"], canonical_id, c["section"], c["text"],
                     Jsonb(c.get("metadata", {})), now])

    def get_corpus_chunks(self, canonical_id: str) -> list[dict]:
        rows = self._rows(
            "SELECT chunk_id, section, text, metadata_json FROM price_behavior_corpus "
            "WHERE canonical_id = %s ORDER BY chunk_id", [canonical_id])
        for row in rows:
            metadata = row.pop("metadata_json") or {}
            row["metadata"] = metadata if isinstance(metadata, dict) else json.loads(metadata)
            row["canonical_id"] = canonical_id
        return rows
