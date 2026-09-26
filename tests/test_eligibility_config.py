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

    assert settings.embedding_api_key == "test-only-shared-key"
    assert settings.llm_api_key == "test-only-shared-key"
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_model == "gpt-5-mini"
    assert settings.llm_enabled is True
