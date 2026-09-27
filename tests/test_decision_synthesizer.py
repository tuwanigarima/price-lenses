import pytest
from tools.decision_synthesizer import (
    extract_synthesis_inputs,
    deterministic_synthesis,
    run_synthesizer,
)


def _make_state(
    history_days=180,
    stance="WAIT",
    sale_days_away=None,
    promo=False,
    history_empty=False,
):
    if history_empty:
        return {}

    state = {
        "history_report": {
            "trend": {
                "total_history_days": history_days,
                "historical_stance": stance,
                "s_history": 87 if stance == "BUY_NOW" else 40,
                "current_price": 89999.0,
                "true_atl": 84999.0,
                "overall_avg": 92000.0,
            },
            "drops": {
                "safe_target_price": 84999.0,
                "upcoming_sale": "Test Sale" if sale_days_away else None,
            },
        },
        "market_report": {
            "status": "complete",
            "confidence": 0.9,
            "upcoming_sales": [
                {"sale_name": "Test Sale", "days_away": sale_days_away}
            ] if sale_days_away else [],
            "best_unconditional_offer": {
                "price": 89999.0,
                "retailer": "Amazon India",
                "condition": "NEW",
            } if not promo else None,
            "best_conditional_offer": {
                "price": 84999.0,
                "retailer": "Amazon India",
                "condition": "NEW",
                "promotion_count": 1,
            } if promo else None,
        }
    }
    return state


def test_refuse_when_history_below_threshold():
    state = _make_state(history_days=10, stance="BUY_NOW")
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["decision"] == "REFUSE_NO_HISTORY"


def test_refuse_when_history_report_empty():
    state = _make_state(history_empty=True)
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["decision"] == "REFUSE_NO_HISTORY"


def test_impending_sale_overrides_buy_now():
    state = _make_state(history_days=180, stance="BUY_NOW", sale_days_away=12)
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["decision"] == "WAIT"


def test_sale_far_away_does_not_block_buy_now():
    state = _make_state(history_days=180, stance="BUY_NOW", sale_days_away=30)
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["decision"] == "BUY_NOW"


def test_promo_at_historical_low():
    state = _make_state(history_days=180, stance="BUY_NOW", promo=True)
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["decision"] == "BUY_NOW"
    assert result["target_price"] == 84999.0


def test_wait_when_stance_is_wait():
    state = _make_state(history_days=180, stance="WAIT")
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["decision"] == "WAIT"


def test_wait_uses_safe_target_price():
    state = _make_state(history_days=180, stance="WAIT")
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["target_price"] == 84999.0


def test_buy_now_uses_best_offer_price():
    state = _make_state(history_days=180, stance="BUY_NOW", promo=False)
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["target_price"] == 89999.0


def test_llm_fallback_on_bad_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "bad_key")
    state = _make_state(history_days=180, stance="WAIT")
    result = run_synthesizer(state)
    assert result["decision"] == "WAIT"
    assert result["_synthesis_mode"] == "deterministic_fallback"


def test_all_reports_empty_returns_refuse():
    state = {}
    inputs = extract_synthesis_inputs(state)
    result = deterministic_synthesis(inputs)
    assert result["decision"] == "REFUSE_NO_HISTORY"
