"""Read-only Pro deployment preflight. Reports names and status, never secret values."""
import argparse
import json
import os
from pathlib import Path


def check(mode, env):
    problems = []
    expected = {
        'ANDROID_PACKAGE_NAME': 'com.vocare.translate',
        'STORE_PRODUCT_ID': 'vocare_pro_monthly',
        'ENTITLEMENT_ENFORCED': 'true',
        'STORE_ALLOW_SANDBOX': 'true' if mode == 'testing' else 'false',
    }
    for name, value in expected.items():
        if env.get(name, '').strip() != value:
            problems.append(f'{name} must be explicitly set to {value}')
    if not env.get('LIVE_CALL_REDIS_URL', '').startswith('rediss://'):
        problems.append('LIVE_CALL_REDIS_URL must configure durable TLS-protected storage')
    raw = env.get('GOOGLE_PLAY_SERVICE_ACCOUNT_JSON', '').strip()
    if not raw and env.get('GOOGLE_PLAY_SERVICE_ACCOUNT_FILE'):
        try:
            raw = Path(env['GOOGLE_PLAY_SERVICE_ACCOUNT_FILE']).read_text(encoding='utf-8')
        except OSError:
            problems.append('GOOGLE_PLAY_SERVICE_ACCOUNT_FILE cannot be read')
    if not raw:
        problems.append('Google Play service-account credentials are missing')
    else:
        try:
            account = json.loads(raw)
            if not isinstance(account, dict) or account.get('type') != 'service_account' or not all(account.get(k) for k in ['client_email', 'private_key', 'token_uri']):
                problems.append('Google Play credentials are not a complete service-account key')
        except (ValueError, TypeError):
            problems.append('Google Play credentials are not valid JSON')
    return problems


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['testing', 'production'], required=True)
    args = parser.parse_args()
    problems = check(args.mode, os.environ)
    for problem in problems:
        print('BLOCKED:', problem)
    if not problems:
        print('Configuration shape passed. Still requires a real Play license-tester purchase and signing verification.')
    raise SystemExit(bool(problems))
