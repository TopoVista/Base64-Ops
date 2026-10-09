from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.billing import usage_service
from app.billing.models import SubscriptionTier
from app.billing.usage_service import (
    UsageService,
    agent_run_limit,
    model_token_limit,
    monthly_period_key,
    reported_token_count,
    request_key_hash,
    resolve_tier,
)
from app.core.config import Settings


def test_inactive_paid_subscription_cannot_unlock_paid_agent_quota() -> None:
    assert resolve_tier({"subscriptionTier": "team", "subscriptionStatus": "past_due"}) is SubscriptionTier.FREE
    assert resolve_tier({"subscriptionTier": "pro", "subscriptionStatus": "trialing"}) is SubscriptionTier.PRO


def test_unknown_or_missing_tier_fails_to_the_free_entitlement() -> None:
    assert resolve_tier({"subscriptionTier": "enterprise-admin"}) is SubscriptionTier.FREE
    assert resolve_tier({}) is SubscriptionTier.FREE


def test_agent_run_limits_come_from_server_configuration() -> None:
    config = Settings(
        free_monthly_agent_runs=3,
        pro_monthly_agent_runs=30,
        team_monthly_agent_runs=300,
        free_monthly_model_tokens=300,
        pro_monthly_model_tokens=3_000,
        team_monthly_model_tokens=30_000,
    )

    assert agent_run_limit(SubscriptionTier.FREE, config) == 3
    assert agent_run_limit(SubscriptionTier.PRO, config) == 30
    assert agent_run_limit(SubscriptionTier.TEAM, config) == 300
    assert model_token_limit(SubscriptionTier.FREE, config) == 300
    assert model_token_limit(SubscriptionTier.PRO, config) == 3_000


def test_period_key_is_utc_month_stable() -> None:
    assert monthly_period_key().count("-") == 1


def test_agent_request_idempotency_key_is_hashed_before_persistence() -> None:
    raw_request_id = "3677b9d2-b6b9-45d8-8fa6-2e167c205589"
    hashed = request_key_hash(raw_request_id)

    assert raw_request_id not in hashed
    assert len(hashed) == 64


def test_provider_usage_normalization_rejects_invalid_or_negative_counts() -> None:
    assert reported_token_count(12) == 12
    assert reported_token_count("15") == 15
    assert reported_token_count(-1) == 0
    assert reported_token_count(True) == 0
    assert reported_token_count("not-a-token-count") == 0


async def test_duplicate_agent_request_does_not_consume_a_second_entitlement(monkeypatch) -> None:
    class DuplicateRequests:
        async def insert_one(self, _document):  # noqa: ANN001, ANN201
            raise DuplicateKeyError("duplicate")

    class UsageLedgers:
        async def update_one(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
            raise AssertionError("A duplicate request must not reach the quota ledger")

    monkeypatch.setattr(
        usage_service,
        "get_db",
        lambda: SimpleNamespace(agent_run_requests=DuplicateRequests(), usage_ledgers=UsageLedgers()),
    )

    with pytest.raises(HTTPException) as exc:
        await UsageService().consume_agent_run({"_id": "user-a"}, request_id="request-a")

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "AGENT_RUN_REQUEST_ALREADY_ACCEPTED"


async def test_monthly_model_token_cap_rejects_the_next_agent_run(monkeypatch) -> None:
    deleted: list[dict] = []

    class Requests:
        async def insert_one(self, _document):  # noqa: ANN001, ANN201
            return None

        async def delete_one(self, query):  # noqa: ANN001, ANN201
            deleted.append(query)

    class UsageLedgers:
        async def find_one(self, query, _projection):  # noqa: ANN001, ANN201
            assert query["meter"] == "model_tokens"
            return {"totalTokens": 50}

    monkeypatch.setattr(
        usage_service,
        "get_db",
        lambda: SimpleNamespace(agent_run_requests=Requests(), usage_ledgers=UsageLedgers()),
    )
    monkeypatch.setattr(
        usage_service,
        "get_settings",
        lambda: Settings(free_monthly_model_tokens=50),
    )

    with pytest.raises(HTTPException) as exc:
        await UsageService().consume_agent_run({"_id": "user-a"}, request_id="request-a")

    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "MODEL_TOKEN_QUOTA_EXCEEDED"
    assert deleted == [{"userId": "user-a", "requestKeyHash": request_key_hash("request-a")}]


async def test_model_usage_is_aggregated_only_from_provider_reported_tokens(monkeypatch) -> None:
    writes: list[tuple[dict, dict, dict]] = []

    class UsageLedgers:
        async def update_one(self, query, update, **kwargs):  # noqa: ANN001, ANN201
            writes.append((query, update, kwargs))

    monkeypatch.setattr(usage_service, "get_db", lambda: SimpleNamespace(usage_ledgers=UsageLedgers()))

    await UsageService().record_model_usage(
        "user-a",
        {"model": "gpt-5.6-terra", "prompt_tokens": 300, "completion_tokens": 40},
    )
    await UsageService().record_model_usage("user-a", {"total_tokens": "invalid"})

    assert len(writes) == 1
    query, update, options = writes[0]
    assert query["userId"] == "user-a"
    assert query["meter"] == "model_tokens"
    assert update["$inc"] == {
        "promptTokens": 300,
        "completionTokens": 40,
        "totalTokens": 340,
        "recordedRuns": 1,
    }
    assert update["$set"]["lastModel"] == "gpt-5.6-terra"
    assert options["upsert"] is True
