"""HTTP smoke test for a disposable, credential-free backend container only."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8080'


def request(path, payload=None):
    req = Request(BASE + path, data=json.dumps(payload).encode() if payload is not None else None,
                  headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


for attempt in range(30):
    try:
        status, _ = request('/healthz')
        assert status == 200
        break
    except (URLError, TimeoutError):
        time.sleep(1)
else:
    raise RuntimeError('Local backend did not become healthy')

assert request('/api/entitlement')[0] == 400
status, balance = request('/api/entitlement?subject=qa-smoke')
assert status == 200 and balance['tier'] == 'free' and balance['enforced'] is True
status, result = request('/api/entitlement/activate', {
    'subject': 'qa-smoke', 'platform': 'android', 'receipt': 'invalid-test-token',
})
assert status == 200 and result['verified'] is False and result['tier'] == 'free'
assert request('/api/entitlement/activate', {
    'subject': 'qa-smoke', 'platform': 'android', 'receipt': {},
})[0] == 400
print('PASS: health, free balance, no grant without store verification, invalid request rejection')
