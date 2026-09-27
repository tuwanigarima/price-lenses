import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import orchestrator
from tests.test_eligibility_agent import advisor_factory, offers
from tests.test_review_defects import TODAY, review
from tools.deal_health import deal_health_from_reports, deal_health_index, s_competitor, s_rating, tier
from tools.eligibility_agent import run_eligibility_analysis
from tools.policy_rag import HashEmbedder
from tools.review_corpus import save_reviews
from tools.review_defects import DEFECTS, detect_defects
from tools.review_index import COLLECTION_NAME, ReviewIndex, ReviewIndexError, review_passages
from tools.review_sentiment import review_sentiment
from tools.review_summary import ReviewSummarizer, extractive_summary, stored_summary_text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "reviews"))
import build_review_index  # noqa: E402

DEFECT = {defect.key: defect for defect in DEFECTS}


class FakeLLM:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    def invoke(self, messages):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.reply)


def reviews_with_heat():
    reviews = [review(f"Great camera and display, happy with it {n}.", n, rating=5) for n in range(8)]
    reviews += [review("It heats up badly while gaming.", 100 + n, rating=2) for n in range(4)]
    reviews += [review("Phone becomes warm like a stove when I play games.", 200, rating=2)]
    reviews += [review("No heating issue at all, battery is fine.", 300, rating=5)]
    return reviews


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_MIN_RELEVANCE", "0.05")
    return ReviewIndex(HashEmbedder(), tmp_path / "chroma")


# ------------------------------------------------------------ index
def test_long_reviews_are_split_into_passages():
    long = review("This phone is fine. " * 100, 1)
    passages = review_passages(long)
    assert len(passages) > 1 and all(len(p) <= 800 for p in passages)
    assert review_passages(review("Short.", 2)) == ["Short."]


def test_sync_adds_new_and_removes_deleted_reviews(index):
    reviews = reviews_with_heat()
    assert index.sync_product("B0TEST0001", reviews) == {"added": 14, "updated": 0, "removed": 0, "passages": 14}
    assert index.sync_product("B0TEST0001", reviews)["added"] == 0  # nothing new
    assert index.sync_product("B0TEST0001", reviews[:10]) == {"added": 0, "updated": 0, "removed": 4, "passages": 10}
    index.sync_product("B0OTHER001", reviews[:2])
    assert index.count("B0TEST0001") == 10 and index.count() == 12


def test_sync_updates_a_review_fetched_again_with_a_new_rating(index):
    index.sync_product("B0TEST0001", [review("Good phone overall.", 1, rating=5)])
    assert index.sync_product("B0TEST0001", [review("Good phone overall.", 1, rating=2)])["updated"] == 1
    assert index.search("B0TEST0001", "good phone", ratings=(1.0, 2.0))[0].rating == 2.0
    assert index.search("B0TEST0001", "good phone", ratings=(4.0, 5.0)) == []


def test_summary_takes_praise_and_complaints_from_star_ratings(index):
    reviews = [review("Great phone, very happy with it.", 1, rating=5),
               review("Great phone at first, then it stopped working.", 2, rating=1),
               review("Battery lasts two days.", 3, source="reddit")]
    index.sync_product("B0TEST0001", reviews)
    hits = ReviewSummarizer(index, llm=None).retrieve("B0TEST0001")
    assert [hit.review_id for hit in hits[:2]] == ["amazon:1", "amazon:2"]  # praise first, then complaints
    assert "reddit:3" in {hit.review_id for hit in hits}


def test_search_stays_within_one_product(index):
    index.sync_product("B0TEST0001", reviews_with_heat())
    index.sync_product("B0OTHER001", [review("Overheats and heats up constantly.", 9)])
    hits = index.search("B0TEST0001", "heats up while gaming", k=3)
    assert hits and all(hit.review_id.startswith("amazon:") for hit in hits)
    assert [hit.number for hit in hits] == list(range(1, len(hits) + 1))
    assert "heat" in hits[0].text.lower()
    assert index.search("B0NOTHERE1", "heat") == []


def test_related_mentions_skip_counted_and_negated_reviews(index):
    index.sync_product("B0TEST0001", reviews_with_heat())
    related = index.related_mentions("B0TEST0001", DEFECT["overheating"], k=5)
    snippets = [item["snippet"] for item in related]
    assert "Phone becomes warm like a stove when I play games." in snippets
    assert not any("heats up" in s or "heating" in s for s in snippets)


def test_index_refuses_a_different_embedder(tmp_path):
    ReviewIndex(HashEmbedder(), tmp_path / "chroma").sync_product("B0TEST0001", [review("ok", 1)])
    with pytest.raises(ReviewIndexError, match="--rebuild"):
        ReviewIndex(HashEmbedder(256), tmp_path / "chroma").count()


# ------------------------------------------------------------ sentiment
def test_sentiment_from_ratings_with_heat_penalty():
    reviews = reviews_with_heat()
    defects = detect_defects(reviews, "mobile phones", today=TODAY, min_mentions=3, min_share=0.01)
    sentiment = review_sentiment(reviews, defects)
    # 9 x 5 stars, 5 x 2 stars -> average 3.93 -> base 73.2, minus 20 for overheating
    assert (sentiment["base"], sentiment["penalty"], sentiment["s_sentiment"]) == (73.2, 20.0, 53.2)
    assert sentiment["penalty_reasons"] == ["Overheating"]
    assert sentiment["rated_reviews"] == 14


def test_sentiment_is_neutral_without_enough_ratings():
    sentiment = review_sentiment([review("Nice", 1, source="reddit")], {"findings": []})
    assert sentiment["s_sentiment"] == 50.0 and sentiment["penalty"] == 0
    assert "only 0 rated reviews" in sentiment["basis"]
    assert review_sentiment([], None)["basis"] == "neutral: no reviews collected"


def test_battery_penalty_applies_once():
    defects = {"findings": [{"defect": "battery_drain", "label": "Poor battery"},
                            {"defect": "overheating", "label": "Overheating"}]}
    reviews = [review("ok", n, rating=5) for n in range(5)]
    assert review_sentiment(reviews, defects)["s_sentiment"] == 80.0


# ------------------------------------------------------------ deal health
def test_deal_health_uses_plan_weights():
    result = deal_health_index({"history": 80, "competitor": 100, "rating": 90, "sentiment": 60})
    assert result["dhi"] == 0.40 * 80 + 0.25 * 100 + 0.20 * 90 + 0.15 * 60  # 84.0
    assert result["tier"] == "🔥 Steal Deal" and result["missing"] == []


def test_deal_health_rescales_missing_factors():
    result = deal_health_index({"history": 50, "competitor": None, "rating": None, "sentiment": 100})
    # weights 0.40 and 0.15 rescaled to 0.727 and 0.273
    assert result["dhi"] == pytest.approx(63.6, abs=0.05)
    assert result["missing"] == ["competitor", "rating"]
    assert deal_health_index({})["dhi"] is None


def test_competitor_and_rating_factors():
    offers_ = [{"effective_price": 60000, "purchasable": True},
               {"effective_price": 50000, "purchasable": False}]  # out of stock: ignored
    assert s_competitor(60000, offers_) == 100.0
    assert s_competitor(75000, offers_) == 80.0
    assert s_competitor(None, offers_) is None and s_competitor(60000, []) is None
    assert s_rating(4.5) == 90.0 and s_rating(None) is None
    assert [tier(score) for score in (80, 79.9, 45, 10)] == ["🔥 Steal Deal", "🟢 Good Deal", "🟡 Fair Deal", "🔴 Overpriced"]


def test_deal_health_from_agent_reports_and_graph_nodes():
    history = {"trend": {"s_history": 40.0, "current_price": 70000, "customer_rating": 4.0}}
    eligibility = {"offers": [{"effective_price": 70000, "purchasable": True}], "sentiment": {"s_sentiment": 53.2}}
    result = deal_health_from_reports(history, eligibility)
    assert result["dhi"] == pytest.approx(0.40 * 40 + 0.25 * 100 + 0.20 * 80 + 0.15 * 53.2, abs=0.05)
    draft = orchestrator.decision_synthesizer_node({"history_report": history, "eligibility_report": eligibility})
    final = orchestrator.verifier_gate_node(draft)
    assert final["final_verdict"]["dhi"] == result["dhi"]
    assert deal_health_from_reports({"trend": {"error": "db down"}}, {})["dhi"] is None


# ------------------------------------------------------------ summary
def test_llm_summary_is_grounded(index):
    reviews = reviews_with_heat()
    index.sync_product("B0TEST0001", reviews)
    llm = FakeLLM("Owners like the camera [1] but some say it heats up while gaming [2].")
    summary = ReviewSummarizer(index, llm=llm).summarize("B0TEST0001", reviews, None, "Galaxy S24")
    assert summary.mode == "llm" and llm.calls == 1
    assert len(summary.citations) >= 2 and summary.citations[0].number == 1
    stored = stored_summary_text(summary.to_dict())
    assert stored.startswith("Owners like") and "Sources: [1] https://x.test/" in stored


def test_ungrounded_or_failed_llm_falls_back_to_counts(index):
    reviews = reviews_with_heat()
    index.sync_product("B0TEST0001", reviews)
    uncited = ReviewSummarizer(index, llm=FakeLLM("Great phone overall.")).summarize("B0TEST0001", reviews)
    assert uncited.mode == "extractive" and "grounding check" in uncited.note
    assert uncited.summary.startswith("14 reviews collected (Amazon 14).")
    error = RuntimeError("Error code: 400 - model 'gemini' does not exist")
    failed = ReviewSummarizer(index, llm=FakeLLM(error=error)).summarize("B0TEST0001", reviews)
    assert failed.mode == "extractive" and "model 'gemini' does not exist" in failed.note
    assert failed.citations  # passages are still shown


def test_summary_without_index_or_reviews():
    reviews = reviews_with_heat()
    defects = detect_defects(reviews, "mobile phones", today=TODAY, min_mentions=3, min_share=0.01)
    text = extractive_summary(reviews, defects)
    assert "Average rating 3.9★ from 14 rated reviews: 9 rate it 4★ or more, 5 rate it 2★ or less." in text
    assert "Overheating: 4 of 14 reviews" in text
    assert ReviewSummarizer(None, llm=None).summarize("B0TEST0001", reviews).mode == "extractive"
    empty = ReviewSummarizer(None, llm=None).summarize("B0TEST0001", [])
    assert empty.mode == "none" and stored_summary_text(empty.to_dict()) is None


# ------------------------------------------------------------ agent + script
def test_agent_adds_sentiment_summary_and_related_mentions(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_MIN_RELEVANCE", "0")
    monkeypatch.setenv("REVIEW_MIN_RELEVANCE", "0.05")
    reviews = reviews_with_heat()
    report = run_eligibility_analysis(
        "B0TEST0001", "Samsung Galaxy S24 5G",
        offers_loader=offers, advisor_factory=advisor_factory(tmp_path), reviews_loader=lambda _id: reviews,
        review_index_factory=lambda: ReviewIndex(HashEmbedder(), tmp_path / "reviews"),
        summarizer_llm=None,
    )
    assert report["sentiment"]["penalty_reasons"] == ["Overheating"]
    assert report["review_summary"]["mode"] == "extractive" and report["review_summary"]["citations"]
    heat = next(f for f in report["defects"]["findings"] if f["defect"] == "overheating")
    assert any("stove" in item["snippet"] for item in heat["related_mentions"])
    assert any(line.startswith("Review index (hash-512): 14 passages") for line in report["agent_trace"])


def test_agent_works_without_the_review_index(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_MIN_RELEVANCE", "0")

    def broken():
        raise RuntimeError("chroma unavailable")

    report = run_eligibility_analysis(
        "B0TEST0001", "Samsung Galaxy S24 5G",
        offers_loader=offers, advisor_factory=advisor_factory(tmp_path),
        reviews_loader=lambda _id: reviews_with_heat(), review_index_factory=broken, summarizer_llm=None,
    )
    assert report["sentiment"]["s_sentiment"] == 53.2
    assert report["review_summary"]["note"] == "Review index unavailable; showing counts only."
    assert "Review index unavailable: chroma unavailable" in report["agent_trace"]


def test_build_script_indexes_and_prints_summaries(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_MIN_RELEVANCE", "0.05")
    monkeypatch.setattr(build_review_index, "product_titles", lambda asins: {"B0TEST0001": "Galaxy S24 phone"})
    saved = {}
    monkeypatch.setattr(build_review_index, "store_summary", lambda asin, text: saved.setdefault(asin, text) or True)
    save_reviews("B0TEST0001", reviews_with_heat(), tmp_path / "reviews")
    index = ReviewIndex(HashEmbedder(), tmp_path / "chroma")

    args = ["--reviews", str(tmp_path / "reviews"), "--summarize"]
    assert build_review_index.main(args + ["--dry-run"], index=index, llm=None) == 0
    out = capsys.readouterr().out
    assert "[indexed] B0TEST0001: 14 passages (14 added, 0 updated, 0 removed)" in out
    assert "summary (extractive)" in out and saved == {}

    assert build_review_index.main(args, index=index, llm=None) == 0
    assert saved["B0TEST0001"].startswith("14 reviews collected")
    assert build_review_index.main(["--reviews", str(tmp_path / "none")], index=index) == 1


def test_collection_name_matches_the_plan():
    assert COLLECTION_NAME == "raw_user_reviews"
