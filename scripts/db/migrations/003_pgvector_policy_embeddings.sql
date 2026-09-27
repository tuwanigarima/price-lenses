-- Agent 3 semantic retrieval in PostgreSQL/Neon using pgvector.
-- This migration is additive: it does not alter, delete, or rewrite existing tables.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_chunk_embeddings (
    chunk_id UUID NOT NULL REFERENCES policy_chunks(chunk_id),
    embedding_model VARCHAR(100) NOT NULL,
    embedding_dimensions INTEGER NOT NULL
        CHECK (embedding_dimensions = 1536),
    embedded_content_sha256 CHAR(64) NOT NULL,
    embedding VECTOR(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chunk_id, embedding_model)
);

CREATE INDEX IF NOT EXISTS idx_policy_chunk_embeddings_model
    ON policy_chunk_embeddings (embedding_model);
