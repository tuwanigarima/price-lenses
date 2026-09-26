#!/usr/bin/env python3
"""Register the curated India retailer policy source list in local PostgreSQL."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eligibility_config import EligibilitySettings  # noqa: E402
from tools.eligibility_db import EligibilityDatabase  # noqa: E402
from tools.policy_corpus import validate_policy_url  # noqa: E402


def main() -> None:
    settings = EligibilitySettings.from_env()
    sources = json.loads((ROOT / "data/policies/sources.json").read_text())
    with EligibilityDatabase(settings.database_url) as database:
        for source in sources:
            validate_policy_url(source["source_url"], source["retailer"])
            source_id = database.register_policy_source(source)
            print(f"Registered {source['retailer']} {source['policy_type']}: {source_id}")


if __name__ == "__main__":
    main()
