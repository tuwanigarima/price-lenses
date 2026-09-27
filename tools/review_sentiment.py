"""Review sentiment score, the S_sentiment factor of the Deal Health Index (Agent 3).

Deterministic, from star ratings and the defect scan:

* base: the average star rating of marketplace reviews on a 0-100 scale
  (1 star = 0, 5 stars = 100). Reddit and YouTube comments have no rating and
  do not change the base. With fewer than ``MIN_RATED_REVIEWS`` rated reviews
  the base is a neutral 50;
* penalty: -20 when the defect scan reports overheating or battery problems
  (implementation plan, section 5), applied once however many of them appear.
"""
from __future__ import annotations

from .review_corpus import Review

MIN_RATED_REVIEWS = 5
NEUTRAL = 50.0
PENALTY = 20.0
PENALISED_DEFECTS = ("overheating", "battery_drain", "battery_swelling")


def review_sentiment(reviews: list[Review], defects: dict | None) -> dict:
    ratings = [review.rating for review in reviews if review.rating is not None]
    if len(ratings) >= MIN_RATED_REVIEWS:
        average = sum(ratings) / len(ratings)
        base = (average - 1.0) / 4.0 * 100.0
        basis = f"average {average:.1f}★ from {len(ratings)} rated reviews"
    else:
        average = None
        base = NEUTRAL
        basis = (
            f"neutral: only {len(ratings)} rated reviews (at least {MIN_RATED_REVIEWS} needed)"
            if reviews else "neutral: no reviews collected"
        )
    findings = (defects or {}).get("findings") or []
    penalised = [f["label"] for f in findings if f["defect"] in PENALISED_DEFECTS]
    penalty = PENALTY if penalised else 0.0
    score = max(0.0, min(100.0, base - penalty))
    return {
        "s_sentiment": round(score, 1),
        "base": round(base, 1),
        "average_rating": round(average, 2) if average is not None else None,
        "rated_reviews": len(ratings),
        "reviews": len(reviews),
        "positive_share": round(sum(1 for r in ratings if r >= 4) / len(ratings), 3) if ratings else None,
        "negative_share": round(sum(1 for r in ratings if r <= 2) / len(ratings), 3) if ratings else None,
        "penalty": penalty,
        "penalty_reasons": penalised,
        "basis": basis,
    }
