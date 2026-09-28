"""Typed, JSON-serialisable contracts for Agent 3."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SUPPORTED_POLICY_RETAILERS: tuple[str, ...] = (
    "Amazon India",
    "Flipkart",
    "Croma",
    "Reliance Digital",
    "Vijay Sales",
)

SUPPORTED_POLICY_TYPES: tuple[str, ...] = (
    "RETURN",
    "REPLACEMENT",
    "REFUND",
    "CANCELLATION",
    "WARRANTY",
)


@dataclass(frozen=True)
class PolicyAgentRequest:
    query: str
    canonical_id: str | None = None
    product_category: str = "electronics"
    country_code: str = "IN"
    retailers: tuple[str, ...] = SUPPORTED_POLICY_RETAILERS
    policy_types: tuple[str, ...] = SUPPORTED_POLICY_TYPES

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        if self.country_code != "IN":
            raise ValueError("Agent 3 supports only Indian policy evidence")
        if not self.retailers:
            raise ValueError("at least one retailer is required")


EligibilityAgentRequest = PolicyAgentRequest


@dataclass(frozen=True)
class PolicyHit:
    chunk_id: str
    document_version_id: str
    retailer: str
    policy_type: str
    content: str
    source_url: str
    heading_path: str | None = None
    product_category: str | None = None
    seller_scope: str | None = None
    condition_scope: str | None = None
    relevance: float = 0.0
    retrieval_sources: tuple[str, ...] = ()
    policy_types: tuple[str, ...] = ()
    evidence_text: str | None = None
    parent_context: str | None = None
    captured_at: str | None = None
    corpus_build_id: str | None = None
    local_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyProtectionReport:
    schema_version: str = "2.0"
    agent: str = "policy_purchase_protection_analyst"
    status: Literal["complete", "partial", "insufficient_evidence", "error"] = (
        "insufficient_evidence"
    )
    analysis_id: str = ""
    canonical_id: str | None = None
    analysis_timestamp: str = ""
    product_category: str = "electronics"
    country_code: str = "IN"
    corpus_build_id: str | None = None
    source_mode: str = "stored_policy_corpus"
    retailers_evaluated: list[str] = field(default_factory=list)
    policy_profiles: list[dict[str, Any]] = field(default_factory=list)
    evidence_chunk_count: int = 0
    evidence_gap_count: int = 0
    cross_retailer_observations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""
    agent_trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EligibilityReport = PolicyProtectionReport


SellerStatus = Literal[
    "VERIFIED_AUTHORIZED",
    "FIRST_PARTY_RETAILER",
    "VERIFIED_UNAUTHORIZED",
    "UNVERIFIED",
    "NOT_APPLICABLE",
]

SafetyStatus = Literal[
    "SAFE_VERIFIED", "SAFE_WITH_WARNINGS", "UNVERIFIED", "DISQUALIFIED"
]
