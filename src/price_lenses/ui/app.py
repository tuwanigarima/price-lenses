"""Streamlit UI.  Run with:  price-lenses ui    (or: streamlit run src/price_lenses/ui/app.py)"""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
import psycopg
import streamlit as st

from price_lenses.cli import build_providers
from price_lenses.config import Settings
from price_lenses.db import Database
from price_lenses.service import PriceLens
from price_lenses.ui import data as ui

st.set_page_config(page_title="Price Lenses", page_icon="🔎", layout="wide")


@st.cache_resource
def get_db(path: str) -> Database:
    return Database(path)


settings = Settings.from_env()
try:
    db = get_db(settings.database_url)
except psycopg.OperationalError as exc:
    st.error("PriceLens cannot connect to PostgreSQL.")
    st.caption("Check PL_DATABASE_URL, database credentials, network access, and SSL settings. "
               "If you are using the optional local database, run the following command from "
               "the project directory and wait for the container to become healthy:")
    st.code("docker compose up -d postgres", language="bash")
    with st.expander("Connection details"):
        st.code(str(exc))
    st.stop()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Search settings")
    use_serpapi = st.checkbox("SerpApi (Google Shopping + Amazon)", value=True)
    use_apify = st.checkbox("Apify (marketplace scrapers)", value=True)
    limit = st.slider("Results per source", 5, 50, 20, step=5)
    enrich = st.checkbox(
        "Enrich Amazon text-search results (extra SerpAPI credits)", value=False,
        help="Amazon Search is always used; enable this to add Amazon Product seller and structured promotion details.",
    )
    apify_enrich = st.checkbox("Apify: add seller & bank-offer details (extra Apify cost)",
                               value=bool(settings.apify_enrichers),
                               help="Runs product-page actors on the cheapest listings per marketplace.")

    with st.expander("API keys (this session only)"):
        st.caption("Leave blank to use values from .env. Keys typed here are not saved.")
        serp_key = st.text_input("SerpApi key", type="password")
        apify_key = st.text_input("Apify token", type="password")
    settings = replace(settings,
                       serpapi_key=serp_key or settings.serpapi_key,
                       apify_token=apify_key or settings.apify_token)
    st.caption(f"SerpApi key: {'✅ set' if settings.serpapi_key else '❌ missing'}  \n"
               f"Apify token: {'✅ set' if settings.apify_token else '❌ missing'}")

    st.divider()
    st.subheader("Previous searches")
    past = db.recent_queries()

    def _load_past() -> None:
        if st.session_state.get("past_pick"):
            st.session_state["active_query"] = st.session_state["past_pick"]

    st.selectbox("Load from database", [""] + [p["query"] for p in past],
                 key="past_pick", on_change=_load_past,
                 format_func=lambda q: q or "— choose —")

# ---------------------------------------------------------------- search box
st.title("🔎 Price Lenses")
st.caption("Compare prices, sellers, ratings and offers for a product across sites.")

with st.form("search_form"):
    c1, c2 = st.columns([5, 1])
    query = c1.text_input("Product", placeholder="e.g. iPhone 17 Pro",
                          label_visibility="collapsed")
    submitted = c2.form_submit_button("Search")

if submitted:
    q = query.strip()
    names = [n for n, on in (("serpapi", use_serpapi), ("apify", use_apify)) if on]
    if not q:
        st.warning("Enter a product name.")
    elif not names:
        st.warning("Select at least one source.")
    else:
        providers = []
        for n in names:  # build one by one so a missing key doesn't block the other source
            try:
                providers += build_providers([n], settings, enrich, apify_enrich)
            except Exception as exc:  # noqa: BLE001
                st.error(f"{n}: {exc}")
        if providers:
            with st.spinner(f"Fetching '{q}' from {', '.join(p.name for p in providers)}…"):
                st.session_state["last_results"] = PriceLens(db, providers).search(q, limit)
            st.session_state["active_query"] = q

# ---------------------------------------------------------------- run status
for r in st.session_state.get("last_results", []):
    if r.error:
        st.error(f"**{r.provider}** failed: {r.error}")
    else:
        st.success(f"**{r.provider}**: {r.count} offers stored")
        for w in r.warnings or []:
            st.warning(f"{r.provider}: {w}")

# ---------------------------------------------------------------- results (read back from DB)
active = st.session_state.get("active_query")
if not active:
    st.info("Search for a product above, or load a previous search from the sidebar.")
    st.stop()

rows = ui.enrich_rows(db.offers_for_query(active))
st.subheader(f"Results for “{active}”")
if not rows:
    st.info("Nothing stored for this query yet (check the errors above).")
    st.stop()

# filters
products = ui.group_products(rows)
f1, f2, f3 = st.columns([3, 2, 1])
choice = f1.selectbox(
    "Product", ["All products"] + [g["product_id"] for g in products],
    format_func=lambda pid: "All products" if pid == "All products" else next(
        f"{(g['title'] or pid)[:70]}  ({g['offers']} offers)" for g in products
        if g["product_id"] == pid))
markets = sorted({r["marketplace"] for r in rows})
picked_markets = f2.multiselect("Marketplace", markets, default=markets)
min_rating = f3.slider("Min rating", 0.0, 5.0, 0.0, step=0.5)

selected_pid = None if choice == "All products" else choice
view = ui.filter_rows(rows, picked_markets, min_rating, selected_pid)
if not view:
    st.warning("No offers match the filters.")
    st.stop()

# headline metrics
s = ui.summarize(view)
m1, m2, m3, m4 = st.columns(4)
if s["cheapest"]:
    m1.metric("Lowest price", ui.fmt_money(s["cheapest"]["price"], s["currency"]),
              help=ui.offer_label(s["cheapest"]))
    m1.caption(ui.offer_label(s["cheapest"]))
m2.metric("Average price", ui.fmt_money(s["average"], s["currency"]))
m3.metric("Offers · marketplaces", f"{s['offers']} · {s['marketplaces']}")
if s["best_rated"]:
    m4.metric("Best rated", f"{s['best_rated']['rating']:.1f} ★")
    m4.caption(ui.offer_label(s["best_rated"]))

# table
df = pd.DataFrame(ui.to_table(view))
st.dataframe(
    df, hide_index=True,
    column_config={
        "Price": st.column_config.NumberColumn(format="%.0f"),
        "Was": st.column_config.NumberColumn(format="%.0f"),
        "Discount %": st.column_config.NumberColumn(format="%.1f%%"),
        "Rating": st.column_config.NumberColumn(format="%.1f ★"),
        "Link": st.column_config.LinkColumn("Link", display_text="Open"),
    })
# offers & promotions (bank / exchange / coupon / festive)
view_ids = {r["offer_id"] for r in view if r.get("offer_id")}
promo_rows = db.promos_for_query(active)
ptable = ui.promos_table(promo_rows, view_ids)
st.markdown("**Offers & promotions**")
if ptable:
    counts = ui.promo_summary(promo_rows, view_ids)
    st.caption("  ·  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    st.dataframe(pd.DataFrame(ptable), hide_index=True, column_config={
        "Amount": st.column_config.NumberColumn(format="%.0f"),
        "Percent": st.column_config.NumberColumn(format="%.0f%%"),
        "Product link": st.column_config.LinkColumn("Product link", display_text="Open")})
else:
    st.caption("No structured offers stored yet. Enable Apify detail enrichment in the sidebar and search "
               "again (bank/exchange offers come from product-page scrapers, not listing search).")

st.download_button("Download CSV", df.to_csv(index=False).encode(), f"{active}.csv", "text/csv")

# price by seller chart (single currency only)
cur = s["currency"]
chart_rows = [r for r in view if r.get("price") is not None and r.get("currency") == cur]
if chart_rows:
    st.markdown(f"**Price by seller ({cur})**")
    chart = pd.DataFrame({"Price": [r["price"] for r in chart_rows]},
                         index=[ui.offer_label(r) + f" [{i}]" for i, r in enumerate(chart_rows)])
    st.bar_chart(chart.sort_values("Price"))

# price history for a single product
if selected_pid:
    hist = db.history_for_product(selected_pid)
    hdf = pd.DataFrame(hist)
    if not hdf.empty and hdf["fetched_at"].nunique() > 1:
        hdf["label"] = [ui.offer_label({"marketplace": m, "seller_name": sl})
                        for m, sl in zip(hdf["marketplace"], hdf["seller_name"])]
        st.markdown("**Price history**")
        st.line_chart(hdf.pivot_table(index="fetched_at", columns="label",
                                      values="price", aggfunc="min"))
    else:
        st.caption("Price history appears here after this product has been searched more than once.")
