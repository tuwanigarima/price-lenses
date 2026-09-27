"""PriceLens Streamlit application with history and live-market workspaces."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from orchestrator import graph
from tools import market_ui
from tools.analytics import get_db_connection
from tools.market_config import MarketSettings
from tools.market_db import MarketDatabase, MarketSchemaError


st.set_page_config(page_title="PriceLens Advisor", page_icon="🔍", layout="wide")


def fetch_price_history(canonical_id: str) -> pd.DataFrame:
    """Fetch the existing historical time series used by the History Agent."""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT recorded_date, price
                FROM price_history
                WHERE canonical_id = %s
                ORDER BY recorded_date ASC
                """,
                (canonical_id,),
            )
            return pd.DataFrame(
                cursor.fetchall(), columns=["recorded_date", "price"]
            )
    finally:
        connection.close()


def render_history_results(result_state: dict) -> None:
    canonical_id = result_state.get("canonical_id")
    product_title = result_state.get("product_title") or canonical_id or "Product"
    history_report = result_state.get("history_report", {})
    trend = history_report.get("trend", {})
    drops = history_report.get("drops", {})

    st.header(product_title)
    st.caption(f"Product ID: {canonical_id}")

    metric_columns = st.columns(4)
    metric_columns[0].metric("Current Price", f"₹{trend.get('current_price', 0):,.0f}")
    metric_columns[1].metric("All-Time Low", f"₹{trend.get('true_atl', 0):,.0f}")
    metric_columns[2].metric("30-Day Average", f"₹{trend.get('avg_30d', 0):,.0f}")
    metric_columns[3].metric("DHI Score", f"{trend.get('s_history', 0)} / 100")

    verdict_column, chart_column = st.columns(2)
    with verdict_column:
        st.subheader("Verdict & Timing")
        with st.container(border=True):
            stance = trend.get("historical_stance", "UNKNOWN")
            if stance == "BUY_NOW":
                st.success("### 🟢 BUY NOW")
                st.write("The price is near its verified historical low.")
            else:
                st.warning("### 🟡 WAIT")
                st.write(drops.get("rationale", "The current price is elevated."))
                if drops.get("safe_target_price") is not None:
                    st.markdown(f"#### Target price: ₹{drops['safe_target_price']:,.0f}")
                    st.info(
                        f"Expected {drops.get('expected_discount_pct', 0)}% reduction "
                        f"during {drops.get('upcoming_sale', 'an upcoming sale')}."
                    )
        if history_report.get("llm_analysis"):
            st.write("**History Agent analysis**")
            st.info(history_report["llm_analysis"])
        if history_report.get("agent_trace"):
            with st.expander("View Agent 1 trace", expanded=True):
                for index, event in enumerate(history_report["agent_trace"], start=1):
                    if isinstance(event, dict):
                        status_icon = {
                            "completed": "✓",
                            "skipped": "○",
                            "fallback": "△",
                            "error": "✕",
                        }.get(event.get("status"), "•")
                        st.markdown(
                            f"**{index}. {status_icon} {str(event.get('stage') or '').replace('_', ' ').title()}** "
                        )
                    else:
                        st.code(str(event), language="text")

    with chart_column:
        st.subheader("Price Trajectory")
        with st.container(border=True):
            try:
                history = fetch_price_history(canonical_id)
            except Exception as exc:
                st.error(f"Unable to load price history: {exc}")
                history = pd.DataFrame()
            if history.empty:
                st.info("No time-series data is available for this product.")
            else:
                figure = px.line(history, x="recorded_date", y="price")
                figure.update_layout(
                    margin=dict(l=20, r=20, t=20, b=20),
                    xaxis_title=None,
                    yaxis_title="Price (₹)",
                    showlegend=False,
                )
                figure.add_hline(
                    y=trend.get("current_price", 0),
                    line_dash="dot",
                    line_color="red",
                    annotation_text="Today",
                )
                figure.add_hline(
                    y=trend.get("true_atl", 0),
                    line_dash="dash",
                    line_color="green",
                    annotation_text="ATL",
                )
                st.plotly_chart(figure, width="stretch")

def market_database(settings: MarketSettings) -> MarketDatabase | None:
    try:
        return MarketDatabase(settings.database_url)
    except MarketSchemaError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Could not connect to the Market Investigator database: {exc}")
    return None


def fetch_market_results(query: str, settings: MarketSettings) -> tuple[list[dict], list[dict]]:
    database = market_database(settings)
    if database is None:
        return [], []
    try:
        return database.offers_for_query(query), database.promotions_for_query(query)
    finally:
        database.close()


def render_market_results(
    query: str,
    offer_rows: list[dict],
    promotion_rows: list[dict],
    report: dict | None = None,
    *,
    key_prefix: str = "unified_market",
) -> None:
    if report and report.get("unresolved_variant_fields"):
        st.info(
            "Run the search again with a specific storage/colour or paste a product URL. "
            "Raw provider matches are hidden to prevent accessory or cross-variant comparisons."
        )
        return
    if report and report.get("evidence"):
        evidence_ids = {
            item.get("offer_id") for item in report["evidence"] if item.get("offer_id")
        }
        offer_rows = [row for row in offer_rows if row.get("offer_id") in evidence_ids]
    if report:
        validation_by_id = {
            str(item.get("offer_id")): item
            for item in report.get("ranked_offers") or []
            if item.get("offer_id")
        }
        offer_rows = [
            {
                **row,
                "validation_status": validation_by_id.get(
                    str(row.get("offer_id")), {}
                ).get("validation_status"),
                "validation_warnings": validation_by_id.get(
                    str(row.get("offer_id")), {}
                ).get("validation_warnings", []),
            }
            for row in offer_rows
        ]
    rows = market_ui.enrich_rows(offer_rows)
    st.subheader(f"Results for “{query}”")
    if not rows:
        st.info("No stored offers were found for this exact query.")
        return

    products = market_ui.group_products(rows)
    product_labels = {
        product["canonical_id"]: (
            f"{(product['title'] or product['canonical_id'])[:70]} "
            f"({product['offers']} offers)"
        )
        for product in products
    }
    marketplaces = sorted({row["marketplace"] for row in rows})
    product_column, market_column, rating_column = st.columns([3, 2, 1])
    chosen_product = product_column.selectbox(
        "Product",
        [""] + list(product_labels),
        format_func=lambda value: "All matched products" if not value else product_labels[value],
        key=f"{key_prefix}_product_{query}",
    )
    chosen_markets = market_column.multiselect(
        "Marketplace",
        marketplaces,
        default=marketplaces,
        key=f"{key_prefix}_marketplaces_{query}",
    )
    minimum_rating = rating_column.slider(
        "Min rating", 0.0, 5.0, 0.0, 0.5, key=f"{key_prefix}_rating_{query}"
    )
    filtered = market_ui.filter_rows(
        rows, chosen_markets, minimum_rating, chosen_product or None
    )
    if not filtered:
        st.warning("No offers match the selected filters.")
        return

    verified_filtered = [
        row for row in filtered if row.get("validation_status") == "VERIFIED"
    ]
    summary = market_ui.summarize(verified_filtered)
    metric_columns = st.columns(4)
    cheapest = summary["cheapest"]
    metric_columns[0].metric(
        "Lowest effective price",
        market_ui.format_money(
            cheapest["effective_price"] if cheapest else None, summary["currency"]
        ),
    )
    metric_columns[1].metric(
        "Average effective price",
        market_ui.format_money(summary["average"], summary["currency"]),
    )
    metric_columns[2].metric(
        "Verified · Marketplaces", f"{summary['offers']} · {summary['marketplaces']}"
    )
    best_rated = summary["best_rated"]
    metric_columns[3].metric(
        "Best rated", f"{best_rated['rating']:.1f} ★" if best_rated else "-"
    )

    table = pd.DataFrame(market_ui.offers_table(filtered))
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "Price": st.column_config.NumberColumn(format="₹ %.0f"),
            "Effective price": st.column_config.NumberColumn(format="₹ %.0f"),
            "Was": st.column_config.NumberColumn(format="₹ %.0f"),
            "Discount %": st.column_config.NumberColumn(format="%.1f%%"),
            "Rating": st.column_config.NumberColumn(format="%.1f ★"),
            "Link": st.column_config.LinkColumn("Link", display_text="Open"),
        },
    )

    visible_offer_ids = {row["offer_id"] for row in filtered if row.get("offer_id")}
    promotions = market_ui.promotions_table(promotion_rows, visible_offer_ids)
    st.markdown("**Product-specific offers and promotions**")
    if promotions:
        counts = market_ui.promotion_summary(promotion_rows, visible_offer_ids)
        st.caption(" · ".join(f"{name}: {count}" for name, count in sorted(counts.items())))
        st.dataframe(
            pd.DataFrame(promotions),
            hide_index=True,
            width="stretch",
            column_config={
                "Amount": st.column_config.NumberColumn(format="₹ %.0f"),
                "Percent": st.column_config.NumberColumn(format="%.1f%%"),
                "Product link": st.column_config.LinkColumn(
                    "Product link", display_text="Open"
                ),
            },
        )
    else:
        st.caption(
            "No structured product-specific promotions are stored for these offers. "
            "Enable provider enrichment and fetch again."
        )

    st.download_button(
        "Download offers CSV",
        table.to_csv(index=False).encode("utf-8"),
        "market-offers.csv",
        "text/csv",
        key=f"{key_prefix}_download_{query}",
    )

    currency = summary["currency"]
    chart_rows = [
        row
        for row in verified_filtered
        if row.get("effective_price") is not None and row.get("currency") == currency
    ]
    if chart_rows:
        chart = pd.DataFrame(
            {
                "Seller": [market_ui.offer_label(row) for row in chart_rows],
                "Effective price": [row["effective_price"] for row in chart_rows],
            }
        ).sort_values("Effective price")
        st.bar_chart(chart.set_index("Seller"))


def render_market_agent_report(report: dict) -> None:
    st.markdown("**Agent 2 market report**")
    coverage = report.get("coverage") or {}
    freshness = report.get("freshness") or {}
    requested_match = report.get("requested_match") or {}
    best = (
        requested_match.get("best_unconditional_offer")
        or report.get("best_unconditional_offer")
    )
    conditional = (
        requested_match.get("best_conditional_offer")
        or report.get("best_conditional_offer")
    )
    columns = st.columns(4)
    columns[0].metric(
        "Requested variant" if requested_match else "Starting price",
        market_ui.format_money(
            best.get("price") if best else None,
            best.get("currency") if best else "INR",
        ),
    )
    columns[1].metric(
        "Potential conditional",
        market_ui.format_money(
            conditional.get("price") if conditional else None,
            conditional.get("currency") if conditional else "INR",
        ),
    )
    columns[2].metric(
        "Retailer coverage",
        f"{len(coverage.get('verified_retailers', []))}/{len(coverage.get('expected_retailers', []))}",
    )
    columns[3].metric("Confidence", f"{float(report.get('confidence') or 0):.0%}")
    validation = report.get("validation_summary") or {}
    st.caption(
        "Offer validation: "
        f"{validation.get('verified', 0)} verified · "
        f"{validation.get('partial', 0)} partial · "
        f"{validation.get('stale', 0)} stale · "
        f"{validation.get('rejected', 0)} rejected"
    )
    st.write(report.get("summary") or "No Agent 2 summary is available.")
    st.caption(
        f"Match: {str(report.get('match_mode', 'unknown')).replace('_', ' ')} · "
        f"Status: {report.get('status', 'unknown')} · Freshness: "
        f"{freshness.get('status', 'unknown')} · Signals: "
        f"{', '.join(report.get('signals') or []) or 'none'}"
    )
    if report.get("missing_inputs"):
        st.info("Conditional-price eligibility still needed: " + ", ".join(report["missing_inputs"]))
    if report.get("variant_groups"):
        st.markdown("**Verified product variants**")
        variants = []
        for group in report["variant_groups"]:
            variant = group.get("variant") or {}
            group_best = group.get("best_unconditional_offer") or {}
            group_conditional = group.get("best_conditional_offer") or {}
            variants.append({
                "Match": str(group.get("match_type") or "").replace("_", " ").title(),
                "Product": group.get("title"),
                "Storage": variant.get("storage"),
                "RAM": variant.get("ram"),
                "Colour": variant.get("color"),
                "Connectivity": variant.get("connectivity"),
                "Screen / device size": (
                    variant.get("screen_size") or variant.get("device_size")
                ),
                "Generation": variant.get("generation"),
                "Condition": variant.get("condition"),
                "Bundle": variant.get("bundle"),
                "Best price": group_best.get("price"),
                "Conditional price": group_conditional.get("price"),
                "Retailer": group_best.get("retailer"),
                "Seller": group_best.get("seller"),
                "Validation": group_best.get("validation_status"),
                "Offers": group.get("offer_count"),
                "Promotions": group.get("promotion_count"),
                "Link": group_best.get("url"),
            })
        st.dataframe(
            pd.DataFrame(variants),
            hide_index=True,
            width="stretch",
            column_config={
                "Best price": st.column_config.NumberColumn(format="₹ %.0f"),
                "Conditional price": st.column_config.NumberColumn(format="₹ %.0f"),
                "Link": st.column_config.LinkColumn("Link", display_text="Open"),
            },
        )
    for warning in report.get("warnings") or []:
        st.warning(warning)
    with st.expander("How Agent 2 investigated this product", expanded=True):
        trace = report.get("agent_trace") or []
        if not trace:
            st.caption("No execution trace is available for this analysis.")
        for index, event in enumerate(trace, start=1):
            status_icon = {
                "completed": "✓",
                "skipped": "○",
                "fallback": "△",
                "error": "✕",
            }.get(event.get("status"), "•")
            st.markdown(
                f"**{index}. {status_icon} {str(event.get('stage') or '').replace('_', ' ').title()}** "
                f"— {float(event.get('duration_ms') or 0):,.0f} ms"
            )
            st.caption(
                f"Tool: {event.get('tool', '-')} · Source: {event.get('source', '-')} · "
                f"Status: {event.get('status', 'unknown')}"
            )
            st.caption(f"Input: {event.get('input_summary') or 'No input summary.'}")
            st.write(event.get("output_summary") or "No result summary.")
            if event.get("display_prompt"):
                st.code(event["display_prompt"], language="text")
            else:
                st.caption("Prompt: Not applicable — this stage does not use an LLM.")


def render_policy_report(report: dict) -> None:
    st.subheader("Agent 3 policy & purchase-protection analysis")
    st.write(report.get("summary") or "No Agent 3 summary is available.")
    columns = st.columns(4)
    columns[0].metric("Retailers", len(report.get("policy_profiles") or []))
    columns[1].metric("Policy chunks", report.get("evidence_chunk_count", 0))
    columns[2].metric("Evidence gaps", report.get("evidence_gap_count", 0))
    columns[3].metric("Evidence status", str(report.get("status", "unknown")).title())
    for warning in report.get("warnings") or []:
        st.warning(warning)

    rows: list[dict] = []
    for profile in report.get("policy_profiles") or []:
        for policy in profile.get("policies") or []:
            citations = policy.get("citations") or []
            first_citation = citations[0] if citations else {}
            rows.append(
                {
                    "Retailer": profile.get("retailer"),
                    "Policy": policy.get("policy_type"),
                    "Status": policy.get("status"),
                    "Summary": policy.get("summary"),
                    "Confidence": policy.get("confidence"),
                    "Citations": len(citations),
                    "Source": first_citation.get("source_url"),
                }
            )
    if rows:
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={
                "Confidence": st.column_config.NumberColumn(format="%.0%%"),
                "Source": st.column_config.LinkColumn("Source", display_text="Open"),
            },
        )
    else:
        st.info("No active retailer policy evidence was available for Agent 3.")

    for observation in report.get("cross_retailer_observations") or []:
        st.info(observation)
    st.caption(
        "Agent 3 evaluates retailer-level protections only. Listing-specific terms "
        "must be combined with Agent 2 evidence by the future synthesizer."
    )

    with st.expander("Agent 3 tool-call sequence", expanded=True):
        trace = report.get("agent_trace") or []
        if not trace:
            st.caption("No execution trace is available for this analysis.")
        for index, event in enumerate(trace, start=1):
            status_icon = {
                "completed": "✓",
                "skipped": "○",
                "fallback": "△",
                "error": "✕",
            }.get(event.get("status"), "•")
            st.markdown(
                f"**{index}. {status_icon} "
                f"{str(event.get('stage') or '').replace('_', ' ').title()}** "
                f"— {float(event.get('duration_ms') or 0):,.0f} ms"
            )
            st.caption(
                f"Tool: {event.get('tool', '-')} · Source: {event.get('source', '-')} · "
                f"Status: {event.get('status', 'unknown')}"
            )
            st.caption(f"Input: {event.get('input_summary') or 'No input summary.'}")
            st.write(event.get("output_summary") or "No output summary.")
            if event.get("display_prompt"):
                st.code(event["display_prompt"], language="text")
            else:
                st.caption("Prompt: Not applicable — this stage does not use an LLM.")


def run_unified_analysis(query: str, *, live_market: bool) -> dict | None:

    from streamlit.runtime.scriptrunner import get_script_run_ctx
    ctx = get_script_run_ctx()
    
    st.markdown("### 🔴 Live Agent Traces")
    trace_cols = st.columns(3)
    a1_container = trace_cols[0].container(height=400)
    a1_container.caption("Agent 1 (History)")
    a2_container = trace_cols[1].container(height=400)
    a2_container.caption("Agent 2 (Market)")
    a3_container = trace_cols[2].container(height=400)
    a3_container.caption("Agent 3 (Policy)")

    initial_state = {
        "query": query,
        "force_market_refresh": live_market,
        "market_provider_policy": "api_first" if live_market else "database_only",
        "st_ctx": ctx,
        "st_containers": {
            "history_agent": a1_container,
            "market_agent": a2_container,
            "policy_agent": a3_container,
        }
    }

    result_state = initial_state.copy()
    completed_agents: set[str] = set()
    orchestration_trace: list[dict[str, str]] = []
    with st.status("Running Agents 1, 2, and 3 in parallel...", expanded=True) as status:
        try:
            for event in graph.stream(initial_state, {"recursion_limit": 15}):
                for node_name, node_state in event.items():
                    result_state.update(node_state)
                    orchestration_trace.append(
                        {
                            "stage": node_name,
                            "status": "completed",
                            "output": (
                                "Report returned"
                                if node_name.endswith("_agent")
                                else "Graph stage completed"
                            ),
                        }
                    )
                    if node_name in {"history_agent", "market_agent", "policy_agent"}:
                        completed_agents.add(node_name)
                        st.write(f"✅ {node_name.replace('_', ' ').title()} completed")
                    elif node_name == "input_resolver":
                        st.write("✅ Product input resolved")
                    elif node_name == "decision_synthesizer":
                        st.write("✅ Decision synthesizer completed")
                    elif node_name == "verifier_gate":
                        st.write("✅ Final verifier completed")
            if len(completed_agents) != 3:
                status.update(
                    label="Unified analysis incomplete", state="error", expanded=True
                )
                st.error("Not every specialist agent returned a report.")
                return None
            status.update(
                label="All three specialist agents completed",
                state="complete",
                expanded=False,
            )
            result_state["orchestration_trace"] = orchestration_trace
            return result_state
        except Exception as exc:
            import traceback
            traceback.print_exc()
            status.update(label="Unified analysis failed", state="error", expanded=True)
            st.error(f"Unified analysis error: {exc}\n\n```\n{traceback.format_exc()}\n```")
            return None


def render_unified_tab() -> None:
    st.subheader("Unified Three-Agent Analysis")
    st.caption(
        "One product input launches History, Market, and Policy Protection agents in "
        "parallel, followed by a decision synthesizer and grounding verifier."
    )
    with st.form("unified_analysis_form"):
        query = st.text_input(
            "Product name, ASIN, or product URL",
            value=st.session_state.get("unified_query", ""),
            placeholder="e.g. iPhone 14 256 GB Blue",
        )
        live_market = st.checkbox(
            "Fetch live market evidence with SerpAPI and Apify",
            value=False,
            help="Disable this to run all agents using only locally stored data.",
        )
        submitted = st.form_submit_button(
            "Run all three agents", type="primary", width="stretch"
        )
    if submitted:
        if not query.strip():
            st.warning("Enter a product to analyze.")
        else:
            st.session_state["unified_query"] = query.strip()
            result = run_unified_analysis(query.strip(), live_market=live_market)
            if result:
                st.session_state["unified_result"] = result

    result = st.session_state.get("unified_result")
    if not result:
        st.info("Enter one product above to run all three specialist agents.")
        return

    reports = st.columns(3)
    history = result.get("history_report") or {}
    market = result.get("market_report") or {}
    policy = result.get("policy_report") or {}
    reports[0].metric(
        "Agent 1 · History",
        (history.get("trend") or {}).get("historical_stance", "No evidence"),
    )
    reports[1].metric("Agent 2 · Market", str(market.get("status", "unknown")).title())
    reports[2].metric(
        "Agent 3 · Policy", str(policy.get("status", "unknown")).title()
    )
    
    final = result.get("final_verdict")
    draft = result.get("draft_verdict")
    
    if final and final.get("decision"):
        decision = final.get("decision")
        color = "green" if decision == "BUY_NOW" else "orange" if decision == "WAIT" else "red"
        
        st.markdown("---")
        st.subheader("🎯 Final Synthesized Verdict")
        
        col1, col2 = st.columns([1, 2])
        with col1:
            st.markdown(f"<h2 style='text-align: center; color: {color};'>{decision.replace('_', ' ')}</h2>", unsafe_allow_html=True)
            conf = final.get('confidence_score')
            conf_display = f"{conf:.0%}" if isinstance(conf, (int, float)) else "0%"
            st.metric("Confidence", conf_display)
        
        with col2:
            target_price = final.get('target_price')
            price_display = f"₹{target_price:,.0f}" if isinstance(target_price, (int, float)) else 'N/A'
            st.markdown(f"**Target/Recommended Price:** {price_display}")
            retailer = final.get('recommended_retailer') or 'N/A'
            st.markdown(f"**Recommended Retailer:** {retailer}")
            st.markdown(f"**Rationale:** {final.get('primary_rationale', '')}")
            
        if draft and draft != final:
            st.warning("⚠️ The deterministic verifier gate modified the LLM's draft verdict to enforce grounding rules.")
        st.markdown("---")
    else:
        st.warning("Synthesizer ran, but returned no final verdict.")


    with st.expander("Unified execution flow", expanded=True):
        for index, event in enumerate(result.get("orchestration_trace") or [], start=1):
            st.markdown(
                f"**{index}. {str(event.get('stage') or '').replace('_', ' ').title()}**"
            )
            st.caption(f"Status: {event.get('status', 'unknown')}")
            st.write(event.get("output") or "Stage completed.")

    history_results_tab, market_results_tab, policy_results_tab = st.tabs(
        ["Agent 1 report", "Agent 2 report", "Agent 3 policy report"]
    )
    with history_results_tab:
        if history.get("trend"):
            render_history_results(result)
        else:
            st.warning("Agent 1 found no usable historical evidence.")
            if history.get("llm_analysis"):
                st.info(history["llm_analysis"])
            with st.expander("View Agent 1 LLM and tool trace", expanded=True):
                trace = history.get("agent_trace") or []
                if not trace:
                    st.caption("No execution trace is available for this analysis.")
                for index, event in enumerate(trace, start=1):
                    if isinstance(event, dict):
                        status_icon = {
                            "completed": "✓",
                            "skipped": "○",
                            "fallback": "△",
                            "error": "✕",
                        }.get(event.get("status"), "•")
                        st.markdown(
                            f"**{index}. {status_icon} {str(event.get('stage') or '').replace('_', ' ').title()}** "
                            f"— {float(event.get('duration_ms') or 0):,.0f} ms"
                        )
                        st.caption(
                            f"Tool: {event.get('tool', '-')} · Source: {event.get('source', '-')} · "
                            f"Status: {event.get('status', 'unknown')}"
                        )
                        st.caption(f"Input: {event.get('input_summary') or 'No input summary.'}")
                        st.write(event.get("output_summary") or "No result summary.")
                        if event.get("display_prompt"):
                            st.code(event["display_prompt"], language="text")
                    else:
                        # Fallback for old traces
                        st.code(str(event), language="text")
    with market_results_tab:
        render_market_agent_report(market)
        try:
            settings = MarketSettings.from_env()
            offers, promotions = fetch_market_results(result["query"], settings)
            render_market_results(
                result["query"],
                offers,
                promotions,
                market,
                key_prefix="unified_market",
            )
        except Exception as exc:
            st.error(f"Unable to render stored market rows: {exc}")
    with policy_results_tab:
        render_policy_report(policy)


st.title("🔍 PriceLens: Autonomous Deal Advisor")
st.caption(
    "Use historical pricing to decide when to buy, then compare live Indian-market offers."
)

render_unified_tab()
