# Backend fixtures for the native apps

Machine-readable response samples for every endpoint in `native/CONTRACT.md` §2.
Every shape is derived from the handler in `bot.py` (not from memory) and the key
set of each file is asserted against the live backend by
`tests/test_native_api_contract.py` (`TestFixturesMatchLive`), so a fixture that
drifts from production fails CI.

Load them in unit tests as the stubbed transport's response bodies. Credentials
and SDP in here are placeholders, not real values.

| File | Endpoint | Notes |
|---|---|---|
| `ice.json` | `GET /api/ice` | `{ iceServers: [ {urls, username?, credential?} ] }`. STUN entry has no username/credential. Both PCs use `iceTransportPolicy = relay`; an empty list must fail the session before any offer. |
| `session_create.json` | `POST /api/translation/session` | `{ session_id, status:"waiting" }`. IDs look like `GRC-<16 url-safe chars>`. |
| `offer_answer.json` | `POST /api/translation/offer` | `{ pc_id, sdp, type:"answer" }`. The SDP only contains `typ relay` candidates (the server strips the rest). Called once per leg; store `pc_id` per leg. A 402 body is `{ error:"no_translation_credit", balance:<balance payload> }` — use `balance_pro_exhausted.json` as the `balance`. |
| `poll_events.json` | `GET /api/translation/poll?session_id=` | A scripted 6-turn en↔zh session. `frames[]` are successive poll responses `{ events, closed }`. Includes `status` and `live` events (ignore unknown types), one `turn_failed` for side B, and a final `closed:true` frame. `expected` holds the counts a correct view model must reach when fed the frames in order with `pc_id_a`/`pc_id_b`. |
| `session_detail.json` | `GET /api/translation/session/{id}` | Full detail incl. `transcript` (the `turn` events in order), `live_transcripts`, `live_previews`, `participants[{pc_id,name,language}]`. |
| `balance_free_unenforced.json` | `GET /api/entitlement?subject=` | **Current production**: `tier:"free"`, `seconds_total:0`, `enforced:false` → cloud allowed, `budgetSeconds = ∞`. |
| `balance_free_enforced.json` | same | Free tier with metering on: `seconds_remaining:0` → not cloud-allowed; route to offline or paywall(`unsupported`). |
| `balance_pro.json` | same | Pro with 45 min left (`seconds_used:900` of `3600`). Tier chip shows minutes left. |
| `balance_pro_exhausted.json` | same | Pro, `seconds_remaining:0`, enforced → paywall(`exhausted`); `/offer` returns 402 with this as `balance`. |
| `balance_sandbox.json` | same | Test environment: `sandbox_ok:true`, `durable:false` (no Redis — balances are in-process only). |
| `entitlement_activate_verified.json` | `POST /api/entitlement/activate` | Balance payload plus `verified:true` — the store receipt was checked with Apple/Google and is pro. Android acknowledges the purchase only after receiving `tier:"pro"` from this call. |
| `entitlement_activate_unverified.json` | same | `verified:false` — the server could not verify (no store credentials configured, or store outage). The tier is whatever it was before; the client must not treat this as a purchase success. |

`POST /api/entitlement/consume` returns the same balance payload as `GET /api/entitlement`
(no `verified` key). `POST /api/hangup` returns `{ ok:true, found:bool }`.
`POST /api/translation/ptt` returns `{ ok:true, state:"recording" }` on hold and
`{ ok:true, state:"released", flushed_bytes:<int> }` on release; error bodies are
`{ ok:false, state?, error }` with 404 (participant not ready), 409 (`state:"translating"`,
previous turn in flight), 410 (`state:"disconnected"`), 400 (bad action).

## Turn attribution (poll_events.json)

For each `turn` event: `isA = event.speaker == pc_id_a` (fallback when `speaker`
is absent: `event.original_lang == lang_a`). A turn spoken by A goes on **B's**
panel as "Translated" and on A's own panel as "You said"; `turn_failed` puts that
speaker's side back to `ready`. `closed:true` ends the session: persist, then tear
down.
