"""Environment configuration for the local-first Eligibility Agent."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _direct_neon_url(value: str | None) -> str | None:
    url = (value or "").strip()
    if not url:
        return None
    return url.replace("-pooler.", ".", 1)


@dataclass(frozen=True)
class EligibilitySettings:
    database_url: str
    database_direct_url: str | None = None
    embedding_backend: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str | None = None
    llm_enabled: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-6-luna"
    offer_freshness_minutes: int = 60

    @classmethod
    def from_env(cls) -> "EligibilitySettings":
        load_dotenv()
        database_url = (
            os.getenv("DATABASE_URL") or os.getenv("PL_DATABASE_URL") or ""
        ).strip()
        if not database_url:
            raise ValueError("DATABASE_URL is required for Agent 3")
        backend = os.getenv("POLICY_EMBEDDINGS", "openai").strip().lower()
        if backend != "openai":
            raise ValueError("POLICY_EMBEDDINGS must be openai")
        dimensions = int(os.getenv("POLICY_EMBEDDING_DIMENSIONS", "1536"))
        if dimensions != 1536:
            raise ValueError(
                "POLICY_EMBEDDING_DIMENSIONS must be 1536 for the pgvector schema"
            )
        return cls(
            database_url=database_url,
            database_direct_url=_direct_neon_url(
                os.getenv("DATABASE_DIRECT_URL") or os.getenv("PL_NEON_DIRECT_URL")
            ),
            embedding_backend=backend,
            embedding_model=os.getenv(
                "POLICY_EMBEDDING_MODEL", "text-embedding-3-small"
            ).strip(),
            embedding_dimensions=dimensions,
            embedding_base_url=os.getenv(
                "POLICY_EMBEDDING_BASE_URL", "https://api.openai.com/v1"
            ).strip(),
            embedding_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
            llm_enabled=_boolean("ELIGIBILITY_AGENT_LLM_ENABLED"),
            llm_base_url=os.getenv(
                "ELIGIBILITY_AGENT_LLM_BASE_URL",
                os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            ).strip(),
            llm_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
            llm_model=os.getenv(
                "ELIGIBILITY_AGENT_LLM_MODEL",
                os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            ).strip(),
            offer_freshness_minutes=int(
                os.getenv("ELIGIBILITY_OFFER_FRESHNESS_MINUTES", "60")
            ),
        )
