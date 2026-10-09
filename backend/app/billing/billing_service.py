"""Subscription persistence and webhook-driven entitlement changes."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.billing.models import SubscriptionStatus, SubscriptionTier, UsageEntitlement
from app.billing.razorpay import RazorpayClient, trusted_checkout_url
from app.billing.usage_service import (
    UsageService,
    agent_run_limit,
    model_token_limit,
    monthly_period_key,
    reported_token_count,
    resolve_tier,
)
from app.core.config import get_settings
from app.db.mongo import get_db
from app.utils.datetime import utc_now
from app.utils.ids import new_id


def provider_event_created_at(event: dict[str, Any]) -> int | None:
    """Return Razorpay's top-level event time when it is a valid epoch.

    The subscription entity's timestamps describe subscription lifecycle, not
    necessarily the webhook emission. Only the top-level event timestamp is
    used for ordering; missing metadata remains supported for compatibility.
    """

    value = event.get("created_at")
    if isinstance(value, bool):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


class BillingService:
    def __init__(self, *, client: RazorpayClient | None = None, usage_service: UsageService | None = None) -> None:
        self._client = client or RazorpayClient()
        self._usage = usage_service or UsageService()

    async def ensure_indexes(self) -> None:
        db = get_db()
        await db.billing_checkouts.create_index("providerSubscriptionId", unique=True, sparse=True)
        await db.billing_checkouts.create_index("checkoutReference", unique=True)
        # At most one unfinished provider checkout may exist for a user. This
        # prevents double-click/retry races from creating duplicate recurring
        # subscriptions at the payment provider.
        await db.billing_checkouts.create_index(
            "activeCheckoutKey",
            unique=True,
            sparse=True,
            name="billing_checkout_one_active_per_user",
        )
        await db.billing_webhook_events.create_index("createdAt", expireAfterSeconds=60 * 60 * 24 * 90)

    async def entitlement(self, user: dict[str, Any]) -> UsageEntitlement:
        db = get_db()
        tier = resolve_tier(user)
        period = monthly_period_key()
        entry = await db.usage_ledgers.find_one(
            {"userId": str(user["_id"]), "period": period, "meter": "agent_run"}, {"count": 1}
        )
        token_entry = await db.usage_ledgers.find_one(
            {"userId": str(user["_id"]), "period": period, "meter": "model_tokens"}, {"totalTokens": 1}
        )
        limit = agent_run_limit(tier)
        consumed = min(limit, int((entry or {}).get("count", 0)))
        token_limit = model_token_limit(tier)
        consumed_tokens = min(token_limit, reported_token_count((token_entry or {}).get("totalTokens")))
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

    async def create_checkout(self, *, user: dict[str, Any], tier: SubscriptionTier) -> dict[str, str]:
        db = get_db()
        now = utc_now()
        checkout_id = new_id("checkout_")
        checkout_reference = new_id("checkout_ref_")
        # Persist a random server-created reference before contacting the
        # provider. If Razorpay delivers a fast webhook, it can be safely
        # matched and bound even before the API response is written back.
        user_id = str(user["_id"])
        checkout = {
            "_id": checkout_id,
            "checkoutReference": checkout_reference,
            "userId": user_id,
            "tier": tier.value,
            "provider": "razorpay",
            "status": "creating",
            "activeCheckoutKey": user_id,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await db.billing_checkouts.insert_one(checkout)
        except DuplicateKeyError:
            existing = await db.billing_checkouts.find_one({"activeCheckoutKey": user_id})
            if (
                existing
                and existing.get("tier") == tier.value
                and existing.get("status") == "created"
                and existing.get("providerSubscriptionId")
                and existing.get("checkoutUrl")
            ):
                return self._checkout_response(existing)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A subscription checkout is already in progress or active. Manage it before changing plans.",
            ) from None
        try:
            hosted = await self._client.create_subscription(
                tier=tier,
                user_id=user_id,
                checkout_reference=checkout_reference,
            )
        except Exception:
            await db.billing_checkouts.update_one(
                {"_id": checkout_id},
                {
                    "$set": {"status": "provider_error", "updatedAt": utc_now()},
                    "$unset": {"activeCheckoutKey": ""},
                },
            )
            raise
        await db.billing_checkouts.update_one(
            {"_id": checkout_id},
            {
                "$set": {
                    "providerSubscriptionId": hosted.provider_subscription_id,
                    "checkoutUrl": hosted.checkout_url or "",
                    "status": "created",
                    "updatedAt": utc_now(),
                }
            },
        )
        return self._checkout_response(
            {
                "providerSubscriptionId": hosted.provider_subscription_id,
                "checkoutUrl": hosted.checkout_url or "",
            }
        )

    @staticmethod
    def _checkout_response(checkout: dict[str, Any]) -> dict[str, str]:
        return {
            "provider": "razorpay",
            "subscriptionId": str(checkout["providerSubscriptionId"]),
            "checkoutUrl": trusted_checkout_url(checkout.get("checkoutUrl")),
            # This key is publishable; it is intentionally not a secret.
            "keyId": get_settings().razorpay_key_id,
        }

    async def process_webhook(self, *, body: bytes, event: dict[str, Any]) -> bool:
        """Process an already-HMAC-verified event once; return whether it was new."""

        db = get_db()
        subscription = ((event.get("payload") or {}).get("subscription") or {}).get("entity") or {}
        provider_id = str(subscription.get("id", ""))
        if not provider_id:
            # This is a verified, irrelevant provider event. Retain only the
            # dedupe marker, not raw payment/customer payloads.
            return True
        notes = subscription.get("notes") or {}
        checkout_reference = str(notes.get("base64_checkout_ref", ""))
        checkout = await db.billing_checkouts.find_one(
            {
                "provider": "razorpay",
                "$or": [
                    {"providerSubscriptionId": provider_id},
                    {"checkoutReference": checkout_reference, "status": {"$in": ["creating", "created"]}},
                ],
            }
        )
        if not checkout:
            # A valid webhook for another merchant subscription cannot grant
            # access merely through notes or a guessed user id.
            return True

        # A matching server record must exist before the event is deduplicated
        # or changes entitlement. This handles provider webhook/API races
        # without trusting a user ID placed in provider notes.
        event_key = "rzp_evt_" + hashlib.sha256(body).hexdigest()
        inserted = await db.billing_webhook_events.update_one(
            {"_id": event_key},
            {"$setOnInsert": {"_id": event_key, "createdAt": utc_now(), "event": str(event.get("event", ""))}},
            upsert=True,
        )
        if inserted.upserted_id is None:
            return False

        provider_status = str(subscription.get("status", "")).lower()
        event_name = str(event.get("event", ""))
        if provider_status == "active" or event_name in {"subscription.activated", "subscription.resumed"}:
            subscription_status = SubscriptionStatus.ACTIVE
            tier = SubscriptionTier(str(checkout["tier"]))
        elif event_name in {"subscription.paused", "payment.failed"} or provider_status in {"halted", "paused"}:
            subscription_status = SubscriptionStatus.PAST_DUE
            tier = SubscriptionTier.FREE
        elif event_name in {"subscription.cancelled", "subscription.completed"} or provider_status in {
            "cancelled",
            "completed",
        }:
            subscription_status = SubscriptionStatus.CANCELED
            tier = SubscriptionTier.FREE
        else:
            return True

        now = utc_now()
        event_created_at = provider_event_created_at(event)
        # A canceled provider subscription is terminal. A delayed activation
        # event cannot resurrect it after cancellation.
        if (
            checkout.get("subscriptionStatus") == SubscriptionStatus.CANCELED.value
            and subscription_status is SubscriptionStatus.ACTIVE
        ):
            return True
        checkout_filter: dict[str, Any] = {"_id": checkout["_id"]}
        if event_created_at is not None:
            # Apply provider events monotonically. This conditional write is
            # essential: two workers may process webhook deliveries at once,
            # so an in-memory timestamp comparison would not be sufficient.
            checkout_filter["$or"] = [
                {"providerEventCreatedAt": {"$exists": False}},
                {"providerEventCreatedAt": {"$lt": event_created_at}},
            ]
        update_result = await db.billing_checkouts.update_one(
            checkout_filter,
            {
                "$set": {
                    "providerSubscriptionId": provider_id,
                    "status": provider_status or event_name,
                    "subscriptionStatus": subscription_status.value,
                    "updatedAt": now,
                    **({"providerEventCreatedAt": event_created_at} if event_created_at is not None else {}),
                },
                **(
                    {"$unset": {"activeCheckoutKey": ""}}
                    if subscription_status is SubscriptionStatus.CANCELED
                    else {}
                ),
            },
        )
        if event_created_at is not None and getattr(update_result, "modified_count", 1) == 0:
            # The checkout already observed a newer provider event. Retain
            # the dedupe marker but never downgrade the user from stale data.
            return True
        user_update = {
            "subscriptionTier": tier.value,
            "subscriptionStatus": subscription_status.value,
            "subscriptionProvider": "razorpay",
            "subscriptionUpdatedAt": now,
        }
        if subscription_status is SubscriptionStatus.ACTIVE:
            user_update["subscriptionProviderSubscriptionId"] = provider_id
            user_filter = {"_id": checkout["userId"]}
        else:
            # Never let an event from an older subscription revoke a newer
            # plan. The activation event above binds the current provider ID.
            user_filter = {"_id": checkout["userId"], "subscriptionProviderSubscriptionId": provider_id}
        await db.users.update_one(user_filter, {"$set": user_update})
        return True
