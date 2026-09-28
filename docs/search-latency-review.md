# Search latency review — 2026-09-28

Scope: current implementation after the origin merge and Amazon local-corpus publication. This review does not change agent behavior, retailer selection, models, or provider configuration. The reusable profiler invokes the existing graph with callbacks; it does not replace its nodes.

## Measurement

The preceding Streamlit AppTest search for `Oppo Reno14 5G` took **185.79 seconds** in `database_only` mode. This is a single observed request, not a percentile or service-level guarantee. A graph-only follow-up records node timings and existing internal trace timings in `data/diagnostics/search-latency.json`.

The follow-up graph-only run completed in **194.009 seconds**, with no UI rendering:

| Stage | Wall time |
| --- | ---: |
| Input resolution | 3.340 s |
| Agent 1: History | 38.160 s |
| Agent 2: Market | 171.710 s |
| Agent 3: Policy | 47.479 s |
| Final synthesis | 18.935 s |
| Verifier | 0.002 s |

Agent 2's measured internal spans were **141.003 s web research**, **13.889 s LLM planning**, **10.994 s LLM summary**, **1.851 s stored-data lookup**, and **0.823 s validation/grouping**. The remaining time includes setup and other uninstrumented work. Web research accounts for approximately **73% of the total wall time**. It completed successfully; this run does not demonstrate a timeout or retry failure.

Agent 3 spent **14.546 s planning**, **13.882 s across five sequential retailer retrievals**, and **10.361 s summarizing**. Approximately 8.69 s was outside those measured spans (including connection/lifecycle and other reads); the profiler does not attribute that residual to an individual operation. Its verified result remained partial with Amazon evidence. History's internal trace durations are zero, so only its node wall time is reliable.

Holding all other timings fixed, removing the blocking 141 s signal search would put the critical path near **70 s**, because Policy would then be the slowest branch. This is a calculation from one trace, **not an optimized benchmark**. Reaching a lower latency budget also requires simplifying History/Policy and the final narrative calls. Both runs use database-only offer retrieval; neither measures SerpAPI/Apify live-offer latency or p95.

Reproduce using the existing configured services (this currently still performs signal web research):

```bash
path/to/venv/bin/python scripts/diagnostics/profile_search.py 'Oppo Reno14 5G'
```

The critical path is:

`input resolution + max(history, market, policy) + synthesis + verification`

The three specialists already run concurrently. Adding their durations overstates user-visible latency. A faster policy agent only reduces total latency when policy becomes the slowest branch; the market bottleneck must be addressed first.

## Findings and recommended changes

1. **Price-signal web research blocks Agent 2, even in database-only mode.** `tools/market_agent.py` calls `_add_price_intelligence` unconditionally before its summary. `orchestrator.py` configures that tool whenever a key exists. The query asks ten broad research questions; `tools/market_websearch_tool.py` sets no explicit request timeout, retry policy, output limit, or tool-call budget. This optional research is therefore on the critical path, even though the UI has disabled live market search. Make the network behavior explicit: database-only should use cached signal evidence or report it unavailable; live mode should have a separate, bounded signal-research budget. Preserve the upstream feature, but move refresh to a background job or return a clearly identified partial result at its deadline. Do not silently treat a pending search as proof that no price-drop signals exist.

2. **History repeats deterministic work and uses multiple model turns.** `history_agent_node` queries trend and, conditionally, sale drops before building a ReAct agent. Its prompt tells that agent to call those same database tools again. Each tool/model turn adds serial latency. Compute one request-scoped snapshot and pass it to one summarization call, or let the final synthesizer write the narrative from the structured facts. Preserve formulas, identity checks and upstream prose-only specialist behavior. The raw trend/drop dictionaries must remain available for charts and verification.

3. **Market planning adds a model round trip without controlling execution.** `_plan_market_tools` returns a plan that is stored in the report, while actual provider branching follows `provider_policy` directly. Use a deterministic plan for these fixed branches. Keep market narration optional or let final synthesis consume the structured report. Avoid paying for planning that cannot change the investigation.

4. **Policy retrieval repeats serial network work.** Agent 3 uses a model planner, loops over all five retailers serially, constructs an embedding client per search, embeds each question, queries keyword/vector candidates, loads chunks, and finally calls a summary model. Four retailers currently have no documents in the published local build, but still receive embedding calls. Query the build's retailer/category coverage once; return explicit missing-document gaps for absent sources. Use deterministic policy questions, reuse the embedding client, batch distinct query embeddings (or share one identical category/topic query vector), and retrieve candidates with per-retailer limits. Reusing an embedding does not mean reusing evidence across retailers. Do not parallelize operations on the same psycopg2 connection; use batching or independent pooled connections.

5. **No application-level request deadline.** History, policy planning/summary, final synthesis and signal research have no explicit timeout/retry settings in their constructors. The installed OpenAI SDK defaults are 600 seconds per request and two retries; these are not a safe end-to-end latency budget. Agent 2's planner/summary use 20 seconds and one retry, which can still exceed 20 seconds overall. Add one monotonic request deadline and pass remaining budgets to each external operation. Bound retries within that budget, retain successful evidence on timeout, and expose the missing/timeout status. A thread future timing out does not cancel its underlying request.

6. **Live provider parallelism still waits for every provider.** `MarketInvestigatorService.search` runs providers concurrently, but exits the executor context before processing results, waiting for all futures. SerpAPI defaults to a 120-second read timeout with two retries; Apify actor calls have no explicit application deadline here. This was not exercised by the database-only benchmark. For live searches, process completed providers incrementally with validation and per-provider deadlines. Do not block a usable partial result on the slowest provider. Keep writes on a controlled connection and discard late results for superseded requests.

7. **Repeated DB setup and round trips are secondary opportunities.** History opens multiple connections and executes multiple aggregates, policy retrieval performs separate reads/rollbacks, and input resolution executes `CREATE EXTENSION IF NOT EXISTS` during a request. Move extension management to migrations, combine compatible read queries and use a bounded thread-safe connection pool. Check query plans before changing indexes or identity-ranking semantics. Do not infer that Neon itself is the bottleneck from total agent duration.

8. **Current traces under-report latency.** History events set `duration_ms` to zero, and UI orchestration events store no durations. Persist request IDs, node start/end times, DB/query and model/embedding spans, provider attempts, cache status and timeout reasons. Render completed evidence progressively; the current UI mainly shows trace updates until the entire graph finishes. Partial evidence should retain freshness, citations, uncertainty and pending-agent status, and must not look like a completed verified purchase recommendation.

## Safe cache boundaries

- History: canonical product ID and history revision/as-of time; invalidate on ingestion.
- Offers: exact variant, country, condition, seller and eligibility inputs; respect the existing freshness policy. Forced refresh must bypass offer-result caches.
- Policy retrieval: corpus build ID, embedding model, category, retailer set, policy types and normalized question. A newly active build must invalidate previous results. Check effective-date transitions even when the build ID is unchanged.
- Query embeddings: model, dimensions and normalized text; exclude unrelated retailer evidence from retrieval results.
- Price signals: product family, country, time horizon and explicit fetched/expiry times. Cache failures briefly and distinguish them from a successful empty result.
- User/session data: never share bank/card/order eligibility results across incompatible users or inputs. Coalesce identical in-flight evidence reads to avoid duplicate work.

## Implementation order and acceptance checks

First bound optional market web research and make database-only behavior truthful. Next remove duplicate history retrieval and fixed-plan LLM calls, then batch/cache policy retrieval. Add deadlines and progressive partial rendering before tuning database indexes or switching models.

Preserve Agent 3's independence, the origin branch's provider integrations, matching safeguards, mathematical outputs and citation verification. Test slow/failing services, no offers, empty policy sources, stale caches, new policy builds, mixed provider success and repeated searches. Compare cold/warm p50 and p95 across Oppo Reno14, iPhone 14 and Noise Pulse 2 Max, in both cached and live modes. A target such as a 15–30 second cached response is a proposed budget, not a measured improvement; live research should have a separately advertised bounded completion/partial-result deadline.
