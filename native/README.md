# Voca native apps

Fully native iOS (SwiftUI) and Android (Compose) replacements for the Capacitor
webview app. `CONTRACT.md` is the source of truth; this file is the map.

## What is where

| Path | Owner | Contents |
|---|---|---|
| `CONTRACT.md` | orchestrator | API, session protocol, product behaviour, module interfaces, design tokens, definition of done |
| `ios/VocaApp/`, `ios/*.xcodeproj` | ios-app | Thin app target |
| `ios/VocaKit/Sources/VocaKit` | ios-app | Design tokens, API client, store, history, screens, app shell |
| `ios/VocaKit/Sources/VocaSession` | ios-session | Cloud (WebRTC) and offline pipelines, `LiveSplitView` |
| `ios/VocaKit/Tests/*` | each owner | XCTest targets |
| `android/app/`, `android/core/`, gradle files | android-app | Compose app, theme, nav, screens, API, store, history |
| `android/session/` | android-session | WebRTC + offline pipeline + live UI |
| `fixtures/` | qa-contract | Backend response samples both apps load in unit tests (`fixtures/README.md`) |
| `QA-REVIEW.md` | qa-contract | Cross-platform review against the §5 checklist |
| `../tests/test_native_api_contract.py` | qa-contract | Backend contract tests (in-process against `bot.py`, and live) |
| `../scripts/verify-native.sh` | qa-contract | One command: contract tests, iOS build+test, Android build+test |
| `../.github/workflows/verify-native.yml` | qa-contract | CI for all of the above |

## How to run everything

```bash
# Backend contract, in-process only (stdlib Python, no deps)
python3 -m unittest tests.test_native_api_contract -v

# Backend contract against the deployed backend
VOCARE_API_BASE=https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io \
  python3 -m unittest tests.test_native_api_contract -v

# Everything (each stage skips with a message when its toolchain is absent)
scripts/verify-native.sh
VOCARE_API_BASE=https://... scripts/verify-native.sh

# iOS on its own (macOS + Xcode)
xcodebuild -project native/ios/VocaApp.xcodeproj -scheme VocaApp \
  -destination 'platform=iOS Simulator,name=iPhone 16' CODE_SIGNING_ALLOWED=NO test

# Android on its own (JDK 21 + Android SDK)
cd native/android && ./gradlew assembleDebug testDebugUnitTest
```

Override the iOS scheme/destination with `IOS_SCHEME` / `IOS_DESTINATION`.
CI (`verify-native.yml`) runs the contract tests on Ubuntu (live tests only when
the `VOCARE_API_BASE` secret is set) and the iOS/Android jobs on macOS.

Each platform directory has its own `README.md` with build notes and what is not
yet verified.

## Contract test coverage

`tests/test_native_api_contract.py` asserts, against the live backend and against
the real handlers extracted from `bot.py`:

- `/healthz` exact body; `/api/ice` non-empty with a TURN entry (live)
- `/api/entitlement` 400 without subject; exact key set incl. `enforced`, `sandbox_ok`, `durable`
- `/activate` 400 on bad bodies; `store_reachable:false` with no receipt does not
  touch the record; `store_reachable:true` with no receipt resets to free; invalid
  receipt is never a purchase
- `/consume` watermark (90, 90, 150, 150 settles at 150; a new session adds)
- `/api/translation/session` create, `client_id` scoping of `/sessions`, detail shape
- `/poll` unknown session is `{events:[], closed:true}`; poll drains; `closed` follows status
- `/hangup` 400 without `pc_id`; `/offer` 404 unknown session; 402 body when enforced and exhausted (in-process)
- `/api/watch/translate` import probe: same source/target is a 400 listing the languages; a 500 means the image is broken
- every fixture's key set equals the corresponding real response

## §5 functionality checklist

Status values: `unverified` (nobody has checked), `implemented` (code read, no test),
`tested` (a real test or a run exercised it). Evidence is a file path or a command
run. Filled in by the cross-platform QA review in `QA-REVIEW.md` (13 findings:
3 high, 5 medium, 1 low-medium, 3 low + 1 shared gap). Rows marked **divergent**
are implemented on both platforms but do different things; see the findings table.
Nothing below has been run on a device: no real WebRTC, speech, purchase, Room,
SwiftData or AdMob path is covered by the green suites.

| # | Requirement (CONTRACT §5 / §3 / §4) | iOS | Android | Evidence |
|---|---|---|---|---|
| 1 | Language table `en zh yue ja ko es fr de ar hi fil` with VOCA-main flags, `yue` withdrawn from picker | implemented | implemented | `Languages.swift:25-37`, `core/model/Languages.kt:20-23`; no test |
| 2 | Free tier: offline ON by default, on-device translation, no cloud minutes | tested | tested | `testOfflineSwitchDefaultsToTierAndSticksOnceTouched` / `the offline switch is tri-state and sticks once touched` |
| 3 | Pro tier: A$29.99/mo `vocare_pro_monthly`, 60 min cloud, tier chip shows minutes left | tested | tested (instrumented) | `testProductIdMatchesTheContract`, `testHomeRendersForAProUserWithMinutesLeft` / `homeShowsMinutesLeftForPro` (androidTest — needs a device) |
| 4 | Offline switch tri-state (`nil` follows tier; sticks once touched) | tested | tested | as #2 |
| 5 | Start routing: offline switch → offline; not cloud-allowed → offline or paywall(`unsupported`); metered and balance 0 → paywall(`exhausted`); else cloud | tested | tested | `EntitlementStoreTests.swift:82-121` / `EntitlementStoreTest.kt:96-121` |
| 6 | Purchase flow: store → `activate` with receipt → server says pro → (Android) acknowledge only then; `cancelled`, `pending`; restore | tested | tested | `testPurchaseNeedsServerVerificationBeforeItCountsAsPro`, `testCancelledAndPendingPurchasesDoNotTouchTheServer` / `a verified purchase grants pro and acknowledges only afterwards`, `an unverified receipt is never acknowledged`. **QA-REVIEW H2** (iOS `store_reachable:false`), **M4** (Android does not resume after purchase) |
| 7 | Banner ad on Home/History/Settings only, never on live, never for pro; ads SDK never initialised without a configured ad unit id | implemented | implemented | `RootView.swift:20-24` + `AdsService.swift:33-40` / `VocaNav.kt:62` + `ads/AdsService.kt:23-29`; no test, no device run |
| 8 | Screens: Home, Language picker, Mic disclosure (once), Live split, Paywall (3 variants, localized price, restore), History, Session detail, Settings, Error (mic/network) | implemented | implemented | all 9 screens wired (`RootView.swift:39-75` / `VocaNav.kt:95-201`); only Home + Paywall are render-tested |
| 9 | Persistence: history schema ported; prefs client_id, mic consent, offline switch, toggles | tested (via fakes) | tested (via fakes) | `testEndHangsUpBothLegsAndPersists` / `the persisted record carries both languages, both names and the transcript`; SwiftData and Room themselves unexercised |
| 10 | Cloud: ICE fetched, empty list fails the session; two PCs `relay` only; both clones enabled during negotiation then disabled | tested (fake legs) | tested (fake legs) | `testEmptyIceServersFailsSession`, `testSetupFollowsProtocolOrderAndTrackHandling` / `an empty ICE list fails the session before any offer`, `setup creates one session, two legs, and disables both mics once connected` |
| 11 | Cloud: offer per leg, ICE gathering wait capped 5s, `pc_id` per leg; poll/timer/metering start on PC A `connected` | implemented | implemented | `WebRTCLeg.swift:88-95` / `WebRtcLeg.kt:205`; the 5s cap is untested on both |
| 12 | PTT: refuse if side not `ready` or other side pressing; disable other leg; hold → then enable own leg; revert on failure | tested | tested | `testHoldAndReleaseStateMachine`, `testOtherSideIsLockedOutWhilePressing`, `testGateFailureRevertsToReady` / the three equivalents |
| 13 | Release: disable leg, `translating`, POST release; no `flushed_bytes` → `ready`; else wait for own `turn` or 36s fallback | tested | tested | `testReleaseWithoutFlushedBytesReturnsToReadyImmediately`, `testTranslatingFallsBackToReadyAfter36Seconds` / `release with no flushed bytes …`, `release with flushed bytes waits, then gives up after the 36s fallback` |
| 14 | Turn attribution `speaker == pc_id` (fallback `original_lang`); A's turn on B's panel as Translated, own panel as You said; `turn_failed` → ready | tested (divergent) | tested | `testTurnSpokenByAAppearsTranslatedOnBAndYouSaidOnA`, `testTurnFailedReleasesThatSide` / same + `attribution survives teardown when the backend sends no original_lang`. **QA-REVIEW M1, M2** |
| 15 | Metering: `/consume` total elapsed every 15s and on End; budget reached → persist, tear down, paywall(`exhausted`) | tested | tested | `testConsumeReportsTotalSecondsEvery15Seconds`, `testBudgetExhaustionEndsSession`, `testUnmeteredBudgetNeverEnds` / the equivalents |
| 16 | Teardown: timers cleared, hangup both pc_ids, tracks stopped, PCs closed, `ended` persisted with duration + transcript; `closed:true` → same | tested | tested | `testEndHangsUpBothLegsAndPersists`, `testEndIsIdempotent`, `testClosedTrueEndsSession` / `closed true ends the session and hangs up both legs`, `end persists exactly one ended record however many times it is tapped` |
| 17 | `failed`/`disconnected` → error(network); mic denied → error(mic); `/offer` 402 → typed error → paywall | **divergent** | implemented (leg B ignored) | **QA-REVIEW H1**: iOS shows 402 as a network error (`testPaymentRequiredIsNetworkErrorAndTearsDown`); Android routes to the exhausted paywall. **M6**: Android drops leg B's connection state |
| 18 | Offline: on-device STT (iOS `requiresOnDeviceRecognition`; Android `EXTRA_PREFER_OFFLINE`, `cmn-Hans-CN` for zh), partials on speaker's panel | tested (partials) | tested | `testPartialsAppearOnTheSpeakersOwnPanelOnly` / same + `Mandarin uses the device recognition locale`; no iOS locale test |
| 19 | Offline: translate via Apple Translation / ML Kit, local-voice TTS only, same panel semantics | tested (fake engine) | tested (fake engine) | `testFullTurnRunsSttThenTranslateThenTts`, `testMissingLocalVoiceStillKeepsTheTurn` / the equivalents; the real engines have never run |
| 20 | Pair availability `installed / supported / unsupported`; in-app pack install (Apple: both directions); declined download is not an error; `yue` never offline; Android adds `fil` (`tl`) | **divergent** | tested | **QA-REVIEW H3**: iOS refuses a `supported` pair instead of installing it (`testSupportedButNotInstalledPairAlsoRefusesTheTurn`); Android installs in place. **M7**: Android's prepare has no decline path |
| 21 | Tests per §8: API client encoding + 402 + network error; entitlement routing; cloud VM state machine; offline VM; purchases fake store; snapshot/UI of Home, Paywall, LiveSplitView | partial | partial | Home + Paywall render tests on both; **neither has a LiveSplitView / LiveSplitScreen snapshot or UI test** |
| 22 | No secrets in repo; ad unit ids and API base from build config | implemented | implemented | `AdsService.swift:12-13` (Info.plist), `android/app/build.gradle.kts:36-40` (env → BuildConfig) |
| 23 | Design tokens and fonts per §7 | implemented | implemented | `Design/VocaTheme.swift`, `core/theme/VocaTheme.kt`; not compared against the canvas |
