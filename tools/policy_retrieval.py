"""OpenAI embeddings and PostgreSQL/pgvector hybrid policy retrieval."""
from __future__ import annotations

import hashlib
from typing import Any, Protocol

from .eligibility_models import PolicyHit
from .policy_corpus import usable_policy_content
from .policy_types import policy_keyword_query


class Embedder(Protocol):
    provider: str
    model: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    provider = "openai"

    def __init__(self, *, model: str, dimensions: int, api_key: str, base_url: str):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")
        if dimensions != 1536:
            raise ValueError("OpenAI policy embeddings must use 1536 dimensions")
        from langchain_openai import OpenAIEmbeddings

        self.model = model
        self.dimensions = dimensions
        self._client = OpenAIEmbeddings(
            model=model,
            dimensions=dimensions,
            api_key=api_key,
            base_url=base_url,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)


def build_embedder(settings: Any) -> Embedder:
    if settings.embedding_backend != "openai":
        raise ValueError("Only OpenAI policy embeddings are supported")
    return OpenAIEmbedder(
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        api_key=settings.embedding_api_key or "",
        base_url=settings.embedding_base_url,
    )


def build_policy_index(
    database: Any,
    settings: Any,
    embedder: Embedder | None = None,
    *,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Build missing or changed pgvector rows from canonical policy chunks."""
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    embedder = embedder or build_embedder(settings)
    if embedder.provider != "openai":
        raise ValueError("Production policy indexing requires OpenAI embeddings")
    if embedder.dimensions != 1536:
        raise ValueError("Policy embeddings must contain 1536 dimensions")

    chunks = [
        row
        for row in database.active_policy_chunks(include_parents=False)
        if usable_policy_content(
            str(row.get("document_content") or row.get("content") or "")
        )
    ]
    if not chunks:
        raise ValueError("No approved active policy chunks are available to index")

    corpus_hash = hashlib.sha256(
        "".join(sorted(str(row["content_sha256"]) for row in chunks)).encode("utf-8")
    ).hexdigest()
    chunk_ids = [str(row["chunk_id"]) for row in chunks]
    existing = database.policy_embedding_hashes(embedder.model, chunk_ids)
    pending = [
        row
        for row in chunks
        if existing.get(str(row["chunk_id"])) != str(row["content_sha256"])
    ]

    changed = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = embedder.embed([str(row["content"]) for row in batch])
        if len(vectors) != len(batch):
            raise ValueError("OpenAI returned a different number of embeddings than inputs")
        for vector in vectors:
            if len(vector) != embedder.dimensions:
                raise ValueError(
                    f"OpenAI returned {len(vector)} dimensions; "
                    f"expected {embedder.dimensions}"
                )
        changed += database.upsert_policy_embeddings(
            [
                {
                    "chunk_id": row["chunk_id"],
                    "embedding_model": embedder.model,
                    "content_sha256": row["content_sha256"],
                    "embedding": vector,
                }
                for row, vector in zip(batch, vectors, strict=True)
            ],
            dimensions=embedder.dimensions,
        )

    indexed_count = database.policy_embedding_count(embedder.model)
    if indexed_count < len(chunks):
        raise RuntimeError(
            f"pgvector validation failed: {indexed_count} active embeddings for "
            f"{len(chunks)} active chunks"
        )
    return {
        "backend": "pgvector",
        "embedding_provider": embedder.provider,
        "embedding_model": embedder.model,
        "embedding_dimensions": embedder.dimensions,
        "chunk_count": len(chunks),
        "embedded_count": changed,
        "unchanged_count": len(chunks) - len(pending),
        "corpus_hash": corpus_hash,
    }


class HybridPolicyRetriever:
    def __init__(
        self,
        database: Any,
        settings: Any,
        *,
        embedder: Embedder | None = None,
    ):
        self.database = database
        self.settings = settings
        self.embedder = embedder
        self.warnings: list[str] = []

    def search(
        self,
        question: str,
        *,
        retailers: list[str],
        policy_types: list[str] | None = None,
        product_category: str | None = "electronics",
        limit: int = 8,
    ) -> list[PolicyHit]:
        self.warnings = list(getattr(self.database, "retrieval_warnings", []))
        if hasattr(self.database, "build_id") and self.database.build_id is None:
            return []
        candidate_limit = max(limit * 2, 12)
        lexical = self.database.keyword_policy_search(
            policy_keyword_query(tuple(policy_types or ())),
            retailers=retailers,
            policy_types=policy_types or [],
            product_category=product_category,
            limit=candidate_limit,
        )
        semantic_ids: list[str] = []
        try:
            embedder = self.embedder or build_embedder(self.settings)
            query_vectors = embedder.embed([question])
            if len(query_vectors) != 1:
                raise ValueError("OpenAI did not return one query embedding")
            semantic = self.database.semantic_policy_search(
                query_vectors[0],
                embedding_model=embedder.model,
                retailers=retailers,
                policy_types=policy_types or [],
                product_category=product_category,
                limit=candidate_limit,
                dimensions=embedder.dimensions,
            )
            semantic_ids = [str(row["chunk_id"]) for row in semantic]
        except Exception as exc:
            # Keyword retrieval keeps Agent 3 usable during API or vector outages.
            semantic_ids = []
            self.warnings.append(f"SEMANTIC_RETRIEVAL_UNAVAILABLE: keyword results only ({type(exc).__name__}).")

        scores: dict[str, float] = {}
        sources: dict[str, set[str]] = {}
        for source, ranked_ids in (
            ("keyword", [str(row["chunk_id"]) for row in lexical]),
            ("semantic", semantic_ids),
        ):
            for rank, chunk_id in enumerate(ranked_ids, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
                sources.setdefault(chunk_id, set()).add(source)

        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        canonical = self.database.policy_chunks_by_ids(ordered)
        hits: list[PolicyHit] = []
        for chunk_id in ordered:
            row = canonical.get(chunk_id)
            if not row or not row.get("is_active"):
                continue
            if not usable_policy_content(
                str(row.get("document_content") or row.get("content") or "")
            ):
                continue
            if retailers and row.get("retailer") not in retailers:
                continue
            if policy_types and not set(policy_types).intersection(row.get("policy_types") or [row.get("policy_type")]):
                continue
            hits.append(
                PolicyHit(
                    chunk_id=chunk_id,
                    document_version_id=str(row["document_version_id"]),
                    retailer=str(row["retailer"]),
                    policy_type=str(row["policy_type"]),
                    content=str(row["content"]),
                    source_url=str(row["source_url"]),
                    heading_path=row.get("heading_path"),
                    product_category=row.get("product_category"),
                    seller_scope=row.get("seller_scope"),
                    condition_scope=row.get("condition_scope"),
                    relevance=round(scores[chunk_id], 6),
                    retrieval_sources=tuple(sorted(sources[chunk_id])),
                    policy_types=tuple(row.get("policy_types") or ()),
                    evidence_text=row.get("evidence_text"),
                    parent_context=row.get("parent_context"),
                    captured_at=(row.get("document_metadata") or {}).get("captured_at"),
                    corpus_build_id=str(row["build_id"]) if row.get("build_id") else None,
                    local_path=(row.get("document_metadata") or {}).get("local_path"),
                )
            )
        return hits
