# Voca — native Android

The fully native Android rewrite of the Voca live-translation app, built against
[`native/CONTRACT.md`](../CONTRACT.md). The Capacitor app in `android/` is
untouched and keeps working; nothing here shares code with it.

## Modules

| Module | Owns |
|---|---|
| `:core` | The shared contract: `VocaApi`, wire + session models, `SessionHistoryStore`, `OfflineEngine`, the `VocaTheme` design tokens (CONTRACT §7) and the bundled Outfit / Space Grotesk / JetBrains Mono fonts. No network, no storage, no screens. |
| `:session` | The live session: two TURN-only WebRTC legs and the push-to-talk protocol (CONTRACT §3), the on-device STT → ML Kit → TTS pipeline (§4), and `LiveSplitScreen`, the one split view both sessions render. Depends on `:core` only. |
| `:app` | The Compose app: `MainActivity`, the nav host and every screen in §5, the Retrofit client, the entitlement store, Play Billing, Room history and the ads gate. Depends on `:core` and `:session`. |

Neither `:app` nor `:session` depends on the other's UI — that is what `:core`
is for.

## Toolchain

JDK 21 and the Android SDK (platform 36, build-tools 36.0.0). `java` is not
assumed to be on the PATH:

```bash
export JAVA_HOME="$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home"
export ANDROID_HOME="$HOME/Library/Android/sdk"
export PATH="$JAVA_HOME/bin:$PATH"
```

`local.properties` must contain `sdk.dir`. It is not committed.

## Build

```bash
cd native/android
./gradlew :app:assembleDebug
```

Build configuration comes from the environment; nothing secret is committed:

| Variable | Default | Effect |
|---|---|---|
| `VOCARE_API_BASE` | the production Azure URL | Backend base URL. |
| `VOCARE_ADMOB_ANDROID_BANNER` | *empty* | Banner ad unit id. **Empty means the ads SDK is never initialised** (CONTRACT §5) — `AdsService.enabled` is false and `Banner` renders nothing. |
| `VOCARE_ADMOB_ANDROID_APP_ID` | Google's public sample app id | `com.google.android.gms.ads.APPLICATION_ID` in the manifest. |

Release signing reads a git-ignored `keystore.properties` when present;
without it the release build type is simply unsigned, so CI that only runs
`assembleDebug` keeps working.

## Test

```bash
./gradlew :app:assembleDebug :app:testDebugUnitTest :session:testDebugUnitTest
```

81 unit tests, all passing:

| Suite | Tests | Covers |
|---|---|---|
| `app/…/api/RetrofitVocaApiTest` | 17 | Request and response encoding for every endpoint in CONTRACT §2 against MockWebServer, using the committed `native/fixtures` payloads: `402 → ApiException.PaymentRequired(balance)`, other non-2xx → `Http`, transport failure → `Network`, unparseable 2xx → `Decode`, empty ICE → `NoIceServers`, and the fire-and-forget behaviour of `hangup`/`consume`. |
| `app/…/store/EntitlementStoreTest` | 14 | Tier, `enforced` (including "missing means enforced"), `sandbox_ok`, budget derivation, the tri-state offline switch, the ads gate, and the whole Start routing table (§5). |
| `app/…/store/PlayPurchasesServiceTest` | 15 | Purchased / cancelled / pending / failed / restore against a fake `StoreClient`, asserting on call **order** that Play is acknowledged only after the server verified the receipt, plus the `store_reachable` rules for `syncEntitlement`. |
| `session/…/cloud/CloudSessionViewModelTest` | 22 | The §3 protocol on virtual time with a fake API and fake legs: setup, mic denial, empty ICE, 402 on `/offer`, hold/release, other-side lockout, gate-failure revert, the `flushed_bytes` short-circuit, the 36s fallback, turn attribution to panels, `turn_failed`, the 15s consume watermark as a running total, budget exhaustion, `closed:true`, End idempotency and teardown. One test replays the committed `poll_events.json` frame by frame and checks the panel counts the fixture declares. |
| `session/…/offline/OfflineSessionViewModelTest` | 13 | The §4 pipeline with a fake engine: a full turn, the `cmn-Hans-CN` recognition locale for Mandarin, partials on the speaker's own panel, a declined download (a notice, not an error), an unsupported pair, nothing recognised, translation failure, a missing local voice, and end-cancels-recognition. |

Fixtures are read from `native/fixtures` by a loader that searches upwards for
the directory and asserts the file it found is non-empty — a fixture path that
silently resolves to nothing would make the tests that read it pass for the
wrong reason.

### Instrumented tests

`app/src/androidTest/…/ScreensUiTest.kt` covers Home and the Paywall with
Compose's test rule. It **compiles** (`./gradlew :app:assembleDebugAndroidTest`)
but has never been run — that needs a device or emulator:

```bash
./gradlew :app:connectedDebugAndroidTest
```

## Bugs found and fixed while writing the tests

Both were found in `:session`, both have a regression test that fails when the
fix is reverted (verified):

1. **`end()` was not idempotent.** `finish()` checked the phase and then
   suspended in `persist()` before setting it, so two End taps both got past
   the guard and wrote two `ended` history rows. The guard is now latched
   synchronously. The fake history store deliberately suspends (`yield()`) like
   the real Room one, or the test would not see it.
2. **Turn attribution broke at teardown.** `sideFor` read `SideRuntime.pcId`,
   which `teardown()` clears — so once a session ended, every turn already on
   screen was re-attributed by the `original_lang` fallback, and landed on the
   wrong panel outright when the backend sent no `original_lang`. Side A's
   pc_id is now copied into a field attribution owns and teardown never touches.

A third issue was found in `:app`: the Retrofit `Json` did not set
`encodeDefaults`, so `type:"offer"`, `platform:"android"` and `topic` were
dropped from request bodies whenever they equalled their Kotlin default. Fixed.

## The free build, and turning the paid tier on

This app ships **free**. There is no paid surface in the binary at all — not
merely hidden at runtime — because the subscription product does not exist in
Play Console, a Subscribe button that errors is a review rejection, and the
published privacy policy promises "no ad networks, no analytics or tracking
SDKs of any kind".

One switch decides it: `-Pvocare.paidTier=true` (or `VOCARE_PAID_TIER=true`),
which sets `BuildConfig.PAID_TIER_ENABLED`. With it **off** (the default):

| | |
|---|---|
| Paywall | not registered in the nav graph (`if (PaidSurface.ENABLED) composable(Routes.PAYWALL)`) |
| Tier chip | a status label; `onOpenPaywall` is null, so it has no upsell affordance |
| Settings | no Subscription section at all (`snapshot.paidSurfaceVisible`) |
| Start routing | `PaidSurface.resolve` turns any paywall verdict into a cloud attempt |
| `BillingClient` | never constructed — `AppContainer.storeClient` is null |
| Manifest | no `com.android.vending.BILLING` (removed at merge), no AdMob meta-data |
| Ads | `src/free/java` compiles a no-op `AdsService`; play-services-ads is not linked |

The billing source (`PlayStoreClient`, `PlayPurchasesService`, `StoreClient`)
stays in the tree and keeps compiling, so the flip is configuration, not a
rewrite.

### Turning the paid tier on

Do these together — a build flag without a Play Console product, or a billing
permission without products, is exactly the state this design avoids.

1. **Build flag.** `-Pvocare.paidTier=true`. `app/build.gradle.kts` then
   compiles `src/paid/java` instead of `src/free/java` and warns about any
   manifest step below that is still missing.
2. **Manifest + Play Console, together.** Replace the `tools:node="remove"`
   entry in `app/src/main/AndroidManifest.xml` with a plain
   `<uses-permission android:name="com.android.vending.BILLING" />`, and create
   the `vocare_pro_monthly` subscription in Play Console with a single
   auto-renewing monthly base plan, no offer id and no trial — `monthlyOffer()`
   in `PlayStoreClient` selects exactly that shape and finds nothing otherwise.
3. **Backend.** `ENTITLEMENT_ENFORCED=true` and `STORE_ALLOW_SANDBOX=false`.
   `enforced:true` is what makes `metered` (and so `paidSurfaceVisible`) true;
   until then the paid build still shows no paywall, by design.
4. **Tests.** Two tests assert the shipped configuration is the free one —
   `EntitlementStoreTest."this build ships free …"` and
   `PaidSurfaceTest."the shipped build has the paid surface switched off"`.
   They are the tripwire against shipping a paid surface by accident; invert
   them deliberately when the paid build becomes the release build.
5. **Ads (only if ads actually go live).** Re-add `implementation(libs.play.services.ads)`
   — it is already conditional on the flag — plus the
   `com.google.android.gms.ads.APPLICATION_ID` meta-data with a real app id,
   `VOCARE_ADMOB_ANDROID_BANNER` for a real unit id, the
   `com.google.android.gms.permission.AD_ID` permission, the advertising-ID
   declaration in Play Data Safety, **and** an updated privacy policy: the
   current one says the app contains no advertising SDKs, and Play's automated
   SDK scan compares that claim against the uploaded binary.

### What must be tested on a device before a paid release

None of it can be tested here; it needs the Play Console product live, a
licence tester account, and an internal-testing track build on real hardware:

- a purchase completing end to end, and `activate` accepting the token;
- `ITEM_ALREADY_OWNED` — buy, then buy again on a second device;
- restore after a reinstall (`queryPurchasesAsync`);
- a pending purchase clearing (test instrument "slow" card);
- that the obfuscated account id shows up against the order in Play Console;
- the Play subscription-management deep link opening the right page;
- cancellation, and the downgrade the next `activate` produces.

## What is NOT verified

Everything below compiles, and is exercised only through fakes. None of it has
run on a device or an emulator.

- **Real WebRTC.** `WebRtcLeg` / `WebRtcLegFactory` (libwebrtc via
  `stream-webrtc-android`) have never negotiated a call. ICE gathering, the
  relay-only transport policy, the mic-clone-per-leg trick and remote audio
  playback are all untested; the view-model tests use a fake `PeerLeg`.
- **Real on-device speech.** `AndroidOfflineEngine` — `SpeechRecognizer` with
  `EXTRA_PREFER_OFFLINE`, ML Kit model download, `TextToSpeech` restricted to
  local voices — is untested. In particular the `cmn-Hans-CN` recognition
  locale for Mandarin is asserted only as a string.
- **Real Play Billing.** `PlayStoreClient` (Play Billing 8.3.0) is untested;
  the purchase tests drive a fake `StoreClient`. Acknowledgement ordering,
  `ITEM_ALREADY_OWNED` recovery and the obfuscated account id are proven
  against the fake and against the response-code mapping, not against Play. No
  purchase can complete without a Play Console product and a licence tester.
- **Room.** `HistoryRepository` and the DAO have no tests at all; the schema is
  a port of the Capacitor app's table and has not been read back on a device.
- **AdMob.** The free build does not link the SDK at all (`src/free/java`), so
  there is nothing to test; the paid `AdMobAdsService` is untested and still
  refuses to initialise without a banner unit id, and no unit id is committed.
- **Any screen on a device.** The nav host, permission flow, mic disclosure
  sheet, DataStore preferences and the deep-linked routes have not been run.
- **The backend.** No test here talks to the real service; the fixtures are
  checked against production by `tests/test_native_api_contract.py` instead.
