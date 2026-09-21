"""Pure data-shaping helpers for the UI (no Streamlit/database imports -> easy to test)."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

CURRENCY_SYMBOL = {"INR": "₹", "USD": "$", "GBP": "£", "EUR": "€"}


def fmt_money(price: float | None, currency: str | None) -> str:
    if price is None:
        return "-"
    sym = CURRENCY_SYMBOL.get(currency or "", (currency + " ") if currency else "")
    return f"{sym}{price:,.0f}"


def discount_pct(price: float | None, original: float | None) -> float | None:
    if price is None or not original or original <= price:
        return None
    return round((original - price) / original * 100, 1)


def parse_offers(offers_json: str | list | None) -> list[str]:
    try:
        v = offers_json if isinstance(offers_json, list) else json.loads(offers_json or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def enrich_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        r = dict(r)
        r["offers_list"] = parse_offers(r.get("offers_json"))
        r["discount_pct"] = discount_pct(r.get("price"), r.get("original_price"))
        r["effective_price"] = r.get("price_with_offers") or r.get("price")
        out.append(r)
    return out


def dominant_currency(rows: list[dict[str, Any]]) -> str | None:
    c = Counter(r["currency"] for r in rows if r.get("price") is not None and r.get("currency"))
    return c.most_common(1)[0][0] if c else None


def filter_rows(rows, marketplaces: list[str] | None = None, min_rating: float = 0.0,
                product_id: str | None = None):
    out = []
    for r in rows:
        if product_id and r["product_id"] != product_id:
            continue
        if marketplaces and r["marketplace"] not in marketplaces:
            continue
        if min_rating and (r.get("rating") or 0) < min_rating:
            continue
        out.append(r)
    return out


def group_products(rows) -> list[dict[str, Any]]:
    """[{product_id, title, offers, cheapest}] sorted by number of offers desc."""
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(r["product_id"], {
            "product_id": r["product_id"],
            "title": r.get("product_title") or r.get("title"),
            "offers": 0, "cheapest": None})
        g["offers"] += 1
        if r.get("price") is not None and (g["cheapest"] is None or r["price"] < g["cheapest"]):
            g["cheapest"] = r["price"]
    return sorted(groups.values(), key=lambda g: -g["offers"])


def summarize(rows) -> dict[str, Any]:
    """Headline numbers. Price stats only use the dominant currency so they never mix."""
    cur = dominant_currency(rows)
    priced = [r for r in rows if r.get("price") is not None and r.get("currency") == cur]
    rated = [r for r in rows if r.get("rating") is not None]
    best_rated = max(rated, key=lambda r: (r["rating"], r.get("review_count") or 0), default=None)
    cheapest = min(priced, key=lambda r: r["price"], default=None)
    return {
        "currency": cur,
        "offers": len(rows),
        "marketplaces": len({r["marketplace"] for r in rows}),
        "cheapest": cheapest,
        "average": (sum(r["price"] for r in priced) / len(priced)) if priced else None,
        "spread": (max(r["price"] for r in priced) - cheapest["price"]) if cheapest else None,
        "best_rated": best_rated,
    }


def offer_label(r: dict[str, Any]) -> str:
    seller = r.get("seller_name")
    return f"{r['marketplace']} · {seller}" if seller else r["marketplace"]


def to_table(rows) -> list[dict[str, Any]]:
    """Display-ready rows (column order = UI order)."""
    return [{
        "Product": r.get("product_title") or r.get("title"),
        "Marketplace": r["marketplace"],
        "Seller": r.get("seller_name"),
        "Price": r.get("price"),
        "Effective price": r.get("effective_price"),
        "Currency": r.get("currency"),
        "Was": r.get("original_price"),
        "Discount %": r.get("discount_pct"),
        "Rating": r.get("rating"),
        "Reviews": r.get("review_count"),
        "Seller rating": r.get("seller_rating"),
        "Assured": r.get("is_assured"),
        "COD": r.get("cod_available"),
        "No-cost EMI": r.get("no_cost_emi"),
        "Return policy": r.get("return_policy"),
        "Delivery by": r.get("delivery_by"),
        "Availability": r.get("availability"),
        "Delivery": r.get("shipping"),
        "Offers": " | ".join(r.get("offers_list", [])),
        "Source": r.get("provider"),
        "Link": r.get("url"),
    } for r in rows]


def promos_table(promo_rows: list[dict[str, Any]], offer_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Display rows for the offers/promotions panel, limited to the offers currently in view."""
    out = []
    for r in promo_rows:
        if offer_ids is not None and r["offer_id"] not in offer_ids:
            continue
        out.append({
            "Product": r.get("product_title"), "Product ID": r.get("product_id"),
            "Provider product ID": r.get("external_id"),
            "Marketplace": r["marketplace"], "Seller": r.get("seller_name"),
            "Type": r.get("promo_type"), "Bank": r.get("bank"), "Card": r.get("card_type"),
            "Offer": r.get("description"), "Amount": r.get("amount"), "Percent": r.get("percent"),
            "EMI": r.get("is_emi"), "Source": r.get("source"), "Product link": r.get("url"),
        })
    return out


def promo_summary(promo_rows: list[dict[str, Any]], offer_ids: set[str] | None = None) -> dict[str, int]:
    """Count of promotions by type (BANK, EXCHANGE, COUPON, FESTIVE ...)."""
    c: Counter = Counter()
    for r in promo_rows:
        if offer_ids is None or r["offer_id"] in offer_ids:
            c[r.get("promo_type") or "OTHER"] += 1
    return dict(c)
