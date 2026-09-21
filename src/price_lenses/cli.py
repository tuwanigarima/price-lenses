from __future__ import annotations

import argparse
import json
import logging

from .config import Settings
from .db import Database
from .service import PriceLens


def build_providers(names: list[str], s: Settings, enrich: bool, apify_enrich: bool | None = None):
    providers = []
    for name in names:
        if name == "serpapi":
            from .providers.serpapi_provider import SerpApiProvider
            providers.append(SerpApiProvider(
                s.serpapi_key or "", country=s.country, google_domain=s.google_domain,
                amazon_domain=s.amazon_domain, enrich_amazon=enrich,
                connect_timeout=s.serpapi_connect_timeout,
                read_timeout=s.serpapi_read_timeout,
                retries=s.serpapi_retries))
        elif name == "apify":
            from .providers.apify_provider import ApifyProvider, specs_from_settings
            do_enrich = bool(s.apify_enrichers) if apify_enrich is None else apify_enrich
            providers.append(ApifyProvider(
                s.apify_token or "", specs=specs_from_settings(s), enrich=do_enrich,
                enrich_limit=s.apify_enrich_limit))
        else:
            raise SystemExit(f"Unknown provider: {name}")
    return providers


def _fmt(v, width):
    return ("" if v is None else str(v))[:width].ljust(width)


def cmd_search(args, s: Settings) -> None:
    db = Database(s.database_url)
    lens = PriceLens(db, build_providers(args.providers, s, args.enrich,
                                         False if args.no_apify_enrich else None))
    for r in lens.search(args.query, args.limit):
        status = f"ERROR: {r.error}" if r.error else "ok"
        print(f"[{r.provider}] {r.count} offers stored ({status})")
        for w in r.warnings or []:
            print(f"    warning: {w}")
    cmd_compare(args, s, db)


def cmd_compare(args, s: Settings, db: Database | None = None) -> None:
    db = db or Database(s.database_url)
    rows = db.compare(args.query, getattr(args, "top", 50))
    if not rows:
        print("No data yet. Run `price-lenses search \"<product>\"` first.")
        return
    print(f"\n{'PRODUCT':22} {'MARKETPLACE':16} {'SELLER':18} {'PRICE':>11} {'RATING':>6} {'REVIEWS':>8}  OFFERS")
    for (pid, title, mkt, seller, price, cur, old, rating, reviews, avail, ship,
         offers_json, url, provider) in rows:
        offer_values = offers_json if isinstance(offers_json, list) else json.loads(offers_json or "[]")
        offers = "; ".join(map(str, offer_values))[:40]
        p = f"{cur or ''} {price:,.0f}" if price is not None else "-"
        print(f"{_fmt(pid,22)} {_fmt(mkt,16)} {_fmt(seller,18)} {p:>11} "
              f"{_fmt(rating,6):>6} {_fmt(reviews,8):>8}  {offers}")


def cmd_history(args, s: Settings) -> None:
    db = Database(s.database_url)
    for ts, mkt, seller, price, cur in db.price_history(args.product_id):
        print(ts, mkt, seller or "-", cur or "", price)


def _open_readonly(s: Settings):
    import psycopg
    from urllib.parse import urlsplit

    try:
        # Bootstrap an empty local database before opening the reporting connection.
        # Schema DDL is idempotent, so `stats` and `sql` work before the first search.
        bootstrap = Database(s.database_url)
        bootstrap.close()
        con = psycopg.connect(
            s.database_url, autocommit=True,
            options="-c default_transaction_read_only=on",
        )
        parsed = urlsplit(s.database_url)
        label = f"{parsed.hostname or 'localhost'}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"
        return con, label
    except psycopg.OperationalError as exc:
        raise SystemExit(
            f"Could not connect to PostgreSQL: {exc}\n"
            "Check PL_DATABASE_URL, database credentials, network access, and SSL settings. "
            "For the optional local database, start it with `docker compose up -d postgres`."
        ) from exc


def _print_table(cur) -> None:
    cols = [d[0] for d in cur.description]
    rows = [["" if v is None else str(v) for v in r] for r in cur.fetchall()]
    widths = [min(60, max([len(c)] + [len(r[i]) for r in rows])) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(v[:w].ljust(w) for v, w in zip(r, widths)))
    print(f"({len(rows)} rows)")


def cmd_stats(args, s: Settings) -> None:
    con, label = _open_readonly(s)
    print(f"Database: {label}\n")
    for t in ("products", "offers", "search_runs"):
        print(f"{t:12} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]} rows")
    print("\nRuns per provider:")
    _print_table(con.execute(
        "SELECT provider, status, count(*) AS runs, sum(result_count) AS offers, "
        "max(started_at) AS last_run FROM search_runs "
        "GROUP BY provider, status ORDER BY last_run DESC"))
    print("\nRecent errors:")
    _print_table(con.execute(
        "SELECT started_at, provider, query, substr(error,1,80) AS error FROM search_runs "
        "WHERE status='error' ORDER BY started_at DESC LIMIT 5"))


def cmd_sql(args, s: Settings) -> None:
    con, _ = _open_readonly(s)
    _print_table(con.execute(args.statement))


def cmd_ui(args, s: Settings) -> None:
    import subprocess
    import sys
    from pathlib import Path

    app = Path(__file__).with_name("ui") / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    if args.host:
        cmd += ["--server.address", args.host]
    raise SystemExit(subprocess.call(cmd))


def main() -> None:
    ap = argparse.ArgumentParser(prog="price-lenses")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="fetch offers from providers and store them")
    sp.add_argument("query")
    sp.add_argument("--providers", nargs="+", default=["serpapi", "apify"],
                    choices=["serpapi", "apify"])
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--enrich", action="store_true",
                    help="SerpAPI text search: enrich discovered Amazon ASINs with product details")
    sp.add_argument("--no-apify-enrich", action="store_true",
                    help="Apify: skip the per-product detail actors (sellers, bank offers) to save credits")
    sp.set_defaults(fn=cmd_search)

    cp = sub.add_parser("compare", help="show stored offers for a previous query")
    cp.add_argument("query")
    cp.add_argument("--top", type=int, default=50)
    cp.set_defaults(fn=cmd_compare)

    hp = sub.add_parser("history", help="price history for a product_id")
    hp.add_argument("product_id")
    hp.set_defaults(fn=cmd_history)

    stp = sub.add_parser("stats", help="row counts, runs per provider, recent errors")
    stp.set_defaults(fn=cmd_stats)

    sqp = sub.add_parser("sql", help="run a read-only SQL query against PostgreSQL")
    sqp.add_argument("statement")
    sqp.set_defaults(fn=cmd_sql)

    up = sub.add_parser("ui", help="launch the web UI (needs: pip install -e '.[ui]')")
    up.add_argument("--port", type=int, default=8501)
    up.add_argument("--host", default=None, help="e.g. 0.0.0.0 inside a Codespace/container")
    up.set_defaults(fn=cmd_ui)

    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    args.fn(args, Settings.from_env())


if __name__ == "__main__":
    main()
