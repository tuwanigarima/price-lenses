"""Deterministic seller, policy, eligibility, and deal scoring for Agent 3."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .market_analysis import age_minutes, promotion_scenarios


FIRST_PARTY_RETAILERS = {
    "croma.com": "croma",
    "reliancedigital.in": "reliance digital",
    "vijaysales.com": "vijay sales",
}
NON_NEW = {"RENEWED", "REFURBISHED", "USED", "OPEN_BOX", "OPEN BOX", "PRE-OWNED"}
OUT_OF_STOCK = re.compile(
    r"out[\s_-]*of[\s_-]*stock|unavailable|sold[\s_-]*out|discontinued|notify\s+me",
    re.I,
)
IN_STOCK = re.compile(r"in[\s_-]*stock|available|delivery\s+by|ships\s+(?:in|within)", re.I)


def normalize_seller(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def variant_key(row: dict[str, Any]) -> str:
    """Build a stable-enough grouping key from explicit columns and listing text."""
    title = str(row.get("title") or row.get("product_title") or "")
    storage = row.get("storage") or _storage_from_title(title)
    ram = row.get("ram") or _ram_from_title(title)
    color = row.get("color") or "unknown-color"
    condition = (row.get("item_condition") or "UNKNOWN").upper().replace(" ", "_")
    return "|".join(
        str(value or "unknown").lower().replace(" ", "")
        for value in (ram, storage, color, condition)
    )


def _first(pattern: str, value: str) -> str | None:
    match = re.search(pattern, value, flags=re.I)
    return match.group(1).upper().replace(" ", "") if match else None


def _ram_from_title(title: str) -> str | None:
    explicit = _first(r"\b(\d+\s*gb)\s+ram\b", title)
    if explicit:
        return explicit
    pair = re.search(r"\b(\d+\s*gb)\s*[/+]\s*(\d+\s*(?:gb|tb))\b", title, re.I)
    return pair.group(1).upper().replace(" ", "") if pair else None


def _storage_from_title(title: str) -> str | None:
    explicit = _first(r"\b(\d+\s*(?:gb|tb))\s+(?:storage|rom)\b", title)
    if explicit:
        return explicit
    pair = re.search(r"\b\d+\s*gb\s*[/+]\s*(\d+\s*(?:gb|tb))\b", title, re.I)
    if pair:
        return pair.group(1).upper().replace(" ", "")
    values = re.findall(r"\b(\d+\s*(?:gb|tb))\b", title, re.I)
    return values[-1].upper().replace(" ", "") if values else None


def classify_stock(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "UNKNOWN"
    if OUT_OF_STOCK.search(text):
        return "OUT_OF_STOCK"
    if IN_STOCK.search(text):
        return "IN_STOCK"
    return "UNKNOWN"


def classify_condition(value: str | None, requested: str | None) -> tuple[str, bool]:
    actual = str(value or "").strip().upper().replace("-", "_") or "UNKNOWN"
    wanted = str(requested or "").strip().upper().replace("-", "_")
    if actual == "UNKNOWN":
        return "UNKNOWN", False
    if wanted and wanted == "NEW" and actual in NON_NEW:
        return actual, True
    if wanted and actual != wanted:
        return actual, True
    return actual, False


def classify_warranty(value: str | None) -> tuple[str, float | None]:
    text = str(value or "").lower()
    if not text:
        return "UNKNOWN", None
    if re.search(r"no\s+warranty|without\s+warranty", text):
        return "NO_WARRANTY", 0.0
    if "seller warranty" in text:
        return "SELLER_WARRANTY", 30.0
    if "manufacturer" in text or "brand warranty" in text:
        return "CLAIMED_MANUFACTURER", 70.0
    return "UNCLEAR", 30.0


def classify_return_policy(value: str | None) -> str:
    text = str(value or "").lower()
    if not text:
        return "UNKNOWN"
    if re.search(r"non[-\s]?returnable|no\s+return|not\s+eligible\s+for\s+return", text):
        return "NO_RETURN"
    if re.search(r"replacement\s+only|only\s+(?:for\s+)?replacement", text):
        return "REPLACEMENT_ONLY"
    if "refund" in text or "return" in text:
        return "RETURN_OR_REFUND"
    return "UNCLEAR"


def delivery_score(value: str | None) -> tuple[str, float | None]:
    text = str(value or "").lower()
    if not text:
        return "UNKNOWN", None
    if re.search(r"same[ -]?day|today|1[ -]?day|tomorrow", text):
        return "FAST", 100.0
    numbers = [int(number) for number in re.findall(r"\b(\d+)\s*(?:day|business day)", text)]
    days = max(numbers) if numbers else None
    if days is None:
        return "STANDARD", 70.0
    if days <= 5:
        return "STANDARD", 70.0
    if days <= 7:
        return "SLOW", 40.0
    return "VERY_SLOW", 0.0


def seller_classification(
    row: dict[str, Any], authorization: dict[str, Any] | None
) -> tuple[str, float, list[str]]:
    marketplace = str(row.get("marketplace") or "").lower()
    seller = normalize_seller(row.get("seller_name"))
    warnings: list[str] = []
    if marketplace in FIRST_PARTY_RETAILERS and seller in {
        "",
        FIRST_PARTY_RETAILERS[marketplace],
    }:
        return "FIRST_PARTY_RETAILER", 100.0, warnings
    if authorization:
        if authorization.get("is_authorized") is True:
            return "VERIFIED_AUTHORIZED", 100.0, warnings
        if authorization.get("is_authorized") is False:
            return "VERIFIED_UNAUTHORIZED", 0.0, ["Seller is recorded as unauthorized"]
    rating = row.get("seller_rating")
    if rating is not None:
        try:
            rating_value = float(rating)
        except (TypeError, ValueError):
            rating_value = 0
        if rating_value >= 4:
            warnings.append("Strong rating does not prove brand authorization")
            return "UNVERIFIED", 60.0, warnings
    warnings.append("Seller authorization could not be verified")
    return "UNVERIFIED", 30.0 if not seller else 40.0, warnings


def choose_price(
    row: dict[str, Any], promotions: list[dict[str, Any]], context: dict[str, Any]
) -> dict[str, Any]:
    listed = float(row["price"])
    scenarios = promotion_scenarios(row, promotions, context)
    eligible = [item for item in scenarios if item.get("eligibility") == "eligible"]
    unknown = [item for item in scenarios if item.get("eligibility") == "unknown"]
    best_eligible = min(eligible, key=lambda item: item["price"], default=None)
    best_conditional = min(unknown, key=lambda item: item["price"], default=None)
    effective = float(best_eligible["price"]) if best_eligible else listed
    eligibility = "ELIGIBLE" if best_eligible else ("UNKNOWN" if best_conditional else "NOT_APPLICABLE")
    return {
        "listed_price": listed,
        "eligible_effective_price": effective,
        "conditional_effective_price": (
            float(best_conditional["price"]) if best_conditional else None
        ),
        "promotion_eligibility": eligibility,
        "eligible_promotion_ids": best_eligible.get("promotion_ids", []) if best_eligible else [],
        "conditional_promotion_ids": (
            best_conditional.get("promotion_ids", []) if best_conditional else []
        ),
    }


def score_group(
    rows: list[dict[str, Any]],
    promotions: list[dict[str, Any]],
    authorizations: dict[str, dict[str, Any]],
    *,
    budget: float | None,
    requested_condition: str | None,
    requires_manufacturer_warranty: bool,
    bank: str | None,
    card_type: str | None,
    wants_emi: bool | None,
    freshness_minutes: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    context = {"bank": bank, "card_type": card_type, "wants_emi": wants_emi}
    prepared: list[dict[str, Any]] = []
    for row in rows:
        if row.get("price") is None or float(row["price"]) <= 0:
            continue
        price = choose_price(row, promotions, context)
        authorization = authorizations.get(str(row.get("offer_id")))
        seller_status, seller_score, seller_warnings = seller_classification(row, authorization)
        condition_status, condition_mismatch = classify_condition(
            row.get("item_condition"), requested_condition
        )
        warranty_status, warranty_value = classify_warranty(row.get("warranty"))
        return_status = classify_return_policy(row.get("return_policy"))
        delivery_status, delivery_value = delivery_score(
            row.get("delivery_by") or row.get("shipping")
        )
        stock_status = classify_stock(row.get("availability"))
        age = age_minutes(row.get("fetched_at"), now)
        stale = age is None or age > freshness_minutes
        risk_flags = list(seller_warnings)
        disqualifications: list[str] = []
        if condition_mismatch:
            disqualifications.append("Item condition does not match the request")
        if stock_status == "OUT_OF_STOCK":
            disqualifications.append("Offer is out of stock")
        if stale:
            disqualifications.append("Offer is stale")
        if seller_status == "VERIFIED_UNAUTHORIZED":
            disqualifications.append("Seller is verified unauthorized")
        if requires_manufacturer_warranty and warranty_status not in {
            "VERIFIED_MANUFACTURER", "CLAIMED_MANUFACTURER"
        }:
            disqualifications.append("Required manufacturer warranty is not evidenced")
        prepared.append(
            {
                "offer_id": row.get("offer_id"),
                "canonical_id": row.get("canonical_id"),
                "variant_key": variant_key(row),
                "retailer": row.get("marketplace"),
                "seller_name": row.get("seller_name"),
                "url": row.get("url"),
                "fetched_at": row.get("fetched_at"),
                "age_minutes": round(age, 1) if age is not None else None,
                **price,
                "stock_status": stock_status,
                "seller_status": seller_status,
                "warranty_status": warranty_status,
                "return_status": return_status,
                "delivery_status": delivery_status,
                "condition_status": condition_status,
                "seller_score": seller_score,
                "warranty_score": warranty_value,
                "delivery_score": delivery_value,
                "risk_flags": risk_flags,
                "disqualifications": disqualifications,
            }
        )
    eligible_prices = [
        row["eligible_effective_price"]
        for row in prepared
        if not row["disqualifications"] and row["eligible_effective_price"] > 0
    ]
    lowest = min(eligible_prices) if eligible_prices else None
    for result in prepared:
        current = result["eligible_effective_price"]
        price_score = round(min(100.0, 100 * lowest / current), 2) if lowest else None
        result["price_score"] = price_score
        known = [
            price_score,
            result["seller_score"],
            result["warranty_score"],
            result["delivery_score"],
        ]
        completeness = sum(value is not None for value in known) / 4
        result["evidence_confidence"] = round(
            100
            * (
                0.25 * (1 if result["age_minutes"] is not None else 0)
                + 0.30 * (1 if result["seller_status"] != "UNVERIFIED" else 0)
                + 0.25 * (1 if result["warranty_status"] != "UNKNOWN" else 0)
                + 0.20 * completeness
            ),
            2,
        )
        if None in known:
            result["deal_score"] = None
        else:
            result["deal_score"] = round(
                0.40 * price_score
                + 0.30 * result["seller_score"]
                + 0.20 * result["warranty_score"]
                + 0.10 * result["delivery_score"],
                2,
            )
        if result["disqualifications"]:
            result["safety_status"] = "DISQUALIFIED"
        elif result["evidence_confidence"] < 50:
            result["safety_status"] = "UNVERIFIED"
        elif result["risk_flags"] or result["return_status"] in {"NO_RETURN", "REPLACEMENT_ONLY"}:
            result["safety_status"] = "SAFE_WITH_WARNINGS"
        else:
            result["safety_status"] = "SAFE_VERIFIED"
        result["within_budget"] = current <= budget if budget is not None else None
        result["amount_over_budget"] = (
            round(max(0.0, current - budget), 2) if budget is not None else None
        )
        result["score_breakdown"] = {
            "price": price_score,
            "seller": result["seller_score"],
            "warranty": result["warranty_score"],
            "delivery": result["delivery_score"],
        }
    return prepared
