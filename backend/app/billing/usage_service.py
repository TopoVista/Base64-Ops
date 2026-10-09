"""Atomic, tenant-scoped usage metering for token-expensive agent runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from fastapi import HTTPException, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.billing.models import SubscriptionStatus, SubscriptionTier, UsageEntitlement
from app.core.config import Settings, get_settings
from app.db.mongo import get_db
from app.utils.datetime import utc_now


def monthly_period_key(now: datetime | None = None) -> str:
    value = now or utc_now()
    return value.astimezone(UTC).strftime("%Y-%m")


def resolve_tier(user: dict[str, Any]) -> SubscriptionTier:
    """Only active/trialing server-side subscriptions unlock paid quotas."""

    raw_tier = str(user.get("subscriptionTier", SubscriptionTier.FREE)).lower()
    raw_status = str(user.get("subscriptionStatus", SubscriptionStatus.ACTIVE)).lower()
    try:
        tier = SubscriptionTier(raw_tier)
    except ValueError:
        return SubscriptionTier.FREE
    if tier is SubscriptionTier.FREE:
        return tier
    if raw_status not in {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}:
        return SubscriptionTier.FREE
    return tier


def agent_run_limit(tier: SubscriptionTier, settings: Settings | None = None) -> int:
    config = settings or get_settings()
    return {
        SubscriptionTier.FREE: config.free_monthly_agent_runs,
        SubscriptionTier.PRO: config.pro_monthly_agent_runs,
        SubscriptionTier.TEAM: config.team_monthly_agent_runs,
    }[tier]


def model_token_limit(tier: SubscriptionTier, settings: Settings | None = None) -> int:
    config = settings or get_settings()
    return {
        SubscriptionTier.FREE: config.free_monthly_model_tokens,
        SubscriptionTier.PRO: config.pro_monthly_model_tokens,
        SubscriptionTier.TEAM: config.team_monthly_model_tokens,
    }[tier]


def request_key_hash(request_id: str) -> str:
    """Never persist a browser-supplied idempotency value in raw form."""
    return sha256(request_id.encode("utf-8")).hexdigest()


def reported_token_count(value: Any) -> int:
    """Normalize provider usage without accepting negative/invalid counters."""

    if isinstance(value, bool):
        return 0
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, count)


class UsageService:
    """Consumes one bounded agent-run entitlement before graph execution.

    The record's unique key is ``(userId, period, meter)``.  Ensure this index
    is installed during production provisioning; ``ensure_indexes`` is safe to
    call repeatedly and invoked during application startup.
    """

    async def ensure_indexes(self) -> None:
        db = get_db()
        await db.usage_ledgers.create_index(
            [("userId", 1), ("period", 1), ("meter", 1)],
            unique=True,
            name="usage_ledger_tenant_period_meter",
        )
        await db.agent_run_requests.create_index(
            [("userId", 1), ("requestKeyHash", 1)],
            unique=True,
            name="agent_run_request_tenant_idempotency",
        )
        await db.agent_run_requests.create_index("expiresAt", expireAfterSeconds=0)

    async def consume_agent_run(self, user: dict[str, Any], *, request_id: str | None = None) -> UsageEntitlement:
        db = get_db()
        tier = resolve_tier(user)
        limit = agent_run_limit(tier)
        token_limit = model_token_limit(tier)
        period = monthly_period_key()
        filter_base = {"userId": str(user["_id"]), "period": period, "meter": "agent_run"}
        now = utc_now()
        request_filter: dict[str, str] | None = None
        if request_id:
            request_filter = {"userId": str(user["_id"]), "requestKeyHash": request_key_hash(request_id)}
            try:
                await db.agent_run_requests.insert_one(
                    {
                        **request_filter,
                        "period": period,
                        "meter": "agent_run",
                        "createdAt": now,
                        "expiresAt": now + timedelta(seconds=get_settings().agent_run_idempotency_ttl_seconds),
                    }
                )
            except DuplicateKeyError:
                # Re-running a request after a browser/SSE failure must never
                # produce a second graph invocation or consume quota again.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "AGENT_RUN_REQUEST_ALREADY_ACCEPTED",
                        "message": "This agent request was already accepted. Refresh the session to view its result.",
                    },
                ) from None

        token_ledger = await db.usage_ledgers.find_one(
            {"userId": str(user["_id"]), "period": period, "meter": "model_tokens"},
            {"totalTokens": 1},
        )
        consumed_tokens = min(token_limit, reported_token_count((token_ledger or {}).get("totalTokens")))
        if consumed_tokens >= token_limit:
            if request_filter:
                await db.agent_run_requests.delete_one(request_filter)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "MODEL_TOKEN_QUOTA_EXCEEDED",
                    "message": (
                        "Your monthly model-token allowance has been reached. "
                        "Upgrade or retry next billing period."
                    ),
                    "period": period,
                    "limit": token_limit,
                    "consumed": consumed_tokens,
                },
            )

        # Create the ledger idempotently first. The second atomic update only
        # succeeds while usage stays within entitlement, including under
        # concurrent browser tabs or multiple API instances.
        await db.usage_ledgers.update_one(
            filter_base,
            {
                "$setOnInsert": {**filter_base, "count": 0, "createdAt": now},
                "$set": {"updatedAt": now, "tierAtConsumption": tier.value},
            },
            upsert=True,
        )
        ledger = await db.usage_ledgers.find_one_and_update(
            {**filter_base, "count": {"$lt": limit}},
            {"$inc": {"count": 1}, "$set": {"updatedAt": now, "tierAtConsumption": tier.value}},
            return_document=ReturnDocument.AFTER,
        )
        if ledger is None:
            if request_filter:
                await db.agent_run_requests.delete_one(request_filter)
            current = await db.usage_ledgers.find_one(filter_base, {"count": 1})
            consumed = int((current or {}).get("count", limit))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "AGENT_RUN_QUOTA_EXCEEDED",
                    "message": "Your monthly agent-run limit has been reached. Upgrade or retry next billing period.",
                    "period": period,
                    "limit": limit,
                    "consumed": consumed,
                },
            )
        consumed = int(ledger["count"])
        return UsageEntitlement(
            tier=tier,
            monthly_agent_run_limit=limit,
            period_key=period,
            consumed_agent_runs=consumed,
            remaining_agent_runs=max(0, limit - consumed),
            monthly_model_token_limit=token_limit,
            consumed_model_tokens=consumed_tokens,
            remaining_model_tokens=max(0, token_limit - consumed_tokens),
        )

    async def record_model_usage(self, user_id: str, usage: dict[str, Any] | None) -> None:
        """Aggregate provider-reported usage for tenant cost observability.

        This is deliberately post-run telemetry, not a pricing calculator or
        client-controlled quota. Invocation remains bounded before the model
        by agent-run entitlements, context/output ceilings, and a tenant lease.
        Missing provider usage is represented by no write instead of a fake
        estimate that could distort billing decisions.
        """

        if not isinstance(usage, dict):
            return
        prompt_tokens = reported_token_count(usage.get("prompt_tokens", usage.get("input_tokens")))
        completion_tokens = reported_token_count(usage.get("completion_tokens", usage.get("output_tokens")))
        total_tokens = reported_token_count(usage.get("total_tokens")) or (prompt_tokens + completion_tokens)
        if total_tokens <= 0:
            return
        model = str(usage.get("model", ""))[:100]
        now = utc_now()
        period = monthly_period_key(now)
        await get_db().usage_ledgers.update_one(
            {"userId": str(user_id), "period": period, "meter": "model_tokens"},
            {
                "$setOnInsert": {
                    "userId": str(user_id),
                    "period": period,
                    "meter": "model_tokens",
                    "promptTokens": 0,
                    "completionTokens": 0,
                    "totalTokens": 0,
                    "recordedRuns": 0,
                    "createdAt": now,
                },
                "$inc": {
                    "promptTokens": prompt_tokens,
                    "completionTokens": completion_tokens,
                    "totalTokens": total_tokens,
                    "recordedRuns": 1,
                },
                "$set": {"updatedAt": now, "lastModel": model},
            },
            upsert=True,
        )
