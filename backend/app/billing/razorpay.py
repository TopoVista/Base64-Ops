"""Minimal Razorpay Subscriptions adapter; no payment secret reaches a client."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, status

from app.billing.models import SubscriptionTier
from app.core.config import Settings, get_settings

_BASE_URL = "https://api.razorpay.com/v1"
_TRUSTED_CHECKOUT_HOSTS = ("rzp.io", "razorpay.com")


def verify_webhook_signature(*, body: bytes, signature: str, secret: str) -> bool:
    """Validate Razorpay's HMAC against the exact raw request bytes."""

    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def trusted_checkout_url(value: object) -> str:
    """Validate the provider's hosted redirect before it reaches a browser.

    A checkout URL is response data from an external provider, not a redirect
    authority. Restrict it to HTTPS Razorpay-owned hosts so a malformed
    response, cache entry, or test configuration cannot create an open
    redirect from an authenticated billing page.
    """

    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing provider returned an invalid checkout response.",
        )
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    trusted = any(host == domain or host.endswith(f".{domain}") for domain in _TRUSTED_CHECKOUT_HOSTS)
    if (
        parsed.scheme != "https"
        or not host
        or not trusted
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing provider returned an invalid checkout response.",
        )
    return value


@dataclass(frozen=True)
class HostedSubscription:
    provider_subscription_id: str
    checkout_url: str | None


class RazorpayClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _plan_for(self, tier: SubscriptionTier) -> str:
        plan_id = {
            SubscriptionTier.PRO: self._settings.razorpay_pro_plan_id,
            SubscriptionTier.TEAM: self._settings.razorpay_team_plan_id,
        }.get(tier, "")
        if not plan_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Billing is not configured for the selected plan.",
            )
        return plan_id

    def configured(self) -> bool:
        return bool(
            self._settings.razorpay_key_id
            and self._settings.razorpay_key_secret
            and self._settings.razorpay_webhook_secret
            and self._settings.razorpay_pro_plan_id
            and self._settings.razorpay_team_plan_id
        )

    async def create_subscription(
        self, *, tier: SubscriptionTier, user_id: str, checkout_reference: str
    ) -> HostedSubscription:
        if tier is SubscriptionTier.FREE:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Free does not need checkout.")
        if not self.configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Billing is not configured yet.",
            )

        payload = {
            "plan_id": self._plan_for(tier),
            "total_count": 120,
            "customer_notify": 1,
            # It is a correlation hint only. Webhooks are authorized against a
            # server-created checkout record, never against these notes alone.
            "notes": {
                "base64_user_id": user_id,
                "base64_tier": tier.value,
                "base64_checkout_ref": checkout_reference,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.post(
                    f"{_BASE_URL}/subscriptions",
                    auth=(self._settings.razorpay_key_id, self._settings.razorpay_key_secret),
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Billing provider is temporarily unavailable. Please retry shortly.",
            ) from exc

        if not isinstance(data, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Billing provider returned an invalid response.",
            )
        subscription_id = str(data.get("id", ""))
        if not subscription_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Billing provider returned an invalid response.",
            )
        return HostedSubscription(
            provider_subscription_id=subscription_id,
            checkout_url=trusted_checkout_url(data.get("short_url")),
        )
