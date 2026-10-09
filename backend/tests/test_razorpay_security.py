import hashlib
import hmac

import pytest
from fastapi import HTTPException

from app.billing.models import SubscriptionTier
from app.billing.razorpay import RazorpayClient, trusted_checkout_url, verify_webhook_signature
from app.core.config import Settings


def test_razorpay_webhook_signature_requires_exact_raw_body() -> None:
    body = b'{"event":"subscription.activated"}'
    secret = "webhook-test-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(body=body, signature=signature, secret=secret)
    assert not verify_webhook_signature(body=body + b" ", signature=signature, secret=secret)
    assert not verify_webhook_signature(body=body, signature="wrong", secret=secret)


def test_billing_client_selects_provider_plan_only_from_server_config() -> None:
    client = RazorpayClient(Settings(razorpay_pro_plan_id="plan_pro", razorpay_team_plan_id="plan_team"))

    assert client._plan_for(SubscriptionTier.PRO) == "plan_pro"
    assert client._plan_for(SubscriptionTier.TEAM) == "plan_team"


@pytest.mark.parametrize("url", ["http://rzp.io/i/example", "https://evil.example/checkout", "https://rzp.io@evil.example/"])
def test_hosted_checkout_url_cannot_be_an_open_redirect(url: str) -> None:
    with pytest.raises(HTTPException) as error:
        trusted_checkout_url(url)

    assert error.value.status_code == 502


def test_hosted_checkout_url_accepts_only_https_razorpay_domains() -> None:
    assert trusted_checkout_url("https://rzp.io/i/example") == "https://rzp.io/i/example"
    assert trusted_checkout_url("https://checkout.razorpay.com/subscription/example") == (
        "https://checkout.razorpay.com/subscription/example"
    )
