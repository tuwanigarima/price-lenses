from pathlib import Path


def test_agent3_migration_is_additive_and_contains_required_tables():
    sql = (
        Path(__file__).parents[1]
        / "scripts/db/migrations/002_eligibility_agent.sql"
    ).read_text().lower()
    for table in (
        "policy_sources",
        "policy_document_versions",
        "policy_chunks",
        "eligibility_analysis_runs",
        "offer_safety_assessments",
        "rag_index_manifests",
    ):
        assert f"create table if not exists {table}" in sql
    assert "drop table" not in sql
    assert "truncate " not in sql
    assert "delete from" not in sql
