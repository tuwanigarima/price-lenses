from scripts.dev.import_products_json import amazon_offer, stable_id, usable_product


def test_products_json_import_only_creates_amazon_offer_with_asin_and_price():
    assert amazon_offer({"canonical_id": "B0CS5XW6TN", "current_price": 99999})
    assert not amazon_offer({"canonical_id": "asin:B0CS5XW6TN", "current_price": 99999})
    assert not amazon_offer({"canonical_id": "title:abc", "current_price": 99999})
    assert not amazon_offer({"canonical_id": "B0CS5XW6TN", "current_price": None})


def test_product_import_ids_are_stable_and_fit_schema():
    assert stable_id("pjr", "B0CS5XW6TN") == stable_id("pjr", "B0CS5XW6TN")
    assert len(stable_id("pjr", "B0CS5XW6TN")) == 32
    assert usable_product({"canonical_id": "x", "title": "Phone"})
    assert not usable_product({"canonical_id": "x", "title": ""})
