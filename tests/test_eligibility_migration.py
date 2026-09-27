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


def test_pgvector_migration_only_adds_embedding_storage():
    sql = (
        Path(__file__).parents[1]
        / "scripts/db/migrations/003_pgvector_policy_embeddings.sql"
    ).read_text().lower()

    assert "create extension if not exists vector" in sql
    assert "create table if not exists policy_chunk_embeddings" in sql
    assert "embedding vector(1536)" in sql
    assert "alter table" not in sql
    assert "drop table" not in sql
    assert "truncate " not in sql
    assert "delete from" not in sql
