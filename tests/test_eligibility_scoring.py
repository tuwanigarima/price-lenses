from datetime import datetime, timezone

from tools.eligibility_scoring import score_group, variant_key


def test_variant_key_does_not_treat_single_storage_value_as_ram():
    key = variant_key(
        {"title": "Samsung Galaxy S24 FE 256GB Graphite", "item_condition": "NEW"}
    )
    assert key.startswith("unknown|256gb|")


def test_variant_key_extracts_ram_storage_pair():
    key = variant_key(
        {"title": "Samsung Galaxy S24 Ultra 8GB/256GB Graphite", "item_condition": "NEW"}
    )
    assert key.startswith("8gb|256gb|")


def test_stale_offer_is_disqualified_and_promotion_stays_conditional():
    row = {
        "offer_id": "offer-1",
        "canonical_id": "sku-1",
        "title": "Phone 8GB/256GB",
        "price": 50000,
        "marketplace": "amazon.in",
        "seller_name": "Seller",
        "seller_rating": 4.5,
        "availability": "In stock",
        "warranty": "1 year manufacturer warranty",
        "return_policy": "7 day replacement only",
        "delivery_by": "Delivery in 2 days",
        "item_condition": "NEW",
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }
    promotions = [{
        "promotion_id": "promo-1",
        "offer_id": "offer-1",
        "promotion_type": "bank",
        "bank": "HDFC",
        "card_type": "credit",
        "amount": 2000,
        "percent": None,
        "is_emi": False,
        "description": "HDFC credit card discount",
    }]
    result = score_group(
        [row], promotions, {}, budget=None, requested_condition="NEW",
        requires_manufacturer_warranty=False, bank=None, card_type=None,
        wants_emi=None, freshness_minutes=60,
        now=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )[0]
    assert result["safety_status"] == "DISQUALIFIED"
    assert result["promotion_eligibility"] == "UNKNOWN"
    assert result["eligible_effective_price"] == 50000
    assert result["conditional_effective_price"] == 48000
