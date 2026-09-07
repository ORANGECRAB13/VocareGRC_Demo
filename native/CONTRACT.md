# Voca native apps — shared contract

This document is the single source of truth for the fully native iOS and
Android rewrites of the Voca live-translation app. Five people/agents build
against it in parallel; if something here is wrong, fix it HERE first and tell
the others, do not silently diverge.

The current app is a Capacitor webview (`static/vocare-app.jsx`, ~3.9k lines)
with a few native plugins. That code is the **behavioural reference** — when
this document is silent, do what the JSX does. The backend (`bot.py`) does
**not** change: both native apps speak the exact same HTTP + WebRTC API.

Existing native code worth porting rather than rewriting:

| Feature | iOS | Android |
|---|---|---|
| History store | `ios/App/App/TranslationHistoryStore.swift` (SwiftData) | `android/app/src/main/java/com/vocare/translate/history/*` (Room) |
| On-device translation | `ios/App/App/OfflineTranslatePlugin.swift` (Apple Translation, incl. `prepare()` via SwiftUI `.translationTask`) | ML Kit `com.google.mlkit:translate` |
| Purchases | `ios/App/App/PurchasesPlugin.swift` (StoreKit 2) | `android/.../purchases/PurchasesPlugin.java` (Play Billing **8.3.0** — this is the tested, working version; copy its API usage) |
| Watch app | `ios/App/Vocare Watch App/` (SwiftUI, already native) | — |

---

## 1. Layout and ownership

Everything new lives under `native/`. The Capacitor apps in `ios/` and
`android/` are untouched and keep working.

```
native/
  CONTRACT.md                  ← this file
  ios/
    VocaApp.xcodeproj          ← thin app target; synchronized folder group over VocaApp/
    VocaApp/                   ← @main App, Info.plist, Assets, PrivacyInfo.xcprivacy
    VocaKit/                   ← Swift package, ALL logic + SwiftUI views
      Package.swift            ← declares targets: VocaKit, VocaSession, tests for each
      Sources/VocaKit/         ← owner: ios-app
        Design/  API/  Store/  History/  Screens/  App/
      Sources/VocaSession/     ← owner: ios-session
        Cloud/  Offline/  UI/
      Tests/VocaKitTests/      ← owner: ios-app
      Tests/VocaSessionTests/  ← owner: ios-session
  android/
    settings.gradle.kts, build.gradle.kts, gradle/, gradlew   ← owner: android-app
    app/                       ← owner: android-app (Compose app, theme, nav, screens, api, store, history)
    session/                   ← owner: android-session (WebRTC + offline pipeline + live UI)
```

Ownership is by directory. **Do not edit another owner's directory.** If you
need an interface from another module, it is defined in §6 — code against it,
and if it is missing or wrong, say so rather than working around it.

`ios-app` creates `Package.swift` with **both** library targets and both test
targets declared from the start, so `ios-session` never has to touch it.
`android-app` creates `settings.gradle.kts` with `:session` included from the
start, for the same reason.

Nobody commits. The orchestrator commits per area after review.

---

## 2. Backend API (unchanged; base URL is `VOCARE_API_BASE`)

Production: `https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io`
All JSON. Errors are `{ "error": "..." }` with a 4xx status unless noted.

### Identity
`client_id` — an opaque per-install UUID the app generates once and persists.
It is **not** a credential; it scopes history and the credit balance. Send it
as `client_id` on session creation and as `subject` on entitlement calls.

### Session lifecycle (cloud translation)

| | |
|---|---|
| `GET /api/ice` | → `{ iceServers: [ {urls, username?, credential?} ] }`. **Fails the session if empty.** |
| `POST /api/translation/session` | `{ caller_name, caller_language, topic, client_id }` → `{ session_id, status:"waiting" }` |
| `POST /api/translation/offer` | `{ session_id, language, name, sdp, type:"offer" }` → `{ pc_id, sdp, type:"answer" }`. Called **once per participant leg** (twice per face-to-face session). Returns **402** `{error:"no_translation_credit", balance}` when metering is enforced and the balance is 0. |
| `POST /api/translation/ptt` | `{ pc_id, action: "hold" \| "release" }` → `{ ..., flushed_bytes? }`. Server-side audio gate. |
| `GET /api/translation/poll?session_id=` | → `{ events: [...], closed: bool }`. Drains a queue; poll every 1s while live. |
| `POST /api/hangup` | `{ pc_id }` → `{ ok, found }`. Call for **each** pc_id on teardown. Fire-and-forget. |
| `GET /api/translation/sessions?client_id=` | history list for this install |
| `GET /api/translation/session/{id}` | `{ session_id, caller_name, lang, topic, status, transcript:[...], live_transcripts }` |

Poll event shapes:
```
{ type:"turn", speaker:<pc_id>, original_lang:"en", original:"...", translated:"...", speaker_name? }
{ type:"turn_failed", speaker:<pc_id> }
{ type:"status", ... }   // ignore unknown types
```
`closed: true` means the server ended the session — persist and tear down.

### Entitlement / credits

| | |
|---|---|
| `GET /api/entitlement?subject=` | balance payload |
| `POST /api/entitlement/activate` | `{ subject, platform:"ios"\|"android", receipt, store_reachable:bool }` → balance + `verified:bool` |
| `POST /api/entitlement/consume` | `{ subject, session_id, session_seconds }` → balance. **Total** elapsed seconds for that session, never a delta (idempotent watermark). |

Balance payload:
```
{ tier:"free"|"pro", period:"2026-09", seconds_total, seconds_used, seconds_remaining,
  durable:bool, enforced:bool, sandbox_ok:bool, verified?:bool }
```
- `receipt` = StoreKit 2 `jwsRepresentation` (iOS) or Play `purchaseToken` (Android).
- `store_reachable:true` with empty receipt = verified "no subscription" (downgrades). `false` = "couldn't ask" (must NOT downgrade).
- `enforced:false` (current production) → metering is recorded but nobody is refused; treat as cloud-allowed. A missing field means enforced.

### Watch
`POST /api/watch/translate`, headers `X-Vocare-Source-Language`, `X-Vocare-Target-Language`, body `audio/wav` → translated WAV + text. iOS only; existing Watch app already uses it.

---

## 3. Cloud session protocol (must match the JSX exactly)

1. Request mic. Get ICE servers. Create session → `session_id`. Persist session as `active`.
2. Create **two** peer connections A and B with `iceTransportPolicy = relay` (TURN only — the server also strips non-relay candidates from its answer). Audio only.
3. Clone the mic track once per leg. **Add both clones enabled** during negotiation (mobile Safari needed this; keep it), then disable both once connected.
4. `ontrack` on each PC → play that remote audio. Leg A's remote audio is the translation *for A to hear*; same for B.
5. Create offer per leg, `setLocalDescription`, wait for ICE gathering complete (cap 5s), POST `/offer`, `setRemoteDescription(answer)`, store `pc_id` per leg.
6. When PC A reports `connected`: start the 1s poll loop, the elapsed timer, and metering.
7. **Push-to-talk**, per side, serialised through a per-side queue:
   - Hold: refuse if that side's turnState ≠ `ready` or the other side is pressing. Disable the *other* leg's track. POST ptt `hold` → **only then** enable this leg's track. On failure revert to `ready`.
   - Release: disable this leg's track, mark `translating`, POST ptt `release`. If the response lacks `flushed_bytes` (nothing was captured) mark `ready` immediately. Otherwise stay `translating` until a `turn` event for this speaker arrives, or **36s** fallback.
8. Poll handling: for each `turn`, attribute by `speaker == pc_id` (fallback: `original_lang == langA`). A turn spoken by A is shown on **B's** panel as "Translated" and on A's own panel as "You said". `turn_failed` → that side back to `ready`.
9. Metering: report total elapsed seconds via `/consume` every **15s** and on End. If `budgetSeconds` (from `seconds_remaining`, or ∞ when `enforced:false`) is reached: persist, tear down, route to paywall `exhausted`.
10. Teardown: clear timers, hangup both pc_ids, stop tracks, close PCs, persist `ended` with duration + transcript.

`connectionState` `failed`/`disconnected` → error screen `network`. Mic denied → error screen `mic`.

---

## 4. Offline session pipeline (on-device only, zero network)

Per hold-and-release turn on side S (language `from` → `to`):
1. STT on-device in `from`'s locale (iOS: `SFSpeechRecognizer` with `requiresOnDeviceRecognition`; Android: `SpeechRecognizer` with `EXTRA_PREFER_OFFLINE`, locale `cmn-Hans-CN` for zh — `zh-CN` is not device-supported). Stream partials to the speaker's own panel.
2. Translate `from → to` (Apple Translation via `TranslationSession(installedSource:target:)`; ML Kit `Translator`). English is the pivot on both engines.
3. TTS in `to`'s locale, **local voices only**.
4. Append `{ side, from, to, original, translated }`; same panel semantics as cloud.

Pair availability tri-state: `installed | supported | unsupported`. Language packs are installable **in-app** (ML Kit `downloadModelIfNeeded`; Apple via `prepareTranslation()` inside a hidden SwiftUI `.translationTask` host — Apple is directional, prepare **both** directions). A declined download is not an error.

Supported offline codes: iOS `en zh ja ko es fr de ar hi`; Android adds `fil` (ML Kit code `tl`). Cantonese (`yue`) is never offline.

---

## 5. Product behaviour

### Languages
`en zh yue ja ko es fr de ar hi fil` — display names and flags as in the JSX `LANGUAGES` table. Note VOCA `main` withdrew `yue` from the picker and added flags; **match VOCA main**, not the older JSX.

### Tiers
- **free** (default): offline mode ON by default, on-device translation, banner ad on Home/History/Settings (never on a live screen). No cloud minutes.
- **pro**: A$29.99/mo (`vocare_pro_monthly`), 60 min/month cloud. Tier chip shows minutes left.
- Offline switch preference is tri-state: `nil` → follows tier default; once touched, sticks.
- Routing on Start: offline switch ON and pair offline-capable → offline session. Else if not cloud-allowed → pair offline-capable ? offline : paywall(`unsupported`). Else if metered and balance 0 → paywall(`exhausted`). Else cloud.
- Purchase flow: store purchase → `activate` with receipt → server says pro → (Android only) acknowledge the purchase **only now**. Handle `cancelled`, `pending`. Never show ads to pro.
- The ads SDK must **never** be initialised without a configured ad unit id (missing `GADApplicationIdentifier` is a fatal ObjC exception).

### Screens (all in the Voca design, §7)
Home (logo, tier chip, mascot, offline switch, language pair card, Start) · Language picker (search, flags) · Mic disclosure sheet (once, before first mic use — Play policy) · **Live session** (one shared split view for cloud and offline: rotated top half for B, centre bar with codes / clock / `ON DEVICE` badge or live dot / End, bottom half for A) · Paywall (`upsell` / `unsupported` / `exhausted` variants, localized store price, restore) · History (live + past) · Session detail (transcript) · Settings (session toggles, subscription section, offline languages with install buttons, legal links) · Error (mic / network).

### Persistence
History in the native store (port the existing SwiftData/Room schema: sessionId, callerName, topic, languageA/B, participantA/B, status, durationSeconds, transcript JSON). Preferences: client_id, mic consent, offline switch, settings toggles.

---

## 6. Module interfaces (cross-owner)

### iOS — `VocaSession` exposes, `VocaKit` consumes
```swift
public protocol VocaAPI {                       // implemented in VocaKit/API, injected into VocaSession
  func iceServers() async throws -> [ICEServer]
  func createSession(_ req: CreateSessionRequest) async throws -> String        // session_id
  func offer(_ req: OfferRequest) async throws -> OfferAnswer                   // throws APIError.paymentRequired(balance) on 402
  func ptt(pcId: String, action: PTTAction) async throws -> PTTResult
  func poll(sessionId: String) async throws -> PollResponse
  func hangup(pcId: String) async
  func consume(subject: String, sessionId: String, seconds: Int) async -> Balance?
}
public protocol SessionHistoryStore { func save(_ s: SessionRecord) async }     // VocaKit/History
public struct SessionConfig { langA, langB, nameA, nameB, clientId, budgetSeconds: TimeInterval /* .infinity when unmetered */ }

@MainActor public final class CloudSessionViewModel: ObservableObject { init(config:, api:, history:); start(); hold(side:); release(side:); end(); @Published state: LiveState }
@MainActor public final class OfflineSessionViewModel: ObservableObject { init(config:, engine: OfflineEngine); hold(side:); release(side:); end(); @Published state: LiveState }
public struct LiveState { sideA: SideState; sideB: SideState; elapsed: TimeInterval; phase: .connecting/.live/.ended/.error(SessionError); badge: String? }
public struct SideState { name, langCode, langLabel, pressing: Bool, disabled: Bool, status: String, turns: [TurnView], note: String? }
public struct LiveSplitView: View { init(state: LiveState, onHold:(Side)->Void, onRelease:(Side)->Void, onEnd:()->Void) }  // in VocaSession/UI, used by VocaKit/Screens
public protocol OfflineEngine { pairStatus(a,b) async -> PairStatus; prepare(a,b) async throws -> Bool; recognize(locale:, partial:) async throws -> String; translate(text, from, to) async throws -> String; speak(text, locale) async }
```
Design tokens (`VocaTheme`) and `Icon` live in `VocaKit/Design` and are imported by `VocaSession/UI` — so `VocaSession` depends on `VocaKit`, not the reverse. `VocaKit/Screens/LiveScreen` just hosts `LiveSplitView` with the right view model.

### Android — `:session` exposes, `:app` consumes
Same shape in Kotlin: `interface VocaApi` (Retrofit or Ktor, implemented in `:app`, injected into `:session`), `CloudSessionViewModel`, `OfflineSessionViewModel`, `LiveState`/`SideState`, `@Composable LiveSplitScreen(state, onHold, onRelease, onEnd)`, `interface OfflineEngine`. `:session` depends on a small `:core` module (theme tokens + `VocaApi` interface + models) that `android-app` creates first, so neither module depends on the other's UI.

---

## 7. Design tokens (from the canvas; copy exactly)

Ink `#1C2033` · Ground `#FBF9F7` · Surface `#FFFFFF` · Surface2 `#F1EEFC`
A/brand violet `#6C5CE7` deep `#5546C9` light `#8B7CF0` · B green `#34A46F` deep `#2A8B5D` light `#5FBF8E` · Accent pink `#E0397F` · Error `#D93A5C`
Panels: A idle `#F7F5FF` active `#EBE7FD`; B idle `#F4FAF6` active `#E2F3E9`
Type: **Outfit** (display, 700, tracking −0.02em), **Space Grotesk** (body), **JetBrains Mono** (uppercase micro-labels, tracking .12–.16em). Fonts are in `static/vendor-fonts/*.woff2` — convert/bundle as needed.
Radii: cards 18–22, buttons 20, mic 104px circle. The home mascot is the canvas robot; a static illustration is acceptable in v1, animation is a bonus.

---

## 8. Definition of done (per platform)

1. Compiles clean and runs on a device/simulator; every screen in §5 reachable.
2. **Tests** (real, runnable, in CI):
   - API client: request/response encoding for every endpoint, 402 → typed error, network failure → typed error (stubbed transport).
   - Entitlement: tier/enforced/sandbox routing, budget derivation, `store_reachable` semantics.
   - Cloud session view model with a fake API: hold/release state machine, other-side lockout, `flushed_bytes` short-circuit, 36s fallback, turn attribution to panels, 15s consume watermark, budget exhaustion → ended, closed:true → ended.
   - Offline session view model with a fake engine: full turn, partials on the right panel, declined download, unsupported pair.
   - Purchases service with a fake store: purchased / cancelled / pending / restore; Android acknowledges only after server verification.
   - Snapshot or UI test of Home, Paywall and LiveSplitView.
3. No secrets in the repo. Ad unit ids and API base come from build config.
4. A `README.md` in your directory: how to build, test, and what is not yet verified.

The orchestrator will run: `xcodebuild test` for iOS, `./gradlew testDebugUnitTest assembleDebug` for Android (toolchain permitting), and the Python contract tests in `tests/` against the deployed backend.
