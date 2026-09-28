#!/usr/bin/env python3
"""Validate local files; optionally embed and atomically publish the collection."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.local_policy_corpus import CORPUS_ROOT, DEFAULT_MANIFEST, prepare_collection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--corpus-root", type=Path, default=CORPUS_ROOT)
    parser.add_argument("--publish", action="store_true", help="Call the configured embedding API and atomically replace the active local index")
    parser.add_argument("--rebuild", action="store_true", help="Re-embed even when the same corpus/model is already active; requires --publish")
    args = parser.parse_args()
    if args.rebuild and not args.publish:
        parser.error("--rebuild requires --publish")
    prepared = prepare_collection(args.manifest, args.corpus_root)
    summary = {"documents_ready": len(prepared["documents"]),
               "chunks_ready": sum(len(d["chunks"]) for d in prepared["documents"]),
               "pending": prepared["pending"], "errors": prepared["errors"],
               "corpus_hash": prepared["corpus_hash"], "published": False}
    if not args.publish or prepared["errors"]:
        print(json.dumps(summary, indent=2))
        if prepared["errors"]:
            raise SystemExit(1)
        return

    import psycopg2
    from tools.eligibility_config import EligibilitySettings
    from tools.policy_retrieval import build_embedder
    from tools.local_policy_db import embed_collection, publish_collection

    settings = EligibilitySettings.from_env()
    connection = psycopg2.connect(settings.database_direct_url or settings.database_url, connect_timeout=10)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('policy_local.chunks')")
            if cursor.fetchone()[0] is None:
                raise SystemExit("Local corpus schema is missing. Run scripts/policies/init_local_schema.py first.")
            cursor.execute(
                "SELECT build_id FROM policy_local.builds WHERE active AND corpus_hash=%s AND embedding_model=%s AND chunker_version=%s",
                (prepared["corpus_hash"], settings.embedding_model, prepared["chunker_version"]),
            )
            existing = cursor.fetchone()
        connection.rollback()
        if existing and not args.rebuild:
            summary.update(build_id=str(existing[0]), reused_existing_build=True)
            print(json.dumps(summary, indent=2))
            return
        embedder = build_embedder(settings)
        # Embedding failures occur before publication; the active build survives.
        vectors = embed_collection(prepared, embedder)
        summary["build_id"] = publish_collection(connection, prepared, vectors, embedder.model)
        summary["published"] = True
    finally:
        connection.close()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
