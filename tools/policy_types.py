"""Ground policy-topic coverage in the text of an official policy chunk."""
from __future__ import annotations

import re
from typing import Any


_POLICY_TYPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "RETURN": re.compile(r"\breturn(?:ed|ing|s)?\b|\brefund(?:ed|ing|s)?\b", re.I),
    "REPLACEMENT": re.compile(
        r"\breplac(?:e|ed|ement|ements|ing)\b|\bexchange(?:d|s)?\b", re.I
    ),
    "CANCELLATION": re.compile(r"\bcancell?(?:ation|ations|ed|ing|able)?\b", re.I),
    "WARRANTY": re.compile(r"\bwarrant(?:y|ies)\b|\bguarantee(?:d|s)?\b", re.I),
    "FAQ": re.compile(r"\bfaq(?:s)?\b|frequently asked questions", re.I),
}


def policy_keyword_query(policy_types: list[str] | tuple[str, ...]) -> str:
    """Return a broad PostgreSQL websearch query for requested policy topics."""
    terms: list[str] = []
    aliases = {
        "RETURN": ("return", "refund"),
        "REPLACEMENT": ("replacement", "replace", "exchange"),
        "CANCELLATION": ("cancellation", "cancel"),
        "WARRANTY": ("warranty", "guarantee"),
        "FAQ": ("FAQ", "questions"),
    }
    for policy_type in policy_types:
        terms.extend(aliases.get(str(policy_type).upper(), ()))
    return " OR ".join(dict.fromkeys(terms)) or "policy OR protection"


def chunk_supports_policy_type(chunk: dict[str, Any], policy_type: str) -> bool:
    """Check whether a chunk can ground a requested policy type.

    A source's declared type is authoritative. Combined retailer policy pages can
    also ground another type when that topic is explicitly present in the cited
    chunk itself. This avoids treating a whole return page as warranty evidence.
    """
    requested = str(policy_type or "").upper()
    if str(chunk.get("policy_type") or "").upper() == requested:
        return True
    pattern = _POLICY_TYPE_PATTERNS.get(requested)
    if pattern is None:
        return False
    text = " ".join(
        str(chunk.get(field) or "")
        for field in ("heading_path", "content")
    )
    if requested == "FAQ" and str(chunk.get("chunk_type") or "").upper() == "FAQ":
        return True
    return bool(pattern.search(text))
