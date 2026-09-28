#!/usr/bin/env python3
"""Compatibility command: rebuild the curated local collection in Neon pgvector."""
from __future__ import annotations

import sys
from import_local_documents import main as import_main


def main() -> None:
    if "--publish" not in sys.argv:
        sys.argv.append("--publish")
    import_main()


if __name__ == "__main__":
    main()
