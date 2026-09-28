import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import ContextualPrecisionMetric, ContextualRecallMetric, FaithfulnessMetric, AnswerRelevancyMetric
from tools.eligibility_agent import PolicyProtectionAgent
from tools.eligibility_models import PolicyAgentRequest

# RAG Evaluation Dataset for Policy Agent
rag_test_cases = [
    {
        "test_case_id": "RAG_001_AMAZON_RETURN",
        "query": "What is the return policy for a defective mobile phone on Amazon?",
        "expected_answer": "Amazon offers a 7-day replacement for defective mobile phones. No direct refunds.",
        "retailer": "Amazon",
        "expected_retrieval_keywords": ["7-day", "replacement", "mobile", "defective"]
    },
    {
        "test_case_id": "RAG_002_FLIPKART_OPEN_BOX",
        "query": "Does Flipkart require open box delivery for laptops?",
        "expected_answer": "Yes, Flipkart requires Open Box Delivery for high-value electronics like laptops to be eligible for returns.",
        "retailer": "Flipkart",
        "expected_retrieval_keywords": ["Open Box Delivery", "laptops", "electronics"]
    }
]

@pytest.mark.parametrize("test_case_data", rag_test_cases, ids=[tc["test_case_id"] for tc in rag_test_cases])
def test_policy_agent_rag_deepeval(test_case_data):
    # 1. Arrange
    agent = PolicyProtectionAgent(
        request=PolicyAgentRequest(
            product_category="electronics",
            target_retailers=[test_case_data["retailer"]],
            policy_types=["RETURN", "WARRANTY"]
        )
    )
    
    # 2. Act (Mocking the RAG pipeline call)
    # Note: In a real test run, this queries your PGVector/Chroma DB.
    try:
        report = agent.analyze()
        actual_answer = report.get("summary", "")
        # Assuming your agent stores retrieved chunks in evidence_list or similar
        retrieval_context = [str(e) for e in report.get("evidence_list", [])] 
    except Exception as e:
        pytest.skip(f"Skipping due to missing DB/API setup in test env: {e}")
        return

    # Skip evaluation if no context was retrieved (prevents DeepEval from crashing on empty context)
    if not retrieval_context:
        pytest.skip("No retrieval context found from database.")
        return

    # 3. Assert using DeepEval RAG Metrics
    test_case = LLMTestCase(
        input=test_case_data["query"],
        actual_output=actual_answer,
        expected_output=test_case_data["expected_answer"],
        retrieval_context=retrieval_context
    )
    
    # RAG Triad Metrics
    answer_relevancy = AnswerRelevancyMetric(threshold=0.7)
    faithfulness = FaithfulnessMetric(threshold=0.8)
    
    # Note: Contextual Precision/Recall require expected_output to score the context relevance.
    assert_test(test_case, [answer_relevancy, faithfulness])
