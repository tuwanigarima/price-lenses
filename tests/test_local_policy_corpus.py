import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.local_policy_corpus import MAX_CHUNK_BYTES, category_scopes, prepare_collection
from tools.local_policy_db import LocalPolicyDatabase, embed_collection, publish_collection
from tools.policy_corpus import PolicyFetchError, _fetch_with_apify, fetch_policy_document
from tools.policy_retrieval import HybridPolicyRetriever
from tools.policy_types import chunk_supports_policy_type


def test_saved_html_preserves_table_columns_and_notes():
    from tools.policy_corpus import html_to_markdown
    _, content = html_to_markdown(
        b'<main><h1>Returns</h1><table><tr><th>Category</th><th>Condition</th></tr>'
        b'<tr><td>Phones</td><td>Example condition</td></tr></table><p>Subject to listed exceptions.</p></main>',
        "fixture",
    )
    assert "| Category | Condition |" in content
    assert "| Phones | Example condition |" in content
    assert "Subject to listed exceptions." in content


def collection(tmp_path, text=None, **changes):
    source = {
        "id": "phone_returns", "source_url": "https://example.test/returns",
        "source_kind": "document", "corpus": "retailer_policy", "publisher": "Flipkart",
        "title": "Example test policy", "local_path": "policy.md", "ingest": True,
        "policy_types": ["RETURN"], "policy_types_verified": True,
        "reviewed_at": "2026-09-27", "category_scope": "smartphone", "captured_at": None,
        **changes,
    }
    content = text or "# Mobile returns\n\n" + (
        "Example fixture: Returns require the original packaging and purchase invoice. "
        "This text is test data, not an actual retailer policy.\n\n"
    ) * 3
    (tmp_path / "policy.md").write_text(content)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"acquisition_mode": "local_files_only", "sources": [source]}))
    return prepare_collection(path, tmp_path)


def test_missing_collection_never_calls_network(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("Network must not run"))
    result = collection(tmp_path, local_path="missing.md")
    assert not result["documents"]
    assert "Local document missing" in result["errors"][0]
    for fetch in (fetch_policy_document, _fetch_with_apify):
        with pytest.raises(PolicyFetchError, match="disabled"):
            fetch("https://example.test", "Flipkart")


@pytest.mark.parametrize("changes,reason", [
    ({"local_path": "https://example.test/policy.md"}, "not a URL"),
    ({"local_path": "../outside.md"}, "inside the corpus"),
    ({"source_kind": "index"}, "Only reviewed documents"),
    ({"corpus": "cross_check_only"}, "Only reviewed documents"),
    ({"policy_types_verified": False}, "Review document scope"),
    ({"sha256": "bad"}, "File hash differs"),
    ({"captured_at": "2026-09-27T00:00:00"}, "requires a timezone"),
    ({"effective_from": "2026-10-01", "effective_until": "2026-09-01"}, "precedes"),
])
def test_invalid_selected_files_block_publication(tmp_path, changes, reason):
    prepared = collection(tmp_path, **changes)
    assert reason in prepared["errors"][0]
    with pytest.raises(ValueError):
        publish_collection(None, prepared, [], "unused")


def test_symlink_cannot_escape_corpus(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (tmp_path / "outside.md").write_text("outside")
    (corpus / "linked.md").symlink_to(tmp_path / "outside.md")
    result = collection(corpus, local_path="linked.md")
    assert "inside the corpus" in result["errors"][0]


def test_long_multibyte_clauses_are_bounded_and_reconstructible(tmp_path):
    text = "# Mobile returns\n\n" + "Return conditions 😀 and qualifying exceptions. " * 350
    prepared = collection(tmp_path, text)
    assert not prepared["errors"]
    doc = prepared["documents"][0]
    assert len(doc["chunks"]) > 1
    covered = set()
    for chunk in doc["chunks"]:
        assert len(chunk["content"].encode()) <= MAX_CHUNK_BYTES
        assert text[chunk["char_start"]:chunk["char_end"]] == chunk["excerpt"]
        covered.update(range(chunk["char_start"], chunk["char_end"]))
        assert chunk["needs_parent_context"]
    assert covered == set(range(len(text)))


def test_section_categories_do_not_mix_mobile_and_laptop(tmp_path):
    text = "# Phones\n\n" + "Return rules for phones require evidence. " * 8
    text += "\n\n# Laptops\n\n" + "Warranty rules for laptops require documentation. " * 8
    prepared = collection(tmp_path, text, category_scope="electronics", section_scopes={
        "Phones": {"category_scope": "mobile_phones", "policy_types": ["RETURN"]},
        "Laptops": {"category_scope": "laptop", "policy_types": ["WARRANTY"]},
    })
    assert not prepared["errors"]
    chunks = prepared["documents"][0]["chunks"]
    assert [(c["product_category"], c["policy_types"]) for c in chunks] == [
        ("mobile_phones", ["RETURN"]), ("laptop", ["WARRANTY"])]
    assert "laptop" not in category_scopes("smartphone")


def test_metadata_topics_do_not_fabricate_body_evidence():
    assert not chunk_supports_policy_type({
        "policy_type": "MULTI", "content": "Topics: RETURN WARRANTY",
        "evidence_text": "Shipping is handled by the carrier.",
    }, "WARRANTY")


def test_repeated_preparation_is_stable(tmp_path):
    first = collection(tmp_path)
    second = collection(tmp_path)
    assert first["corpus_hash"] == second["corpus_hash"]
    assert first["documents"][0]["captured_at"] is None


def test_reviewed_return_scope_does_not_imply_warranty_or_cancellation():
    chunk = {"policy_type": "MULTI", "policy_types": ["RETURN"],
             "evidence_text": "Returns require a warranty card. Excessive cancelling may suspend accounts."}
    assert chunk_supports_policy_type(chunk, "RETURN")
    assert not chunk_supports_policy_type(chunk, "WARRANTY")
    assert not chunk_supports_policy_type(chunk, "CANCELLATION")


@pytest.mark.parametrize("vectors", [[], [[0.0] * 1536], [[float('nan')] * 1536], [[1.0]]])
def test_invalid_embeddings_never_publish(tmp_path, vectors):
    prepared = collection(tmp_path)
    embedder = SimpleNamespace(dimensions=1536, embed=lambda _: vectors)
    with pytest.raises(ValueError):
        embed_collection(prepared, embedder)


def test_empty_published_corpus_skips_embedding_api():
    db = SimpleNamespace(build_id=None, retrieval_warnings=["LOCAL_CORPUS_NOT_READY"])
    retriever = HybridPolicyRetriever(db, SimpleNamespace())
    assert retriever.search("returns", retailers=["Flipkart"]) == []
    assert retriever.warnings == ["LOCAL_CORPUS_NOT_READY"]


def test_local_queries_are_build_corpus_category_and_date_scoped():
    db = object.__new__(LocalPolicyDatabase)
    db.build_id = "active-build"
    calls = []
    db._rows = lambda sql, params: calls.append((sql, params)) or []
    db.keyword_policy_search("return", retailers=["Flipkart"], policy_types=["RETURN"], product_category="smartphone")
    sql, params = calls[0]
    assert "policy_local.chunks" in sql and "d.build_id=%s" in sql
    assert "d.corpus='retailer_policy'" in sql
    assert "effective_until>=CURRENT_DATE" in sql
    assert "c.policy_types &&" in sql
    assert params[:2] == ("active-build", ["mobile_phones", "electronics", "all_products"])


class Transaction:
    def __init__(self, fail=False):
        self.fail, self.statements, self.committed, self.rolled_back = fail, [], False, False
        self.inserted = 0

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        self.committed = kind is None
        self.rolled_back = kind is not None

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if sql.startswith("INSERT INTO policy_local.chunks"):
            if self.fail:
                raise RuntimeError("simulated write failure")
            self.inserted += 1

    def fetchone(self):
        return (self.inserted,)


def test_publication_failure_does_not_deactivate_previous_build(tmp_path):
    prepared = collection(tmp_path)
    conn = Transaction(fail=True)
    with pytest.raises(RuntimeError):
        publish_collection(conn, prepared, [[1.0] + [0.0] * 1535], "test-model")
    assert conn.rolled_back and not conn.committed
    assert not any("SET active=FALSE" in sql for sql in conn.statements)


def test_publication_activates_only_after_all_rows_validate(tmp_path):
    prepared = collection(tmp_path)
    conn = Transaction()
    publish_collection(conn, prepared, [[1.0] + [0.0] * 1535], "test-model")
    assert conn.committed
    assert conn.statements[-2].startswith("UPDATE policy_local.builds SET active=FALSE")
    assert conn.statements[-1].startswith("UPDATE policy_local.builds SET active=TRUE")
