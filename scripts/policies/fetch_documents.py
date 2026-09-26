#!/usr/bin/env python3
"""Fetch approved policy sources and store immutable documents and chunks."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eligibility_config import EligibilitySettings  # noqa: E402
from tools.eligibility_db import EligibilityDatabase  # noqa: E402
from tools.policy_chunking import PolicyDocument, chunk_document  # noqa: E402
from tools.policy_corpus import fetch_policy_document  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", action="append", default=[])
    args = parser.parse_args()
    settings = EligibilitySettings.from_env()
    with EligibilityDatabase(settings.database_url) as database:
        for source in database.sources_for_fetch(args.source_id):
            source_id = str(source["source_id"])
            try:
                fetched = fetch_policy_document(source["source_url"], source["retailer"])
                document_id, created = database.store_document_version(
                    source_id=source_id,
                    title=fetched.title,
                    content=fetched.content,
                    metadata={"source_url": fetched.url, "content_type": fetched.content_type},
                )
                chunks = chunk_document(
                    PolicyDocument(
                        document_version_id=document_id,
                        retailer=source["retailer"],
                        policy_type=source["policy_type"],
                        title=fetched.title,
                        content=fetched.content,
                        country_code=source["country_code"],
                        product_category="electronics",
                        metadata={"source_url": fetched.url},
                    )
                )
                # Rebuild deterministic chunks even when this immutable version
                # already exists; that repairs a prior interrupted chunking run.
                database.store_policy_chunks(chunks)
                database.record_fetch_status(source_id, "SUCCESS", http_status=fetched.http_status)
                print(f"{source['retailer']}: {'stored' if created else 'unchanged'}")
            except Exception as exc:
                database.record_fetch_status(source_id, "ERROR")
                print(f"{source['retailer']}: ERROR: {exc}")


if __name__ == "__main__":
    main()
