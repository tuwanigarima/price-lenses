#!/usr/bin/env python3
"""Import products.json into local Agent 1/2 tables without deleting data.

All usable records populate the Agent 1 product catalog. Aggregate historical
fields remain aggregate fields; the importer never fabricates dated price
history. Records with a raw ASIN and a current price also become Amazon Agent 2
offer snapshots because those rows have a defensible marketplace identifier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


RAW_ASIN = re.compile(r"^[A-Z0-9]{10}$")


def stable_id(prefix: str, value: str) -> str:
    return (prefix + hashlib.sha256(value.encode("utf-8")).hexdigest())[:32]


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def usable_product(row: dict) -> bool:
    return bool(row.get("canonical_id") and str(row.get("title") or "").strip())


def amazon_offer(row: dict) -> bool:
    return bool(
        RAW_ASIN.fullmatch(str(row.get("canonical_id") or ""))
        and row.get("current_price") is not None
        and float(row["current_price"]) > 0
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="path to products.json")
    args = parser.parse_args()
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    if not any(host in database_url for host in ("localhost", "127.0.0.1")):
        raise SystemExit("Refusing to import: this development importer requires a local database URL")
    payload = json.loads(args.path.read_text())
    if not isinstance(payload, list):
        raise SystemExit("Expected products.json to contain a JSON array")

    product_count = 0
    offer_count = 0
    connection = psycopg2.connect(database_url, connect_timeout=10)
    try:
        with connection:
            with connection.cursor() as cursor:
                for row in payload:
                    if not isinstance(row, dict) or not usable_product(row):
                        continue
                    canonical_id = str(row["canonical_id"])[:50]
                    created_at = parse_timestamp(row.get("created_at"))
                    cursor.execute(
                        """
                        INSERT INTO products (
                            canonical_id, title, brand, model, color, storage, ram,
                            image_url, list_price, current_price, all_time_low,
                            all_time_high, avg_30_days, overall_avg, customer_rating,
                            review_count, in_stock, seller_name, warranty_description,
                            ai_reviews_summary, created_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s,%s,%s
                        )
                        ON CONFLICT (canonical_id) DO UPDATE SET
                            title=EXCLUDED.title,
                            brand=COALESCE(EXCLUDED.brand, products.brand),
                            model=COALESCE(EXCLUDED.model, products.model),
                            color=COALESCE(EXCLUDED.color, products.color),
                            storage=COALESCE(EXCLUDED.storage, products.storage),
                            ram=COALESCE(EXCLUDED.ram, products.ram),
                            image_url=COALESCE(EXCLUDED.image_url, products.image_url),
                            list_price=COALESCE(EXCLUDED.list_price, products.list_price),
                            current_price=COALESCE(EXCLUDED.current_price, products.current_price),
                            all_time_low=COALESCE(EXCLUDED.all_time_low, products.all_time_low),
                            all_time_high=COALESCE(EXCLUDED.all_time_high, products.all_time_high),
                            avg_30_days=COALESCE(EXCLUDED.avg_30_days, products.avg_30_days),
                            overall_avg=COALESCE(EXCLUDED.overall_avg, products.overall_avg),
                            customer_rating=COALESCE(EXCLUDED.customer_rating, products.customer_rating),
                            review_count=COALESCE(EXCLUDED.review_count, products.review_count),
                            in_stock=COALESCE(EXCLUDED.in_stock, products.in_stock),
                            seller_name=COALESCE(EXCLUDED.seller_name, products.seller_name),
                            warranty_description=COALESCE(
                                EXCLUDED.warranty_description, products.warranty_description
                            ),
                            ai_reviews_summary=COALESCE(
                                EXCLUDED.ai_reviews_summary, products.ai_reviews_summary
                            )
                        """,
                        (
                            canonical_id, row["title"], row.get("brand"), row.get("model"),
                            row.get("color"), row.get("storage"), row.get("ram"),
                            row.get("image_url"), row.get("list_price"), row.get("current_price"),
                            row.get("all_time_low"), row.get("all_time_high"),
                            row.get("avg_30_days"), row.get("overall_avg"),
                            row.get("customer_rating"), row.get("review_count"),
                            row.get("in_stock", True), row.get("seller_name"),
                            row.get("warranty_description"), row.get("ai_reviews_summary"),
                            created_at,
                        ),
                    )
                    product_count += 1
                    if not amazon_offer(row):
                        continue
                    asin = canonical_id
                    run_id = stable_id("pjr", canonical_id)
                    offer_id = stable_id("pjo", canonical_id)
                    cursor.execute(
                        """
                        INSERT INTO market_search_runs (
                            run_id, query, provider, status, result_count,
                            started_at, finished_at
                        ) VALUES (%s,%s,'local_products_json','success',1,%s,%s)
                        ON CONFLICT (run_id) DO UPDATE SET
                            query=EXCLUDED.query, status='success', result_count=1,
                            started_at=EXCLUDED.started_at, finished_at=EXCLUDED.finished_at
                        """,
                        (run_id, row["title"], created_at, created_at),
                    )
                    cursor.execute(
                        """
                        INSERT INTO market_offers (
                            offer_id, run_id, canonical_id, provider, marketplace,
                            external_id, title, url, price, currency, original_price,
                            rating, review_count, seller_name, availability, fetched_at
                        ) VALUES (
                            %s,%s,%s,'local_products_json','amazon.in',%s,%s,%s,%s,
                            'INR',%s,%s,%s,%s,%s,%s
                        ) ON CONFLICT (offer_id) DO UPDATE SET
                            title=EXCLUDED.title, price=EXCLUDED.price,
                            original_price=EXCLUDED.original_price,
                            rating=EXCLUDED.rating, review_count=EXCLUDED.review_count,
                            seller_name=COALESCE(EXCLUDED.seller_name, market_offers.seller_name),
                            availability=EXCLUDED.availability,
                            fetched_at=EXCLUDED.fetched_at
                        """,
                        (
                            offer_id, run_id, canonical_id, asin, row["title"],
                            f"https://www.amazon.in/dp/{asin}", row["current_price"],
                            row.get("list_price"), row.get("customer_rating"),
                            row.get("review_count"), row.get("seller_name"),
                            "In stock" if row.get("in_stock") else "Out of stock",
                            created_at,
                        ),
                    )
                    cursor.execute(
                        """
                        INSERT INTO market_offer_details (offer_id, warranty)
                        VALUES (%s,%s)
                        ON CONFLICT (offer_id) DO UPDATE SET
                            warranty=COALESCE(EXCLUDED.warranty, market_offer_details.warranty)
                        """,
                        (offer_id, row.get("warranty_description")),
                    )
                    offer_count += 1
        print(
            f"Imported {product_count} product rows and {offer_count} grounded "
            "Amazon offer snapshots; no price-history rows were fabricated."
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
