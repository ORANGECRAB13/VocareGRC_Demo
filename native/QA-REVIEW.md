# Cross-platform QA review — Voca native iOS vs Android

Reviewed on branch `native-apps` against `CONTRACT.md` §2–§5, reading both
implementations side by side. Both trees compile and their suites are green
(iOS 105, Android 81, 0 failures), so nothing here is a build or test break —
these are behavioural divergences between the two apps, or between one app and
the contract, that the green suites do not catch (in three cases the suites
actively enshrine the divergence).

Method: for each contract behaviour, read the same code path on both platforms
and compare. Only differences with a file:line on each side are listed. Where
the two agree, that is stated and no finding is raised.

## Findings

| # | Sev | Platform | Behaviour | iOS | Android | Contract says |
|---|---|---|---|---|---|---|
| H1 | high | iOS | 402 on `/offer` is shown as a network error instead of routing to the `exhausted` paywall | `Sources/VocaSession/Cloud/CloudSessionViewModel.swift:163-166` (generic `catch` → `fail(.network)`); test enshrines it: `Tests/VocaSessionTests/CloudSessionViewModelTests.swift:114` | correct: `session/.../cloud/CloudSessionViewModel.kt:169-172` → `EndReason.EXHAUSTED`, routed at `app/.../ui/VocaNav.kt:222-224` | §2 `/offer` returns 402 `no_translation_credit`; §5 `exhausted` paywall variant; §8 "402 → typed error" |
| H2 | high | iOS | `store_reachable:false` can never be sent, so a store lookup that comes back empty for any reason downgrades a paying user | `Sources/VocaKit/Store/PurchasesService.swift:54-59` always returns `reachable: true`; `StoreClient.swift:108-117` returns `nil` for "no entitlement" and for "could not read"; the no-downgrade branch at `Store/EntitlementStore.swift:97` is therefore dead code | correct: `app/.../store/PlayPurchasesService.kt:70-75` falls back to `GET /entitlement` when the store cannot be asked; tested at `PlayPurchasesServiceTest.kt:212` | §2 "`false` = couldn't ask (must NOT downgrade)". The backend resets the record to free on `store_reachable:true` + empty receipt (see `tests/test_native_api_contract.py` coverage listed in `native/README.md`) |
| H3 | high | iOS | An offline session on a `supported`-but-not-installed pair is a dead end: every turn is refused, no in-session install | `Sources/VocaSession/Offline/OfflineSessionViewModel.swift:93-94` requires `.installed`; test enshrines it: `Tests/VocaSessionTests/OfflineSessionViewModelTests.swift:140` | installs in place: `session/.../offline/OfflineSessionViewModel.kt:90-99`; test `OfflineSessionViewModelTest.kt:101` | §4 "Language packs are installable **in-app** … A declined download is not an error". Compounded by §5 routing: `canTranslateOffline` (`Sources/VocaKit/App/Languages.swift:49`) is code-based, not install-based, so a free user is routed *into* the session that cannot produce a turn |
| M1 | med | iOS | `turn_failed` without a `speaker` always releases side B, and never uses the `original_lang` fallback | `CloudSessionViewModel.swift:269` (`event.speaker == speakerPcIdA ? .a : .b`) | `CloudSessionViewModel.kt:323` uses the shared `sideFor(speaker, originalLang)` | §3.8 attribution is "`speaker == pc_id`, fallback `original_lang == langA`"; `turn_failed` carries the same `speaker` field |
| M2 | med | both (differently) | The same turn payload lands on different panels when `speaker` is present but unmatched, or is `""` | `CloudSessionViewModel.swift:288-293`: a non-empty `speaker` short-circuits to B when `speakerPcIdA` is nil; `""` is treated as absent | `CloudSessionViewModel.kt:337-343`: falls back to `original_lang` when `attributionPcIdA` is nil; `""` is treated as present, so it resolves to B | §3.8. This is the code path that already produced one of the two bugs found earlier; the fallback rule should be identical on both |
| M3 | med | both | Where a finished session lands: iOS goes to History, Android goes Home | `Sources/VocaKit/App/Router.swift:166-169` (`sessionStopped` → History tab) | `app/.../ui/VocaNav.kt:216-220` (pop back to Home; only `EXHAUSTED` navigates further) | §5 is silent; the JSX `handleStop` behaviour iOS cites is the reference. Pick one |
| M4 | med | Android | A successful purchase does not resume the session the user was trying to start | resumes: `Router.swift:73-74, 138-141, 149-156` (`interruptedLaunch`) | `app/.../ui/AppViewModel.kt:124-135` only updates the balance and a message; the paywall stays up until the user dismisses it | §5 routing funnels a blocked Start through the paywall; the JSX resumes. Divergent UX for the money path |
| M5 | med | Android | No in-session language swap on the offline screen | `Sources/VocaSession/Offline/OfflineSessionViewModel.swift:198-205` + `UI/LiveSplitView.swift:56-79` (`onSwap`, offline only) | `session/.../ui/LiveSplitScreen.kt:113` renders the swap glyph as decoration only; `OfflineSessionViewModel.kt` has no `swapLanguages` | §6 (iOS note) has `onSwap` as offline-only; §6 Android lists the same component contract. One app has the feature, the other draws the icon |
| M6 | med | Android | Leg B failing/disconnecting is ignored — the session looks live with no path for B | `CloudSessionViewModel.swift:133-136` wires leg B's `failed`/`disconnected` to the network error | `CloudSessionViewModel.kt:195-196` `if (side != Side.A) return` drops every leg-B state | §3 "`connectionState` `failed`/`disconnected` → error screen `network`" — not scoped to leg A |
| M7 | med | Android | `prepare()` has no decline path and no confirmation before a ~30 MB download | `Sources/VocaSession/Offline/AppleOfflineEngine.swift:61-78` returns `false` when the user cancels Apple's sheet | `session/.../offline/AndroidOfflineEngine.kt:48-52` returns `true` or throws — never `false`; `DownloadConditions.Builder().build()` allows cellular; the "declined" branch at `OfflineSessionViewModel.kt:93` is unreachable in production and only its fake-engine test covers it | §4 "A declined download is not an error" presumes a decline exists |
| M8 | low-med | Android | `end()` after a session error still writes an `ended` history record | `CloudSessionViewModel.swift:81` — `end()` is a no-op once torn down | `CloudSessionViewModel.kt:381-382` guards only `phase is Ended`; after `fail()` the phase is `Error`, so End proceeds to `finish()` | §3.10 persists `ended` on teardown; a session that failed is already recorded by neither. Cosmetic today (the error screen replaces the live UI) but the two states differ |
| L1 | low | iOS | End racing a `closed:true` poll persists the `ended` record twice | `CloudSessionViewModel.swift:279-284` has no `finishing`-style latch before the first `await` | `CloudSessionViewModel.kt:390-401` latches `finishing` before `persist` | Harmless: both stores upsert on `sessionId` (`History/HistoryStore.swift:76`, `app/.../history/SessionEntity.kt:44`). Listed because it is the same class of race Android fixed deliberately |
| L2 | low | iOS | No safety net when the live screen goes away without End | none — `Sources/VocaSession/UI/LiveScreen.swift` has no `onDisappear`, and `TaskScheduler` tokens are only cancelled explicitly (`Scheduling.swift:24-28`) | `CloudSessionViewModel.kt:447-454` `onCleared` persists `ended` and hangs both legs up | §3.10. Android needs it (system back pops the live destination); iOS's only exit today is End, so this is latent, not live |
| L3 | low | iOS | The language table and offline code list exist twice and must be hand-synced | `Sources/VocaKit/App/Languages.swift:45` and `Sources/VocaSession/Models.swift:158` are independent copies of `offlineCodes` + `canTranslateOffline` | single copy in `core/.../offline/OfflineEngine.kt:14-23` | §4/§5 — a future edit to one copy silently diverges the routing check from the engine check |

### Checked and equivalent (no finding)

- **PTT hold gate order** — both refuse unless `phase == live`, own state `ready`
  and the other side not pressing; both disable the *other* leg's track before
  the gate call and enable their own only after `hold` returns; both revert to
  `ready` on gate failure, guarded by a press token so a stale gate cannot
  unmute. `CloudSessionViewModel.swift:297-325` / `CloudSessionViewModel.kt:218-251`.
- **Release short-circuit and fallback** — `flushed_bytes` absent or 0 →
  `ready` immediately; otherwise `translating` until a matching turn or the
  fallback. Both use 36 s (`CloudSessionViewModel.swift:15` /
  `CloudSessionViewModel.kt:508`). Per-side FIFO serialisation on both
  (`queueGate` / `Mutex`).
- **Turn panel semantics** — a turn spoken by A is `mine` on A and the
  translation on B, on both, including after teardown (both keep a separate
  copy of leg A's `pc_id`: `CloudSessionViewModel.swift:34` /
  `CloudSessionViewModel.kt:106`). The residual differences are M1/M2.
- **Metering** — total elapsed, never a delta, every 15 s and on End, on both;
  `enforced:false` → infinite budget on both; budget reached → report, persist,
  tear down, `exhausted` on both. Missing `enforced` decodes as `true` on both
  (`Models.swift:239` / `ApiModels.kt:159` + `metered = enforced != false`).
- **Teardown** — both hang up **both** `pc_id`s, cancel timer and poll, close
  legs, persist `ended` with duration and transcript; `closed:true` ends the
  session on both.
- **Entitlement routing** — the §5 Start order is identical, expression for
  expression (`Store/EntitlementStore.swift:74-80` /
  `app/.../store/EntitlementStore.kt:59-64`), including the tri-state offline
  switch (`nil` follows tier, then sticks) and `showsAds`.
- **Purchases** — Android acknowledges only after `verified && pro`
  (`PlayPurchasesService.kt:89-99`, and the same rule on the launch-sync path at
  :77-79); iOS has no acknowledge concept and finishes the StoreKit transaction
  only after it holds the JWS (`StoreClient.swift:95-98`). Both handle
  `cancelled`, `pending` and restore.
- **Ads** — neither SDK is initialised without a configured unit id
  (`Store/AdsService.swift:33-40` also requires `GADApplicationIdentifier`;
  `app/.../ads/AdsService.kt:23-29`), and the banner is gated to the tab-bar
  screens on both, never on a live screen (`Screens/RootView.swift:20-24` /
  `VocaNav.kt:62`).
- **Offline language sets** — iOS `en zh ja ko es fr de ar hi`
  (`Languages.swift:45`), Android the same plus `fil` mapped to ML Kit `tl`
  (`OfflineEngine.kt:15-18`); `yue` is never offline and is withdrawn from both
  pickers. Android recognition uses `cmn-Hans-CN` for `zh`
  (`core/.../model/Languages.kt:45`), iOS uses `zh-CN` for `SFSpeechRecognizer`
  and `zh-Hans` for Apple Translation (`AppleOfflineEngine.swift:29`), which is
  correct per platform. Apple's directional `prepare` does both directions
  (`AppleOfflineEngine.swift:63-76`). TTS is local-voice-only on both
  (`AndroidOfflineEngine.kt:273-278` / `AppleOfflineEngine` synthesizer).
- **§2 wire format** — every request/response field name matches the contract on
  both (`API/Models.swift` `CodingKeys` vs `core/.../model/ApiModels.kt`
  `@SerialName`), including `caller_name`, `caller_language`, `client_id`,
  `session_id`, `pc_id`, `flushed_bytes`, `original_lang`, `speaker_name`,
  `session_seconds`, `store_reachable`, `seconds_*`, `sandbox_ok`. Both accept
  the string-or-array form of `iceServers.urls` and fail the session on an
  empty list. 402 decodes to a typed error carrying the balance on both.
- **WebRTC legs** — `relay`-only, one mic clone per leg added *enabled* for
  negotiation and disabled once connected, 5 s ICE gathering cap, on both
  (`Cloud/WebRTCLeg.swift:35,44,95` / `cloud/WebRtcLeg.kt:65,144-147,205`).
- **Badge** — the model strings differ (`"On device"` vs `"ON DEVICE"`) but
  both views uppercase at render (`VocaTheme.swift:149` `.textCase(.uppercase)`,
  `LiveSplitScreen.kt:128` `badge.uppercase()`), so the screens agree. Not a
  finding; noted only because the two test suites assert different literals.

## Fixes for the HIGH findings

### H1 — iOS 402 must route to the `exhausted` paywall

In `Sources/VocaSession/Cloud/CloudSessionViewModel.swift`, catch the typed
error before the generic handler in `setupSession()`:

```swift
} catch let error as APIError where error.isPaymentRequired {
    if tornDown { return }
    await persistSession(status: "ended")
    teardown()
    phase = .ended(.exhausted)
    publish()
} catch {
    if tornDown { return }
    fail(.network)
}
```

`.ended(.exhausted)` already reaches the paywall through
`LiveScreen.swift:123-126` → `LiveCallbacks.onExhausted` →
`AppRouter.sessionExhausted()` (`Router.swift:171-173`), so no other change is
needed. Replace `testPaymentRequiredIsNetworkErrorAndTearsDown`
(`CloudSessionViewModelTests.swift:114`) with the Android assertion: the phase
is `.ended(.exhausted)` and both known legs are hung up. Note the 402 can also
arrive on leg B after leg A succeeded — the same branch covers it because both
`offer` calls are inside the one `do`.

### H2 — iOS must be able to say `store_reachable:false`

`Transaction.currentEntitlements` never throws, so today "nothing owned" and
"could not read the receipt" are the same `nil`. Give the store client a
tri-state and let `sync()` take its existing no-downgrade branch:

1. In `Store/StoreClient.swift`, change
   `func currentEntitlement(id:) async -> StoreEntitlement?` to
   `async throws -> StoreEntitlement?`, and in `StoreKitClient` throw
   `StoreError.unavailable` when `AppStore` has no signed-in account / the
   receipt is unavailable (`Transaction.currentEntitlements` yielding only
   `.unverified` results is the practical signal; a fresh install before the
   receipt syncs is the case that bites).
2. In `Store/PurchasesService.swift:54-59`:

```swift
public func currentReceipt() async -> Held {
    do {
        guard let held = try await store.currentEntitlement(id: productID) else {
            return Held(reachable: true, receipt: "")   // verified "no subscription"
        }
        return Held(reachable: true, receipt: held.receipt)
    } catch {
        return Held(reachable: false, receipt: "")      // could not ask — must not downgrade
    }
}
```

`EntitlementStore.sync()` (`:97-102`) then takes the `GET /api/entitlement`
branch, which is exactly what `PlayPurchasesService.syncEntitlement` does. Add
the iOS mirror of Android's `a store we could not ask must not downgrade`
(`PlayPurchasesServiceTest.kt:212`).

### H3 — iOS must install a `supported` pair in-session

In `Sources/VocaSession/Offline/OfflineSessionViewModel.swift:92-104`, replace
the `.installed`-only guard with the Android shape:

```swift
switch await engine.pairStatus(a, b) {
case .installed: break
case .supported:
    guard try await engine.prepare(a, b) else {
        throw OfflineEngineError.translationUnavailable(from: a, to: b)  // declined: notice, not error
    }
case .unsupported:
    throw OfflineEngineError.translationUnavailable(from: a, to: b)
}
```

`AppleOfflineEngine.prepare` already presents Apple's own download sheet (both
directions) and returns `false` on cancel, so the user confirmation the
contract implies is there on iOS — which is also why Android should gain one
(M7) rather than iOS losing this path. Replace
`testSupportedButNotInstalledPairAlsoRefusesTheTurn`
(`OfflineSessionViewModelTests.swift:140`) with the Android pair of tests: a
supported pair downloads before the first turn, and a declined download is a
notice that leaves the session usable.

## CONTRACT §5 functionality checklist

`tested` = a real test named below exercises it; `implemented` = code read and
correct, no test; `divergent` = works but differs from the other platform (see
findings). Test names are abbreviated; iOS tests live in
`ios/VocaKit/Tests/`, Android unit tests in `*/src/test/`.

| # | Feature | iOS | Android | Evidence |
|---|---|---|---|---|
| 1 | Language table, `yue` withdrawn from picker, flags | implemented | implemented | `Languages.swift:25-37`, `core/model/Languages.kt:20-23` — no test on either |
| 2 | Free tier: offline ON by default, on-device, no cloud minutes | tested | tested | iOS `testOfflineSwitchDefaultsToTierAndSticksOnceTouched`; Android `the offline switch is tri-state and sticks once touched` |
| 3 | Pro: `vocare_pro_monthly`, 60 min, minutes in the tier chip | tested | tested | iOS `testProductIdMatchesTheContract`, `testHomeRendersForAProUserWithMinutesLeft`; Android `homeShowsMinutesLeftForPro` (instrumented — needs device) |
| 4 | Offline switch tri-state | tested | tested | as #2 |
| 5 | Start routing order (§5) | tested | tested | iOS `EntitlementStoreTests.swift:82-121`; Android `EntitlementStoreTest.kt:96-121` |
| 6 | Purchase: store → activate → server → (Android) acknowledge only then; cancelled/pending/restore | tested | tested | iOS `testPurchaseNeedsServerVerificationBeforeItCountsAsPro`, `testCancelledAndPendingPurchasesDoNotTouchTheServer`, `testRestoreWithNothingOnTheAccountReportsNotFound`; Android `a verified purchase grants pro and acknowledges only afterwards`, `an unverified receipt is never acknowledged`, `restore …` — **but see M4** (post-purchase resume) |
| 6a | `store_reachable` semantics | **divergent (H2)** | tested | Android `a store we could not ask must not downgrade`; iOS `testSyncWithNoEntitlementIsAVerifiedNoSubscription` only covers the reachable case |
| 7 | Banner on Home/History/Settings only, never live, never pro; SDK never started without a unit id | implemented | implemented | `RootView.swift:20-24` + `AdsService.swift:33-40`; `VocaNav.kt:62` + `ads/AdsService.kt:23-29`. No test on either — unverified on device |
| 8 | Every §5 screen reachable | implemented | implemented | iOS `RootView.swift:39-75` covers all 9; Android `VocaNav.kt:95-201` covers all 9. Only Home + Paywall are render-tested (iOS `ScreenRenderTests`, Android `ScreensUiTest`, the latter instrumented) |
| 9 | Persistence: history schema + prefs | tested (partial) | tested (partial) | iOS `testEndHangsUpBothLegsAndPersists`; Android `the persisted record carries both languages, both names and the transcript`. Room/SwiftData themselves are unexercised — see below |
| 10 | ICE fetched, empty fails; two `relay` PCs; clones enabled then disabled | tested | tested | iOS `testEmptyIceServersFailsSession`, `testSetupFollowsProtocolOrderAndTrackHandling`; Android `an empty ICE list fails the session before any offer`, `setup creates one session, two legs, and disables both mics once connected` (fake legs — real `relay`/clone code unrun) |
| 11 | Offer per leg, 5 s ICE cap, clocks start on A `connected` | implemented | implemented | `WebRTCLeg.swift:88-95`, `WebRtcLeg.kt:205` — the cap itself is untested on both |
| 12 | PTT hold rules | tested | tested | iOS `testHoldAndReleaseStateMachine`, `testOtherSideIsLockedOutWhilePressing`, `testGateFailureRevertsToReady`; Android `hold mutes the other leg first and unmutes this one only after the server gate opens`, `the other side is locked out …`, `a failed hold gate reverts the side to ready` |
| 13 | Release: short-circuit, 36 s fallback | tested | tested | iOS `testReleaseWithoutFlushedBytesReturnsToReadyImmediately`, `testTranslatingFallsBackToReadyAfter36Seconds`; Android `release with no flushed bytes …`, `release with flushed bytes waits, then gives up after the 36s fallback` |
| 14 | Turn attribution + `turn_failed` | tested, **divergent (M1, M2)** | tested | iOS `testTurnSpokenByAAppearsTranslatedOnBAndYouSaidOnA`, `testTurnWithoutSpeakerFallsBackToOriginalLanguage`, `testTurnFailedReleasesThatSide`; Android the same plus `attribution survives teardown when the backend sends no original_lang` (no iOS equivalent) |
| 15 | Metering every 15 s / on End; exhaustion → paywall | tested | tested | iOS `testConsumeReportsTotalSecondsEvery15Seconds`, `testConsumeWatermarkIsIdempotentAcrossRepeatedReports`, `testBudgetExhaustionEndsSession`, `testUnmeteredBudgetNeverEnds`; Android the four equivalents (`elapsed seconds are reported every 15 seconds as a total, never a delta`, …) |
| 16 | Teardown: timers, both hangups, persist `ended`; `closed:true` | tested | tested | iOS `testEndHangsUpBothLegsAndPersists`, `testEndIsIdempotent`, `testClosedTrueEndsSession`, `testEndBeforeConnectedStillHangsUpKnownLegs`; Android `closed true ends the session and hangs up both legs`, `end persists exactly one ended record however many times it is tapped`, `teardown stops the clock and the poll loop` |
| 17 | `failed`/`disconnected` → network; mic denied → mic; 402 → paywall | **divergent (H1)**; leg B covered | leg B **not** covered (M6) | iOS `testConnectionLostIsNetworkError`, `testMicDeniedIsMicError`, `testPaymentRequiredIsNetworkErrorAndTearsDown` (wrong expectation); Android `a failed leg routes to the network error screen`, `a denied microphone …`, `a 402 on offer ends the session as exhausted` |
| 18 | On-device STT, `cmn-Hans-CN` for zh, partials on the speaker's panel | tested (partials); locale implemented | tested | iOS `testPartialsAppearOnTheSpeakersOwnPanelOnly`, `testPartialsFromSideBAppearOnPanelB`; Android the same plus `Mandarin uses the device recognition locale` (no iOS locale test) |
| 19 | On-device translate + local-voice TTS, same panel semantics | tested (fake engine) | tested (fake engine) | iOS `testFullTurnRunsSttThenTranslateThenTts`, `testMissingLocalVoiceStillKeepsTheTurn`; Android `a full turn recognises, translates and speaks in the listener's locale`, `a missing local voice still keeps the transcript` |
| 20 | Pair tri-state, in-app install, declined download not an error, `yue` never offline, Android `fil`→`tl` | **divergent (H3)** | tested (M7: decline path unreachable in production) | iOS `testUnsupportedPairRefusesTheTurn`, `testSupportedButNotInstalledPairAlsoRefusesTheTurn` (enshrines H3), `testDeclinedDownloadReturnsFalseAndIsNotAnError`; Android `a supported pair is downloaded before the first turn`, `a declined download is a notice, not an error …` |
| 21 | §8 test set incl. snapshot of Home, Paywall **and LiveSplitView** | partial | partial | Home + Paywall only on both (`ScreenRenderTests.swift:85-128`, `ScreensUiTest.kt:39-110`); **neither platform has a LiveSplitView/LiveSplitScreen snapshot or UI test** |
| 22 | No secrets; ad unit ids and API base from build config | implemented | implemented | `AdsService.swift:12-13` (Info.plist), `android/app/build.gradle.kts:36-40` (env → BuildConfig, sample AdMob app id as the default) |
| 23 | §7 design tokens and fonts | implemented | implemented | `Design/VocaTheme.swift`, `core/theme/VocaTheme.kt` — no automated comparison against the canvas on either side |

## What neither app has ever exercised

The green suites are unit tests over fakes. Everything below is code that has
never run against the thing it talks to, on either platform:

- **Real WebRTC.** Every cloud test uses a fake `PeerLeg`. `WebRTCLeg.swift`
  and `WebRtcLeg.kt` — `relay`-only policy, the per-leg mic clone, the
  enabled-during-negotiation trick, the 5 s ICE gathering cap, remote track
  playout — have never negotiated with the backend. The one behaviour most
  likely to be wrong in the field is the mic clone: nothing proves both legs
  carry audio.
- **Real speech.** `AppleOfflineEngine` (`SFSpeechRecognizer`,
  `TranslationSession`, `AVSpeechSynthesizer`) and `AndroidOfflineEngine`
  (`SpeechRecognizer`, ML Kit, `TextToSpeech`) are entirely untested — Speech
  needs a mic and entitlements, Apple Translation does not run in the
  Simulator, ML Kit needs a real download. Pair status, model download,
  `cmn-Hans-CN`, and local-voice selection are all code-read only.
- **Real purchases.** Both purchase suites use a fake store. StoreKit 2 against
  a sandbox account and Play Billing 8.3.0 against a licence-tester account
  have not been run. Android's acknowledge-only-after-verification rule — the
  one whose failure mode is auto-refunded subscriptions — is proven only
  against `FakeStoreClient`.
- **Room and SwiftData.** `HistoryDatabase`/`SessionDao` and the SwiftData
  `ModelContainer` are never opened in a test; the history tests assert against
  in-memory fakes of `SessionHistoryStore`. No migration has been exercised
  against the existing Capacitor app's database, which the contract says these
  schemas were ported from.
- **AdMob.** Neither `AdsService` is unit-tested and no banner has been
  rendered. The iOS fatal-exception case (missing `GADApplicationIdentifier`)
  is guarded in code but has never been provoked.
- **On-device runs.** `ScreensUiTest.kt` is an `androidTest` (instrumented) —
  it does not run in `testDebugUnitTest`, so it is not part of the green 81.
  No screenshot, no device or emulator run, and no evidence that either app has
  been launched. §8.1 ("compiles clean and runs on a device/simulator; every
  screen in §5 reachable") is unverified on both platforms.
- **The backend.** `tests/test_native_api_contract.py` covers the wire format,
  but no native client has spoken to the deployed backend. Notably the 402 path
  in H1 has only ever been produced by a fake.
