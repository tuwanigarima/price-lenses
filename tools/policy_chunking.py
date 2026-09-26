"""Structure-aware chunking for retailer FAQ and policy documents."""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PolicyDocument:
    document_version_id: str
    retailer: str
    policy_type: str
    title: str
    content: str
    country_code: str = "IN"
    product_category: str | None = None
    seller_scope: str | None = None
    condition_scope: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyChunk:
    chunk_id: str
    document_version_id: str
    parent_chunk_id: str | None
    chunk_index: int
    heading_path: str
    chunk_type: str
    content: str
    token_count: int
    content_sha256: str
    retailer: str
    policy_type: str
    country_code: str
    product_category: str | None
    seller_scope: str | None
    condition_scope: str | None
    metadata: dict[str, Any]


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FAQ_QUESTION = re.compile(r"^(?:q(?:uestion)?\s*[:.-]|\d+[.)]\s*)?(.+\?)\s*$", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def approximate_tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _sections(markdown: str) -> list[tuple[str, str]]:
    hierarchy: list[str] = []
    current_path = ""
    buffer: list[str] = []
    result: list[tuple[str, str]] = []
    for raw in markdown.splitlines():
        match = HEADING.match(raw.strip())
        if match:
            if any(line.strip() for line in buffer):
                result.append((current_path, "\n".join(buffer).strip()))
            level, title = len(match.group(1)), match.group(2).strip()
            hierarchy = hierarchy[: level - 1]
            hierarchy.append(title)
            current_path = " > ".join(hierarchy)
            buffer = []
        else:
            buffer.append(raw)
    if any(line.strip() for line in buffer):
        result.append((current_path, "\n".join(buffer).strip()))
    return result or [("", markdown.strip())]


def _faq_units(text: str) -> list[str] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    question_indexes = [index for index, line in enumerate(lines) if FAQ_QUESTION.match(line)]
    if not question_indexes:
        return None
    units: list[str] = []
    for position, start in enumerate(question_indexes):
        end = question_indexes[position + 1] if position + 1 < len(question_indexes) else len(lines)
        unit = "\n".join(lines[start:end]).strip()
        if unit:
            units.append(unit)
    return units


def _split_text(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    if approximate_tokens(text) <= max_tokens:
        return [text.strip()]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) == 1:
        paragraphs = [part.strip() for part in SENTENCE_END.split(text) if part.strip()]
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for paragraph in paragraphs:
        tokens = approximate_tokens(paragraph)
        if current and current_tokens + tokens > max_tokens:
            pieces.append("\n\n".join(current))
            overlap: list[str] = []
            overlap_size = 0
            for prior in reversed(current):
                prior_tokens = approximate_tokens(prior)
                if overlap_size + prior_tokens > overlap_tokens:
                    break
                overlap.insert(0, prior)
                overlap_size += prior_tokens
            current = overlap
            current_tokens = overlap_size
        current.append(paragraph)
        current_tokens += tokens
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def chunk_document(
    document: PolicyDocument,
    *,
    max_tokens: int = 450,
    overlap_tokens: int = 60,
) -> list[PolicyChunk]:
    if max_tokens < 100:
        raise ValueError("max_tokens must be at least 100")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be between 0 and max_tokens")
    chunks: list[PolicyChunk] = []
    for heading_path, section_text in _sections(document.content):
        faq_units = _faq_units(section_text) if document.policy_type.upper() == "FAQ" else None
        units = faq_units or _split_text(section_text, max_tokens, overlap_tokens)
        chunk_type = "FAQ" if faq_units else ("TABLE" if "|" in section_text else "POLICY_CLAUSE")
        parent_id = None
        if len(units) > 1:
            parent_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{document.document_version_id}:parent:{heading_path}",
                )
            )
            parent_content = (
                f"Retailer: {document.retailer}\n"
                f"Country: {document.country_code}\n"
                f"Policy: {document.policy_type}\n"
                f"Category: {document.product_category or 'ALL'}\n"
                f"Section: {heading_path or document.title}\n\n{section_text.strip()}"
            )
            chunks.append(
                PolicyChunk(
                    chunk_id=parent_id,
                    document_version_id=document.document_version_id,
                    parent_chunk_id=None,
                    chunk_index=len(chunks),
                    heading_path=heading_path,
                    chunk_type="PARENT",
                    content=parent_content,
                    token_count=approximate_tokens(parent_content),
                    content_sha256=hashlib.sha256(
                        parent_content.encode("utf-8")
                    ).hexdigest(),
                    retailer=document.retailer,
                    policy_type=document.policy_type,
                    country_code=document.country_code,
                    product_category=document.product_category,
                    seller_scope=document.seller_scope,
                    condition_scope=document.condition_scope,
                    metadata={**document.metadata, "retrieval_only": False},
                )
            )
        for unit in units:
            index = len(chunks)
            context = (
                f"Retailer: {document.retailer}\n"
                f"Country: {document.country_code}\n"
                f"Policy: {document.policy_type}\n"
                f"Category: {document.product_category or 'ALL'}\n"
                f"Section: {heading_path or document.title}\n\n{unit.strip()}"
            )
            content_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
            chunk_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{document.document_version_id}:{index}:{content_hash}",
                )
            )
            chunks.append(
                PolicyChunk(
                    chunk_id=chunk_id,
                    document_version_id=document.document_version_id,
                    parent_chunk_id=parent_id,
                    chunk_index=index,
                    heading_path=heading_path,
                    chunk_type=chunk_type,
                    content=context,
                    token_count=approximate_tokens(context),
                    content_sha256=content_hash,
                    retailer=document.retailer,
                    policy_type=document.policy_type,
                    country_code=document.country_code,
                    product_category=document.product_category,
                    seller_scope=document.seller_scope,
                    condition_scope=document.condition_scope,
                    metadata=dict(document.metadata),
                )
            )
    return chunks
