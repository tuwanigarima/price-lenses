"""PostgreSQL repository for Agent 3 and its policy corpus."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from .policy_chunking import PolicyChunk


class EligibilitySchemaError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _native(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def normalize_seller(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _vector_literal(values: Iterable[float], *, dimensions: int = 1536) -> str:
    vector = [float(value) for value in values]
    if len(vector) != dimensions:
        raise ValueError(
            f"embedding has {len(vector)} dimensions; expected {dimensions}"
        )
    return json.dumps(vector, separators=(",", ":"))


class EligibilityDatabase:
    def __init__(self, database_url: str, *, verify_schema: bool = True):
        if not database_url:
            raise ValueError("database_url is required")
        self.connection = psycopg2.connect(database_url, connect_timeout=10)
        if verify_schema:
            self._verify_schema()

    def __enter__(self) -> "EligibilityDatabase":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _verify_schema(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.eligibility_analysis_runs')")
            ready = cursor.fetchone()[0]
        self.connection.rollback()
        if ready is None:
            self.close()
            raise EligibilitySchemaError(
                "Agent 3 database tables are missing. Run: python scripts/db/migrate.py"
            )

    def _rows(self, statement: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(statement, params)
            rows = cursor.fetchall()
        self.connection.rollback()
        return [
            {key: _native(value) for key, value in row.items()}
            for row in rows
        ]

    # ------------------------------------------------------------ offer data
    def load_offers(
        self,
        *,
        offer_ids: Iterable[str] = (),
        run_ids: Iterable[str] = (),
        canonical_id: str | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in offer_ids if value))
        runs = list(dict.fromkeys(str(value) for value in run_ids if value))
        if not ids and not runs and not canonical_id:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if ids:
            clauses.append("o.offer_id = ANY(%s)")
            params.append(ids)
        if runs:
            clauses.append("o.run_id = ANY(%s)")
            params.append(runs)
        if canonical_id:
            clauses.append("o.canonical_id = %s")
            params.append(canonical_id)
        params.append(limit)
        return self._rows(
            f"""
            SELECT o.*, p.title AS product_title, p.brand, p.model, p.color,
                   p.storage, p.ram,
                   d.seller_id, d.price_with_offers, d.is_assured,
                   d.cod_available, d.no_cost_emi, d.return_policy,
                   d.delivery_by, d.warranty, d.item_condition
            FROM market_offers o
            JOIN products p ON p.canonical_id = o.canonical_id
            LEFT JOIN market_offer_details d ON d.offer_id = o.offer_id
            WHERE ({' OR '.join(clauses)})
            ORDER BY o.fetched_at DESC, o.price NULLS LAST
            LIMIT %s
            """,
            tuple(params),
        )

    def load_offers_for_query(self, query: str, *, limit: int = 250) -> list[dict[str, Any]]:
        """Load the newest completed snapshot per provider for an exact query."""
        return self._rows(
            """
            WITH ranked_runs AS (
                SELECT run_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY provider ORDER BY started_at DESC, run_id DESC
                       ) AS position
                FROM market_search_runs
                WHERE lower(query) = lower(%s) AND status IN ('success', 'partial')
            )
            SELECT o.*, p.title AS product_title, p.brand, p.model, p.color,
                   p.storage, p.ram,
                   d.seller_id, d.price_with_offers, d.is_assured,
                   d.cod_available, d.no_cost_emi, d.return_policy,
                   d.delivery_by, d.warranty, d.item_condition
            FROM market_offers o
            JOIN ranked_runs r ON r.run_id = o.run_id AND r.position = 1
            JOIN products p ON p.canonical_id = o.canonical_id
            LEFT JOIN market_offer_details d ON d.offer_id = o.offer_id
            ORDER BY o.fetched_at DESC, o.price NULLS LAST
            LIMIT %s
            """,
            (query, limit),
        )

    def promotions_for_offers(self, offer_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in offer_ids if value))
        if not ids:
            return []
        return self._rows(
            """
            SELECT promotion_id, offer_id, promotion_type, bank, card_type,
                   description, amount, percent, is_emi, source
            FROM market_offer_promotions
            WHERE offer_id = ANY(%s)
            ORDER BY offer_id, promotion_type
            """,
            (ids,),
        )

    def seller_authorizations(
        self, offers: Iterable[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        registry = self._rows(
            """
            SELECT s.id, s.brand, s.retailer, s.seller_name, s.is_authorized,
                   a.marketplace, a.normalized_alias,
                   EXISTS (
                       SELECT 1 FROM authorized_seller_evidence evidence
                       WHERE evidence.authorized_seller_id = s.id
                         AND (evidence.valid_until IS NULL OR evidence.valid_until >= CURRENT_TIMESTAMP)
                   ) AS has_current_evidence
            FROM authorized_sellers s
            LEFT JOIN authorized_seller_aliases a
              ON a.authorized_seller_id = s.id AND a.is_active
            """
        )
        result: dict[str, dict[str, Any]] = {}
        for offer in offers:
            seller = normalize_seller(offer.get("seller_name"))
            brand = str(offer.get("brand") or "").lower()
            marketplace = str(offer.get("marketplace") or "").lower()
            for record in registry:
                if str(record.get("brand") or "").lower() not in {"all", brand}:
                    continue
                retailer = str(record.get("retailer") or "").lower()
                if retailer and retailer not in marketplace:
                    continue
                registered = normalize_seller(
                    record.get("normalized_alias") or record.get("seller_name")
                )
                if seller and registered == seller and record.get("has_current_evidence"):
                    result[str(offer["offer_id"])] = record
                    break
        return result

    # ------------------------------------------------------------ policy data
    def sources_for_fetch(self, source_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
        identifiers = list(dict.fromkeys(str(value) for value in source_ids if value))
        where = "AND source_id::text = ANY(%s)" if identifiers else ""
        params = (identifiers,) if identifiers else ()
        return self._rows(
            f"""
            SELECT * FROM policy_sources
            WHERE enabled AND approved_for_ingestion {where}
            ORDER BY retailer, policy_type, source_url
            """,
            params,
        )

    def register_policy_source(self, source: dict[str, Any]) -> str:
        source_id = str(source.get("source_id") or uuid.uuid4())
        with self.connection:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO policy_sources (
                        source_id, retailer, policy_type, source_url,
                        canonical_url, country_code, source_format,
                        ingestion_mode, source_authority, discovery_status,
                        enabled, approved_for_ingestion, search_query,
                        discovered_by, review_notes
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    ON CONFLICT (source_url) DO UPDATE SET
                        retailer=EXCLUDED.retailer,
                        policy_type=EXCLUDED.policy_type,
                        canonical_url=EXCLUDED.canonical_url,
                        source_format=EXCLUDED.source_format,
                        source_authority=EXCLUDED.source_authority,
                        enabled=EXCLUDED.enabled,
                        approved_for_ingestion=EXCLUDED.approved_for_ingestion,
                        review_notes=EXCLUDED.review_notes
                    RETURNING source_id
                    """,
                    (
                        source_id,
                        source["retailer"],
                        source["policy_type"],
                        source["source_url"],
                        source.get("canonical_url"),
                        source.get("country_code", "IN"),
                        source.get("source_format", "HTML"),
                        source.get("ingestion_mode", "FETCH"),
                        source.get("source_authority", "AUTHORITATIVE"),
                        source.get("discovery_status", "VALIDATED"),
                        source.get("enabled", True),
                        source.get("approved_for_ingestion", True),
                        source.get("search_query"),
                        source.get("discovered_by", "curated_seed"),
                        source.get("review_notes"),
                    ),
                )
                return str(cursor.fetchone()[0])

    def record_fetch_status(
        self, source_id: str, status: str, *, http_status: int | None = None
    ) -> None:
        with self.connection:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE policy_sources
                    SET fetch_status=%s, last_http_status=%s, last_checked_at=%s
                    WHERE source_id=%s
                    """,
                    (status, http_status, utc_now(), source_id),
                )

    def store_document_version(
        self,
        *,
        source_id: str,
        title: str,
        content: str,
        parse_status: str = "PARSED",
        parser_version: str = "1.0",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document_id = str(uuid.uuid4())
        with self.connection:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT document_version_id FROM policy_document_versions
                    WHERE source_id=%s AND content_sha256=%s
                    """,
                    (source_id, content_hash),
                )
                existing = cursor.fetchone()
                if existing:
                    return str(existing[0]), False
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM policy_document_versions WHERE source_id=%s
                    """,
                    (source_id,),
                )
                version = cursor.fetchone()[0]
                cursor.execute(
                    """
                    UPDATE policy_document_versions SET is_active=FALSE
                    WHERE source_id=%s AND is_active
                    """,
                    (source_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO policy_document_versions (
                        document_version_id, source_id, version_number, title,
                        content_text, content_sha256, retrieved_at, is_active,
                        parse_status, parser_version, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,%s)
                    """,
                    (
                        document_id, source_id, version, title, content,
                        content_hash, utc_now(), parse_status, parser_version,
                        Json(metadata or {}),
                    ),
                )
        return document_id, True

    def store_policy_chunks(self, chunks: Iterable[PolicyChunk]) -> int:
        rows = list(chunks)
        if not rows:
            return 0
        document_ids = sorted({row.document_version_id for row in rows})
        with self.connection:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE policy_chunks SET is_active=FALSE WHERE document_version_id = ANY(%s::uuid[])",
                    (document_ids,),
                )
                for row in rows:
                    cursor.execute(
                        """
                        INSERT INTO policy_chunks (
                            chunk_id, document_version_id, parent_chunk_id,
                            chunk_index, heading_path, chunk_type, content,
                            token_count, content_sha256, retailer, policy_type,
                            country_code, product_category, seller_scope,
                            condition_scope, is_active, metadata
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s
                        )
                        ON CONFLICT (chunk_id) DO UPDATE SET
                            is_active=TRUE, metadata=EXCLUDED.metadata
                        """,
                        (
                            row.chunk_id, row.document_version_id, row.parent_chunk_id,
                            row.chunk_index, row.heading_path, row.chunk_type,
                            row.content, row.token_count, row.content_sha256,
                            row.retailer, row.policy_type, row.country_code,
                            row.product_category, row.seller_scope,
                            row.condition_scope, Json(row.metadata),
                        ),
                    )
        return len(rows)

    def active_policy_chunks(
        self,
        *,
        retailers: Iterable[str] = (),
        policy_types: Iterable[str] = (),
        product_category: str | None = None,
        include_parents: bool = False,
    ) -> list[dict[str, Any]]:
        retailer_values = list(dict.fromkeys(retailers))
        type_values = list(dict.fromkeys(policy_types))
        clauses = [
            "c.is_active", "d.is_active", "s.approved_for_ingestion",
            "c.token_count >= 50",
        ]
        params: list[Any] = []
        if retailer_values:
            clauses.append("c.retailer = ANY(%s)")
            params.append(retailer_values)
        if type_values:
            clauses.append("c.policy_type = ANY(%s)")
            params.append(type_values)
        if product_category:
            clauses.append("(c.product_category IS NULL OR c.product_category = %s)")
            params.append(product_category)
        if not include_parents:
            clauses.append("c.chunk_type <> 'PARENT'")
        return self._rows(
            f"""
            SELECT c.*, s.source_url, d.content_text AS document_content,
                   d.retrieved_at, d.effective_from, d.effective_until
            FROM policy_chunks c
            JOIN policy_document_versions d
              ON d.document_version_id = c.document_version_id
            JOIN policy_sources s ON s.source_id = d.source_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.retailer, c.policy_type, c.chunk_index
            """,
            tuple(params),
        )

    def policy_chunks_by_ids(self, chunk_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in chunk_ids if value))
        if not ids:
            return {}
        rows = self._rows(
            """
            SELECT c.*, s.source_url, d.content_text AS document_content,
                   d.is_active AS document_is_active, d.retrieved_at,
                   d.effective_from, d.effective_until
            FROM policy_chunks c
            JOIN policy_document_versions d
              ON d.document_version_id = c.document_version_id
            JOIN policy_sources s ON s.source_id = d.source_id
            WHERE c.chunk_id = ANY(%s::uuid[])
            """,
            (ids,),
        )
        for row in rows:
            row["is_active"] = bool(row["is_active"] and row["document_is_active"])
        return {str(row["chunk_id"]): row for row in rows}

    def keyword_policy_search(
        self,
        question: str,
        *,
        retailers: Iterable[str],
        policy_types: Iterable[str] = (),
        product_category: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        retailer_values = list(dict.fromkeys(retailers))
        type_values = list(dict.fromkeys(policy_types))
        clauses = [
            "c.is_active", "d.is_active", "s.approved_for_ingestion",
            "c.token_count >= 50",
            "c.search_vector @@ websearch_to_tsquery('english', %s)",
        ]
        # The rank expression occurs before the WHERE query placeholder.
        params: list[Any] = [question, question]
        if retailer_values:
            clauses.append("c.retailer = ANY(%s)")
            params.append(retailer_values)
        if type_values:
            clauses.append("c.policy_type = ANY(%s)")
            params.append(type_values)
        if product_category:
            clauses.append("(c.product_category IS NULL OR c.product_category=%s)")
            params.append(product_category)
        params.append(limit)
        return self._rows(
            f"""
            SELECT c.*, s.source_url,
                   ts_rank_cd(c.search_vector, websearch_to_tsquery('english', %s)) AS lexical_score
            FROM policy_chunks c
            JOIN policy_document_versions d
              ON d.document_version_id = c.document_version_id
            JOIN policy_sources s ON s.source_id = d.source_id
            WHERE {' AND '.join(clauses)}
            ORDER BY lexical_score DESC
            LIMIT %s
            """,
            tuple(params),
        )

    # ---------------------------------------------------------- vector data
    def policy_embedding_hashes(
        self, embedding_model: str, chunk_ids: Iterable[str]
    ) -> dict[str, str]:
        ids = list(dict.fromkeys(str(value) for value in chunk_ids if value))
        if not ids:
            return {}
        rows = self._rows(
            """
            SELECT chunk_id, embedded_content_sha256
            FROM policy_chunk_embeddings
            WHERE embedding_model=%s AND chunk_id = ANY(%s::uuid[])
            """,
            (embedding_model, ids),
        )
        return {
            str(row["chunk_id"]): str(row["embedded_content_sha256"])
            for row in rows
        }

    def upsert_policy_embeddings(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        dimensions: int = 1536,
    ) -> int:
        values = list(rows)
        if not values:
            return 0
        changed = 0
        with self.connection:
            with self.connection.cursor() as cursor:
                for row in values:
                    cursor.execute(
                        """
                        INSERT INTO policy_chunk_embeddings (
                            chunk_id, embedding_model, embedding_dimensions,
                            embedded_content_sha256, embedding
                        ) VALUES (%s,%s,%s,%s,%s::vector)
                        ON CONFLICT (chunk_id, embedding_model) DO UPDATE SET
                            embedding_dimensions=EXCLUDED.embedding_dimensions,
                            embedded_content_sha256=EXCLUDED.embedded_content_sha256,
                            embedding=EXCLUDED.embedding,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE policy_chunk_embeddings.embedded_content_sha256
                              IS DISTINCT FROM EXCLUDED.embedded_content_sha256
                           OR policy_chunk_embeddings.embedding_dimensions
                              IS DISTINCT FROM EXCLUDED.embedding_dimensions
                        """,
                        (
                            row["chunk_id"],
                            row["embedding_model"],
                            dimensions,
                            row["content_sha256"],
                            _vector_literal(row["embedding"], dimensions=dimensions),
                        ),
                    )
                    changed += cursor.rowcount
        return changed

    def semantic_policy_search(
        self,
        query_embedding: Iterable[float],
        *,
        embedding_model: str,
        retailers: Iterable[str],
        policy_types: Iterable[str] = (),
        product_category: str | None = None,
        limit: int = 12,
        dimensions: int = 1536,
    ) -> list[dict[str, Any]]:
        vector = _vector_literal(query_embedding, dimensions=dimensions)
        retailer_values = list(dict.fromkeys(retailers))
        type_values = list(dict.fromkeys(policy_types))
        clauses = [
            "c.is_active", "d.is_active", "s.approved_for_ingestion",
            "c.token_count >= 50", "e.embedding_model=%s",
        ]
        # The similarity expression occurs before the WHERE placeholders.
        params: list[Any] = [vector, embedding_model]
        if retailer_values:
            clauses.append("c.retailer = ANY(%s)")
            params.append(retailer_values)
        if type_values:
            clauses.append("c.policy_type = ANY(%s)")
            params.append(type_values)
        if product_category:
            clauses.append("(c.product_category IS NULL OR c.product_category=%s)")
            params.append(product_category)
        params.extend([vector, limit])
        return self._rows(
            f"""
            SELECT c.chunk_id,
                   1 - (e.embedding <=> %s::vector) AS semantic_score
            FROM policy_chunk_embeddings e
            JOIN policy_chunks c ON c.chunk_id = e.chunk_id
            JOIN policy_document_versions d
              ON d.document_version_id = c.document_version_id
            JOIN policy_sources s ON s.source_id = d.source_id
            WHERE {' AND '.join(clauses)}
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
            """,
            tuple(params),
        )

    def policy_embedding_count(self, embedding_model: str) -> int:
        rows = self._rows(
            """
            SELECT COUNT(*) AS count
            FROM policy_chunk_embeddings e
            JOIN policy_chunks c ON c.chunk_id=e.chunk_id
            JOIN policy_document_versions d
              ON d.document_version_id=c.document_version_id
            JOIN policy_sources s ON s.source_id=d.source_id
            WHERE e.embedding_model=%s AND c.is_active AND d.is_active
              AND s.approved_for_ingestion
            """,
            (embedding_model,),
        )
        return int(rows[0]["count"]) if rows else 0

    # ------------------------------------------------------------- manifests
    def active_manifest(self, corpus_type: str = "POLICY") -> dict[str, Any] | None:
        rows = self._rows(
            """
            SELECT * FROM rag_index_manifests
            WHERE corpus_type=%s AND status='ACTIVE'
            ORDER BY activated_at DESC LIMIT 1
            """,
            (corpus_type,),
        )
        return rows[0] if rows else None

    def activate_manifest(self, manifest: dict[str, Any]) -> str:
        index_id = str(manifest.get("index_id") or uuid.uuid4())
        with self.connection:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE rag_index_manifests SET status='RETIRED' WHERE corpus_type=%s AND status='ACTIVE'",
                    (manifest["corpus_type"],),
                )
                cursor.execute(
                    """
                    INSERT INTO rag_index_manifests (
                        index_id, corpus_type, collection_name, embedding_provider,
                        embedding_model, embedding_dimensions, document_count,
                        chunk_count, corpus_hash, status, activated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s)
                    ON CONFLICT (collection_name) DO UPDATE SET
                        embedding_provider=EXCLUDED.embedding_provider,
                        embedding_model=EXCLUDED.embedding_model,
                        embedding_dimensions=EXCLUDED.embedding_dimensions,
                        document_count=EXCLUDED.document_count,
                        chunk_count=EXCLUDED.chunk_count,
                        corpus_hash=EXCLUDED.corpus_hash,
                        status='ACTIVE',
                        activated_at=EXCLUDED.activated_at
                    RETURNING index_id
                    """,
                    (
                        index_id, manifest["corpus_type"], manifest["collection_name"],
                        manifest["embedding_provider"], manifest["embedding_model"],
                        manifest["embedding_dimensions"], manifest["document_count"],
                        manifest["chunk_count"], manifest["corpus_hash"], utc_now(),
                    ),
                )
                index_id = str(cursor.fetchone()[0])
        return index_id

    # -------------------------------------------------------------- run data
    def start_analysis(self, query: str, canonical_id: str | None, context: dict[str, Any]) -> str:
        analysis_id = str(uuid.uuid4())
        with self.connection:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO eligibility_analysis_runs (
                        analysis_id, query, canonical_id, request_context,
                        status, started_at
                    ) VALUES (%s,%s,%s,%s,'running',%s)
                    """,
                    (analysis_id, query, canonical_id, Json(context), utc_now()),
                )
                for run_id in context.get("market_run_ids", []):
                    cursor.execute(
                        """
                        INSERT INTO eligibility_market_runs (analysis_id, run_id)
                        VALUES (%s,%s) ON CONFLICT DO NOTHING
                        """,
                        (analysis_id, run_id),
                    )
        return analysis_id

    def finish_analysis(
        self,
        analysis_id: str,
        *,
        status: str,
        warnings: list[str],
        error: str | None = None,
    ) -> None:
        with self.connection:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE eligibility_analysis_runs
                    SET status=%s, warnings=%s, error=%s, finished_at=%s
                    WHERE analysis_id=%s
                    """,
                    (status, Json(warnings), error, utc_now(), analysis_id),
                )

    def store_assessments(
        self, analysis_id: str, assessments: Iterable[dict[str, Any]]
    ) -> dict[str, str]:
        rows = list(assessments)
        assessment_ids: dict[str, str] = {}
        with self.connection:
            with self.connection.cursor() as cursor:
                for item in rows:
                    assessment_id = str(uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT INTO offer_safety_assessments (
                            assessment_id, analysis_id, offer_id, variant_key,
                            seller_status, warranty_status, return_status,
                            delivery_status, condition_status, promotion_eligibility,
                            price_score, seller_score, warranty_score, delivery_score,
                            deal_score, evidence_confidence, safety_status,
                            within_budget, amount_over_budget, risk_flags, score_breakdown
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        ON CONFLICT (analysis_id, offer_id) DO NOTHING
                        """,
                        (
                            assessment_id, analysis_id, item["offer_id"], item["variant_key"],
                            item["seller_status"], item["warranty_status"], item["return_status"],
                            item["delivery_status"], item["condition_status"],
                            item["promotion_eligibility"], item.get("price_score"),
                            item.get("seller_score"), item.get("warranty_score"),
                            item.get("delivery_score"), item.get("deal_score"),
                            item.get("evidence_confidence"), item["safety_status"],
                            item.get("within_budget"), item.get("amount_over_budget"),
                            Json(item.get("risk_flags") or []),
                            Json(item.get("score_breakdown") or {}),
                        ),
                    )
                    assessment_ids[str(item["offer_id"])] = assessment_id
                    for chunk_id in item.get("evidence_chunk_ids") or []:
                        cursor.execute(
                            """
                            INSERT INTO assessment_policy_evidence (
                                assessment_id, chunk_id, claim_type, claim_status,
                                relevance_score, evidence_excerpt
                            ) VALUES (%s,%s,%s,%s,%s,%s)
                            ON CONFLICT DO NOTHING
                            """,
                            (
                                assessment_id, chunk_id, "POLICY_CONTEXT", "EVIDENCED",
                                item.get("policy_relevance"),
                                str(item.get("policy_excerpt") or "")[:1000],
                            ),
                        )
        return assessment_ids
