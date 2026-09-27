import pytest
from tools.verifier_gate import verify_draft_verdict, VerificationResult


def _make_state(history_days=180, atl=80000.0, avg=95000.0):
    return {
        "history_report": {
            "trend": {
                "total_history_days": history_days,
                "true_atl": atl,
                "overall_avg": avg,
            }
        },
        "market_report": {
            "ranked_offers": [
                {"retailer": "Amazon India", "price": 90000.0, "effective_price": 90000.0},
                {"retailer": "Croma", "price": 92000.0, "effective_price": 92000.0},
            ]
        }
    }


def test_passes_valid_buy_now():
    state = _make_state()
    draft = {
        "decision": "BUY_NOW",
        "target_price": 90000.0,
        "recommended_retailer": "Amazon India"
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is True
    assert result.final_verdict["verification_status"] == "VERIFIED"


def test_rejects_ungrounded_buy_now_price():
    state = _make_state()
    draft = {
        "decision": "BUY_NOW",
        "target_price": 89500.0,  # 500 diff, tolerance is 100
        "recommended_retailer": "Amazon India"
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is False
    assert result.final_verdict["verification_status"] == "REJECTED"


def test_partial_when_retailer_missing():
    state = _make_state()
    draft = {
        "decision": "BUY_NOW",
        "target_price": 90000.0,
        "recommended_retailer": "Flipkart"  # Not in ranked_offers
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is True
    assert result.final_verdict["verification_status"] == "PARTIAL"


def test_rejects_wait_target_above_average():
    state = _make_state(avg=95000.0)
    draft = {
        "decision": "WAIT",
        "target_price": 96000.0,
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is False
    assert result.final_verdict["verification_status"] == "REJECTED"


def test_rejects_wait_target_below_atl_floor():
    state = _make_state(atl=80000.0)
    draft = {
        "decision": "WAIT",
        "target_price": 60000.0,  # Below 80000 * 0.85 = 68000
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is False
    assert result.final_verdict["verification_status"] == "REJECTED"


def test_wait_with_no_target_passes():
    state = _make_state()
    draft = {
        "decision": "WAIT",
        "target_price": None,
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is True
    assert result.final_verdict["verification_status"] == "VERIFIED"


def test_rejects_fabricated_url():
    state = _make_state()
    draft = {
        "decision": "BUY_NOW",
        "target_price": 90000.0,
        "recommended_retailer": "Amazon India",
        "key_evidence": [
            {"source_type": "WEB_SEARCH_URL", "reference_id_or_url": "invalid-url"}
        ]
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is False
    assert result.final_verdict["verification_status"] == "REJECTED"


def test_passes_refuse_correctly():
    state = _make_state(history_days=5)
    draft = {
        "decision": "REFUSE_NO_HISTORY",
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is True
    assert result.final_verdict["verification_status"] == "VERIFIED"


def test_rejects_buy_now_with_too_few_history():
    state = _make_state(history_days=5)
    draft = {
        "decision": "BUY_NOW",
        "target_price": 90000.0,
        "recommended_retailer": "Amazon India"
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is False
    assert result.final_verdict["decision"] == "REFUSE_NO_HISTORY"


def test_deal_score_computed_for_buy_now():
    state = _make_state()
    draft = {
        "decision": "BUY_NOW",
        "target_price": 90000.0,
        "recommended_retailer": "Amazon India"
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is True
    assert "effective_deal_score" in result.final_verdict
    assert result.final_verdict["effective_deal_score"] is not None


def test_timing_rationale_populated():
    state = _make_state()
    draft = {
        "decision": "BUY_NOW",
        "target_price": 90000.0,
        "recommended_retailer": "Amazon India"
    }
    result = verify_draft_verdict(draft, state)
    assert result.passed is True
    assert result.final_verdict["timing_and_safety_rationale"] != ""
