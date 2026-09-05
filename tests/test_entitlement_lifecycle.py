"""Exercise the actual bot ledger functions without starting voice-provider clients."""
import ast
import asyncio
import logging
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


def ledger():
    names = {'_billing_period', '_entitlement_key', '_tier_seconds', '_blank_record',
             '_read_entitlement', '_write_entitlement', '_balance_payload',
             '_entitlement_subject', '_entitlement_gate', 'activate_entitlement'}
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in selected:
        node.decorator_list = []
    ctx = dict(datetime=datetime, timezone=timezone, time=time, Request=object,
               logger=logging.getLogger('test'), ENTITLEMENT_ENFORCED=True, STORE_ALLOW_SANDBOX=False,
               PAID_TIER_SECONDS=3600, FREE_TIER_SECONDS=0, _ENTITLEMENT_PREFIX='test',
               _entitlement_memory={}, _entitlement_client=lambda: None,
               _verify_purchase=AsyncMock(return_value=None),
               JSONResponse=lambda content, status_code: SimpleNamespace(content=content, status_code=status_code))
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'bot-ledger', 'exec'), ctx)
    return ctx


class EntitlementLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ctx = ledger()

    def saved(self, **updates):
        record = dict(tier='pro', period=datetime.now(timezone.utc).strftime('%Y-%m'), used=120,
                      expires_at=time.time() + 3600, next_check=0, receipt='test-token', platform='android')
        record.update(updates)
        self.ctx['_entitlement_memory']['install'] = record

    async def test_refund_is_rechecked_server_side(self):
        self.saved()
        self.ctx['_verify_purchase'].return_value = {'tier': 'free', 'expires_at': 0}
        record, _ = await self.ctx['_read_entitlement']('install')
        self.assertEqual(record['tier'], 'free')
        self.ctx['_verify_purchase'].assert_awaited_once_with('android', 'test-token')

    async def test_transient_failure_preserves_unexpired_paid_access(self):
        self.saved()
        record, _ = await self.ctx['_read_entitlement']('install')
        self.assertEqual(record['tier'], 'pro')
        self.assertEqual(record['used'], 120)

    async def test_transient_failure_cannot_extend_expired_access(self):
        self.saved(expires_at=time.time() - 1)
        record, _ = await self.ctx['_read_entitlement']('install')
        self.assertEqual(record['tier'], 'free')

    async def test_new_month_preserves_receipt_and_expiry_but_resets_usage(self):
        self.saved(period='2020-01', next_check=time.time() + 300)
        record, _ = await self.ctx['_read_entitlement']('install')
        self.assertEqual(record['used'], 0)
        self.assertEqual(record['receipt'], 'test-token')
        self.assertEqual(record['tier'], 'pro')

    async def test_verified_renewal_updates_expiry(self):
        self.saved(expires_at=time.time() - 1)
        self.ctx['_verify_purchase'].return_value = {'tier': 'pro', 'expires_at': time.time() + 3600, 'purchase_key': 'store:qa'}
        record, _ = await self.ctx['_read_entitlement']('install')
        self.assertEqual(record['tier'], 'pro')

    async def test_enforced_gate_cannot_be_bypassed_by_omitting_subject(self):
        response = await self.ctx['_entitlement_gate'](None)
        self.assertEqual(response.status_code, 402)
        self.ctx['ENTITLEMENT_ENFORCED'] = False
        self.assertIsNone(await self.ctx['_entitlement_gate'](None))

    async def test_restore_preserves_consumed_minutes(self):
        self.saved(next_check=time.time() + 300)
        self.ctx['_verify_purchase'].return_value = {'tier': 'pro', 'expires_at': time.time() + 3600, 'purchase_key': 'store:qa'}
        request = SimpleNamespace(json=AsyncMock(return_value={'subject': 'install', 'receipt': 'test-token', 'platform': 'android'}))
        result = await self.ctx['activate_entitlement'](request)
        self.assertTrue(result['verified'])
        self.assertEqual(result['seconds_used'], 120)

    async def test_verification_outage_is_not_reported_as_purchase_success(self):
        self.saved(next_check=time.time() + 300)
        request = SimpleNamespace(json=AsyncMock(return_value={'subject': 'install', 'receipt': 'test-token', 'platform': 'android'}))
        result = await self.ctx['activate_entitlement'](request)
        self.assertFalse(result['verified'])
        self.assertEqual(result['tier'], 'pro')

    async def test_invalid_receipt_shape_returns_bad_request(self):
        request = SimpleNamespace(json=AsyncMock(return_value={'subject': 'install', 'receipt': {}, 'platform': 'android'}))
        result = await self.ctx['activate_entitlement'](request)
        self.assertEqual(result.status_code, 400)

    async def activate(self, subject, receipt='test-token', reachable=True):
        return await self.ctx['activate_entitlement'](SimpleNamespace(json=AsyncMock(return_value={
            'subject': subject, 'platform': 'android', 'receipt': receipt, 'store_reachable': reachable,
        })))

    async def test_reinstall_shares_purchase_usage(self):
        self.ctx['_verify_purchase'].return_value = {'tier': 'pro', 'expires_at': time.time() + 3600, 'purchase_key': 'store:qa'}
        await self.activate('first-install')
        record, _ = await self.ctx['_read_entitlement']('first-install')
        record['used'] = 900
        await self.ctx['_write_entitlement']('first-install', record)
        result = await self.activate('reinstalled-device')
        self.assertEqual(result['seconds_used'], 900)
        self.assertEqual(result['seconds_remaining'], 2700)

    async def test_signout_does_not_revoke_other_device(self):
        self.ctx['_verify_purchase'].return_value = {'tier': 'pro', 'expires_at': time.time() + 3600, 'purchase_key': 'store:qa'}
        await self.activate('first-install')
        await self.activate('second-install')
        await self.activate('first-install', receipt='')
        first, _ = await self.ctx['_read_entitlement']('first-install')
        second, _ = await self.ctx['_read_entitlement']('second-install')
        self.assertEqual(first['tier'], 'free')
        self.assertEqual(second['tier'], 'pro')

    async def test_store_storage_namespace_is_not_a_client_identity(self):
        result = await self.activate('store:qa')
        self.assertEqual(result.status_code, 400)

    async def test_shared_purchase_survives_redis_round_trip_and_process_restart(self):
        rows = {}
        async def hset(key, mapping):
            rows[key] = dict(mapping)
        async def hgetall(key):
            return dict(rows.get(key, {}))
        redis = SimpleNamespace(hset=hset, hgetall=hgetall, expire=AsyncMock())
        self.ctx['_entitlement_client'] = lambda: redis
        self.ctx['_verify_purchase'].return_value = {'tier': 'pro', 'expires_at': time.time() + 3600, 'purchase_key': 'store:qa'}
        await self.activate('first-install')
        record, _ = await self.ctx['_read_entitlement']('first-install')
        record['used'] = 600
        await self.ctx['_write_entitlement']('first-install', record)
        self.ctx['_entitlement_memory'].clear()
        result = await self.activate('second-install')
        self.assertTrue(result['durable'])
        self.assertEqual(result['seconds_used'], 600)


if __name__ == '__main__':
    unittest.main()
