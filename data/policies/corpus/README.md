# Saved Agent 3 documents

Place your saved policy bodies in the publisher folders here. No command in the
new pipeline downloads source URLs or uses Apify. An empty folder is not a policy.

The Amazon Returns snapshot captured on 2026-09-27 was exported from existing
approved Neon data on 2026-09-28. `amazon_india/amazon_returns.original.md` and its
metadata file preserve that export. `amazon_returns.md` contains the reviewed,
verbatim general clauses and listing-applicability caveat. The original flattened
policy-type table and unrelated category sections are excluded from ingestion.
The active collection has five embedded chunks from this extract. Other sources
are still pending. No listing-specific return window is established by this file.

The fixed source list is `../curated_manifest.json`. For each file you want to
index, fill in the entry's `local_path` (relative to this folder), `policy_types`,
`category_scope`, `reviewed_at` (YYYY-MM-DD), `policy_types_verified: true`, and
`ingest: true`. Preserve the source URL. Set `captured_at` to the actual capture
timestamp with timezone if known; leave it null if unknown. Optional `sha256`
locks the reviewed file bytes. Effective dates must come from the document.

Supported files: Markdown, text, saved HTML, and text-based PDF. Prefer reviewed
Markdown for tables and FAQ material. Keep original PDFs alongside their reviewed
transcriptions. A scanned PDF needs local OCR/text preparation; the importer does
not fetch or infer missing text.

Example metadata (illustrative, not a statement of retailer policy):

```json
{
  "local_path": "croma/croma_returns.md",
  "category_scope": "mobile_phones",
  "policy_types": ["RETURN", "REPLACEMENT"],
  "policy_types_verified": true,
  "reviewed_at": "YYYY-MM-DD",
  "captured_at": null,
  "ingest": true
}
```

Use `all_products` or `electronics` only for clauses actually applying broadly.
For mixed-category documents, set reviewed section scopes in `section_scopes`,
mapping exact headings to `category_scope` and `policy_types`. Default scope
applies only to sections not overridden. Preserve exclusions, table footnotes,
and paragraph/page markers. Do not label laptop rows as mobile-phone evidence.

Run from the repository root:

```bash
path/to/venv/bin/python scripts/policies/import_local_documents.py
```

This validates and chunks without database or embedding API calls. It reports
missing/unselected documents; it does not fill those gaps from the web.

Once the selected documents pass validation:

```bash
path/to/venv/bin/python scripts/policies/init_local_schema.py
path/to/venv/bin/python scripts/policies/import_local_documents.py --publish
```

`--publish` calls the configured embedding API, then writes PostgreSQL/Neon.
It replaces the active local build in one transaction. Older builds and legacy
policy tables remain available for audit, but are excluded from Agent 3 queries.
The first local build is required before Agent 3 can retrieve this collection.

Index/hub pages and case search portals remain collection references. Their
linked documents are not automatically included. Regulatory documents and
secondary explainers can be stored separately but are excluded from retailer
policy answers. The third-party cross-check summary is excluded entirely.
