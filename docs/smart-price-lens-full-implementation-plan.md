# Smart AI PriceLens: Full Implementation Plan

## 1. Objective

PriceLens is an India-only AI shopping advisor for electronics. It must accept the way users normally search on Amazon, Flipkart, or Google Shopping: exact links, identifiers, detailed titles, partial names, misspellings, and conversational searches.

Representative inputs:

```text
Samsung Galaxy S24 Ultra
Samsung S24 Ultra 12GB 256GB Titanium Gray
Noise Pulse 2 Max
iphone 12 pro white 256 gb
best price for iPhone 14 blue 256GB
Amazon or Flipkart product URL
Amazon ASIN or retailer product identifier
```

For every valid electronics request, PriceLens must:

1. Identify the correct product or product family.
2. Exclude accessories, unrelated models, and non-electronics.
3. Fetch missing or stale market evidence.
4. Run the History, Market, and Policy agents.
5. Return each agent's evidence-backed conclusion, including partial conclusions.
6. Synthesize the strongest response supported by available evidence.
7. Clearly distinguish verified, provisional, stale, missing, and failed evidence.
8. Never invent a product, price, seller, promotion, historical value, or policy.

## 2. Scope

Included:

- Indian electronics market.
- Amazon India, Flipkart, Croma, Reliance Digital, and Vijay Sales.
- SerpAPI Amazon Search and Google Shopping.
- Apify discovery and supported detail enrichment.
- PostgreSQL/Neon.
- PostgreSQL full-text search, `pg_trgm`, and pgvector.
- OpenAI models for structured extraction, planning, summaries, and synthesis.

Excluded for this iteration:

- International retailers.
- General web search for bank promotions.
- Non-electronics such as clothing, groceries, and furniture.
- Accessories such as covers, chargers, cables, stands, and protectors.
- Related-model recommendations.
- Automatic destructive cleanup of previously stored invalid data.

## 3. Target Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│ User input                                                  │
│ Natural text · partial name · detailed title · URL · ASIN   │
└────────────────────────────┬────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│ Input normalizer and product-intent parser                  │
│ Brand · family · model · category · RAM · storage · colour  │
└────────────────────────────┬────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│ Shared product resolver                                     │
│ Identifier lookup → fuzzy PostgreSQL search → hard checks   │
└────────────────────────────┬────────────────────────────────┘
                             ▼
                    Evidence available and fresh?
                       │                    │
                      Yes                   No
                       │                    ▼
                       │        ┌─────────────────────────────┐
                       │        │ Agent 2 discovery preflight │
                       │        │ SerpAPI + Apify             │
                       │        │ Validate before persistence │
                       │        └─────────────┬───────────────┘
                       │                      ▼
                       │          Resolve products again
                       └──────────────────────┬────────────────
                                              ▼
                            Shared resolved-product context
                                              │
                  ┌───────────────────────────┼──────────────────────────┐
                  ▼                           ▼                          ▼
         Agent 1: History            Agent 2: Market           Agent 3: Policy
         Price and timing            Offers and sellers        Protection terms
                  │                           │                          │
                  └───────────────────────────┼──────────────────────────┘
                                              ▼
                                      LLM synthesizer
                                              ▼
                                  Deterministic final verifier
                                              ▼
                                        Unified UI result
```

The specialist agents run in parallel only after shared resolution. This prevents each agent from analyzing a different product.

## 4. Phase 0: Restore a Clean Git Baseline

The current unfinished merge contains code outside the selected architecture. Before implementation:

```bash
git merge --abort
git status
git branch --show-current
.venv/bin/python -m pytest -q
```

Create a clean baseline before changing product resolution. This phase has no database impact.

## 5. Phase 1: Shared Contracts

Introduce contracts shared by the resolver, all agents, synthesizer, verifier, and UI:

```text
ProductIntent
ProductCandidate
ProductResolution
VariantIdentity
EvidenceReference
AgentRationale
AgentReport
SynthesisInput
SynthesisResult
VerificationResult
```

### Product intent

```python
@dataclass
class ProductIntent:
    raw_query: str
    normalized_query: str
    input_type: str
    brand: str | None
    family: str | None
    model: str | None
    category: str | None
    ram: str | None
    storage: str | None
    color: str | None
    connectivity: str | None
    condition: str | None
    chipset: str | None
    identifiers: dict[str, str]
    explicit_attributes: set[str]
```

### Common agent report

```python
@dataclass
class AgentReport:
    agent: str
    status: str
    scope: dict
    conclusion: dict
    rationale: list[dict]
    evidence: list[dict]
    confidence: float
    warnings: list[str]
    missing_evidence: list[str]
    retryable: bool
    agent_trace: list[dict]
```

Every agent returns this contract. Empty reports are prohibited.

Statuses:

- `complete`: sufficient verified evidence.
- `partial`: useful evidence with material limitations.
- `insufficient_evidence`: successful execution with no reliable conclusion.
- `error`: technical failure prevented analysis.

## 6. Phase 2: Natural Shopping Input Handling

Normalize without losing identity:

- Normalize casing and whitespace.
- Treat `256 GB` and `256GB` identically.
- Treat `gray` and `grey` as aliases.
- Remove phrases such as `best price for`, `buy`, and `offer on`.
- Preserve model tokens such as `S24`, `12 Pro`, and `Pulse 2 Max`.
- Decode title and identifier information from retailer URLs.
- Preserve the original query for UI and audit.

Input types:

```text
AMAZON_URL
FLIPKART_URL
ASIN
PROVIDER_IDENTIFIER
DETAILED_PRODUCT_TEXT
PARTIAL_PRODUCT_TEXT
UNSUPPORTED_CATEGORY
```

URL and identifier resolution take priority over fuzzy matching.

Use hybrid parsing:

1. Deterministic URL and identifier extraction.
2. Deterministic RAM, storage, colour, connectivity, and condition patterns.
3. Brand, model, category, and colour aliases.
4. Optional LLM structured extraction for ambiguous text.
5. Deterministic validation of the LLM result.

Required corrections:

- `Bluetooth` must not imply colour Blue.
- Preserve `Titanium Gray`.
- Treat `12GB` as RAM when another capacity is explicitly storage.
- Keep `Snapdragon 8 Gen 3` as chipset information.
- Recognize model phrases such as `iPhone 12 Pro`.
- Do not use arbitrary shared numbers as product identity.

## 7. Null and Missing-Value Handling

Null means unknown, not zero, false, a confirmed mismatch, or agent failure.

### Product resolution

When `canonical_id` is null:

1. Attempt fuzzy database resolution.
2. If permitted and necessary, run provider discovery.
3. Resolve again after accepted results are persisted.
4. If still unresolved, return `insufficient_product_identity`.

Agent 1 must not silently skip execution.

### Agent 1

- Dated history: complete analysis.
- Stored aggregates only: partial analysis.
- No history: `insufficient_evidence` with `stance=UNKNOWN`.
- Database failure: `error` and `retryable=true`.

### Agent 2

- Null price: reject.
- Null availability: `PROVISIONAL`.
- Null marketplace seller: seller evidence incomplete.
- Null promotion: no promotion; not an error.
- Null colour/storage: unknown, not automatically conflicting.
- Missing exact variant: return alternatives separately.

### Agent 3

- Unknown specific category: fall back to `electronics`.
- Missing vector embedding: use keyword retrieval.
- Missing policy evidence: `insufficient_evidence`.
- Missing LLM: deterministic policy summary.

The UI must use explicit labels such as `Not confirmed`, `No history`, and `No exact variant found`, never ₹0 for missing price.

## 8. Electronics and Accessory Classification

Supported core categories include smartphones, tablets, laptops, smartwatches, televisions, cameras, headphones, earbuds, speakers, gaming consoles, computer components, home appliances, and personal electronic devices.

Exclude cases, covers, screen protectors, chargers, adapters, cables, stands, holders, replacement straps, spare parts, skins, sleeves, and product-compatible accessory listings.

Hard phrases include:

```text
compatible with
designed for
protective cover
back cover
flip cover
screen guard
tempered glass
replacement strap
charging cable
```

Context remains important: Sony headphones are a product; earphones designed for an iPhone are not an iPhone.

Classification must run before persistence in `products` or `market_offers`.

## 9. PostgreSQL Fuzzy Resolution

### Required migration

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_products_title_trgm
ON products USING gin (lower(title) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_market_runs_query_trgm
ON market_search_runs USING gin (lower(query) gin_trgm_ops);
```

These operations do not modify existing rows.

Resolution order:

1. ASIN, GTIN, or provider identifier.
2. Exact normalized title and attributes.
3. Brand plus family/model equality.
4. Trigram similarity.
5. Full-text/token search.
6. Provider discovery.
7. Resolution over newly accepted results.

Reject candidates with brand, family, model, condition, category, accessory, or requested-configuration conflicts. Different colours and capacities remain useful but are separate variants.

Example result:

```json
{
  "status": "exact",
  "primary_candidate_id": "B0CS5XW6TN",
  "exact_candidates": ["B0CS5XW6TN"],
  "same_configuration_other_color": ["B0CS5Z3T4M"],
  "other_configurations": ["B0CQYGF1QY"],
  "rejected_candidates": [],
  "confidence": 0.98
}
```

## 10. Product Identity Schema

Keep `products` unchanged and add durable family, identifier, alias, and classification tables.

### `product_families`

```sql
CREATE TABLE product_families (
    family_id UUID PRIMARY KEY,
    brand VARCHAR(100),
    family_name VARCHAR(255) NOT NULL,
    model VARCHAR(150),
    category VARCHAR(100) NOT NULL,
    normalized_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_product_families_name_trgm
ON product_families USING gin (normalized_name gin_trgm_ops);
```

### `product_family_members`

```sql
CREATE TABLE product_family_members (
    family_id UUID NOT NULL REFERENCES product_families(family_id),
    canonical_id VARCHAR(50) NOT NULL REFERENCES products(canonical_id),
    variant_signature TEXT NOT NULL,
    identity_confidence NUMERIC(5,4) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (family_id, canonical_id)
);
```

### `product_identifiers`

```sql
CREATE TABLE product_identifiers (
    identifier_id UUID PRIMARY KEY,
    canonical_id VARCHAR(50) NOT NULL REFERENCES products(canonical_id),
    identifier_type VARCHAR(30) NOT NULL,
    identifier_value VARCHAR(255) NOT NULL,
    marketplace VARCHAR(100),
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    source VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (identifier_type, identifier_value, marketplace)
);
```

Identifier types include ASIN, GTIN, EAN, Flipkart PID, retailer SKU, and provider product ID.

### `product_search_aliases`

```sql
CREATE TABLE product_search_aliases (
    alias_id UUID PRIMARY KEY,
    family_id UUID REFERENCES product_families(family_id),
    canonical_id VARCHAR(50) REFERENCES products(canonical_id),
    alias_text TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_source VARCHAR(50) NOT NULL,
    confidence NUMERIC(5,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (family_id IS NOT NULL OR canonical_id IS NOT NULL)
);

CREATE INDEX idx_product_aliases_trgm
ON product_search_aliases USING gin (normalized_alias gin_trgm_ops);
```

### `product_classifications`

```sql
CREATE TABLE product_classifications (
    canonical_id VARCHAR(50) PRIMARY KEY REFERENCES products(canonical_id),
    category VARCHAR(100),
    product_kind VARCHAR(40) NOT NULL,
    status VARCHAR(30) NOT NULL,
    confidence NUMERIC(5,4),
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    classifier_version VARCHAR(50) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

This quarantines existing accessories without deleting them.

## 11. Agent 2 Discovery Preflight

Split Agent 2 into market discovery and market analysis.

When stored evidence is missing or stale:

1. Generate a concise query from `ProductIntent`.
2. Call SerpAPI and Apify concurrently.
3. Normalize responses.
4. Reject accessories and identity conflicts.
5. Extract product and variant attributes.
6. Validate retailer, currency, price, and India scope.
7. Persist only accepted products and offers.
8. Record rejection counts and reasons.
9. Resolve candidates again.

The LLM may propose the provider query, but deterministic code must approve it.

## 12. Provider Diagnostics Schema

### `market_run_diagnostics`

```sql
CREATE TABLE market_run_diagnostics (
    run_id VARCHAR(32) PRIMARY KEY REFERENCES market_search_runs(run_id),
    raw_result_count INTEGER NOT NULL DEFAULT 0,
    normalized_result_count INTEGER NOT NULL DEFAULT 0,
    supported_retailer_count INTEGER NOT NULL DEFAULT 0,
    product_matched_count INTEGER NOT NULL DEFAULT 0,
    price_valid_count INTEGER NOT NULL DEFAULT 0,
    persisted_count INTEGER NOT NULL DEFAULT 0,
    rejection_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    provider_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### `market_rejected_results`

```sql
CREATE TABLE market_rejected_results (
    rejection_id UUID PRIMARY KEY,
    run_id VARCHAR(32) NOT NULL REFERENCES market_search_runs(run_id),
    provider VARCHAR(50) NOT NULL,
    marketplace VARCHAR(100),
    title TEXT,
    external_id VARCHAR(255),
    rejection_reason VARCHAR(100) NOT NULL,
    rejection_details JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_market_rejected_run
ON market_rejected_results(run_id, rejection_reason);
```

Rejected results remain available for diagnostics without polluting canonical products.

## 13. Agent 1: History and Timing

Agent 1 accepts resolved candidate IDs and does not independently resolve products.

Behaviour:

- Exact input: analyze the exact variant first.
- Partial input: analyze every family member with history.
- Dated history: complete analysis.
- Stored aggregates only: partial analysis.
- No history: `insufficient_evidence`.
- Database failure: `error` without cancelling other agents.
- Never borrow a current price from another variant.

Tools:

```text
get_price_history
calculate_history_metrics
get_historical_sale_drops
get_upcoming_sales
compare_variant_history
```

Tools calculate prices and scores; the LLM explains returned evidence.

No changes are required to `products` or `price_history`.

## 14. Agent 2: Current Market

Agent 2 owns discovery, current prices, retailers, sellers, availability, promotions, variants, and commercial validation.

Retrieve using:

```text
resolved canonical IDs
+ current provider run IDs
+ latest stored offers for candidate IDs
```

Do not depend on exact query equality.

Offer states:

- `VERIFIED`: exact identity, positive INR price, fresh, seller and availability confirmed.
- `PROVISIONAL`: valid fresh price but seller or availability evidence missing.
- `STALE`: correct product but beyond freshness limits.
- `REJECTED`: accessory, wrong product, invalid price, or unsupported retailer.

Recommended audit table:

```sql
CREATE TABLE market_offer_validations (
    offer_id VARCHAR(32) NOT NULL REFERENCES market_offers(offer_id),
    validator_version VARCHAR(50) NOT NULL,
    validation_status VARCHAR(30) NOT NULL,
    checks JSONB NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    rejection_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    validated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (offer_id, validator_version)
);
```

Tools:

```text
check_market_freshness
search_serpapi
search_apify
validate_provider_results
persist_validated_snapshot
load_offers_by_product
load_offer_details
load_offer_promotions
group_offers_by_variant
rank_commercial_offers
```

The LLM plans and explains; deterministic guards validate, enforce spend limits, and control persistence.

## 15. Agent 3: Policy Protection

Agent 3 owns retailer policy and purchase-protection evidence only. It does not own sellers, prices, promotions, offer validation, or market ranking.

### Required schema fix

```sql
ALTER TABLE eligibility_analysis_runs
ALTER COLUMN status TYPE VARCHAR(32);
```

This permits `insufficient_evidence` and does not delete data.

Category hierarchy:

```text
smartphone → smartphone + electronics + neutral
smartwatch → smartwatch + electronics + neutral
laptop → laptop + electronics + neutral
unknown electronic → electronics + neutral
```

Semantic retrieval failure falls back to keyword search. Missing evidence becomes `insufficient_evidence`, not an exception.

Continue using `policy_chunks` and `policy_chunk_embeddings`; rebuild missing embeddings rather than introducing another vector store.

Tools:

```text
infer_policy_categories
search_policy_keywords
search_policy_vectors
load_policy_chunks
compare_retailer_policies
identify_policy_gaps
```

Every policy claim must cite an active chunk. Existing legacy seller/offer-safety tables are retained but not used by the policy-only agent.

## 16. Unified Orchestration

State:

```python
class PriceLensState(TypedDict):
    query: str
    product_intent: dict
    product_resolution: dict
    candidate_ids: list[str]
    exact_candidate_id: str | None
    provider_run_ids: list[str]

    history_report: dict
    market_report: dict
    policy_report: dict

    synthesis_result: dict
    verification_result: dict
```

Graph:

```text
START
  ↓
parse_input
  ↓
resolve_products
  ↓
evidence_gate
  ├── sufficient → parallel specialist agents
  └── missing/stale → market_discovery → resolve_products
  ↓
history_agent + market_agent + policy_agent
  ↓
synthesizer
  ↓
final_verifier
  ↓
END
```

Each node catches its own failures and returns a structured report so one agent cannot cancel the graph.

## 17. Unified Analysis Persistence

### `price_lens_analysis_runs`

```sql
CREATE TABLE price_lens_analysis_runs (
    analysis_id UUID PRIMARY KEY,
    raw_query TEXT NOT NULL,
    normalized_query TEXT,
    input_type VARCHAR(40),
    product_intent JSONB NOT NULL DEFAULT '{}'::jsonb,
    product_resolution JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    error TEXT
);
```

### `price_lens_agent_reports`

```sql
CREATE TABLE price_lens_agent_reports (
    report_id UUID PRIMARY KEY,
    analysis_id UUID NOT NULL REFERENCES price_lens_analysis_runs(analysis_id),
    agent_name VARCHAR(40) NOT NULL,
    status VARCHAR(32) NOT NULL,
    scope JSONB NOT NULL DEFAULT '{}'::jsonb,
    conclusion JSONB NOT NULL DEFAULT '{}'::jsonb,
    rationale JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_references JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence NUMERIC(5,4),
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE (analysis_id, agent_name)
);
```

Store structured rationale, not private chain-of-thought.

## 18. Synthesizer

Input:

```json
{
  "request": {},
  "product_resolution": {},
  "agent_1_report": {},
  "agent_2_report": {},
  "agent_3_report": {}
}
```

Recommendation states:

```text
BUY_NOW
BUY_IF_CONFIRMED
WAIT
COMPARE_VARIANTS
TRACK_PRICE
INSUFFICIENT_EVIDENCE
UNSUPPORTED_PRODUCT
```

Rules:

- No `BUY_NOW` without an exact usable current offer.
- No timing claim without Agent 1 evidence.
- No current price claim without Agent 2 evidence.
- No policy claim without Agent 3 evidence.
- Alternative variants remain separate.
- Partial reports remain usable but reduce confidence.
- Missing evidence is always disclosed.

Missing-evidence behaviour:

| Agent 1 | Agent 2 | Agent 3 | Synthesizer result |
|---|---|---|---|
| Available | Available | Available | Full recommendation |
| Missing | Available | Available | Current comparison; timing unknown |
| Available | Missing | Available | Historical guidance; no purchasable offer |
| Available | Available | Missing | Price/timing; protection unknown |
| Missing | Available | Missing | Current-market comparison only |
| Available | Missing | Missing | Historical tracking guidance only |
| Missing | Missing | Available | Policy information only |
| Missing | Missing | Missing | `INSUFFICIENT_EVIDENCE` |

## 19. Synthesis Persistence

```sql
CREATE TABLE price_lens_synthesis_results (
    synthesis_id UUID PRIMARY KEY,
    analysis_id UUID NOT NULL UNIQUE REFERENCES price_lens_analysis_runs(analysis_id),
    recommendation VARCHAR(40) NOT NULL,
    draft_result JSONB NOT NULL,
    verified_result JSONB,
    confidence NUMERIC(5,4),
    verification_status VARCHAR(30) NOT NULL,
    verification_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

This lets the UI reload prior analyses without repeating providers and LLM calls.

## 20. Deterministic Final Verifier

After synthesis, verify:

- Product and exact-variant identity.
- Every current-price claim against Agent 2 evidence.
- Every historical claim against Agent 1 evidence.
- Promotion-to-offer binding.
- Policy citations against Agent 3 evidence.
- Correct provisional and stale labels.
- Recommendation eligibility.
- Deterministic confidence components.
- Absence of unsupported LLM claims.

If verification fails:

```text
Reject LLM draft
        ↓
Generate deterministic fallback
        ↓
Persist verification errors
        ↓
Display safe response
```

## 21. Unified UI

Use one product input and display:

1. Resolved product and requested attributes.
2. Final recommendation.
3. Best verified or provisional offer.
4. Historical timing.
5. Other genuine variants.
6. Seller and promotion conditions.
7. Purchase-protection evidence.
8. Confidence and evidence quality.
9. Missing evidence and recommended next action.
10. Combined sanitized tool-execution trace.

Do not display private chain-of-thought, raw provider payloads, or unsupported generated claims.

## 22. Database Change Summary

### Existing schema changes

| Change | Required | Risk |
|---|---:|---|
| Enable `pg_trgm` | Yes | Low |
| Add trigram indexes | Yes | Low; index-build cost |
| Expand `eligibility_analysis_runs.status` to `VARCHAR(32)` | Yes | Low |
| Change `products` columns | No | Avoided |
| Change `price_history` | No | None |
| Change `market_offers` | No | None |
| Change policy embedding schema | No | None |

### New tables

| Table | Purpose | Priority |
|---|---|---|
| `product_families` | Stable family identity | Required |
| `product_family_members` | Map variants to families | Required |
| `product_identifiers` | Cross-provider identifiers | Required |
| `product_search_aliases` | Natural and fuzzy queries | Required |
| `product_classifications` | Exclude accessories/non-electronics | Required |
| `market_run_diagnostics` | Explain provider result loss | Required |
| `market_rejected_results` | Audit rejected provider items | Required |
| `market_offer_validations` | Persist validation decisions | Recommended |
| `price_lens_analysis_runs` | Unified request audit | Production requirement |
| `price_lens_agent_reports` | Persist conclusions and rationale | Production requirement |
| `price_lens_synthesis_results` | Persist verified final output | Production requirement |

Existing tables retained without deletion:

```text
products
price_history
sales_calendar
authorized_sellers
market_search_runs
market_offers
market_offer_details
market_offer_promotions
policy_sources
policy_document_versions
policy_chunks
policy_rules
policy_chunk_embeddings
rag_index_manifests
```

## 23. Migration Strategy

Use separate reviewable migrations:

```text
004_product_resolution.sql
005_provider_diagnostics.sql
006_agent_orchestration.sql
007_eligibility_status_fix.sql
```

Before application:

1. Review SQL and affected objects.
2. Confirm Neon backup and restore capability.
3. Run against local PostgreSQL.
4. Compare row counts before and after.
5. Verify that no existing table loses rows.
6. Apply to Neon only after local validation.
7. Run read-only schema validation.
8. Backfill new tables separately from schema creation.

## 24. Existing-Data Backfill

Run in controlled batches:

1. Classify existing products.
2. Mark known accessories as `ACCESSORY`.
3. Create product families.
4. Map variants to families.
5. Infer ASIN identifiers from valid canonical IDs.
6. Create search aliases from titles.
7. Leave uncertain records as `UNKNOWN`.
8. Do not delete polluted or rejected rows automatically.

Previously observed accessory results should be reviewed separately. Any deletion requires explicit approval.

## 25. LLM Tool Governance

The LLM is a planner, interpreter, and explainer. Typed tools query, calculate, validate, and persist.

Every call passes through a guard checking:

- Tool permission for the current agent.
- Candidate IDs are within scope.
- Country and retailer support.
- Provider budget.
- Database write permission.
- Product validation before persistence.

The LLM must not execute arbitrary SQL, directly persist raw provider responses, override accessory rejection, calculate price metrics itself, guess promotion binding, invent policies, or fill evidence gaps from memory.

## 26. Validation Matrix

| Input | Expected result |
|---|---|
| Full Samsung title | Exact `B0CS5XW6TN`; Black/256GB and Gray/512GB separated |
| `Noise Pulse 2 Max` | Genuine Pulse 2 Max variants; unrelated models excluded |
| `iphone 12 pro 256gb white` | iPhone only; OPPO Reno and accessories rejected |
| Amazon URL | Resolve ASIN and compare retailers |
| Flipkart URL | Resolve PID/title and compare retailers |
| Misspelled product | Fuzzy resolution with explicit confidence |
| No stored product | Provider preflight, persistence, re-resolution, then all agents |
| Missing history | Agent 1 returns `insufficient_evidence` |
| Missing availability | Agent 2 returns a provisional offer |
| Missing embeddings | Agent 3 uses keyword retrieval |
| Non-electronic query | Unsupported category without provider spend |

## 27. Test Plan

Unit tests:

- Product-intent extraction.
- Compound colours.
- RAM/storage separation.
- Accessory classification.
- Brand/model conflict rejection.
- Variant classification.
- Null handling.
- Agent report contracts.
- Synthesis claim validation.

Integration tests:

- PostgreSQL trigram resolution.
- Product-family mapping.
- Provider validation before persistence.
- Agent 1 no-history response.
- Agent 2 provisional-offer response.
- Agent 3 category fallback.
- Partial-agent synthesis.
- Final-verifier fallback.

Controlled live tests:

- Detailed Samsung query.
- Partial Noise query.
- iPhone accessory-contamination query.
- Amazon URL.
- Flipkart URL.
- Provider timeout and partial-provider success.

## 28. Delivery Sequence

2. Establish a clean test baseline.
3. Implement shared contracts.
4. Implement intent parsing and null handling.
5. Add electronics/accessory classification.
6. Apply product-resolution migrations locally.
7. Implement fuzzy resolution and identity rules.
8. Backfill local product families and classifications.
9. Implement Agent 2 discovery preflight.
10. Fix Apify normalization and provider diagnostics.
11. Fix Agent 1 candidate-list and no-history behaviour.
12. Fix Agent 2 canonical lookup, variants, and provisional offers.
13. Apply the Agent 3 status fix locally.
14. Add Agent 3 category hierarchy and keyword fallback.
15. Standardize all agent reports.
16. Implement the new orchestrator.
17. Add unified analysis persistence.
18. Implement the synthesizer.
19. Implement the deterministic final verifier.
20. Update the unified UI.
21. Run unit and integration tests.
22. Run controlled live-provider tests.
23. Review all migrations before applying them to Neon.
24. Deploy and monitor resolution accuracy, rejection rates, partial-response frequency, and verifier failures.

## 29. Completion Criteria

The design is complete when:

- Detailed and partial queries resolve consistently.
- Every valid electronics query runs all three agents.
- Accessories and unrelated products do not enter canonical comparison.
- Agents always return complete, partial, insufficient, or error reports.
- Agent 1 uses exact-variant history.
- Agent 2 uses canonical IDs rather than exact query strings.
- Agent 3 handles missing evidence without schema errors.
- Apify and SerpAPI result loss is observable.
- The synthesizer consumes structured rationale and evidence.
- The final verifier rejects unsupported claims.
- The UI returns useful partial responses when complete evidence is unavailable.
- Existing product, history, market, and policy data remains intact.

