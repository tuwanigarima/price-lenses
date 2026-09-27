from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_dashboard_has_one_unified_product_input(monkeypatch):
    # Initial rendering does not connect or call providers; it only validates config.
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/pricelens")

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    # A clean CI environment may need extra time for first-time LangGraph imports.
    app = AppTest.from_file(app_path, default_timeout=60).run()

    assert not app.exception
    assert not app.tabs
    assert [title.value for title in app.title] == [
        "🔍 PriceLens: Autonomous Deal Advisor"
    ]
    assert [field.label for field in app.text_input] == [
        "Product name, ASIN, or product URL"
    ]
