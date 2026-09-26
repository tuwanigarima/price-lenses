#!/usr/bin/env python3
"""Build and activate a versioned local Chroma policy collection."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eligibility_config import EligibilitySettings  # noqa: E402
from tools.eligibility_db import EligibilityDatabase  # noqa: E402
from tools.policy_retrieval import build_policy_index  # noqa: E402


def main() -> None:
    settings = EligibilitySettings.from_env()
    with EligibilityDatabase(settings.database_url) as database:
        print(json.dumps(build_policy_index(database, settings), indent=2))


if __name__ == "__main__":
    main()
