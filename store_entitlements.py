"""Interpret verified Google Play responses without trusting client purchase fields."""

from datetime import datetime, timezone


def google_entitlement(body, product_id, allow_sandbox=False, now=None):
    now = now or datetime.now(timezone.utc)
    free = {"tier": "free", "expires_at": 0}
    if not isinstance(body, dict):
        return free
    if "testPurchase" in body and not allow_sandbox:
        return free
    if body.get("subscriptionState") not in {
        "SUBSCRIPTION_STATE_ACTIVE",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
        "SUBSCRIPTION_STATE_CANCELED",
    }:
        return free
    expiries = []
    items = body.get("lineItems")
    if not isinstance(items, list):
        return free
    for item in items:
        if not isinstance(item, dict) or item.get("productId") != product_id:
            continue
        try:
            expiry = datetime.fromisoformat(item["expiryTime"].replace("Z", "+00:00"))
            if expiry.tzinfo is not None and expiry > now:
                expiries.append(expiry.timestamp())
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    if not expiries:
        return free
    return {"tier": "pro", "expires_at": max(expiries)}
