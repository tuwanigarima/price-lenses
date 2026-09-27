import re
from typing import TypedDict, Any
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI

# 1. Define the LangGraph State
class PriceLensState(TypedDict, total=False):
    query: str
    canonical_id: str
    product_title: str
    deadline_days: int
    force_market_refresh: bool
    market_provider_policy: str
    bank: str
    card_type: str
    wants_emi: bool
    product_category: str
    retailer_scope: list[str]
    st_ctx: Any
    st_containers: dict
    
    # Reports from parallel agents (Disjoint state keys for safe concurrent fan-out)
    history_report: dict
    market_report: dict
    policy_report: dict
    
    # Final outputs
    draft_verdict: dict
    final_verdict: dict
    errors: list[str]

# ==========================================
# 2. Define the Nodes
# ==========================================
def input_resolver_node(state: PriceLensState):
    """Node 0: Parses raw user query to extract ASIN/canonical_id"""
    from tools.analytics import get_db_connection
    from tools.market_matching import product_relevance
    
    query = state.get("query", "").strip()
    asin = None
    title = query
    errors = []
    
    # 1. Regex for Amazon URL
    dp_match = re.search(r"/dp/([A-Z0-9]{10})", query)
    if dp_match:
        asin = dp_match.group(1)
    # 2. Regex for Raw ASIN
    elif re.match(r"^[A-Z0-9]{10}$", query):
        asin = query
        
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if asin:
                cur.execute("SELECT title FROM products WHERE canonical_id = %s", (asin,))
                row = cur.fetchone()
                if row:
                    title = row[0]
                else:
                    errors.append(f"ASIN {asin} not found in database.")
            else:
                # Text input may match multiple storage/colour variants. Resolve only
                # when one product is clearly stronger; otherwise Agent 2 asks for
                # clarification instead of binding the graph to an accessory.
                # Fuzzy search
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
                cur.execute(
                    "SELECT canonical_id, title FROM products "
                    "ORDER BY word_similarity(%s, title) DESC LIMIT 50",
                    (query,)
                )
                ranked = sorted(
                    (
                        (product_relevance(query, candidate_title), candidate_id, candidate_title)
                        for candidate_id, candidate_title in cur.fetchall()
                    ),
                    reverse=True,
                )
                ranked = [candidate for candidate in ranked if candidate[0] >= 0.10]
                if ranked:
                    _score, asin, title = ranked[0]
    except Exception as e:
        errors.append(str(e))
    
    category_text = f"{query} {title}".lower()
    category_rules = (
        ("smartphone", ("phone", "iphone", "galaxy", "pixel", "mobile")),
        ("laptop", ("laptop", "macbook", "notebook")),
        ("television", ("television", " tv ", "smart tv")),
        ("tablet", ("tablet", "ipad")),
        ("smartwatch", ("watch", "smartwatch")),
        ("audio", ("headphone", "earbud", "speaker", "soundbar")),
        ("camera", ("camera", "dslr", "mirrorless")),
    )
    product_category = next(
        (
            category
            for category, keywords in category_rules
            if any(keyword in f" {category_text} " for keyword in keywords)
        ),
        "electronics",
    )
    return {
        "canonical_id": asin,
        "product_title": title,
        "product_category": product_category,
        "retailer_scope": [
            "Amazon India",
            "Flipkart",
            "Croma",
            "Reliance Digital",
            "Vijay Sales",
        ],
        "errors": errors,
    }

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
import os

@tool
def check_price_trend(canonical_id: str) -> dict:
    """Gets the mathematical baseline (ATL, ATH, current price, and S_history score). Use this first."""
    from tools.analytics import query_historical_trend
    return query_historical_trend(canonical_id)

@tool
def get_historical_sale_drops(canonical_id: str) -> dict:
    """Finds what price the product historically dropped to during mega-sales. Use if product is inflated."""
    from tools.analytics import query_sale_event_drops
    return query_sale_event_drops(canonical_id)

def history_agent_node(state: PriceLensState):
    """Node 1: Runs the History Analysis Tools via LLM ReAct Agent"""
    from tools.analytics import query_historical_trend, query_sale_event_drops
    
    ctx = state.get("st_ctx")
    containers = state.get("st_containers") or {}
    a1_container = containers.get("history_agent")
    
    if ctx:
        import threading
        from streamlit.runtime.scriptrunner import add_script_run_ctx
        add_script_run_ctx(threading.current_thread(), ctx)
        
    agent_trace = []
        
    def stream_trace(msg, _trace=agent_trace):
        _trace.append(msg)
        if a1_container:
            if isinstance(msg, dict):
                status = msg.get('status', '')
                icon = {"completed": "✓", "skipped": "○", "fallback": "△", "error": "✕"}.get(status, "•")
                stage = str(msg.get('stage') or '').replace('_', ' ').title()
                a1_container.markdown(f"**{icon} {stage}**  \n<small>{msg.get('output_summary', '')}</small>", unsafe_allow_html=True)
            else:
                a1_container.code(msg, language="text")

    asin = state.get("canonical_id")
    if not asin:
        stream_trace({
            "stage": "understand_request",
            "tool": "check_canonical_id",
            "status": "error",
            "source": "system",
            "input_summary": "No verified product ID",
            "output_summary": "Historical analysis skipped because product ID is missing",
            "duration_ms": 0,
            "display_prompt": None
        })
        return {"history_report": {"agent_trace": agent_trace}}
        
    # We still fetch the raw deterministic dicts so the Streamlit UI can render the charts flawlessly
    trend = query_historical_trend(asin)
    drops = {}
    if trend.get("historical_stance") == "WAIT":
        drops = query_sale_event_drops(asin)

    llm_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    llm_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    llm_model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

   
        
    try:
        if not llm_api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        llm = ChatOpenAI(
                base_url=llm_base_url,
                api_key=llm_api_key,
                model=llm_model,
        )

        # result = llm.invoke("crack a joke");
        # print('result',result);

        
        system_prompt = (
            "You are the History & Trend Analyst Agent for PriceLens.\n"
            "Your objective is to analyze the historical price trajectory of a product and determine if today is a financially optimal time to buy.\n\n"
            "INSTRUCTIONS:\n"
            "1. Call `check_price_trend` for the product.\n"
            "2. Analyze the JSON. Is the product near its All-Time Low (S_history >= 75)?\n"
            "3. If it is expensive (S_history < 75), call `get_historical_sale_drops` to find the target wait price.\n"
            "4. Write a 3-sentence financial analysis explaining the momentum and seasonality. End with a clear BUY_NOW or WAIT stance."
        )
        
        agent = create_react_agent(llm, tools=[check_price_trend, get_historical_sale_drops], prompt=system_prompt)
        
        # 5. Capture the exact thought process (Trace Logging)
        # Suppress system prompt display for cleaner UI.
        
        final_state = None
        for step in agent.stream({"messages": [("user", f"Analyze the historical price for ASIN: {asin}")]}):
            for node_name, node_state in step.items():
                messages = node_state.get("messages", [])
                if not isinstance(messages, list):
                    messages = [messages]
                
                if node_name == "agent":
                    for msg in messages:
                        if hasattr(msg, 'tool_calls') and msg.tool_calls:
                            for tc in msg.tool_calls:
                                stream_trace({
                                    "stage": f"call_{tc['name']}",
                                    "tool": tc['name'],
                                    "status": "completed",
                                    "source": "database",
                                    "input_summary": f"Arguments: {tc['args']}",
                                    "output_summary": "Executed database query",
                                    "duration_ms": 0,
                                    "display_prompt": None
                                })
                elif node_name == "tools":
                    pass # Output is captured implicitly by the agent's next step
            
            final_state = step
            
        # Extract the final textual analysis
        last_node = list(final_state.keys())[0]
        llm_analysis = final_state[last_node]["messages"][-1].content
        
        stream_trace({
            "stage": "generate_financial_analysis",
            "tool": "react_agent",
            "status": "completed",
            "source": "llm",
            "input_summary": "Database results",
            "output_summary": "Financial analysis generated",
            "duration_ms": 0,
            "display_prompt": "Analyze the historical price trajectory and determine if today is a financially optimal time to buy."
        })
        
    except Exception as exc:
        # Historical pricing is deterministic database analysis and must remain
        # available when the optional LLM commentary service is offline.
        stance = trend.get("historical_stance", "UNKNOWN")
        if trend.get("error"):
            llm_analysis = f"Historical analysis could not be completed: {trend['error']}"
        elif stance == "BUY_NOW":
            llm_analysis = (
                "The current price is close to the product's verified historical low. "
                "Based on the stored price history, the current recommendation is BUY NOW."
            )
        else:
            target = drops.get("safe_target_price")
            target_text = f" near ₹{target:,.0f}" if target is not None else ""
            llm_analysis = (
                "The current price is above the preferred historical buying range. "
                f"Consider waiting for a target price{target_text}; the current "
                "recommendation is WAIT."
            )
        
        stream_trace({
            "stage": "generate_financial_analysis",
            "tool": "deterministic_fallback",
            "status": "fallback",
            "source": "deterministic fallback",
            "input_summary": "Database results",
            "output_summary": f"LLM unavailable; deterministic analysis displayed ({type(exc).__name__})",
            "duration_ms": 0,
            "display_prompt": None
        })
            
    return {
        "history_report": {
            "trend": trend, 
            "drops": drops,
            "llm_analysis": llm_analysis,
            "agent_trace": agent_trace
        }
    }

def market_agent_node(state: PriceLensState):
    """Node 2: database-first India market analysis with grounded evidence."""
    from tools.market_agent import MarketInvestigatorAgent
    from tools.market_agent_models import FreshnessPolicy, MarketAgentRequest
    from tools.market_config import MarketSettings
    from tools.market_db import MarketDatabase
    from tools.market_service import build_providers

    ctx = state.get("st_ctx")
    containers = state.get("st_containers") or {}
    a2_container = containers.get("market_agent")
    
    if ctx:
        import threading
        from streamlit.runtime.scriptrunner import add_script_run_ctx
        add_script_run_ctx(threading.current_thread(), ctx)
        
    def a2_callback(event: dict):
        if a2_container:
            status = event.get('status', '')
            icon = {"completed": "✓", "skipped": "○", "fallback": "△", "error": "✕"}.get(status, "•")
            stage = str(event.get('stage') or '').replace('_', ' ').title()
            a2_container.markdown(f"**{icon} {stage}**  \n<small>{event.get('output_summary')}</small>", unsafe_allow_html=True)

    database = None
    try:
        settings = MarketSettings.from_env()
        provider_names = tuple(
            name
            for name, configured in (
                ("serpapi", bool(settings.serpapi_key)),
                ("apify", bool(settings.apify_token)),
            )
            if configured
        )
        providers = build_providers(settings, provider_names) if provider_names else []
        database = MarketDatabase(settings.database_url)
        agent = MarketInvestigatorAgent(
            database,
            providers,
            freshness=FreshnessPolicy(
                price_minutes=settings.market_price_freshness_minutes,
                availability_minutes=settings.market_availability_freshness_minutes,
                delivery_minutes=settings.market_delivery_freshness_minutes,
                promotion_minutes=settings.market_promotion_freshness_minutes,
                seller_minutes=settings.market_seller_freshness_minutes,
                product_minutes=settings.market_product_freshness_minutes,
            ),
            enable_llm_summary=settings.market_agent_llm_enabled,
            trace_callback=a2_callback,
        )
        request = MarketAgentRequest(
            query=state.get("query", ""),
            canonical_id=state.get("canonical_id"),
            deadline_days=int(state.get("deadline_days", 30)),
            force_refresh=bool(state.get("force_market_refresh", False)),
            provider_policy=state.get("market_provider_policy") or settings.market_provider_policy,
            bank=state.get("bank"),
            card_type=state.get("card_type"),
            wants_emi=state.get("wants_emi"),
        )
        return {"market_report": agent.analyze(request)}
    except Exception as exc:
        return {
            "market_report": {
                "schema_version": "1.0",
                "agent": "market_investigator",
                "status": "error",
                "signals": ["INSUFFICIENT_EVIDENCE"],
                "warnings": [str(exc)],
                "summary": "Market analysis could not be completed.",
            }
        }
    finally:
        if database is not None:
            database.close()

def policy_agent_node(state: PriceLensState):
    """Node 3: retrieve retailer policy evidence independently of offers."""
    from tools.eligibility_agent import PolicyProtectionAgent
    from tools.eligibility_config import EligibilitySettings
    from tools.eligibility_db import EligibilityDatabase
    from tools.eligibility_models import PolicyAgentRequest, SUPPORTED_POLICY_RETAILERS
    from tools.policy_retrieval import HybridPolicyRetriever

    ctx = state.get("st_ctx")
    containers = state.get("st_containers") or {}
    a3_container = containers.get("policy_agent")
    
    if ctx:
        import threading
        from streamlit.runtime.scriptrunner import add_script_run_ctx
        add_script_run_ctx(threading.current_thread(), ctx)
        
    def a3_callback(event: dict):
        if a3_container:
            status = event.get("status", "")
            icon = {
                "completed": "✓",
                "skipped": "○",
                "fallback": "△",
                "error": "✕",
            }.get(status, "•")
            stage = str(event.get('stage') or '').replace('_', ' ').title()
            a3_container.markdown(f"**{icon} {stage}**")
            a3_container.caption(
                f"Tool: {event.get('tool', '-')} · "
                f"Source: {event.get('source', '-')} · "
                f"Status: {event.get('status', 'unknown')}"
            )
            a3_container.caption(event.get("output_summary") or "No output summary.")

    database = None
    try:
        settings = EligibilitySettings.from_env()
        database = EligibilityDatabase(settings.database_url)
        report = PolicyProtectionAgent(
            database,
            HybridPolicyRetriever(database, settings),
            freshness_minutes=settings.offer_freshness_minutes,
            enable_llm_summary=settings.llm_enabled,
            llm_settings=settings,
            trace_callback=a3_callback,
        ).analyze(
            PolicyAgentRequest(
                query=state.get("query", ""),
                canonical_id=state.get("canonical_id"),
                product_category=state.get("product_category") or "electronics",
                retailers=tuple(
                    state.get("retailer_scope") or SUPPORTED_POLICY_RETAILERS
                ),
            )
        )
        return {"policy_report": report}
    except Exception as exc:
        return {
            "policy_report": {
                "schema_version": "2.0",
                "agent": "policy_purchase_protection_analyst",
                "status": "error",
                "warnings": [str(exc)],
                "summary": "Retailer policy analysis could not be completed.",
            }
        }
    finally:
        if database is not None:
            database.close()

def decision_synthesizer_node(state: PriceLensState):
    """Node 4: Single-pass LLM synthesis with deterministic fallback (Option A)."""
    from tools.decision_synthesizer import run_synthesizer
    try:
        return {"draft_verdict": run_synthesizer(state)}
    except Exception as exc:
        return {"draft_verdict": {
            "decision": "REFUSE_NO_HISTORY",
            "target_price": None,
            "recommended_retailer": None,
            "recommended_seller": None,
            "condition": None,
            "confidence_score": 0.0,
            "primary_rationale": f"Synthesizer error: {exc}",
            "key_evidence": [],
            "_synthesis_mode": "error_fallback",
        }}

def verifier_gate_node(state: PriceLensState):
    """Node 5: Zero-LLM deterministic grounding verifier (₹100 tolerance)."""
    from tools.verifier_gate import verify_draft_verdict
    draft = state.get("draft_verdict") or {}
    result = verify_draft_verdict(draft, state)
    errors = list(state.get("errors") or [])
    if not result.passed:
        errors.append(result.reason)
    return {"final_verdict": result.final_verdict, "errors": errors}

# ==========================================
# 3. Build the Directed Acyclic Graph (DAG)
# ==========================================
builder = StateGraph(PriceLensState)

builder.add_node("input_resolver", input_resolver_node)
builder.add_node("history_agent", history_agent_node)
builder.add_node("market_agent", market_agent_node)
builder.add_node("policy_agent", policy_agent_node)
builder.add_node("decision_synthesizer", decision_synthesizer_node)
builder.add_node("verifier_gate", verifier_gate_node)

builder.add_edge(START, "input_resolver")
builder.add_edge("input_resolver", "history_agent")
builder.add_edge("input_resolver", "market_agent")
builder.add_edge("input_resolver", "policy_agent")
builder.add_edge(
    ["history_agent", "market_agent", "policy_agent"],
    "decision_synthesizer",
)
builder.add_edge("decision_synthesizer", "verifier_gate")
builder.add_edge("verifier_gate", END)

graph = builder.compile()

if __name__ == "__main__":
    test_state = {"query": "B0CS5XW6TN"}
    print("--- 🚀 Initializing PriceLens LangGraph DAG ---")
    for event in graph.stream(test_state, {"recursion_limit": 10}):
        for node_name, node_state in event.items():
            print(f"✅ Node Completed: [{node_name}]")
    
    final = graph.invoke(test_state)
    print("\\n--- 🏁 DAG Execution Complete ---")
    print(final.get("final_verdict"))
