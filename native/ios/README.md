# Voca — native iOS

The fully native rewrite of the Voca live-translation app, built against
[`native/CONTRACT.md`](../CONTRACT.md). The Capacitor app in `ios/` is
untouched and keeps working; nothing here shares code with it.

```
native/ios/
  VocaApp.xcodeproj      thin app target (synchronized folder group over VocaApp/)
  VocaApp/               @main App, Info.plist, Assets, PrivacyInfo, VocaApp.storekit
  VocaKit/               the Swift package — all logic and SwiftUI
    Sources/VocaKit/     Design · API · Store · History · App · Screens
    Sources/VocaSession/ Cloud (WebRTC) · Offline (Speech/Translation/TTS) · UI
    Tests/VocaKitTests/  · Tests/VocaSessionTests/
```

`VocaSession` depends on `VocaKit`, never the reverse. The app target imports
both and composes them.

## Requirements

- Xcode 16 or newer with an iOS 18 SDK (verified on Xcode with the
  iOS 26.5 simulator SDK), Swift 6 language mode.
- Package dependencies resolve from the network on first build:
  `stasel/WebRTC` (exact 152.0.0) and `swift-package-manager-google-mobile-ads`
  (from 13.9.0, which also pulls GoogleUserMessagingPlatform).

## Build

```bash
cd native/ios/VocaKit
xcodebuild -scheme VocaKit     -destination 'generic/platform=iOS Simulator' build
xcodebuild -scheme VocaSession -destination 'generic/platform=iOS Simulator' build

cd native/ios
xcodebuild -project VocaApp.xcodeproj -scheme VocaApp \
  -destination 'generic/platform=iOS Simulator' build
```

Plain `swift build` at the package root **does not work** and is not expected
to: `VocaKit` imports `GoogleMobileAds`, which has no macOS slice. Always build
for an iOS destination.

## Test

Tests live in the package. The auto-generated `VocaKit` and `VocaSession`
schemes are build-only, so the test action runs through `VocaKit-Package`:

```bash
cd native/ios/VocaKit
xcodebuild test -scheme VocaKit-Package \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'

# one target at a time
xcodebuild test -scheme VocaKit-Package -destination '…' -only-testing:VocaKitTests
xcodebuild test -scheme VocaKit-Package -destination '…' -only-testing:VocaSessionTests
```

Last full run: **VocaKitTests 58 passed, VocaSessionTests 47 passed, 0 failures.**

What is covered:

| Area | Tests |
|---|---|
| API client | request/response encoding for every §2 endpoint, 402 → `APIError.paymentRequired`, transport failure → `APIError.network`, all against a `URLProtocol` stub |
| Entitlement | tier / `enforced` / `sandbox_ok` / `store_reachable` semantics, budget derivation (`.infinity` when unmetered), §5 start routing, offline-switch tri-state, ad eligibility |
| Purchases | fake `StoreClient`: purchased, cancelled, pending, restore (found and not found), empty receipt, storefront price |
| Cloud session | fake API + fake legs + manual clock: hold/release state machine, other-side lockout, `flushed_bytes` short-circuit, 36s fallback, turn attribution, 15s consume watermark, budget exhaustion, `closed:true` |
| Offline session | fake `OfflineEngine`: full STT→translate→TTS turn, partials on the speaker's own panel, unsupported/uninstalled pair, declined download, missing local voice, lockout, swap, teardown |
| Render | Home (free / pro / unsupported pair) and Paywall (all three variants) hosted in a `UIWindow` and rasterised; asserts a real, non-blank image |

Several tests read `native/fixtures/*.json` (balances, ICE, offer answer, the
scripted 6-turn poll script). They are found by relative path from the test
sources; if the fixtures are missing, the balance tests fall back to inline
equivalents and the poll-script test fails loudly rather than silently passing.

## Configuration

- API base: `VOCARE_API_BASE` in the app's Info.plist; falls back to the
  production container app URL.
- AdMob: `GADApplicationIdentifier` is Google's public test app id. The banner
  unit id (`VOCARE_ADMOB_IOS_BANNER`) is deliberately **absent**, so the ads SDK
  is never started — supply it from build config when ads are wanted.
- StoreKit: `VocaApp/VocaApp.storekit` (copied from `ios/App/Vocare.storekit`)
  is wired into the VocaApp scheme for local purchase testing.
- Signing: automatic, team `DZZNRYF752`, bundle id `com.vocare.translate.native`.

No secrets are checked in.

## What is NOT verified

Everything below compiles and is exercised only through fakes. None of it has
been run against the real thing:

- **Real WebRTC.** `WebRTCLeg` has never negotiated with the backend. Nothing
  here proves relay-only ICE, the two-leg offer flow, audio actually flowing, or
  the server's answer being accepted. All cloud tests use a fake `PeerLeg`.
- **Real Speech / Translation / TTS.** `AppleOfflineEngine` (SFSpeechRecognizer
  with `requiresOnDeviceRecognition`, `TranslationSession` inside a hidden
  `.translationTask` host, AVSpeechSynthesizer local voices) is untested against
  the frameworks. In particular the hidden hosting controller that presents
  Apple's language-download sheet has never been shown on a device, and the
  `nonisolated(unsafe)` hand-off of `TranslationSession` out of `.translationTask`
  is a compile-time accommodation, not a proven-safe pattern.
- **Real StoreKit purchases.** `StoreKitClient` is not covered; only the fake is.
  No sandbox purchase, restore, renewal or refund has been performed, and the
  server-side receipt verification round trip has not been run end to end.
- **No on-device or simulator run.** The app builds for `generic/platform=iOS
  Simulator` but has never been launched. Nothing confirms the screens navigate,
  the mic permission prompts appear, SwiftData history persists across launches,
  or the live split view behaves under a real finger.
- **The backend.** Every API test is stubbed. The fixtures are asserted against
  production by `tests/test_native_api_contract.py`, but this package never
  talks to a server.
- **Design fidelity.** The render tests assert "something drew", not that it
  matches the canvas. No golden images, no visual review.
- **Watch app, ads rendering, localisation.** Not built here.

## Contract deviations

- §6 places `LiveScreen` in `VocaKit/Screens`. It cannot live there: it needs
  `LiveSplitView` and both view models, which are in `VocaSession`, and
  `VocaSession` already depends on `VocaKit`. It is
  `Sources/VocaSession/UI/LiveScreen.swift` instead. `RootView` was written
  generic over its live screen for this reason, and the app target supplies it.
- `AppleOfflineEngine` is adapted to `VocaKit.OfflineAvailability` in the app
  target (a one-line retroactive conformance in `VocaApp.swift`), so VocaKit
  never imports VocaSession.
