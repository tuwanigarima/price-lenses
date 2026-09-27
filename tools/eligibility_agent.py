"""Agent 3: India retailer policy and purchase-protection analysis."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable

from .eligibility_models import PolicyAgentRequest, PolicyHit, PolicyProtectionReport
from .policy_corpus import usable_policy_content
from .policy_types import chunk_supports_policy_type
from .policy_report_verifier import verify_policy_report


SummaryWriter = Callable[[dict[str, Any]], str]


class PolicyProtectionAgent:
    """Retrieve and verify retailer policy evidence without reading offers."""

    def __init__(
        self,
        database: Any,
        retriever: Any | None = None,
        *,
        freshness_minutes: int | None = None,
        summary_writer: SummaryWriter | None = None,
        enable_llm_summary: bool = False,
        llm_settings: Any | None = None,
        trace_callback: Any = None,
    ):
        self.database = database
        self.retriever = retriever
        self.summary_writer = summary_writer
        self.enable_llm_summary = enable_llm_summary
        self.llm_settings = llm_settings
        self.trace_callback = trace_callback


    def _append_trace(self, trace_list: list, event: dict):
        trace_list.append(event)
        if getattr(self, "trace_callback", None):
            try:
                self.trace_callback(event)
            except Exception:
                # Observability must never make the policy analysis fail.
                pass

    def analyze(self, request: PolicyAgentRequest) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []
        context = {
            "product_category": request.product_category,
            "country_code": request.country_code,
            "retailers": list(request.retailers),
            "policy_types": list(request.policy_types),
            "offer_independent": True,
        }
        analysis_id = self.database.start_analysis(
            request.query, request.canonical_id, context
        )
        report = PolicyProtectionReport(
            analysis_id=analysis_id,
            canonical_id=request.canonical_id,
            analysis_timestamp=datetime.now(timezone.utc).isoformat(),
            product_category=request.product_category,
            country_code=request.country_code,
            retailers_evaluated=list(request.retailers),
            agent_trace=trace,
        )
        try:
            hits: list[dict[str, Any]] = []
            policy_plan = self._plan_policy_searches(request, trace)
            for planned_search in policy_plan:
                retailer = planned_search["retailer"]
                question = planned_search["question"]
                started = perf_counter()
                retailer_hits = self._retrieve_retailer_policies(
                    request, retailer, question
                )
                hits.extend(retailer_hits)
                self._append_trace(trace, self._trace(
                        "retrieve_retailer_policy_evidence",
                        "search_retailer_policies",
                        started,
                        f"Retrieved {len(retailer_hits)} active policy chunks for {retailer}",
                        input_summary=(
                            f"Retailer: {retailer}; category: {request.product_category}; "
                            f"question: {question}"
                        ),
                    ) | {"source": "PostgreSQL FTS + pgvector"}
                )

            profiles_started = perf_counter()
            profiles = self._build_profiles(request, hits)
            report.policy_profiles = profiles
            cited_ids = {
                str(citation["chunk_id"])
                for profile in profiles
                for policy in profile["policies"]
                for citation in policy["citations"]
            }
            report.evidence_chunk_count = len(cited_ids)
            report.evidence_gap_count = sum(
                len(profile["evidence_gaps"]) for profile in profiles
            )
            if not cited_ids:
                report.status = "insufficient_evidence"
                report.warnings.append(
                    "No active official policy evidence was retrieved."
                )
            elif report.evidence_gap_count:
                report.status = "partial"
                report.warnings.append(
                    "Some retailer policy types have no active official evidence."
                )
            else:
                report.status = "complete"

            report.cross_retailer_observations = self._cross_retailer_observations(
                profiles
            )
            self._append_trace(
                trace,
                self._trace(
                    "build_policy_profiles",
                    "classify_policy_coverage",
                    profiles_started,
                    (
                        f"Built {len(profiles)} retailer profiles from "
                        f"{len(hits)} retrieved chunks with "
                        f"{report.evidence_gap_count} evidence gaps"
                    ),
                    input_summary=(
                        f"{len(request.retailers)} retailers and "
                        f"{len(request.policy_types)} requested policy types"
                    ),
                ),
            )
            summary_started = perf_counter()
            (
                report.summary,
                summary_status,
                summary_source,
                summary_output,
            ) = self._summary(report.to_dict())
            self._append_trace(trace, {
                    **self._trace(
                        "generate_policy_summary",
                        "summarize_policy_profiles",
                        summary_started,
                        summary_output,
                        input_summary=(
                            f"{len(profiles)} retailer policy profiles and "
                            f"{report.evidence_chunk_count} cited chunks"
                        ),
                    ),
                    "source": summary_source,
                    "status": summary_status,
                    "display_prompt": self._summary_display_prompt(),
                })
            active_chunks = self.database.policy_chunks_by_ids(cited_ids)
            verification_started = perf_counter()
            verify_policy_report(report.to_dict(), active_chunks)
            self._append_trace(
                trace,
                self._trace(
                    "verify_policy_grounding",
                    "verify_policy_report",
                    verification_started,
                    (
                        f"Verified {report.evidence_chunk_count} unique citations "
                        "against active official policy chunks"
                    ),
                    input_summary=(
                        f"{len(profiles)} retailer profiles and "
                        f"{report.evidence_gap_count} declared evidence gaps"
                    ),
                ),
            )
            report.agent_trace = trace
            self.database.finish_analysis(
                analysis_id, status=report.status, warnings=report.warnings
            )
            return report.to_dict()
        except Exception as exc:
            self.database.finish_analysis(
                analysis_id, status="error", warnings=report.warnings, error=str(exc)
            )
            raise

    def _retrieve_retailer_policies(
        self, request: PolicyAgentRequest, retailer: str, question: str
    ) -> list[dict[str, Any]]:
        if self.retriever is None:
            return []
        results = self.retriever.search(
            question,
            retailers=[retailer],
            policy_types=list(request.policy_types),
            product_category=request.product_category,
            limit=12,
        )
        normalized = [
            item.to_dict() if isinstance(item, PolicyHit) else dict(item)
            for item in results
        ]
        return [item for item in normalized if self._usable_policy_hit(item)]

    @staticmethod
    def _usable_policy_hit(hit: dict[str, Any]) -> bool:
        return usable_policy_content(str(hit.get("content") or ""))

    def _plan_policy_searches(
        self, request: PolicyAgentRequest, trace: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        started = perf_counter()
        default = [
            {
                "retailer": retailer,
                "question": (
                    f"{request.product_category} return replacement cancellation "
                    "warranty purchase protection and FAQ rules"
                ),
            }
            for retailer in request.retailers
        ]
        display_prompt = (
            "Plan one official-policy search for every supplied Indian retailer. "
            "Use the search_retailer_policies tool with only an allowed retailer and "
            "a concise question covering the requested policy types. Do not answer "
            "policy questions from memory."
        )
        if not self.enable_llm_summary or not self.llm_settings:
            self._append_trace(trace, {
                    **self._trace(
                        "plan_policy_investigation",
                        "search_retailer_policies",
                        started,
                        "LLM planning disabled; deterministic retailer search plan used",
                        input_summary=f"{len(request.retailers)} supported retailers",
                    ),
                    "source": "deterministic fallback",
                    "status": "skipped",
                    "display_prompt": display_prompt,
                })
            return default
        try:
            from langchain_openai import ChatOpenAI

            if not self.llm_settings.llm_api_key:
                raise ValueError("OPENAI_API_KEY is not configured")
            tool_schema = {
                "type": "function",
                "function": {
                    "name": "search_retailer_policies",
                    "description": (
                        "Search approved official Indian retailer policy chunks."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "retailer": {
                                "type": "string",
                                "enum": list(request.retailers),
                            },
                            "question": {"type": "string"},
                        },
                        "required": ["retailer", "question"],
                        "additionalProperties": False,
                    },
                },
            }
            llm = ChatOpenAI(
                model=self.llm_settings.llm_model,
                api_key=self.llm_settings.llm_api_key,
                base_url=self.llm_settings.llm_base_url,
                temperature=0,
            ).bind_tools([tool_schema])
            response = llm.invoke(
                display_prompt
                + "\nProduct category: "
                + request.product_category
                + "\nRetailers: "
                + ", ".join(request.retailers)
                + "\nPolicy types: "
                + ", ".join(request.policy_types)
            )
            planned: dict[str, str] = {}
            for call in getattr(response, "tool_calls", []) or []:
                if call.get("name") != "search_retailer_policies":
                    continue
                args = call.get("args") or {}
                retailer = str(args.get("retailer") or "")
                question = " ".join(str(args.get("question") or "").split())
                if retailer in request.retailers and question:
                    planned[retailer] = question[:500]
            searches = [
                {
                    "retailer": item["retailer"],
                    "question": planned.get(item["retailer"], item["question"]),
                }
                for item in default
            ]
            self._append_trace(trace, {
                    **self._trace(
                        "plan_policy_investigation",
                        "search_retailer_policies",
                        started,
                        f"LLM planned {len(planned)} retailer policy tool calls; mandatory coverage was preserved",
                        input_summary=f"{len(request.retailers)} supported retailers",
                    ),
                    "source": "llm",
                    "status": "completed",
                    "display_prompt": display_prompt,
                })
            return searches
        except Exception as exc:
            self._append_trace(trace, {
                    **self._trace(
                        "plan_policy_investigation",
                        "search_retailer_policies",
                        started,
                        f"LLM planning unavailable; deterministic plan used ({type(exc).__name__})",
                        input_summary=f"{len(request.retailers)} supported retailers",
                    ),
                    "source": "deterministic fallback",
                    "status": "fallback",
                    "display_prompt": display_prompt,
                }
            )
            return default

    @staticmethod
    def _build_profiles(
        request: PolicyAgentRequest, hits: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for retailer in request.retailers:
            retailer_hits = [
                hit for hit in hits if str(hit.get("retailer")) == retailer
            ]
            policies: list[dict[str, Any]] = []
            gaps: list[str] = []
            for policy_type in request.policy_types:
                matches = [
                    hit
                    for hit in retailer_hits
                    if chunk_supports_policy_type(hit, policy_type)
                ]
                citations = [
                    {
                        "chunk_id": str(hit["chunk_id"]),
                        "source_url": hit.get("source_url"),
                        "heading_path": hit.get("heading_path"),
                        "relevance": hit.get("relevance"),
                        "retrieval_sources": list(hit.get("retrieval_sources") or []),
                    }
                    for hit in matches[:3]
                ]
                if matches:
                    content = " ".join(str(matches[0].get("content") or "").split())
                    policies.append(
                        {
                            "policy_type": policy_type,
                            "status": "EVIDENCED",
                            "summary": content[:500],
                            "confidence": (
                                0.85
                                if len(matches[0].get("retrieval_sources") or []) > 1
                                else 0.65
                            ),
                            "citations": citations,
                        }
                    )
                else:
                    gaps.append(policy_type)
                    policies.append(
                        {
                            "policy_type": policy_type,
                            "status": "MISSING",
                            "summary": "No active official evidence was retrieved.",
                            "confidence": 0.0,
                            "citations": [],
                        }
                    )
            profiles.append(
                {
                    "retailer": retailer,
                    "policies": policies,
                    "evidence_gaps": gaps,
                    "risk_flags": (
                        ["Listing-specific terms must still be confirmed"]
                        + (["Official policy evidence is incomplete"] if gaps else [])
                    ),
                }
            )
        return profiles

    @staticmethod
    def _cross_retailer_observations(
        profiles: list[dict[str, Any]],
    ) -> list[str]:
        complete = [
            profile["retailer"]
            for profile in profiles
            if not profile.get("evidence_gaps")
        ]
        if not complete:
            return [
                "No retailer has complete evidence for every requested policy type."
            ]
        return [
            "Complete requested policy coverage is available for: "
            + ", ".join(complete)
        ]

    def _summary(self, payload: dict[str, Any]) -> tuple[str, str, str, str]:
        deterministic = self._deterministic_summary(payload)
        if self.summary_writer:
            try:
                return (
                    self.summary_writer(payload),
                    "completed",
                    "configured summary writer",
                    "Generated a citation-grounded retailer policy summary.",
                )
            except Exception as exc:
                return (
                    deterministic,
                    "fallback",
                    "deterministic fallback",
                    f"Summary writer failed; used deterministic summary: {exc}",
                )
        if not self.enable_llm_summary or not self.llm_settings:
            return (
                deterministic,
                "skipped",
                "deterministic fallback",
                "LLM summary is disabled; used the deterministic policy summary.",
            )
        try:
            from langchain_openai import ChatOpenAI

            if not self.llm_settings.llm_api_key:
                return (
                    deterministic,
                    "skipped",
                    "deterministic fallback",
                    "No OpenAI API key is configured; used the deterministic summary.",
                )
            llm = ChatOpenAI(
                model=self.llm_settings.llm_model,
                api_key=self.llm_settings.llm_api_key,
                base_url=self.llm_settings.llm_base_url,
                temperature=0,
            )
            summary_payload = self._compact_summary_payload(
                payload.get("policy_profiles") or []
            )
            prompt = self._summary_display_prompt() + "\n\n" + json.dumps(
                summary_payload, default=str
            )
            content = str(llm.invoke(prompt).content).strip()
            retailer_names = [
                str(profile.get("retailer") or "")
                for profile in summary_payload
                if profile.get("retailer")
            ]
            if content and all(
                retailer.lower() in content.lower() for retailer in retailer_names
            ):
                return (
                    content,
                    "completed",
                    "LLM",
                    "Generated a citation-grounded retailer policy summary.",
                )
            reason = "empty" if not content else "incomplete"
            return (
                deterministic,
                "fallback",
                "deterministic fallback",
                f"The LLM returned an {reason} retailer summary; used the deterministic summary.",
            )
        except Exception as exc:
            return (
                deterministic,
                "fallback",
                "deterministic fallback",
                f"LLM summary failed; used deterministic summary: {exc}",
            )

    @staticmethod
    def _summary_display_prompt() -> str:
        return (
            "You are PriceLens Agent 3, an Indian retailer policy and purchase-protection "
            "analyst. Summarize only the supplied citation-grounded policy profiles in "
            "at most 200 words. Include one concise sentence for every supplied retailer "
            "and explicitly distinguish evidenced policy types from missing types. Explain "
            "return, replacement, cancellation, warranty and FAQ gaps. Do not discuss "
            "prices, rank offers, invent rules, or issue BUY_NOW/WAIT."
        )

    @staticmethod
    def _compact_summary_payload(
        profiles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep every retailer in the LLM prompt without dumping full RAG payloads."""
        compact: list[dict[str, Any]] = []
        for profile in profiles:
            policies = []
            for policy in profile.get("policies") or []:
                policies.append(
                    {
                        "policy_type": policy.get("policy_type"),
                        "status": policy.get("status"),
                        "summary": str(policy.get("summary") or "")[:350],
                        "source_urls": list(
                            dict.fromkeys(
                                str(citation.get("source_url") or "")
                                for citation in policy.get("citations") or []
                                if citation.get("source_url")
                            )
                        )[:2],
                    }
                )
            compact.append(
                {
                    "retailer": profile.get("retailer"),
                    "policies": policies,
                    "evidence_gaps": list(profile.get("evidence_gaps") or []),
                    "risk_flags": list(profile.get("risk_flags") or []),
                }
            )
        return compact

    @staticmethod
    def _deterministic_summary(payload: dict[str, Any]) -> str:
        profiles = payload.get("policy_profiles") or []
        gaps = int(payload.get("evidence_gap_count") or 0)
        retailer_results: list[str] = []
        for profile in profiles:
            evidenced = [
                str(policy.get("policy_type"))
                for policy in profile.get("policies") or []
                if policy.get("status") == "EVIDENCED"
            ]
            missing = list(profile.get("evidence_gaps") or [])
            retailer_results.append(
                f"{profile.get('retailer')}: evidenced "
                f"{', '.join(evidenced) if evidenced else 'none'}; missing "
                f"{', '.join(missing) if missing else 'none'}."
            )
        return (
            f"Agent 3 reviewed active policy evidence for {len(profiles)} Indian "
            f"retailers and found {gaps} missing retailer-policy combinations. "
            + " ".join(retailer_results)
            + " "
            "These are retailer-level protections; listing-specific terms must still "
            "be confirmed before purchase."
        )

    @staticmethod
    def _trace(
        stage: str,
        tool: str,
        started: float,
        output: str,
        *,
        input_summary: str,
    ) -> dict[str, Any]:
        return {
            "stage": stage,
            "tool": tool,
            "source": "deterministic",
            "status": "completed",
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "input_summary": input_summary,
            "output_summary": output,
        }


EligibilitySafetyAgent = PolicyProtectionAgent
