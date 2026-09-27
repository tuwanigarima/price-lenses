"""Index stored reviews into Chroma and write products.ai_reviews_summary.

    python scripts/reviews/build_review_index.py                     # index every product in data/reviews
    python scripts/reviews/build_review_index.py --asin B0CS5XW6TN --summarize
    python scripts/reviews/build_review_index.py --rebuild           # after changing POLICY_EMBEDDINGS
    python scripts/reviews/build_review_index.py --summarize --dry-run  # print summaries, write nothing

Reviews come from data/reviews/<ASIN>.jsonl (scripts/reviews/fetch_reviews.py).
Indexing only adds new reviews and removes deleted ones, so it can be re-run
after each fetch. --summarize writes the cited summary to the products table
(DATABASE_URL); without an LLM it writes the counts-only summary.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

from tools.eligibility_agent import infer_category  # noqa: E402
from tools.review_corpus import DEFAULT_REVIEW_DIR, load_reviews  # noqa: E402
from tools.review_defects import detect_defects  # noqa: E402
from tools.review_summary import ReviewSummarizer, stored_summary_text  # noqa: E402


def product_titles(asins: list[str]) -> dict[str, str]:
    from tools.analytics import get_db_connection

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT canonical_id, title FROM products WHERE canonical_id = ANY(%s)", (asins,))
            return dict(cursor.fetchall())
    finally:
        connection.close()


def store_summary(asin: str, text: str) -> bool:
    from tools.analytics import get_db_connection

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE products SET ai_reviews_summary = %s WHERE canonical_id = %s", (text, asin))
            updated = cursor.rowcount == 1
        connection.commit()
        return updated
    finally:
        connection.close()


def main(argv: list[str] | None = None, index=None, llm="auto") -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Index reviews in Chroma and summarise them.")
    parser.add_argument("--asin", action="append", help="Product ASIN (repeatable); default: all stored")
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--rebuild", action="store_true", help="Drop the collection and index from scratch")
    parser.add_argument("--summarize", action="store_true", help="Write products.ai_reviews_summary")
    parser.add_argument("--dry-run", action="store_true", help="With --summarize: print, do not write")
    args = parser.parse_args(argv)

    asins = args.asin or sorted(path.stem for path in Path(args.reviews).glob("*.jsonl"))
    if not asins:
        print(f"No stored reviews in {args.reviews}. Run scripts/reviews/fetch_reviews.py first.")
        return 1
    if index is None:
        from tools.review_index import default_review_index

        index = default_review_index()
    if args.rebuild:
        index.reset()
        print("Dropped the raw_user_reviews collection.")

    titles: dict[str, str] = {}
    if args.summarize:
        try:
            titles = product_titles(asins)
        except Exception as exc:  # titles only improve the category and the prompt
            print(f"[warn   ] product titles unavailable: {exc}")
    summarizer = ReviewSummarizer(index, llm=llm) if args.summarize else None

    failures = 0
    for asin in asins:
        reviews = load_reviews(asin, args.reviews)
        if not reviews:
            print(f"[skip   ] {asin}: no stored reviews.")
            continue
        try:
            synced = index.sync_product(asin, reviews)
        except Exception as exc:
            failures += 1
            print(f"[failed ] {asin}: {exc}")
            continue
        print(f"[indexed] {asin}: {synced['passages']} passages ({synced['added']} added, "
              f"{synced['updated']} updated, {synced['removed']} removed)")
        if summarizer is None:
            continue
        title = titles.get(asin)
        defects = detect_defects(reviews, infer_category(title))
        summary = summarizer.summarize(asin, reviews, defects, title).to_dict()
        text = stored_summary_text(summary)
        print(f"          summary ({summary['mode']}){': ' + summary['note'] if summary['note'] else ''}")
        if args.dry_run:
            print("          " + text.replace("\n", "\n          "))
            continue
        try:
            if store_summary(asin, text):
                print("          saved to products.ai_reviews_summary")
            else:
                print(f"[warn   ] {asin} is not in the products table; summary not saved.")
        except Exception as exc:
            failures += 1
            print(f"[failed ] {asin}: could not save summary: {exc}")
    print(f"Review index: {index.count()} passages in total.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
