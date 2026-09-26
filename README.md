# 🔍 PriceLens: Autonomous Agentic RAG Advisor

PriceLens is a multi-agent India e-commerce analysis system. It combines stored
price history, current offers from SerpAPI/Apify, and grounded retailer-policy
evidence. Agents 1–3 are available; the cross-agent decision synthesizer remains
an explicit future stage and does not currently emit a final combined verdict.

Built as an **Agentic RAG** pipeline using **LangGraph**, PriceLens eschews traditional sequential LLM wrappers in favor of a highly parallelized Directed Acyclic Graph (DAG) architecture.

---

## 🏗️ The Multi-Agent Architecture

PriceLens uses **LangGraph** for product resolution and three independent
specialist reports. All three agents fan out after input resolution.

```mermaid
graph TD
    User((User Input)) --> Node0[0. Input Resolver]
    
    Node0 --> Agent1[1. History Analyst<br/>PostgreSQL Time-Series]
    Node0 --> Agent2[2. Market Investigator<br/>SerpAPI/Apify + PostgreSQL]
    Node0 --> Agent3[3. Policy & Purchase Protection Analyst<br/>PostgreSQL + Chroma policy RAG]
    
    Agent1 --> Node4[4. Decision Synthesizer<br/>future implementation]
    Agent2 --> Node4
    Agent3 --> Node4
    
    Node4 --> UI[5. Streamlit Dashboard]
```

### 1. History & Trend Analyst (Deterministic + ReAct)
* **Role:** Analyzes 365-day price histories to determine the True All-Time Low (ATL) and Deal Health Index ($S_{history}$).
* **Tools:** Executes pure Python/PostgreSQL tools (`check_price_trend`, `get_historical_sale_drops`) to guarantee 0% hallucination on financial math.

### 2. Market & Offer Investigator
* **Role:** Finds and normalizes current India-market offers, variants, sellers,
  availability, and product-bound promotions across the supported retailers.
  It deterministically validates every offer before ranking verified prices.

### 3. Policy & Purchase Protection Analyst
* **Role:** Compares retailer-level return, replacement, cancellation, warranty,
  and FAQ evidence without reading or ranking Agent 2 offers.
* **Tools:** Uses hybrid PostgreSQL full-text/Chroma retrieval over approved
  official India retailer policy documents, followed by citation verification.

### 4. Decision Synthesizer (future)
* **Role:** Will reconcile the three specialist reports. Its graph node currently
  returns `pending_future_implementation`; Agent 3 never emits BUY/WAIT itself.

---

## 🛠️ Tech Stack
* **Orchestration:** LangGraph, LangChain
* **LLM Engine:** Optional OpenAI-compatible guarded tool planning and summaries;
  deterministic execution and fallbacks
* **Database:** Local PostgreSQL (`psycopg2`)
* **Vector Store:** Local ChromaDB (derived/rebuildable policy index)
* **UI/Frontend:** Streamlit, Plotly Express

---

## 🚀 Getting Started (Local Setup)

### 1. Clone & Environment Setup
Clone the repository and activate your Python virtual environment:
```bash
# Example using venv
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies
Install the required packages from the generated `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 3. Start local data services and configure `.env`

```bash
docker compose up -d postgres chroma
cp .env.example .env
```

The local runtime database setting is:

```env
DATABASE_URL=postgresql://pricelens:pricelens_local@localhost:5433/pricelens
CHROMA_HOST=localhost
CHROMA_PORT=8000
CHROMA_DATABASE=price_lenses

# One server-side key is shared by all three agents.
OPENAI_API_KEY=your_openai_project_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5-mini

MARKET_AGENT_LLM_ENABLED=true
MARKET_AGENT_LLM_BASE_URL=https://api.openai.com/v1
MARKET_AGENT_LLM_MODEL=gpt-5-mini

ELIGIBILITY_AGENT_LLM_ENABLED=true
ELIGIBILITY_AGENT_LLM_BASE_URL=https://api.openai.com/v1
ELIGIBILITY_AGENT_LLM_MODEL=gpt-5-mini
```

Keep provider/LLM keys only in `.env`, which is ignored by Git. Initialize the
base schema and apply both additive migrations:

```bash
python scripts/db/init_db.py
python scripts/db/migrate.py
```

### 4. Run the Dashboard
Launch the interactive Streamlit UI:
```bash
streamlit run app.py
```
The dashboard has one unified product input and three report tabs:

- **History & Timing** uses the existing historical analyst and price-history tables.
- **Market Investigator** loads stored market offers or explicitly fetches live
  SerpAPI/Apify results. Merely opening the tab does not consume provider credits.
- **Policy Protection** runs Agent 3 independently and shows retailer policy
  profiles, evidence gaps, citations, and its tool/LLM trace.

### 5. Run the Market Investigator data pipeline

The live-market pipeline uses the same `DATABASE_URL` and `products` catalog as
the historical analyst. It adds append-only market run, offer, seller-detail,
and promotion tables without replacing the existing history tables.

Configure `SERPAPI_API_KEY` and `APIFY_API_TOKEN` in the ignored `.env`, then
fetch and persist offers from both providers:

```bash
python scripts/market_search.py "Apple iPhone 16 128GB"
```

Use `--providers serpapi` or `--providers apify` to run one provider. SerpAPI
uses Amazon Search and Google Shopping for discovery. Optional Amazon Product
API enrichment is disabled by default and can be enabled with
`SERPAPI_ENRICH_AMAZON=true`. Apify enriches the cheapest discovered listings
with configured Flipkart and bank-offer actors.

### 6. Build and test Agent 3 locally

Register approved policy sources, fetch immutable versions, and build Chroma:

```bash
python scripts/policies/seed_sources.py
python scripts/policies/fetch_documents.py
python scripts/policies/build_chroma_index.py
python scripts/policies/validate_policy_index.py "Can I return a defective phone?" --retailer Flipkart
```

Test Agent 3 independently after policy indexing:

```bash
python scripts/run_eligibility_agent.py \
  "Samsung S24 Ultra 8GB/256GB Graphite" \
  --product-category smartphone
```

To import the supplied product catalog for Agents 1 and 2:

```bash
python scripts/dev/import_products_json.py /path/to/products.json
```

The importer upserts product aggregates for Agent 1 and creates Agent 2 Amazon
snapshots only for rows with a raw ASIN and positive current price. It does not
turn aggregate ATL/ATH values into invented dated `price_history` rows; Agent 1
uses those aggregates transparently when no dated series exists.

`policy_sources` records fetch failures instead of activating unusable content.
PostgreSQL remains canonical; Chroma can always be rebuilt from active chunks.

---

## 🧠 Key Features for Evaluators
* **Trace observability:** Agent 2 and Agent 3 expose their executed tool stages.
* **Grounded evidence:** Agent 2 verifies offer prices and product-bound
  promotions; Agent 3 verifies retailer-policy claims against active chunks.
* **Safe degradation:** Missing policy evidence lowers confidence and produces a
  partial report instead of inventing a return, warranty, or authorization claim.
