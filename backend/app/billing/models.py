from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class SubscriptionTier(StrEnum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class UsageEntitlement(BaseModel):
    tier: SubscriptionTier
    monthly_agent_run_limit: int
    period_key: str
    consumed_agent_runs: int
    remaining_agent_runs: int
    monthly_model_token_limit: int
    consumed_model_tokens: int
    remaining_model_tokens: int
