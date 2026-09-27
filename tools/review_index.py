"""Chroma ``raw_user_reviews`` collection: search reviews by meaning (Agent 3).

The defect scan (``review_defects``) counts reviews whose words match a fixed
pattern. This index finds reviews by meaning instead, for two uses:

* related mentions: reviews that describe a counted defect in other words
  ("warm like a stove" for overheating). They are listed next to the defect
  but never added to its count, so the counts stay traceable to exact words;
* retrieval for the review summary (``review_summary``).

Reviews are split into passages and stored with their product, source, date,
rating and link. ``sync_product`` adds new reviews and removes deleted ones, so
it is cheap to call on every analysis. As with the policy index, the embedding
backend is stored on the collection and a mismatch is refused.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .review_corpus import SOURCE_LABELS, Review
from .review_defects import Defect

COLLECTION_NAME = "raw_user_reviews"
DEFAULT_MIN_RELEVANCE = 0.35
MAX_PASSAGE_CHARS = 800

# What each defect sounds like when owners describe it in their own words.
DEFECT_QUERIES = {
    "display_lines": "green or pink line appeared across the screen display",
    "display_other": "screen flickers, has dead pixels or burn-in, display went black",
    "ghost_touch": "touch screen registers phantom touches or stops responding",
    "overheating": "device becomes very hot and heats up while charging, gaming or calling",
    "battery_drain": "battery drains quickly and does not last a day",
    "battery_swelling": "battery swelled up and the back panel is bulging",
    "charging": "phone does not charge or charges very slowly, charging port problem",
    "network": "network signal drops, calls disconnect, SIM or Wi-Fi not working",
    "camera": "camera photos are blurry, camera does not focus or shakes",
    "audio": "speaker crackles, no sound, microphone not working in calls",
    "one_side_audio": "one earbud stopped working, only one side plays sound",
    "bluetooth": "bluetooth keeps disconnecting from the phone",
    "software": "device lags, hangs, apps crash and it restarts by itself",
    "hinge_keyboard": "laptop hinge broke or keyboard keys stopped working",
    "dead_unit": "product was dead on arrival or stopped working and will not turn on",
    "not_genuine": "received a used, refurbished, fake or opened box unit instead of new",
}


class ReviewIndexError(RuntimeError):
    """Raised when the review index cannot be used with the configured embedder."""


@dataclass
class ReviewHit:
    number: int
    text: str
    relevance: float
    review_id: str
    source: str
    date: str | None
    rating: float | None
    url: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def review_passages(review: Review) -> list[str]:
    """One passage per review; long posts (Reddit, YouTube) are split on sentences."""
    text = re.sub(r"\s+", " ", review.full_text).strip()
    if len(text) <= MAX_PASSAGE_CHARS:
        return [text] if text else []
    passages, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) + 1 > MAX_PASSAGE_CHARS:
            passages.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
        while len(current) > MAX_PASSAGE_CHARS:  # one very long sentence
            passages.append(current[:MAX_PASSAGE_CHARS])
            current = current[MAX_PASSAGE_CHARS:]
    if current:
        passages.append(current)
    return passages


def _passage_id(product_id: str, review_id: str, index: int) -> str:
    return f"{product_id}|{review_id}#{index}"


class ReviewIndex:
    def __init__(self, embedder, persist_dir: Path | str | None = None):
        import chromadb

        from .policy_rag import DEFAULT_CHROMA_DIR

        self.embedder = embedder
        self.persist_dir = Path(persist_dir or os.getenv("POLICY_CHROMA_DIR") or DEFAULT_CHROMA_DIR)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.persist_dir))

    def _collection(self):
        collection = self._client.get_or_create_collection(
            COLLECTION_NAME,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={"embedder": self.embedder.name},
        )
        built_with = (collection.metadata or {}).get("embedder")
        if built_with != self.embedder.name:
            raise ReviewIndexError(
                f"The review index was built with '{built_with}' embeddings but "
                f"'{self.embedder.name}' is configured. Rebuild it with "
                "python scripts/reviews/build_review_index.py --rebuild, or change POLICY_EMBEDDINGS."
            )
        return collection

    def reset(self) -> None:
        if COLLECTION_NAME in {c.name for c in self._client.list_collections()}:
            self._client.delete_collection(COLLECTION_NAME)

    def sync_product(self, product_id: str, reviews: list[Review], batch_size: int = 64) -> dict:
        """Make the stored passages for ``product_id`` match ``reviews``."""
        collection = self._collection()
        wanted: dict[str, tuple[str, dict]] = {}
        for review in reviews:
            metadata = {"product_id": product_id, "review_id": review.review_id, "source": review.source}
            if review.date:
                metadata["date"] = review.date
            if review.rating is not None:
                metadata["rating"] = float(review.rating)
            if review.url:
                metadata["url"] = review.url
            for index, passage in enumerate(review_passages(review)):
                wanted[_passage_id(product_id, review.review_id, index)] = (passage, metadata)

        stored = collection.get(where={"product_id": product_id}, include=["documents", "metadatas"])
        existing = {
            passage_id: (text, metadata)
            for passage_id, text, metadata in zip(stored["ids"], stored["documents"], stored["metadatas"])
        }
        stale = sorted(set(existing) - set(wanted))
        if stale:
            collection.delete(ids=stale)
        # New passages, and passages of a review fetched again with a new text or rating.
        missing = [passage_id for passage_id in wanted if passage_id not in existing]
        changed = [passage_id for passage_id in wanted
                   if passage_id in existing and existing[passage_id] != wanted[passage_id]]
        pending = missing + changed
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            texts = [wanted[passage_id][0] for passage_id in batch]
            collection.upsert(
                ids=batch,
                documents=texts,
                embeddings=self.embedder.embed(texts),
                metadatas=[wanted[passage_id][1] for passage_id in batch],
            )
        return {"added": len(missing), "updated": len(changed), "removed": len(stale), "passages": len(wanted)}

    def count(self, product_id: str | None = None) -> int:
        collection = self._collection()
        if product_id is None:
            return collection.count()
        return len(collection.get(where={"product_id": product_id}, include=[])["ids"])

    def search(
        self,
        product_id: str,
        query: str,
        *,
        k: int = 5,
        min_relevance: float | None = None,
        ratings: tuple[float, float] | None = None,
    ) -> list[ReviewHit]:
        """The ``k`` most similar reviews of one product, one passage per review.

        ``ratings`` (low, high) keeps only marketplace reviews rated in that range.
        """
        query = (query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        if min_relevance is None:
            configured = os.getenv("REVIEW_MIN_RELEVANCE")
            min_relevance = float(configured) if configured else getattr(
                self.embedder, "default_min_relevance", DEFAULT_MIN_RELEVANCE
            )
        collection = self._collection()
        where = {"product_id": product_id}
        if ratings:
            where = {"$and": [where, {"rating": {"$gte": ratings[0]}}, {"rating": {"$lte": ratings[1]}}]}
        stored = len(collection.get(where=where, include=[])["ids"])
        if not stored:
            return []
        result = collection.query(
            query_embeddings=self.embedder.embed([query]),
            n_results=min(stored, k * 3),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[ReviewHit] = []
        seen: set[str] = set()
        for text, metadata, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            relevance = 1.0 - float(distance)
            if relevance < min_relevance or metadata["review_id"] in seen:
                continue
            seen.add(metadata["review_id"])
            hits.append(ReviewHit(
                number=len(hits) + 1,
                text=text,
                relevance=round(relevance, 4),
                review_id=metadata["review_id"],
                source=SOURCE_LABELS.get(metadata["source"], metadata["source"]),
                date=metadata.get("date"),
                rating=metadata.get("rating"),
                url=metadata.get("url"),
            ))
            if len(hits) == k:
                break
        return hits

    def related_mentions(self, product_id: str, defect: Defect, k: int = 3) -> list[dict]:
        """Reviews that describe ``defect`` without the words the scan counts.

        Passages that contain any of the defect's patterns are skipped: they
        are either already counted or were rejected as negated ("no heating").
        """
        query = DEFECT_QUERIES.get(defect.key, defect.label)
        related = []
        for hit in self.search(product_id, query, k=k * 3):
            lowered = hit.text.lower()
            if any(re.search(pattern, lowered) for pattern in defect.patterns):
                continue
            related.append({
                "source": hit.source, "date": hit.date, "rating": hit.rating, "url": hit.url,
                "relevance": hit.relevance, "snippet": _snippet(hit.text),
            })
            if len(related) == k:
                break
        return related


def _snippet(text: str, limit: int = 220) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def default_review_index() -> ReviewIndex:
    from .policy_rag import embedder_from_env

    return ReviewIndex(embedder_from_env())
