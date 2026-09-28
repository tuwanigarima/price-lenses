#!/usr/bin/env python3
"""Apply only the independent local-policy migration to an existing Neon DB."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eligibility_config import EligibilitySettings


def main() -> None:
    settings = EligibilitySettings.from_env()
    path = ROOT / "scripts/db/migrations/004_local_policy_corpus.sql"
    contents = path.read_bytes()
    checksum = hashlib.sha256(contents).hexdigest()
    connection = psycopg2.connect(settings.database_direct_url or settings.database_url, connect_timeout=10)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext('price_lenses_migrations'))")
                cursor.execute("SELECT to_regclass('public.schema_migrations'), to_regclass('public.eligibility_analysis_runs'), to_regtype('vector')")
                if not all(cursor.fetchone()):
                    raise RuntimeError("Base Agent 3 schema and pgvector must exist before initializing the local collection")
                cursor.execute("SELECT checksum FROM schema_migrations WHERE version=%s", (path.name,))
                existing = cursor.fetchone()
                if existing:
                    if existing[0] != checksum:
                        raise RuntimeError(f"Applied migration was modified: {path.name}")
                    print(f"Already applied: {path.name}")
                    return
                cursor.execute(contents.decode())
                cursor.execute("INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)", (path.name, checksum))
                print(f"Applied: {path.name}. Existing migration checksums and policy rows were not changed.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
