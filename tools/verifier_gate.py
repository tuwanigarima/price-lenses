"""tools/verifier_gate.py — Node 5: Deterministic Verifier Gate."""
from __future__ import annotations
from dataclasses import dataclass, field

PRICE_TOLERANCE_INR: float = 100.0  # ₹100 lenient (floating-point safe)
MIN_HISTORY_DAYS: int = 30


@dataclass
class VerificationResult:
    passed: bool
    reason: str
    final_verdict: dict = field(default_factory=dict)


def verify_draft_verdict(
    draft: dict,
    state: dict,
    *,
    price_tolerance_inr: float = PRICE_TOLERANCE_INR,
) -> VerificationResult:
    decision  = str(draft.get("decision") or "")
    history   = state.get("history_report") or {}
    trend     = history.get("trend") or {}
    data_days = int(trend.get("total_history_days") or 0)

    # ── Check 1 ───────────────────────────────────────────────────────────────
    if data_days < MIN_HISTORY_DAYS and decision != "REFUSE_NO_HISTORY":
        return _reject(
            draft,
            f"VERIFIER_REJECT: {decision!r} proposed with only {data_days} "
            f"history days (minimum {MIN_HISTORY_DAYS}). Overriding to REFUSE_NO_HISTORY.",
            override_decision="REFUSE_NO_HISTORY",
        )

    # ── Check 2 ───────────────────────────────────────────────────────────────
    verification_status = "VERIFIED"
    if decision == "BUY_NOW":
        result = _check_buy_now_price(draft, state, price_tolerance_inr)
        if not result.passed:
            return result
        if "PARTIAL" in result.reason:
            verification_status = "PARTIAL"
    elif decision == "WAIT":
        result = _check_wait_target(draft, trend)
        if not result.passed:
            return result

    # ── Check 3 ───────────────────────────────────────────────────────────────
    for item in draft.get("key_evidence") or []:
        src = str(item.get("source_type") or "")
        ref = str(item.get("reference_id_or_url") or "")
        if src == "WEB_SEARCH_URL" and not ref.startswith(("http://", "https://")):
            return _reject(
                draft,
                f"VERIFIER_REJECT: WEB_SEARCH_URL evidence has invalid URL: {ref!r}",
            )

    # ── All checks passed ─────────────────────────────────────────────────────
    return VerificationResult(
        passed=True,
        reason="VERIFIED_SUCCESS",
        final_verdict={
            **draft,
            "schema_version": "1.0",
            "verification_status": verification_status,
            "effective_deal_score": _compute_deal_score(draft, state),
            "timing_and_safety_rationale": _build_timing_rationale(draft, state),
        },
    )


def _check_buy_now_price(draft: dict, state: dict, tolerance: float) -> VerificationResult:
    target   = draft.get("target_price")
    retailer = str(draft.get("recommended_retailer") or "").lower()
    if target is None:
        return _reject(draft, "VERIFIER_REJECT: BUY_NOW verdict has no target_price.")

    market = state.get("market_report") or {}
    ranked = list(market.get("ranked_offers") or [])
    for group in market.get("variant_groups") or []:
        ranked += group.get("ranked_offers") or []

    candidate_prices = [
        float(o.get("price") or o.get("effective_price") or 0)
        for o in ranked
        if retailer in str(o.get("retailer") or o.get("marketplace") or "").lower()
        and float(o.get("price") or o.get("effective_price") or 0) > 0
    ]

    if not candidate_prices:
        # Soft pass — retailer not in ranked_offers (could be new live data)
        return VerificationResult(
            passed=True,
            reason="VERIFIER_PARTIAL: retailer not found in ranked_offers",
            final_verdict={
                **draft,
                "schema_version": "1.0",
                "verification_status": "PARTIAL",
                "effective_deal_score": _compute_deal_score(draft, state),
                "timing_and_safety_rationale": _build_timing_rationale(draft, state),
            },
        )

    db_price = min(candidate_prices)
    if abs(float(target) - db_price) > tolerance:
        return _reject(
            draft,
            f"VERIFIER_REJECT: BUY_NOW target ₹{float(target):,.0f} differs "
            f"from grounded market price ₹{db_price:,.0f} by more than ₹{tolerance:.0f}.",
        )
    return VerificationResult(passed=True, reason="price_grounded")


def _check_wait_target(draft: dict, trend: dict) -> VerificationResult:
    target = draft.get("target_price")
    if target is None:
        return VerificationResult(passed=True, reason="wait_no_target_acceptable")
    target = float(target)
    atl = trend.get("true_atl")
    avg = trend.get("overall_avg")
    if avg is not None and target >= float(avg):
        return _reject(draft,
            f"VERIFIER_REJECT: WAIT target ₹{target:,.0f} must be below overall_avg ₹{float(avg):,.0f}.")
    if atl is not None and target < float(atl) * 0.85:
        return _reject(draft,
            f"VERIFIER_REJECT: WAIT target ₹{target:,.0f} is below ATL ₹{float(atl):,.0f} × 0.85.")
    return VerificationResult(passed=True, reason="wait_target_grounded")


def _compute_deal_score(draft: dict, state: dict) -> float | None:
    if draft.get("decision") != "BUY_NOW":
        return None
    trend  = (state.get("history_report") or {}).get("trend") or {}
    market = state.get("market_report") or {}
    policy = state.get("policy_report") or {}
    s      = float(trend.get("s_history") or 50)
    conf   = float(market.get("confidence") or 0.5) * 100
    pol_ok = 100.0 if not int(policy.get("evidence_gap_count") or 0) else 60.0
    return round(max(0.0, min(100.0, 0.50 * s + 0.30 * conf + 0.20 * pol_ok)), 2)


def _build_timing_rationale(draft: dict, state: dict) -> str:
    parts    = []
    decision = str(draft.get("decision") or "")
    trend    = (state.get("history_report") or {}).get("trend") or {}
    market   = state.get("market_report") or {}
    policy   = state.get("policy_report") or {}

    s = trend.get("s_history")
    if s is not None:
        parts.append(f"Price health score: {s:.0f}/100.")

    for sale in (market.get("upcoming_sales") or [])[:1]:
        parts.append(
            f"Next sale: {sale.get('sale_name', 'N/A')} "
            f"in ~{sale.get('days_away', '?')} days."
        )

    if decision == "BUY_NOW":
        r = draft.get("recommended_retailer") or "the recommended retailer"
        t = draft.get("target_price")
        parts.append(f"BUY_NOW from {r}" + (f" at ₹{float(t):,.0f}." if t else "."))
    elif decision == "WAIT":
        t = draft.get("target_price")
        parts.append("WAIT" + (f" for price near ₹{float(t):,.0f}." if t else " for a price drop."))
    else:
        parts.append(
            "Insufficient price history — cannot make a reliable recommendation. "
            "Please try again once more data is available."
        )

    summary = str(policy.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    return " ".join(parts)


def _reject(draft: dict, reason: str, *, override_decision: str = "REFUSE_NO_HISTORY") -> VerificationResult:
    return VerificationResult(
        passed=False,
        reason=reason,
        final_verdict={
            "decision": override_decision,
            "target_price": None,
            "recommended_retailer": None,
            "recommended_seller": None,
            "condition": None,
            "confidence_score": 0.0,
            "primary_rationale": reason,
            "key_evidence": [],
            "schema_version": "1.0",
            "verification_status": "REJECTED",
            "effective_deal_score": None,
            "timing_and_safety_rationale": reason,
            "_original_draft": draft,
        },
    )
