"""Deterministic commercial validation for normalized Agent 2 offers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .market_agent_models import FreshnessPolicy, SUPPORTED_RETAILERS
from .market_analysis import age_minutes, marketplace_name


VALIDATION_STATUSES = ("VERIFIED", "PARTIAL", "STALE", "REJECTED")
_FIRST_PARTY_RETAILERS = {
    "croma.com",
    "reliancedigital.in",
    "vijaysales.com",
}


def validate_offer(
    row: dict[str, Any],
    promotions: list[dict[str, Any]],
    *,
    policy: FreshnessPolicy,
    now: datetime,
) -> dict[str, Any]:
    """Return a serialisable validation result without mutating source data."""
    checks: dict[str, str] = {}
    warnings: list[str] = []
    rejection_reasons: list[str] = []

    retailer = marketplace_name(row.get("marketplace"))
    checks["retailer"] = "PASS" if retailer in SUPPORTED_RETAILERS else "FAIL"
    if checks["retailer"] == "FAIL":
        rejection_reasons.append("Unsupported Indian retailer")

    currency = str(row.get("currency") or "INR").upper()
    checks["currency"] = "PASS" if currency == "INR" else "FAIL"
    if currency != "INR":
        rejection_reasons.append("Offer currency is not INR")

    try:
        price = float(row.get("price"))
    except (TypeError, ValueError):
        price = 0
    checks["price"] = "PASS" if price > 0 else "FAIL"
    if price <= 0:
        rejection_reasons.append("Offer has no positive price")

    availability = str(row.get("availability") or "").strip().lower()
    if any(value in availability for value in ("out of stock", "unavailable", "sold out")):
        checks["availability"] = "FAIL"
        rejection_reasons.append("Offer is not currently available")
    elif availability:
        checks["availability"] = "PASS"
    else:
        checks["availability"] = "UNKNOWN"
        warnings.append("Availability was not provided")

    if row.get("url"):
        checks["product_url"] = "PASS"
    else:
        checks["product_url"] = "UNKNOWN"
        warnings.append("Product URL was not provided")

    seller = str(row.get("seller_name") or "").strip()
    if seller or retailer in _FIRST_PARTY_RETAILERS:
        checks["seller_identity"] = "PASS"
    else:
        checks["seller_identity"] = "UNKNOWN"
        warnings.append("Marketplace seller identity was not provided")

    age = age_minutes(row.get("fetched_at"), now)
    if age is None:
        checks["freshness"] = "UNKNOWN"
        warnings.append("Offer observation time was not provided")
    elif age > policy.price_minutes:
        checks["freshness"] = "FAIL"
        warnings.append(
            f"Offer is {age:.0f} minutes old; price TTL is {policy.price_minutes} minutes"
        )
    else:
        checks["freshness"] = "PASS"

    invalid_promotions = [
        promo
        for promo in promotions
        if str(promo.get("offer_id") or "") != str(row.get("offer_id") or "")
    ]
    if invalid_promotions:
        checks["promotion_binding"] = "FAIL"
        warnings.append("One or more promotions were not bound to this offer")
    elif promotions:
        checks["promotion_binding"] = "PASS"
    else:
        checks["promotion_binding"] = "NOT_APPLICABLE"

    # Hard checks: a FAIL or UNKNOWN here blocks VERIFIED status.
    _HARD_CHECKS = {"retailer", "currency", "price", "freshness", "promotion_binding"}
    # Soft checks: UNKNOWN is recorded and warned but does not prevent VERIFIED.
    _SOFT_CHECKS = {"availability", "product_url", "seller_identity"}

    if rejection_reasons:
        status = "REJECTED"
    elif checks["freshness"] == "FAIL":
        status = "STALE"
    elif any(
        checks.get(key) in {"UNKNOWN", "FAIL"}
        for key in _HARD_CHECKS
        if key in checks
    ):
        status = "PARTIAL"
    else:
        status = "VERIFIED"

    return {
        "status": status,
        "checks": checks,
        "warnings": warnings,
        "rejection_reasons": rejection_reasons,
        "age_minutes": round(age, 1) if age is not None else None,
    }


def validation_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    result = {status.lower(): 0 for status in VALIDATION_STATUSES}
    for row in rows:
        status = str(row.get("validation_status") or "REJECTED").lower()
        if status in result:
            result[status] += 1
    return result
