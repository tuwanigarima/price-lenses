#!/usr/bin/env python3
"""Seed a small idempotent local-only Agent 2 snapshot for Agent 3 smoke tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    if not any(host in database_url for host in ("localhost", "127.0.0.1")):
        raise SystemExit("Refusing to seed: this fixture is restricted to a local database URL")
    connection = psycopg2.connect(database_url, connect_timeout=10)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO products (
                        canonical_id, title, brand, model, color, storage, ram,
                        current_price, in_stock, created_at
                    ) VALUES (
                        'LOCAL-S24U-256', 'Samsung Galaxy S24 Ultra 8GB/256GB Graphite',
                        'Samsung', 'Galaxy S24 Ultra', 'Graphite', '256GB', '8GB',
                        109999, TRUE, CURRENT_TIMESTAMP
                    ) ON CONFLICT (canonical_id) DO NOTHING
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO market_search_runs (
                        run_id, query, provider, status, result_count, started_at, finished_at
                    ) VALUES (
                        'localagent3run000000000000000001',
                        'Samsung S24 Ultra 8GB/256GB Graphite', 'local_fixture',
                        'success', 3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    ) ON CONFLICT (run_id) DO UPDATE SET
                        started_at=CURRENT_TIMESTAMP, finished_at=CURRENT_TIMESTAMP
                    """
                )
                offers = [
                    (
                        'localagent3offer000000000000001', 'flipkart.com',
                        'Samsung Galaxy S24 Ultra 8GB/256GB Graphite', 96999,
                        'Budget Mobiles', 4.1, 'In stock', 'Delivery in 5 days',
                        'https://www.flipkart.com/',
                    ),
                    (
                        'localagent3offer000000000000002', 'amazon.in',
                        'Samsung Galaxy S24 Ultra 8GB/256GB Graphite', 98999,
                        'Appario Retail Private Ltd', 4.7, 'In stock', 'Delivery in 2 days',
                        'https://www.amazon.in/',
                    ),
                    (
                        'localagent3offer000000000000003', 'croma.com',
                        'Samsung Galaxy S24 Ultra 8GB/256GB Graphite', 99999,
                        'Croma', 4.6, 'In stock', 'Delivery tomorrow',
                        'https://www.croma.com/',
                    ),
                ]
                for offer in offers:
                    cursor.execute(
                        """
                        INSERT INTO market_offers (
                            offer_id, run_id, canonical_id, provider, marketplace,
                            external_id, title, url, price, currency, seller_name,
                            seller_rating, availability, shipping, fetched_at
                        ) VALUES (
                            %s, 'localagent3run000000000000000001', 'LOCAL-S24U-256',
                            'local_fixture', %s, %s, %s, %s, %s, 'INR', %s, %s, %s, %s,
                            CURRENT_TIMESTAMP
                        ) ON CONFLICT (offer_id) DO UPDATE SET fetched_at=CURRENT_TIMESTAMP
                        """,
                        (offer[0], offer[1], f"fixture-{offer[0][-1]}", offer[2], offer[8],
                         offer[3], offer[4], offer[5], offer[6], offer[7]),
                    )
                    cursor.execute(
                        """
                        INSERT INTO market_offer_details (
                            offer_id, seller_id, price_with_offers, is_assured,
                            cod_available, no_cost_emi, return_policy, delivery_by,
                            warranty, item_condition
                        ) VALUES (
                            %s, %s, NULL, TRUE, TRUE, TRUE, %s, %s,
                            '1 year manufacturer warranty', 'NEW'
                        ) ON CONFLICT (offer_id) DO NOTHING
                        """,
                        (offer[0], f"seller-{offer[0][-1]}",
                         "7 day replacement only" if offer[1] == "flipkart.com" else "7 day return and refund",
                         offer[7]),
                    )
                cursor.execute(
                    """
                    INSERT INTO market_offer_promotions (
                        promotion_id, offer_id, promotion_type, bank, card_type,
                        description, amount, is_emi, source
                    ) VALUES (
                        'localagent3promo0000000000000001',
                        'localagent3offer000000000000002', 'bank', 'HDFC', 'credit',
                        '₹3000 instant discount with eligible HDFC credit cards',
                        3000, FALSE, 'local_fixture'
                    ) ON CONFLICT (promotion_id) DO NOTHING
                    """
                )
        print("Seeded local Agent 3 fixture query: Samsung S24 Ultra 8GB/256GB Graphite")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
