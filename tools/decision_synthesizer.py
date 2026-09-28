"""tools/decision_synthesizer.py — Node 4: Decision Synthesizer (Option A: single-pass)."""
from __future__ import annotations

import json
import os

DECISION_ENUM = frozenset({"BUY_NOW", "WAIT", "REFUSE_NO_HISTORY"})
MIN_HISTORY_DAYS = 14


# ── Input normalisation ───────────────────────────────────────────────────────

def extract_synthesis_inputs(state: dict) -> dict:
    history = state.get("history_report") or {}
    market  = state.get("market_report")  or {}
    policy  = state.get("policy_report")  or {}

    trend = history.get("trend") or {}
    drops = history.get("drops") or {}

    # Prefer conditional offer (has bank promo) over unconditional
    best_offer = (
        market.get("best_conditional_offer")
        or market.get("best_unconditional_offer")
    )
    # Also collect ranked_offers for the verifier gate's price grounding
    ranked = list(market.get("ranked_offers") or [])
    for group in market.get("variant_groups") or []:
        ranked += group.get("ranked_offers") or []

    return {
        # History
        "s_history":         trend.get("s_history"),
        "historical_stance": trend.get("historical_stance", "WAIT"),
        "current_price":     trend.get("current_price"),
        "true_atl":          trend.get("true_atl"),
        "overall_avg":       trend.get("overall_avg"),
        "safe_target_price": drops.get("safe_target_price"),
        "upcoming_sale_name": drops.get("upcoming_sale"),
        "history_analysis":  history.get("llm_analysis", ""),
        "history_data_days": int(trend.get("total_history_days") or 0),

        # Market
        "market_status":     market.get("status"),
        "market_confidence": float(market.get("confidence") or 0.0),
        "best_offer":        best_offer,
        "ranked_offers":     ranked,
        "market_signals":    market.get("signals") or [],
        "upcoming_sales":    market.get("upcoming_sales") or [],
        "market_summary":    market.get("summary", ""),

        # Policy/Eligibility (current output — policy profiles only)
        "policy_status":     policy.get("status"),
        "policy_gaps":       int(policy.get("evidence_gap_count") or 0),
        "policy_summary":    policy.get("summary", ""),
        "cross_retailer":    policy.get("cross_retailer_observations") or [],

        # Note: policy_report.variant_results not yet available (Agent 3 WIP)
        # Synthesizer will use market_report for offer selection until then
    }


# ── LLM system prompt ─────────────────────────────────────────────────────────

SYNTHESIZER_SYSTEM = """\
You are the PriceLens Decision Synthesizer. You receive structured JSON from
three specialist agents and must output a single purchase recommendation.

STRICT RULES:
1. Reply with valid JSON ONLY — no markdown fences, no prose outside JSON.
2. "decision" must be exactly one of: "BUY_NOW", "WAIT", "REFUSE_NO_HISTORY".
3. BUY_NOW → "target_price" must equal best_offer.price from the input.
4. WAIT → "target_price" must equal safe_target_price from the input.
5. "confidence_score" is 0.0–1.0 reflecting signal convergence.
6. "key_evidence" must only reference IDs or URLs present in the input JSON.
7. Never invent prices, sellers, retailers, or URLs.

CONFLICT RESOLUTION MATRIX (apply strictly in this order):
  Rule 1: If policy_gaps > 0 OR policy_summary mentions "No Returns" or "USED" condition → WAIT (Safety Override)
  Rule 2: Any upcoming_sales entry with days_away <= 14 → WAIT
  Rule 3: If best_offer has an effective_price that is <= true_atl (even if historical_stance is WAIT) → BUY_NOW (Promo Override)
  Rule 4: historical_stance == BUY_NOW AND best_offer.promotion_count > 0 → BUY_NOW
  Rule 5: historical_stance == BUY_NOW AND best_offer exists → BUY_NOW
  Rule 6: If history is insufficient or unknown, decide BUY_NOW if strong market offers exist; otherwise WAIT.
  Rule 7: all other cases → WAIT


OUTPUT JSON SCHEMA:
{
  "decision": "BUY_NOW" | "WAIT" | "REFUSE_NO_HISTORY",
  "target_price": <number | null>,
  "recommended_retailer": <string | null>,
  "recommended_seller": <string | null>,
  "condition": "NEW" | "RENEWED" | "USED" | null,
  "confidence_score": <float 0.0–1.0>,
  "primary_rationale": <string, max 200 words>,
  "key_evidence": [
    {
      "source_type": "DUCKDB_RECORD" | "SERP_SHOPPING_OFFER" | "WEB_SEARCH_URL",
      "reference_id_or_url": <string>,
      "fact_summary": <string>,
      "verified": <boolean>
    }
  ]
}
"""


def build_synthesis_prompt(inputs: dict) -> str:
    """Serialise the normalised inputs as the LLM user message."""
    # Exclude ranked_offers (too large) — summarise instead
    safe = {k: v for k, v in inputs.items()
            if k != "ranked_offers" and v is not None}
    safe["ranked_offer_count"] = len(inputs.get("ranked_offers") or [])
    return json.dumps(safe, default=str, ensure_ascii=False, indent=2)


def parse_llm_verdict(content: str) -> dict:
    """Parse and validate LLM JSON output. Raises ValueError on bad output."""
    stripped = content.strip()
    # Strip markdown code fences if LLM added them
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
    verdict = json.loads(stripped.strip())
    if verdict.get("decision") not in DECISION_ENUM:
        raise ValueError(f"Invalid decision enum: {verdict.get('decision')!r}")
    return verdict


# ── Deterministic fallback ────────────────────────────────────────────────────

def deterministic_synthesis(inputs: dict) -> dict:
    """
    Pure-Python conflict resolution matrix — zero LLM, zero hallucination.
    Activated when LLM is offline, key is missing, or output is invalid.
    """
    data_days = inputs.get("history_data_days", 0)

    # ── History limits removed per user request ───────────────────────────────

    stance     = inputs.get("historical_stance", "WAIT")
    best_offer = inputs.get("best_offer") or {}
    upcoming   = inputs.get("upcoming_sales") or []
    s_history  = inputs.get("s_history")

    # Rule 1: Impending sale overrides even a good price
    imminent = [
        s for s in upcoming
        if isinstance(s.get("days_away"), (int, float)) and int(s["days_away"]) <= 14
    ]
    if imminent:
        sale = imminent[0]
        target = inputs.get("safe_target_price") or inputs.get("current_price")
        return {
            "decision": "WAIT",
            "target_price": float(target) if target else None,
            "recommended_retailer": None,
            "recommended_seller": None,
            "condition": "NEW",
            "confidence_score": 0.85,
            "primary_rationale": (
                f"A verified {sale.get('sale_name', 'sale event')} is "
                f"{int(sale['days_away'])} days away. Waiting is recommended "
                "even though the current price is near its historical low."
            ),
            "key_evidence": _history_evidence(inputs),
            "_synthesis_mode": "deterministic_fallback",
        }

    # Rules 2 + 3: BUY_NOW conditions
    if stance == "BUY_NOW" and best_offer:
        has_promo = int(best_offer.get("promotion_count") or 0) > 0
        price     = best_offer.get("price") or best_offer.get("effective_price")
        retailer  = best_offer.get("retailer") or best_offer.get("marketplace")
        seller    = best_offer.get("seller_name") or best_offer.get("seller")
        reason    = "active bank promotion at" if has_promo else "verified offer at"
        current_price = inputs.get('current_price') or 0
        rationale = (
            f"S_history={s_history} indicates the current price "
            f"(₹{current_price:,.0f}) is near the historical low. "
            f"There is a {reason} ₹{float(price):,.0f} from {retailer}. Recommended: BUY_NOW."
            if price else
            f"S_history={s_history} — current price near historical low. BUY_NOW."
        )
        confidence = round(
            0.50 * min(1.0, float(s_history or 0) / 100.0)
            + 0.30 * float(inputs.get("market_confidence") or 0)
            + 0.20 * (0.0 if inputs.get("policy_gaps") else 1.0),
            2,
        )
        return {
            "decision": "BUY_NOW",
            "target_price": float(price) if price else None,
            "recommended_retailer": retailer,
            "recommended_seller": seller,
            "condition": str(best_offer.get("condition") or "NEW").upper(),
            "confidence_score": confidence,
            "primary_rationale": rationale,
            "key_evidence": _history_evidence(inputs) + _offer_evidence(best_offer),
            "_synthesis_mode": "deterministic_fallback",
        }

    # Rule 4: Default WAIT
    target = inputs.get("safe_target_price") or inputs.get("true_atl")
    sale_name = inputs.get("upcoming_sale_name")
    return {
        "decision": "WAIT",
        "target_price": float(target) if target else None,
        "recommended_retailer": None,
        "recommended_seller": None,
        "condition": "NEW",
        "confidence_score": 0.70,
        "primary_rationale": (
            f"S_history={s_history} — current price is above the preferred "
            "historical buying range. "
            + (f"Wait for price near ₹{float(target):,.0f}" if target else "Wait for a price drop.")
            + (f" during {sale_name}." if sale_name else ".")
        ),
        "key_evidence": _history_evidence(inputs),
        "_synthesis_mode": "deterministic_fallback",
    }


# ── Evidence helpers ──────────────────────────────────────────────────────────

def _history_evidence(inputs: dict) -> list:
    cid = inputs.get("canonical_id") or "UNKNOWN"
    price = inputs.get("current_price")
    if not price:
        return []
    return [{
        "source_type": "DUCKDB_RECORD",
        "reference_id_or_url": cid,
        "fact_summary": (
            f"S_history={inputs.get('s_history')}, "
            f"current=₹{float(price):,.0f}, "
            f"ATL=₹{float(inputs.get('true_atl') or 0) if inputs.get('true_atl') else 'N/A'}, "
            f"history_days={inputs.get('history_data_days')}"
        ),
        "verified": True,
    }]


def _offer_evidence(offer: dict) -> list:
    if not offer:
        return []
    offer_ref = str(offer.get("offer_id") or offer.get("external_id") or "")
    price = offer.get("price") or offer.get("effective_price") or 0
    retailer = offer.get("retailer") or offer.get("marketplace") or "?"
    return [{
        "source_type": "SERP_SHOPPING_OFFER",
        "reference_id_or_url": offer_ref,
        "fact_summary": f"{retailer}: ₹{float(price):,.0f}",
        "verified": bool(offer_ref),
    }]


# ── Top-level entry point ─────────────────────────────────────────────────────

def run_synthesizer(state: dict) -> dict:
    """Called by decision_synthesizer_node in orchestrator.py."""
    inputs = extract_synthesis_inputs(state)
    inputs["canonical_id"] = state.get("canonical_id")

    api_key  = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    if api_key:
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                base_url=base_url, api_key=api_key,
                model=model, temperature=0,
            )
            response = llm.invoke([
                ("system", SYNTHESIZER_SYSTEM),
                ("user", build_synthesis_prompt(inputs)),
            ])
            verdict = parse_llm_verdict(str(response.content))
            verdict["_synthesis_mode"] = "llm"
            return verdict
        except Exception:
            pass  # fall through to deterministic

    return deterministic_synthesis(inputs)
