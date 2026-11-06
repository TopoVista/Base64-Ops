"""Server-owned data-boundary routing for all repository model inference.

This module is intentionally independent of the agent graph.  It receives a
small, trusted repository classification captured during GitHub authorization,
selects a configured route deterministically, and exposes only safe provenance
for traces and receipts.  It never accepts a route from browser input and it
never falls back from a sovereign/blocked route to a hosted provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_openai import ChatOpenAI

from app.core.config import Settings, get_settings

RepositoryClassification = Literal["public", "private", "regulated", "restricted"]
RouteKind = Literal["hosted_openai", "sovereign_openai_compatible", "deterministic_only"]

_CLASSIFICATIONS = frozenset({"public", "private", "regulated", "restricted"})
_ROUTES = frozenset({"hosted_openai", "sovereign_openai_compatible", "deterministic_only"})


@dataclass(frozen=True)
class ModelRoute:
    """A resolved route without credentials or endpoint internals."""

    classification: RepositoryClassification
    kind: RouteKind
    provider: str
    model: str | None
    data_boundary: str
    available: bool
    reason: str

    def provenance(self) -> dict[str, str | bool | None]:
        """Return the safe subset suitable for traces, replay, and browser UI."""

        return {
            "classification": self.classification,
            "route": self.kind,
            "provider": self.provider,
            "model": self.model,
            "dataBoundary": self.data_boundary,
            "available": self.available,
            "reason": self.reason,
        }


class ModelRoutingService:
    """Resolve and construct only policy-authorized model clients."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @staticmethod
    def classify_repository(session: dict[str, object] | None) -> RepositoryClassification:
        """Use persisted server-derived classification; default conservatively.

        ``repositoryClassification`` is set from GitHub's authoritative
        ``private`` field when a session is created.  Higher classifications
        are tenant policy data and may only be written by server-side control
        paths.  Missing/legacy repository visibility is treated as private.
        """

        value = (session or {}).get("repositoryClassification")
        if isinstance(value, str) and value.lower() in _CLASSIFICATIONS:
            return value.lower()  # type: ignore[return-value]
        if (session or {}).get("repositoryVisibility") == "public":
            return "public"
        return "private"

    def resolve(self, classification: str | None) -> ModelRoute:
        safe_classification: RepositoryClassification = (
            classification.lower()  # type: ignore[assignment]
            if isinstance(classification, str) and classification.lower() in _CLASSIFICATIONS
            else "private"
        )
        configured = {
            "public": self.settings.model_routing_public_route,
            "private": self.settings.model_routing_private_route,
            "regulated": self.settings.model_routing_regulated_route,
            "restricted": self.settings.model_routing_restricted_route,
        }[safe_classification].strip().lower()
        route: RouteKind = configured if configured in _ROUTES else "deterministic_only"  # type: ignore[assignment]
        if route == "hosted_openai":
            return ModelRoute(
                classification=safe_classification,
                kind=route,
                provider="openai",
                model=self.settings.chat_model or None,
                data_boundary="hosted_provider",
                available=bool(self.settings.openai_api_key),
                reason="configured_hosted_route" if self.settings.openai_api_key else "hosted_provider_unconfigured",
            )
        if route == "sovereign_openai_compatible":
            available = bool(self.settings.model_sovereign_base_url and self.settings.model_sovereign_model)
            return ModelRoute(
                classification=safe_classification,
                kind=route,
                provider="customer_managed_openai_compatible",
                model=self.settings.model_sovereign_model or None,
                data_boundary="customer_or_local_boundary",
                available=available,
                reason="configured_sovereign_route" if available else "sovereign_provider_unconfigured",
            )
        return ModelRoute(
            classification=safe_classification,
            kind="deterministic_only",
            provider="none",
            model=None,
            data_boundary="no_external_model_egress",
            available=False,
            reason="policy_requires_deterministic_analysis",
        )

    def create_chat_model(
        self,
        route: ModelRoute,
        *,
        temperature: float,
        max_tokens: int,
    ) -> ChatOpenAI:
        """Construct a client only for the selected route, otherwise fail closed."""

        if not route.available:
            raise RuntimeError("No policy-authorized model route is available.")
        common = {
            "model": route.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self.settings.llm_timeout_seconds,
            "max_retries": self.settings.llm_max_retries,
        }
        if route.kind == "hosted_openai":
            return ChatOpenAI(api_key=self.settings.openai_api_key, **common)
        if route.kind == "sovereign_openai_compatible":
            return ChatOpenAI(
                api_key=self.settings.model_sovereign_api_key or "not-required",
                base_url=self.settings.model_sovereign_base_url,
                **common,
            )
        raise RuntimeError("Deterministic-only policy does not permit model creation.")
