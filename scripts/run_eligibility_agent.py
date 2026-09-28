#!/usr/bin/env python3
"""Run Agent 3 independently against local retailer policy evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eligibility_agent import PolicyProtectionAgent  # noqa: E402
from tools.eligibility_config import EligibilitySettings  # noqa: E402
from tools.local_policy_db import LocalPolicyDatabase as EligibilityDatabase  # noqa: E402
from tools.eligibility_models import PolicyAgentRequest  # noqa: E402
from tools.policy_retrieval import HybridPolicyRetriever  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--canonical-id")
    parser.add_argument("--product-category", default="electronics")
    parser.add_argument("--retailer", action="append", default=[])
    args = parser.parse_args()
    settings = EligibilitySettings.from_env()
    with EligibilityDatabase(settings.database_url) as database:
        retriever = HybridPolicyRetriever(database, settings)
        report = PolicyProtectionAgent(
            database,
            retriever,
            freshness_minutes=settings.offer_freshness_minutes,
            enable_llm_summary=settings.llm_enabled,
            llm_settings=settings,
        ).analyze(
            PolicyAgentRequest(
                query=args.query,
                canonical_id=args.canonical_id,
                product_category=args.product_category,
                **({"retailers": tuple(args.retailer)} if args.retailer else {}),
            )
        )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
