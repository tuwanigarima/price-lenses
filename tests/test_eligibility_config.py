from tools.eligibility_config import EligibilitySettings


def test_agent3_and_policy_embeddings_use_shared_openai_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/pricelens")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-shared-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")
    monkeypatch.setenv("POLICY_EMBEDDINGS", "openai")
    monkeypatch.setenv("ELIGIBILITY_AGENT_LLM_ENABLED", "true")
    monkeypatch.delenv("ELIGIBILITY_AGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ELIGIBILITY_AGENT_LLM_MODEL", raising=False)

    settings = EligibilitySettings.from_env()

    assert settings.embedding_backend == "openai"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 1536
    assert settings.embedding_api_key == "test-only-shared-key"
    assert settings.llm_api_key == "test-only-shared-key"
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_model == "gpt-5-mini"
    assert settings.llm_enabled is True


def test_agent3_rejects_non_openai_embedding_backend(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/pricelens")
    monkeypatch.setenv("POLICY_EMBEDDINGS", "local")

    try:
        EligibilitySettings.from_env()
    except ValueError as exc:
        assert "must be openai" in str(exc)
    else:
        raise AssertionError("Agent 3 accepted a non-OpenAI embedding backend")


def test_agent3_derives_direct_neon_endpoint_from_pooler(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:secret@ep-example-pooler.us-east-2.aws.neon.tech/neondb",
    )
    monkeypatch.setenv(
        "DATABASE_DIRECT_URL",
        "postgresql://user:secret@ep-example-pooler.us-east-2.aws.neon.tech/neondb",
    )
    monkeypatch.setenv("POLICY_EMBEDDINGS", "openai")

    settings = EligibilitySettings.from_env()

    assert settings.database_direct_url == (
        "postgresql://user:secret@ep-example.us-east-2.aws.neon.tech/neondb"
    )
