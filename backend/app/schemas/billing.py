from pydantic import BaseModel

from app.billing.models import SubscriptionTier, UsageEntitlement


class CreateCheckoutRequest(BaseModel):
    tier: SubscriptionTier


class CheckoutResponse(BaseModel):
    provider: str
    subscriptionId: str
    checkoutUrl: str
    keyId: str


class EntitlementResponse(UsageEntitlement):
    pass
