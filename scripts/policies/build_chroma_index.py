#!/usr/bin/env python3
"""Compatibility entry point; policy vectors are now built in Neon pgvector."""
from __future__ import annotations

import warnings

from build_pgvector_index import main


if __name__ == "__main__":
    warnings.warn(
        "build_chroma_index.py is deprecated; use build_pgvector_index.py",
        DeprecationWarning,
        stacklevel=1,
    )
    main()
