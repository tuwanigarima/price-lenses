"""Deal Health Index (implementation plan, section 5).

    DHI = 0.40 * S_history + 0.25 * S_competitor + 0.20 * S_rating + 0.15 * S_sentiment

* S_history: from the History Agent (position between all-time low and high).
* S_competitor: 100 when the tracked price is the cheapest in-stock offer from
  an acceptable seller; otherwise 100 * cheapest / tracked price.
* S_rating: stars / 5 * 100.
* S_sentiment: from the review sentiment score (``review_sentiment``).

A factor without data is left out and the remaining weights are scaled up to
sum to 1, so a missing input does not silently count as 0. The report lists
which factors were used.
"""
from __future__ import annotations

WEIGHTS = {"history": 0.40, "competitor": 0.25, "rating": 0.20, "sentiment": 0.15}
LABELS = {"history": "Price history", "competitor": "Competitor price",
          "rating": "Customer rating", "sentiment": "Review sentiment"}
TIERS = ((80, "🔥 Steal Deal"), (65, "🟢 Good Deal"), (45, "🟡 Fair Deal"), (0, "🔴 Overpriced"))


def s_rating(stars) -> float | None:
    if stars is None:
        return None
    stars = float(stars)
    return round(max(0.0, min(100.0, stars / 5.0 * 100.0)), 1) if stars > 0 else None


def s_competitor(tracked_price, offers: list[dict]) -> float | None:
    """``offers`` are Agent 3's assessed offers (``effective_price``, ``purchasable``)."""
    prices = [
        float(offer["effective_price"]) for offer in offers or []
        if offer.get("purchasable") and offer.get("effective_price")
    ]
    if not tracked_price or not prices:
        return None
    cheapest = min(prices)
    if float(tracked_price) <= cheapest:
        return 100.0
    return round(100.0 * cheapest / float(tracked_price), 1)


def tier(score: float) -> str:
    return next(label for floor, label in TIERS if score >= floor)


def deal_health_index(components: dict[str, float | None]) -> dict:
    used = {name: float(value) for name, value in components.items()
            if name in WEIGHTS and value is not None}
    missing = [name for name in WEIGHTS if name not in used]
    if not used:
        return {"dhi": None, "tier": None, "components": {}, "missing": missing}
    total_weight = sum(WEIGHTS[name] for name in used)
    parts = {
        name: {
            "label": LABELS[name],
            "score": round(value, 1),
            "weight": round(WEIGHTS[name] / total_weight, 3),
            "points": round(value * WEIGHTS[name] / total_weight, 1),
        }
        for name, value in used.items()
    }
    dhi = round(sum(value * WEIGHTS[name] for name, value in used.items()) / total_weight, 1)
    return {"dhi": dhi, "tier": tier(dhi), "components": parts, "missing": missing}


def deal_health_from_reports(history_report: dict | None, eligibility_report: dict | None) -> dict:
    trend = (history_report or {}).get("trend") or {}
    eligibility = eligibility_report or {}
    return deal_health_index({
        "history": None if trend.get("error") else trend.get("s_history"),
        "competitor": s_competitor(trend.get("current_price"), eligibility.get("offers") or []),
        "rating": s_rating(trend.get("customer_rating")),
        "sentiment": (eligibility.get("sentiment") or {}).get("s_sentiment"),
    })
