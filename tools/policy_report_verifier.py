"""Ground Agent 3 policy claims against active policy chunks."""
from __future__ import annotations

from typing import Any


class PolicyReportVerificationError(ValueError):
    pass


_FORBIDDEN_OFFER_FIELDS = {
    "offer_id",
    "listed_price",
    "eligible_effective_price",
    "conditional_effective_price",
    "recommended_safe_offer",
    "deal_score",
    "variant_results",
    "market_run_ids",
}


def verify_policy_report(
    report: dict[str, Any], active_chunks: dict[str, dict[str, Any]]
) -> None:
    errors: list[str] = []
    cited: set[str] = set()
    if report.get("country_code") != "IN":
        errors.append("policy report is outside the supported Indian scope")
    if _contains_forbidden_fields(report):
        errors.append("policy report contains offer-specific fields")

    for profile in report.get("policy_profiles") or []:
        retailer = str(profile.get("retailer") or "")
        for policy in profile.get("policies") or []:
            policy_type = str(policy.get("policy_type") or "")
            citations = policy.get("citations") or []
            if policy.get("status") == "EVIDENCED" and not citations:
                errors.append(f"{retailer} {policy_type} has no citation")
            for citation in citations:
                chunk_id = str(citation.get("chunk_id") or "")
                chunk = active_chunks.get(chunk_id)
                if not chunk or not chunk.get("is_active", True):
                    errors.append(f"unknown or inactive policy chunk {chunk_id}")
                    continue
                cited.add(chunk_id)
                if str(chunk.get("retailer") or "") != retailer:
                    errors.append(f"chunk {chunk_id} belongs to another retailer")
                if str(chunk.get("policy_type") or "") != policy_type:
                    errors.append(f"chunk {chunk_id} belongs to another policy type")

    if int(report.get("evidence_chunk_count") or 0) != len(cited):
        errors.append("evidence_chunk_count does not match unique cited chunks")
    if errors:
        raise PolicyReportVerificationError("; ".join(errors))


def _contains_forbidden_fields(value: Any) -> bool:
    if isinstance(value, dict):
        if _FORBIDDEN_OFFER_FIELDS.intersection(value):
            return True
        return any(_contains_forbidden_fields(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_fields(item) for item in value)
    return False
