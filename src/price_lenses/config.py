from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:  # dotenv is optional at runtime
        pass


def _json_env(name: str) -> dict:
    raw = os.getenv(name)
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except ValueError:
        raise SystemExit(f"{name} is not valid JSON")


@dataclass(frozen=True)
class Settings:
    serpapi_key: str | None
    apify_token: str | None
    country: str
    google_domain: str
    amazon_domain: str
    database_url: str
    serpapi_connect_timeout: int = 10
    serpapi_read_timeout: int = 120
    serpapi_retries: int = 2
    min_history_days: int = 180
    # ---- Apify actors ----
    apify_ecom_actor: str = "apify/e-commerce-scraping-tool"
    apify_ecom_mode: str = "Sellers"          # Sellers | Products | Google Listing
    apify_flipkart_details_actor: str = "piotrv1001/flipkart-product-details-scraper"
    apify_bank_offers_actor: str = "pale_tapestry/bank-offers-aggregator-actor"
    apify_enrichers: tuple = ("flipkart_details", "bank_offers")   # () disables enrichment
    apify_enrich_limit: int = 5               # max product URLs enriched per marketplace per search
    apify_extra_input: dict = field(default_factory=dict)  # {"ecom_search": {...}, "bank_offers": {...}}

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        g = os.getenv
        enrichers = tuple(x.strip() for x in g("APIFY_ENRICHERS", "flipkart_details,bank_offers").split(",")
                          if x.strip())
        return cls(
            serpapi_key=g("SERPAPI_API_KEY") or None,
            apify_token=g("APIFY_API_TOKEN") or None,
            country=g("PL_COUNTRY", "in"),
            google_domain=g("PL_GOOGLE_DOMAIN", "google.co.in"),
            amazon_domain=g("PL_AMAZON_DOMAIN", "amazon.in"),
            database_url=g(
                "PL_DATABASE_URL",
                "postgresql://pricelens:pricelens_local@localhost:5433/pricelens",
            ),
            serpapi_connect_timeout=int(g("SERPAPI_CONNECT_TIMEOUT", "10")),
            serpapi_read_timeout=int(g("SERPAPI_READ_TIMEOUT", "120")),
            serpapi_retries=int(g("SERPAPI_RETRIES", "2")),
            min_history_days=int(g("PL_MIN_HISTORY_DAYS", "180")),
            apify_ecom_actor=g("APIFY_ECOM_ACTOR", "apify/e-commerce-scraping-tool"),
            apify_ecom_mode=g("APIFY_ECOM_MODE", "Sellers"),
            apify_flipkart_details_actor=g("APIFY_FLIPKART_DETAILS_ACTOR",
                                           "piotrv1001/flipkart-product-details-scraper"),
            apify_bank_offers_actor=g("APIFY_BANK_OFFERS_ACTOR",
                                      "pale_tapestry/bank-offers-aggregator-actor"),
            apify_enrichers=enrichers,
            apify_enrich_limit=int(g("APIFY_ENRICH_LIMIT", "5")),
            apify_extra_input=_json_env("APIFY_EXTRA_INPUT_JSON"),
        )
