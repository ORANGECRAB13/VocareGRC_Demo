# Android and Pro deployment candidate — 5 September 2026

Status: verified code deployed to Azure; paid Pro enforcement remains disabled pending Play credentials.
Source: `voca-redesign-subscriptions`, runtime commit `f7f4f4d`, in
`F:\voca\qa-redesign-subscriptions`. The original `F:\voca` checkout is preserved.
The branch README was not used.

## Implemented

- Portable Windows bundle build; Android plugin wiring regenerated and committed-file drift checked by new CI.
- AdMob application metadata prevents the missing-ID startup failure. With no banner configured, no ads are requested.
  Google's sample application ID is the safe default; live ads require an actual app ID and banner ID.
- Play Billing 8.3.0; initial connection requests are queued, billing launches on the UI thread,
  duplicate purchases are rejected, and product details are refreshed for each request.
- The Pro offer is the monthly auto-renewing base plan without an introductory offer, matching the displayed monthly price.
- Pro verification checks product, state, expiry and sandbox policy. Cancelling renewal retains access until expiry.
- Receipts and expiry are persisted with the ledger and rechecked server-side at most every five minutes on access.
  A temporary verification outage preserves access only until the already-verified expiry.
- Restore, resume and delayed purchase events retry acknowledgement after fresh server verification.
  Verification failures no longer claim that a charge has already been refunded.
- Reinstalled and additional devices share the subscription's remaining minutes. Signing out unlinks only that installation.
- Offline speech waits for native completion and cached final results, checks downloaded translation packs and
  on-device recognition availability, cleans up cancelled/timed-out capture, and selects local TTS voices only.
  Android avoids the speech plugin's unsafe worker-thread support probe and lets the real on-device start report
  unsupported packs or locales as a recoverable error instead of terminating the app.
- Restored missing `phone_approval.py`, `voice_control.py`, and `vocare_llm.py` from local QA commit `8071585`.
  They are byte-identical to those tracked versions (last relevant update `7b17a6d`, 27 August).
  These are existing dependencies of `bot.py`; without them the image cannot start.
- Backend base image is pinned to the inspected registry digest, and Android signing files are excluded from Docker context.

## Verified locally

- `npm run test:mobile`: 14 behavioral regressions passed.
- `python -m unittest discover -s tests -p 'test_*.py'`: 22 tests passed, including shared-ledger Redis persistence.
- `npm run android:sync` and `npm run verify:www` passed.
- Java 21: `assembleDebug testDebugUnitTest lintDebug bundleRelease` passed. Lint: 0 errors, 32 warnings.
  The inherited native unit test is only an example; it does not prove store behavior.
- Browser home and Pro paywall render from the rebuilt bundle. This does not emulate native billing or speech.
- Actual Docker image imports `bot` and starts the HTTP service.
- Isolated, network-disabled container HTTP smoke passes health, free balance,
  no grant without Play credentials, and malformed receipt rejection.
- Release AAB inspected with `jarsigner`: unsigned, as expected without an upload key.
- Physical Samsung S24+ (`SM-S926B`) QA: debug APK installed and launched; Home, Settings, offline packs and Pro
  paywall rendered at the device's native resolution with accessible controls and no layout clipping.
- The English and Mandarin ML Kit models downloaded successfully and were reported on-device. Entering an offline
  session initially exposed an Android 16 crash in the speech plugin's availability probe. The guarded rebuild was
  reinstalled and the same hold/release path then started on-device recognition, emitted completion, removed its
  listeners and returned the expected `NO_MATCH` for a silent automated capture without restarting the process.
- The live language-direction icon is now an accessible swap control for offline sessions and physically swapped the
  English and Mandarin halves. Android Mandarin recognition now requests the device-supported `cmn-Hans-CN` locale
  rather than `zh-CN`; the 52.97 MB speech pack installed and a silent capture completed with `NO_MATCH` instead of
  `UNSUPPORTED_LOCALE`.
- Restore returned an inactive entitlement and the UI correctly reported no subscription. A sideloaded APK cannot
  resolve the Play subscription product, so Subscribe failed safely without opening or completing a charge.

## Artifacts

- Debug APK: `android/app/build/outputs/apk/debug/app-debug.apk`
- Unsigned release AAB: `android/app/build/outputs/bundle/release/app-release.aab`
- Lint report: `android/app/build/reports/lint-results-debug.html`
- Local container image: `vocare-redesign-qa:20260905` (not pushed)
- Base image: `vocaregrcregistry.azurecr.io/vocare-grc-base@sha256:4e0f21ac39fe2f72651cd178f354e44540d610f9669b800f4ad16b9aebeade32`

## Required before store testing

1. Open the Google account that owns the Play Console developer account, or obtain an invitation.
   The currently signed-in account opened `/signup`; no developer registration or purchase was submitted.
2. Confirm Play app package `com.vocare.translate` and an active `vocare_pro_monthly` subscription with an
   eligible, auto-renewing monthly base plan. Product availability and real localized pricing are not yet verified.
3. Supply the Google Play service-account key via a secret-backed `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`
   or a mounted `GOOGLE_PLAY_SERVICE_ACCOUNT_FILE`. Never commit the key or paste it into the handoff.
   The account needs Play API access to this application; local shape validation cannot establish that permission.
4. Supply the existing upload keystore and local `android/keystore.properties`.
   Confirm a versionCode greater than the highest uploaded Play version, then rebuild and verify signing.
5. Configure a separate store-testing backend and build with `VOCARE_API_BASE` pointing to it.
   Use `ANDROID_PACKAGE_NAME=com.vocare.translate`, `STORE_PRODUCT_ID=vocare_pro_monthly`,
   `ENTITLEMENT_ENFORCED=true`, `STORE_ALLOW_SANDBOX=true`, and a durable `rediss://` ledger.
   Run `python scripts/check-pro-config.py --mode testing` inside that configured backend.
6. Install through an internal test track with a Play license tester. Exercise successful and declined payment,
   pending approval, acknowledgement, restore/reinstall, renewal cancellation, expiry, refund and app resume.
   Verify the resulting balance with the backend; the paywall alone is not payment proof.
7. On the Android phone, complete the human-audio checks that ADB cannot supply: speak consecutive turns in both
   directions, verify the translated text and local TTS, test microphone denial, and repeat in airplane mode.

## Production handoff

The live app is `vocare-grc-bot` in resource group `vocare-grc`; it uses Azure LLM routing
and the agent translation engine. Its environment-variable configuration lacks Play verification
credentials and entitlement flags. Redis configuration is present. No live settings were changed.

Runtime commit `f7f4f4d` was built from the pinned base, passed the disposable-container smoke test, and was
published as immutable digest `sha256:c7743a3013197117a3e0cf78a66232ea1648b82d7872ad69c17e5b93b232d13e`.
Azure revision `vocare-grc-bot--subf7f4f4d` became healthy and received 100% traffic. Live `/healthz`,
`/vocare`, and the free entitlement response were verified after cutover. The previous revision remains
available in Container Apps' inactive revision history for rollback.

Do not use the legacy deployment workflow's different app/resource-group target for this candidate.
The new `verify-redesign.yml` workflow verifies and uploads QA artifacts only; it does not deploy.

After the store/device checks and a reviewed commit, build the production bundle against the production API,
use `STORE_ALLOW_SANDBOX=false`, and run `python scripts/check-pro-config.py --mode production` in the configured runtime.
That preflight checks configuration shape only. Rebuild the final Android and backend artifacts after configuration changes.
Push the reviewed backend candidate to ACR, resolve its immutable digest, and deploy that digest to the verified target.
Verify the new revision's readiness, health endpoint, entitlement behavior, and traffic before declaring deployment complete.
Retain the previous live revision/digest for rollback. The branch was committed and pushed and Azure was deployed;
no Play Store upload was performed and paid entitlement enforcement was not enabled.

References: [Play Billing versions](https://developer.android.com/google/play/billing/deprecation-faq),
[subscription lifecycle](https://developer.android.com/google/play/billing/lifecycle/subscriptions),
[Play billing testing](https://developer.android.com/google/play/billing/test),
[AdMob application ID](https://developers.google.com/admob/android/quick-start).
