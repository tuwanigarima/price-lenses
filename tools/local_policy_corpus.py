"""Parse an explicitly curated local corpus. This module never fetches URLs."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path

from .policy_corpus import html_to_markdown, pdf_to_text, usable_policy_content

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data/policies/curated_manifest.json"
CORPUS_ROOT = ROOT / "data/policies/corpus"
CHUNKER_VERSION = "local-structure-v1"
MAX_CHUNK_BYTES = 2400  # Hard UTF-8 cap; roughly 300–600 English tokens, not a token count.


def category_name(value: str) -> str:
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    return {"smartphone": "mobile_phones", "smartphones": "mobile_phones",
            "phone": "mobile_phones", "mobile": "mobile_phones",
            "mobile_phone": "mobile_phones", "cell_phone": "mobile_phones"}.get(key, key)


def category_scopes(value: str | None) -> list[str]:
    category = category_name(value or "electronics")
    return list(dict.fromkeys([category, "electronics", "all_products"]))


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _date(value, field: str) -> str | None:
    if value is None:
        return None
    date.fromisoformat(str(value))
    return str(value)


def _within(root: Path, name: str) -> Path:
    if not name or "://" in name:
        raise ValueError("local_path must identify a saved file, not a URL")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("local_path must stay inside the corpus folder")
    if not path.is_file():
        raise ValueError(f"Local document missing: {name}")
    return path


def _sections(text: str):
    """Yield intact heading sections with character offsets into normalized text."""
    heading, start, offset = "Document", 0, 0
    for line in text.splitlines(keepends=True):
        if re.match(r"^#{1,6}\s+", line):
            if text[start:offset].strip():
                yield heading, start, offset, text[start:offset]
            heading = line.lstrip("#").strip()
            start = offset
        offset += len(line)
    if text[start:offset].strip():
        yield heading, start, offset, text[start:offset]


def _pieces(text: str, budget: int):
    """Bounded source spans; favor paragraphs/sentences and overlap long units."""
    start = 0
    while start < len(text):
        end = start
        used = 0
        while end < len(text) and used + len(text[end].encode("utf-8")) <= budget:
            used += len(text[end].encode("utf-8"))
            end += 1
        if end == len(text):
            yield start, end
            return
        lower = start + max(1, (end - start) // 2)
        boundaries = [m.end() for m in re.finditer(r"\n\s*\n|[.!?]\s+", text[start:end])]
        split = next((start + pos for pos in reversed(boundaries) if start + pos >= lower), end)
        yield start, split
        # Around 50 English words; the full section is retained separately.
        start = max(start + 1, split - min(200, (split - start) // 4))


def chunk_local_document(document: dict) -> list[dict]:
    chunks = []
    for heading, section_start, section_end, section in _sections(document["content"]):
        scope = (document.get("section_scopes") or {}).get(heading, {})
        category = category_name(scope.get("category_scope") or document["category_scope"])
        topics = scope.get("policy_types") or document["policy_types"]
        prefix = (f"Retailer: {document['publisher']}\n"
                  f"Topics: {', '.join(topics)}\n"
                  f"Category: {category}\nSection: {heading}\n\n")
        budget = MAX_CHUNK_BYTES - len(prefix.encode("utf-8"))
        if budget < 400:
            raise ValueError("Document metadata/heading is too long for a useful chunk")
        # Sections containing tables retain headers and footnotes as full parent
        # context. Small sections, including FAQ Q&A, stay intact.
        for start, end in _pieces(section, budget):
            excerpt = section[start:end]
            if not excerpt.strip():
                continue
            content = prefix + excerpt
            chunks.append({
                "content": content, "content_sha256": _hash(content.encode()),
                "excerpt": excerpt, "heading_path": heading,
                "char_start": section_start + start, "char_end": section_start + end,
                "parent_start": section_start, "parent_end": section_end,
                "needs_parent_context": len(section.encode()) > budget,
                "policy_types": topics,
                "product_category": category,
            })
    return chunks


def prepare_collection(manifest_path=DEFAULT_MANIFEST, corpus_root=CORPUS_ROOT) -> dict:
    """Read files and validate the manifest without API or database access."""
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get("acquisition_mode") != "local_files_only":
        raise ValueError("Only local_files_only acquisition is supported")
    if any(manifest.get(key) for key in ("web_search_enabled", "web_fetch_enabled", "apify_enabled", "follow_links")):
        raise ValueError("Network acquisition must remain disabled")
    documents, errors, pending = [], [], []
    ids, urls = set(), set()
    for entry in manifest["sources"]:
        ident = entry["id"]
        if ident in ids or entry["source_url"] in urls:
            errors.append(f"{ident}: duplicate source ID or URL")
            continue
        ids.add(ident)
        urls.add(entry["source_url"])
        if not entry.get("ingest"):
            pending.append({"id": ident, "status": entry.get("status", "not_selected")})
            continue
        try:
            if entry["source_kind"] != "document" or entry["corpus"] in {"cross_check_only", "case_law"}:
                raise ValueError("Only reviewed documents may be indexed; portals and excluded sources cannot")
            if not entry.get("policy_types_verified") or not entry.get("reviewed_at"):
                raise ValueError("Review document scope and set policy_types_verified/reviewed_at first")
            topics = entry.get("policy_types") or []
            if not topics or any(not re.fullmatch(r"[A-Z_]+", item) for item in topics):
                raise ValueError("Explicit reviewed policy_types are required")
            category = entry.get("category_scope")
            if not category:
                raise ValueError("Explicit category_scope is required; mixed pages need reviewed category extracts")
            captured = entry.get("captured_at")
            if captured is not None:
                instant = datetime.fromisoformat(captured.replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    raise ValueError("captured_at requires a timezone")
            _date(entry["reviewed_at"], "reviewed_at")
            effective_from = _date(entry.get("effective_from"), "effective_from")
            effective_until = _date(entry.get("effective_until"), "effective_until")
            if effective_from and effective_until and effective_until < effective_from:
                raise ValueError("effective_until precedes effective_from")
            path = _within(Path(corpus_root), entry.get("local_path") or "")
            raw = path.read_bytes()
            digest = _hash(raw)
            if entry.get("sha256") and entry["sha256"] != digest:
                raise ValueError("File hash differs from the reviewed manifest")
            suffix = path.suffix.lower()
            if suffix in {".md", ".txt"}:
                title, content = entry["title"], raw.decode("utf-8")
            elif suffix in {".html", ".htm"}:
                title, content = html_to_markdown(raw, entry["title"])
            elif suffix == ".pdf":
                title, content = pdf_to_text(raw, entry["title"])
            else:
                raise ValueError("Supported local formats: .md, .txt, .html, .pdf")
            if not usable_policy_content(content, minimum_characters=200):
                raise ValueError("No usable policy text; scanned PDFs require a local text/OCR copy")
            headings = [item[0] for item in _sections(content)]
            for heading, scope in (entry.get("section_scopes") or {}).items():
                if headings.count(heading) != 1:
                    raise ValueError(f"Section scope must match one unique heading: {heading}")
                if not isinstance(scope, dict) or not scope.get("category_scope"):
                    raise ValueError(f"Section scope requires category_scope: {heading}")
                if "policy_types" in scope and (not scope["policy_types"] or any(not re.fullmatch(r"[A-Z_]+", x) for x in scope["policy_types"])):
                    raise ValueError(f"Invalid section policy_types: {heading}")
            document = {**entry, "title": title, "content": content,
                        "raw_sha256": digest, "content_sha256": _hash(content.encode()),
                        "category_scope": category_name(category), "policy_types": topics,
                        "effective_from": effective_from, "effective_until": effective_until}
            document["chunks"] = chunk_local_document(document)
            documents.append(document)
        except Exception as exc:
            # A bad PDF or malformed entry is a collection error, never a reason
            # to publish the remainder silently or acquire remote content.
            errors.append(f"{ident}: {exc}")
    if not documents:
        errors.append("No reviewed local policy documents are ready. No index can be published.")
    fingerprint = json.dumps({"version": CHUNKER_VERSION, "documents": documents}, sort_keys=True)
    return {"documents": documents, "errors": errors, "pending": pending,
            "corpus_hash": _hash(fingerprint.encode()), "chunker_version": CHUNKER_VERSION}
