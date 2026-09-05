import unittest
from datetime import datetime, timezone

from store_entitlements import google_entitlement

NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)
PRODUCT = 'vocare_pro_monthly'


def purchase(state='ACTIVE', product=PRODUCT, expiry='2026-10-05T00:00:00Z'):
    return {'subscriptionState': 'SUBSCRIPTION_STATE_' + state,
            'lineItems': [{'productId': product, 'expiryTime': expiry}]}


class GoogleEntitlementTests(unittest.TestCase):
    def check_tier(self, body, expected, sandbox=False):
        result = google_entitlement(body, PRODUCT, sandbox, NOW)
        self.assertEqual(result['tier'], expected)
        if expected == 'pro':
            self.assertGreater(result['expires_at'], NOW.timestamp())

    def test_paid_states_preserve_access(self):
        for state in ['ACTIVE', 'IN_GRACE_PERIOD', 'CANCELED']:
            with self.subTest(state=state):
                self.check_tier(purchase(state), 'pro')

    def test_unpaid_states_do_not_grant_access(self):
        for state in ['PENDING', 'PAUSED', 'ON_HOLD', 'EXPIRED', 'PENDING_PURCHASE_CANCELED', 'UNKNOWN']:
            with self.subTest(state=state):
                self.check_tier(purchase(state), 'free')

    def test_other_product_cannot_unlock_pro(self):
        self.check_tier(purchase(product='another_subscription'), 'free')

    def test_expired_active_and_cancelled_receipts_are_rejected(self):
        for state in ['ACTIVE', 'CANCELED']:
            self.check_tier(purchase(state, expiry='2026-09-04T23:59:59Z'), 'free')

    def test_missing_malformed_and_unzoned_expiry_rejected(self):
        for expiry in [None, '', 'garbage', 42, '2026-10-05T00:00:00']:
            with self.subTest(expiry=expiry):
                self.check_tier(purchase(expiry=expiry), 'free')

    def test_expiry_boundary(self):
        self.check_tier(purchase(expiry='2026-09-05T00:00:00Z'), 'free')

    def test_sandbox_requires_explicit_enablement(self):
        body = {**purchase(), 'testPurchase': {}}
        self.check_tier(body, 'free')
        self.check_tier(body, 'pro', sandbox=True)

    def test_expiry_must_belong_to_pro_product(self):
        body = purchase(expiry='2026-09-04T00:00:00Z')
        body['lineItems'] += purchase(product='other')['lineItems']
        self.check_tier(body, 'free')

    def test_malformed_response_is_not_a_purchase(self):
        for body in [None, [], {}, {'subscriptionState': 'SUBSCRIPTION_STATE_ACTIVE'}]:
            self.check_tier(body, 'free')


if __name__ == '__main__':
    unittest.main()
