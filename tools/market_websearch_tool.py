"""OpenAI web-search tool for price-drop intelligence (Query 3).

Uses the OpenAI Responses API with the built-in ``web_search`` tool to surface
signals that may cause the price of a product to fall in India:

  - Successor / newer-model announcements
  - Official brand price cuts or MRP revisions
  - Upcoming Indian e-commerce sale events (Big Billion Days, Great Indian
    Festival, Diwali Sale, Republic Day Sale …)
  - Market signals: overstock, discontinuation, competitive pressure

The tool is intentionally read-only and stateless.  It never writes to the
database and makes no assumptions about the offer data already collected by
the market agent.  Results are attached to the report under the key
``price_intelligence`` and a trace event is appended to ``agent_trace``.

Usage example::

    from tools.market_websearch_tool import PriceSignalWebSearchTool

    tool = PriceSignalWebSearchTool(api_key=os.environ["OPENAI_API_KEY"])
    result = tool.search("Apple iPhone 15 128 GB", deadline_days=30)
    print(result.answer)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any


# ---------------------------------------------------------------------------
# Query template — deadline_days is substituted at call time so the sale-
# event window matches the MarketAgentRequest.deadline_days field.
# ---------------------------------------------------------------------------
_QUERY_TEMPLATE = """\
Is now a good time to buy "{product}" in India, or is there any reason the \
price might drop soon? Search for the latest news and signals that could \
affect its price.

Look for the following, and report everything you find with dates:

SUCCESSOR / NEW MODEL:
1. Has a newer version or successor of "{product}" been announced or recently \
released in India? (e.g. next-generation model, upgraded variant)
2. What is its India launch date or expected release date?
3. Did the price of "{product}" already drop after the newer model announcement?

OFFICIAL PRICE CUTS:
4. Has the brand officially reduced the price of "{product}" in India recently?
5. Are there any reports of an upcoming MRP revision or permanent price cut?

UPCOMING SALE EVENTS (India):
6. Are any major Indian e-commerce sale events coming up in the next \
{deadline_days} days where "{product}" is likely to be discounted?
   (e.g. Flipkart Big Billion Days, Amazon Great Indian Festival, Republic \
Day Sale, Diwali Sale, End of Season Sale, Independence Day Sale)
7. What discounts have been historically seen on "{product}" or its category \
during such sales?

MARKET SIGNALS:
8. Are there any reports of overstock, slow sales, or distributor-level \
discounts on "{product}" in India?
9. Is the product being phased out or discontinued in India?
10. Have any competing products launched recently that may pressure the price \
of "{product}" downward?

Return only factual, sourced information with the publication date of each \
piece of news. Do not speculate or estimate prices.\
"""


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------
@dataclass
class WebSearchStep:
    """One step captured from the OpenAI Responses API output trace."""

    step_type: str                  # e.g. "web_search_call", "message", ...
    content: str | None = None      # text content when available

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.step_type, "content": self.content}


@dataclass
class PriceIntelligenceResult:
    """Full result returned by :class:`PriceSignalWebSearchTool`.

    Attributes
    ----------
    product:
        The original product string passed to :meth:`PriceSignalWebSearchTool.search`.
    query:
        The fully expanded question that was sent to the model.
    answer:
        The model's final text answer (``response.output_text``).
    steps:
        Every step in the OpenAI Responses output list, so the UI can show
        which URLs were visited, what the model thought, etc.
    fetched_at:
        ISO-8601 UTC timestamp of when the call completed.
    duration_ms:
        Wall-clock time for the entire API round-trip in milliseconds.
    model:
        The OpenAI model that was used.
    error:
        Set to a human-readable string if the call failed; ``None`` on success.
    """

    product: str
    query: str
    answer: str
    steps: list[WebSearchStep] = field(default_factory=list)
    fetched_at: str = ""
    duration_ms: float = 0.0
    model: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "query": self.query,
            "answer": self.answer,
            "steps": [step.to_dict() for step in self.steps],
            "fetched_at": self.fetched_at,
            "duration_ms": round(self.duration_ms, 1),
            "model": self.model,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
class PriceSignalWebSearchTool:
    """Wraps the OpenAI Responses API ``web_search`` built-in tool.

    Parameters
    ----------
    api_key:
        OpenAI API key.  Must not be empty.
    model:
        Model to use.  Defaults to ``"gpt-4o-mini"`` which supports the
        built-in ``web_search`` tool and is cost-efficient for this task.
    base_url:
        Override the OpenAI API base URL (useful for proxies or local stubs).
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
    ) -> None:
        if not api_key or api_key.strip() in {"", "not-needed"}:
            raise ValueError(
                "PriceSignalWebSearchTool requires a valid OPENAI_API_KEY"
            )
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-4o-mini"
        self.base_url = (base_url or "").strip() or None

    # ------------------------------------------------------------------
    def search(
        self,
        product: str,
        *,
        deadline_days: int = 60,
    ) -> PriceIntelligenceResult:
        """Run the price-drop intelligence query for *product*.

        Parameters
        ----------
        product:
            Human-readable product name as entered by the user
            (e.g. ``"Apple iPhone 15 128 GB"``).
        deadline_days:
            How far into the future to look for sale events.  Passed directly
            from :attr:`~tools.market_agent_models.MarketAgentRequest.deadline_days`.

        Returns
        -------
        PriceIntelligenceResult
            Always returns a result object — on API failure the ``error`` field
            is populated and ``answer`` is empty so callers never receive an
            exception.
        """
        from openai import OpenAI  # lazy import — keeps startup fast

        started_at = perf_counter()
        query = _QUERY_TEMPLATE.format(
            product=product,
            deadline_days=max(1, int(deadline_days)),
        )

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)

        try:
            response = client.responses.create(
                model=self.model,
                tools=[{"type": "web_search"}],
                input=query,
            )

            steps: list[WebSearchStep] = []
            for item in response.output or []:
                content = (
                    getattr(item, "output_text", None)
                    or getattr(item, "text", None)
                    or getattr(item, "content", None)
                )
                # content can be a list of content blocks (message items)
                if isinstance(content, list):
                    content = " ".join(
                        getattr(block, "text", str(block)) for block in content
                    )
                steps.append(WebSearchStep(step_type=item.type, content=content))

            return PriceIntelligenceResult(
                product=product,
                query=query,
                answer=response.output_text or "",
                steps=steps,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=(perf_counter() - started_at) * 1000,
                model=self.model,
            )

        except Exception as exc:  # never crash the outer report pipeline
            return PriceIntelligenceResult(
                product=product,
                query=query,
                answer="",
                fetched_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=(perf_counter() - started_at) * 1000,
                model=self.model,
                error=self._safe_error(exc),
            )

    # ------------------------------------------------------------------
    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """Return a user-safe error string without leaking request bodies."""
        safe = {
            "AuthenticationError": "OpenAI authentication failed — check OPENAI_API_KEY",
            "PermissionDeniedError": "OpenAI key cannot access the configured model",
            "NotFoundError": "Configured OpenAI model or endpoint not found",
            "RateLimitError": "OpenAI rate limit or quota exceeded",
            "APITimeoutError": "OpenAI request timed out",
            "APIConnectionError": "Could not connect to OpenAI endpoint",
            "BadRequestError": "OpenAI rejected the model request",
        }
        return safe.get(type(exc).__name__, type(exc).__name__)
