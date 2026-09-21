# price-lenses

Search a product (e.g. "iPhone 17 Pro") across marketplaces via **SerpApi** and **Apify**,
normalise the results, match them to one canonical product, and store everything in **PostgreSQL**.

## Setup
```bash
cd /Users/garimatuwani/Projects/applications/price-lenses
python -m venv .venv && source .venv/bin/activate
pip install -e ".[ui,dev]"
cp .env.example .env        # add SERPAPI_API_KEY / APIFY_API_TOKEN when you have them
docker compose up -d postgres
pytest
```

## Usage
```bash
price-lenses search "iPhone 17 Pro" --providers serpapi apify --limit 20
price-lenses search "iPhone 17 Pro" --providers serpapi --enrich   # + Amazon seller detail (extra credits)
price-lenses compare "iPhone 17 Pro"
price-lenses history asin:B0FQFB8FMG
```
Query the DB directly:
```bash
docker compose exec postgres psql -U pricelens -d pricelens \
  -c "SELECT * FROM latest_offers ORDER BY price LIMIT 20"
```

## How product search is routed

SerpAPI engines have separate responsibilities:

| User input | Identification | Cross-retailer discovery |
|---|---|---|
| Amazon URL or bare ASIN | Amazon Search resolves the ASIN and title | The resolved title is sent to Google Shopping; Amazon Product is optional enrichment |
| Flipkart/Croma/Reliance/Vijay Sales URL | The product slug becomes a query; supported Apify detail actors enrich the page | Google Shopping searches the approved retailers |
| Product text | Amazon Search discovers ASINs | Google Shopping searches across retailers; detail actors verify shortlisted pages |

Google Shopping is the broad multi-store discovery source. Amazon Search is the required Amazon
discovery API, while Amazon Product is optional detail enrichment for a known ASIN. Results are restricted to Amazon
India, Flipkart, Croma, Reliance Digital and Vijay Sales. Google Shopping does not guarantee a
result from every retailer, so complete coverage requires a healthy adapter for each retailer;
currently Amazon and Flipkart have the richest detail support.

## Inspecting the database
`PL_DATABASE_URL` selects the runtime PostgreSQL database. The default local connection in
`.env.example` matches `compose.yaml`; production can use a Neon pooled connection instead.
`PL_TEST_DATABASE_URL` stays pointed at local Docker so tests never create or drop schemas in Neon.
```bash
price-lenses stats                                   # row counts, runs per provider, recent errors
price-lenses sql "SELECT * FROM latest_offers ORDER BY price LIMIT 10"
docker compose exec postgres psql -U pricelens -d pricelens  # interactive SQL shell
```
PostgreSQL data is persisted in the `pricelens_pgdata` Docker volume and survives container restarts.
Run `docker compose stop postgres` to stop it and `docker compose up -d postgres` to start it again.

If the UI reports `connection refused` on port 5433, start and verify PostgreSQL before launching it:

```bash
docker compose up -d postgres
docker compose exec postgres pg_isready -U pricelens -d pricelens
price-lenses stats
price-lenses ui
```

## Using Neon as the runtime database

In Neon, create separate pooled and direct connection strings. Use the pooled URL for the
application and the direct URL only while initializing or migrating the database. Never commit
either URL: `.env` is ignored by Git, while `.env.example` must contain placeholders only.

```dotenv
# .env (example shape only; obtain the real value from Neon after rotating its password)
PL_DATABASE_URL="postgresql://APP_USER:PASSWORD@POOLER_HOST/neondb?sslmode=require&channel_binding=require"
PL_TEST_DATABASE_URL=postgresql://pricelens:pricelens_local@localhost:5433/pricelens
```

If a connection string has been pasted into chat, logs, an issue, or source control, rotate its
password before using it. Validate the active destination without printing credentials:

```bash
price-lenses stats
price-lenses sql "SELECT current_database(), current_user, current_setting('ssl')"
```

### One-time local-to-Neon migration

Set `PL_LOCAL_DATABASE_URL` to the local source and `PL_NEON_DIRECT_URL` to the direct Neon
connection in the ignored `.env` file. The guarded migration command creates the schema, refuses
to copy into a non-empty destination, transfers all application tables in dependency order, and
verifies counts and foreign keys without displaying either connection string.

```bash
python scripts/migrate_postgres.py
```

After importing, compare every table count on both databases and check referential integrity:

```sql
SELECT 'products' table_name, count(*) FROM products
UNION ALL SELECT 'search_runs', count(*) FROM search_runs
UNION ALL SELECT 'offers', count(*) FROM offers
UNION ALL SELECT 'offer_details', count(*) FROM offer_details
UNION ALL SELECT 'offer_promos', count(*) FROM offer_promos
UNION ALL SELECT 'canonical_products', count(*) FROM canonical_products
UNION ALL SELECT 'price_history', count(*) FROM price_history
UNION ALL SELECT 'price_behavior_corpus', count(*) FROM price_behavior_corpus;

SELECT count(*) AS orphan_offers
FROM offers o LEFT JOIN products p ON p.product_id=o.product_id
LEFT JOIN search_runs r ON r.run_id=o.run_id
WHERE p.product_id IS NULL OR r.run_id IS NULL;

SELECT count(*) AS orphan_details
FROM offer_details d LEFT JOIN offers o ON o.offer_id=d.offer_id
WHERE o.offer_id IS NULL;

SELECT count(*) AS orphan_promotions
FROM offer_promos p LEFT JOIN offers o ON o.offer_id=p.offer_id
WHERE o.offer_id IS NULL;
```

Once validation succeeds, put only the rotated **pooled** URL in `.env` as `PL_DATABASE_URL`,
restart the UI, and run one controlled search. Stop the local container with
`docker compose stop postgres`, but retain its volume until Neon has been stable and backed up.
Rollback is simply restoring the local `PL_DATABASE_URL` and starting that container again.

## Web UI
```bash
price-lenses ui                 # opens http://localhost:8501
price-lenses ui --host 0.0.0.0  # inside a Codespace/container, then open the forwarded port 8501
```
Type a product, pick sources, press Search. The app fetches, stores in PostgreSQL, then reads the
results back from the database and shows: headline metrics (lowest / average price, offer count,
best rated), a filterable table (product, marketplace, min rating) with seller, discount, offers and
links, a price-by-seller chart, CSV download, and price history once a product has been searched
more than once. Earlier searches can be reloaded from the sidebar without spending API credits.
API keys can be pasted in the sidebar for the session (not saved) or set in `.env`.

SerpAPI uses bounded retries and separate connect/read timeouts. Slow upstream searches do not
discard results returned by the other SerpAPI engine. Override the defaults in `.env` if needed:

```dotenv
SERPAPI_CONNECT_TIMEOUT=10
SERPAPI_READ_TIMEOUT=120
SERPAPI_RETRIES=2
```

## Apify: getting sellers, bank offers and festive offers
Listing/search scrapers return thin data. Rich data comes from **page-level actors**, so the Apify provider
runs two stages (`providers/apify_provider.py`):

| Stage | Actor (default) | Gives you |
|---|---|---|
| Discover | `apify/e-commerce-scraping-tool` (search-engine "Sellers" mode) | product + one row per seller/store with price, URL, shipping |
| Enrich (Flipkart) | `piotrv1001/flipkart-product-details-scraper` | seller name/id/rating, Assured, COD, no-cost EMI, return policy, delivery date, warranty, MRP, bank + exchange offers, price after offers |
| Enrich (Flipkart, Amazon.in) | `pale_tapestry/bank-offers-aggregator-actor` | bank / card / EMI offers parsed into bank, card type, % and cap |

Only the cheapest `APIFY_ENRICH_LIMIT` (default 5) product URLs per marketplace are enriched, to control cost.
Set `APIFY_ENRICHERS=` (empty) or pass `--no-apify-enrich` for discovery only. Promotions are stored as rows in
`offer_promos` (types BANK, EXCHANGE, COUPON, CASHBACK, EMI, FESTIVE, SPECIAL_PRICE) and seller/delivery details in
`offer_details`. If an actor's input differs from what is coded, patch it without code changes via
`APIFY_EXTRA_INPUT_JSON`. Always check one real run per actor: the raw JSON of every item is kept in `offers.raw_json`.

## How product matching works
ASIN is **Amazon's** identifier - Flipkart, Walmart, eBay etc. don't use it. So matching is layered:
1. ASIN (from the ASIN field or any `/dp/<ASIN>` URL, including Google Shopping links to Amazon)
2. GTIN/UPC/EAN if a source exposes it
3. Fuzzy title match (model numbers and storage sizes must agree), else a new `title:<hash>` product

## Tables
`products` (canonical), `offers` (append-only -> price history), `search_runs` (audit/errors),
view `latest_offers` (newest observation per product/marketplace/seller).

## Layout
`providers/serpapi_provider.py` - cross-store Google Shopping discovery + Amazon search/detail routing
`providers/apify_provider.py` - runs actors (IDs from `.env`), normalises varied output
`matching.py`, `normalize.py`, `db.py`, `service.py`, `cli.py`
`ui/app.py` (Streamlit), `ui/data.py` (pure display logic, unit-tested)
