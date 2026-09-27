-- Agent 3: Eligibility & Safety Analyst.
-- This migration is additive. Existing product, history, and market evidence
-- is never deleted or rewritten.

CREATE TABLE IF NOT EXISTS policy_sources (
    source_id UUID PRIMARY KEY,
    retailer VARCHAR(100) NOT NULL,
    policy_type VARCHAR(50) NOT NULL,
    source_url TEXT NOT NULL UNIQUE,
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
    last_checked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_policy_sources_scope
    ON policy_sources (country_code, retailer, policy_type, enabled);

CREATE TABLE IF NOT EXISTS policy_document_versions (
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_policy_one_active_version
    ON policy_document_versions (source_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_policy_documents_active
    ON policy_document_versions (is_active, retrieved_at DESC);

CREATE TABLE IF NOT EXISTS policy_chunks (
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
        to_tsvector('english', COALESCE(heading_path, '') || ' ' || COALESCE(content, ''))
    ) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_version_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_policy_chunks_filters
    ON policy_chunks (
        country_code, retailer, policy_type, product_category, is_active
    );
CREATE INDEX IF NOT EXISTS idx_policy_chunks_text_search
    ON policy_chunks USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS policy_rules (
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

CREATE INDEX IF NOT EXISTS idx_policy_rules_lookup
    ON policy_rules (
        retailer, rule_type, product_category, condition_scope,
        verification_status
    );

CREATE TABLE IF NOT EXISTS authorized_seller_aliases (
    alias_id BIGSERIAL PRIMARY KEY,
    authorized_seller_id INTEGER NOT NULL REFERENCES authorized_sellers(id),
    marketplace VARCHAR(100) NOT NULL,
    seller_alias VARCHAR(255) NOT NULL,
    normalized_alias VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (marketplace, normalized_alias)
);

CREATE TABLE IF NOT EXISTS authorized_seller_evidence (
    evidence_id UUID PRIMARY KEY,
    authorized_seller_id INTEGER NOT NULL REFERENCES authorized_sellers(id),
    source_url TEXT NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS product_reviews (
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
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_product_review_provider_id
    ON product_reviews (provider, marketplace, provider_review_id)
    WHERE provider_review_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_product_reviews_product
    ON product_reviews (canonical_id, reviewed_at DESC);

CREATE TABLE IF NOT EXISTS product_review_risk_signals (
    signal_id UUID PRIMARY KEY,
    canonical_id VARCHAR(50) NOT NULL REFERENCES products(canonical_id),
    risk_type VARCHAR(50) NOT NULL,
    severity VARCHAR(50) NOT NULL,
    supporting_review_count INTEGER NOT NULL CHECK (supporting_review_count > 0),
    confidence NUMERIC(5,2) NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    evidence_review_ids JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS eligibility_analysis_runs (
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

CREATE TABLE IF NOT EXISTS eligibility_market_runs (
    analysis_id UUID NOT NULL REFERENCES eligibility_analysis_runs(analysis_id),
    run_id VARCHAR(32) NOT NULL REFERENCES market_search_runs(run_id),
    PRIMARY KEY (analysis_id, run_id)
);

CREATE TABLE IF NOT EXISTS offer_safety_assessments (
    assessment_id UUID PRIMARY KEY,
    analysis_id UUID NOT NULL REFERENCES eligibility_analysis_runs(analysis_id),
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

CREATE INDEX IF NOT EXISTS idx_offer_safety_analysis
    ON offer_safety_assessments (analysis_id, variant_key, deal_score DESC);

CREATE TABLE IF NOT EXISTS assessment_policy_evidence (
    assessment_id UUID NOT NULL
        REFERENCES offer_safety_assessments(assessment_id),
    chunk_id UUID NOT NULL REFERENCES policy_chunks(chunk_id),
    claim_type VARCHAR(50) NOT NULL,
    claim_status VARCHAR(30) NOT NULL,
    relevance_score NUMERIC(7,6),
    evidence_excerpt TEXT NOT NULL,
    PRIMARY KEY (assessment_id, chunk_id, claim_type)
);

CREATE TABLE IF NOT EXISTS rag_index_manifests (
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_rag_index
    ON rag_index_manifests (corpus_type) WHERE status = 'ACTIVE';
