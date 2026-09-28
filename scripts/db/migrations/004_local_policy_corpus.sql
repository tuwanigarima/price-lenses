-- Isolate the curated local collection from legacy web-ingested policy rows.
-- Publication uses one transaction after every embedding has been validated.
CREATE SCHEMA IF NOT EXISTS policy_local;
CREATE TABLE IF NOT EXISTS policy_local.builds (
    build_id UUID PRIMARY KEY,
    corpus_hash CHAR(64) NOT NULL,
    embedding_model TEXT NOT NULL,
    chunker_version TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS policy_local_one_active
    ON policy_local.builds (active) WHERE active;
CREATE TABLE IF NOT EXISTS policy_local.documents (
    document_id UUID PRIMARY KEY,
    build_id UUID NOT NULL REFERENCES policy_local.builds(build_id),
    source_key TEXT NOT NULL,
    publisher TEXT NOT NULL,
    corpus TEXT NOT NULL,
    source_url TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL,
    effective_from DATE,
    effective_until DATE,
    UNIQUE (build_id, source_key)
);
CREATE TABLE IF NOT EXISTS policy_local.chunks (
    chunk_id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES policy_local.documents(document_id),
    content TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    heading_path TEXT NOT NULL,
    policy_types TEXT[] NOT NULL,
    product_category TEXT NOT NULL,
    metadata JSONB NOT NULL,
    embedding VECTOR(1536) NOT NULL,
    search_vector TSVECTOR GENERATED ALWAYS AS
        (to_tsvector('english', content)) STORED
);
CREATE INDEX IF NOT EXISTS policy_local_chunk_fts ON policy_local.chunks USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS policy_local_chunk_topics ON policy_local.chunks USING GIN(policy_types);
CREATE INDEX IF NOT EXISTS policy_local_documents_build ON policy_local.documents(build_id);
CREATE INDEX IF NOT EXISTS policy_local_chunks_document ON policy_local.chunks(document_id);
