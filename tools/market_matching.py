"""Decide which canonical product an Offer belongs to.

Priority:
  1. ASIN   -> "asin:B0XXXXXXXX"   (exact; Amazon's identifier, also present in Amazon URLs)
  2. GTIN   -> "gtin:0194253..."   (exact; UPC/EAN, shared by many retailers)
  3. Title  -> fuzzy match against products already known in this run; otherwise a new
               "title:<hash>" product is created.

NOTE: ASIN is Amazon's id. Flipkart, Walmart, eBay etc. do not use it, so cross-site
matching only works via ASIN when the site links to / reports an Amazon ASIN.
GTIN and fuzzy title matching cover the rest.
"""
from __future__ import annotations

import hashlib
from difflib import SequenceMatcher

from .market_models import Offer
from .market_normalize import normalize_title

TITLE_MATCH_THRESHOLD = 0.82

ACCESSORY_TERMS = {
    "back cover", "case", "flip cover", "screen guard", "screen protector",
    "tempered glass", "camera lens", "lens guard", "protector", "skin",
    "charger", "charging cable", "usb cable", "adapter", "stand", "holder",
    "replacement", "spare", "pouch", "sleeve", "scratch", "bubble", "guard",
}

_PRODUCT_SPEC_TERMS = {
    "ram", "storage", "battery", "mah", "camera", "display", "processor",
    "smartphone", "laptop", "television", "oled", "amoled", "ssd",
}

_STRICT_SUFFIXES = {
    "fe", "pro", "ultra", "plus", "max", "mini", "se", "lite", "classic", "air"
}


def _token_set(s: str) -> set[str]:
    return set(s.split())


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _token_set(na), _token_set(nb)
    jac = len(ta & tb) / len(ta | tb)
    # Model numbers and strict suffixes must agree: penalise if they contradict.
    nums_a = {t for t in ta if any(c.isdigit() for c in t) or t in _STRICT_SUFFIXES}
    nums_b = {t for t in tb if any(c.isdigit() for c in t) or t in _STRICT_SUFFIXES}
    if nums_a and nums_b and not (nums_a & nums_b):
        return 0.0
    return max(seq * 0.6 + jac * 0.4, 0.0)


def is_accessory_title(title: str | None) -> bool:
    normalized = normalize_title(title or "")
    return any(term in normalized for term in ACCESSORY_TERMS)


MAJOR_BRANDS = {
    "apple", "samsung", "google", "oneplus", "xiaomi", "redmi", "poco",
    "oppo", "vivo", "realme", "motorola", "moto", "iqoo", "nothing",
    "asus", "sony", "hp", "dell", "lenovo", "acer", "lg", "noise", "boat"
}

# Product-family / series keywords that strongly identify a product line.
# If the title contains one of these but the query does NOT, it is a different
# product — e.g. a "Note" result should never satisfy an "iPhone" query.
_FAMILY_KEYWORDS = {
    "note", "galaxy", "pixel", "reno", "nord", "edge", "razr",
    "zenfone", "xperia", "voyage", "narzo", "spark", "infinix",
    "iphone", "ipad", "macbook", "pulse", "colorfit",
}

# Brand synonyms: product sub-names that uniquely identify a brand even when
# the brand name itself is absent from the title.
BRAND_SYNONYMS: dict[str, set[str]] = {
    "apple":    {"iphone", "ipad", "macbook", "airpods", "imac", "ipod", "apple watch"},
    "samsung":  {"galaxy"},
    "google":   {"pixel"},
    "motorola": {"moto"},
}


def product_relevance(query: str, title: str | None) -> float:
    """Score whether a listing is the requested product rather than an accessory.

    Model tokens containing digits (``s2``, ``s24``, ``16``) and strict suffixes
    (``pro``, ``ultra``) are mandatory when the query supplies them.
    Accessory listings are rejected unless the query itself explicitly asks for an accessory.
    """
    normalized_query = normalize_title(query)
    normalized_title = normalize_title(title or "")
    if not normalized_query or not normalized_title:
        return 0.0
    query_accessory = is_accessory_title(normalized_query)
    if is_accessory_title(normalized_title) and not query_accessory:
        return 0.0

    query_tokens = set(normalized_query.split())
    title_tokens = set(normalized_title.split())

    # Shared numbers cannot substitute for a requested product family.
    # Preserve the upstream brand, synonym, family-conflict and suffix guards.
    if not (query_tokens & _FAMILY_KEYWORDS).issubset(title_tokens):
        return 0.0

    # 0a. Strict Brand Boundary: both sides declare a major brand → they must match.
    query_brands = query_tokens & MAJOR_BRANDS
    title_brands = title_tokens & MAJOR_BRANDS
    if query_brands and title_brands and not query_brands.intersection(title_brands):
        return 0.0

    # 0b. Query has a brand but title has NO recognizable brand → the query brand
    #     must appear literally in the raw (un-normalized) title, OR one of its
    #     known synonyms must appear (e.g. "iphone" counts as "apple").
    #     This catches titles like "Note 15 5G (8GB/128GB)" that carry no brand
    #     prefix but are clearly a different product family from "Apple iPhone 15".
    raw_title_lower = (title or "").lower()
    if query_brands and not title_brands:
        def _brand_present(brand: str) -> bool:
            if brand in raw_title_lower:
                return True
            return any(syn in raw_title_lower for syn in BRAND_SYNONYMS.get(brand, set()))
        if not any(_brand_present(brand) for brand in query_brands):
            return 0.0

    # 0c. Product-family conflict: if the title contains a family keyword (e.g.
    #     "note", "galaxy", "pixel") that the query does NOT mention, it belongs
    #     to a different product line.
    title_families = title_tokens & _FAMILY_KEYWORDS
    query_families = query_tokens & _FAMILY_KEYWORDS
    if title_families - query_families:
        return 0.0

    # 1. Mandatory inclusion: if query asks for S24 or Ultra, candidate MUST have it.
    model_tokens = {
        token for token in query_tokens
        if (any(character.isdigit() for character in token) or token in _STRICT_SUFFIXES)
        and not token.endswith(("gb", "tb", "mah"))
    }
    if model_tokens and not model_tokens.issubset(title_tokens):
        return 0.0

    # 2. Strict rejection: if candidate has an extra suffix (e.g., Ultra) that the query didn't ask for, reject it.
    candidate_suffixes = title_tokens & _STRICT_SUFFIXES
    query_suffixes = query_tokens & _STRICT_SUFFIXES
    if candidate_suffixes and not candidate_suffixes.issubset(query_suffixes):
        return 0.0

    overlap = len(query_tokens & title_tokens) / max(1, len(query_tokens))
    score = 0.65 * title_similarity(normalized_query, normalized_title) + 0.35 * overlap
    if model_tokens:
        score += 0.20
    if _PRODUCT_SPEC_TERMS & title_tokens:
        score += 0.10
    return min(1.0, score)


class ProductResolver:
    """Stateful resolver; feed it known products first (from DB), then resolve offers."""

    def __init__(self, known: dict[str, str] | None = None):
        # product_id -> normalized title
        self.known: dict[str, str] = dict(known or {})
        self.gtin_index: dict[str, str] = {}
        self.asin_index: dict[str, str] = {}

    def resolve(self, offer: Offer) -> str:
        if offer.asin:
            pid = f"asin:{offer.asin.upper()}"
            self._register(pid, offer)
            return pid
        if offer.gtin:
            pid = self.gtin_index.get(offer.gtin) or f"gtin:{offer.gtin}"
            self.gtin_index[offer.gtin] = pid
            self._register(pid, offer)
            return pid
        best_id, best = None, 0.0
        for pid, known_title in self.known.items():
            score = title_similarity(offer.title, known_title)
            if score > best:
                best_id, best = pid, score
        if best_id and best >= TITLE_MATCH_THRESHOLD:
            return best_id
        pid = "title:" + hashlib.sha1(normalize_title(offer.title).encode()).hexdigest()[:12]
        self._register(pid, offer)
        return pid

    def _register(self, pid: str, offer: Offer) -> None:
        self.known.setdefault(pid, offer.title)
