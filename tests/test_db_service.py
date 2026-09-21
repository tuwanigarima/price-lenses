import os
import uuid

import psycopg
import pytest
from psycopg import sql

from price_lenses.db import Database
from price_lenses.models import Offer
from price_lenses.service import PriceLens


class Stub:
    def __init__(self, name, offers): self.name, self._o = name, offers
    def search(self, q, limit=20): return self._o


class Boom:
    name = "boom"
    def search(self, q, limit=20): raise RuntimeError("no key")


@pytest.fixture
def db():
    url = os.getenv(
        "PL_TEST_DATABASE_URL",
        "postgresql://pricelens:pricelens_local@localhost:5433/pricelens",
    )
    schema = "test_" + uuid.uuid4().hex
    try:
        database = Database(url, schema=schema)
    except psycopg.OperationalError as exc:
        pytest.skip(f"test PostgreSQL is not available: {exc}")
    try:
        yield database
    finally:
        database.close()
        with psycopg.connect(url, autocommit=True) as con:
            con.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_end_to_end_matching_and_history(db):
    a = Offer("serpapi", "amazon.in", "Apple iPhone 17 Pro 256GB", asin="B0FQFB8FMG", price=134900, seller_name="Appario")
    b = Offer("apify", "flipkart.com", "Apple iPhone 17 Pro (256 GB)", price=132900, seller_name="RetailNet")
    res = PriceLens(db, [Stub("serpapi", [a]), Boom(), Stub("apify", [b])]).search("iphone 17 pro")
    assert [r.count for r in res] == [1, 0, 1] and res[1].error == "no key"
    assert db.con.execute("SELECT count(*) FROM products").fetchone()[0] == 1
    rows = db.compare("iPhone 17 Pro")
    assert [r[4] for r in rows] == [132900, 134900]        # cheapest first
    assert len(db.price_history("asin:B0FQFB8FMG")) == 2


def test_seller_details_and_promos_round_trip(db):
    from price_lenses.models import Promo
    o = Offer("apify", "flipkart.com", "Apple iPhone 16 (Black, 128 GB)", asin="B0DGJ6Q7LM", price=69900,
              seller_name="TrueComRetail", seller_id="f6d1", seller_rating=4.6, price_with_offers=66405,
              is_assured=True, cod_available=True, return_policy="7_day", delivery_by="Fri, 18 Sep",
              promos=[Promo("BANK", "Bank offers Rs3,495 off", amount=3495, source="flipkart_details"),
                      Promo("EXCHANGE", "Up to Rs40,900", amount=40900)])
    PriceLens(db, [Stub("apify", [o])]).search("iphone 16")
    [row] = db.offers_for_query("iPhone 16")
    assert row["price_with_offers"] == 66405 and row["is_assured"] is True and row["seller_id"] == "f6d1"
    promos = db.promos_for_query("iphone 16")
    assert {(p["promo_type"], p["amount"]) for p in promos} == {("BANK", 3495), ("EXCHANGE", 40900)}
    assert {p["offer_id"] for p in promos} == {row["offer_id"]}
    assert {p["product_id"] for p in promos} == {row["product_id"]}
    assert {p["product_title"] for p in promos} == {"Apple iPhone 16 (Black, 128 GB)"}
