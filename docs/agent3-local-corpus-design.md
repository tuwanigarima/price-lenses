# Agent 3: fixed local policy corpus

Status (2026-09-28): local-only ingestion and runtime retrieval are implemented. The additive `policy_local` schema is initialized in Neon. The first active build contains one reviewed Amazon Returns snapshot and five embedded chunks. The snapshot was exported from existing approved Neon data, without fetching any source page. Other manifest entries remain pending review/import.

The initial empty local index caused Agent 3 to report `insufficient_evidence` even though legacy Amazon chunks existed. Build `79f0db2b-7910-4a50-88e3-017af9549abd` closes that migration gap. The immutable original and its capture metadata are saved under `data/policies/corpus/amazon_india/`. The reviewed extract preserves complete general return clauses and the listing-applicability caveat; it excludes a flattened mixed-category table whose cell relationships were lost during legacy extraction. It establishes general return/replacement/refund evidence, not an Oppo-specific return window, warranty, or cancellation entitlement. Reviewed local topic scope now prevents incidental references (for example, a warranty card required for returns) from establishing additional policy coverage.

## Implemented foundation

- `data/policies/corpus/` is the chosen input folder; its README explains metadata and commands.
- `scripts/policies/import_local_documents.py` validates selected local files without network access by default. `--publish` embeds through the configured provider and atomically publishes to Neon; `--rebuild` forces re-embedding of an unchanged build.
- Web-fetch and Apify policy-acquisition entry points are disabled. Existing market-agent acquisition is unaffected.
- `tools/local_policy_corpus.py` preserves headings/source spans, bounded child excerpts, complete parent-section context, HTML table columns, and PDF page labels. Optional reviewed `section_scopes` map mixed-document headings to topics/categories. Chunk size has a hard 2,400 UTF-8 byte bound, not an exact tokenizer-based limit.
- `tools/local_policy_db.py` retrieves only a pinned local build, retailer-policy corpus, applicable category scopes and effective dates. No legacy policy rows are used.
- Failed selected files or invalid embeddings prevent publication. An unchanged active corpus/model is reused unless `--rebuild` is requested. Changed collections are currently embedded in full; per-chunk incremental reuse is future work.
- Agent 3 retains other retailers' findings when one lookup fails and exposes semantic-retrieval degradation. Source paths, capture dates, build IDs and supporting passages are retained in reports.
- Refund is a default substantive topic; FAQ remains a supported requested topic but is no longer a default protection-coverage requirement.

The more advanced items below (automatic claim extraction/verification, dedicated table-row and FAQ splitting, regulatory answering, conflict resolution, reranking evaluation, and snapshot-age policy) remain proposed work. Do not interpret this foundation as proof that every edge case is solved.

## Scope

Agent 3 remains independent of Agent 2. It answers retailer/category policy questions from a fixed, explicitly listed set of locally saved documents. It does not discover URLs, follow links, fetch websites, or fall back to Apify. Source URLs are provenance, not instructions to fetch.

`data/policies/curated_manifest.json` records the user's 23 supplied entries. It distinguishes document candidates, hubs, search portals, and excluded material. Classifications and candidate policy topics come from the supplied descriptions; they are not evidence of the page contents. Local document paths, capture times, and hashes remain unknown until files are supplied.

The old `sources.json` is retained for history. The old fetch command now exits without fetching; the embedding compatibility command routes to the local importer. Existing policy data is retained, but excluded from the new retrieval path.

## Collection boundaries

- Retailer policy documents support retailer protection claims.
- Government instruments form a separate regulatory corpus. Record jurisdiction, instrument date, amendments, and version; a 2020 document alone is not proof of current legal requirements.
- The ICSI explainer is secondary commentary, not the statutory instrument.
- DCA indexes and retailer hubs are collection references. Their navigation links are not substantive rules. Specific documents require local copies and explicit manifest entries; do not automatically follow them.
- Case portals do not supply evidence until specific judgments are selected and stored with court, date, case number, and relevant passages. Do not treat a judgment's outcome as a universal retailer policy.
- The Zlash summary is excluded from ingestion and retrieval. No cross-check fetch is authorized in this workflow.

## Workspace layout

```text
data/policies/
  curated_manifest.json
  corpus/<publisher>/<source-id>.<pdf|html|md>
  corpus/README.md
```

Keep originals immutable. Normalized text, hashes and metadata are stored separately in Neon document snapshots. The manifest records original URL, actual captured_at, review status and optional reviewed file hash. Never substitute import time for an unknown source capture time. Avoid duplicating document text for every policy topic or product.

## Local-only ingestion contract

Add an import command accepting only local manifest entries and supported file types. It should:

1. Validate that the selected entry is ingestible and its local path resolves within the corpus root, including symlink resolution.
2. Reject HTTP paths, missing files, hubs without substantive reviewed content, excluded entries, and unapproved source additions. Never silently fetch missing content.
3. Parse saved Markdown/HTML/PDF without loading remote assets. Preserve headings, table headers, bullet structure, page boundaries and source spans. Flag scanned PDFs for local OCR/review rather than embedding empty text.
4. Validate extracted content and metadata, and present missing or malformed documents in a collection report.
5. Create a staged document version, chunks, topic/category metadata, and hashes.
6. Build embeddings only for new or changed embedded text/model combinations.
7. Validate coverage, citations, vector dimensions, and retrieval fixtures before atomically activating a corpus build. A failed build must leave the previous build available.

The old web-fetch entry point must be disabled in local-only mode, including direct use of its helper and Apify fallback. Embedding-provider calls and configured database writes remain allowed; local-only acquisition does not mean offline embeddings.

## Category and policy taxonomy

Primary filters are country, corpus, retailer, policy topic, and category. Use a hierarchy such as `all_products > electronics > mobile_phones`, with aliases `mobile`, `smartphone`, and `cell phone` mapped to the same category. Optional brand, condition, seller, purchase channel, and effective-date scopes refine applicability.

Treat retailer, topic and category as metadata facets rather than requiring one physical copy per combination. Allow a chunk to cover multiple topics/categories. Do not label an entire mixed-category document as mobile-phone policy just because a mobile question retrieved it.

Query exact category clauses plus applicable ancestor clauses. A narrower clause refines a broader one only when authority, effective version and exceptions support that interpretation; contradictions remain explicit. Do not assume all phones or all physical branches have identical policies: retain brand, listing, seller and online/in-store exceptions.

Separate FAQ (document structure) from substantive topics such as return, replacement, refund, cancellation, warranty, payment, pricing, delivery and return process. FAQ absence should not count as missing purchase protection.

## Chunking strategy

Start with structure-aware child chunks and recoverable parent context. This is a candidate to evaluate against the existing chunker, not a proven optimum before examining the documents.

| Structure | Retrieval unit | Context that must remain available |
|---|---|---|
| Policy clause | Clause with qualifiers and exceptions | Heading and enclosing policy section |
| FAQ | Question and full answer | Related exception section |
| Eligibility table | Category row with repeated column headers | Table title, notes, footnotes and relevant section |
| Regulation | Numbered rule/subrule including provisos | Instrument, definitions and parent rule |
| Judgment | Coherent passage with paragraph/page references | Case metadata and surrounding reasoning |

Target approximately 300–600 measured tokens for child chunks, with an explicit maximum validated by the chunker. Split large units along subclauses/sentences with roughly 50–80 tokens of overlap; preserve links to the complete parent. Oversized or malformed units are flagged. Never split a restriction from its qualifying condition merely to meet a target size.

Prefix embedding text with concise retailer, topic, category and heading context. Preserve the unmodified evidence text and exact source span separately. Content hash must cover the actual embedded text, including its prefix. Record tokenizer/chunker versions and document identity. Keep source-level duplicate aliases in provenance while avoiding redundant indexing.

Do not embed the whole page as one vector, or infer protections merely from keywords such as `return`. 'Returns are excluded' is evidence of an exclusion.

## Database plan

Use the existing Neon PostgreSQL database, pgvector and PostgreSQL full-text search. No second database service is needed. The implementation deliberately isolates local collections in new tables to avoid rewriting the existing production evidence schema:

| Implemented table | Purpose |
|---|---|
| policy_local.builds | Build hash, model/chunker version, and atomic active-build pointer |
| policy_local.documents | Local document bodies, provenance, corpus kind, effective dates and metadata |
| policy_local.chunks | Multi-topic/category metadata, source/parent spans, full-text index and 1,536-dimensional vectors |

The following earlier reuse plan is superseded for storage; structured rule extraction remains a potential extension:

| Existing table | Intended use / extension |
|---|---|
| policy_sources | Approved local source registry; local ingestion mode and corpus kind |
| policy_document_versions | Immutable snapshots, true capture time, effective dates, parser version, local hash/path |
| policy_chunks | Child/parent evidence, category/topic scopes, page/section/character locators |
| policy_chunk_embeddings | Existing 1,536-dimensional vectors, model and embedded-content hash |
| policy_rules | Structured supported clauses with applicability, exclusions and evidence spans |
| rag_index_manifests | Validated active build plus an explicit build-to-document/chunk membership relation |

Local chunks use a policy-types array and one reviewed category per chunk; section overrides support mixed documents. Corpus and build membership are enforced by both lexical and vector queries. A policy applying to multiple disjoint categories still needs explicit reviewed scope design; it must not be silently broadened to all electronics.

Keep the existing configured embedding model/dimension initially. Reindex when embedded text or model changes; dimension changes require a schema migration. The local importer prepares all embeddings before its transaction, inserts a complete inactive build, validates row count, and then switches the active build. A failed transaction preserves the previous build.

## Retrieval and answer generation

1. Resolve retailer/category/topic independently of prices and offer results.
2. Restrict to the active curated build and intended corpus. Query applicable category/ancestor scopes and effective versions.
3. Search each needed substantive topic using lexical and semantic retrieval, merge with reciprocal rank fusion, and deduplicate.
4. Rerank for question relevance and scope if evaluation shows a benefit. Expand parent context, table footnotes and exception passages before extracting an answer.
5. Extract structured claims: resolution, window, conditions, exclusions, applicability, source span, and evidence IDs. Unknown fields remain null.
6. Verify citations and extracted values; detect conflicts and expired or undated snapshots. Citation existence alone is insufficient.
7. Summarize only verified claims. Return successful retailer/topic findings when others fail. Surface semantic-search failure as a degraded retrieval status rather than silently hiding it.

Cache by corpus build, retailer, category, topic and relevant scope, not by individual phone model unless the rules actually vary by model/brand. Validate category scope before reusing results.

## Report contract and synthesis

Separate evidence availability from applicability and outcome:

```json
{
  "retailer": "Retailer A",
  "category": "mobile_phones",
  "policy_type": "RETURN",
  "evidence_status": "found",
  "applicability": "requires_listing_confirmation",
  "resolution": "unknown",
  "window_days": null,
  "conditions": [],
  "exclusions": [],
  "evidence_ids": ["chunk-id"],
  "corpus_build_id": "build-id",
  "reason_codes": ["LISTING_TERMS_REQUIRED"]
}
```

Report missing local document, not indexed, no relevant clause, stale snapshot, retrieval error, conflicting evidence and confirmed exclusion distinctly. A fixed collection provides 'according to the saved snapshot' answers, not an assurance that a policy is current on the web.

Pass structured findings and evidence references to synthesis. Keep regulatory context separate from retailer promises and do not let it fabricate a checkout guarantee. Agent 3 never requires Agent 2; synthesis can later combine the independent reports.

## Acceptance checks

- With all acquisition network paths disabled, local parsing/import can proceed; missing files cannot trigger a request or Apify call.
- Corpus/hash/build filters prevent retrieval of older unrelated DB sources and the excluded third-party summary.
- Mobile queries never use laptop-only rows; table headers and qualifying footnotes survive chunking.
- Negation, replacement-only policies, brand exceptions, missing warranty evidence and conflicting clauses produce correct claim states.
- Semantic failure retains lexical findings with an explicit warning; one retailer failure retains other retailer findings.
- Failed embedding or validation does not replace the active build; unchanged reimport is idempotent.
- Measure retrieval recall by topic/category, grounded claim precision, exception preservation, missing-evidence accuracy, latency and embedding cost on reviewed examples from the actual files. Choose chunk sizes based on those results.

## Current blocker

Only source URLs have been supplied. The chosen folder is `data/policies/corpus/`, with publisher subfolders and suggested filenames in the manifest. Populate the selected entries with saved document bodies and reviewed metadata before embedding/publication. No source content, retrieval date, rule or embedding should be invented to fill that gap.

## Validation and environment note

The repository test suite passes (140 tests at implementation time). Additional tests cover missing files, traversal/symlinks, excluded hubs, review/hash/date validation, long multibyte clauses, category scoping, embedding failures, publication ordering/rollback, and per-retailer failures. Actual corpus quality cannot be evaluated before the documents are present.

The general migration runner detected a pre-existing checksum mismatch for `001_market_investigator.sql`. Its history was not edited. `scripts/policies/init_local_schema.py` applied and recorded only the independent `004_local_policy_corpus.sql` migration, after checking the existing Agent 3 and pgvector prerequisites. Resolving the old migration drift remains separate work.
