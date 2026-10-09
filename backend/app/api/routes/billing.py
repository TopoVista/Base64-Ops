from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_current_user
from app.billing.billing_service import BillingService
from app.billing.razorpay import verify_webhook_signature
from app.core.config import get_settings
from app.schemas.billing import CheckoutResponse, CreateCheckoutRequest, EntitlementResponse

router = APIRouter()
service = BillingService()


@router.get("/entitlement", response_model=EntitlementResponse)
async def entitlement(user: dict = Depends(get_current_user)) -> dict:
    return (await service.entitlement(user)).model_dump()


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(payload: CreateCheckoutRequest, user: dict = Depends(get_current_user)) -> dict:
    return await service.create_checkout(user=user, tier=payload.tier)


@router.post("/webhooks/razorpay", include_in_schema=False)
async def razorpay_webhook(request: Request) -> dict[str, bool]:
    body = await request.body()
    settings = get_settings()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_webhook_signature(body=body, signature=signature, secret=settings.razorpay_webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature.")
    try:
        event = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload.") from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload.")
    return {"processed": await service.process_webhook(body=body, event=event)}
