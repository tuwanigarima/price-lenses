from price_lenses.ui import data as ui


def row(pid, mkt, seller, price, cur="INR", orig=None, rating=None, reviews=None, title="iPhone 17 Pro"):
    return {"product_id": pid, "product_title": title, "title": title, "marketplace": mkt,
            "seller_name": seller, "price": price, "currency": cur, "original_price": orig,
            "rating": rating, "review_count": reviews, "offers_json": '["10% OFF"]', "url": "u",
            "provider": "serpapi"}


ROWS = ui.enrich_rows([
    row("asin:A", "amazon.in", "Appario", 134900, orig=149900, rating=4.5, reviews=3400),
    row("asin:A", "flipkart.com", "RetailNet", 132900, rating=4.6, reviews=900),
    row("asin:A", "amazon.com", "Amazon", 1099, cur="USD"),
    row("asin:B", "amazon.in", "X", 99000, title="iPhone 17"),
])


def test_discount_and_offers():
    assert ui.discount_pct(134900, 149900) == 10.0
    assert ui.discount_pct(100, 100) is None and ui.discount_pct(None, 5) is None
    assert ROWS[0]["offers_list"] == ["10% OFF"] and ROWS[0]["discount_pct"] == 10.0
    assert ui.parse_offers("not json") == []


def test_summary_uses_dominant_currency_only():
    s = ui.summarize(ROWS)
    assert s["currency"] == "INR"
    assert s["cheapest"]["seller_name"] == "X"          # 99000 among INR rows only
    assert s["average"] == (134900 + 132900 + 99000) / 3
    assert s["best_rated"]["marketplace"] == "flipkart.com"
    assert s["offers"] == 4 and s["marketplaces"] == 3


def test_filters_and_groups():
    assert len(ui.filter_rows(ROWS, product_id="asin:A")) == 3
    assert len(ui.filter_rows(ROWS, marketplaces=["amazon.in"])) == 2
    assert len(ui.filter_rows(ROWS, min_rating=4.6)) == 1
    g = ui.group_products(ROWS)
    assert g[0]["product_id"] == "asin:A" and g[0]["offers"] == 3


def test_format_and_table():
    assert ui.fmt_money(134900, "INR") == "₹134,900" and ui.fmt_money(None, "INR") == "-"
    t = ui.to_table(ROWS)[0]
    assert t["Discount %"] == 10.0 and t["Offers"] == "10% OFF" and t["Link"] == "u"
    assert ui.offer_label(ROWS[0]) == "amazon.in · Appario"


def test_empty():
    s = ui.summarize([])
    assert s["cheapest"] is None and s["average"] is None and s["offers"] == 0


def test_promotion_table_keeps_product_and_provider_identity():
    rows = [{
        "offer_id": "offer-1", "product_id": "asin:B0X", "product_title": "Phone 128GB",
        "external_id": "B0X", "marketplace": "amazon.in", "seller_name": "Seller",
        "promo_type": "BANK", "bank": "HDFC Bank", "card_type": "Credit Card",
        "description": "10% off", "amount": 1500, "percent": 10, "is_emi": False,
        "source": "serpapi_amazon_product", "url": "https://amazon.in/dp/B0X",
    }]
    [promo] = ui.promos_table(rows, {"offer-1"})
    assert promo["Product ID"] == "asin:B0X"
    assert promo["Provider product ID"] == "B0X"
    assert promo["Product link"].endswith("B0X")
