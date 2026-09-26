"""Versioned Chroma indexing and hybrid retrieval for policy evidence."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import requests

from .eligibility_models import PolicyHit


class Embedder(Protocol):
    provider: str
    model: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class HashEmbedder:
    """Offline deterministic embedder intended for tests and local smoke runs."""

    dimensions: int = 256
    provider: str = "hash"
    model: str = "sha256-token-hash-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in text.lower().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                vector[index] += -1.0 if digest[4] & 1 else 1.0
            length = math.sqrt(sum(value * value for value in vector)) or 1.0
            result.append([value / length for value in vector])
        return result


@dataclass
class LocalEmbedder(HashEmbedder):
    """Dependency-free local embedder; OpenAI embeddings remain optional."""

    dimensions: int = 384
    provider: str = "local"
    model: str = "local-token-hash-v1"


class OpenAIEmbedder:
    provider = "openai"

    def __init__(self, *, model: str, dimensions: int, api_key: str, base_url: str):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")
        from langchain_openai import OpenAIEmbeddings

        self.model = model
        self.dimensions = dimensions
        self._client = OpenAIEmbeddings(
            model=model, dimensions=dimensions, api_key=api_key, base_url=base_url
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)


def build_embedder(settings: Any) -> Embedder:
    if settings.embedding_backend == "hash":
        return HashEmbedder(dimensions=settings.embedding_dimensions)
    if settings.embedding_backend == "openai":
        return OpenAIEmbedder(
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            api_key=settings.embedding_api_key or "",
            base_url=settings.embedding_base_url,
        )
    return LocalEmbedder(dimensions=settings.embedding_dimensions)


class ChromaRestCollection:
    def __init__(self, client: "ChromaRestClient", collection: dict[str, Any]):
        self.client = client
        self.id = str(collection["id"])
        self.name = str(collection["name"])

    def upsert(self, **payload: Any) -> None:
        self.client._request("POST", f"{self.client.collection_path}/{self.id}/upsert", payload)

    def query(self, **payload: Any) -> dict[str, Any]:
        translated = {
            "query_embeddings": payload["query_embeddings"],
            "n_results": payload.get("n_results", 10),
            "where": payload.get("where"),
            "include": payload.get("include", ["distances"]),
        }
        return self.client._request(
            "POST", f"{self.client.collection_path}/{self.id}/query", translated
        )


class ChromaRestClient:
    """Small HTTP client for the four Chroma operations PriceLens needs."""

    def __init__(self, *, host: str, port: int, tenant: str, database: str):
        self.base_url = f"http://{host}:{port}/api/v2"
        self.tenant = tenant
        self.database = database
        self.database_path = (
            f"{self.base_url}/tenants/{quote(tenant, safe='')}/databases/{quote(database, safe='')}"
        )
        self.collection_path = f"{self.database_path}/collections"
        self._ensure_database()

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
        response = requests.request(method, url, json=payload, timeout=(5, 30))
        response.raise_for_status()
        return response.json() if response.content else {}

    def _ensure_database(self) -> None:
        response = requests.get(self.database_path, timeout=(5, 15))
        if response.status_code == 404:
            create_url = f"{self.base_url}/tenants/{quote(self.tenant, safe='')}/databases"
            created = requests.post(create_url, json={"name": self.database}, timeout=(5, 15))
            if created.status_code not in {200, 409}:
                created.raise_for_status()
        else:
            response.raise_for_status()

    def get_or_create_collection(
        self, *, name: str, metadata: dict[str, Any] | None = None
    ) -> ChromaRestCollection:
        collection = self._request(
            "POST",
            self.collection_path,
            {"name": name, "metadata": metadata, "get_or_create": True},
        )
        return ChromaRestCollection(self, collection)

    def get_collection(self, name: str) -> ChromaRestCollection:
        collection = self._request(
            "GET", f"{self.collection_path}/{quote(name, safe='')}"
        )
        return ChromaRestCollection(self, collection)


def chroma_client(settings: Any) -> ChromaRestClient:
    return ChromaRestClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        tenant=settings.chroma_tenant,
        database=settings.chroma_database,
    )


def build_policy_index(database: Any, settings: Any, embedder: Embedder | None = None) -> dict[str, Any]:
    embedder = embedder or build_embedder(settings)
    chunks = database.active_policy_chunks(include_parents=False)
    if not chunks:
        raise ValueError("No approved active policy chunks are available to index")
    corpus_hash = hashlib.sha256(
        "".join(sorted(str(row["content_sha256"]) for row in chunks)).encode("utf-8")
    ).hexdigest()
    collection_name = f"{settings.policy_collection_prefix}_{corpus_hash[:12]}"
    collection = chroma_client(settings).get_or_create_collection(
        name=collection_name, metadata={"hnsw:space": "cosine", "corpus_hash": corpus_hash}
    )
    batch_size = 100
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        collection.upsert(
            ids=[str(row["chunk_id"]) for row in batch],
            documents=[str(row["content"]) for row in batch],
            embeddings=embedder.embed([str(row["content"]) for row in batch]),
            metadatas=[
                {
                    "retailer": str(row["retailer"]),
                    "policy_type": str(row["policy_type"]),
                    "country_code": str(row["country_code"]),
                    "product_category": str(row.get("product_category") or "ALL"),
                }
                for row in batch
            ],
        )
    document_count = len({str(row["document_version_id"]) for row in chunks})
    index_id = database.activate_manifest(
        {
            "corpus_type": "POLICY",
            "collection_name": collection_name,
            "embedding_provider": embedder.provider,
            "embedding_model": embedder.model,
            "embedding_dimensions": embedder.dimensions,
            "document_count": document_count,
            "chunk_count": len(chunks),
            "corpus_hash": corpus_hash,
        }
    )
    return {"index_id": index_id, "collection_name": collection_name, "chunk_count": len(chunks)}


class HybridPolicyRetriever:
    def __init__(self, database: Any, settings: Any, *, embedder: Embedder | None = None, client: Any = None):
        self.database = database
        self.settings = settings
        self.embedder = embedder
        self.client = client

    def search(
        self,
        question: str,
        *,
        retailers: list[str],
        policy_types: list[str] | None = None,
        product_category: str | None = "electronics",
        limit: int = 8,
    ) -> list[PolicyHit]:
        lexical = self.database.keyword_policy_search(
            question,
            retailers=retailers,
            policy_types=policy_types or [],
            product_category=product_category,
            limit=max(limit * 2, 12),
        )
        semantic_ids: list[str] = []
        manifest = self.database.active_manifest("POLICY")
        if manifest:
            try:
                embedder = self.embedder or build_embedder(self.settings)
                client = self.client or chroma_client(self.settings)
                collection = client.get_collection(str(manifest["collection_name"]))
                where: dict[str, Any] | None = None
                if len(retailers) == 1:
                    where = {"retailer": retailers[0]}
                response = collection.query(
                    query_embeddings=embedder.embed([question]),
                    n_results=max(limit * 2, 12),
                    where=where,
                    include=["distances"],
                )
                semantic_ids = [str(value) for value in (response.get("ids") or [[]])[0]]
            except Exception:
                semantic_ids = []
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
            if retailers and row.get("retailer") not in retailers:
                continue
            if policy_types and row.get("policy_type") not in policy_types:
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
                )
            )
        return hits
