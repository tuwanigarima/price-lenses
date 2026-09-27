# Agent 2 — Market Investigator: Current Implementation

## 1. Purpose and Current Scope

Agent 2 finds and compares current prices for electronics in the Indian market. It accepts a product name, retailer URL, or supported product identifier and builds a variant-aware comparison across:

- Amazon India
- Flipkart
- Croma
- Reliance Digital
- Vijay Sales

Agent 2 owns current-market discovery, product and variant matching, price comparison, offer-bound promotions, retailer coverage, freshness, and a grounded market summary.

Agent 2 does **not** issue the final `BUY_NOW` or `WAIT` recommendation. Agent 1 owns historical timing, Agent 2 owns commercial offer validation, Agent 3 owns retailer-policy evidence, and the future synthesizer will combine their reports.

---

## 2. Implemented Runtime Flow

```text
┌──────────────────────────────────────────────────────────────────────┐
│                         USER PRODUCT INPUT                           │
│  Product name · Amazon/Flipkart URL · ASIN · explicit attributes    │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  1. PARSE REQUEST                                                   │
│  Extract storage, RAM, colour, connectivity, size, generation,      │
│  condition, and bundle attributes explicitly present in the input.  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  2. SELECT DATA POLICY                                              │
│  api_first · database_first · database_only                         │
└───────────────────────┬───────────────────────────┬──────────────────┘
                        │                           │
              Live lookup required          Stored lookup only
                        │                           │
                        ▼                           │
┌───────────────────────────────────────────┐       │
│  3. FETCH PROVIDERS CONCURRENTLY          │       │
│  SerpAPI Search + Apify Products mode     │       │
└───────────────────────┬───────────────────┘       │
                        │                           │
                        ▼                           │
┌───────────────────────────────────────────┐       │
│  4. VALIDATE AND PERSIST                  │       │
│  India retailer · INR · positive price   │       │
│  product relevance · accessory rejection │       │
└───────────────────────┬───────────────────┘       │
                        │                           │
                        └──────────────┬────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  5. READ MARKET SNAPSHOT                                            │
│  Current provider run + latest stored observations as fallback      │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  6. GROUP EXACT VARIANTS                                            │
│  Never combine prices or promotions across different variants       │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  7. CALCULATE AND VERIFY                                            │
│  Listed price · conditional price · coverage · freshness · promos   │
│  Deterministic verifier grounds every displayed offer and discount  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  8. GENERATE GROUNDED SUMMARY                                       │
│  OpenAI receives compact verified facts; deterministic fallback      │
│  remains available when the LLM is disabled or unavailable          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  9. STREAMLIT MARKET INVESTIGATOR UI                                │
│  Requested match · other variants · offers · promotions · trace     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. User Input and Variant Behaviour

### Broad product input

Input such as `Samsung S24 FE` does not require the user to select RAM, storage, or colour. Agent 2 returns every verified S24 FE variant it can identify and calculates a separate best price for each group.

### Explicit attributes

Input such as `Samsung S24 FE 256GB Blue` uses the supplied attributes as filters and orders results as follows:

1. Exact requested variant
2. Same requested configuration in another colour
3. Other configurations of the same product family
4. No verified match

Related models are not intentionally included. Missing attributes expand the result set rather than block the search.

### Implemented variant attributes

Agent 2 currently extracts and groups by:

- Storage
- RAM
- Colour
- Connectivity, such as 4G or 5G
- Screen size
- Device size, such as watch case size
- Generation
- Condition, including renewed, refurbished, open-box, or used
- Common physical bundles, such as a charger, keyboard, pen, or stylus

Every variant retains its own offers and promotions. A lower price for a different storage, RAM, condition, connectivity, size, or bundle is never used as the price of the requested variant.

---

## 4. Provider Integration

### SerpAPI

SerpAPI is used as a search provider. It discovers product offers across supported Indian retailers. Amazon product-detail enrichment is optional; discovery remains search-based.

Configured behavior includes:

- India country and Google domain settings
- Separate connection and read timeouts
- Retry handling
- Optional Amazon detail enrichment
- Normalization into the shared `Offer` model

### Apify

Apify uses the e-commerce actor in `Products` mode. Optional detail enrichers add Flipkart details and bank-offer information when configured.

Apify and SerpAPI execute concurrently. Their database writes are performed sequentially after validation so one provider failure does not discard a successful result from another provider.

### Supported provider outcomes

- `ok`: provider completed without validation warnings
- `partial`: provider returned usable results with warnings or excluded records
- `error`: provider failed; other provider results remain usable

---

## 5. Validation and Product Identity

Before persistence, Agent 2 rejects provider records when they have:

- An unsupported marketplace
- No usable price or a non-positive price
- A non-INR currency
- Low product relevance
- A recognized accessory title when the user requested the 
 product

Product resolution uses identifiers in this order:

1. ASIN
2. GTIN/EAN when available
3. Existing canonical product match
4. Normalized-title matching
5. A stable title-derived identifier for a new product

The current matcher combines normalized-title similarity, token overlap, numeric model tokens, product-specification signals, and accessory terms.

---

## 6. PostgreSQL Persistence

Agent 2 uses the PostgreSQL database configured by `DATABASE_URL` or `PL_DATABASE_URL`. The current deployment can point to Neon without changing application queries.

```text
┌──────────────────────┐
│ products             │  Canonical product catalog shared with history
└──────────┬───────────┘
           │
           │ canonical_id
           ▼
┌──────────────────────┐       ┌──────────────────────────┐
│ market_search_runs   │──────▶│ market_offers            │
│ query/provider/status│ run_id│ Immutable observations   │
└──────────────────────┘       └────────────┬─────────────┘
                                            │ offer_id
                         ┌──────────────────┴──────────────────┐
                         ▼                                     ▼
             ┌────────────────────────┐          ┌────────────────────────┐
             │ market_offer_details   │          │ market_offer_promotions│
             │ delivery/seller/policy │          │ bank/coupon/special    │
             └────────────────────────┘          └────────────────────────┘

                    latest_market_offers view
                 selects the newest comparable rows
```

### Write policy

- One search-run row is created per provider attempt.
- Provider observations are appended to `market_offers`.
- Seller/detail rows and promotions remain bound to an `offer_id`.
- Existing products are not overwritten because product insertion uses conflict-safe behavior.
- Only the search run created by the current execution is updated to its terminal status.
- Agent 2 does not delete historical observations.

### Read policy

- `api_first`: call configured providers, persist results, then read the new run and stored fallback coverage.
- `database_first`: read stored evidence and refresh it when freshness or retailer coverage requires it.
- `database_only`: read stored results without calling providers or writing records.

---

## 7. Deterministic Price and Promotion Analysis

For every exact variant, Agent 2 calculates:

- Lowest listed price
- Lowest unconditional price
- Lowest conditional price
- Retailer coverage
- Observation freshness
- Offer ranking
- Product-specific promotion count

Promotions are joined by `offer_id`. They are never borrowed from another product or variant.

Bank discounts, coupons, special prices, and provider-reported effective prices may produce conditional scenarios. Cashback, generic festive text, and exchange messaging remain informational unless the provider supplies enough structured evidence to calculate a valid price reduction.

Eligibility-sensitive prices remain conditional when the user has not provided bank, card, or EMI preferences.

---

## 8. Agent 2 LLM Boundary

The current implementation is deterministic-first. The LLM does not calculate prices, execute arbitrary SQL, or control provider persistence.

The LLM is called once, after deterministic verification, to summarize compact verified facts. These include:

- Requested attributes
- Match mode
- Variant groups
- Verified prices and retailers
- Offer-specific promotions
- Coverage and freshness
- Warnings and missing eligibility inputs

The prompt prohibits invented facts, seller-safety conclusions, and final `BUY_NOW` or `WAIT` recommendations. Summaries are limited to 150 words. If the LLM fails, Agent 2 preserves the deterministic report and displays a fallback summary.

### Shared OpenAI configuration

```dotenv
OPENAI_API_KEY=<local secret>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5-mini
MARKET_AGENT_LLM_ENABLED=true
MARKET_AGENT_LLM_BASE_URL=https://api.openai.com/v1
MARKET_AGENT_LLM_MODEL=gpt-5-mini
```

The real key belongs only in the ignored `.env` file. Agents may override the
shared model and endpoint, but they all use `OPENAI_API_KEY`.

---

## 9. Agent Execution Trace

The Market Investigator UI contains an expandable **How Agent 2 investigated this product** section. A fresh Agent 2 report records stages such as:

| Stage | Tool | Execution type |
|---|---|---|
| Understand request | `parse_product_request` | Deterministic |
| Search current market | `search_serpapi_and_apify` | Provider execution |
| Load market data | `get_market_snapshot` | PostgreSQL read |
| Validate and group | `group_product_variants` | Deterministic |
| Verify report | `verify_market_report` | Deterministic |
| Prepare summary | `generate_market_summary` | LLM or fallback |

Each trace event contains status, source, sanitized input and output summaries, duration, and a display-safe prompt only when an LLM was actually invoked. API keys, connection strings, raw provider payloads, and unrestricted internal prompts are not displayed.

Older reports already held in Streamlit session state do not contain the trace. Restarting Streamlit or running a new analysis generates the current report structure.

---

## 10. Market Report Contract

Agent 2 returns `MarketReport` schema version `1.2`. The technical evidence and downstream contract remain internal for the future synthesizer; they are not rendered as raw JSON in the UI.

```json
{
  "schema_version": "1.2",
  "agent": "market_investigator",
  "status": "complete | partial | insufficient_evidence | error",
  "match_mode": "exact_variant | expanded_colors | expanded_variants | product_family_not_found",
  "requested_attributes": {},
  "requested_match": {},
  "variant_groups": [],
  "lowest_starting_price": 0,
  "best_unconditional_offer": {},
  "best_conditional_offer": {},
  "coverage": {},
  "freshness": {},
  "signals": [],
  "confidence": 0.0,
  "missing_inputs": [],
  "warnings": [],
  "agent_trace": [],
  "summary": ""
}
```

### Match modes

- `exact_variant`: at least one variant satisfies all explicit user attributes.
- `expanded_colors`: the technical configuration is found only in another colour.
- `expanded_variants`: the user supplied no variant constraints or only other configurations were verified.
- `product_family_not_found`: no comparable INR offer passed validation.

---

## 11. Streamlit UI

The Market Investigator tab currently displays:

1. Requested-variant price or family starting price
2. Potential conditional price
3. Retailer coverage
4. Confidence
5. Grounded summary
6. Verified variant comparison table
7. Retailer and seller offer table
8. Product-specific promotion table
9. Offer comparison chart and CSV download
10. Expandable Agent 2 execution trace

The raw Agent 2 evidence object and future downstream contract are intentionally hidden from the UI.

---

## 12. Failure and Fallback Behaviour

| Failure | Current behavior |
|---|---|
| One provider fails | Preserve and analyze the other provider's results |
| All providers return no valid offers | Fall back to stored evidence when available |
| LLM is missing or unavailable | Keep deterministic calculations and summary |
| Promotion eligibility is unknown | Show a conditional price and missing inputs |
| Unsupported/non-INR result | Exclude it before persistence |
| Database schema is missing | Stop with the migration command instead of silently failing |
| Old Streamlit session report | May lack current variant groups/trace until a new run or restart |

---

## 13. Current Known Limitations

- Product-family matching is rule-based and can still admit unusual accessories whose titles do not contain a recognized accessory term.
- Distinctive family suffixes such as `FE`, `Ultra`, `Pro`, `Plus`, or `Mini` need continued matcher coverage to prevent adjacent models from entering the same family.
- Apify actor output quality varies by retailer and may return zero priced product records.
- Product URLs provide stronger identity when the retailer exposes a stable product identifier; cross-retailer identity still relies on GTIN or normalized title matching when no shared ID is available.
- Colour extraction uses a maintained vocabulary and may not recognize every marketing colour name.
- Cross-agent combination of validated offers with Agent 3 retailer-policy profiles remains a future synthesizer responsibility.
- The final multi-agent purchase recommendation and synthesizer are not implemented yet.

---

## 14. Main Implementation Files

| File | Responsibility |
|---|---|
| `app.py` | Market Investigator form, variant tables, promotions, and execution trace |
| `tools/market_agent.py` | Agent 2 runtime, provider policy, grouping, report assembly, and LLM summary |
| `tools/market_agent_models.py` | Request, freshness, retailer, and report contracts |
| `tools/market_agent_tools.py` | Controlled snapshot, refresh, attribute extraction, and matching tools |
| `tools/market_service.py` | Concurrent provider execution, validation, and persistence orchestration |
| `tools/market_serpapi_provider.py` | SerpAPI search and optional Amazon enrichment adapter |
| `tools/market_apify_provider.py` | Apify product discovery and detail-enrichment adapter |
| `tools/market_matching.py` | Product relevance, accessory rejection, and canonical resolution |
| `tools/market_analysis.py` | Deterministic price, promotion, freshness, coverage, and compatibility calculations |
| `tools/market_verifier.py` | Grounds report prices, promotions, and variant groups against stored evidence |
| `tools/market_db.py` | PostgreSQL repository and append-oriented market persistence |
| `scripts/db/migrations/001_market_investigator.sql` | Agent 2 market schema and latest-offer view |

---

## 15. Current Test Coverage

The automated suite covers provider parsing, validation, database behavior, variant expansion, exact matching, accessory rejection, promotions, UI helpers, LLM configuration, report verification, and fallback paths. The current project suite passes without making live provider or billed LLM requests during tests.
