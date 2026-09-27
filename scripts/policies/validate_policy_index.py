#!/usr/bin/env python3
"""Run a read-only retrieval smoke test against the active policy index."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eligibility_config import EligibilitySettings  # noqa: E402
from tools.eligibility_db import EligibilityDatabase  # noqa: E402
from tools.policy_retrieval import HybridPolicyRetriever  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default="Can I return a defective mobile phone?")
    parser.add_argument("--retailer", action="append", default=["Amazon India"])
    args = parser.parse_args()
    settings = EligibilitySettings.from_env()
    with EligibilityDatabase(settings.database_url) as database:
        hits = HybridPolicyRetriever(database, settings).search(
            args.question, retailers=args.retailer
        )
        print(json.dumps([hit.to_dict() for hit in hits], indent=2))


if __name__ == "__main__":
    main()
