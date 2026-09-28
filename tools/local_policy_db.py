"""Atomic publication and snapshot-scoped retrieval for the local policy corpus."""
from __future__ import annotations

import math
import uuid

from psycopg2.extras import Json

from .eligibility_db import EligibilityDatabase, _vector_literal
from .local_policy_corpus import category_scopes


def embed_collection(prepared: dict, embedder, batch_size: int = 64) -> list[list[float]]:
    if prepared["errors"]:
        raise ValueError("; ".join(prepared["errors"]))
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if embedder.dimensions != 1536:
        raise ValueError("Local policy schema requires 1536-dimensional embeddings")
    chunks = [c for doc in prepared["documents"] for c in doc["chunks"]]
    vectors = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        result = embedder.embed([c["content"] for c in batch])
        if len(result) != len(batch):
            raise ValueError("Embedding count does not match input count")
        for vector in result:
            if len(vector) != 1536 or not all(math.isfinite(float(x)) for x in vector):
                raise ValueError("Embedding contains invalid dimensions or non-finite values")
            if not any(float(x) != 0 for x in vector):
                raise ValueError("Zero embedding cannot support cosine search")
        vectors.extend(result)
    return vectors


def publish_collection(connection, prepared: dict, vectors: list, model: str) -> str:
    """No network embedding calls or partial commits inside publication."""
    chunks = [c for doc in prepared["documents"] for c in doc["chunks"]]
    if prepared["errors"] or not chunks or len(chunks) != len(vectors):
        raise ValueError("A complete validated collection and matching embeddings are required")
    # Validate before opening the transaction; callers cannot bypass these checks.
    literals = []
    for vector in vectors:
        if not all(math.isfinite(float(x)) for x in vector) or not any(vector):
            raise ValueError("Invalid embedding")
        literals.append(_vector_literal(vector))
    build_id = str(uuid.uuid4())
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('pricelens_local_policy_publish'))")
            cursor.execute(
                "INSERT INTO policy_local.builds (build_id,corpus_hash,embedding_model,chunker_version) VALUES (%s,%s,%s,%s)",
                (build_id, prepared["corpus_hash"], model, prepared["chunker_version"]),
            )
            offset = 0
            for doc in prepared["documents"]:
                document_id = str(uuid.uuid4())
                metadata = {k: v for k, v in doc.items() if k not in {"chunks", "content"}}
                cursor.execute(
                    "INSERT INTO policy_local.documents (document_id,build_id,source_key,publisher,corpus,source_url,content,metadata,effective_from,effective_until) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (document_id, build_id, doc["id"], doc["publisher"], doc["corpus"], doc["source_url"],
                     doc["content"], Json(metadata), doc["effective_from"], doc["effective_until"]),
                )
                for chunk in doc["chunks"]:
                    cursor.execute(
                        "INSERT INTO policy_local.chunks (chunk_id,document_id,content,content_sha256,heading_path,policy_types,product_category,metadata,embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                        (str(uuid.uuid4()), document_id, chunk["content"], chunk["content_sha256"],
                         chunk["heading_path"], chunk["policy_types"], chunk["product_category"],
                         Json({k: v for k, v in chunk.items() if k != "content"}), literals[offset]),
                    )
                    offset += 1
            cursor.execute("SELECT COUNT(*) FROM policy_local.chunks c JOIN policy_local.documents d ON c.document_id=d.document_id WHERE d.build_id=%s", (build_id,))
            if cursor.fetchone()[0] != len(chunks):
                raise ValueError("Published chunk count mismatch")
            cursor.execute("UPDATE policy_local.builds SET active=FALSE WHERE active")
            cursor.execute("UPDATE policy_local.builds SET active=TRUE WHERE build_id=%s", (build_id,))
    return build_id


class LocalPolicyDatabase(EligibilityDatabase):
    """Uses legacy analysis logging, but never retrieves legacy policy evidence."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.retrieval_warnings = []
        self.build_id = None
        self.embedding_model = None
        try:
            ready = self._rows("SELECT to_regclass('policy_local.builds') AS ready")
            builds = self._rows("SELECT build_id, embedding_model FROM policy_local.builds WHERE active") if ready[0]["ready"] else []
            if builds:
                self.build_id = str(builds[0]["build_id"])
                self.embedding_model = builds[0]["embedding_model"]
            else:
                self.retrieval_warnings.append("LOCAL_CORPUS_NOT_READY: no published local collection; legacy web-ingested evidence is excluded.")
        except Exception:
            self.close()
            raise

    def _search(self, question, *, retailers, policy_types=(), product_category=None,
                limit=12, vector=None, embedding_model=None):
        if self.build_id is None:
            return []
        if vector is not None and embedding_model != self.embedding_model:
            raise ValueError("Query embedding model does not match the published local corpus")
        clauses = ["d.build_id=%s", "d.corpus='retailer_policy'",
                   "(d.effective_from IS NULL OR d.effective_from<=CURRENT_DATE)",
                   "(d.effective_until IS NULL OR d.effective_until>=CURRENT_DATE)",
                   "c.product_category=ANY(%s)"]
        params = [self.build_id, category_scopes(product_category)]
        if retailers:
            clauses.append("d.publisher=ANY(%s)")
            params.append(list(retailers))
        if policy_types:
            clauses.append("c.policy_types && %s::text[]")
            params.append(list(policy_types))
        if vector is None:
            clauses.append("c.search_vector @@ websearch_to_tsquery('english', %s)")
            params.append(question)
            order = "ts_rank_cd(c.search_vector, websearch_to_tsquery('english', %s)) DESC"
            params.append(question)
        else:
            order = "c.embedding <=> %s::vector"
            params.append(_vector_literal(vector))
        params.append(limit)
        return self._rows(
            f"SELECT c.chunk_id FROM policy_local.chunks c JOIN policy_local.documents d ON d.document_id=c.document_id WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT %s",
            tuple(params),
        )

    def keyword_policy_search(self, question, **kwargs):
        return self._search(question, **kwargs)

    def semantic_policy_search(self, vector, *, dimensions=1536, **kwargs):
        if dimensions != 1536:
            raise ValueError("Expected 1536 embedding dimensions")
        return self._search(None, vector=vector, **kwargs)

    def policy_chunks_by_ids(self, chunk_ids):
        ids = list(chunk_ids)
        if not ids or not self.build_id:
            return {}
        rows = self._rows(
            """SELECT c.*, d.document_id AS document_version_id, d.publisher AS retailer,
                      d.source_url, d.content AS document_content, d.metadata AS document_metadata,
                      d.build_id, TRUE AS is_active
               FROM policy_local.chunks c JOIN policy_local.documents d ON d.document_id=c.document_id
               WHERE c.chunk_id=ANY(%s::uuid[]) AND d.build_id=%s AND d.corpus='retailer_policy'
                 AND (d.effective_from IS NULL OR d.effective_from<=CURRENT_DATE)
                 AND (d.effective_until IS NULL OR d.effective_until>=CURRENT_DATE)""",
            (ids, self.build_id),
        )
        result = {}
        for row in rows:
            metadata = row["metadata"]
            row["policy_type"] = "MULTI"
            row["evidence_text"] = metadata["excerpt"]
            row["parent_context"] = row["document_content"][metadata["parent_start"]:metadata["parent_end"]]
            result[str(row["chunk_id"])] = row
        return result
