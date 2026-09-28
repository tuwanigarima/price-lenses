import inspect
import builtins

import pytest

from orchestrator import graph, input_resolver_node, policy_agent_node, verifier_gate_node


def test_all_three_specialists_fan_out_independently_from_input_resolver():
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}

    assert ("input_resolver", "history_agent") in edges
    assert ("input_resolver", "market_agent") in edges
    assert ("input_resolver", "policy_agent") in edges
    assert ("market_agent", "policy_agent") not in edges


def test_policy_agent_node_has_no_market_report_or_offer_dependency():
    source = inspect.getsource(policy_agent_node)

    assert "market_report" not in source
    assert "offer_ids" not in source
    assert "run_ids" not in source


@pytest.mark.parametrize("missing_module", ["bs4", "pypdf"])
def test_policy_import_failure_returns_error_report(monkeypatch, missing_module):
    original_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "tools.eligibility_agent":
            raise ModuleNotFoundError(
                f"No module named '{missing_module}'", name=missing_module
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    result = policy_agent_node({"query": "Oppo Reno14 5G"})

    assert set(result) == {"policy_report"}
    report = result["policy_report"]
    assert report["status"] == "error"
    assert missing_module in report["warnings"][0]
    assert report["agent"] == "policy_purchase_protection_analyst"


@pytest.mark.parametrize("title,expected_id", [
    ("Samsung Galaxy A15 128GB Blue Android 14", None),
    ("Apple iPhone 14 128GB Blue", None),
    ("Apple iPhone 14 256GB Black", None),
    ("Apple iPhone 14 256GB Blue", "B012345678"),
])
def test_history_resolver_requires_requested_product_and_variant(monkeypatch, title, expected_id):
    from tools import analytics

    class Connection:
        closed = False
        def cursor(self): return self
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, *_): pass
        def fetchall(self): return [("B012345678", title, 0.9, True)]
        def close(self): self.closed = True

    connection = Connection()
    monkeypatch.setattr(analytics, "get_db_connection", lambda: connection)
    result = input_resolver_node({"query": "iPhone 14 256 GB Blue"})
    assert result["canonical_id"] == expected_id
    assert connection.closed


def test_invalid_model_citation_recovers_without_discarding_history():
    result = verifier_gate_node({
        "canonical_id": "B0B6BLTGTT",
        "history_report": {"trend": {
            "total_history_days": 325, "historical_stance": "WAIT",
            "current_price": 1499, "true_atl": 899, "overall_avg": 1281,
            "s_history": 0,
        }, "drops": {"safe_target_price": 899}},
        "market_report": {"status": "insufficient_evidence"},
        "draft_verdict": {
            "decision": "WAIT", "target_price": 899, "_synthesis_mode": "llm",
            "key_evidence": [{"source_type": "WEB_SEARCH_URL", "reference_id_or_url": "Sale name instead of URL"}],
        },
    })
    final = result["final_verdict"]
    assert final["decision"] == "WAIT"
    assert final["verification_status"] == "VERIFIED"
    assert final["_synthesis_mode"] == "deterministic_recovery"
    assert final["key_evidence"][0]["reference_id_or_url"] == "B0B6BLTGTT"
    assert not result["errors"]
