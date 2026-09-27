"""Review summary for a product: the ``products.ai_reviews_summary`` text (Agent 3).

Flow: the review index retrieves passages about a few fixed topics (what owners
like, complaints, battery, performance, camera/display, build and value) ->
the LLM summarises only those numbered passages, citing them as [n] -> the
citations are checked the same way as policy answers.

When there is no LLM, the LLM fails, its answer cites nothing or an unknown
passage, or the review index is unavailable, a plain summary is built from
the rating counts and the defect scan instead (``mode="extractive"``), so a
summary is always available once reviews are collected.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .policy_rag import default_llm, validate_citations
from .review_corpus import SOURCE_LABELS, Review
from .review_index import ReviewHit
from .review_defects import defect_summary

log = logging.getLogger(__name__)

# (query, star-rating range). Similarity alone does not tell praise from
# complaints, so those two topics use the star rating; the product topics
# also reach Reddit and YouTube comments, which have no rating.
TOPICS = (
    ("great product, very happy with it, worth buying", (4.0, 5.0)),
    ("problem, disappointed, stopped working, not worth the money", (1.0, 2.0)),
    ("battery life and charging", None),
    ("performance, speed, lag and heating", None),
    ("camera, display and sound quality", None),
    ("build quality, durability and value for money", None),
)
PASSAGES_PER_TOPIC = 2
MAX_PASSAGES = 10
# Looser than the related-mentions cut-off: these passages only need to be on
# topic, they are not presented as reports of a defect.
SUMMARY_MIN_RELEVANCE = 0.25

SYSTEM_PROMPT = (
    "You summarise customer reviews for PriceLens, an Indian electronics purchase advisor.\n"
    "Use ONLY the numbered review passages provided.\n"
    "Rules:\n"
    "1. Write 3-5 sentences: what owners like, then the most common complaints.\n"
    "2. Cite every statement with its passage number in square brackets, e.g. [2].\n"
    "3. Report complaints as what reviewers say, not as facts about every unit. "
    "Never add specifications or opinions that are not in the passages.\n"
    "4. Keep it under 120 words."
)


@dataclass
class ReviewSummary:
    summary: str
    mode: str  # "llm" | "extractive" | "none"
    citations: list[ReviewHit] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict:
        return {"summary": self.summary, "mode": self.mode,
                "citations": [hit.to_dict() for hit in self.citations], "note": self.note}


def extractive_summary(reviews: list[Review], defects: dict | None) -> str:
    """Counts only: how many reviews, the average rating and reported defects."""
    by_source: dict[str, int] = {}
    for review in reviews:
        label = SOURCE_LABELS.get(review.source, review.source)
        by_source[label] = by_source.get(label, 0) + 1
    sources = ", ".join(f"{name} {count}" for name, count in sorted(by_source.items()))
    parts = [f"{len(reviews)} reviews collected ({sources})."]
    ratings = [review.rating for review in reviews if review.rating is not None]
    if ratings:
        high = sum(1 for rating in ratings if rating >= 4)
        low = sum(1 for rating in ratings if rating <= 2)
        parts.append(
            f"Average rating {sum(ratings) / len(ratings):.1f}★ from {len(ratings)} rated reviews: "
            f"{high} rate it 4★ or more, {low} rate it 2★ or less."
        )
    findings = (defects or {}).get("findings") or []
    total = (defects or {}).get("reviews_analyzed") or len(reviews)
    if findings:
        parts.append("Most reported problems: " + "; ".join(
            defect_summary(finding, total) for finding in findings[:3]
        ) + ".")
    elif defects is not None:
        parts.append("No defect is reported by enough reviewers to count as a pattern.")
    return " ".join(parts)


def _format_passages(hits: list[ReviewHit]) -> str:
    blocks = []
    for hit in hits:
        meta = ", ".join(part for part in (
            hit.source, hit.date, f"{hit.rating:g} stars" if hit.rating else None,
        ) if part)
        blocks.append(f"[{hit.number}] ({meta}) {hit.text}")
    return "\n\n".join(blocks)


class ReviewSummarizer:
    def __init__(self, index=None, llm="auto"):
        self.index = index
        self._llm = llm
        self._llm_error: str | None = None

    def _get_llm(self):
        if self._llm == "auto":
            try:
                self._llm = default_llm()
            except Exception as exc:  # missing package or bad config
                log.warning("Review summary LLM unavailable: %s", exc)
                self._llm_error = _describe(exc)
                self._llm = None
        return self._llm

    def retrieve(self, product_id: str) -> list[ReviewHit]:
        threshold = min(
            SUMMARY_MIN_RELEVANCE,
            getattr(self.index.embedder, "default_min_relevance", SUMMARY_MIN_RELEVANCE),
        )
        hits: list[ReviewHit] = []
        seen: set[str] = set()
        for query, ratings in TOPICS:
            for hit in self.index.search(
                product_id, query, k=PASSAGES_PER_TOPIC, min_relevance=threshold, ratings=ratings
            ):
                if hit.review_id in seen:
                    continue
                seen.add(hit.review_id)
                hit.number = len(hits) + 1
                hits.append(hit)
                if len(hits) == MAX_PASSAGES:
                    return hits
        return hits

    def summarize(
        self,
        product_id: str,
        reviews: list[Review],
        defects: dict | None = None,
        product_title: str | None = None,
    ) -> ReviewSummary:
        if not reviews:
            return ReviewSummary(
                "No reviews collected for this product. Run scripts/reviews/fetch_reviews.py.", "none"
            )
        fallback = extractive_summary(reviews, defects)
        if self.index is None:
            return ReviewSummary(fallback, "extractive", note="Review index unavailable; showing counts only.")
        try:
            hits = self.retrieve(product_id)
        except Exception as exc:
            return ReviewSummary(fallback, "extractive", note=f"Review search failed: {_describe(exc)}")
        if not hits:
            return ReviewSummary(fallback, "extractive", note="No review passage matched the summary topics.")
        llm = self._get_llm()
        if llm is None:
            note = (f"LLM unavailable ({self._llm_error}); showing counts and passages."
                    if self._llm_error else "LLM not configured; showing counts and passages.")
            return ReviewSummary(fallback, "extractive", hits, note)
        product = f" for {product_title}" if product_title else ""
        try:
            response = llm.invoke([
                ("system", SYSTEM_PROMPT),
                ("user", f"Summarise these customer reviews{product}.\n\nReview passages:\n"
                         f"{_format_passages(hits)}"),
            ])
            content = getattr(response, "content", response) or ""
            if isinstance(content, list):  # some providers return content blocks
                content = " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part) for part in content
                )
            text = str(content).strip()
        except Exception as exc:
            self._llm = None  # don't wait on an unreachable LLM again this session
            self._llm_error = _describe(exc)
            return ReviewSummary(fallback, "extractive", hits,
                                 f"LLM unavailable ({self._llm_error}); showing counts and passages.")
        problem = validate_citations(text, hits)
        if problem:
            return ReviewSummary(fallback, "extractive", hits,
                                 f"LLM summary rejected by the grounding check: {problem}.")
        return ReviewSummary(text, "llm", hits)


def _describe(exc: Exception) -> str:
    """Error type plus the server's message, so the cause is visible in the app."""
    message = re.sub(r"\s+", " ", str(exc)).strip()
    if len(message) > 200:
        message = message[:200].rsplit(" ", 1)[0] + "…"
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def stored_summary_text(summary: dict) -> str | None:
    """The text written to ``products.ai_reviews_summary``: the summary plus its sources."""
    if not summary or summary.get("mode") == "none":
        return None
    text = summary["summary"]
    if summary.get("mode") == "llm" and summary.get("citations"):
        sources = "; ".join(
            f"[{hit['number']}] {hit['url'] or hit['source']}" for hit in summary["citations"]
        )
        text = f"{text}\n\nSources: {sources}"
    return text
