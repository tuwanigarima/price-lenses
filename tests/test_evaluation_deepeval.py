import json
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCaseParams
from tools.decision_synthesizer import run_synthesizer
from orchestrator import PriceLensState

# Load Golden Dataset
with open("tests/eval_dataset.json", "r") as f:
    eval_dataset = json.load(f)

# Define G-Eval custom metric to check if the LLM followed the exact expected decision (BUY_NOW vs WAIT)
decision_metric = GEval(
    name="Decision Alignment",
    criteria="Determine whether the actual output decision strictly aligns with the expected output decision.",
    evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.EXPECTED_OUTPUT],
    strict_mode=True
)

@pytest.mark.parametrize("test_case_data", eval_dataset, ids=[tc["test_case_id"] for tc in eval_dataset])
def test_synthesizer_decision_deepeval(test_case_data):
    # 1. Arrange: Setup the mocked State for the Synthesizer
    state = PriceLensState(
        query=test_case_data["input_state"]["query"],
        history_report=test_case_data["input_state"]["history_report"],
        market_report=test_case_data["input_state"]["market_report"],
        policy_report=test_case_data["input_state"]["policy_report"],
        draft_verdict={},
        final_verdict={},
        errors=[]
    )
    
    # 2. Act: Run the Synthesizer
    draft_verdict = run_synthesizer(state)
    actual_decision = draft_verdict.get("decision", "UNKNOWN")
    actual_reasoning = draft_verdict.get("reasoning", "")
    
    actual_combined_output = f"DECISION: {actual_decision}\nREASONING: {actual_reasoning}"
    expected_decision = test_case_data["expected_decision"]
    
    # Format the input properly for DeepEval
    input_text = (
        f"Query: {state['query']}\n"
        f"History: {state['history_report']}\n"
        f"Market: {state['market_report']}\n"
        f"Policy: {state['policy_report']}"
    )

    # 3. Assert using DeepEval
    test_case = LLMTestCase(
        input=input_text,
        actual_output=actual_combined_output,
        expected_output=f"DECISION: {expected_decision}",
        retrieval_context=[str(state["policy_report"])], # Useful if we test hallucination of policies
    )
    
    # Check if facts are faithful to the context and decision aligns
    faithfulness_metric = FaithfulnessMetric(threshold=0.8)
    
    assert_test(test_case, [decision_metric, faithfulness_metric])

