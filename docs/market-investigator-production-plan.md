# PriceLens Market Investigator — Production Plan

## 1. Goal and first release scope

Build a trustworthy India-focused electronics comparison service that answers:

1. Is this the exact product/variant the user asked for?
2. What is its current price at each supported retailer?
3. Who is selling it, and what seller/warranty/return risks are visible?
4. Which promotions apply, under what conditions, and what can this user actually pay?
5. How fresh and reliable is every value?

The first production release supports five retailers:

- Amazon India
- Flipkart
- Croma
- Reliance Digital
- Vijay Sales

It supports electronics only. Variant identity must include category-specific attributes such as model/MPN, RAM, storage, colour, connectivity, screen size, region, and bundle contents. A different variant must never be presented as the same product merely because its title is similar.

The first release does **not** include global travel arbitrage, review summarisation, policy RAG, or LLM-generated purchase recommendations. Those are later features. Market data quality, product identity, price computation, and provenance come first.

## 2. Assessment of the current prototype

Useful foundations already present:

- SerpAPI discovery through Google Shopping and Amazon search.
- Apify discovery plus detail enrichment for Flipkart and bank offers.
- Defensive normalisation, structured promotion parsing, product matching, append-only observations, and raw payload retention.
- Seller, delivery, warranty, return-policy, and promotion fields.
- Failure isolation between providers.
- Unit tests for normalisation, matching, providers, persistence, promotions, and UI shaping.

Production gaps:

- DuckDB is a single-process prototype store; concurrent API/workers require PostgreSQL.
- Fetches are synchronous and serial. There is no durable queue, idempotency key, retry policy, circuit breaker, or distributed rate limit.
- Product identity is too title-dependent. The current numeric-token guard is not enough for all electronics variants.
- `price_with_offers` loses eligibility, validity, caps, stackability, and calculation provenance.
- Search results can be discovery leads, not authoritative retailer observations.
- Only Flipkart has meaningful page-level detail coverage. Croma, Reliance Digital, and Vijay Sales require validated adapters.
- Community Apify Actor schemas can change. Their contracts, versions, costs, and live health are not controlled.
- The current `latest_offers` identity can merge observations that should be distinct, such as fulfilment, condition, pincode, or variant changes.
- No freshness SLA, confidence score, reconciliation workflow, alerting, or data-quality quarantine exists.
- No service API, authentication, secrets management, audit policy, or retention policy exists.

The attached five-day plan is suitable for a capstone/demo, but not for a production launch. A six-second hard join is also incompatible with scraper jobs that may take tens of seconds or minutes. Missing history should lower confidence in a timing recommendation; it should not suppress a valid current-price comparison.

## 3. Product principles

1. **Deterministic facts before agents.** Collection, matching, promotion maths, ranking, and verification remain deterministic. An LLM may later explain a verified result but must not invent prices, sellers, eligibility, or dates.
2. **Observations are immutable.** Never overwrite historical prices. Each retrieval creates a timestamped observation.
3. **Provenance is user-visible.** Every price, seller fact, and promotion retains source, source URL, retrieval time, location/pincode, parser version, and confidence.
4. **Conditional prices are scenarios.** Exchange values, bank-card discounts, coupons, cashback, and EMI are not one universally available price.
5. **Unknown is better than wrong.** Low-confidence matches and malformed source data are quarantined instead of silently published.
6. **Discovery and verification are separate.** SerpAPI finds candidates; retailer-detail adapters verify the shortlisted listings.

## 4. Target architecture

```text
Client / Streamlit or Web UI
          |
          v
FastAPI query API ---- PostgreSQL read models/cache
          |
          v
Durable job queue + scheduler (Celery/Redis initially)
          |
          +--> SerpAPI discovery adapter
          +--> Apify discovery/detail adapters
          +--> retailer-specific validation adapters
                          |
                          v
                 Normalise + validate
                          |
                          v
              Identity/matching pipeline
                          |
                          v
        PostgreSQL observations + raw evidence
                          |
                          v
       Promotion scenario and deal-ranking engine
```

Two ingestion modes use the same pipeline:

- **On demand:** return cached results immediately when fresh; enqueue refresh work and stream/poll status when stale.
- **Scheduled:** refresh watched products and high-demand products at retailer-appropriate intervals.

The API must not hold an HTTP request open for a full Apify run. It returns a search/job ID, partial results, source statuses, and a completion state.

## 5. Source strategy

### SerpAPI

Use Google Shopping for broad discovery and Amazon Search for Amazon ASIN discovery. For shortlisted Amazon candidates, use Amazon Product detail retrieval with other sellers enabled and capture structured promotions where available. Search snippets are never sufficient evidence for a final seller or effective-price claim.

### Apify

Use Actors for retailer-page enrichment and for retailers without a suitable structured API. Each Actor integration must have:

- A pinned Actor/version or a controlled internal fork.
- JSON input/output contract fixtures.
- Schema validation before persistence.
- A live canary run and freshness alert.
- Timeout, retry, proxy, and per-run cost limits.
- Recorded Actor run ID, dataset ID, build/version, usage/cost, and parser version.
- A replacement plan because community Actors can disappear or change.

### Coverage rollout

Implement and validate in this order:

1. Amazon India and Flipkart: largest identity/seller complexity and best current prototype support.
2. Croma and Reliance Digital: first-party/retail seller flows and pincode-sensitive availability.
3. Vijay Sales: fifth retailer after its detail contract is verified.

For every retailer, define a capability matrix: search, product details, marketplace sellers, price/MRP, stock, pincode delivery, promotions, warranty, return policy, and stable IDs. A missing capability must be reported as `unknown`, not inferred.

## 6. Canonical data model

Use internal UUIDs. Retailer IDs are aliases, not primary product identity.

Core tables:

- `retailers`: retailer identity and adapter configuration.
- `canonical_products`: brand, category, family, canonical title.
- `product_variants`: model/MPN, GTIN/EAN/UPC, variant attributes JSONB, manufacturer warranty.
- `external_products`: retailer/provider IDs (ASIN, Flipkart PID, retailer SKU), URL, variant ID, active state.
- `sellers`: retailer-scoped seller ID/name and first/last seen.
- `seller_observations`: rating, review count, badges, authorised/assured evidence, observed time.
- `listings`: external product + seller + condition + fulfilment + region/pincode identity.
- `price_observations`: selling price, MRP, currency, stock, delivery fee/date, observed time, source and evidence ID.
- `promotions`: typed promotion and original text.
- `promotion_terms`: bank/card/network, payment mode, EMI flag, percent, flat amount, cap, minimum order, coupon code, start/end, membership, pincode and other eligibility.
- `listing_promotions`: listing/promotion relationship and explicit stackability evidence.
- `price_scenarios`: reproducible computed totals for a declared user eligibility profile.
- `ingestion_runs` and `ingestion_items`: status, attempts, latency, counts, errors, cost and idempotency.
- `source_evidence`: provider payload or object-store reference, checksum, retrieved time, URL, parser version and retention metadata.
- `match_candidates`: proposed variant, confidence, evidence/features, decision status and reviewer outcome.
- `sales_events`: retailer/category event windows with confidence and source; never a permanently hard-coded calendar.

Important constraints and indexes:

- Monetary values use `NUMERIC`, never floating point.
- All timestamps are `TIMESTAMPTZ`; all source observations include `observed_at` and `ingested_at`.
- Unique idempotency keys prevent duplicate writes on retries.
- Partition `price_observations` by time when volume warrants it; index `(listing_id, observed_at DESC)`.
- Retain raw payloads for a bounded period; retain checksum and parsed provenance longer.
- Use PostgreSQL migrations (Alembic), connection pooling, backups, point-in-time recovery, and read replicas only when load requires them.

## 7. Product matching pipeline

Matching is a staged classifier, not a single fuzzy-title score:

1. Parse and normalise brand, model/MPN, GTIN, storage, RAM, colour, connectivity, size, generation, condition, and bundle.
2. Match exact GTIN/EAN/UPC when trustworthy.
3. Match known retailer aliases (ASIN/PID/SKU) to an existing variant.
4. Generate candidates by brand/category/model tokens.
5. Enforce hard conflicts: model, storage, RAM, connectivity, size, condition, and bundle cannot disagree.
6. Score remaining candidates and store feature-level reasons.
7. Auto-accept only above a calibrated threshold; quarantine the middle band; create a new variant only when evidence supports it.

Measure precision and recall on a labelled electronics dataset. The launch gate should prioritise precision: at least 99% on auto-accepted matches, because one wrong merge corrupts comparison and history.

## 8. Seller and promotion handling

### Seller facts

Store seller and fulfilment separately. “Sold by X, fulfilled by Amazon” is not equivalent to a first-party Amazon sale. An `authorised` or `safe` label requires explicit evidence and a timestamp. Seller scores must not be shared across retailers.

### Price scenarios

Return at least these values:

- `listed_price`: current public selling price.
- `unconditional_checkout_price`: listed price plus unavoidable delivery fees minus unconditional coupons.
- `best_bank_price`: best eligible instant-discount scenario, with bank/card/EMI terms.
- `cashback_net_cost`: shown separately because cashback may arrive later.
- `exchange_price`: always separate and labelled “up to”; never used as the default cheapest price.
- `emi_cost`: total payable including disclosed interest and processing fee when known; “no-cost EMI” is a financing term, not automatically a discount.

Each scenario includes the formula, terms used, eligibility assumptions, validity window, stackability status, confidence, and source evidence. If stackability is unknown, do not add discounts together.

## 9. Ranking and recommendation

The Market Investigator first ranks **verified current deals**, not future timing. A deterministic score can use:

- Effective price for the declared eligibility profile.
- Match confidence.
- Data freshness and source quality.
- Seller/fulfilment evidence.
- Stock and delivery certainty.
- Warranty and return-policy evidence.

Do not mix product review rating with seller trust. Do not treat a low price as valid if its match, seller, stock, or promotion eligibility is uncertain.

Purchase timing becomes a separate phase after enough history exists. Its output includes evidence horizon, volatility, percentile, expected saving range, confidence, and next known sale window. It can answer “insufficient history” for timing while still returning current verified deals.

## 10. Reliability, security, and operations

- Per-provider rate limits, exponential backoff with jitter, bounded retries, circuit breakers, and dead-letter jobs.
- Per-stage timeouts; partial results remain available when one source fails.
- Cache keys include query/variant, pincode, and relevant eligibility inputs.
- Metrics: success rate, p50/p95 latency, stale-result rate, empty-result rate, schema failures, match quarantine rate, retailer coverage, cost per verified listing, and promotion parsing confidence.
- Distributed tracing from user search to provider calls and database writes; structured logs with secrets removed.
- Alerts on provider schema drift, sudden price outliers, zero-result spikes, stale retailer coverage, and cost-budget breaches.
- API keys in a secret manager, not UI session state in production. Rotate keys and restrict operator endpoints.
- Validate and allow-list outbound retailer URLs; protect against SSRF and malicious redirects.
- Encrypt in transit and at rest; define role-based database access and audit logs.
- Review each retailer/provider's terms, robots guidance, data licensing, attribution requirements, and retention rules before launch. Prefer official/affiliate APIs where available.
- Add affiliate disclosure if monetised, and never rank by commission without an explicit user-visible policy.

## 11. Delivery plan

### Phase 0 — Source and product contract spike (2–4 days)

- Run real samples for 10 representative electronics across all five retailers.
- Validate the exact SerpAPI and Apify input/output contracts, coverage, latency, and cost.
- Produce retailer capability matrix and choose/fork Actors.
- Label an initial cross-retailer variant matching set.

**Exit gate:** seller and promotion evidence demonstrated for Amazon and Flipkart; price/detail evidence demonstrated for all five, or a documented coverage gap and fallback.

### Phase 1 — PostgreSQL foundation (3–5 days)

- Introduce repository interfaces so DuckDB and PostgreSQL are not mixed into domain logic.
- Add PostgreSQL schema and Alembic migrations.
- Build a repeatable DuckDB-to-PostgreSQL migration/import utility.
- Add idempotent observations, ingestion audit records, and source evidence.

**Exit gate:** existing tests pass against PostgreSQL integration tests; retrying the same ingestion does not duplicate observations.

### Phase 2 — Durable ingestion (5–8 days)

- Split discovery from detail verification.
- Add background jobs, provider concurrency, timeouts, retries, rate limits, and partial status.
- Implement Amazon and Flipkart adapters first.
- Add schema validation and golden contract fixtures.

**Exit gate:** provider failure does not fail the search; status and provenance are accurate; live canaries run reliably.

### Phase 3 — Identity and offer engine (5–8 days)

- Add attribute extraction, hard-conflict matching, confidence, and quarantine.
- Implement structured promotion terms and scenario calculation.
- Add seller/fulfilment history and source confidence.

**Exit gate:** >=99% precision on auto-accepted labelled matches; 100% correct scenario maths on the promotion fixture suite.

### Phase 4 — Remaining retailer coverage (5–8 days)

- Add Croma, Reliance Digital, and Vijay Sales detail adapters.
- Make pincode and availability part of listing observations.
- Add retailer-specific monitoring and fallback paths.

**Exit gate:** agreed field-completeness SLA met per retailer, with `unknown` used for unsupported fields.

### Phase 5 — API, UI, and operations (5–8 days)

- Add FastAPI endpoints for search jobs, partial/final results, product history, and source health.
- Update UI to expose freshness, confidence, eligibility, source failures, and price scenarios.
- Add dashboards, alerting, secrets management, backup/restore test, load test, and runbooks.

**Exit gate:** SLOs met under target load and recovery procedures exercised.

### Phase 6 — Timing recommendation (after history accrues)

- Collect scheduled price history and maintain sourced sales events.
- Back-test deterministic BUY/WAIT rules before introducing an explanatory LLM.
- Add a verifier that checks every cited price and calculation against immutable observations.

**Exit gate:** back-tested accuracy/calibration target agreed by product and no recommendation can reference unverified data.

Expected production-ready Market Investigator: roughly 4–6 engineering weeks for one experienced engineer, depending on live source reliability and compliance review. A five-day milestone can deliver only a controlled alpha for Amazon/Flipkart plus the PostgreSQL foundation.

## 12. Test and launch gates

- Unit tests for every parser, normaliser, matching feature, and promotion formula.
- Contract tests from versioned real payload fixtures for every provider/retailer.
- PostgreSQL integration tests, migration tests, concurrency/idempotency tests, and recovery tests.
- Daily live canaries that do not silently update golden fixtures.
- Data-quality tests for INR currency, non-negative prices, MRP relationships, outliers, required IDs, freshness, and duplicate listings.
- Load tests for on-demand searches and scheduled refresh overlap.
- Chaos tests for provider timeout, malformed payload, partial dataset, quota exhaustion, and database retry.
- Human-reviewed benchmark of at least 100 product variants across categories and all supported retailers before public launch.

Initial SLO targets:

- 99.5% query API availability, excluding declared upstream-wide outages.
- Cached response p95 under 500 ms.
- Search job accepted under 1 second; progressive status available immediately.
- At least 95% of published prices within the defined freshness window.
- Zero known cross-variant price merges in the launch benchmark.
- 100% of displayed conditional prices include eligibility and evidence.

## 13. Decisions required before implementation

1. Confirm the fifth retailer (recommended: Vijay Sales; alternative: Tata CLiQ).
2. Define launch pincodes. Recommendation: start with major metros and always ask/store pincode because stock, delivery, and sometimes price vary by location.
3. Decide expected search volume and maximum data-provider cost per user query.
4. Define raw-payload retention and whether evidence is stored in PostgreSQL or object storage.
5. Decide whether users will provide a card/Prime/EMI eligibility profile; otherwise show multiple labelled scenarios.
6. Approve the initial field-completeness SLA per retailer, since not every site exposes seller and promotion data equally.

## 14. Recommended first implementation slice

After the decisions above, implement one vertical slice:

`iPhone query -> SerpAPI discovery -> Amazon/Flipkart detail verification -> strict variant match -> PostgreSQL observations -> seller/promotion scenarios -> API response with provenance`

Use 10 manually verified products across phones, laptops, TVs, audio, and appliances. Only after this slice passes its accuracy, cost, and reliability gates should the same adapter contract be extended to the other three retailers.
