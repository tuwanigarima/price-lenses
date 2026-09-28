"""Regressions for combining upstream ranking with exact-variant guards."""
import pytest

from orchestrator import input_resolver_node
from tools.market_matching import product_relevance


def resolve(monkeypatch, query, rows):
    from tools import analytics

    class Connection:
        def cursor(self): return self
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, *_): pass
        def fetchall(self): return rows
        def close(self): pass

    monkeypatch.setattr(analytics, "get_db_connection", Connection)
    return input_resolver_node({"query": query})


def test_matching_history_record_keeps_upstream_ranking_preference(monkeypatch):
    title = "Apple iPhone 14 256GB Blue"
    result = resolve(monkeypatch, "iPhone 14 256GB Blue", [
        ("title:duplicate", title, 0.99, False),
        ("B012345678", title, 0.8, True),
    ])
    assert result["canonical_id"] == "B012345678"


def test_history_bonus_cannot_override_requested_storage(monkeypatch):
    result = resolve(monkeypatch, "iPhone 14 256GB Blue", [
        ("B012345678", "Apple iPhone 14 128GB Blue", 0.99, True),
        ("title:correct", "Apple iPhone 14 256GB Blue", 0.8, False),
    ])
    assert result["canonical_id"] == "title:correct"


def test_history_bonus_does_not_hide_unresolved_color(monkeypatch):
    result = resolve(monkeypatch, "iPhone 14 256GB", [
        ("B012345678", "Apple iPhone 14 256GB Blue", 0.99, True),
        ("title:other", "Apple iPhone 14 256GB Pink", 0.8, False),
    ])
    assert result["canonical_id"] is None
    assert "ambiguous" in result["errors"][0]


@pytest.mark.parametrize("query,title", [
    ("Apple iPhone 15", "Note 15 5G 128GB"),
    ("Samsung Galaxy S24", "Samsung Galaxy S24 Ultra 256GB"),
    ("Samsung Galaxy S24 Ultra", "Samsung Galaxy S24 256GB"),
    ("Apple iPhone 15", "Samsung Galaxy 15 128GB"),
])
def test_upstream_family_brand_and_suffix_rejections_remain(query, title):
    assert product_relevance(query, title) == 0


def test_upstream_apple_brand_synonym_remains_supported():
    assert product_relevance("Apple iPhone 15", "iPhone 15 128GB Blue") >= 0.25
