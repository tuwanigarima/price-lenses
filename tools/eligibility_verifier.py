"""Deterministic grounding checks for Agent 3 reports."""
from __future__ import annotations

from typing import Any


class EligibilityReportVerificationError(ValueError):
    pass


def verify_eligibility_report(
    report: dict[str, Any],
    offers: list[dict[str, Any]],
    active_policy_chunks: dict[str, dict[str, Any]] | None = None,
) -> None:
    offer_by_id = {str(row["offer_id"]): row for row in offers if row.get("offer_id")}
    chunks = active_policy_chunks or {}
    errors: list[str] = []
    seen: set[str] = set()
    for group in report.get("variant_results") or []:
        group_key = str(group.get("variant_key") or "")
        for assessment in group.get("offers") or []:
            offer_id = str(assessment.get("offer_id") or "")
            source = offer_by_id.get(offer_id)
            if source is None:
                errors.append(f"unknown offer {offer_id}")
                continue
            if offer_id in seen:
                errors.append(f"offer {offer_id} appears more than once")
            seen.add(offer_id)
            if assessment.get("variant_key") != group_key:
                errors.append(f"offer {offer_id} is in the wrong variant group")
            listed = assessment.get("listed_price")
            if listed is None or round(float(listed), 2) != round(float(source.get("price") or 0), 2):
                errors.append(f"offer {offer_id} has an ungrounded listed price")
            expected = assessment.get("score_breakdown") or {}
            if all(expected.get(key) is not None for key in ("price", "seller", "warranty", "delivery")):
                recomputed = round(
                    0.40 * float(expected["price"])
                    + 0.30 * float(expected["seller"])
                    + 0.20 * float(expected["warranty"])
                    + 0.10 * float(expected["delivery"]),
                    2,
                )
                if recomputed != round(float(assessment.get("deal_score") or 0), 2):
                    errors.append(f"offer {offer_id} deal score does not recompute")
            if assessment.get("safety_status") == "DISQUALIFIED":
                selected_ids = {
                    str((group.get("recommended_safe_offer") or {}).get("offer_id") or ""),
                }
                if offer_id in selected_ids:
                    errors.append(f"disqualified offer {offer_id} was recommended")
            for chunk_id in assessment.get("evidence_chunk_ids") or []:
                chunk = chunks.get(str(chunk_id))
                if chunk is None or not chunk.get("is_active", True):
                    errors.append(f"offer {offer_id} cites inactive or unknown chunk {chunk_id}")
    if report.get("total_offers_evaluated") != len(seen):
        errors.append("total_offers_evaluated does not match unique assessed offers")
    forbidden = {"BUY_NOW", "WAIT", "BUY_ABROAD"}
    if forbidden.intersection(report.get("signals", []) or []):
        errors.append("Agent 3 returned a final purchase decision")
    if errors:
        raise EligibilityReportVerificationError("; ".join(errors))
