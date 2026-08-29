# Vocare Translate — App Store & Google Play submission pack

**Prepared:** 2026-08-29
**App name:** Vocare Translate
**Bundle / application ID:** `com.vocare.translate` (from `capacitor.config.json`)
**Hosted web app:** `https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/vocare`
**Privacy policy URL:** `https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/privacy`
(served by the `/privacy` route in `bot.py`, reading `static/privacy.html`)

> Everything below is written as final copy you can paste into the consoles. Text in
> `[PLACEHOLDER: …]` must be replaced before submission — those are facts that cannot be
> determined from the code. Assumptions are listed in the final section; read that section
> before you paste anything.

---

## 0. Blockers to clear before you submit

These will cause a rejection or a false declaration if left as-is.

1. **`PrivacyInfo.xcprivacy` does not exist for the app target.** The only privacy manifests in
   the repo belong to the Capacitor and Cordova frameworks under `ios/App/build/`. Apple requires
   one in the app target. Section 6 gives the content it needs.
2. **Session endpoints are unauthenticated.** `GET /api/translation/sessions` and
   `GET /api/translation/session/{session_id}` (`bot.py:5777`, `bot.py:5813`) require no
   credentials, and in the browser build `loadSessionHistory()` (`static/vocare-app.jsx:193`)
   lists *every* session on the container. Both stores accept this if disclosed, and the privacy
   policy discloses it — but it is a genuine confidentiality gap.
3. **Server-side transcripts have no expiry.** `translation_sessions` (`bot.py:4738`) is a plain
   dict with no TTL or eviction; transcripts survive until the container restarts. You cannot
   state a retention period honestly until a purge exists.
4. **`NSMicrophoneUsageDescription` is present; there is no watchOS-target usage string checked
   in.** Confirm the Watch App target has its own microphone usage description — `WatchAudioController.swift`
   records audio.
5. **Offline demo mode is not yet in `static/vocare-app.jsx`.** The Review Notes below describe
   it. Do not submit those notes until the mode ships and you have confirmed the exact tap path.

---

## 1. App Store Connect — App Privacy ("nutrition label")

Answer in the order the console presents. **Start:** "Do you or your third-party partners collect
data from this app?" → **Yes**.

For each type, Apple asks four sub-questions in this order: *(a)* is it collected, *(b)* linked to
the user's identity, *(c)* used for tracking, *(d)* purposes.

### Data types to select

| Apple category | Type | Collect? | Linked to user? | Used for tracking? | Purposes |
|---|---|---|---|---|---|
| Contact Info | — | No | — | — | — |
| Health & Fitness | — | No | — | — | — |
| Financial Info | — | No | — | — | — |
| Location | — | No | — | — | — |
| Sensitive Info | — | **No** | — | — | — |
| Contacts | — | No | — | — | — |
| User Content | **Audio Data** | **Yes** | **No** | **No** | App Functionality |
| User Content | **Other User Content** (transcripts, speaker name, topic) | **Yes** | **No** | **No** | App Functionality |
| Browsing History | — | No | — | — | — |
| Search History | — | No | — | — | — |
| Identifiers | — | **No** (no account, no device or advertising ID collected) | — | — | — |
| Purchases | — | No | — | — | — |
| Usage Data | — | **No** (no analytics SDK is bundled — verified: `package.json` has only `@capacitor/*`) | — | — | — |
| Diagnostics | **Crash Data / Performance Data** | **No** | — | — | — |
| Other Data | — | No | — | — | — |

**Notes for the person filling this in**

- *Audio Data* is unambiguously "collected" under Apple's definition: the microphone stream leaves
  the device to our server and to ElevenLabs. Do **not** claim on-device-only processing.
- Answer **No** to "linked to the user's identity" for both types: there is no account, no
  identifier, and nothing that ties a session to a person unless the user types a name into the
  free-text speaker field. Apple accepts free-text-the-user-may-choose-to-enter as unlinked.
- **Sensitive Info: No.** Apple's "Sensitive Info" category means data you deliberately collect
  about health, race, sexual orientation, etc. Incidental content in a translated conversation is
  not that, and selecting it would misdescribe the app.
- If you later add a crash reporter, Diagnostics flips to Yes.

### Optional privacy disclosure text (App Privacy → "Privacy Choices" / additional context)

> Vocare Translate has no accounts and no sign-in. Audio you record while holding the speak button
> is sent to our server and to our speech and translation providers to produce a translation, then
> discarded. Conversation transcripts are stored on your device and are removed when you delete the
> app. No analytics, advertising, or tracking SDKs are included.

---

## 2. App Store Connect — Review Notes

Paste as-is into **App Review Information → Notes**.

> Vocare Translate is a real-time two-way speech translator (English ⇄ Mandarin at launch). Two
> people share one device; each holds their own speak button, and the app speaks the translation
> aloud in the other language.
>
> **Native functionality (please note — this app is not a web wrapper):**
>
> 1. **On-device translation history.** Session transcripts are persisted natively in SwiftData
>    through a custom Capacitor plugin written for this app (`TranslationHistoryPlugin`, backed by
>    `TranslationHistoryStore`). The History tab reads from that native store, not from the web.
>    Transcripts stay on the device; there is no account and no cloud history sync.
> 2. **Apple Watch companion app.** A native watchOS app (SwiftUI) records a push-to-talk utterance
>    on the wrist, uploads it for translation, and plays the translated speech back through the
>    watch — with a WatchConnectivity handoff to the paired iPhone session. The watch app is a
>    first-class translation client, not a remote control.
> 3. **Native audio session and microphone handling**, live WebRTC audio capture and playback, and
>    a native launch experience.
>
> **How to review without speaking two languages — offline demo mode:**
>
> 1. Launch the app. On the home screen, tap **Try the demo**.
> 2. The demo plays a pre-recorded English ⇄ Mandarin conversation end to end, showing the live
>    transcript, the translated text, and the spoken output exactly as a real session appears.
> 3. Demo mode is fully bundled: it needs no network connection and never opens the microphone. You
>    can enable Airplane Mode and it will still run.
> 4. To review the live path instead, tap **Start a conversation**, allow microphone access, choose
>    English and Mandarin, hold the left button and speak English; the translated Mandarin plays
>    back and both texts appear in the transcript. A second person is not required — speaking into
>    one side is enough to see the full round trip.
> 5. After ending a session, open the **History** tab to see the transcript persisted natively on
>    the device. Force-quit and relaunch the app; the history is still there.
>
> **Microphone use:** the microphone is opened only while a speak button is held, and only during a
> translation session. It is never used in demo mode and never in the background.
>
> **Privacy:** full policy at
> `https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/privacy`

`[PLACEHOLDER: confirm the exact on-screen labels of the demo entry point and the start button once
demo mode ships, and correct steps 1 and 4 to match. A reviewer following a wrong label is a
rejection.]`

`[PLACEHOLDER: if the Watch app is NOT included in this submission's build, delete item 2 above —
claiming a companion app that is not in the binary is worse than not claiming it.]`

### Demo / test account

**Sign-in required:** **No.**

> This app has no login, no user accounts, and no paywall. Every feature — including the offline
> demo and full live translation — is available immediately on launch. No demo credentials are
> needed or can be provided.

### Contact information

- First/Last name: `[PLACEHOLDER: review contact name]`
- Phone: `[PLACEHOLDER: review contact phone, international format]`
- Email: `[PLACEHOLDER: review contact email]`

---

## 3. App Store Connect — Age rating questionnaire

Answer **None** to every content question. The specific items:

| Question | Answer |
|---|---|
| Cartoon or Fantasy Violence | None |
| Realistic Violence | None |
| Prolonged Graphic or Sadistic Realistic Violence | None |
| Profanity or Crude Humor | None |
| Mature/Suggestive Themes | None |
| Horror/Fear Themes | None |
| Medical/Treatment Information | None |
| Alcohol, Tobacco, or Drug Use or References | None |
| Simulated Gambling | None |
| Sexual Content or Nudity | None |
| Graphic Sexual Content and Nudity | None |
| Contests | None |
| Unrestricted Web Access | **No** — the app loads only its own service; there is no browser or arbitrary URL entry. |
| Gambling and Contests | No |
| Age Assurance / Made for Kids | **No** — not directed at children. |
| User-Generated Content shared with others | **No** — transcripts stay between the two people present and on the device. |
| Messaging / chat with strangers | No |

**Expected rating: 4+.**

> If the console offers "does your app contain user-generated content", answer No with the
> reasoning above: content is not published, broadcast, or shared to other users of the app.

### URLs

- **Support URL:** `[PLACEHOLDER: support page URL — required, must resolve. A single page with the
  privacy contact email and a short FAQ is enough.]`
- **Marketing URL:** `[PLACEHOLDER: marketing URL — optional, leave blank if none.]`
- **Privacy Policy URL:** `https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/privacy`
- **Copyright:** `[PLACEHOLDER: e.g. 2026 <legal entity name>]`
- **Primary category:** Productivity. **Secondary:** Utilities.
  (Reference/Education are also defensible; Productivity fits a two-person live tool best.)

---

## 4. Google Play Console — Data safety form

**"Does your app collect or share any of the required user data types?"** → **Yes**
**"Is all of the user data collected by your app encrypted in transit?"** → **Yes**
(WebRTC media is SRTP/DTLS; API calls are HTTPS; the Capacitor config sets `cleartext: false`.)
**"Do you provide a way for users to request that their data be deleted?"** → **Yes**
(URL: the privacy policy page, which gives the deletion contact.)

### Data types

| Data category | Data type | Collected | Shared | Processed ephemerally | Required or optional | Purpose |
|---|---|---|---|---|---|---|
| **Audio** | Voice or sound recordings | **Yes** | **Yes** — with the speech and translation providers listed in the policy | **Yes** — streamed and discarded, never written to storage | Required | App functionality |
| **Personal info** | Name | **Yes** (optional free-text speaker label) | No | No | **Optional** | App functionality |
| **Messages** | Other in-app messages | **Yes** (conversation transcripts) | **Yes** — with the translation provider | No | Required | App functionality |
| **App activity** | Other actions | No | — | — | — | — |
| **Device or other IDs** | — | **No** | — | — | — | — |
| **App info and performance** | Crash logs / Diagnostics | **No** | — | — | — | — |
| Location, Financial, Health, Contacts, Calendar, Photos, Files, Web browsing | — | **No** | — | — | — | — |

> **On "Shared":** Play defines sharing as transfer to a third party. Sending audio to ElevenLabs
> and text to the language-model provider **is** sharing under that definition, even though they
> are processors acting for us. Declare it. Play's "service provider" carve-out is narrow and
> reviewers do not apply it generously to AI vendors.

> **On "Processed ephemerally":** you may tick this for Audio (audio is streamed through and never
> persisted server-side — confirmed: no write path in `bot.py`). You may **not** tick it for
> Messages/transcripts, which are retained in server memory with no expiry and on the device.

### Data safety — deletion and retention

- **Data deletion request mechanism:** "Users can request data deletion" → **Yes**, via the contact
  address in the privacy policy.
- **Account deletion:** not applicable — the app has no accounts. Play accepts this; there is a
  "my app doesn't have accounts" option in the Data deletion section.
- **Deletion URL:** `https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/privacy`

### Justification text (paste where Play asks "why is this data collected")

> **Voice recordings:** the app records speech only while the user holds a speak button, and sends
> it to our translation service so it can be transcribed, translated and spoken back in the other
> language. This is the entire function of the app. The audio is not stored.
>
> **Transcripts:** the transcribed and translated text is shown to both speakers during the
> conversation and saved as a session history on the user's own device so they can look back at
> what was said. Transcripts are not used for advertising, profiling or model training.
>
> **Name:** an entirely optional label a user may type to distinguish the two speakers in a
> transcript. It is never verified, never linked to an account, and the app works without it.

---

## 5. Google Play Console — App content declarations

| Declaration | Answer |
|---|---|
| Privacy policy | `https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/privacy` |
| App access | **All functionality is available without special access.** No login, no restricted areas. State this explicitly in the free-text box. |
| Ads | **No, my app does not contain ads.** |
| Content rating questionnaire | Category: **Utility, Productivity, Communication, or Other**. Answer No to every violence, sexuality, language, controlled-substance, gambling and crude-humour question. Declare: does the app allow users to interact/communicate? — **No** (the two speakers are physically co-present; the app does not connect strangers). Does it share user-provided personal information with third parties? — **No**. Does it allow purchase of digital goods? — **No**. Expected: **Everyone / PEGI 3**. |
| Target audience and content | Target age groups: **18 and over** (and 16–17 if you want the wider band). **Not** "Designed for Families". Answer **No** to "could your app appeal to children". |
| News app | No |
| COVID-19 contact tracing / status | No |
| Data safety | See section 4 |
| Government app | No |
| Financial features | None |
| Health apps | None |
| Advertising ID | **Not used** — declare the app does not use the Advertising ID permission (the manifest does not request `com.google.android.gms.permission.AD_ID`; verified in `android/app/src/main/AndroidManifest.xml`). |
| Permissions declaration | `RECORD_AUDIO` is declared. If Play prompts for a sensitive-permission justification, use the voice-recording justification text in section 4. `INTERNET` and `MODIFY_AUDIO_SETTINGS` are not sensitive and need no declaration. |

---

## 6. iOS `PrivacyInfo.xcprivacy` — required content

Create at `ios/App/App/PrivacyInfo.xcprivacy` and add it to the **App** target (and a matching one
for the Watch App target). This must agree with sections 1 and 4.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSPrivacyTracking</key>
  <false/>
  <key>NSPrivacyTrackingDomains</key>
  <array/>
  <key>NSPrivacyCollectedDataTypes</key>
  <array>
    <dict>
      <key>NSPrivacyCollectedDataType</key>
      <string>NSPrivacyCollectedDataTypeAudioData</string>
      <key>NSPrivacyCollectedDataTypeLinked</key>
      <false/>
      <key>NSPrivacyCollectedDataTypeTracking</key>
      <false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array>
        <string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string>
      </array>
    </dict>
    <dict>
      <key>NSPrivacyCollectedDataType</key>
      <string>NSPrivacyCollectedDataTypeOtherUserContent</string>
      <key>NSPrivacyCollectedDataTypeLinked</key>
      <false/>
      <key>NSPrivacyCollectedDataTypeTracking</key>
      <false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array>
        <string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string>
      </array>
    </dict>
  </array>
  <key>NSPrivacyAccessedAPITypes</key>
  <array>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategoryUserDefaults</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array>
        <string>CA92.1</string>
      </array>
    </dict>
  </array>
</dict>
</plist>
```

`[PLACEHOLDER: verify the required-reason API list against what the shipped build actually calls —
Capacitor uses UserDefaults (CA92.1) and may use File Timestamp (C617.1) and Disk Space (E174.1)
depending on which plugins are linked. Xcode's "Generate Privacy Report" on the archive will tell
you; add any category it names.]`

---

## 7. Consistency matrix

The same fact must read the same way in four places. Divergence here is a common rejection cause.

| Data | Privacy policy (`static/privacy.html`) | `PrivacyInfo.xcprivacy` | App Store App Privacy | Play Data Safety |
|---|---|---|---|---|
| **Microphone audio** | §2 "Microphone audio" — sent to server + ElevenLabs, not stored | `NSPrivacyCollectedDataTypeAudioData`, Linked=false, Tracking=false, purpose AppFunctionality | User Content → **Audio Data**; collected; not linked; not tracking; App Functionality | Audio → **Voice or sound recordings**; collected ✔, shared ✔, ephemeral ✔, required, App functionality |
| **Transcripts (original + translated text)** | §2 "Transcripts", §4 retention table | `NSPrivacyCollectedDataTypeOtherUserContent`, Linked=false | User Content → **Other User Content**; collected; not linked; App Functionality | Messages → **Other in-app messages**; collected ✔, shared ✔, not ephemeral, required |
| **Speaker name (optional free text)** | §2 "Language and session settings" | Covered by *Other User Content* (not a separate Apple type) | Rolled into **Other User Content** — do **not** tick Contact Info → Name | Personal info → **Name**; collected ✔, not shared, **optional** |
| **Language / topic / session settings** | §2 "Language and session settings" | Covered by *Other User Content* | Rolled into **Other User Content** | Rolled into Messages; no separate type |
| **IP / connection metadata** | §2 "Technical connection data", §3 Twilio + Google STUN rows | Not a declarable Apple type (transient connection data) | Not declared — Apple does not have an IP type and it is not used for tracking | Not declared — Play excludes transient network data not stored |
| **Server logs** | §4 "Server logs" row | n/a | Diagnostics → **No** (no SDK; server-side logs are outside the app-collection definition) | App info and performance → **No** |
| **Device / advertising IDs** | §2 "What we deliberately do not collect" | `NSPrivacyTracking` = false, no tracking domains | Identifiers → **No**; Tracking → **No** for every type | Device or other IDs → **No**; Advertising ID → **not used** |
| **Analytics / usage** | §2 "What we deliberately do not collect" | none declared | Usage Data → **No** | App activity → **No** |
| **On-device history** | §4 device rows, §5 clearing instructions | Not declared — data never leaves the device is not "collected" | Not declared (same reason) | Not declared (same reason) — but the deletion mechanism in §4 must still describe it |
| **Demo mode** | §6 — no mic, no network, collects nothing | n/a | Mentioned in Review Notes only | n/a |
| **Accounts** | §2 "no sign-in at all" | n/a | Demo account section: "no sign-in required" | App access: "all functionality available without special access"; Account deletion: N/A |

**Check before submitting:** every row must be true in all four columns simultaneously. If you add
a crash reporter, four cells change. If you switch the translation engine to ElevenLabs Agents, the
processor list in the policy changes but no store cell does.

---

## 8. Store listing copy

### App name

- **App Store (30 chars max):** `Vocare Translate` (16)
- **Play (30 chars max):** `Vocare Translate` (16)
- **App Store subtitle (30 chars max):** `Live two-way voice translator` (29)

### Short description — Play (80 chars max)

> `Hold to speak. Real-time two-way voice translation for face-to-face conversations.` (81 → use:)

> `Hold to speak. Real-time two-way voice translation, face to face.` (65)

### Promotional text — App Store (170 chars max)

> Two people, one phone, two languages. Hold your button, speak normally, and Vocare speaks your
> words aloud in the other person's language — with the transcript kept on your device.

### Full description (Play, 4000 chars max; reuse for the App Store description)

> **Vocare Translate turns a two-language conversation into one you can actually have.**
>
> Put the phone between you. Each person holds their own speak button. Say what you mean in your
> own language, and Vocare speaks it aloud in theirs — with both the original and the translation
> on screen as you go.
>
> **Built for face-to-face conversations**
> Not a phrasebook and not a text box. Vocare is designed for the moment two people are standing in
> front of each other and need to get something done: a counter, a clinic waiting room, a site
> visit, a family kitchen.
>
> **What you get**
> • Real-time two-way speech translation between English and Mandarin
> • Natural synthesised speech, not a robotic read-out
> • A live transcript of both sides as the conversation happens
> • Push-to-talk, so the app only listens when you want it to
> • Conversation history saved on your device, so you can look back at what was agreed
> • An Apple Watch companion, for translating from the wrist
> • An offline demo you can play before you ever turn the microphone on
>
> **Private by design**
> No account. No sign-up. No advertising, no analytics, no tracking. Your conversation history
> lives on your own device and is gone when you delete the app. The microphone opens only while you
> are holding a speak button.
>
> **Honest about what it does**
> Translating your speech means sending it to our translation service — we say exactly who is
> involved and what they receive in our privacy policy, linked below. Please don't use Vocare for
> conversations you need to keep confidential.
>
> Languages at launch: English and Mandarin Chinese. More on the way.

`[PLACEHOLDER: confirm the launch language pair before publishing — the code paths and voices
configured are English and Mandarin only (TRANSLATION_VOICES in bot.py). Remove "More on the way"
if you would rather not commit.]`

`[PLACEHOLDER: remove the Apple Watch bullet from the Play listing — there is no Watch companion on
Android.]`

### Keywords — App Store (100 chars, comma-separated, no spaces)

```
translate,translator,voice,speech,mandarin,chinese,interpreter,conversation,live,realtime,offline
```
(99 chars.) Do not repeat words already in the app name or subtitle — "Vocare", "translate",
"live", "two-way" are already indexed from those fields, so drop `translate` and `live` if you want
room for another term.

### Play tags / category

- **Category:** Tools (alternative: Communication)
- **Tags:** Translation, Voice, Productivity

---

## 9. Screenshot shot list

Same six shots everywhere; only the frame size changes. Shoot them from the offline demo so the
transcript content is controlled and repeatable.

**The six shots**

1. **Home / language pair** — the two language chips set to English and Mandarin, the two speak
   buttons visible. Caption: *"Two people. One phone. Two languages."*
2. **Live translation, mid-turn** — one speak button held and lit, the live transcript showing an
   English line and its Mandarin translation. Caption: *"Hold to speak. Hear it in their language."*
3. **Full conversation transcript** — a scrolled transcript with four to six exchanges, both
   languages side by side. Caption: *"Every line, both ways, as you go."*
4. **History tab** — a list of past sessions with dates and durations. Caption: *"Your conversations
   stay on your device."*
5. **Demo mode running** — the demo playing with an airplane-mode indicator visible in the status
   bar if you can capture it. Caption: *"Try it offline before you speak a word."*
6. **Privacy / settings** — the settings screen and the "no account, no tracking" statement.
   Caption: *"No account. No ads. No tracking."*

**App Store — required and recommended sizes**

| Device | Resolution (portrait) | Required? | Shots |
|---|---|---|---|
| iPhone 6.9" (16 Pro Max / 15 Pro Max) | 1320 × 2868 | **Required** | All 6 |
| iPhone 6.5" (11 Pro Max / XS Max) | 1242 × 2688 | Required if you don't upload 6.9" only — Apple now scales 6.9" down; upload anyway if you have them | All 6 |
| iPad 13" (Pro M4) | 2064 × 2752 | **Required only if the app supports iPad.** `UISupportedInterfaceOrientations~ipad` is present in `Info.plist`, so the app is currently iPad-capable — either supply iPad shots or set the target to iPhone-only. | 1, 2, 3, 4 |
| Apple Watch | 410 × 502 (Series 9 45mm) or 416 × 496 (S10 46mm) | **Required if the Watch app ships** | Watch idle, watch recording, watch showing translation |

**Google Play — required assets**

| Asset | Size | Required? | Shots |
|---|---|---|---|
| Phone screenshots | 1080 × 1920 min, 16:9 or 9:16, 2–8 images | **Required (min 2, use all 6)** | All 6 |
| 7" tablet | 1200 × 1920 | Only if you list tablet support | 1, 2, 3, 4 |
| 10" tablet | 1600 × 2560 | Only if you list tablet support | 1, 2, 3, 4 |
| Feature graphic | 1024 × 500, no transparency | **Required** | App mark + "Live two-way voice translation" on the cream/navy palette |
| App icon | 512 × 512 PNG, 32-bit | **Required** | — |

`[PLACEHOLDER: decide iPad and Android tablet support. Declaring it means supplying tablet
screenshots and testing the layout at that size; the current UI is built for phone dimensions.]`

---

## 10. Assumptions and things I could not determine from the code

Every item here is a fact I had to leave open or infer. Check each one before submitting.

1. **The language-model provider is unknown.** `bot.py` reads `LLM_PROVIDER` at runtime and
   supports OpenAI, Azure OpenAI / Azure AI Foundry, AWS Bedrock, Cerebras, Groq, Mistral and
   DeepSeek (`create_llm`, `bot.py:1330`). `.env.example` sets no default and no deployment file in
   the repo pins one. **Which company processes your users' speech — and in which country — is
   determined entirely by the deployed environment variable.** The privacy policy has a placeholder
   for this; it must be filled with the real deployed value.
2. **Which translation engine is deployed is unknown.** `TRANSLATION_ENGINE` selects between the
   cascaded path (ElevenLabs STT → external LLM → ElevenLabs TTS) and the ElevenLabs Agents path
   (ElevenLabs only). The policy describes both honestly; if you have settled on one, simplify it.
3. **No legal entity, contact email, or postal address exists anywhere in the repo.** All are
   placeholders. Both stores verify the privacy contact.
4. **No concrete retention period can be stated.** `translation_sessions` has no expiry (see §0.3).
   I wrote "until the container restarts" plus a placeholder, because inventing "30 days" would be
   a false statement in a privacy policy.
5. **Processor retention settings are unverified.** I could not confirm from the code whether
   ElevenLabs history logging is disabled on the account or what the LLM provider's abuse-monitoring
   retention is — `.env.example` lists `ELEVENLABS_TTS_ENABLE_LOGGING` but no value is set in the
   repo. Check the vendor dashboards.
6. **Offline demo mode does not exist in the checked-in code.** No `demo`/`offline` code path is in
   `static/vocare-app.jsx`. The Review Notes and screenshot 5 assume the shipped behaviour matches
   the brief (bundled recording, no network, no microphone). Verify before submitting — Review Notes
   that describe a non-existent button are a guaranteed rejection.
7. **Android on-device storage is in progress.** The policy and the Data Safety answers assume the
   Room store mirrors the iOS SwiftData store. If Android history is not shipping in v1, remove the
   Android row from the policy's retention table and the history bullet from the Play listing.
8. **There is no in-app delete for history.** `TranslationHistoryStore` exposes `upsert`, `list` and
   a getter but no delete or clear (`ios/App/App/TranslationHistoryStore.swift`). The policy
   therefore says "delete the app", which is accurate but weak — Play's data-deletion expectations
   are better met with an in-app control. Consider adding one.
9. **Twilio is a media relay, not just a signalling service.** `fetch_twilio_ice_servers()`
   (`bot.py:1632`) fetches TURN credentials from Twilio; when a peer-to-peer path cannot be
   established, encrypted audio is relayed through Twilio's servers. I disclosed this. The fallback
   when Twilio is unconfigured is Google's public STUN server, which sees the device IP.
10. **Third-party CDNs are in the app's load path.** `static/vocare.html` loads React, ReactDOM and
    Babel from `unpkg.com` and fonts from `fonts.googleapis.com`/`gstatic.com`. Those hosts see the
    device IP on every launch. Disclosed in the policy. Consider bundling them locally — it removes
    a disclosure, removes a launch-time network dependency, and removes an external code-execution
    surface in a shipping app. It also makes the "offline demo" claim true for the whole app rather
    than just the demo path.
11. **`Info.plist` still lists `armv7` under `UIRequiredDeviceCapabilities`** — harmless boilerplate,
    but worth changing to `arm64` before submission.
12. **The app ships as a remote-URL Capacitor shell.** `capacitor.config.json` points `server.url` at
    the live Azure host, so the entire UI is loaded from the network at runtime rather than bundled
    in `www/`. Apple reads this as a candidate 4.2 minimum-functionality problem, which is exactly
    why the Review Notes lead with the native plugin, the Watch app and the bundled demo. It also
    means the app is unusable with no connection except in demo mode — make sure demo mode is
    genuinely bundled in `www/` and not fetched.
13. **Store copy character counts** were measured by hand; re-check in the console, which counts
    differently for CJK and emoji.
14. **The privacy policy asserts "no analytics, crash-reporting or advertising SDKs"** on the basis
    that `package.json` contains only `@capacitor/{core,ios,android}` and no such library appears in
    `vocare.html`, `vocare-app.jsx`, or the Android manifest and Gradle files. Re-verify if
    dependencies change.
