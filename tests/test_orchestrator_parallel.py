import inspect

from orchestrator import graph, policy_agent_node


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
