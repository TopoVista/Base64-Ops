from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError

from app.billing import billing_service
from app.billing.billing_service import BillingService
from app.billing.models import SubscriptionTier
from app.core.config import Settings


async def test_duplicate_checkout_returns_the_existing_hosted_checkout(monkeypatch) -> None:
    class Checkouts:
        async def insert_one(self, _document):  # noqa: ANN001, ANN201
            raise DuplicateKeyError("active checkout")

        async def find_one(self, _query):  # noqa: ANN001, ANN201
            return {
                "tier": "pro",
                "status": "created",
                "providerSubscriptionId": "sub_existing",
                "checkoutUrl": "https://rzp.io/i/checkout",
            }

    db = SimpleNamespace(billing_checkouts=Checkouts())
    monkeypatch.setattr(billing_service, "get_db", lambda: db)
    monkeypatch.setattr(billing_service, "get_settings", lambda: Settings(razorpay_key_id="rzp_key"))

    result = await BillingService().create_checkout(user={"_id": "user-a"}, tier=SubscriptionTier.PRO)

    assert result == {
        "provider": "razorpay",
        "subscriptionId": "sub_existing",
        "checkoutUrl": "https://rzp.io/i/checkout",
        "keyId": "rzp_key",
    }


async def test_old_subscription_cancellation_cannot_downgrade_newer_provider_subscription(monkeypatch) -> None:
    class Checkouts:
        updates: list[tuple[dict, dict]] = []

        async def find_one(self, _query):  # noqa: ANN001, ANN201
            return {
                "_id": "checkout-old",
                "userId": "user-a",
                "tier": "pro",
                "providerSubscriptionId": "sub_old",
            }

        async def update_one(self, query, update):  # noqa: ANN001, ANN201
            self.updates.append((query, update))

    class Events:
        async def update_one(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
            return SimpleNamespace(upserted_id="event-new")

    class Users:
        updates: list[tuple[dict, dict]] = []

        async def update_one(self, query, update):  # noqa: ANN001, ANN201
            self.updates.append((query, update))

    checkouts = Checkouts()
    users = Users()
    db = SimpleNamespace(billing_checkouts=checkouts, billing_webhook_events=Events(), users=users)
    monkeypatch.setattr(billing_service, "get_db", lambda: db)

    await BillingService().process_webhook(
        body=b'{"event":"subscription.cancelled"}',
        event={
            "event": "subscription.cancelled",
            "payload": {"subscription": {"entity": {"id": "sub_old", "status": "cancelled", "notes": {}}}},
        },
    )

    assert users.updates[0][0] == {"_id": "user-a", "subscriptionProviderSubscriptionId": "sub_old"}
    assert checkouts.updates[0][1]["$unset"] == {"activeCheckoutKey": ""}


async def test_stale_provider_event_cannot_downgrade_a_newer_subscription_state(monkeypatch) -> None:
    class Checkouts:
        updates: list[tuple[dict, dict]] = []

        async def find_one(self, _query):  # noqa: ANN001, ANN201
            return {
                "_id": "checkout-current",
                "userId": "user-a",
                "tier": "pro",
                "providerSubscriptionId": "sub_current",
                "providerEventCreatedAt": 200,
            }

        async def update_one(self, query, update):  # noqa: ANN001, ANN201
            self.updates.append((query, update))
            return SimpleNamespace(modified_count=0)

    class Events:
        async def update_one(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
            return SimpleNamespace(upserted_id="event-new")

    class Users:
        updates: list[tuple[dict, dict]] = []

        async def update_one(self, query, update):  # noqa: ANN001, ANN201
            self.updates.append((query, update))

    checkouts = Checkouts()
    users = Users()
    monkeypatch.setattr(
        billing_service,
        "get_db",
        lambda: SimpleNamespace(billing_checkouts=checkouts, billing_webhook_events=Events(), users=users),
    )

    await BillingService().process_webhook(
        body=b'{"event":"payment.failed","created_at":100}',
        event={
            "event": "payment.failed",
            "created_at": 100,
            "payload": {"subscription": {"entity": {"id": "sub_current", "status": "halted", "notes": {}}}},
        },
    )

    assert checkouts.updates[0][0]["$or"][1] == {"providerEventCreatedAt": {"$lt": 100}}
    assert not users.updates
