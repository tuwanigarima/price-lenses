from __future__ import annotations

import logging
from dataclasses import dataclass

from .db import Database
from .matching import ProductResolver
from .providers import Provider

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    provider: str
    count: int
    error: str | None = None
    warnings: list[str] | None = None


class PriceLens:
    def __init__(self, db: Database, providers: list[Provider]):
        self.db = db
        self.providers = providers

    def search(self, query: str, limit: int = 20) -> list[RunResult]:
        """Query every provider, match offers to canonical products, persist everything.

        One provider failing does not stop the others.
        """
        resolver = ProductResolver(self.db.known_products())
        results: list[RunResult] = []
        for provider in self.providers:
            run_id = self.db.start_run(query, provider.name)
            try:
                offers = provider.search(query, limit)
                # Resolve ASIN-bearing offers first so title-only offers can attach to them.
                offers.sort(key=lambda o: (o.asin is None, o.gtin is None))
                rows = []
                for o in offers:
                    pid = resolver.resolve(o)
                    self.db.upsert_product(pid, o)
                    rows.append((pid, o))
                n = self.db.insert_offers(run_id, rows)
                warnings = list(getattr(provider, "warnings", []) or [])
                self.db.finish_run(run_id, n, error="; ".join(warnings) or None,
                                   status="partial" if warnings else "ok")
                results.append(RunResult(provider.name, n, warnings=warnings))
            except Exception as exc:  # noqa: BLE001 - report and continue
                log.exception("provider %s failed", provider.name)
                self.db.finish_run(run_id, 0, error=str(exc))
                results.append(RunResult(provider.name, 0, str(exc)))
        return results
