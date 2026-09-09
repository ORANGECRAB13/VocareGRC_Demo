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
| `VOCARE_ADMOB_ANDROID_APP_ID` | Google's sample app id | `com.google.android.gms.ads.APPLICATION_ID` in the manifest. **Required for any release task** — see "Ads". |
| `VOCARE_ADMOB_ANDROID_APP_OPEN_UNIT` | Google's sample App Open unit | The App Open ad unit. **Required for any release task.** |
| `VOCARE_ADMOB_ANDROID_BANNER` | *empty* | Optional banner unit; empty renders no banner. |
| `VOCARE_ADMOB_TEST_DEVICE_IDS` | *empty* | Comma-separated AdMob test device ids. |
| `VOCARE_UMP_TEST_DEVICE_HASH` | *empty* | UMP test device for the EEA debug geography (debug builds only). |
| `VOCARE_ADS` / `-Pvocare.ads` | `true` | `false` removes the AdMob and UMP SDKs from the build. |
| `VOCARE_PAID_TIER` / `-Pvocare.paidTier` | `false` | `true` compiles in the paywall and Play Billing. |

Release signing reads a git-ignored `keystore.properties` when present;
without it the release build type is simply unsigned, so CI that only runs
`assembleDebug` keeps working.

## Test

```bash
./gradlew :app:assembleDebug :app:testDebugUnitTest :session:testDebugUnitTest
```

118 unit tests (81 in `:app`, 37 in `:session`), all passing:

| Suite | Tests | Covers |
|---|---|---|
| `app/…/api/RetrofitVocaApiTest` | 17 | Request and response encoding for every endpoint in CONTRACT §2 against MockWebServer, using the committed `native/fixtures` payloads: `402 → ApiException.PaymentRequired(balance)`, other non-2xx → `Http`, transport failure → `Network`, unparseable 2xx → `Decode`, empty ICE → `NoIceServers`, and the fire-and-forget behaviour of `hangup`/`consume`. |
| `app/…/store/EntitlementStoreTest` | 17 | Tier, `enforced` (including "missing means enforced"), `sandbox_ok`, budget derivation, the tri-state offline switch, the ads gate, and the whole Start routing table (§5). |
| `app/…/ads/AppOpenAdManagerTest` | 15 | The App Open state machine with a fake loader, clock and scheduler: nothing loads before consent; Pro never loads or shows; cold start shows only after Home rendered and only inside the grace window; never over the mic sheet, the paywall, a live route, or a session in progress in the background; the foreground cap (t=0 yes, +1 h no, +4 h yes; configurable); a four-hour-old ad is discarded and replaced, also in place while idle; reload after dismiss; failure backs off 30 s → 30 min; a blank unit id never calls the loader. |
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

## The free build, ads, and turning the paid tier on

This app ships **free and ad-supported**. There is no paid surface in the
binary at all — not merely hidden at runtime — because the subscription
product does not exist in Play Console and a Subscribe button that errors is a
review rejection. Advertising, on the other hand, is in every build.

Two independent build switches:

| Property / env | Default | Sets | Decides |
|---|---|---|---|
| `vocare.paidTier` / `VOCARE_PAID_TIER` | `false` | `BuildConfig.PAID_TIER_ENABLED` | paywall, Settings subscription section, `BillingClient` |
| `vocare.ads` / `VOCARE_ADS` | `true` | `BuildConfig.ADS_ENABLED` | whether AdMob + UMP are linked and `showAds` can ever be true |

With the paid tier **off** (the default):

| | |
|---|---|
| Paywall | not registered in the nav graph (`if (PaidSurface.ENABLED) composable(Routes.PAYWALL)`) |
| Tier chip | a status label; `onOpenPaywall` is null, so it has no upsell affordance |
| Settings | no Subscription section at all (`snapshot.paidSurfaceVisible`) |
| Start routing | `PaidSurface.resolve` turns any paywall verdict into a cloud attempt |
| `BillingClient` | never constructed — `AppContainer.storeClient` is null |
| Manifest | no `com.android.vending.BILLING` (removed at merge) |

The billing source (`PlayStoreClient`, `PlayPurchasesService`, `StoreClient`)
stays in the tree and keeps compiling, so the flip is configuration, not a
rewrite. There is no `src/free` / `src/paid` source-set switch any more;
nothing needed it once ads stopped being tied to the tier.

### Ads

**What shows.** One AdMob **App Open** ad (`ads/AppOpenAdManager.kt`), and
optionally a banner if `VOCARE_ADMOB_ANDROID_BANNER` is set (it is not, by
default). The manager preloads at launch, shows once on cold start after Home
has rendered, and on a return from the background at most once per four hours
(`AdsConfig.foregroundCapMinutes`). It never shows over the mic disclosure
sheet, the paywall, the error screen, a live route, or while a cloud/offline
session is connecting or live in the background; a loaded ad older than four
hours is discarded; a failed load backs off from 30 s to 30 min. Google draws
the close control — the app never covers, delays or auto-dismisses it.

**Who sees it.** `EntitlementStore.Snapshot.showAds` is
`ADS_ENABLED && tier == FREE`. Pro never sees an ad. With metering off
(`enforced:false`, the current backend) everyone is free, so everyone is
ad-supported. Accepted edge: a returning Pro subscriber is "free" to the app
until the first entitlement sync of the launch completes, and may see one App
Open ad on that launch.

**Consent (UMP).** `AdMobRuntime.start` runs
`requestConsentInfoUpdate` → `loadAndShowConsentFormIfRequired` on every
launch and only calls `MobileAds.initialize` (and lets the manager preload)
once `canRequestAds()` is true. The EEA/UK consent message is mandatory for
serving there; it is never skipped. When UMP reports
`privacyOptionsRequirementStatus == REQUIRED`, Settings → About gains a
"Privacy options" row that opens `showPrivacyOptionsForm`. In AdMob console
the app needs a published GDPR message (and the US-states message if you
serve there) or the form has nothing to show. To exercise the EEA flow from
Australia: build debug with `VOCARE_UMP_TEST_DEVICE_HASH=<hash logcat prints>`;
the debug geography is compiled out of release.

**Ids — env vars only, never committed.**

| Env | Default (Google's sample, test ads only) |
|---|---|
| `VOCARE_ADMOB_ANDROID_APP_ID` → manifest `APPLICATION_ID` | `ca-app-pub-3940256099942544~3347511713` |
| `VOCARE_ADMOB_ANDROID_APP_OPEN_UNIT` | `ca-app-pub-3940256099942544/9257395921` |
| `VOCARE_ADMOB_ANDROID_BANNER` (optional) | empty = no banner |
| `VOCARE_ADMOB_TEST_DEVICE_IDS` (comma-separated) | empty; `MobileAds.setRequestConfiguration` test devices, so real units are safe on a dev phone |
| `VOCARE_UMP_TEST_DEVICE_HASH` | empty; debug builds only |

**The release guard.** Shipping Google's sample ids is an AdMob policy
violation, so `app/build.gradle.kts` refuses to configure any release task
(`bundleRelease`, `assembleRelease`, plain `build`/`bundle`/`assemble`) while
either id starts with `ca-app-pub-3940256099942544`, and `preReleaseBuild`
checks again at execution time for any alias that slipped past. The message
names the offending variable(s). Debug builds keep the test ids. A release
therefore always looks like:

```sh
VOCARE_ADMOB_ANDROID_APP_ID=ca-app-pub-XXXX~YYYY \
VOCARE_ADMOB_ANDROID_APP_OPEN_UNIT=ca-app-pub-XXXX/ZZZZ \
./gradlew :app:bundleRelease
```

**Manifest.** `com.google.android.gms.ads.APPLICATION_ID` is set from the
`admobAppId` placeholder in every build (the SDK's init provider crashes
without it). `com.google.android.gms.permission.AD_ID` is merged in by
play-services-ads and deliberately not removed. `BILLING` stays removed.

**Play Console: Data Safety and store listing.** This build requires:

- *Data collected → Device or other IDs → Advertising ID*: collected, shared
  with Google (AdMob) for **Advertising or marketing**; not optional; not
  processed ephemerally. (Also *App activity / App interactions* if you enable
  any AdMob analytics; not needed for what ships here.)
- *Ads*: "Does your app contain ads?" = **Yes**.
- The Google Mobile Ads SDK appears in Play's SDK index for the upload; the
  declaration must match it.
- Families: this app is not in a Families program; if that changes, AdMob's
  child-directed settings become mandatory.

**Privacy policy.** The published policy currently says the app contains no
advertising SDKs; that is no longer true and the coordinator is updating the
policy separately. Do not ship this build to production until the new policy
is live at the URL in `LegalLinks.PRIVACY`.

**Turning ads off.** `-Pvocare.ads=false`: no `play-services-ads`, no UMP,
`ads/sdk/` not compiled, `ADS_ENABLED=false`, `showAds` always false,
`NoAdsRuntime` in the container. Verified by building the debug APK that way
and confirming zero `com/google/android/gms/ads` references in its dex.

### Turning the paid tier on

Do these together — a build flag without a Play Console product, or a billing
permission without products, is exactly the state this design avoids.

1. **Build flag.** `-Pvocare.paidTier=true`. `app/build.gradle.kts` warns
   about any manifest step below that is still missing.
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
5. **Ads.** Nothing to do: ads are already live for free users and already
   hidden from Pro (`showAds`). Once `enforced:true`, a Pro subscriber stops
   seeing ads on the first entitlement sync after purchase.

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
