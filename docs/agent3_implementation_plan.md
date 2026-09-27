# Agent 3 — Eligibility & Safety Analyst: Final Implementation Plan

## 1. Purpose

Agent 3 protects the buyer from an offer that looks inexpensive but is unsafe,
ineligible, stale, misleading, or operationally poor. It evaluates the exact
India-market offers discovered by Agent 2 and answers:

> Which offer is the safest usable offer for each product variant, and what
> seller, condition, warranty, return, delivery, promotion-eligibility, and
> product-risk warnings should the buyer understand?

This document is the implementation plan for Agent 3. It does not modify or
replace `implementation_plan.md` or `low_level_design.md`. Where those older
documents refer to DuckDB, Agent 3 will use the PostgreSQL/Neon and Agent 2
tables that exist in the current application.

The initial implementation will be built and validated locally with:

- Local PostgreSQL as the durable source of truth.
- Local ChromaDB as a rebuildable semantic-search index.
- Official Indian retailer policy and FAQ documents.
- Existing Agent 2 offer snapshots from SerpAPI and Apify.
- A deterministic scoring and verification layer.
- An optional LLM for grounded explanation, never for price mathematics or
  unsupported policy decisions.

After local validation, PostgreSQL can point to Neon and Chroma can be moved to
a managed or separately hosted service without changing Agent 3's contracts.

---

## 2. Scope and System Ownership

### 2.1 Agent responsibilities

| Component | Responsibility |
|---|---|
| Input/identity resolver | Resolve product family, identifiers, explicit attributes, and compatible variants |
| Agent 1 — History Analyst | Historical position, observed ATL/ATH, trend, and historical timing |
| Agent 2 — Market Investigator | Current India-market offers, variants, prices, promotions, availability, freshness, and market coverage |
| Agent 3 — Eligibility & Safety Analyst | Seller authorization/trust, condition, warranty, return/replacement, delivery, promotion eligibility, product risks, and safest current offer |
| Future Decision Synthesizer | Reconcile all specialist reports and propose the final purchase decision |
| Deterministic verifier | Ground the final recommendation in stored prices, sellers, policies, scores, and evidence |

### 2.2 Agent 3 owns

- Seller identification, authorization evidence, and trust classification.
- Product condition validation: new, renewed, used, open-box, or unknown.
- Manufacturer-warranty, seller-warranty, no-warranty, and unknown status.
- Return, replacement, cancellation, and refund policy interpretation.
- Delivery and fulfillment classification.
- Applying user-specific eligibility to product-bound promotions already found
  by Agent 2.
- Product-specific review-risk analysis when enough corroborated reviews exist.
- A deterministic offer score and separate evidence-confidence score.
- Per-variant cheapest and safest offer results.
- A traceable, grounded Agent 3 explanation.

### 2.3 Agent 3 does not own

- Product discovery across marketplaces; Agent 2 owns it.
- Historical ATL, ATH, averages, percentiles, or price momentum; Agent 1 owns
  them.
- Generic internet searches for bank offers.
- International pricing or travel arbitrage.
- A final `BUY_NOW` or `WAIT` recommendation.
- Inventing missing seller, warranty, return, or promotion conditions.
- Treating a seller rating or marketplace badge as proof of brand
  authorization.
- Using customer reviews as official retailer-policy evidence.

### 2.4 Market scope

- Country: India only.
- Currency: INR only for the first release.
- Category: electronics.
- Initial retailers:
  - Amazon India
  - Flipkart
  - Croma
  - Reliance Digital
  - Vijay Sales

---

## 3. Target Agent Flow

Agent 3 must evaluate the same immutable offer observations used by Agent 2.
It must not independently select arbitrary rows from `latest_market_offers`.

```text
+---------------------------+
| User product text or URL  |
| + optional preferences    |
+-------------+-------------+
              |
              v
+---------------------------+
| Input and identity        |
| resolver                  |
+-------------+-------------+
              |
       +------+-----------------------------+
       |                                    |
       v                                    v
+-------------------+          +---------------------------+
| Agent 1           |          | Shared market evidence    |
| historical data   |          | SerpAPI/Apify -> Postgres |
+---------+---------+          +-------------+-------------+
          |                                  |
          |                         +--------+--------+
          |                         |                 |
          |                         v                 v
          |              +----------------+ +----------------+
          |              | Agent 2        | | Agent 3        |
          |              | market report  | | safety report  |
          |              +--------+-------+ +--------+-------+
          |                       |                  |
          +-----------------------+--------+---------+
                                           |
                                           v
                               +-----------------------+
                               | Future synthesizer    |
                               +-----------+-----------+
                                           |
                                           v
                               +-----------------------+
                               | Deterministic verifier|
                               +-----------------------+
```

### 3.1 Initial integration approach

The first implementation does not need to refactor all Agent 2 provider code.
Agent 2 will return the provider `run_id` and exact `offer_id` values that Agent
3 should evaluate. Once this contract works, provider acquisition can be moved
into an explicit shared market-evidence node.

### 3.2 Broad product queries

RAM, storage, colour, and other variant attributes are not mandatory user
inputs. When the user gives a broad product query, Agent 3 evaluates every
verified variant group independently.

Agent 3 must never compare incompatible variants in the same cheapest/safest
ranking. Explicit user attributes determine the primary group, while other
verified variants may be shown afterward.

---

## 4. Agent 3 Request Contract

```json
{
  "query": "Samsung S24 Ultra 256GB",
  "canonical_id": "optional",
  "market_run_ids": ["provider-run-id"],
  "offer_ids": ["offer-id"],
  "variant_groups": [],
  "budget": 100000,
  "bank": "HDFC",
  "card_type": "credit",
  "wants_emi": false,
  "requested_condition": "NEW",
  "requires_manufacturer_warranty": true
}
```

Only the query and market evidence are required. Missing optional preferences
must not block analysis:

- No budget: rank against comparable current offers and report budget status as
  unknown.
- No bank/card: keep bank discounts conditional.
- No condition: prefer new offers, but clearly group other conditions.
- No warranty preference: still classify warranty quality.

---

## 5. Storage Architecture

```text
+-------------------------- PostgreSQL --------------------------+
| Durable, authoritative data                                   |
|                                                               |
| products / market_*                 Existing Agent 1/2 tables  |
| policy_sources                      Official URL registry       |
| policy_document_versions            Immutable snapshots         |
| policy_chunks                       Canonical evidence text      |
| policy_rules                        Verified structured rules   |
| authorized_seller_*                 Authorization provenance    |
| product_reviews                     Raw normalized reviews      |
| eligibility_analysis_runs           Agent 3 executions          |
| offer_safety_assessments            Deterministic results       |
| assessment_policy_evidence          Claim-to-chunk links        |
| rag_index_manifests                 Chroma index versions       |
+-------------------------------+-------------------------------+
                                |
                                | index approved active chunks
                                v
+--------------------------- ChromaDB ----------------------------+
| Rebuildable semantic indexes                                  |
|                                                               |
| retailer_policy_chunks_<version>                               |
| product_review_chunks_<version>                                |
+---------------------------------------------------------------+
```

PostgreSQL is the source of truth. Chroma is derived state. Losing Chroma must
not lose policy documents, policy history, analysis history, or offer evidence.

---

## 6. Existing PostgreSQL Schema Reused by Agent 3

Agent 3 will reuse these current tables without deleting or replacing them:

| Existing object | Agent 3 use |
|---|---|
| `products` | Product identity, brand, model, and variant attributes |
| `market_search_runs` | Exact Agent 2 provider attempts |
| `market_offers` | Offer, seller, marketplace, price, URL, and freshness |
| `market_offer_details` | Seller ID, offer price, assured status, COD, return, delivery, warranty, and condition |
| `market_offer_promotions` | Product-bound bank, card, EMI, coupon, and promotion details |
| `authorized_sellers` | Existing initial authorization registry |
| `latest_market_offers` | UI convenience; not the preferred snapshot source for Agent 3 |

Agent 3 reads offers using explicit `run_id` and `offer_id` values. It must not
silently replace current-run evidence with a stale row from the latest-offers
view.

---

## 7. Additive PostgreSQL Schema for Agent 3

The following is the target additive migration. Names and field sizes can be
adjusted during implementation, but the ownership and relationships should
remain unchanged.

### 7.1 Policy sources and ingestion

```sql
CREATE TABLE policy_sources (
    source_id UUID PRIMARY KEY,
    retailer VARCHAR(100) NOT NULL,
    policy_type VARCHAR(50) NOT NULL,
    source_url TEXT NOT NULL,
    canonical_url TEXT,
    country_code CHAR(2) NOT NULL DEFAULT 'IN',
    source_format VARCHAR(50) NOT NULL DEFAULT 'HTML',
    ingestion_mode VARCHAR(30) NOT NULL DEFAULT 'FETCH',
    source_authority VARCHAR(30) NOT NULL DEFAULT 'UNVERIFIED',
    discovery_status VARCHAR(30) NOT NULL DEFAULT 'DISCOVERED',
    fetch_status VARCHAR(30),
    last_http_status INTEGER,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    approved_for_ingestion BOOLEAN NOT NULL DEFAULT FALSE,
    search_query TEXT,
    discovered_by VARCHAR(50),
    review_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_checked_at TIMESTAMPTZ,
    UNIQUE (source_url)
);

CREATE INDEX idx_policy_sources_scope
    ON policy_sources (country_code, retailer, policy_type, enabled);
```

`source_authority` values:

- `AUTHORITATIVE`
- `OFFICIAL_GUIDANCE`
- `REGULATION`
- `PRODUCT_PAGE`
- `UNVERIFIED`
- `REJECTED`

Only approved official sources may proceed to active document ingestion.

### 7.2 Immutable policy-document versions

```sql
CREATE TABLE policy_document_versions (
    document_version_id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES policy_sources(source_id),
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    title TEXT NOT NULL,
    content_text TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    effective_from DATE,
    effective_until DATE,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    parse_status VARCHAR(30) NOT NULL,
    parser_version VARCHAR(50),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, version_number),
    UNIQUE (source_id, content_sha256)
);

CREATE UNIQUE INDEX uq_policy_one_active_version
    ON policy_document_versions (source_id)
    WHERE is_active;

CREATE INDEX idx_policy_documents_active
    ON policy_document_versions (is_active, retrieved_at DESC);
```

New content creates a new row. An existing policy version is never overwritten.

### 7.3 Canonical policy chunks

```sql
CREATE TABLE policy_chunks (
    chunk_id UUID PRIMARY KEY,
    document_version_id UUID NOT NULL
        REFERENCES policy_document_versions(document_version_id),
    parent_chunk_id UUID REFERENCES policy_chunks(chunk_id),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    heading_path TEXT,
    chunk_type VARCHAR(30) NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count > 0),
    content_sha256 CHAR(64) NOT NULL,
    retailer VARCHAR(100) NOT NULL,
    policy_type VARCHAR(50) NOT NULL,
    country_code CHAR(2) NOT NULL DEFAULT 'IN',
    product_category VARCHAR(100),
    seller_scope VARCHAR(50),
    condition_scope VARCHAR(50),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector(
            'english',
            COALESCE(heading_path, '') || ' ' || COALESCE(content, '')
        )
    ) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_version_id, chunk_index)
);

CREATE INDEX idx_policy_chunks_filters
    ON policy_chunks (
        country_code, retailer, policy_type, product_category, is_active
    );

CREATE INDEX idx_policy_chunks_text_search
    ON policy_chunks USING GIN (search_vector);
```

PostgreSQL retains canonical text even though a searchable copy also exists in
Chroma.

### 7.4 Verified structured policy rules

```sql
CREATE TABLE policy_rules (
    rule_id UUID PRIMARY KEY,
    chunk_id UUID NOT NULL REFERENCES policy_chunks(chunk_id),
    rule_type VARCHAR(50) NOT NULL,
    retailer VARCHAR(100) NOT NULL,
    country_code CHAR(2) NOT NULL DEFAULT 'IN',
    product_category VARCHAR(100),
    seller_scope VARCHAR(50),
    condition_scope VARCHAR(50),
    resolution VARCHAR(50),
    window_days INTEGER CHECK (window_days IS NULL OR window_days >= 0),
    rule_conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_excerpt TEXT NOT NULL,
    verification_status VARCHAR(30) NOT NULL DEFAULT 'PROPOSED',
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_policy_rules_lookup
    ON policy_rules (
        retailer, rule_type, product_category, condition_scope,
        verification_status
    );
```

The LLM may propose structured rules, but only `VERIFIED` rules may directly
drive deterministic eligibility decisions.

### 7.5 Seller authorization aliases and evidence

The existing `authorized_sellers` table remains. These tables add exact alias
handling and provenance.

```sql
CREATE TABLE authorized_seller_aliases (
    alias_id BIGSERIAL PRIMARY KEY,
    authorized_seller_id INTEGER NOT NULL
        REFERENCES authorized_sellers(id),
    marketplace VARCHAR(100) NOT NULL,
    seller_alias VARCHAR(255) NOT NULL,
    normalized_alias VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (marketplace, normalized_alias)
);

CREATE TABLE authorized_seller_evidence (
    evidence_id UUID PRIMARY KEY,
    authorized_seller_id INTEGER NOT NULL
        REFERENCES authorized_sellers(id),
    source_url TEXT NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Fuzzy substring matching must not establish seller authorization.

### 7.6 Product reviews and review-risk signals

```sql
CREATE TABLE product_reviews (
    review_record_id UUID PRIMARY KEY,
    canonical_id VARCHAR(50) NOT NULL REFERENCES products(canonical_id),
    provider VARCHAR(50) NOT NULL,
    marketplace VARCHAR(100) NOT NULL,
    provider_review_id VARCHAR(255),
    rating NUMERIC(3,2),
    review_title TEXT,
    review_body TEXT NOT NULL,
    verified_purchase BOOLEAN,
    reviewed_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (provider, marketplace, provider_review_id)
);

CREATE INDEX idx_product_reviews_product
    ON product_reviews (canonical_id, reviewed_at DESC);

CREATE TABLE product_review_risk_signals (
    signal_id UUID PRIMARY KEY,
    canonical_id VARCHAR(50) NOT NULL REFERENCES products(canonical_id),
    risk_type VARCHAR(50) NOT NULL,
    severity VARCHAR(50) NOT NULL,
    supporting_review_count INTEGER NOT NULL CHECK (supporting_review_count > 0),
    confidence NUMERIC(5,2) NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    evidence_review_ids JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Customer reviews can support product-risk warnings, but never official policy,
seller authorization, or warranty claims.

### 7.7 Agent 3 analysis runs

```sql
CREATE TABLE eligibility_analysis_runs (
    analysis_id UUID PRIMARY KEY,
    query TEXT NOT NULL,
    canonical_id VARCHAR(50) REFERENCES products(canonical_id),
    request_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(50) NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);

CREATE TABLE eligibility_market_runs (
    analysis_id UUID NOT NULL
        REFERENCES eligibility_analysis_runs(analysis_id),
    run_id VARCHAR(32) NOT NULL REFERENCES market_search_runs(run_id),
    PRIMARY KEY (analysis_id, run_id)
);
```

### 7.8 Per-offer deterministic assessments

```sql
CREATE TABLE offer_safety_assessments (
    assessment_id UUID PRIMARY KEY,
    analysis_id UUID NOT NULL
        REFERENCES eligibility_analysis_runs(analysis_id),
    offer_id VARCHAR(32) NOT NULL REFERENCES market_offers(offer_id),
    variant_key TEXT NOT NULL,
    seller_status VARCHAR(40) NOT NULL,
    warranty_status VARCHAR(40) NOT NULL,
    return_status VARCHAR(40) NOT NULL,
    delivery_status VARCHAR(40) NOT NULL,
    condition_status VARCHAR(40) NOT NULL,
    promotion_eligibility VARCHAR(40) NOT NULL,
    price_score NUMERIC(5,2) CHECK (price_score BETWEEN 0 AND 100),
    seller_score NUMERIC(5,2) CHECK (seller_score BETWEEN 0 AND 100),
    warranty_score NUMERIC(5,2) CHECK (warranty_score BETWEEN 0 AND 100),
    delivery_score NUMERIC(5,2) CHECK (delivery_score BETWEEN 0 AND 100),
    deal_score NUMERIC(5,2) CHECK (deal_score BETWEEN 0 AND 100),
    evidence_confidence NUMERIC(5,2)
        CHECK (evidence_confidence BETWEEN 0 AND 100),
    safety_status VARCHAR(40) NOT NULL,
    within_budget BOOLEAN,
    amount_over_budget NUMERIC(14,2),
    risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    score_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (analysis_id, offer_id)
);

CREATE INDEX idx_offer_safety_analysis
    ON offer_safety_assessments (analysis_id, variant_key, deal_score DESC);
```

### 7.9 Claim-to-policy evidence links

```sql
CREATE TABLE assessment_policy_evidence (
    assessment_id UUID NOT NULL
        REFERENCES offer_safety_assessments(assessment_id),
    chunk_id UUID NOT NULL REFERENCES policy_chunks(chunk_id),
    claim_type VARCHAR(50) NOT NULL,
    claim_status VARCHAR(30) NOT NULL,
    relevance_score NUMERIC(7,6),
    evidence_excerpt TEXT NOT NULL,
    PRIMARY KEY (assessment_id, chunk_id, claim_type)
);
```

### 7.10 Chroma index manifests

```sql
CREATE TABLE rag_index_manifests (
    index_id UUID PRIMARY KEY,
    corpus_type VARCHAR(30) NOT NULL,
    collection_name VARCHAR(255) NOT NULL UNIQUE,
    embedding_provider VARCHAR(50) NOT NULL,
    embedding_model VARCHAR(100) NOT NULL,
    embedding_dimensions INTEGER NOT NULL,
    document_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    corpus_hash CHAR(64) NOT NULL,
    status VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_one_active_rag_index
    ON rag_index_manifests (corpus_type)
    WHERE status = 'ACTIVE';
```

This prevents the application from querying a partially built or incompatible
Chroma collection.

---

## 8. ChromaDB Collections and Schemas

Chroma does not enforce relational tables. The application will enforce a
strict collection contract and validate metadata during indexing.

### 8.1 Retailer policy collection

Versioned collection name:

```text
retailer_policy_chunks_<index_id>
```

Example record:

```json
{
  "id": "policy-chunk-uuid",
  "document": "Mobile phones are eligible for replacement...",
  "embedding": [0.012, -0.034, 0.091],
  "metadata": {
    "document_version_id": "uuid",
    "source_id": "croma-return-policy",
    "retailer": "croma",
    "country_code": "IN",
    "policy_type": "RETURN",
    "product_category": "MOBILE_PHONE",
    "seller_scope": "FIRST_PARTY",
    "condition_scope": "NEW",
    "heading_path": "Returns > Digital > Mobile Phones",
    "chunk_type": "POLICY_CLAUSE",
    "effective_from": "2026-01-01",
    "is_active": true,
    "content_sha256": "sha256"
  }
}
```

Chroma metadata must remain scalar. Complex exceptions and structured rules
remain in PostgreSQL.

### 8.2 Product-review collection

Versioned collection name:

```text
product_review_chunks_<index_id>
```

Example record:

```json
{
  "id": "review-chunk-uuid",
  "document": "The display developed a green line after...",
  "embedding": [0.022, -0.014, 0.041],
  "metadata": {
    "review_record_id": "uuid",
    "canonical_id": "B0EXAMPLE",
    "marketplace": "amazon.in",
    "rating": 2,
    "verified_purchase": true,
    "reviewed_at": "2026-08-15",
    "risk_scope": "DISPLAY"
  }
}
```

Policy and review documents must remain in different collections so review text
can never be retrieved as an official retailer rule.

### 8.3 Test collections

Automated tests use distinct collection names, for example:

```text
retailer_policy_chunks_test_<test_run_id>
product_review_chunks_test_<test_run_id>
```

Tests must never write to the development or production collection.

---

## 9. Local Development Services and Persistence

The local Compose environment will contain two persistent database services:

```text
compose.yaml
+-- postgres
|   +-- host port: 5433
|   +-- named volume: pricelens_postgres_data
|
+-- chroma
    +-- host port: 8000
    +-- named volume: pricelens_chroma_data
```

Suggested ignored local environment variables:

```dotenv
DATABASE_URL=postgresql://price_lenses:local_password@localhost:5433/price_lenses
PL_TEST_DATABASE_URL=postgresql://price_lenses:local_password@localhost:5433/price_lenses_test

CHROMA_HOST=localhost
CHROMA_PORT=8000
CHROMA_TENANT=default_tenant
CHROMA_DATABASE=price_lenses

POLICY_EMBEDDING_MODEL=text-embedding-3-small
POLICY_EMBEDDING_DIMENSIONS=1536
```

Secrets must not be stored in `.env.example`, documentation, code, logs, or
Git. The example environment file will contain placeholders only.

The local named volumes persist when containers stop. They are deleted only by
an explicit volume-removal operation, which is not part of normal startup or
shutdown.

---

## 10. Policy Discovery and Web Ingestion

### 10.1 Runtime boundary

Agent 3 will not search the open web during each product analysis. Web search
is an offline administrative discovery process.

```text
Web search / SerpAPI
        |
        v
Candidate official URLs
        |
        v
Domain and content validation
        |
        v
Fetch actual HTML/PDF
        |
        v
Version in PostgreSQL
        |
        v
Chunk and index approved content
```

Search-result snippets are never indexed as policy evidence.

### 10.2 Discovery searches

Examples:

```text
site:amazon.in/gp/help/customer/display returns replacement cancellation India
site:flipkart.com/helpcentre return refund replacement electronics
site:croma.com cancellation return warranty FAQ
site:reliancedigital.in cancellation return refund policy
site:vijaysales.com FAQ cancellation return warranty
```

SerpAPI may be used for scheduled discovery because it is already part of the
project. It is not used to manufacture policy facts.

### 10.3 Official-domain allowlist

```text
amazon.in
flipkart.com
stories.flipkart.com
croma.com
reliancedigital.in
vijaysales.com
```

Official government domains can be added to a separate allowlist for Indian
consumer regulations.

Reject:

- Seller forums as customer-policy authority.
- Reddit and social posts.
- Personal blogs.
- Third-party policy summaries.
- Cached or copied content without provenance.
- Search snippets without fetching the source.
- AI-generated policy summaries.

### 10.4 Fetch methods

| Source type | Fetch method |
|---|---|
| Static HTML | HTTP client plus structured HTML parser |
| JavaScript-rendered page | Playwright or a configured Apify website-content actor |
| PDF | Controlled download plus page-preserving PDF extraction |
| CAPTCHA/login wall | Mark blocked; do not bypass access controls |
| Manually supplied official copy | Store with URL, retrieval date, content hash, and reviewer |

### 10.5 Fetch and security validation

- Require HTTPS.
- Resolve and validate the final redirected hostname.
- Reject private, loopback, link-local, and non-allowlisted network targets.
- Apply connection/read timeouts and maximum document sizes.
- Respect retailer terms, robots rules, and reasonable request rates.
- Detect CAPTCHA, login, empty shell, and JavaScript placeholder pages.
- Require a minimum amount of meaningful policy text.
- Remove scripts, styles, navigation, forms, and duplicated menus.
- Sanitize content before storage and LLM use.
- Calculate a SHA-256 hash.
- Never overwrite a good document with a failed or incomplete fetch.

### 10.6 Controlled link crawling

From an approved help page, follow only one level of links containing policy
keywords such as:

```text
return, replacement, refund, cancellation, warranty, delivery,
shipping, payment, EMI, open-box, terms, FAQ
```

Recommended limits:

- Maximum crawl depth: 1.
- Maximum pages per retailer: 30.
- Delay between requests: 1–3 seconds.
- Deduplicate canonical URLs and content hashes.

### 10.7 Update lifecycle

```text
DISCOVERED -> VALIDATED -> FETCHED -> PARSED -> APPROVED -> INDEXED
```

Failure states:

```text
BLOCKED, TOO_SHORT, WRONG_DOMAIN, DUPLICATE, IRRELEVANT, REJECTED
```

Suggested schedule:

- Weekly discovery of new official policy URLs.
- Daily or weekly content-hash checks for approved sources.
- Immediate new version when content changes.
- Old PostgreSQL versions retained for audit.
- New Chroma collection built in staging and activated only after validation.

---

## 11. Policy-Aware Chunking

Generic fixed-character chunking is insufficient for policy documents.

### 11.1 FAQ pages

- Keep one complete question and answer together.
- Typical size: 100–350 tokens.
- No overlap unless an answer explicitly depends on the preceding item.
- Store the question in the chunk text and metadata.

### 11.2 Policy sections

- Split by the heading hierarchy.
- Typical child chunk: 250–500 tokens.
- Overlap: approximately 50–80 tokens.
- Preserve the complete heading path.
- Do not separate exceptions from the rule they modify.

### 11.3 Tables

Each table row becomes readable text and repeats its column headings:

```text
Retailer: Croma
Category: Mobile Phones
Window: 7 days
Resolution: Replacement
Condition: Manufacturing defect
```

### 11.4 Restrictive clauses

Clauses containing these concepts must remain with the governing rule:

- except
- unless
- only if
- not eligible
- replacement only
- after activation
- opened package
- physical damage
- authorized seller
- inspection or technician verification

### 11.5 Parent-child chunks

- Child chunks are used for semantic retrieval.
- Parent sections retain surrounding context.
- After a child matches, Agent 3 loads the parent and adjacent exception
  clauses before policy extraction.

Every embedded chunk includes a context prefix:

```text
Retailer: Flipkart
Country: India
Policy: Return and Replacement
Category: Mobile Phones
Section: Replacement-only products
```

---

## 12. Hybrid Semantic Search

### 12.1 Query construction

Agent 3 builds small policy-specific questions from the offer context instead
of embedding the entire user conversation:

```text
What is Amazon India's return or replacement policy for a new mobile phone?

Does manufacturer warranty apply to this brand when sold by a third-party
Amazon India seller?

What is the damaged-on-delivery policy for this product category?
```

### 12.2 Metadata filtering

Filter before ranking:

```text
country_code = IN
retailer = the offer retailer
policy_type = the requested policy types
product_category IN (exact category, ELECTRONICS, ALL)
condition_scope IN (exact condition, ALL)
is_active = true
```

### 12.3 Retrieval stages

1. Retrieve semantic candidates from Chroma.
2. Retrieve keyword candidates from PostgreSQL `search_vector`.
3. Merge rankings using reciprocal-rank fusion.
4. Remove duplicate chunks.
5. Select approximately five to eight chunks.
6. Expand parent sections and adjacent exceptions.
7. Reload canonical chunk text from PostgreSQL.
8. Confirm that source and document versions are still active.

Chroma results are never treated as authoritative without reloading and
validating the corresponding PostgreSQL `chunk_id`.

### 12.4 No-match and conflict behavior

- No relevant chunk: `INSUFFICIENT_POLICY_EVIDENCE`.
- Conflicting current policies: `POLICY_CONFLICT`.
- Only expired/stale policy available: `STALE_POLICY`.
- Policy exists but category/seller scope differs: `NOT_APPLICABLE`.

Unknown evidence must never be converted into a positive warranty or return
guarantee.

---

## 13. Agent 3 Tools

Required tools execute in a fixed audited sequence. The LLM does not decide to
skip mandatory safety checks.

### Tool 1: `load_offer_safety_evidence`

Loads only explicit offer IDs from the current Agent 2 snapshot:

- Product and variant identity.
- Marketplace and seller.
- Listed and conditional price.
- Seller ID/rating and fulfillment badge.
- Availability and fetch timestamp.
- Condition, warranty, return, and delivery data.
- Product-bound promotions.

### Tool 2: `check_seller_authorization`

Returns one of:

```text
VERIFIED_AUTHORIZED
FIRST_PARTY_RETAILER
VERIFIED_UNAUTHORIZED
UNVERIFIED
NOT_APPLICABLE
```

### Tool 3: `search_retailer_policy`

Inputs:

- Retailer
- Policy types
- Product category
- Brand
- Condition
- Seller scope
- Focused question

Returns policy chunk IDs, source URLs, similarity/rank information, effective
dates, and canonical excerpts.

### Tool 4: `apply_policy_and_warranty_rules`

Evaluates:

- Manufacturer/seller/no/unknown warranty.
- Refund, return, replacement, or no-return resolution.
- Policy window and conditions.
- New/renewed/open-box condition compatibility.
- Product-category exclusions.

### Tool 5: `analyse_product_review_risks`

Detects a controlled taxonomy such as:

- Display line or panel failure.
- Heating and thermal throttling.
- Battery drain or swelling.
- Camera defect.
- Performance instability.
- Damaged or previously opened delivery.
- Warranty or service-centre complaints.

A severe risk requires corroboration. A single review or a provider-generated
AI summary cannot independently create a severe warning.

### Tool 6: `calculate_offer_safety_scores`

Pure deterministic Python. No LLM arithmetic.

### Tool 7: `verify_eligibility_report`

Confirms that every recommended price, seller, policy, promotion, and score is
grounded in stored evidence.

---

## 14. Deterministic Scoring and Safety Rules

### 14.1 Deal score

Retain the documented component weights:

```text
DealScore =
    0.40 * PriceScore
  + 0.30 * SellerScore
  + 0.20 * WarrantyScore
  + 0.10 * DeliveryScore
```

### 14.2 Price-score correction

The older formula `100 * (1 - effective_price / budget)` should not be used:
it gives an offer at the exact budget a score of zero and cannot operate when
the user supplies no budget.

Use a comparable-market score:

```text
PriceScore =
    100 * lowest_comparable_eligible_price / candidate_eligible_price
```

Clamp the result to `0..100`. Compare only the same verified variant and
condition.

Budget is a separate result:

```text
within_budget = eligible_effective_price <= budget
amount_over_budget = max(0, eligible_effective_price - budget)
```

### 14.3 Seller score

Initial rule set:

| Seller evidence | Score |
|---|---:|
| Verified authorized or confirmed first-party | 100 |
| Strong marketplace seller but authorization unknown | Maximum 60 |
| Seller identified but important evidence missing | 40 |
| Seller not identified | 30 |
| Verified unauthorized where authorization is required | 0 |

Seller rating contributes to trust but does not prove authorization.

### 14.4 Warranty score

| Warranty evidence | Score |
|---|---:|
| Verified manufacturer warranty | 100 |
| Manufacturer warranty claimed but not independently confirmed | 70 |
| Seller-only warranty | 30 |
| Explicitly no warranty | 0 |
| Missing evidence | Unknown; reduce confidence |

### 14.5 Delivery score

| Delivery | Score |
|---|---:|
| One day or equivalent | 100 |
| Two to five days | 70 |
| Six to seven days | 40 |
| More than seven days | 0 |
| Missing | Unknown; reduce confidence |

### 14.6 Condition handling

- Exact requested condition: no penalty.
- Renewed/open-box when the user requested new: disqualify.
- Missing condition: `UNVERIFIED`, never silently assume `NEW`.
- Different condition groups are not directly compared.

### 14.7 Safety status

Every offer receives both a numeric score and a categorical state:

```text
SAFE_VERIFIED
SAFE_WITH_WARNINGS
UNVERIFIED
DISQUALIFIED
```

A cheap offer with weak seller or policy evidence may have a strong price score
but cannot become the safest verified recommendation.

### 14.8 Hard disqualifications

- Wrong product family or incompatible variant.
- Accessory or misleading bundle.
- Renewed/open-box item when new was requested.
- Explicitly invalid warranty where manufacturer warranty is required.
- Verified unauthorized seller where authorization is required.
- Stale, unavailable, or ungrounded listing.
- Price not traceable to the supplied Agent 2 offer snapshot.

### 14.9 Evidence confidence

Evidence confidence is separate from deal score. It should include:

- Product/variant identity completeness.
- Offer freshness.
- Seller identity and authorization provenance.
- Policy freshness and applicability.
- Warranty/return/delivery field completeness.
- Review depth where a review-risk conclusion is shown.

Missing evidence reduces confidence rather than being silently treated as
safe or unsafe.

---

## 15. Promotion Eligibility Boundary

Agent 2 owns discovery and normalization of product-specific promotions. Agent
3 evaluates whether the current user appears eligible.

Inputs may include:

- Bank
- Credit/debit card type
- EMI preference
- Minimum transaction amount
- New-customer restriction
- Exchange requirement

Prices remain distinct:

```text
listed_price
eligible_effective_price
conditional_effective_price
```

When required inputs are missing:

```text
promotion_eligibility = UNKNOWN
price_type = CONDITIONAL
```

Agent 3 must not present a conditional bank, EMI, coupon, or exchange price as
guaranteed.

---

## 16. LLM Boundary

The LLM runs after retrieval and deterministic calculations.

### 16.1 Allowed LLM work

- Convert retrieved policy clauses into a typed proposal.
- Explain why the cheapest offer was not selected.
- Summarize warranty and return-policy differences.
- Summarize corroborated product-review concerns.
- Explain unknown or conflicting evidence.
- Produce a concise user-facing Agent 3 summary.

### 16.2 Prohibited LLM work

- Calculate deal or confidence scores.
- Change deterministic safety classifications.
- Declare an unknown seller authorized.
- Invent return windows, warranty coverage, or delivery promises.
- Turn a conditional price into a guaranteed price.
- Issue `BUY_NOW` or `WAIT`.

### 16.3 Policy extraction response

```json
{
  "answer_status": "SUPPORTED",
  "return_type": "REPLACEMENT_ONLY",
  "window_days": 7,
  "manufacturer_warranty": "CONDITIONAL",
  "conditions": [],
  "warnings": [],
  "citations": [
    {
      "chunk_id": "uuid",
      "claim": "Replacement-only within the applicable window"
    }
  ]
}
```

If evidence is insufficient, the output must say so explicitly.

### 16.4 Failure behavior

If the LLM is unavailable, Agent 3 still returns:

- Offer classifications.
- Deterministic scores.
- Policy passages and citations.
- Warnings and missing evidence.
- A deterministic fallback summary.

---

## 17. Verification Gate

Before Agent 3 returns a report, verify:

- Every result references a real `offer_id` in the supplied snapshot.
- Stored price and seller match the report.
- Variant grouping is consistent.
- Seller authorization has registry evidence.
- Policy chunks exist in PostgreSQL and are active.
- Retailer, country, category, seller scope, and condition scope apply.
- Policy citations support the associated typed claim.
- Conditional promotions are labeled conditional.
- Stale offers cannot become current recommendations.
- Scores recompute exactly from stored component values.
- Reviews are not used as official policy evidence.
- The report contains no final purchase-timing verdict.

Verification failure downgrades or rejects the affected claim/offer; it does
not silently repair the result using an LLM.

---

## 18. Agent 3 Output Contract

```json
{
  "schema_version": "1.0",
  "agent": "eligibility_safety_analyst",
  "status": "complete",
  "analysis_id": "uuid",
  "market_snapshot_run_ids": ["run-id"],
  "total_offers_evaluated": 5,
  "variant_results": [
    {
      "variant_key": "12GB|256GB|Black|NEW",
      "cheapest_raw_offer": {},
      "recommended_safe_offer": {},
      "offers": [
        {
          "offer_id": "offer-id",
          "retailer": "Amazon India",
          "seller_name": "Example Seller",
          "listed_price": 104999,
          "eligible_effective_price": null,
          "conditional_effective_price": 99999,
          "condition": "NEW",
          "seller_status": "UNVERIFIED",
          "warranty_status": "CLAIMED_MANUFACTURER",
          "return_status": "REPLACEMENT_ONLY",
          "delivery_status": "STANDARD",
          "promotion_eligibility": "UNKNOWN",
          "deal_score": 72,
          "evidence_confidence": 58,
          "safety_status": "SAFE_WITH_WARNINGS",
          "score_breakdown": {},
          "risk_flags": [],
          "evidence_chunk_ids": []
        }
      ]
    }
  ],
  "product_risk_signals": [],
  "warnings": [],
  "summary": "Grounded Agent 3 explanation",
  "agent_trace": []
}
```

There is one cheapest and one safest result per variant group, not one result
across incompatible variants.

---

## 19. UI Plan

Add a third application tab:

```text
History | Live Market Investigator | Purchase Safety
```

The Purchase Safety tab displays:

- Safest offer for each variant.
- Cheapest offer versus safest offer.
- Score component breakdown.
- Seller authorization/trust status.
- Warranty and return/replacement status.
- Item condition.
- Delivery estimate.
- Conditional promotion eligibility.
- Product-review risk signals.
- Policy freshness and official source links.
- Expandable `How Agent 3 evaluated these offers` trace.

Do not expose large raw JSON contracts in the normal UI. Do not show a final
multi-agent `BUY_NOW` or `WAIT` decision until the synthesizer and final
verifier are implemented.

---

## 20. Initial Local Dataset

### 20.1 PostgreSQL

Start with:

- One approved official return/cancellation source for each of the five
  retailers.
- One active version per source.
- Approximately 5–15 chunks per document.
- Existing Agent 2 offers for one known electronics product.
- One Agent 3 analysis run.
- Three to five per-offer assessments.
- Seller authorization entries only when an official source supports them.

Do not seed invented policy facts into development data. Synthetic policies
belong only in isolated test fixtures.

### 20.2 Chroma

```text
5 policy documents
    -> approximately 30–60 approved chunks
    -> one staged retailer-policy collection
    -> retrieval validation
    -> active collection
```

This dataset must validate retailer/category filtering, cancellation, returns,
replacement-only clauses, citations, no-match behavior, and LLM fallback.

---

## 21. Proposed Project Structure

```text
tools/
+-- eligibility_agent.py
+-- eligibility_models.py
+-- eligibility_tools.py
+-- eligibility_scoring.py
+-- eligibility_verifier.py
+-- eligibility_db.py
+-- eligibility_ui.py
+-- policy_corpus.py
+-- policy_chunking.py
+-- policy_retrieval.py
+-- policy_extraction.py
+-- review_risk.py

scripts/
+-- db/
|   +-- migrations/
|       +-- 002_eligibility_agent.sql
+-- policies/
    +-- discover_policy_sources.py
    +-- fetch_policy_documents.py
    +-- parse_policy_documents.py
    +-- chunk_policy_documents.py
    +-- build_chroma_index.py
    +-- validate_policy_index.py
    +-- refresh_policy_corpus.py

tests/
+-- test_eligibility_scoring.py
+-- test_eligibility_verifier.py
+-- test_policy_discovery.py
+-- test_policy_chunking.py
+-- test_policy_retrieval.py
+-- test_seller_authorization.py
+-- test_review_risk.py
+-- test_eligibility_agent.py
+-- test_eligibility_ui.py
```

---

## 22. Implementation Phases

### Phase 1 — Contracts and local services

- Add request/report models.
- Add Chroma service and named volume to Compose.
- Add environment validation and placeholder documentation.
- Add separate local development and test database/collection configuration.

### Phase 2 — Additive PostgreSQL schema

- Create migration `002_eligibility_agent.sql`.
- Apply it to the local database only first.
- Confirm no existing table or row is deleted or rewritten.
- Add repository methods and foreign-key tests.

### Phase 3 — Policy discovery and ingestion

- Seed official URL candidates for five retailers.
- Implement allowlisted discovery and secure fetching.
- Implement immutable versioning and content-hash detection.
- Add blocked/dynamic-page fallback.

### Phase 4 — Chunking and Chroma indexing

- Implement FAQ, policy, table, and parent-child chunking.
- Store canonical chunks in PostgreSQL.
- Build a versioned Chroma collection.
- Store and activate an index manifest only after validation.

### Phase 5 — Hybrid retrieval and policy verification

- Add Chroma dense retrieval.
- Add PostgreSQL keyword retrieval.
- Add reciprocal-rank fusion and metadata filtering.
- Reload canonical chunks from PostgreSQL.
- Add no-match, stale, conflict, and applicability handling.

### Phase 6 — Seller and offer safety engine

- Read explicit Agent 2 offers.
- Add seller authorization and alias resolution.
- Add condition, warranty, return, delivery, and freshness classification.
- Add promotion-eligibility evaluation.
- Add deterministic scoring and hard gates.

### Phase 7 — Review intelligence

- Persist normalized product-specific reviews.
- Build a separate review collection.
- Add risk taxonomy, recency, corroboration, and severity thresholds.
- Prevent cross-product and cross-variant contamination.

### Phase 8 — Agent runtime and LLM summary

- Execute mandatory tools in a fixed sequence.
- Add grounded structured policy extraction.
- Add claim-level citation verification.
- Add deterministic fallback and execution trace.

### Phase 9 — Orchestration

- Replace the current Agent 3 stub.
- Pass Agent 2 run/offer IDs into Agent 3.
- Preserve disjoint report keys for the future join barrier.
- Leave the future synthesizer behavior unchanged.

### Phase 10 — UI and production hardening

- Add Purchase Safety tab.
- Show per-variant cheapest and safest offers.
- Add policy citations, freshness, confidence, warnings, and trace.
- Add metrics, structured logs, timeouts, retries, and cost controls.

---

## 23. Test and Validation Plan

### 23.1 Unit tests

- Seller aliases do not create false authorization.
- Seller ratings do not prove authorization.
- Warranty/condition/return/delivery classifications.
- Every score component and boundary.
- Budget absent, under budget, and over budget.
- Conditional bank/card/EMI prices.
- Stale and unavailable offer rejection.
- Variant isolation.
- FAQ, table, clause, and exception chunking.
- URL/domain validation and redirect rejection.
- Policy conflict, stale policy, and no-match behavior.
- LLM citation and structured-output validation.
- LLM outage fallback.

### 23.2 Retrieval evaluation

Build a fixed question set for all supported retailers and electronics
categories. Measure:

- Recall at 5.
- Correct retailer and policy-type filtering.
- Correct category applicability.
- Citation accuracy.
- Exception-clause retrieval.
- No-answer accuracy.
- Conflict detection.

### 23.3 PostgreSQL integration tests

- Run against `PL_TEST_DATABASE_URL` only.
- Apply migrations into an isolated test database.
- Validate all foreign keys and unique constraints.
- Confirm append-only policy versioning.
- Confirm Agent 3 uses supplied run/offer IDs.
- Confirm test records never touch Neon.

### 23.4 Chroma integration tests

- Use a test-only database/collection.
- Validate collection-manifest compatibility.
- Verify staged builds do not replace an active index on failure.
- Rebuild Chroma from PostgreSQL and compare chunk counts/hashes.
- Confirm policy and review collections remain isolated.

### 23.5 End-to-end scenarios

1. Cheapest offer is verified safe.
2. Cheapest offer has unknown seller and warranty.
3. Cheapest offer is renewed while user requested new.
4. Retailer permits replacement but not refund.
5. Conditional bank price without user card details.
6. All offers are stale.
7. Broad query returns multiple independent variants.
8. Policy source is stale or conflicting.
9. Chroma is offline but structured rules still work.
10. LLM is offline but deterministic analysis still completes.

---

## 24. Observability and Operations

Record metrics for:

- Policy discovery and fetch success/failure.
- Blocked or insufficient-content pages.
- Document changes and new versions.
- Chunk and embedding counts.
- Chroma retrieval latency.
- PostgreSQL keyword retrieval latency.
- Retrieval no-match and policy-conflict rates.
- Agent 3 partial/error rates.
- Seller/warranty/return evidence completeness.
- LLM latency, failure, and fallback rate.
- Verification rejection reasons.

Never log API keys, database passwords, raw connection strings, or sensitive
user payment details.

---

## 25. Acceptance Criteria

Agent 3 is complete when:

- It evaluates the exact offer observations supplied by Agent 2.
- Broad queries return separate results for every verified variant.
- It distinguishes the cheapest offer from the safest offer.
- It never presents unknown seller authorization as verified.
- It never presents a conditional promotion as guaranteed.
- Stale offers cannot become current recommendations.
- Every policy claim links to an active PostgreSQL chunk and official source.
- Policy and review evidence cannot be mixed.
- The Chroma index can be rebuilt from PostgreSQL.
- The deterministic report works when the LLM is unavailable.
- The report does not issue the final `BUY_NOW` or `WAIT` decision.
- Local PostgreSQL and Chroma data persist across normal container restarts.
- Tests use isolated databases and collections.
- No existing PriceLens data is deleted or destructively modified.

---

## 26. Explicitly Deferred

- Final multi-agent decision synthesis.
- Final purchase-decision verifier integration beyond Agent 3's own report.
- International pricing or travel arbitrage.
- Generic bank-offer web search.
- Non-electronics categories.
- Automatic trust in unreviewed policy discoveries.
- Using uncorroborated customer reviews as decisive safety evidence.

This scope keeps Agent 3 focused: determine whether each current India-market
offer is usable and safe, explain the evidence, and hand a verified specialist
report to the future synthesizer.
