from tools.eligibility_agent import PolicyProtectionAgent
from tools.eligibility_models import PolicyAgentRequest, PolicyHit
from types import SimpleNamespace


class FakeDatabase:
    def __init__(self):
        self.finished = None
        self.context = None

    def start_analysis(self, _query, _canonical_id, context):
        self.context = context
        return "analysis-1"

    def finish_analysis(self, analysis_id, **kwargs):
        self.finished = (analysis_id, kwargs)

    def policy_chunks_by_ids(self, ids):
        return {
            value: {
                "chunk_id": value,
                "is_active": True,
                "retailer": value.split("-")[0],
                "policy_type": "RETURN",
            }
            for value in ids
        }

    def __getattr__(self, name):
        if "offer" in name or "promotion" in name or "seller" in name:
            raise AssertionError(f"Agent 3 attempted offer-dependent database call: {name}")
        raise AttributeError(name)


class FakeRetriever:
    def search(self, *_args, retailers, **_kwargs):
        retailer = retailers[0]
        return [
            PolicyHit(
                chunk_id=f"{retailer}-return",
                document_version_id=f"{retailer}-document",
                retailer=retailer,
                policy_type="RETURN",
                content=(
                    "Electronics may have category-specific return or replacement rules. "
                    "Customers must review the applicable window and retain the original "
                    "packaging and purchase documentation."
                ),
                source_url="https://example.test/returns",
                relevance=0.03,
                retrieval_sources=("keyword", "semantic"),
            )
        ]


def test_one_retailer_failure_preserves_other_policy_evidence():
    class PartlyFailingRetriever(FakeRetriever):
        def search(self, *args, retailers, **kwargs):
            if retailers == ["Amazon India"]:
                raise TimeoutError("fixture timeout")
            return super().search(*args, retailers=retailers, **kwargs)

    report = PolicyProtectionAgent(FakeDatabase(), PartlyFailingRetriever()).analyze(
        PolicyAgentRequest(query="phone", retailers=("Amazon India", "Flipkart"), policy_types=("RETURN",))
    )
    assert report["status"] == "partial"
    assert report["evidence_chunk_count"] == 1
    assert any("RETRIEVAL_ERROR: Amazon India" in warning for warning in report["warnings"])


def test_agent3_builds_policy_profiles_without_reading_offers():
    database = FakeDatabase()
    request = PolicyAgentRequest(
        query="Phone 8GB/256GB Graphite",
        product_category="smartphone",
        retailers=("Amazon India", "Flipkart"),
        policy_types=("RETURN", "WARRANTY"),
    )

    report = PolicyProtectionAgent(database, FakeRetriever()).analyze(request)

    assert report["status"] == "partial"
    assert report["product_category"] == "smartphone"
    assert report["evidence_chunk_count"] == 2
    assert report["evidence_gap_count"] == 2
    assert {profile["retailer"] for profile in report["policy_profiles"]} == {
        "Amazon India",
        "Flipkart",
    }
    assert "offer_id" not in str(report)
    assert database.context["offer_independent"] is True
    assert database.finished[1]["status"] == "partial"
    stages = [event["stage"] for event in report["agent_trace"]]
    assert stages[-3:] == [
        "build_policy_profiles",
        "generate_policy_summary",
        "verify_policy_grounding",
    ]
    summary_event = report["agent_trace"][-2]
    assert summary_event["status"] == "skipped"
    assert "purchase-protection" in summary_event["display_prompt"]
    assert report["agent_trace"][-1]["tool"] == "verify_policy_report"


def test_agent3_streams_every_trace_event_to_callback():
    streamed = []
    report = PolicyProtectionAgent(
        FakeDatabase(), FakeRetriever(), trace_callback=streamed.append
    ).analyze(
        PolicyAgentRequest(
            query="phone",
            retailers=("Flipkart",),
            policy_types=("RETURN",),
        )
    )

    assert streamed == report["agent_trace"]
    assert [event["tool"] for event in streamed] == [
        "search_retailer_policies",
        "search_retailer_policies",
        "classify_policy_coverage",
        "summarize_policy_profiles",
        "verify_policy_report",
    ]


def test_agent3_llm_policy_plan_preserves_every_required_retailer(monkeypatch):
    import langchain_openai

    class FakeChatOpenAI:
        def __init__(self, **_kwargs): pass
        def bind_tools(self, _schemas): return self
        def invoke(self, _prompt):
            return SimpleNamespace(
                tool_calls=[{
                    "name": "search_retailer_policies",
                    "args": {
                        "retailer": "Amazon India",
                        "question": "phone return and warranty rules",
                    },
                }]
            )

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)
    settings = SimpleNamespace(
        llm_api_key="test-only-key",
        llm_model="gpt-5-mini",
        llm_base_url="https://api.openai.com/v1",
    )
    agent = PolicyProtectionAgent(
        FakeDatabase(),
        FakeRetriever(),
        enable_llm_summary=True,
        llm_settings=settings,
    )
    request = PolicyAgentRequest(
        query="phone",
        retailers=("Amazon India", "Flipkart"),
        policy_types=("RETURN",),
    )
    trace = []

    plan = agent._plan_policy_searches(request, trace)

    assert [item["retailer"] for item in plan] == ["Amazon India", "Flipkart"]
    assert plan[0]["question"] == "phone return and warranty rules"
    assert trace[0]["source"] == "llm"


def test_agent3_rejects_error_pages_as_policy_evidence():
    assert PolicyProtectionAgent._usable_policy_hit({
        "content": (
            "We are sorry. We cannot find the page you were trying to reach. "
            "Please visit customer service for assistance with your request."
        )
    }) is False
    assert PolicyProtectionAgent._usable_policy_hit({
        "content": (
            "Electronics can be returned within the documented category window when "
            "the item is eligible and the customer supplies the required evidence."
        )
    }) is True


def test_combined_policy_chunk_can_ground_explicit_topics_only():
    chunk = {
        "policy_type": "RETURN",
        "heading_path": "Cancellation and replacement",
        "content": (
            "An order may be cancelled before dispatch. A defective product may be "
            "replaced after verification."
        ),
    }

    from tools.policy_types import chunk_supports_policy_type

    assert chunk_supports_policy_type(chunk, "RETURN") is True
    assert chunk_supports_policy_type(chunk, "CANCELLATION") is True
    assert chunk_supports_policy_type(chunk, "REPLACEMENT") is True
    assert chunk_supports_policy_type(chunk, "WARRANTY") is False


def test_deterministic_summary_names_every_retailer_and_gap():
    payload = {
        "evidence_gap_count": 1,
        "policy_profiles": [
            {
                "retailer": "Amazon India",
                "policies": [{"policy_type": "RETURN", "status": "EVIDENCED"}],
                "evidence_gaps": [],
            },
            {
                "retailer": "Flipkart",
                "policies": [{"policy_type": "FAQ", "status": "MISSING"}],
                "evidence_gaps": ["FAQ"],
            },
        ],
    }

    summary = PolicyProtectionAgent._deterministic_summary(payload)

    assert "Amazon India: evidenced RETURN; missing none" in summary
    assert "Flipkart: evidenced none; missing FAQ" in summary
