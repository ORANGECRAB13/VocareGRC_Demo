// Vocare Mobile Translation App
// Real-time two-way voice translation via WebRTC

const { useState, useEffect, useRef, useCallback, useMemo } = React;

// ─────────────────────────────────────────────
// Design tokens (mirrors CSS vars in vocare.html)
// ─────────────────────────────────────────────

const T = {
  // ── Voca palette (Live speech translation redesign) ──
  // Person B / green
  teal:        '#34A46F',
  tealLight:   '#E2F3E9',
  tealMid:     '#5FBF8E',
  // Person A / violet — also the brand primary
  amber:       '#6C5CE7',
  amberLight:  '#EBE7FD',
  bg:          '#FBF9F7',
  surface:     '#FFFFFF',
  surface2:    '#F1EEFC',
  textPrimary: '#1C2033',
  textMuted:   '#6E7285',
  textFaint:   '#9EA1AF',
  border:      '#E6E4EC',
  error:       '#D93A5C',
  errorLight:  '#FBE4E9',
  success:     '#34A46F',
  slate:       '#1C2033',
  cream:       '#FBF9F7',

  navy:        '#1C2033',  // brand ink
  accent:      '#E0397F',  // pink highlight
  gold:        '#34A46F',  // live indicator dot
  tealDeep:    '#2A8B5D',  // Person B ink on light surfaces
  tealSoft:    '#5FBF8E',
  tealPale:    '#7ED3A6',
  amberSoft:   '#8B7CF0',  // Person A ink, light
  amberDeep:   '#5546C9',  // Person A pressed

  // Live panels are light in this design, tinted toward each speaker.
  panelIdle:   '#FBF9F7',
  panelAIdle:  '#F7F5FF',
  panelBIdle:  '#F4FAF6',
  panelA:      '#EBE7FD',  // while A records
  panelB:      '#E2F3E9',  // while B records

  // Alpha washes lifted from the canvas
  inkA07:      'rgba(108,92,231,.07)',
  inkA08:      'rgba(108,92,231,.08)',
  inkA14:      'rgba(108,92,231,.14)',
  inkA35:      'rgba(108,92,231,.35)',
  inkB09:      'rgba(52,164,111,.09)',
  inkB14:      'rgba(52,164,111,.14)',
  inkB35:      'rgba(52,164,111,.35)',
  tileA:       'rgba(139,124,240,.18)',
  tileB:       'rgba(52,164,111,.16)',
  hairline:    'rgba(28,32,51,.08)',
  inkMute:     'rgba(28,32,51,.45)',
  // Panels are light now, so what used to be cream-on-navy is ink-on-tint.
  creamMute:   'rgba(28,32,51,.45)',
  creamFaint:  'rgba(28,32,51,.4)',

  // Type
  display:     "'Outfit', system-ui, sans-serif",
  body:        "'Space Grotesk', system-ui, sans-serif",
  mono:        "'JetBrains Mono', ui-monospace, monospace",
};

// ─────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────

const LANGUAGES = [
  { code: 'en', flag: '🇺🇸', label: 'English' },
  { code: 'zh', flag: '🇨🇳', label: 'Mandarin' },
  { code: 'yue', flag: '🇭🇰', label: 'Cantonese' },
  { code: 'ja', flag: '🇯🇵', label: 'Japanese' },
  { code: 'ko', flag: '🇰🇷', label: 'Korean' },
  { code: 'es', flag: '🇪🇸', label: 'Spanish' },
  { code: 'fr', flag: '🇫🇷', label: 'French' },
  { code: 'de', flag: '🇩🇪', label: 'German' },
  { code: 'ar', flag: '🇦🇪', label: 'Arabic' },
  { code: 'hi', flag: '🇮🇳', label: 'Hindi' },
  { code: 'fil', flag: '🇵🇭', label: 'Filipino (Tagalog)' },
];

function getLang(code) {
  return LANGUAGES.find(l => l.code === code) || { code, flag: '🌐', label: code };
}

// ─────────────────────────────────────────────
// On-device translation capability
// ─────────────────────────────────────────────

// Which languages have a first-party on-device translation model, per platform.
// Checked against Apple's published Translation framework list (21 languages)
// and ML Kit's supported-language list (59).
//
// Two gaps matter for this app and are deliberate, not oversights:
//   - Cantonese has NO on-device model on either platform. Apple's only Chinese
//     is Mandarin; ML Kit's only Chinese is "zh", also Mandarin.
//   - Filipino exists on Android (ML Kit calls it "tl") but not on Apple's list.
// Those pairs stay server-only, which is why this table is per-platform rather
// than a single list.
const OFFLINE_TRANSLATION_LANGUAGES = {
  ios: ['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'ar', 'hi'],
  android: ['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'ar', 'hi', 'fil'],
};

// Our codes are BCP-47-ish and mostly pass straight through; ML Kit predates
// the fil/tl rename and only knows "tl".
const MLKIT_LANGUAGE_CODE = { fil: 'tl' };

function nativePlatform() {
  try {
    return window.Capacitor?.getPlatform?.() || 'web';
  } catch (_) {
    return 'web';
  }
}

function offlineTranslationLanguages() {
  return OFFLINE_TRANSLATION_LANGUAGES[nativePlatform()] || [];
}

// Whether this pair could be translated with no network, on this platform.
// Says nothing about whether the models are actually downloaded yet — that is a
// separate, asynchronous question the plugin answers.
function canTranslateOffline(a, b) {
  if (!a || !b || a === b) return false;
  const supported = offlineTranslationLanguages();
  return supported.includes(a) && supported.includes(b);
}

// Two very different engines behind one interface.
//
//   mlkit (Android) — per-LANGUAGE models the app can download itself.
//   apple (iOS 26+) — per-PAIR availability, and the headless API deliberately
//                     CANNOT download. Packs come from Settings > Apps >
//                     Translate. Pretending otherwise would mean offering a
//                     button that cannot work.
//
// Both are absent in a browser, so this returns null and callers fall back to
// the server — the same shape as nativeHistoryPlugin().
function offlineEngine() {
  const p = window.Capacitor?.Plugins;
  if (!p) return null;
  if (p.VocareOfflineTranslate) return { kind: 'apple', plugin: p.VocareOfflineTranslate };
  if (p.Translation) return { kind: 'mlkit', plugin: p.Translation };
  return null;
}

function mlkitCode(code) {
  return MLKIT_LANGUAGE_CODE[code] || code;
}

// Both engines can now install languages from inside the app:
//   mlkit — downloads the per-language models itself.
//   apple — cannot download from the headless API, but the plugin's prepare()
//           hosts a SwiftUI view to reach prepareTranslation(), which presents
//           Apple's own download sheet. We never draw that sheet or see its
//           progress, so a prepare() call either comes back installed or comes
//           back declined.
function offlineCanSelfDownload() {
  return offlineEngine() !== null;
}

// Is this pair ready to translate with no network right now?
// Returns 'installed' | 'supported' | 'unsupported' | null (engine absent).
// 'supported' and 'unsupported' are different states deserving different UI:
// one is a download away, the other will never work on this device.
async function offlinePairStatus(a, b) {
  const engine = offlineEngine();
  if (!engine || !canTranslateOffline(a, b)) return null;
  try {
    if (engine.kind === 'apple') {
      const { status } = await engine.plugin.pairStatus({ source: a, target: b });
      return status || 'unsupported';
    }
    const { languages = [] } = await engine.plugin.getDownloadedModels();
    const have = new Set(languages);
    return have.has(mlkitCode(a)) && have.has(mlkitCode(b)) ? 'installed' : 'supported';
  } catch (error) {
    console.warn('Could not read offline translation availability:', error);
    return null;
  }
}

// Models are ~30MB each and download over any connection, so this stays an
// explicit user action behind a button, never something that happens on launch.
//
// The two engines disagree about what a "language" is — ML Kit installs one
// model per language, Apple installs per direction — so this takes the pair and
// lets each engine do the right thing with it. Resolves true when the pair is
// ready, false when the user declined.
async function downloadOfflinePair(a, b) {
  const engine = offlineEngine();
  if (!engine) throw new Error('offline_unavailable');

  if (engine.kind === 'mlkit') {
    for (const code of [a, b]) {
      await engine.plugin.downloadModel({ language: mlkitCode(code) });
    }
    return true;
  }

  // Apple is directional: preparing en→zh does not prepare zh→en, and this app
  // always translates both ways, so ask for both.
  for (const [source, target] of [[a, b], [b, a]]) {
    const { installed } = await engine.plugin.prepare({ source, target });
    if (!installed) return false;
  }
  return true;
}

async function translateOffline(text, sourceCode, targetCode) {
  const engine = offlineEngine();
  if (!engine || !canTranslateOffline(sourceCode, targetCode)) return null;
  try {
    if (engine.kind === 'apple') {
      const { text: translated } = await engine.plugin.translate({
        text, source: sourceCode, target: targetCode,
      });
      return (translated || '').trim() || null;
    }
    const { text: translated } = await engine.plugin.translate({
      text,
      sourceLanguage: mlkitCode(sourceCode),
      targetLanguage: mlkitCode(targetCode),
    });
    return (translated || '').trim() || null;
  } catch (error) {
    // Usually a missing language pack. The caller decides whether to prompt for
    // a download or fall back to the server.
    console.warn('On-device translation failed:', error);
    return null;
  }
}

// ─────────────────────────────────────────────
// API base
// ─────────────────────────────────────────────

// The native shells bundle this UI locally, so their API calls are cross-origin
// and need an absolute base injected at build time by scripts/build-www.mjs.
// In the browser at /vocare nothing is injected, apiBase() is '', and every
// request stays same-origin exactly as before.
function apiBase() {
  return window.VOCARE_API_BASE || '';
}

function isNativeBundle() {
  return Boolean(window.VOCARE_NATIVE_BUNDLE);
}

function api(path, opts) {
  return fetch(apiBase() + path, opts);
}

// Opaque per-install id so the server can scope the session list to this device.
// The list endpoint used to return every session on the server to any caller.
// This is deliberately NOT an identity or a credential — it only stops one
// device seeing another's conversations. If storage is unavailable we fall back
// to a per-load value, which just means history looks empty rather than leaking.
let _clientId = null;
function clientId() {
  if (_clientId) return _clientId;
  try {
    _clientId = window.localStorage.getItem('vocare.clientId');
    if (!_clientId) {
      _clientId = (crypto.randomUUID && crypto.randomUUID())
        || `c-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      window.localStorage.setItem('vocare.clientId', _clientId);
    }
  } catch (_) {
    _clientId = _clientId || `c-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  return _clientId;
}

// ─────────────────────────────────────────────
// Tiers, purchases and translation-minute credits
// ─────────────────────────────────────────────
//
//   free — everything runs on the device. Same screens, same gestures, but
//          the offline engine does the work and a banner ad sits under it.
//          No server call, so no per-minute cost to us and no credit meter.
//   pro  — AUD 29.99/month for PLAN_MINUTES minutes of cloud translation,
//          which is the two-agent face-to-face path.
//
// The store is the source of truth for *whether* someone is subscribed; the
// server is the source of truth for *how many minutes they have left*. Keeping
// those apart matters: a reinstall restores the purchase but must not restore
// the minutes already spent this month.

const PLAN_PRICE_FALLBACK = 'A$29.99';
const PLAN_MINUTES = 60;
const PRO_PRODUCT_ID = 'vocare_pro_monthly';

// The native plugin is ours: StoreKit 2 on iOS (ios/App/App/PurchasesPlugin.swift),
// Play Billing on Android (android/.../purchases/PurchasesPlugin.java). Both hand
// back a store receipt and nothing else — the tier is whatever the server says
// after it verifies that receipt with Apple or Google. A device that lies about
// owning a subscription gets a verified "free" and no minutes.
//
// Absent in a browser, so every helper degrades to "no store, free tier" instead
// of throwing.
function purchasesPlugin() {
  return window.Capacitor?.Plugins?.VocarePurchases || null;
}

function admobPlugin() {
  return window.Capacitor?.Plugins?.AdMob || null;
}

function storePlatform() {
  return nativePlatform() === 'ios' ? 'ios' : 'android';
}

// StoreKit calls it `jws`, Play calls it `purchaseToken`. One shape from here on.
function receiptOf(result) {
  return result?.jws || result?.purchaseToken || '';
}

// A subscription can renew, lapse or be refunded while the app is closed, so the
// plugins emit `entitlementChanged` and we re-verify rather than trusting the
// event's contents.
function onEntitlementChanged(handler) {
  const plugin = purchasesPlugin();
  if (!plugin?.addListener) return () => {};
  const registration = plugin.addListener('entitlementChanged', handler);
  return () => { Promise.resolve(registration).then(r => r?.remove?.()).catch(() => {}); };
}

// The store's localized price, so the paywall shows what the user will actually
// be charged in their storefront rather than an Australian price everywhere.
async function storePrice() {
  const plugin = purchasesPlugin();
  if (!plugin) return null;
  try {
    const product = await plugin.getProduct();
    return product?.displayPrice || null;
  } catch (error) {
    console.warn('Could not read product price:', error);
    return null;
  }
}

// What this device currently owns. Returns the receipt to verify, plus whether
// the store was actually reachable — the server needs to tell "no subscription"
// apart from "could not ask", and only the first should downgrade anyone.
async function currentReceipt() {
  const plugin = purchasesPlugin();
  if (!plugin) return { reachable: false, receipt: '' };
  try {
    const held = await plugin.currentEntitlement();
    return { reachable: true, receipt: held?.active ? receiptOf(held) : '' };
  } catch (error) {
    console.warn('Could not read entitlement:', error);
    return { reachable: false, receipt: '' };
  }
}

async function purchasePro() {
  const plugin = purchasesPlugin();
  if (!plugin) throw new Error('in_app_purchase_unavailable');
  const result = await plugin.purchase();
  if (result?.status === 'cancelled') return { status: 'cancelled' };
  if (result?.status === 'pending') return { status: 'pending' };
  if (result?.status !== 'purchased') throw new Error('purchase_failed');
  return { status: 'purchased', receipt: receiptOf(result), raw: result };
}

async function restorePurchases() {
  const plugin = purchasesPlugin();
  if (!plugin) throw new Error('in_app_purchase_unavailable');
  const held = await plugin.restore();
  return held?.active ? { status: 'purchased', receipt: receiptOf(held), raw: held } : { status: 'none' };
}

// Play auto-refunds a purchase that is not acknowledged within three days. We
// acknowledge only once the server has accepted the receipt, so a purchase we
// could not verify costs the user nothing. StoreKit has no equivalent step.
async function acknowledgeIfNeeded(raw) {
  const plugin = purchasesPlugin();
  if (!plugin?.acknowledge || !raw?.purchaseToken || raw.acknowledged) return;
  try {
    await plugin.acknowledge({ purchaseToken: raw.purchaseToken });
  } catch (error) {
    console.warn('Could not acknowledge purchase:', error);
  }
}

// ── Ads (free tier only) ──
// Ads are never shown to a subscriber, and never on a live translation screen:
// an ad over a medical or legal conversation is the wrong call regardless of
// what it earns.
async function showFreeTierBanner() {
  const plugin = admobPlugin();
  if (!plugin) return;

  // Starting the Google Mobile Ads SDK without GADApplicationIdentifier in
  // Info.plist raises GADInvalidInitializationException, which is an uncaught
  // ObjC exception — it kills the app, it does not fail the call. So refuse to
  // initialize unless an ad unit is actually configured for this platform.
  // A missing ad id must cost us a banner, never the session someone is in.
  const adId = nativePlatform() === 'ios'
    ? window.VOCARE_ADMOB_IOS_BANNER
    : window.VOCARE_ADMOB_ANDROID_BANNER;
  if (!adId) return;

  try {
    await plugin.initialize({ initializeForTesting: Boolean(window.VOCARE_ADS_TEST) });
    await plugin.showBanner({
      adId,
      position: 'BOTTOM_CENTER',
      margin: 82,  // clears the tab bar
    });
  } catch (error) {
    console.warn('Could not show banner:', error);
  }
}

async function hideFreeTierBanner() {
  try { await admobPlugin()?.hideBanner(); } catch (_) {}
}

// ── Credit balance ──

async function fetchBalance() {
  const res = await api(`/api/entitlement?subject=${encodeURIComponent(clientId())}`);
  if (!res.ok) throw new Error(`balance ${res.status}`);
  return res.json();
}

async function activateEntitlement({ receipt = '', reachable = false } = {}) {
  const res = await api('/api/entitlement/activate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      subject: clientId(),
      platform: storePlatform(),
      receipt,
      store_reachable: reachable,
    }),
  });
  if (!res.ok) throw new Error(`activate ${res.status}`);
  return res.json();
}

// Reports the session's *total* elapsed seconds, never a delta, so a retry or a
// duplicate teardown settles to the same balance instead of charging twice.
async function reportUsage(sessionId, sessionSeconds) {
  try {
    const res = await api('/api/entitlement/consume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subject: clientId(), session_id: sessionId, session_seconds: Math.round(sessionSeconds) }),
      keepalive: true,
    });
    return res.ok ? res.json() : null;
  } catch (_) {
    return null;
  }
}

function formatMinutes(seconds) {
  const mins = Math.floor(Math.max(0, seconds) / 60);
  const secs = Math.max(0, Math.round(seconds)) % 60;
  if (mins >= 10) return `${mins} min`;
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

// ─────────────────────────────────────────────
// WebRTC helpers
// ─────────────────────────────────────────────

async function fetchIceServers() {
  // Both peer connections run with iceTransportPolicy: 'relay', so an empty
  // server list guarantees ICE failure and leaves the caller sitting on
  // "Connecting…" until the connection state eventually flips to failed.
  // Throwing here lets setupSession fail fast into the error path instead.
  const r = await api('/api/ice');
  if (!r.ok) throw new Error(`Could not fetch ICE servers (${r.status})`);
  const d = await r.json();
  const iceServers = d.iceServers || [];
  if (!iceServers.length) throw new Error('No ICE servers available');
  return iceServers;
}

async function waitForICE(pc) {
  return new Promise(resolve => {
    if (pc.iceGatheringState === 'complete') return resolve();
    const check = () => {
      if (pc.iceGatheringState === 'complete') {
        pc.removeEventListener('icegatheringstatechange', check);
        resolve();
      }
    };
    pc.addEventListener('icegatheringstatechange', check);
    setTimeout(resolve, 5000);
  });
}

async function createOffer(pc, stream) {
  stream.getTracks().forEach(t => pc.addTrack(t, new MediaStream([t])));
  await pc.setLocalDescription(await pc.createOffer());
  await waitForICE(pc);
  return { sdp: pc.localDescription.sdp, type: pc.localDescription.type };
}

async function setTranslationPttGate(pcId, action) {
  if (!pcId) throw new Error('Translation participant is not connected');
  const response = await api('/api/translation/ptt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pc_id: pcId, action }),
    keepalive: action === 'release',
  });
  if (!response.ok) throw new Error(`PTT gate ${action} failed (${response.status})`);
  return response.json();
}

async function hangupPcId(pcId, { keepalive = false } = {}) {
  if (!pcId) return;
  try {
    await api('/api/hangup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pc_id: pcId }),
      keepalive,
    });
  } catch (_) {}
}

function fireAndForgetHangup(pcId) {
  if (!pcId) return;
  // A beacon with an application/json body is a non-simple cross-origin
  // request, and sendBeacon cannot preflight — from the native bundle it would
  // be dropped silently and leak the peer connection server-side. Same-origin
  // in the browser it is still the most reliable unload-time send, so keep it
  // there and fall through to keepalive fetch on native.
  if (!isNativeBundle() && navigator.sendBeacon) {
    const body = JSON.stringify({ pc_id: pcId });
    try {
      const blob = new Blob([body], { type: 'application/json' });
      if (navigator.sendBeacon(apiBase() + '/api/hangup', blob)) return;
    } catch (_) {}
  }
  void hangupPcId(pcId, { keepalive: true });
}

// Capacitor injects this bridge only in the native iOS shell. Browsers continue
// to use the Azure session endpoints, while iOS keeps its durable copy in SwiftData.
function nativeHistoryPlugin() {
  return window.Capacitor?.Plugins?.TranslationHistory || null;
}

async function saveNativeSession(session) {
  const plugin = nativeHistoryPlugin();
  if (!plugin) return;
  try {
    await plugin.saveSession({
      ...session,
      transcriptJSON: JSON.stringify(session.transcript || []),
    });
  } catch (error) {
    console.warn('Could not save iOS session history:', error);
  }
}

async function loadSessionHistory() {
  const plugin = nativeHistoryPlugin();
  if (!plugin) {
    const response = await api(`/api/translation/sessions?client_id=${encodeURIComponent(clientId())}`);
    return (await response.json()).sessions || [];
  }

  const local = (await plugin.listSessions()).sessions || [];
  // Retain visibility of a currently running Azure session, but prefer the
  // SwiftData record whenever both stores contain the same session id.
  try {
    const response = await api(`/api/translation/sessions?client_id=${encodeURIComponent(clientId())}`);
    const remote = (await response.json()).sessions || [];
    const localIds = new Set(local.map(session => session.session_id));
    const remoteLive = remote.filter(session =>
      (session.status === 'live' || session.status === 'active') && !localIds.has(session.session_id)
    );
    return [...remoteLive, ...local];
  } catch {
    return local;
  }
}

async function loadSessionDetail(sessionId) {
  const plugin = nativeHistoryPlugin();
  if (plugin) {
    try {
      const result = await plugin.getSession({ sessionId });
      if (result.session) return result.session;
    } catch (error) {
      console.warn('Could not load iOS session detail:', error);
    }
  }
  const response = await api(`/api/translation/session/${sessionId}`);
  return response.ok ? response.json() : null;
}

// ─────────────────────────────────────────────
// Icon component
// ─────────────────────────────────────────────

const ICONS = {
  mic: (
    <path d="M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3zm-1 17.93V21h-2v1h6v-1h-2v-1.07A8 8 0 0 0 20 12h-2a6 6 0 0 1-12 0H4a8 8 0 0 0 7 7.93z" />
  ),
  'mic-off': (
    <path d="M19 11h-1.7c0 .74-.16 1.43-.43 2.05l1.23 1.23c.56-.98.9-2.09.9-3.28zm-4.02.17c0-.06.02-.11.02-.17V5c0-1.66-1.34-3-3-3S9 3.34 9 5v.18l5.98 5.99zM4.27 3L3 4.27l6.01 6.01V11c0 1.66 1.33 3 2.99 3 .22 0 .44-.03.65-.08l1.66 1.66c-.71.33-1.5.52-2.31.52-2.76 0-5.3-2.1-5.3-5.1H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c.91-.13 1.77-.45 2.54-.9L19.73 21 21 19.73 4.27 3z" />
  ),
  stop: (
    <path d="M6 6h12v12H6z" />
  ),
  pause: (
    <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
  ),
  'arrow-right': (
    <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z" />
  ),
  swap: (
    <path d="M6.99 11L3 15l3.99 4v-3H14v-2H6.99v-3zM21 9l-3.99-4v3H10v2h7.01v3L21 9z" />
  ),
  history: (
    <path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z" />
  ),
  home: (
    <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z" />
  ),
  settings: (
    <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.57 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z" />
  ),
  chevron: (
    <path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z" />
  ),
  'chevron-left': (
    <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
  ),
  check: (
    <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
  ),
  'wifi-off': (
    <path d="M24 .01L23.99 0 0 23.99 1.02 25 5.5 20.5C8.29 22.64 11.0 24 12 24c10 0 12-8.52 12-8.52-1.15-3.06-3.08-5.72-5.65-7.73L24 .01zm-12 4C9.4 4.01 6.95 4.72 4.85 6.04L6.3 7.5C7.98 6.55 9.93 6 12 6c5.52 0 10 4.48 10 10 0 0-.53 2.13-2.08 4.06L21.62 21.8C23.13 19.5 24 16.76 24 14c0-5.45-3.45-10.12-8.35-11.84L14 4l-1.51-.02C12.33 4 12.17 4 12 4zM2.26 12.24C1.23 12.7.16 13.27 0 14c0 0 2 10 12 10 .57 0 1.12-.04 1.65-.11L2.26 12.24zm9.74.76 5 5c-.29.03-.59.05-.89.05-2.77 0-5.01-2.24-5.01-5 0-.02.0-.03 0-.05h.9z" />
  ),
  globe: (
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z" />
  ),
  volume: (
    <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
  ),
  clock: (
    <path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67V7z" />
  ),
  x: (
    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
  ),
  alert: (
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" />
  ),
  search: (
    <path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" />
  ),
  plus: (
    <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z" />
  ),
  translate: (
    <path d="m12.87 15.07-2.54-2.51.03-.03c1.74-1.94 2.98-4.17 3.71-6.53H17V4h-7V2H8v2H1v1.99h11.17C11.5 7.92 10.44 9.75 9 11.35 8.07 10.32 7.3 9.19 6.69 8h-2c.73 1.63 1.73 3.17 2.98 4.56l-5.09 5.02L4 19l5-5 3.11 3.11.76-2.04zM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2l-4.5-12zm-2.62 7 1.62-4.33L19.12 17h-3.24z" />
  ),
};

function Icon({ name, size = 24, color = 'currentColor', style = {} }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={color}
      style={{ display: 'block', flexShrink: 0, ...style }}
    >
      {ICONS[name] || null}
    </svg>
  );
}

// ─────────────────────────────────────────────
// Waveform — 12 animated bars
// ─────────────────────────────────────────────

function Waveform({ color = T.teal, active = true, height = 28 }) {
  const bars = 12;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 2, height }}>
      {Array.from({ length: bars }).map((_, i) => (
        <div
          key={i}
          style={{
            width: 3,
            height: '100%',
            background: color,
            borderRadius: 99,
            animation: active ? `wave ${0.8 + (i % 4) * 0.15}s ease-in-out ${i * 0.06}s infinite` : 'none',
            transform: active ? undefined : 'scaleY(0.3)',
            opacity: active ? 1 : 0.4,
            transformOrigin: 'center',
          }}
        />
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────
// TypingDots
// ─────────────────────────────────────────────

function TypingDots({ color = T.textMuted }) {
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
      {[0, 1, 2].map(i => (
        <div
          key={i}
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: color,
            animation: `dot-blink 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────
// LangBadge
// ─────────────────────────────────────────────

function LangBadge({ code, name, variant = 'teal' }) {
  const lang = getLang(code);
  const bg = variant === 'teal' ? T.tealLight : T.amberLight;
  const fg = variant === 'teal' ? T.teal : T.amber;
  return (
    <div style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      padding: '3px 10px 3px 6px',
      borderRadius: 99,
      background: bg,
      fontSize: 13,
      fontWeight: 500,
      color: fg,
    }}>
      <span style={{ fontSize: 15 }}>{lang.flag}</span>
      <span>{name || lang.label}</span>
    </div>
  );
}

// ─────────────────────────────────────────────
// PulseRing — rendered around a button (absolute)
// ─────────────────────────────────────────────

function PulseRing({ color = T.teal, size = 68 }) {
  return (
    <>
      {[0, 1, 2].map(i => (
        <div
          key={i}
          style={{
            position: 'absolute',
            width: size,
            height: size,
            borderRadius: '50%',
            border: `2px solid ${color}`,
            opacity: 0,
            animation: `pulse-ring 1.8s ease-out ${i * 0.6}s infinite`,
            pointerEvents: 'none',
          }}
        />
      ))}
    </>
  );
}

// ─────────────────────────────────────────────
// ConvTurn — single conversation turn
// Speech bubble styling per design spec
// ─────────────────────────────────────────────

function ConvTurn({ turn, colorHex }) {
  const isZh = turn.original_lang === 'zh' || turn.translated_lang === 'zh';
  const originalFont = isZh && turn.original_lang === 'zh'
    ? { fontFamily: "'Noto Sans SC', sans-serif" }
    : {};
  const translationFont = isZh && turn.translated_lang === 'zh'
    ? { fontFamily: "'Noto Sans SC', sans-serif" }
    : {};
  const originalBg = colorHex === T.amber ? T.amberLight : T.tealLight;
  const originalBorder = colorHex === T.amber ? T.amber + '2e' : T.teal + '2e';

  // Bubble radius: EN (speaker A) → 4px 16px 16px 16px, ZH (speaker B) → 16px 4px 16px 16px
  const origRadius = turn.original_lang === 'zh' ? '16px 4px 16px 16px' : '4px 16px 16px 16px';

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      animation: 'fade-in-up 0.3s ease-out both',
      }}>
        {/* Original speech bubble */}
        <div style={{
          padding: '10px 13px',
          background: originalBg,
          border: `1px solid ${originalBorder}`,
          borderRadius: origRadius,
          fontSize: 14,
          color: T.textPrimary,
          lineHeight: 1.5,
          fontWeight: 500,
          ...originalFont,
        }}>
          {turn.original}
        </div>
        {/* Translation bubble */}
      {turn.translated && (
        <div style={{
          padding: '10px 13px',
          background: T.surface,
          border: `1px solid ${T.border}`,
          borderRadius: '12px',
          fontSize: 14,
          color: T.textPrimary,
          lineHeight: 1.4,
          boxShadow: '0 1px 2px rgba(15, 23, 42, 0.03)',
          ...translationFont,
        }}>
          {turn.translated}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// StatusPill
// ─────────────────────────────────────────────

const STATUS_CONFIG = {
  idle:        { label: 'Idle',        bg: T.surface2,    color: T.textMuted,  dot: T.textFaint },
  listening:   { label: 'Listening',   bg: T.surface2,    color: T.textMuted,  dot: T.textFaint },
  detecting:   { label: 'Detecting',   bg: T.tealLight,   color: T.teal,       dot: T.teal },
  translating: { label: 'Translating', bg: T.amberLight,  color: T.amber,      dot: T.amber },
  speaking:    { label: 'Speaking',    bg: T.tealLight,   color: T.teal,       dot: T.teal },
  muted:       { label: 'Muted',       bg: T.errorLight,  color: T.error,      dot: T.error },
  error:       { label: 'Error',       bg: T.errorLight,  color: T.error,      dot: T.error },
};

function StatusPill({ status = 'idle', colorOverride }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.idle;
  const animate = ['detecting', 'speaking', 'translating'].includes(status);
  return (
    <div style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      padding: '3px 10px',
      borderRadius: 99,
      background: colorOverride ? colorOverride + '22' : cfg.bg,
      fontSize: 12,
      fontWeight: 600,
      color: colorOverride || cfg.color,
    }}>
      <div style={{
        width: 6,
        height: 6,
        borderRadius: '50%',
        background: colorOverride || cfg.dot,
        animation: animate ? 'dot-blink 1.4s ease-in-out infinite' : 'none',
      }} />
      {cfg.label}
    </div>
  );
}

// ─────────────────────────────────────────────
// TabBar
// ─────────────────────────────────────────────

function TabBar({ active, onChange }) {
  const tabs = [
    { id: 'home',    icon: 'home',    label: 'Home' },
    { id: 'live',    icon: 'mic',     label: 'Translate' },
    { id: 'history', icon: 'history', label: 'History' },
    { id: 'settings', icon: 'settings', label: 'Settings' },
  ];
  return (
    <div style={{
      height: 'calc(82px + env(safe-area-inset-bottom, 0px))', display: 'flex', alignItems: 'flex-start',
      paddingTop: 11, background: 'rgba(251,249,247,.92)', backdropFilter: 'blur(18px)',
      borderTop: '1px solid rgba(28,32,51,.08)', paddingBottom: 'env(safe-area-inset-bottom, 0px)', flexShrink: 0,
    }}>
      {tabs.map(t => {
        const isActive = active === t.id;
        return (
          <button
            key={t.id}
            onClick={() => onChange(t.id)}
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 0, gap: 5, color: isActive ? T.amber : 'rgba(28,32,51,.38)',
              transition: 'color 0.15s',
            }}
          >
            <Icon name={t.icon} size={22} />
            <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: '.02em' }}>{t.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────
// Offline mode preference
// ─────────────────────────────────────────────
//
// Stored as true/false once the user touches the switch, and null until then.
// The tri-state matters: the default depends on tier (free users start
// on-device, where translation costs nothing and works without a connection),
// and a subscriber who deliberately turns it ON should not have it flipped back
// the next time their tier is re-read.

const OFFLINE_PREF_KEY = 'vocare.offlineMode';

function readOfflinePref() {
  try {
    const raw = localStorage.getItem(OFFLINE_PREF_KEY);
    return raw === null ? null : raw === 'true';
  } catch (_) {
    return null;
  }
}

function writeOfflinePref(value) {
  try { localStorage.setItem(OFFLINE_PREF_KEY, String(value)); } catch (_) {}
}

// ─────────────────────────────────────────────
// OfflineSwitch — on the home screen, above the language pair
// ─────────────────────────────────────────────

function OfflineSwitch({ on, onChange, langA, langB }) {
  const [status, setStatus] = useState(undefined); // installed | supported | unsupported | null
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const pairPossible = canTranslateOffline(langA, langB);

  useEffect(() => {
    let active = true;
    setError(null);
    offlinePairStatus(langA, langB).then(s => { if (active) setStatus(s); });
    return () => { active = false; };
  }, [langA, langB]);

  // Hidden entirely in a browser, where there is no on-device engine to switch
  // to. A dead toggle is worse than no toggle.
  if (!offlineTranslationLanguages().length || !offlineEngine()) return null;

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const ok = await downloadOfflinePair(langA, langB);
      if (!ok) setError('The download was declined. Offline translation needs both languages installed.');
      setStatus(await offlinePairStatus(langA, langB));
    } catch (_) {
      setError('Could not install the languages. Check your connection and try again.');
    } finally {
      setBusy(false);
    }
  }

  const needsDownload = on && pairPossible && status === 'supported';
  const impossible = on && !pairPossible;

  const subtitle = !pairPossible
    ? `${getLang(langA).label} and ${getLang(langB).label} cannot be translated on-device — this pair needs a connection.`
    : status === 'installed'
      ? 'Translated on this device. No connection needed, and nothing leaves your phone.'
      : status === 'supported'
        ? 'Both languages need to be installed on this device first.'
        : 'Runs on this device instead of the cloud.';

  return (
    <div style={{
      background: T.surface, border: `1px solid ${needsDownload || impossible ? T.inkA35 : T.hairline}`,
      borderRadius: 18, padding: '13px 15px', marginBottom: 8,
      boxShadow: '0 10px 30px -20px rgba(28,32,51,.28)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate }}>Offline mode</div>
            {on && status === 'installed' && (
              <span style={{ fontFamily: T.mono, fontSize: 9, letterSpacing: '.1em', textTransform: 'uppercase', fontWeight: 700, color: T.tealDeep }}>Ready</span>
            )}
          </div>
          <div style={{ fontSize: 11.5, lineHeight: 1.45, color: T.inkMute, marginTop: 2, textWrap: 'pretty' }}>{subtitle}</div>
        </div>
        <button
          onClick={() => onChange(!on)}
          role="switch"
          aria-checked={on}
          aria-label="Offline mode"
          style={{
            width: 44, height: 26, flexShrink: 0, borderRadius: 99, border: 'none',
            background: on ? T.amber : 'rgba(28,32,51,.16)', padding: 3,
            display: 'flex', justifyContent: on ? 'flex-end' : 'flex-start',
            transition: 'background .2s',
          }}
        >
          <div style={{ width: 20, height: 20, borderRadius: '50%', background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,.25)' }} />
        </button>
      </div>

      {needsDownload && (
        <button
          onClick={download}
          disabled={busy}
          style={{
            width: '100%', marginTop: 11, height: 42, borderRadius: 13, border: 'none',
            background: T.inkA08, color: T.amber, fontSize: 13.5, fontWeight: 600, opacity: busy ? .6 : 1,
          }}
        >
          {busy ? 'Installing…' : `Install ${getLang(langA).label} and ${getLang(langB).label}`}
        </button>
      )}

      {error && (
        <div style={{ marginTop: 9, fontSize: 11.5, lineHeight: 1.45, color: T.error }}>{error}</div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// VocaMascot — the hopping translator from the design canvas.
// Pure CSS: `voca-*` keyframes live in vocare.html. Decorative only,
// so it is hidden from assistive tech.
// ─────────────────────────────────────────────

function VocaMascot() {
  const arc = (side, size, delay) => ({
    position: 'absolute', [side]: size === 30 ? 44 : size === 48 ? 26 : 8, top: 97,
    width: size, height: size, marginTop: -size / 2,
    border: '3.5px solid transparent',
    [side === 'left' ? 'borderLeftColor' : 'borderRightColor']: T.amberSoft,
    borderRadius: '50%', opacity: .45,
    animation: 'voca-arc 1.9s ease-in-out infinite', animationDelay: `${delay}ms`,
  });

  return (
    <div aria-hidden="true" style={{ width: 290, height: 180, position: 'relative', animation: 'voca-wander 7s ease-in-out infinite' }}>
      {[[30, 0], [48, 200], [66, 400]].map(([size, delay]) => (
        <React.Fragment key={size}>
          <div style={arc('left', size, delay)} />
          <div style={arc('right', size, delay)} />
        </React.Fragment>
      ))}

      <div style={{
        position: 'absolute', left: '50%', bottom: 8, width: 88, height: 14, marginLeft: -44, borderRadius: '50%',
        background: 'radial-gradient(50% 50% at 50% 50%, rgba(76,60,180,.34) 0%, rgba(76,60,180,0) 72%)',
        animation: 'voca-shadow 1.15s ease-in-out infinite',
      }} />

      <div style={{ position: 'absolute', left: '50%', bottom: 22, marginLeft: -62, width: 124, height: 122, animation: 'voca-hop 1.15s ease-in-out infinite' }}>
        {/* waving arm + flag */}
        <div style={{
          position: 'absolute', right: -4, top: 34, width: 14, height: 26, borderRadius: 8,
          background: 'linear-gradient(230deg,#FFFFFF,#DED8F5)', boxShadow: 'inset 2px -2px 5px rgba(76,60,180,.2)',
          transformOrigin: '50% 100%', animation: 'voca-wave 7s ease-in-out infinite',
        }} />
        <div style={{ position: 'absolute', right: -40, top: -8, width: 70, height: 74, transformOrigin: '16% 92%', animation: 'voca-wave 7s ease-in-out infinite' }}>
          <div style={{ position: 'absolute', left: 9, bottom: 0, width: 3.5, height: 70, borderRadius: 2, background: 'linear-gradient(180deg,#F3F0FD,#B9AEE8)' }} />
          <div style={{
            position: 'absolute', left: 10, top: 2, width: 46, height: 30, borderRadius: '3px 8px 8px 3px',
            background: 'linear-gradient(135deg,#8B7CF0,#6C5CE7 60%,#5546C9)', boxShadow: '0 4px 10px -4px rgba(76,60,180,.65)',
            display: 'grid', placeItems: 'center', transformOrigin: 'left center', animation: 'voca-flap 1.1s ease-in-out infinite',
          }}>
            <span style={{ fontFamily: T.mono, fontSize: 14, fontWeight: 700, color: T.cream, letterSpacing: '.06em' }}>文A</span>
          </div>
        </div>

        {/* ears */}
        <div style={{ position: 'absolute', left: -10, top: 52, width: 22, height: 34, borderRadius: 12, background: 'linear-gradient(120deg,#FFFFFF,#DED8F5)', boxShadow: 'inset -2px -3px 6px rgba(76,60,180,.2)' }} />
        <div style={{ position: 'absolute', right: -10, top: 52, width: 22, height: 34, borderRadius: 12, background: 'linear-gradient(240deg,#FFFFFF,#DED8F5)', boxShadow: 'inset 2px -3px 6px rgba(76,60,180,.2)' }} />

        {/* feet */}
        <div style={{ position: 'absolute', left: 26, bottom: -7, width: 22, height: 14, borderRadius: '0 0 8px 8px', background: 'linear-gradient(180deg,#2A2358,#151233)' }} />
        <div style={{ position: 'absolute', right: 26, bottom: -7, width: 22, height: 14, borderRadius: '0 0 8px 8px', background: 'linear-gradient(180deg,#2A2358,#151233)' }} />

        {/* body */}
        <div style={{
          position: 'absolute', inset: 0, borderRadius: 40,
          background: 'linear-gradient(158deg,#FFFFFF 4%,#F3F0FD 44%,#DAD2F4 78%,#C6BCEC 100%)',
          boxShadow: 'inset 0 10px 16px rgba(255,255,255,.9), inset 0 -14px 22px rgba(108,92,231,.22), inset 0 0 0 1px rgba(255,255,255,.7), 0 22px 34px -14px rgba(76,60,180,.5)',
        }} />
        <div style={{ position: 'absolute', left: 14, top: 9, width: 44, height: 16, borderRadius: '50%', background: 'rgba(255,255,255,.9)', filter: 'blur(5px)' }} />

        {/* face */}
        <div style={{
          position: 'absolute', left: 21, top: 19, right: 21, height: 74, borderRadius: 23,
          background: 'radial-gradient(120% 110% at 26% 14%, #35286E 0%, #1A1442 46%, #0E0B29 100%)',
          boxShadow: 'inset 0 0 0 1.5px rgba(140,110,255,.5), inset 0 2px 10px rgba(0,0,0,.6), 0 3px 8px rgba(20,14,60,.45)',
          overflow: 'hidden',
        }}>
          <div style={{ position: 'absolute', left: '-10%', top: '-30%', width: '70%', height: '150%', background: 'linear-gradient(100deg,rgba(255,255,255,.16),rgba(255,255,255,0) 62%)', transform: 'skewX(-14deg)' }} />
          <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: `linear-gradient(180deg,${T.accent},${T.amber} 60%,rgba(108,92,231,0))` }} />
          <div style={{ position: 'absolute', right: 0, top: 8, bottom: 8, width: 2, background: `linear-gradient(180deg,rgba(108,92,231,0),${T.amberSoft})` }} />
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 9 }}>
            <div style={{ display: 'flex', gap: 22 }}>
              {[0, 1].map(i => (
                <span key={i} style={{ width: 11, height: 15, borderRadius: 5, background: '#FFFFFF', boxShadow: '0 0 10px rgba(255,255,255,.75)', display: 'block', animation: 'voca-blink 4.2s ease-in-out infinite' }} />
              ))}
            </div>
            <div style={{ width: 30, height: 15, border: '3.5px solid transparent', borderBottomColor: '#FFFFFF', borderRadius: '0 0 34px 34px', filter: 'drop-shadow(0 0 6px rgba(255,255,255,.6))' }} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// WelcomeScreen
// ─────────────────────────────────────────────

function WelcomeScreen({ langA, langB, onStart, onPickA, onPickB, onSwap, tier = 'free', secondsLeft = 0, onUpgrade, offlineMode = false, onOfflineChange }) {
  const langAInfo = getLang(langA);
  const langBInfo = getLang(langB);

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
      background: `radial-gradient(120% 70% at 50% 6%, ${T.surface2} 0%, rgba(251,249,247,0) 58%), radial-gradient(90% 50% at 88% 96%, #EBF6F0 0%, rgba(251,249,247,0) 60%), ${T.bg}`,
    }}>
      <div style={{ padding: '30px 26px 0', display: 'flex', alignItems: 'center', gap: 9, flexShrink: 0 }}>
        <div style={{ width: 26, height: 26, borderRadius: 8, background: T.amber, display: 'grid', placeItems: 'center' }}>
          <div style={{ display: 'flex', gap: 4 }}>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: '#fff', display: 'block' }} />
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: '#fff', display: 'block' }} />
          </div>
        </div>
        <div style={{ fontFamily: T.display, fontSize: 17, fontWeight: 700, letterSpacing: '-.01em', color: T.slate }}>Voca</div>

        {/* Tier chip. A subscriber sees what is left before starting, not after
            it runs out; a free user sees the one route to the cloud engine. */}
        <button
          onClick={onUpgrade}
          style={{
            marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6,
            padding: '6px 11px', borderRadius: 99, border: `1px solid ${T.hairline}`,
            background: tier === 'pro' ? T.inkA07 : T.surface,
            fontFamily: T.mono, fontSize: 9.5, letterSpacing: '.1em', textTransform: 'uppercase',
            fontWeight: 700, color: tier === 'pro' ? T.amberDeep : T.inkMute,
          }}
        >
          {tier === 'pro' ? (
            <>
              <span style={{ width: 5, height: 5, borderRadius: '50%', background: secondsLeft > 0 ? T.teal : T.error, display: 'block' }} />
              {formatMinutes(secondsLeft)} left
            </>
          ) : 'Free · Upgrade'}
        </button>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', paddingBottom: 8 }}>
        <VocaMascot />
        <div style={{ fontFamily: T.display, fontSize: 15.5, fontWeight: 500, color: 'rgba(28,32,51,.5)', marginTop: 16, whiteSpace: 'nowrap' }}>
          Ready when you are
        </div>
      </div>

      <div style={{ padding: '0 20px 24px', flexShrink: 0 }}>
        <OfflineSwitch on={offlineMode} onChange={onOfflineChange} langA={langA} langB={langB} />
        <div style={{
          display: 'flex', alignItems: 'stretch', gap: 8, background: T.surface,
          border: `1px solid ${T.hairline}`, borderRadius: 22, padding: 8,
          boxShadow: '0 10px 30px -18px rgba(28,32,51,.28)',
        }}>
          <button onClick={onPickA} style={{ flex: 1, minWidth: 0, padding: '12px 14px', borderRadius: 15, background: T.inkA07, textAlign: 'left' }}>
            <div style={{ fontFamily: T.mono, fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', color: T.amberDeep, fontWeight: 700 }}>Person A</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: T.slate, marginTop: 5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{langAInfo.label}</div>
          </button>
          <button onClick={onSwap} aria-label="Swap languages" style={{ width: 38, display: 'grid', placeItems: 'center', color: 'rgba(28,32,51,.4)', borderRadius: 12 }}>
            <Icon name="swap" size={18} />
          </button>
          <button onClick={onPickB} style={{ flex: 1, minWidth: 0, padding: '12px 14px', borderRadius: 15, background: T.inkB09, textAlign: 'right' }}>
            <div style={{ fontFamily: T.mono, fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', color: T.tealDeep, fontWeight: 700 }}>Person B</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: T.slate, marginTop: 5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{langBInfo.label}</div>
          </button>
        </div>

        <button
          onClick={onStart}
          style={{
            width: '100%', height: 62, marginTop: 12, background: T.amber, color: '#fff', border: 'none', borderRadius: 20,
            fontSize: 17, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 11,
            boxShadow: '0 12px 26px -8px rgba(108,92,231,.6)',
          }}
        >
          <Icon name="mic" size={19} color="#fff" /> Start a session
        </button>

        <div style={{
          textAlign: 'center', fontFamily: T.mono, fontSize: 10, letterSpacing: '.14em',
          textTransform: 'uppercase', color: 'rgba(28,32,51,.35)', marginTop: 14,
        }}>
          Hold your half to talk
        </div>
      </div>
    </div>
  );
}

function SessionCard({ session, onClick }) {
  const isLive = session.status === 'live' || session.status === 'active';
  const lang = getLang(session.lang || 'en');
  const durationStr = session.duration != null ? formatDuration(session.duration) : null;

  return (
    <button
      onClick={onClick}
      style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '14px 15px', background: '#fff',
        border: '1px solid rgba(28,32,51,.08)', borderRadius: 18, marginBottom: 8, cursor: 'pointer', textAlign: 'left',
      }}
    >
      <div style={{
        width: 38, height: 38, background: isLive ? 'rgba(52,164,111,.12)' : 'rgba(108,92,231,.08)',
        color: isLive ? '#2A8B5D' : T.amber, borderRadius: 12, display: 'grid', placeItems: 'center', flexShrink: 0,
        fontSize: 11, fontWeight: 700,
      }}>
        {String(session.lang || 'EN').toUpperCase()}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
          <span style={{ fontSize: 14.5, fontWeight: 600, color: T.slate, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {session.topic || session.caller_name || 'Translation Session'}
          </span>
          {isLive && (
            <span style={{ fontSize: 10, fontWeight: 700, color: '#2A8B5D', background: 'rgba(52,164,111,.12)', padding: '4px 8px', borderRadius: 99, letterSpacing: '.1em' }}>
              Live
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11.5, color: 'rgba(28,32,51,.5)' }}>{session.caller_name}</span>
          {session.participant_count != null && (
            <span style={{ fontSize: 12, color: T.textFaint }}>· {session.participant_count} people</span>
          )}
          {durationStr && (
            <span style={{ fontSize: 12, color: T.textFaint }}>· {durationStr}</span>
          )}
        </div>
      </div>
    </button>
  );
}

function formatDuration(secs) {
  if (secs == null) return '';
  if (typeof secs === 'string') return secs;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// Live-bar clock: tabular MM:SS, as drawn on the canvas.
function formatClock(secs) {
  const n = typeof secs === 'number' ? Math.max(0, secs) : 0;
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
}

// ─────────────────────────────────────────────
// SessionSetupScreen
// ─────────────────────────────────────────────

// ─────────────────────────────────────────────
// Microphone disclosure
// ─────────────────────────────────────────────

// Google Play requires a prominent disclosure before a sensitive permission is
// requested, and Apple expects the same in substance. Until this is accepted the
// app must not reach getUserMedia. Stored per-device; localStorage can throw in
// a locked-down WebView, so a failure to read means "not yet consented" and a
// failure to write just costs the user one extra tap next time.
const MIC_CONSENT_KEY = 'vocare.micConsent.v1';

function hasMicConsent() {
  try {
    return window.localStorage.getItem(MIC_CONSENT_KEY) === 'granted';
  } catch (_) {
    return false;
  }
}

function recordMicConsent() {
  try {
    window.localStorage.setItem(MIC_CONSENT_KEY, 'granted');
  } catch (_) {}
}

function MicDisclosure({ onAccept, onCancel }) {
  return (
    <div style={{
      position: 'absolute', inset: 0, zIndex: 40, display: 'flex', alignItems: 'flex-end',
      background: 'rgba(28,32,51,.55)', animation: 'fade-in .2s ease-out',
    }}>
      <div style={{
        width: '100%', background: T.cream, borderRadius: '26px 26px 0 0', padding: '24px 22px 28px',
        boxShadow: '0 -18px 40px -12px rgba(28,32,51,.4)',
      }}>
        <div style={{ width: 46, height: 46, borderRadius: 15, display: 'grid', placeItems: 'center', background: T.slate }}>
          <Icon name="mic" size={21} color={T.cream} />
        </div>
        <h2 style={{ fontFamily: T.display, fontSize: 25, fontWeight: 700, letterSpacing: '-.02em', color: T.slate, marginTop: 14 }}>
          Before we turn on the microphone
        </h2>
        <div style={{ fontSize: 14, lineHeight: 1.6, color: 'rgba(28,32,51,.78)', marginTop: 12 }}>
          To translate your conversation, Vocare records audio while you hold the speak
          button and sends it to our servers, where it is transcribed and translated.
        </div>
        <ul style={{ listStyle: 'none', marginTop: 14, display: 'flex', flexDirection: 'column', gap: 9 }}>
          {[
            'Audio is captured only while a speak button is held down — never in the background.',
            'Transcripts of the conversation are saved on this device so you can read them later.',
            'There are no accounts, and nothing is used for advertising or tracking.',
          ].map(line => (
            <li key={line} style={{ display: 'flex', gap: 9, fontSize: 13, lineHeight: 1.5, color: 'rgba(28,32,51,.7)' }}>
              <span style={{ color: T.amber, fontWeight: 700 }}>·</span>
              <span>{line}</span>
            </li>
          ))}
        </ul>
        {/* Absolute, and no target="_blank". The native bundle is served from
            capacitor://localhost (iOS) / https://localhost (Android), where a
            relative /privacy does not exist, and neither WebView opens _blank
            links without a window handler. An off-origin https URL is handed to
            the system browser by Capacitor's navigation policy. */}
        <a
          href={`${apiBase()}/privacy`}
          rel="noopener noreferrer"
          style={{ display: 'inline-block', marginTop: 14, fontSize: 13, fontWeight: 600, color: T.amber, textDecoration: 'underline' }}
        >
          Read the privacy policy
        </a>
        <button
          onClick={() => { recordMicConsent(); onAccept(); }}
          style={{
            width: '100%', height: 58, marginTop: 18, background: T.amber, color: '#fff', border: 'none',
            borderRadius: 19, fontSize: 16.5, fontWeight: 600,
          }}
        >
          Allow and continue
        </button>
        <button
          onClick={onCancel}
          style={{
            width: '100%', height: 48, marginTop: 8, background: 'transparent', color: 'rgba(28,32,51,.6)',
            border: 'none', fontSize: 15, fontWeight: 600,
          }}
        >
          Not now
        </button>
      </div>
    </div>
  );
}

function SessionSetupScreen({ langA, langB, onBack, onStart, onLangPick }) {
  const [nameA, setNameA] = useState('Person A');
  const [nameB, setNameB] = useState('Person B');
  const [localLangA, setLocalLangA] = useState(langA);
  const [localLangB, setLocalLangB] = useState(langB);
  const [pickingFor, setPickingFor] = useState(null); // null | 'a' | 'b'

  const langAInfo = getLang(localLangA);
  const langBInfo = getLang(localLangB);

  // The microphone disclosure is gated centrally in App.handleStart, which both
  // this screen and the home card route through.
  function handleStart() {
    onStart({
      mode: 'face-to-face',
      micMode: 'hold',
      nameA,
      nameB,
      langA: localLangA,
      langB: localLangB,
    });
  }

  if (pickingFor !== null) {
    return (
      <LangScreen
        targetName={pickingFor === 'a' ? nameA : nameB}
        variant={pickingFor === 'a' ? 'amber' : 'teal'}
        selected={pickingFor === 'a' ? localLangA : localLangB}
        onBack={() => setPickingFor(null)}
        onSelect={code => {
          if (pickingFor === 'a') setLocalLangA(code);
          else setLocalLangB(code);
          setPickingFor(null);
        }}
      />
    );
  }

  return (
    <div style={{ flex: 1, position: 'relative', display: 'flex', flexDirection: 'column', overflowY: 'auto', background: T.cream }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '60px 22px 18px' }}>
        <button onClick={onBack} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(28,32,51,.05)', color: T.slate }}><Icon name="chevron-left" size={17} /></button>
        <h1 style={{ fontFamily: T.display, fontSize: 26, fontWeight: 700, letterSpacing: '-.02em', color: T.slate }}>Session setup</h1>
      </div>

      <div style={{ padding: '0 20px 28px' }}>
        <Section title="Who is talking">
          <PersonRow
            label="Person A · this side"
            name={nameA}
            lang={localLangA}
            onNameChange={setNameA}
            onLangTap={() => setPickingFor('a')}
            variant="amber"
          />
          <PersonRow
            label="Person B · far side"
            name={nameB}
            lang={localLangB}
            onNameChange={setNameB}
            onLangTap={() => setPickingFor('b')}
            variant="teal"
            style={{ marginTop: 8 }}
          />
        </Section>

        <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(28,32,51,.45)', fontWeight: 700, margin: '22px 0 9px' }}>Microphone</div>
        <div style={{ borderRadius: 18, padding: 15, background: T.slate, border: `1px solid ${T.slate}`, color: T.cream }}>
          <Icon name="mic" size={19} color={T.cream} />
          <div style={{ fontSize: 14.5, fontWeight: 600, marginTop: 8 }}>Hold to speak</div>
        </div>

        <div style={{ marginTop: 22, background: T.slate, borderRadius: 22, padding: '18px 18px 20px', color: T.cream }}>
          <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(251,249,247,.5)', fontWeight: 700 }}>How it works</div>
          <div style={{ fontSize: 13.5, lineHeight: 1.55, color: 'rgba(251,249,247,.78)', marginTop: 10 }}>Lay the phone flat between you. Hold your half to talk; release and the other half hears it translated.</div>
        </div>

        <button
          onClick={handleStart}
          style={{
            width: '100%', height: 62, marginTop: 18, background: T.amber, color: '#fff', border: 'none', borderRadius: 20,
            fontSize: 17, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
            boxShadow: '0 12px 24px -8px rgba(108,92,231,.55)',
          }}
        >
          Start session
        </button>
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div>
      <h3 style={{ fontSize: 10.5, fontWeight: 700, color: 'rgba(28,32,51,.45)', textTransform: 'uppercase', letterSpacing: '.16em', margin: '6px 0 9px' }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function ToggleCard({ active, title, subtitle, icon, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '14px 12px',
        background: active ? T.tealLight : T.surface,
        border: `1.5px solid ${active ? T.teal : T.border}`,
        borderRadius: 14,
        textAlign: 'left',
        transition: 'all 0.15s',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <Icon name={icon} size={20} color={active ? T.teal : T.textFaint} />
      <span style={{ fontSize: 14, fontWeight: 600, color: active ? T.teal : T.textPrimary }}>{title}</span>
      <span style={{ fontSize: 12, color: active ? T.teal : T.textMuted, opacity: active ? 0.8 : 1 }}>{subtitle}</span>
    </button>
  );
}

function PersonRow({ label, name, lang, onNameChange, onLangTap, dimmed = false, variant = 'amber', style = {} }) {
  const langInfo = getLang(lang);
  const accent = variant === 'teal' ? T.teal : T.amber;
  const ink = variant === 'teal' ? '#2A8B5D' : T.amber;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '13px 15px', background: '#fff',
      border: `1px solid ${accent}38`, borderRadius: 18,
      opacity: dimmed ? 0.55 : 1,
      ...style,
    }}>
      <div style={{ width: 8, height: 34, borderRadius: 99, background: accent, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 9.5, letterSpacing: '.14em', textTransform: 'uppercase', fontWeight: 700, color: ink }}>{label}</div>
        <input value={name} onChange={e => onNameChange(e.target.value)} disabled={dimmed} style={{ width: '100%', marginTop: 3, padding: 0, fontSize: 16, fontWeight: 600, color: T.slate, background: 'none', border: 'none', outline: 'none', fontFamily: T.body }} />
      </div>
      <button
        onClick={onLangTap}
        disabled={dimmed}
        style={{
          display: 'flex', alignItems: 'center', gap: 4, padding: '8px 12px', background: accent + '14', border: 'none', borderRadius: 12,
          fontSize: 13,
          fontWeight: 600, color: ink,
          cursor: 'pointer',
          flexShrink: 0,
        }}
      >
        <span>{langInfo.label}</span>
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────
// PersonNameTag — colored square tile with flag, name and lang
// Used in FaceToFace and Auto live screens
// ─────────────────────────────────────────────

function PersonNameTag({ langInfo, name, color, pressing, tint, ink }) {
  const textColor = T.slate;
  const subColor = T.inkMute;
  const tileBg = tint || (color + '29');
  const tileInk = ink || color;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{
        width: 30, height: 30, background: tileBg, color: tileInk, borderRadius: 10,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        flexShrink: 0,
        transition: 'background 0.2s',
      }}>
        <span style={{ fontSize: 11, fontWeight: 700 }}>{langInfo.code.toUpperCase()}</span>
      </div>
      <div>
        <p style={{ fontSize: 14, fontWeight: 600, color: textColor, transition: 'color 0.2s' }}>{name}</p>
        <p style={{ fontSize: 11, color: subColor, transition: 'color 0.2s' }}>{langInfo.label}</p>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// LiveTurn — one bubble inside a live half-screen.
// `mine` turns are the reader's own words; the rest are the
// translation they are meant to read, with the source underneath.
// ─────────────────────────────────────────────

function LiveTurn({ turn, mine, side }) {
  const tagColor = mine ? 'rgba(28,32,51,.4)' : (side === 'A' ? T.amberDeep : T.tealDeep);
  const bg = mine ? T.surface : (side === 'A' ? T.amber : T.teal);
  const fg = mine ? T.slate : '#fff';
  const text = mine ? turn.original : turn.translated;
  const sub = mine ? null : turn.original;
  const zhFont = { fontFamily: "'Noto Sans SC', 'Space Grotesk', sans-serif" };
  const isZh = v => typeof v === 'string' && /[一-鿿]/.test(v);

  return (
    <div style={{ maxWidth: '88%', alignSelf: mine ? 'flex-end' : 'flex-start', animation: 'fade-in-up .3s ease-out both' }}>
      <div style={{ fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', fontWeight: 700, color: tagColor, marginBottom: 4 }}>
        {mine ? 'You said' : 'Translated'}
      </div>
      <div style={{ background: bg, color: fg, borderRadius: 16, padding: '11px 13px', fontSize: 15.5, lineHeight: 1.4, ...(isZh(text) ? zhFont : {}) }}>
        {text}
      </div>
      {sub && (
        <div style={{ fontSize: 11.5, color: 'rgba(28,32,51,.4)', marginTop: 5, fontStyle: 'italic', ...(isZh(sub) ? zhFont : {}) }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// LiveSplitView — the two-sided session screen
// ─────────────────────────────────────────────
//
// Presentation only. Both live screens render this, so a cloud session and an
// on-device session look and behave identically; only the badge in the centre
// bar differs. It holds no state and touches no engine, which keeps the
// separation the offline screen was built with — that separation was always
// about logic, never about pixels.
//
// Each `side` descriptor is:
//   { langInfo, name, status, pressing, disabled, onDown, onUp, turns, note }
// where `turns` is [{ key, turn, mine }] oldest-first — the panel reverses it so
// the newest sits nearest that person's microphone.

function LiveSidePanel({ side, data, rotated }) {
  const isA = side === 'A';
  const accent = isA ? T.amber : T.teal;
  const accentDeep = isA ? T.amberDeep : T.tealDeep;
  const idleBg = isA ? T.panelAIdle : T.panelBIdle;
  const activeBg = isA ? T.panelA : T.panelB;
  const micIdle = isA ? T.inkA14 : T.inkB14;
  const micBorder = isA ? T.inkA35 : T.inkB35;
  const tint = isA ? T.tileA : T.tileB;

  return (
    <div style={{
      flex: 1, minHeight: 0,
      background: data.pressing ? activeBg : idleBg,
      display: 'flex', flexDirection: 'column',
      transition: 'background 0.3s', position: 'relative',
      ...(rotated ? { transform: 'rotate(180deg)' } : null),
    }}>
      <div style={{ padding: '14px 18px 6px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <PersonNameTag langInfo={data.langInfo} name={data.name} tint={tint} ink={isA ? accent : accentDeep} pressing={data.pressing} />
        <div style={{
          marginLeft: 'auto', fontSize: 10.5, letterSpacing: '.1em', textTransform: 'uppercase',
          fontWeight: 700, color: data.pressing ? accentDeep : 'rgba(28,32,51,.4)',
        }}>{data.status}</div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column-reverse', gap: 10, padding: '6px 18px 4px' }}>
        {data.turns.slice().reverse().map(t => (
          <LiveTurn key={t.key} turn={t.turn} side={side} mine={t.mine} />
        ))}
        {data.note && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, alignSelf: 'flex-start' }}>
            <TypingDots color={'rgba(28,32,51,.4)'} />
            <span style={{ fontSize: 13, color: T.inkMute }}>{data.note}</span>
          </div>
        )}
      </div>

      <div style={{
        height: 150, flex: 'none', display: 'grid', placeItems: 'center',
        ...(rotated ? null : { paddingBottom: 'env(safe-area-inset-bottom, 0px)' }),
      }}>
        <div style={{ position: 'relative', width: 104, height: 104, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {data.pressing && <PulseRing color={accent} size={104} />}
          <button
            data-ptt="true"
            onPointerDown={data.onDown}
            onPointerUp={data.onUp}
            onPointerCancel={data.onUp}
            onContextMenu={e => e.preventDefault()}
            disabled={data.disabled}
            aria-label={`${data.name} hold to speak`}
            style={{
              width: 104, height: 104, borderRadius: '50%',
              background: data.pressing ? accent : micIdle,
              border: `2px solid ${data.pressing ? accentDeep : micBorder}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              transition: 'transform .12s, background .2s, border-color .2s',
              transform: data.pressing ? 'scale(1.12)' : 'scale(1)',
              cursor: data.disabled ? 'default' : 'pointer',
              opacity: data.dimmed ? 0.58 : 1,
              touchAction: 'none', WebkitTouchCallout: 'none', WebkitUserSelect: 'none', userSelect: 'none',
              position: 'relative', zIndex: 1,
            }}
          >
            <Icon name="mic" size={30} color={data.pressing ? '#fff' : (isA ? accent : accentDeep)} />
          </button>
        </div>
      </div>
    </div>
  );
}

function LiveSplitView({ sideA, sideB, elapsedStr, onEnd, badge, notice, children }) {
  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column', background: T.bg, overflow: 'hidden',
      userSelect: 'none', WebkitUserSelect: 'none', WebkitTouchCallout: 'none',
    }}>
      {children}

      {/* Person B — top half, rotated so they read it from across the table */}
      <LiveSidePanel side="B" data={sideB} rotated />

      <div style={{
        height: 56, background: T.surface, display: 'flex', alignItems: 'center',
        padding: '0 16px', gap: 12, flexShrink: 0,
        borderTop: `1px solid ${T.hairline}`, borderBottom: `1px solid ${T.hairline}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: T.tealSoft }}>{sideB.langInfo.code.toUpperCase()}</span>
          <Icon name="swap" size={15} color="rgba(28,32,51,.35)" />
          <span style={{ fontSize: 11, fontWeight: 700, color: T.amberSoft }}>{sideA.langInfo.code.toUpperCase()}</span>
        </div>

        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          {badge ? (
            <span style={{
              fontFamily: T.mono, fontSize: 9, letterSpacing: '.12em', textTransform: 'uppercase',
              fontWeight: 700, color: T.tealDeep, background: T.tealLight, borderRadius: 99, padding: '4px 8px',
            }}>{badge}</span>
          ) : (
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: T.gold, animation: 'dot-blink 1.6s ease-in-out infinite' }} />
          )}
          <span style={{ fontSize: 12.5, color: 'rgba(28,32,51,.75)', fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}>{elapsedStr}</span>
        </div>

        <button
          onClick={onEnd}
          style={{
            height: 34, padding: '0 14px', borderRadius: 12, background: 'rgba(52,164,111,.15)',
            color: T.tealSoft, border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
          }}
        >
          <span style={{ width: 9, height: 9, background: 'currentColor', borderRadius: 2, display: 'block' }} />
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>End</span>
        </button>
      </div>

      {notice && (
        <div style={{
          flexShrink: 0, padding: '9px 18px', background: T.errorLight, color: T.error,
          fontSize: 12, lineHeight: 1.4,
        }}>{notice}</div>
      )}

      {/* Person A — bottom half, the phone's owner */}
      <LiveSidePanel side="A" data={sideA} />
    </div>
  );
}

// ─────────────────────────────────────────────
// FaceToFaceLiveScreen — real WebRTC, PTT
// ─────────────────────────────────────────────

function FaceToFaceLiveScreen({ config, onStop, onError, budgetSeconds = Infinity, onUsage, onExhausted }) {
  const { nameA, nameB, langA, langB } = config;
  const langAInfo = getLang(langA);
  const langBInfo = getLang(langB);

  // State
  const [phase, setPhase] = useState('connecting'); // connecting | connected | error
  const [pressA, setPressA] = useState(false);
  const [pressB, setPressB] = useState(false);
  const [turnStateA, setTurnStateA] = useState('ready'); // ready | recording | translating
  const [turnStateB, setTurnStateB] = useState('ready'); // ready | recording | translating
  const [elapsed, setElapsed] = useState(0);
  const [turnsA, setTurnsA] = useState([]); // spoken by A
  const [turnsB, setTurnsB] = useState([]); // spoken by B
  const [connState, setConnState] = useState('Connecting…');
  const [sessionId, setSessionId] = useState(null);

  // Refs
  const audioRefA = useRef(null);
  const audioRefB = useRef(null);
  const trackARef = useRef(null);
  const trackBRef = useRef(null);
  const pcARef = useRef(null);
  const pcBRef = useRef(null);
  const pcAIdRef = useRef(null);
  const pcBIdRef = useRef(null);
  const pointerARef = useRef(null);
  const pointerBRef = useRef(null);
  const gateQueueARef = useRef(Promise.resolve());
  const gateQueueBRef = useRef(Promise.resolve());
  const streamRef = useRef(null);
  const timerRef = useRef(null);
  const pollRef = useRef(null);
  const sessionIdRef = useRef(null);
  const transcriptRef = useRef([]);
  const elapsedRef = useRef(0);
  const mountedRef = useRef(true);
  const turnTimeoutARef = useRef(null);
  const turnTimeoutBRef = useRef(null);

  // Setup WebRTC on mount
  useEffect(() => {
    mountedRef.current = true;
    setupSession();
    return () => {
      mountedRef.current = false;
      cleanup();
    };
  }, []);

  async function setupSession() {
    try {
      // 1. Mic
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;
      if (!mountedRef.current) { stream.getTracks().forEach(t => t.stop()); return; }

      // 2. ICE servers
      const iceServers = await fetchIceServers();

      // 3. Create session
      const sessionRes = await api('/api/translation/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caller_name: nameA, caller_language: langA, topic: 'Translation Session', client_id: clientId() }),
      });
      if (!sessionRes.ok) throw new Error('Session create failed');
      const { session_id } = await sessionRes.json();
      if (!mountedRef.current) return;
      sessionIdRef.current = session_id;
      setSessionId(session_id);
      void persistSession('active');

      // 4. Create peer connections
      const pcCfg = { iceServers, iceTransportPolicy: 'relay' };
      const pcA = new RTCPeerConnection(pcCfg);
      const pcB = new RTCPeerConnection(pcCfg);
      pcARef.current = pcA;
      pcBRef.current = pcB;

      // 5. Clone the microphone directly for each leg. Keep both clones live while
      // negotiating so mobile Safari establishes RTP for them; starting negotiation
      // with disabled tracks can leave them permanently silent when enabled later.
      const sourceTrack = stream.getAudioTracks()[0];
      const trackA = sourceTrack.clone();
      const trackB = sourceTrack.clone();
      trackARef.current = trackA;
      trackBRef.current = trackB;

      // 6. Audio output
      pcA.ontrack = e => {
        if (audioRefA.current) audioRefA.current.srcObject = e.streams[0];
      };
      pcB.ontrack = e => {
        if (audioRefB.current) audioRefB.current.srcObject = e.streams[0];
      };

      // 7. Connection state monitoring
      pcA.onconnectionstatechange = () => {
        if (!mountedRef.current) return;
        const st = pcA.connectionState;
        if (st === 'connected') {
          setPhase('connected');
          setConnState('Connected');
          startTimer();
          startPoll();
        } else if (st === 'failed' || st === 'disconnected') {
          setPhase('error');
          setConnState('Connection lost');
        }
      };

      // 8. Create offers
      const streamA = new MediaStream([trackA]);
      const streamB = new MediaStream([trackB]);
      const offerA = await createOffer(pcA, streamA);
      const offerB = await createOffer(pcB, streamB);

      if (!mountedRef.current) return;

      // 9. Connect A
      const ansA = await api('/api/translation/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id, language: langA, name: nameA, sdp: offerA.sdp, type: offerA.type }),
      });
      if (!ansA.ok) throw new Error('Offer A failed');
      const ansAData = await ansA.json();
      pcAIdRef.current = ansAData.pc_id;
      await pcA.setRemoteDescription({ sdp: ansAData.sdp, type: ansAData.type });

      if (!mountedRef.current) return;

      // 10. Connect B
      const ansB = await api('/api/translation/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id, language: langB, name: nameB, sdp: offerB.sdp, type: offerB.type }),
      });
      if (!ansB.ok) throw new Error('Offer B failed');
      const ansBData = await ansB.json();
      pcBIdRef.current = ansBData.pc_id;
      await pcB.setRemoteDescription({ sdp: ansBData.sdp, type: ansBData.type });

      // RTP is established; push-to-talk owns the tracks from this point onward.
      trackA.enabled = false;
      trackB.enabled = false;

    } catch (err) {
      console.error(
        'FaceToFace setup error:',
        err?.name || 'Error',
        err?.message || String(err),
        err?.stack || ''
      );
      if (!mountedRef.current) return;
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        onError('mic');
      } else {
        // Previously this only set local state, leaving a dead panel with no way
        // out but "End" — the failure a reviewer on a flaky connection hits.
        // ErrorScreen already handles this case and offers a retry.
        setPhase('error');
        setConnState('Connection failed');
        onError('network');
      }
    }
  }

  // Metered minutes are only honest if they survive a crash, so usage is
  // reported on a short cadence rather than only at teardown. Reports carry the
  // session's total elapsed seconds, so a duplicate or a retry settles to the
  // same balance instead of charging twice.
  const USAGE_REPORT_EVERY = 15;

  function reportElapsed() {
    if (onUsage && sessionIdRef.current) onUsage(sessionIdRef.current, elapsedRef.current);
  }

  function startTimer() {
    if (timerRef.current) return;
    timerRef.current = setInterval(() => {
      if (!mountedRef.current) return;
      setElapsed(e => {
        elapsedRef.current = e + 1;
        return elapsedRef.current;
      });

      const spent = elapsedRef.current;
      if (spent % USAGE_REPORT_EVERY === 0) reportElapsed();

      // Out of credit: end the call rather than let it run on unbilled. The
      // user keeps what was said — the transcript is persisted first.
      if (spent >= budgetSeconds) {
        reportElapsed();
        persistSession('ended').finally(() => {
          cleanup();
          if (onExhausted) onExhausted();
          else onStop();
        });
      }
    }, 1000);
  }

  function clearTurnTimeout(side) {
    const timeoutRef = side === 'A' ? turnTimeoutARef : turnTimeoutBRef;
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }

  function markTurnReady(side) {
    clearTurnTimeout(side);
    if (!mountedRef.current) return;
    if (side === 'A') setTurnStateA('ready');
    else setTurnStateB('ready');
  }

  function markTurnTranslating(side) {
    clearTurnTimeout(side);
    if (side === 'A') setTurnStateA('translating');
    else setTurnStateB('translating');

    // If an upstream translation fails without producing a turn event, don't
    // permanently strand this speaker. The backend uses the same stale-turn
    // recovery window, so the next press starts a fresh independent utterance.
    const timeoutRef = side === 'A' ? turnTimeoutARef : turnTimeoutBRef;
    timeoutRef.current = setTimeout(() => markTurnReady(side), 36000);
  }

  function startPoll() {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      if (!mountedRef.current || !sessionIdRef.current) return;
      try {
        const r = await api(`/api/translation/poll?session_id=${sessionIdRef.current}`);
        if (!r.ok || !mountedRef.current) return;
        const d = await r.json();
        if (d.events && d.events.length > 0) {
          const turnEvents = d.events.filter(e => e.type === 'turn');
          if (turnEvents.length > 0) {
            turnEvents.forEach(ev => {
              transcriptRef.current = [...transcriptRef.current, ev];
              const isA = ev.speaker
                ? ev.speaker === pcAIdRef.current
                : ev.original_lang === langA;
              if (isA) {
                setTurnsB(prev => [...prev, ev]);
                markTurnReady('A');
              } else {
                setTurnsA(prev => [...prev, ev]);
                markTurnReady('B');
              }
            });
            void persistSession('active');
          }
          // A turn the server gave up on (agent never answered): release the
          // speaker's "Translating" state immediately instead of waiting for
          // the 36s client fallback.
          d.events.filter(e => e.type === 'turn_failed').forEach(ev => {
            markTurnReady(ev.speaker === pcAIdRef.current ? 'A' : 'B');
          });
        }
        if (d.closed) {
          await persistSession('ended');
          cleanup();
          if (mountedRef.current) onStop();
        }
      } catch {}
    }, 1000);
  }

  async function persistSession(status) {
    if (!sessionIdRef.current) return;
    await saveNativeSession({
      sessionId: sessionIdRef.current,
      callerName: nameA,
      topic: 'Translation Session',
      languageA: langA,
      languageB: langB,
      participantA: nameA,
      participantB: nameB,
      status,
      durationSeconds: elapsedRef.current,
      transcript: transcriptRef.current,
    });
  }

  function cleanup() {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    pointerARef.current = null;
    pointerBRef.current = null;
    clearTurnTimeout('A');
    clearTurnTimeout('B');
    if (pcAIdRef.current) { fireAndForgetHangup(pcAIdRef.current); pcAIdRef.current = null; }
    if (pcBIdRef.current) { fireAndForgetHangup(pcBIdRef.current); pcBIdRef.current = null; }
    if (audioRefA.current) audioRefA.current.srcObject = null;
    if (audioRefB.current) audioRefB.current.srcObject = null;
    if (trackARef.current) { trackARef.current.stop(); trackARef.current = null; }
    if (trackBRef.current) { trackBRef.current.stop(); trackBRef.current = null; }
    if (pcARef.current) { pcARef.current.ontrack = null; pcARef.current.onconnectionstatechange = null; pcARef.current.close(); pcARef.current = null; }
    if (pcBRef.current) { pcBRef.current.ontrack = null; pcBRef.current.onconnectionstatechange = null; pcBRef.current.close(); pcBRef.current = null; }
    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null; }
    if (mountedRef.current) {
      setPressA(false);
      setPressB(false);
      setTurnStateA('ready');
      setTurnStateB('ready');
    }
  }

  // PTT handlers — the mic track is only unmuted once the server-side gate for
  // this participant has actually opened, so a turn never starts mid-flight.
  const queueGate = useCallback((queueRef, pcId, action) => {
    const operation = queueRef.current
      .catch(() => {})
      .then(() => setTranslationPttGate(pcId, action));
    queueRef.current = operation;
    return operation;
  }, []);

  const pressBStart = useCallback((event) => {
    event.preventDefault();
    if (turnStateB !== 'ready' || pressA) return;
    const pointerId = event.pointerId;
    pointerBRef.current = pointerId;
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch (_) {}
    if (trackARef.current) trackARef.current.enabled = false;
    setPressA(false);
    setPressB(true);
    setTurnStateB('recording');
    void queueGate(gateQueueBRef, pcBIdRef.current, 'hold')
      .then(() => {
        if (pointerBRef.current === pointerId && trackBRef.current) {
          trackBRef.current.enabled = true;
        }
      })
      .catch(error => {
        console.error('Person B PTT gate failed:', error);
        if (pointerBRef.current === pointerId) {
          pointerBRef.current = null;
          setPressB(false);
          setTurnStateB('ready');
        }
      });
  }, [queueGate, turnStateB, pressA]);
  const pressBEnd = useCallback((event) => {
    const activePointer = pointerBRef.current;
    if (activePointer == null) return;
    if (event?.pointerId != null && event.pointerId !== activePointer) return;
    if (trackBRef.current) trackBRef.current.enabled = false;
    if (event?.currentTarget) {
      try { event.currentTarget.releasePointerCapture(activePointer); } catch (_) {}
    }
    pointerBRef.current = null;
    setPressB(false);
    markTurnTranslating('B');
    void queueGate(gateQueueBRef, pcBIdRef.current, 'release')
      .then(result => {
        if (!result?.flushed_bytes) markTurnReady('B');
      })
      .catch(error => {
        console.error('Person B PTT release failed:', error);
        markTurnReady('B');
      });
  }, [queueGate]);
  const pressAStart = useCallback((event) => {
    event.preventDefault();
    if (turnStateA !== 'ready' || pressB) return;
    const pointerId = event.pointerId;
    pointerARef.current = pointerId;
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch (_) {}
    if (trackBRef.current) trackBRef.current.enabled = false;
    setPressB(false);
    setPressA(true);
    setTurnStateA('recording');
    void queueGate(gateQueueARef, pcAIdRef.current, 'hold')
      .then(() => {
        if (pointerARef.current === pointerId && trackARef.current) {
          trackARef.current.enabled = true;
        }
      })
      .catch(error => {
        console.error('Person A PTT gate failed:', error);
        if (pointerARef.current === pointerId) {
          pointerARef.current = null;
          setPressA(false);
          setTurnStateA('ready');
        }
      });
  }, [queueGate, turnStateA, pressB]);
  const pressAEnd = useCallback((event) => {
    const activePointer = pointerARef.current;
    if (activePointer == null) return;
    if (event?.pointerId != null && event.pointerId !== activePointer) return;
    if (trackARef.current) trackARef.current.enabled = false;
    if (event?.currentTarget) {
      try { event.currentTarget.releasePointerCapture(activePointer); } catch (_) {}
    }
    pointerARef.current = null;
    setPressA(false);
    markTurnTranslating('A');
    void queueGate(gateQueueARef, pcAIdRef.current, 'release')
      .then(result => {
        if (!result?.flushed_bytes) markTurnReady('A');
      })
      .catch(error => {
        console.error('Person A PTT release failed:', error);
        markTurnReady('A');
      });
  }, [queueGate]);

  const lastTurnA = turnsA[turnsA.length - 1];
  const lastTurnB = turnsB[turnsB.length - 1];
  const elapsedStr = formatClock(elapsed);

  // Chronological view of the same events the poll already banked. Read-only —
  // turnsA/turnsB drive the re-render, this ref only supplies stable ordering.
  const allTurns = transcriptRef.current;
  const spokenByA = ev => ev.original_lang === langA;
  const hasSpokenA = allTurns.some(ev => ev.speaker
    ? ev.speaker === pcAIdRef.current
    : spokenByA(ev));
  const hasSpokenB = allTurns.some(ev => ev.speaker
    ? ev.speaker === pcBIdRef.current
    : !spokenByA(ev));

  return (
    <LiveSplitView
      elapsedStr={elapsedStr}
      onEnd={async () => { await persistSession('ended'); reportElapsed(); cleanup(); onStop(); }}
      sideA={{
        langInfo: langAInfo,
        name: nameA,
        pressing: pressA,
        disabled: phase !== 'connected' || turnStateA === 'translating' || pressB,
        dimmed: turnStateA === 'translating',
        onDown: pressAStart,
        onUp: pressAEnd,
        status: pressA ? 'Recording'
          : turnStateA === 'translating' ? 'Translating...'
          : pressB ? `Listening to ${nameB}`
          : phase === 'connected' ? (hasSpokenA ? 'Ready - speak again' : 'Ready')
          : connState,
        turns: allTurns.map((t, i) => ({ key: i, turn: t, mine: spokenByA(t) })),
        note: phase === 'connecting' && !lastTurnA ? connState : null,
      }}
      sideB={{
        langInfo: langBInfo,
        name: nameB,
        pressing: pressB,
        disabled: phase !== 'connected' || turnStateB === 'translating' || pressA,
        dimmed: turnStateB === 'translating',
        onDown: pressBStart,
        onUp: pressBEnd,
        status: pressB ? 'Recording'
          : turnStateB === 'translating' ? 'Translating...'
          : pressA ? `Listening to ${nameA}`
          : phase === 'connected' ? (hasSpokenB ? 'Ready - speak again' : 'Ready')
          : connState,
        turns: allTurns.map((t, i) => ({ key: i, turn: t, mine: !spokenByA(t) })),
        note: phase === 'connecting' && !lastTurnB ? connState : null,
      }}
    >
      {/* Hidden audio elements — one per leg */}
      <audio ref={audioRefA} autoPlay playsInline style={{ display: 'none' }} />
      <audio ref={audioRefB} autoPlay playsInline style={{ display: 'none' }} />
    </LiveSplitView>
  );
}

// ─────────────────────────────────────────────
// AutoLiveScreen — always-listening WebRTC
// ─────────────────────────────────────────────

function AutoLiveScreen({ config, onStop, onError }) {
  const { nameA, nameB, langA, langB } = config;
  const langAInfo = getLang(langA);
  const langBInfo = getLang(langB);

  const [phase, setPhase] = useState('connecting');
  const [mutedA, setMutedA] = useState(false);
  const [mutedB, setMutedB] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [turnsA, setTurnsA] = useState([]);
  const [turnsB, setTurnsB] = useState([]);
  const [statusA, setStatusA] = useState('listening');
  const [statusB, setStatusB] = useState('listening');
  const [connState, setConnState] = useState('Connecting…');

  const audioRef = useRef(null);
  const pcRef = useRef(null);
  const pcIdRef = useRef(null);
  const streamRef = useRef(null);
  const timerRef = useRef(null);
  const pollRef = useRef(null);
  const sessionIdRef = useRef(null);
  const mountedRef = useRef(true);
  const mutedARef = useRef(false);
  const mutedBRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    setupSession();
    return () => {
      mountedRef.current = false;
      cleanup();
    };
  }, []);

  // Sync muted state to refs + track
  useEffect(() => {
    mutedARef.current = mutedA;
    applyMute();
  }, [mutedA]);

  useEffect(() => {
    mutedBRef.current = mutedB;
    applyMute();
  }, [mutedB]);

  function applyMute() {
    if (!streamRef.current) return;
    const track = streamRef.current.getAudioTracks()[0];
    if (track) track.enabled = !mutedARef.current && !mutedBRef.current;
  }

  async function setupSession() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;
      if (!mountedRef.current) { stream.getTracks().forEach(t => t.stop()); return; }

      const iceServers = await fetchIceServers();

      const sessionRes = await api('/api/translation/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caller_name: nameA, caller_language: langA, topic: 'Auto Translation', client_id: clientId() }),
      });
      if (!sessionRes.ok) throw new Error('Session create failed');
      const { session_id } = await sessionRes.json();
      if (!mountedRef.current) return;
      sessionIdRef.current = session_id;

      const pc = new RTCPeerConnection({ iceServers, iceTransportPolicy: 'relay' });
      pcRef.current = pc;

      pc.ontrack = e => {
        if (audioRef.current) audioRef.current.srcObject = e.streams[0];
      };

      pc.onconnectionstatechange = () => {
        if (!mountedRef.current) return;
        const st = pc.connectionState;
        if (st === 'connected') {
          setPhase('connected');
          setConnState('Connected');
          startTimer();
          startPoll();
        } else if (st === 'failed' || st === 'disconnected') {
          setPhase('error');
          setConnState('Connection lost');
        }
      };

      const offer = await createOffer(pc, stream);
      if (!mountedRef.current) return;

      const ans = await api('/api/translation/auto-offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id, lang_a: langA, lang_b: langB, sdp: offer.sdp, type: offer.type }),
      });
      if (!ans.ok) throw new Error('Auto-offer failed');
      const ansData = await ans.json();
      pcIdRef.current = ansData.pc_id;
      await pc.setRemoteDescription({ sdp: ansData.sdp, type: ansData.type });

    } catch (err) {
      console.error('Auto setup error:', err);
      if (!mountedRef.current) return;
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        onError('mic');
      } else {
        // Previously this only set local state, leaving a dead panel with no way
        // out but "End" — the failure a reviewer on a flaky connection hits.
        // ErrorScreen already handles this case and offers a retry.
        setPhase('error');
        setConnState('Connection failed');
        onError('network');
      }
    }
  }

  function startTimer() {
    if (timerRef.current) return;
    timerRef.current = setInterval(() => {
      if (mountedRef.current) setElapsed(e => e + 1);
    }, 1000);
  }

  function startPoll() {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      if (!mountedRef.current || !sessionIdRef.current) return;
      try {
        const r = await api(`/api/translation/poll?session_id=${sessionIdRef.current}`);
        if (!r.ok || !mountedRef.current) return;
        const d = await r.json();
        if (d.events && d.events.length > 0) {
          d.events.forEach(ev => {
            if (ev.type === 'turn') {
              const isA = ev.original_lang === langA;
              if (isA) {
                setTurnsB(prev => [...prev, ev]);
                setStatusB('speaking');
                setTimeout(() => { if (mountedRef.current) setStatusB('listening'); }, 2000);
              } else {
                setTurnsA(prev => [...prev, ev]);
                setStatusA('speaking');
                setTimeout(() => { if (mountedRef.current) setStatusA('listening'); }, 2000);
              }
            } else if (ev.type === 'status') {
              // status events (listening, translating, etc.)
              if (ev.lang === langA) setStatusA(ev.status);
              else if (ev.lang === langB) setStatusB(ev.status);
            }
          });
        }
        if (d.closed) {
          cleanup();
          if (mountedRef.current) onStop();
        }
      } catch {}
    }, 1000);
  }

  function cleanup() {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (pcIdRef.current) { fireAndForgetHangup(pcIdRef.current); pcIdRef.current = null; }
    if (audioRef.current) audioRef.current.srcObject = null;
    if (pcRef.current) { pcRef.current.ontrack = null; pcRef.current.onconnectionstatechange = null; pcRef.current.close(); pcRef.current = null; }
    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null; }
    if (mountedRef.current) {
      setMutedA(false);
      setMutedB(false);
    }
  }

  function toggleMuteA() {
    setMutedA(m => !m);
    setStatusA(prev => prev === 'muted' ? 'listening' : 'muted');
  }

  function toggleMuteB() {
    setMutedB(m => !m);
    setStatusB(prev => prev === 'muted' ? 'listening' : 'muted');
  }

  const lastTurnA = turnsA[turnsA.length - 1];
  const lastTurnB = turnsB[turnsB.length - 1];
  const elapsedStr = formatDuration(elapsed);

  // Derive effective status
  const effStatusA = mutedA ? 'muted' : statusA;
  const effStatusB = mutedB ? 'muted' : statusB;

  const speakingB = effStatusB === 'speaking';
  const speakingA = effStatusA === 'speaking';

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.bg, overflow: 'hidden' }}>
      <audio ref={audioRef} autoPlay playsInline style={{ display: 'none' }} />

      {/* Person B panel — top, rotated 180° */}
      <div style={{
        flex: 1,
        transform: 'rotate(180deg)',
        background: speakingB ? T.teal + '22' : T.bg,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '24px 24px 20px',
        transition: 'background 0.3s',
      }}>
        {/* Person info */}
        <div style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 10 }}>
          <PersonNameTag langInfo={langBInfo} name={nameB} color={T.teal} pressing={false} />
          <div style={{ marginLeft: 'auto' }}>
            <AutoStatusTag status={effStatusB} color={T.teal} />
          </div>
        </div>

        {/* Content area: waveform / last phrase */}
        <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {speakingB && (
            <div style={{
              padding: '10px 14px',
              background: T.teal + '18',
              border: `1px dashed ${T.teal}55`,
              borderRadius: 12,
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              minHeight: 42,
              overflow: 'hidden',
            }}>
              <Waveform color={T.teal} active={true} height={20} />
              <span style={{
                fontSize: 13,
                color: T.teal,
                fontWeight: 500,
                letterSpacing: '-0.01em',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                Listening for speech<span style={{ animation: 'dot-blink 1s infinite' }}>|</span>
              </span>
            </div>
          )}
          {lastTurnB && (
            <div style={{ animation: 'fade-in-up 0.3s ease-out' }}>
              <ConvTurn turn={lastTurnB} colorHex={T.teal} />
            </div>
          )}
          {phase === 'connecting' && !lastTurnB && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <TypingDots color={T.textFaint} />
              <span style={{ fontSize: 13, color: T.textFaint }}>{connState}</span>
            </div>
          )}
        </div>

        {/* Mute button — 36px circle */}
        <button
          onClick={toggleMuteB}
          style={{
            width: 36, height: 36,
            borderRadius: '50%',
            background: mutedB ? T.errorLight : T.tealLight,
            border: `1.5px solid ${mutedB ? T.error : T.teal}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            transition: 'all 0.15s',
          }}
        >
          <Icon name={mutedB ? 'mic-off' : 'mic'} size={17} color={mutedB ? T.error : T.teal} />
        </button>
      </div>

      {/* Center divider — 44px, white bg */}
      <div style={{
        height: 44,
        background: '#fff',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 20px',
        flexShrink: 0,
        borderTop: `1px solid ${T.border}`,
        borderBottom: `1px solid ${T.border}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 15 }}>{langAInfo.flag}</span>
          <span style={{ fontSize: 12, color: T.textFaint }}>↕</span>
          <span style={{ fontSize: 15 }}>{langBInfo.flag}</span>
        </div>
        {phase === 'connected' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: T.success, animation: 'dot-blink 2s infinite' }} />
            <span style={{ fontSize: 12, color: T.textMuted }}>{elapsedStr}</span>
          </div>
        )}
        {phase === 'connecting' && (
          <span style={{ fontSize: 12, color: T.textFaint }}>Connecting…</span>
        )}
        {phase === 'error' && (
          <span style={{ fontSize: 12, color: T.error }}>Disconnected</span>
        )}
        <button
          onClick={() => { cleanup(); onStop(); }}
          style={{
            width: 28, height: 28,
            borderRadius: '50%',
            background: T.error,
            border: 'none',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <Icon name="stop" size={14} color="#fff" />
        </button>
      </div>

      {/* Person A panel — bottom */}
      <div style={{
        flex: 1,
        background: speakingA ? T.amber + '22' : T.bg,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '20px 24px 32px',
        transition: 'background 0.3s',
      }}>
        {/* Person info */}
        <div style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 10 }}>
          <PersonNameTag langInfo={langAInfo} name={nameA} color={T.amber} pressing={false} />
          <div style={{ marginLeft: 'auto' }}>
            <AutoStatusTag status={effStatusA} color={T.amber} />
          </div>
        </div>

        {/* Content area */}
        <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {speakingA && (
            <div style={{
              padding: '10px 14px',
              background: T.amber + '18',
              border: `1px dashed ${T.amber}55`,
              borderRadius: 12,
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              minHeight: 42,
              overflow: 'hidden',
            }}>
              <Waveform color={T.amber} active={true} height={20} />
              <span style={{
                fontSize: 13,
                color: T.amber,
                fontWeight: 500,
                letterSpacing: '-0.01em',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                Listening for speech<span style={{ animation: 'dot-blink 1s infinite' }}>|</span>
              </span>
            </div>
          )}
          {lastTurnA && (
            <div style={{ animation: 'fade-in-up 0.3s ease-out' }}>
              <ConvTurn turn={lastTurnA} colorHex={T.amber} />
            </div>
          )}
          {phase === 'connecting' && !lastTurnA && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <TypingDots color={T.textFaint} />
              <span style={{ fontSize: 13, color: T.textFaint }}>{connState}</span>
            </div>
          )}
        </div>

        {/* Mute button — 36px circle */}
        <button
          onClick={toggleMuteA}
          style={{
            width: 36, height: 36,
            borderRadius: '50%',
            background: mutedA ? T.errorLight : T.amberLight,
            border: `1.5px solid ${mutedA ? T.error : T.amber}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            transition: 'all 0.15s',
          }}
        >
          <Icon name={mutedA ? 'mic-off' : 'mic'} size={17} color={mutedA ? T.error : T.amber} />
        </button>
      </div>
    </div>
  );
}

// AutoStatusTag — status pill for auto screen
function AutoStatusTag({ status, color }) {
  if (status === 'muted') {
    return (
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: '3px 10px', borderRadius: 99,
        background: T.errorLight, fontSize: 12, fontWeight: 600, color: T.error,
      }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: T.error }} />
        Muted
      </div>
    );
  }
  if (status === 'speaking') {
    return (
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: '3px 10px', borderRadius: 99,
        background: color + '22', fontSize: 12, fontWeight: 600, color,
      }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: color, animation: 'dot-blink 1.4s ease-in-out infinite' }} />
        Speaking
      </div>
    );
  }
  // listening / idle / etc
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 99,
      background: T.surface2, fontSize: 12, fontWeight: 600, color: T.textMuted,
    }}>
      <div style={{ width: 6, height: 6, borderRadius: '50%', background: T.textFaint }} />
      Listening
    </div>
  );
}

// ─────────────────────────────────────────────
// LanguagePairScreen — edit both sides from the home language control
// ─────────────────────────────────────────────

function LanguagePairScreen({ langA, langB, onBack, onSave }) {
  const [sideA, setSideA] = useState(langA);
  const [sideB, setSideB] = useState(langB);
  const [picking, setPicking] = useState(null);

  if (picking) {
    return (
      <LangScreen
        onBack={() => setPicking(null)}
        onSelect={code => {
          if (picking === 'a') setSideA(code);
          else setSideB(code);
          setPicking(null);
        }}
      />
    );
  }

  const a = getLang(sideA);
  const b = getLang(sideB);
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.bg }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '52px 16px 16px', background: T.surface, borderBottom: `1px solid ${T.border}` }}>
        <button onClick={onBack} style={{ padding: 4, color: T.textMuted }}><Icon name="chevron-left" size={24} /></button>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>Conversation Languages</h1>
      </div>
      <div style={{ padding: '28px 16px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {[
          { side: 'a', caption: 'PERSON A SPEAKS', info: a, color: T.amber },
          { side: 'b', caption: 'PERSON B SPEAKS', info: b, color: T.teal },
        ].map(item => (
          <button key={item.side} onClick={() => setPicking(item.side)} style={{ width: '100%', padding: '20px', borderRadius: 20, background: T.surface, border: `1px solid ${T.border}`, display: 'flex', alignItems: 'center', gap: 16, textAlign: 'left' }}>
            <div style={{ width: 50, height: 50, borderRadius: 16, background: item.color + '22', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 25 }}>{item.info.flag}</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1.1, color: T.textFaint }}>{item.caption}</div>
              <div style={{ fontSize: 18, fontWeight: 650, color: T.textPrimary, marginTop: 4 }}>{item.info.label}</div>
            </div>
            <Icon name="chevron" size={18} color={T.textFaint} />
          </button>
        ))}
        <button onClick={() => { setSideA(sideB); setSideB(sideA); }} style={{ alignSelf: 'center', width: 48, height: 48, borderRadius: '50%', background: T.slate, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '-7px 0', zIndex: 1 }} aria-label="Swap languages">
          <Icon name="swap" size={21} color={T.cream} />
        </button>
        <button onClick={() => onSave(sideA, sideB)} style={{ width: '100%', padding: 17, marginTop: 10, borderRadius: 17, background: T.amber, color: T.cream, fontSize: 16, fontWeight: 700 }}>
          Use These Languages
        </button>
      </div>
    </div>
  );
}

// LangScreen — single language picker
// ─────────────────────────────────────────────

function LangScreen({ onBack, onSelect, targetName = 'Person', variant = 'amber', selected }) {
  const [query, setQuery] = useState('');
  const accent = variant === 'teal' ? '#2A8B5D' : T.amber;
  const tint = variant === 'teal' ? 'rgba(52,164,111,.1)' : 'rgba(108,92,231,.08)';

  const filtered = query.trim()
    ? LANGUAGES.filter(l =>
        l.label.toLowerCase().includes(query.toLowerCase()) ||
        l.code.toLowerCase().includes(query.toLowerCase())
      )
    : LANGUAGES;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.cream, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '60px 20px 14px', background: '#fff', borderBottom: '1px solid rgba(28,32,51,.08)',
        flexShrink: 0,
      }}>
        <button onClick={onBack} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(28,32,51,.05)', color: T.slate }}>
          <Icon name="chevron-left" size={17} />
        </button>
        <div><div style={{ fontSize: 9.5, letterSpacing: '.14em', textTransform: 'uppercase', fontWeight: 700, color: accent }}>{targetName}</div><h1 style={{ fontFamily: T.display, fontSize: 24, fontWeight: 700, letterSpacing: '-.02em', color: T.slate, lineHeight: 1.1 }}>Choose language</h1></div>
      </div>

      {/* Search */}
      <div style={{ padding: '0 20px 14px', background: '#fff', flexShrink: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '11px 13px', background: 'rgba(28,32,51,.05)', borderRadius: 14,
        }}>
          <Icon name="search" size={16} color="rgba(28,32,51,.4)" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search languages"
            style={{
              flex: 1,
              background: 'none',
              border: 'none',
              outline: 'none',
              fontSize: 15,
              color: T.textPrimary,
            }}
          />
        </div>
      </div>

      {/* Language list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 14px 24px' }}>
        {filtered.map(lang => (
          <button
            key={lang.code}
            onClick={() => onSelect(lang.code)}
            style={{
              display: 'flex', alignItems: 'center', gap: 13, padding: '14px 12px', borderRadius: 16,
              width: '100%',
              textAlign: 'left',
            }}
          >
            <span style={{ width: 40, height: 40, borderRadius: 13, background: tint, color: accent, display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 700 }}>{lang.code.toUpperCase()}</span>
            <p style={{ flex: 1, fontSize: 15.5, fontWeight: 600, color: T.slate }}>{lang.label}</p>
            {selected === lang.code && <Icon name="check" size={18} color={T.amber} />}
          </button>
        ))}
        {filtered.length === 0 && (
          <p style={{ textAlign: 'center', padding: '40px 20px', color: T.textFaint, fontSize: 14 }}>
            No languages found
          </p>
        )}
        <div style={{ height: 24 }} />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// HistoryScreen
// ─────────────────────────────────────────────

// ─────────────────────────────────────────────
// OfflineLiveScreen — fully on-device, no network
// ─────────────────────────────────────────────
//
// Deliberately separate from FaceToFaceLiveScreen. That screen is a WebRTC
// session against the server; this one never touches the network at all:
//   hold  -> on-device speech recognition
//   release -> on-device translation
//   then  -> on-device speech synthesis
// Keeping them apart means nothing here can affect a live server session.

// Speech and synthesis want full locales, not bare language codes.
const SPEECH_LOCALE = {
  en: 'en-US', zh: 'zh-CN', yue: 'zh-HK', ja: 'ja-JP', ko: 'ko-KR',
  es: 'es-ES', fr: 'fr-FR', de: 'de-DE', ar: 'ar-SA', hi: 'hi-IN', fil: 'fil-PH',
};
const speechLocale = code => SPEECH_LOCALE[code] || code;


function OfflineLiveScreen({ langA, langB, onBack }) {
  const [turns, setTurns] = useState([]);
  const [holding, setHolding] = useState(null);   // 'a' | 'b' | null
  const [phase, setPhase] = useState('idle');     // idle | listening | translating | speaking
  const [error, setError] = useState(null);
  const [partial, setPartial] = useState('');
  // Fixed for the session: the pair is chosen on the home screen, exactly as it
  // is for a cloud session.
  const a = langA;
  const b = langB;
  const [sttLocales, setSttLocales] = useState(null); // locales the recognizer knows
  const [elapsed, setElapsed] = useState(0);
  const listenerRef = useRef(null);
  const startRef = useRef(null);

  const SR = window.Capacitor?.Plugins?.SpeechRecognition;
  const TTS = window.Capacitor?.Plugins?.TextToSpeech;

  useEffect(() => {
    const tick = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => clearInterval(tick);
  }, []);

  useEffect(() => {
    let handle;
    (async () => {
      try {
        handle = await SR?.addListener?.('partialResults', ev => {
          setPartial((ev?.matches && ev.matches[0]) || '');
        });
        listenerRef.current = handle;
      } catch (_) {}
      try {
        const { languages = [] } = await SR?.getSupportedLanguages?.() || {};
        setSttLocales(languages.map(l => String(l).toLowerCase()));
      } catch (_) {
        setSttLocales([]);   // unknown: do not block, just cannot pre-warn
      }
    })();
    return () => {
      try { listenerRef.current?.remove?.(); } catch (_) {}
      try { SR?.stop?.(); } catch (_) {}
      try { TTS?.stop?.(); } catch (_) {}
    };
  }, []);

  async function press(side) {
    if (phase !== 'idle' || !SR) return;
    setError(null);
    setPartial('');
    setHolding(side);
    setPhase('listening');
    const from = side === 'a' ? a : b;
    try {
      // Check availability before asking for anything. On iOS, touching speech
      // recognition without NSSpeechRecognitionUsageDescription terminates the
      // app outright rather than throwing, so the usage string is mandatory —
      // this guard only covers the softer cases (no engine, unsupported device).
      const avail = await SR.available?.();
      if (avail && avail.available === false) {
        throw new Error('On-device speech recognition is not available on this device.');
      }
      const perm = await SR.requestPermissions();
      const state = perm?.speechRecognition;
      if (state && state !== 'granted') {
        throw new Error(
          'Speech recognition permission was declined. Enable it for Vocare in iPhone Settings.'
        );
      }
      // start() resolves with the FINAL matches when recognition ends — stop()
      // returns void and never carries a result. Hold the promise and read it
      // after stop(); relying on partial results alone silently loses the whole
      // utterance for any language whose partials do not arrive.
      startRef.current = SR.start({
        language: speechLocale(from),
        useOnDeviceRecognition: true,   // the whole point — no audio leaves the device
        partialResults: true,
        maxResults: 1,
        popup: false,
      });
      startRef.current.catch(() => {});   // handled in release()
    } catch (err) {
      setHolding(null);
      setPhase('idle');
      setError(err?.message || 'Could not start on-device speech recognition.');
    }
  }

  async function release(side) {
    if (holding !== side) return;
    const from = side === 'a' ? a : b;
    const to = side === 'a' ? b : a;
    setHolding(null);
    let heard = '';
    try {
      await SR.stop();
      // Give the recognizer a moment to finalise, but never hang on it.
      const res = await Promise.race([
        startRef.current,
        new Promise(resolve => setTimeout(() => resolve(null), 4000)),
      ]);
      heard = (res && res.matches && res.matches[0]) || partial || '';
    } catch (_) {
      heard = partial || '';
    } finally {
      startRef.current = null;
    }
    heard = heard.trim();
    if (!heard) {
      // Previously this returned silently, which looked exactly like the button
      // doing nothing. The usual cause is the language having no on-device
      // recognition assets installed.
      setPhase('idle');
      setPartial('');
      setError(
        `Nothing was recognised in ${getLang(from).label}. `
        + `On-device recognition for ${getLang(from).label} may not be installed — `
        + 'add it under iPhone Settings \u203a General \u203a Keyboard \u203a Dictation Languages.'
      );
      return;
    }

    setPhase('translating');
    let translated = null;
    try {
      translated = await translateOffline(heard, from, to);
    } catch (_) {}

    if (!translated) {
      setPhase('idle');
      setPartial('');
      setError(
        `Could not translate ${getLang(from).label} to ${getLang(to).label} on this device. `
        + 'The language may not be downloaded — check Settings.'
      );
      return;
    }

    setTurns(t => [...t, { side, from, to, original: heard, translated }]);
    setPartial('');
    setPhase('speaking');
    try {
      await TTS?.speak({ text: translated, lang: speechLocale(to), rate: 1.0 });
    } catch (_) {
      // A missing voice should not lose the transcript — the text still shows.
    }
    setPhase('idle');
  }

  const busy = phase !== 'idle';

  // NOTE: Half, Panel and Note live at module scope on purpose. A component
  // declared inside a render is a NEW component type each time, so React
  // unmounts and recreates its DOM. Mid-press that destroys the very node
  // holding the pointer, and the pointerup never reaches the handler — a
  // hold-to-speak button that sometimes never releases.


  const langAInfo = getLang(a);
  const langBInfo = getLang(b);

  const sideStatus = (side) => {
    if (holding === side) return 'Recording';
    if (phase === 'translating') return 'Translating...';
    if (phase === 'speaking') return 'Speaking...';
    if (holding) return side === 'a' ? 'Listening to Person B' : 'Listening to Person A';
    return turns.length ? 'Ready - speak again' : 'Ready';
  };

  const sideTurns = (side) => turns.map((t, i) => ({
    key: i,
    turn: t,
    mine: t.side === side,
  }));

  // The partial transcript belongs on the speaker's own half — it is what they
  // are saying, not what the other person is hearing.
  const sideNote = (side) => (holding === side && partial ? partial : null);

  return (
    <LiveSplitView
      elapsedStr={formatClock(elapsed)}
      onEnd={onBack}
      badge="On device"
      notice={error}
      sideA={{
        langInfo: langAInfo,
        name: 'Person A',
        pressing: holding === 'a',
        disabled: busy && holding !== 'a',
        dimmed: busy && holding !== 'a',
        onDown: () => press('a'),
        onUp: () => release('a'),
        status: sideStatus('a'),
        turns: sideTurns('a'),
        note: sideNote('a'),
      }}
      sideB={{
        langInfo: langBInfo,
        name: 'Person B',
        pressing: holding === 'b',
        disabled: busy && holding !== 'b',
        dimmed: busy && holding !== 'b',
        onDown: () => press('b'),
        onUp: () => release('b'),
        status: sideStatus('b'),
        turns: sideTurns('b'),
        note: sideNote('b'),
      }}
    />
  );
}

function HistoryScreen({ onBack, onSession }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const history = await loadSessionHistory();
        if (active) {
          setSessions(history);
          setLoading(false);
        }
      } catch {
        if (active) setLoading(false);
      }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => { active = false; clearInterval(id); };
  }, []);

  const live = sessions.filter(s => s.status === 'live' || s.status === 'active');
  const ended = sessions.filter(s => s.status !== 'live' && s.status !== 'active');

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: T.cream }}>
      <div style={{ padding: '62px 22px 14px', flexShrink: 0 }}>
        <h1 style={{ fontFamily: T.display, fontSize: 34, fontWeight: 700, letterSpacing: '-.02em', color: T.slate, lineHeight: 1.05 }}>History</h1>
      </div>

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '40px 0' }}>
            <TypingDots />
          </div>
        ) : sessions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px 24px', color: T.textFaint }}>
            <Icon name="history" size={40} color={T.border} style={{ margin: '0 auto 12px' }} />
            <p style={{ fontSize: 15 }}>No sessions yet</p>
          </div>
        ) : (
          <div style={{ padding: '6px 20px 96px' }}>
            {live.length > 0 && (
              <>
                <p style={{ fontSize: 10.5, fontWeight: 700, color: '#2A8B5D', textTransform: 'uppercase', letterSpacing: '.16em', margin: '6px 0 9px' }}>In progress</p>
                {live.map(s => <div key={s.session_id} style={{ background: T.slate, borderRadius: 20, padding: 16, marginBottom: 8 }}><button onClick={() => onSession(s.session_id)} style={{ width: '100%', color: T.cream, textAlign: 'left' }}><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: T.teal, animation: 'dot-blink 1.6s ease-in-out infinite' }} /><span style={{ fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', fontWeight: 700, color: T.tealSoft }}>Live · {formatDuration(s.duration)}</span></div><div style={{ fontSize: 17, fontWeight: 600, marginTop: 8 }}>{s.topic || 'Translation Session'}</div><div style={{ fontSize: 12, color: 'rgba(251,249,247,.55)', marginTop: 3 }}>{s.caller_name}</div></button></div>)}
              </>
            )}
            {ended.length > 0 && (
              <>
                {live.length > 0 && <div style={{ height: 8 }} />}
                <p style={{ fontSize: 10.5, fontWeight: 700, color: 'rgba(28,32,51,.45)', textTransform: 'uppercase', letterSpacing: '.16em', margin: '18px 0 9px' }}>Past</p>
                {ended.map(s => <SessionCard key={s.session_id} session={s} onClick={() => onSession(s.session_id)} />)}
              </>
            )}
          </div>
        )}
        <div style={{ height: 16 }} />
      </div>
    </div>
  );
}

function SessionDetailScreen({ sessionId, onBack }) {
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    let active = true;
    loadSessionDetail(sessionId).then(d => { if (active) setDetail(d); }).catch(() => {});
    return () => { active = false; };
  }, [sessionId]);
  const transcript = detail?.transcript || [];
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: T.cream }}>
      <div style={{ padding: '58px 20px 16px', background: T.slate, borderRadius: '0 0 28px 28px' }}>
        <button onClick={onBack} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(251,249,247,.1)', color: T.cream }}><Icon name="chevron-left" size={17} /></button>
        <h1 style={{ fontFamily: T.display, fontSize: 27, fontWeight: 700, letterSpacing: '-.02em', color: T.cream, marginTop: 14, lineHeight: 1.15 }}>{detail?.topic || 'Translation session'}</h1>
        <div style={{ fontSize: 12, color: 'rgba(251,249,247,.55)', marginTop: 6 }}>{detail?.caller_name || sessionId}</div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '18px 20px 40px' }}>
        <div style={{ background: '#fff', border: '1px solid rgba(108,92,231,.18)', borderRadius: 20, padding: 16 }}>
          <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: T.amber, fontWeight: 700 }}>Automated notes</div>
          <div style={{ fontSize: 13.5, lineHeight: 1.5, color: T.slate, marginTop: 11 }}>{transcript.length ? `${transcript.length} translated conversation turn${transcript.length === 1 ? '' : 's'} recorded.` : 'Notes will appear after translated conversation turns are recorded.'}</div>
        </div>
        <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(28,32,51,.45)', fontWeight: 700, margin: '20px 0 10px' }}>Full transcript</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {transcript.map((t, i) => <div key={i} style={{ alignSelf: i % 2 ? 'flex-end' : 'flex-start', maxWidth: '86%' }}><div style={{ fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', fontWeight: 700, color: i % 2 ? '#2A8B5D' : T.amber, marginBottom: 4 }}>{t.speaker_name || 'Speaker'}</div><div style={{ background: i % 2 ? '#fff' : 'rgba(108,92,231,.06)', border: `1px solid ${i % 2 ? 'rgba(52,164,111,.2)' : 'rgba(108,92,231,.16)'}`, borderRadius: 16, padding: '11px 13px', fontSize: 14.5, lineHeight: 1.45, color: T.slate }}>{t.original}</div><div style={{ fontSize: 11.5, color: 'rgba(28,32,51,.42)', marginTop: 4, fontStyle: 'italic' }}>{t.translated}</div></div>)}
        </div>
      </div>
    </div>
  );
}

// Which languages work with no connection, and how to get the ones that don't.
// Status is shown against English, the pivot both engines are built around.
function OfflinePanel({ children }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(28,32,51,.45)', fontWeight: 700, marginBottom: 9 }}>Offline translation</div>
      <div style={{ background: '#fff', border: '1px solid rgba(28,32,51,.08)', borderRadius: 18, overflow: 'hidden' }}>{children}</div>
    </div>
  );
}

function OfflineNote({ children }) {
  return <div style={{ padding: '14px 15px', fontSize: 13, lineHeight: 1.5, color: 'rgba(28,32,51,.6)' }}>{children}</div>;
}

function OfflineTranslationSection({ onStart }) {
  const engine = offlineEngine();
  const supported = offlineTranslationLanguages().filter(c => c !== 'en');
  const canDownload = offlineCanSelfDownload();
  const [statuses, setStatuses] = useState(null); // code -> installed|supported|unsupported
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  async function refresh() {
    if (!engine) { setStatuses({}); return; }
    const entries = await Promise.all(
      supported.map(async code => [code, await offlinePairStatus('en', code)])
    );
    setStatuses(Object.fromEntries(entries));
  }

  useEffect(() => { void refresh(); }, []);

  async function handleDownload(code) {
    setBusy(code); setError(null);
    try {
      // English is the pivot both engines translate through, so it is needed too.
      // English is the pivot both engines translate through.
      await downloadOfflinePair('en', code);
      await refresh();
    } catch (err) {
      setError(`Could not download ${getLang(code).label}. Check your connection and try again.`);
    } finally {
      setBusy(null);
    }
  }



  if (!offlineTranslationLanguages().length) {
    return <OfflinePanel><OfflineNote>Offline translation is available in the Vocare app on iOS and Android. In a browser, every session needs a connection.</OfflineNote></OfflinePanel>;
  }
  if (!engine) {
    return <OfflinePanel><OfflineNote>Offline translation is not available in this version. Sessions need a connection.</OfflineNote></OfflinePanel>;
  }

  return (
    <div>
      <OfflinePanel>
        <div style={{ padding: '13px 15px', borderBottom: '1px solid rgba(28,32,51,.06)', fontSize: 12.5, lineHeight: 1.5, color: 'rgba(28,32,51,.62)' }}>
          {canDownload
            ? 'Download a language to translate it to and from English with no connection. Each is about 30MB, so use Wi-Fi.'
            : 'Languages you have downloaded on this device can be translated to and from English with no connection. Add more in Settings \u203a Apps \u203a Translate \u203a Downloaded Languages.'}
          {' '}Cantonese has no offline model on any phone and always needs a connection.
        </div>
        {supported.map((code, i) => {
          const status = statuses ? statuses[code] : undefined;
          return (
            <div key={code} style={{ padding: '12px 15px', display: 'flex', alignItems: 'center', gap: 12, borderTop: i ? '1px solid rgba(28,32,51,.06)' : 'none' }}>
              <span style={{ fontSize: 17 }}>{getLang(code).flag}</span>
              <div style={{ flex: 1, fontSize: 14.5, fontWeight: 600, color: T.slate }}>{getLang(code).label}</div>
              {statuses === null && <span style={{ fontSize: 12, color: 'rgba(28,32,51,.4)' }}>Checking\u2026</span>}
              {status === 'installed' && <span style={{ fontSize: 12, fontWeight: 600, color: '#2A8B5D' }}>On device</span>}
              {status === 'unsupported' && <span style={{ fontSize: 12, color: 'rgba(28,32,51,.35)' }}>Not available</span>}
              {status === 'supported' && (canDownload ? (
                <button
                  onClick={() => handleDownload(code)}
                  disabled={busy === code}
                  style={{ fontSize: 12.5, fontWeight: 600, color: busy === code ? 'rgba(28,32,51,.4)' : T.amber, background: 'transparent', border: 'none', padding: '4px 2px' }}
                >
                  {busy === code ? 'Downloading\u2026' : 'Download'}
                </button>
              ) : (
                <span style={{ fontSize: 12, color: 'rgba(28,32,51,.45)' }}>In iPhone Settings</span>
              ))}
            </div>
          );
        })}
      </OfflinePanel>
      {onStart && (
        <button
          onClick={onStart}
          style={{
            width: '100%', height: 52, marginTop: -8, marginBottom: 18, background: '#fff',
            color: T.slate, border: '1px solid rgba(28,32,51,.14)', borderRadius: 18,
            fontSize: 15, fontWeight: 600, display: 'flex', alignItems: 'center',
            justifyContent: 'center', gap: 8,
          }}
        >
          <Icon name="mic" size={17} color={T.slate} />
          Start an offline session
        </button>
      )}
      {error && <div style={{ fontSize: 12, color: '#D93A5C', marginTop: -12, marginBottom: 16, paddingLeft: 2 }}>{error}</div>}
    </div>
  );
}

// Subscription state, and the only in-app route to changing it. Cancellation
// deliberately is not offered here: both stores require it to happen in the
// user's store account, and a button that only says so is worse than the row
// that shows what they have.
function SubscriptionSection({ tier, secondsLeft, onUpgrade }) {
  const pro = tier === 'pro';
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontFamily: T.mono, fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(28,32,51,.45)', fontWeight: 700, marginBottom: 9 }}>
        Subscription
      </div>
      <div style={{ background: T.surface, border: `1px solid ${T.hairline}`, borderRadius: 18, overflow: 'hidden' }}>
        <div style={{ padding: '15px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <div style={{ fontFamily: T.display, fontSize: 20, fontWeight: 700, letterSpacing: '-.02em', color: T.slate }}>
              {pro ? 'Voca Pro' : 'Free'}
            </div>
            {pro && (
              <div style={{ fontFamily: T.mono, fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', fontWeight: 700, color: secondsLeft > 0 ? T.tealDeep : T.error }}>
                {formatMinutes(secondsLeft)} left
              </div>
            )}
          </div>
          <div style={{ fontSize: 12.5, lineHeight: 1.5, color: T.inkMute, marginTop: 4, textWrap: 'pretty' }}>
            {pro
              ? `${PLAN_MINUTES} minutes of cloud translation each month, then on-device translation stays available.`
              : 'On-device translation, free and unlimited, for the language pairs your phone can do without a network.'}
          </div>
          {pro && (
            <div style={{ height: 6, borderRadius: 99, background: 'rgba(28,32,51,.08)', marginTop: 11, overflow: 'hidden' }}>
              <div style={{ height: '100%', borderRadius: 99, background: T.amber, width: `${Math.round(100 * Math.max(0, secondsLeft) / (PLAN_MINUTES * 60))}%` }} />
            </div>
          )}
        </div>
        <button
          onClick={onUpgrade}
          style={{ width: '100%', padding: '14px 16px', borderTop: '1px solid rgba(28,32,51,.06)', display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left' }}
        >
          <div style={{ flex: 1, fontSize: 14.5, fontWeight: 600, color: pro ? T.slate : T.amber }}>
            {pro ? 'Manage or restore purchase' : `Upgrade to Pro — ${PLAN_PRICE_FALLBACK}/mo`}
          </div>
          <Icon name="chevron" size={16} color="rgba(28,32,51,.3)" style={{ transform: 'rotate(180deg)' }} />
        </button>
      </div>
    </div>
  );
}

function SettingsScreen({ onStartOffline, tier = 'free', secondsLeft = 0, onUpgrade }) {
  const [values, setValues] = useState({ notes: true, save: true, autoplay: true, haptics: true, large: false });
  const groups = [
    ['Session', [['notes','Automated notes','Summarise each session when it ends'],['save','Save transcripts','Keep full text on this device'],['autoplay','Speak translations aloud','Play synthesised voice on the other half']]],
    ['Accessibility', [['haptics','Haptic confirmation','Buzz on press and release'],['large','Larger transcript text','Increase live text size by 20%']]],
  ];
  // Both stores require the privacy policy to be reachable from inside the app,
  // not only from the store listing.
  const links = [
    ['Privacy policy', 'How microphone audio and transcripts are handled', `${apiBase()}/privacy`],
  ];
  return <div style={{ flex: 1, overflowY: 'auto', background: T.cream }}><div style={{ padding: '62px 22px 14px' }}><h1 style={{ fontFamily: T.display, fontSize: 34, fontWeight: 700, letterSpacing: '-.02em', color: T.slate }}>Settings</h1></div><div style={{ padding: '6px 20px 96px' }}>{groups.map(([title, rows]) => <div key={title} style={{ marginBottom: 20 }}><div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(28,32,51,.45)', fontWeight: 700, marginBottom: 9 }}>{title}</div><div style={{ background: '#fff', border: '1px solid rgba(28,32,51,.08)', borderRadius: 18, overflow: 'hidden' }}>{rows.map(([key,label,hint], i) => <button key={key} onClick={() => setValues(v => ({...v, [key]: !v[key]}))} style={{ width: '100%', padding: '14px 15px', display: 'flex', alignItems: 'center', gap: 12, borderTop: i ? '1px solid rgba(28,32,51,.06)' : 'none', textAlign: 'left' }}><div style={{ flex: 1 }}><div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate }}>{label}</div><div style={{ fontSize: 11.5, color: 'rgba(28,32,51,.48)', marginTop: 2 }}>{hint}</div></div><div style={{ width: 44, height: 26, borderRadius: 99, background: values[key] ? T.amber : 'rgba(28,32,51,.16)', padding: 3, display: 'flex', justifyContent: values[key] ? 'flex-end' : 'flex-start' }}><div style={{ width: 20, height: 20, borderRadius: '50%', background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,.25)' }} /></div></button>)}</div></div>)}<SubscriptionSection tier={tier} secondsLeft={secondsLeft} onUpgrade={onUpgrade} /><OfflineTranslationSection onStart={onStartOffline} /><div style={{ marginBottom: 20 }}><div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(28,32,51,.45)', fontWeight: 700, marginBottom: 9 }}>Legal</div><div style={{ background: '#fff', border: '1px solid rgba(28,32,51,.08)', borderRadius: 18, overflow: 'hidden' }}>{links.map(([label, hint, href], i) => <a key={label} href={href} rel="noopener noreferrer" style={{ width: '100%', padding: '14px 15px', display: 'flex', alignItems: 'center', gap: 12, borderTop: i ? '1px solid rgba(28,32,51,.06)' : 'none', textAlign: 'left', textDecoration: 'none' }}><div style={{ flex: 1 }}><div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate }}>{label}</div><div style={{ fontSize: 11.5, color: 'rgba(28,32,51,.48)', marginTop: 2 }}>{hint}</div></div><Icon name="chevron-left" size={16} color="rgba(28,32,51,.3)" style={{ transform: 'rotate(180deg)' }} /></a>)}</div></div></div></div>;
}

// ─────────────────────────────────────────────
// ErrorScreen
// ─────────────────────────────────────────────

function ErrorScreen({ type, onRetry, onBack }) {
  const isMic = type === 'mic';

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '32px 24px',
      background: T.cream,
      animation: 'fade-in 0.3s ease-out',
    }}>
      <div style={{
        width: 80, height: 80,
        borderRadius: '50%',
        background: T.errorLight,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        marginBottom: 20,
      }}>
        <Icon name={isMic ? 'mic-off' : 'wifi-off'} size={36} color={T.error} />
      </div>

      <h2 style={{ fontFamily: T.display, fontSize: 28, fontWeight: 700, letterSpacing: '-.02em', marginBottom: 8, textAlign: 'center', color: T.navy, lineHeight: 1.1 }}>
        {isMic ? 'Microphone access required' : 'Connection failed'}
      </h2>
      <p style={{ fontSize: 13.5, color: T.inkMute, textAlign: 'center', lineHeight: 1.55, maxWidth: 280, marginBottom: 32 }}>
        {isMic
          ? 'Voca needs microphone access to translate speech. Allow it for Voca in your device settings, then try again.'
          : 'Unable to connect to the translation server. Please check your connection and try again.'}
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: '100%', maxWidth: 280 }}>
        <button
          onClick={onRetry}
          style={{
            height: 56,
            background: T.amber,
            color: '#fff',
            borderRadius: 18,
            fontSize: 16,
            fontWeight: 600,
            boxShadow: '0 12px 24px -8px rgba(108,92,231,.6)',
          }}
        >
          {'Try again'}
        </button>
        <button
          onClick={onBack}
          style={{
            height: 56,
            background: '#fff',
            color: T.navy,
            borderRadius: 18,
            fontSize: 16,
            fontWeight: 600,
            border: `1px solid ${T.hairline}`,
          }}
        >
          Go back
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// PaywallScreen
// ─────────────────────────────────────────────
//
// Reached three ways, and the copy changes for each because the user's
// situation is genuinely different:
//   'upsell'      — a free user who wants the cloud engine.
//   'unsupported' — a free user whose language pair has no on-device model.
//                   This is the honest upsell: the free tier cannot do it.
//   'exhausted'   — a subscriber who has spent this month's minutes.

function PaywallScreen({ reason = 'upsell', balance, langA, langB, onClose, onPurchased }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const heading = reason === 'exhausted'
    ? 'You are out of minutes'
    : reason === 'unsupported'
      ? 'This pair needs Voca Pro'
      : 'Voca Pro';

  const lede = reason === 'exhausted'
    ? `Your ${PLAN_MINUTES} minutes reset at the start of next month. Until then you can keep translating on-device, free.`
    : reason === 'unsupported'
      ? `${getLang(langA).label} to ${getLang(langB).label} has no on-device model, so it needs the cloud engine.`
      : 'Natural two-way conversation, translated live by a voice on each side.';

  async function run(kind, fn) {
    setBusy(kind);
    setError(null);
    try {
      const result = await fn();

      if (result.status === 'cancelled') return;
      if (result.status === 'pending') {
        setError('Your purchase is awaiting approval. Minutes appear as soon as it clears.');
        return;
      }
      if (result.status !== 'purchased') {
        setError(kind === 'restore'
          ? 'No previous subscription found on this account.'
          : 'The purchase did not complete.');
        return;
      }

      // The server is what decides the tier — it re-checks the receipt with
      // Apple or Google before granting a single minute.
      const fresh = await activateEntitlement({ receipt: result.receipt, reachable: true });
      if (fresh.tier !== 'pro') {
        setError('The store confirmed a purchase but it could not be verified. No charge has been kept — please contact support.');
        return;
      }
      // Only now is it safe to acknowledge on Play; an unacknowledged purchase
      // is auto-refunded, which is the correct outcome if we got this far and
      // could not verify.
      await acknowledgeIfNeeded(result.raw);
      onPurchased(fresh);
    } catch (err) {
      setError(
        String(err?.message) === 'in_app_purchase_unavailable'
          ? 'Purchases are only available in the App Store and Google Play builds.'
          : 'Something went wrong reaching the store. Please try again.'
      );
    } finally {
      setBusy(null);
    }
  }

  // Show the storefront's own price when the store can tell us; the Australian
  // price is only a fallback for the browser build.
  const [price, setPrice] = useState(PLAN_PRICE_FALLBACK);
  useEffect(() => {
    let active = true;
    storePrice().then(p => { if (active && p) setPrice(p); });
    return () => { active = false; };
  }, []);

  const perks = [
    ['mic', 'Two live voices', 'Each person hears the other in their own language, in the moment.'],
    ['globe', 'Every language pair', 'All eleven languages, including the pairs no phone can do on-device.'],
    ['clock', `${PLAN_MINUTES} minutes a month`, 'Minutes count real conversation, not time the app sits idle.'],
  ];

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflowY: 'auto', background: T.bg }}>
      <div style={{ padding: '54px 22px 0', flexShrink: 0 }}>
        <button onClick={onClose} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(28,32,51,.05)', color: T.slate }} aria-label="Close">
          <Icon name="chevron-left" size={17} />
        </button>
      </div>

      <div style={{ padding: '18px 22px 0' }}>
        <div style={{ fontFamily: T.mono, fontSize: 10, letterSpacing: '.16em', textTransform: 'uppercase', color: T.amber, fontWeight: 700 }}>
          {reason === 'exhausted' ? 'Monthly allowance' : 'Upgrade'}
        </div>
        <h1 style={{ fontFamily: T.display, fontSize: 33, fontWeight: 700, letterSpacing: '-.025em', color: T.slate, lineHeight: 1.05, marginTop: 8 }}>
          {heading}
        </h1>
        <p style={{ fontSize: 14, lineHeight: 1.55, color: T.inkMute, marginTop: 10, textWrap: 'pretty' }}>{lede}</p>
      </div>

      {balance && balance.tier === 'pro' && (
        <div style={{ margin: '18px 20px 0', background: T.surface, border: `1px solid ${T.hairline}`, borderRadius: 18, padding: '14px 16px' }}>
          <div style={{ fontFamily: T.mono, fontSize: 9.5, letterSpacing: '.14em', textTransform: 'uppercase', color: T.inkMute, fontWeight: 700 }}>Remaining this month</div>
          <div style={{ fontFamily: T.display, fontSize: 26, fontWeight: 700, letterSpacing: '-.02em', color: T.slate, marginTop: 4 }}>
            {formatMinutes(balance.seconds_remaining)}
          </div>
          <div style={{ height: 6, borderRadius: 99, background: 'rgba(28,32,51,.08)', marginTop: 10, overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 99, background: T.amber,
              width: `${balance.seconds_total ? Math.round(100 * balance.seconds_remaining / balance.seconds_total) : 0}%`,
            }} />
          </div>
        </div>
      )}

      <div style={{ padding: '18px 20px 0', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {perks.map(([icon, title, body]) => (
          <div key={title} style={{ display: 'flex', gap: 12, background: T.surface, border: `1px solid ${T.hairline}`, borderRadius: 18, padding: '14px 15px' }}>
            <div style={{ width: 38, height: 38, flexShrink: 0, borderRadius: 12, background: T.inkA08, color: T.amber, display: 'grid', placeItems: 'center' }}>
              <Icon name={icon} size={18} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate }}>{title}</div>
              <div style={{ fontSize: 12, lineHeight: 1.45, color: T.inkMute, marginTop: 2, textWrap: 'pretty' }}>{body}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ padding: '20px 20px 0' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'center', gap: 6 }}>
          <span style={{ fontFamily: T.display, fontSize: 34, fontWeight: 700, letterSpacing: '-.025em', color: T.slate }}>{price}</span>
          <span style={{ fontSize: 14, color: T.inkMute }}>/ month</span>
        </div>
        <p style={{ textAlign: 'center', fontSize: 11.5, color: T.textFaint, marginTop: 6 }}>
          Renews monthly. Cancel any time in your store account.
        </p>
      </div>

      <div style={{ padding: '16px 20px 0' }}>
        {error && (
          <div style={{ background: T.errorLight, color: T.error, borderRadius: 14, padding: '11px 13px', fontSize: 12.5, lineHeight: 1.45, marginBottom: 10 }}>
            {error}
          </div>
        )}
        <button
          onClick={() => run('purchase', purchasePro)}
          disabled={Boolean(busy)}
          style={{
            width: '100%', height: 62, background: T.amber, color: '#fff', border: 'none', borderRadius: 20,
            fontSize: 17, fontWeight: 600, opacity: busy ? .6 : 1,
            boxShadow: '0 12px 26px -8px rgba(108,92,231,.6)',
          }}
        >
          {busy === 'purchase' ? 'Contacting the store…' : `Subscribe — ${price}/mo`}
        </button>
        <button
          onClick={() => run('restore', restorePurchases)}
          disabled={Boolean(busy)}
          style={{ width: '100%', height: 46, marginTop: 8, background: 'none', border: 'none', color: T.amber, fontSize: 13.5, fontWeight: 600 }}
        >
          {busy === 'restore' ? 'Checking…' : 'Restore a previous purchase'}
        </button>
      </div>

      <div style={{ padding: '10px 24px 32px' }}>
        <button onClick={onClose} style={{ width: '100%', background: 'none', border: 'none', color: T.inkMute, fontSize: 13, fontWeight: 600, padding: '10px 0' }}>
          {reason === 'unsupported' ? 'Pick another language pair' : 'Keep translating on-device, free'}
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// App root
// ─────────────────────────────────────────────

function App() {
  const [screen, setScreen] = useState('home');
  const [tab, setTab] = useState('home');
  const [sessionConfig, setSessionConfig] = useState(null);
  const [errorType, setErrorType] = useState('mic');
  const [langA, setLangA] = useState('en');
  const [langB, setLangB] = useState('zh');
  const [langPickSide, setLangPickSide] = useState('a');
  const [detailSessionId, setDetailSessionId] = useState(null);
  const [pendingMicConfig, setPendingMicConfig] = useState(null);
  const [balance, setBalance] = useState(null);
  const [paywallReason, setPaywallReason] = useState('upsell');
  const [offlinePref, setOfflinePref] = useState(readOfflinePref);

  const tier = balance?.tier === 'pro' ? 'pro' : 'free';
  const secondsLeft = balance?.seconds_remaining ?? 0;
  // Before the store products exist the server meters usage but refuses nobody,
  // so a build with no purchases still reaches the cloud engine. The tier shown
  // in the UI stays honest — it just does not gate anything yet.
  const metered = balance?.enforced !== false;
  const cloudAllowed = tier === 'pro' || !metered;

  // Free users default to on-device: it is free to run, needs no connection,
  // and is the tier's actual product rather than a degraded version of the
  // paid one. Once the user touches the switch their choice wins for good.
  const offlineMode = offlinePref === null ? tier !== 'pro' : offlinePref;

  function setOfflineMode(value) {
    setOfflinePref(value);
    writeOfflinePref(value);
  }

  // Ask the store what the user actually owns, tell the server, then read the
  // balance back. The server's answer wins for minutes — the store only ever
  // decides the tier.
  const syncEntitlement = useCallback(async () => {
    try {
      const { receipt, reachable } = await currentReceipt();
      // No store on this build (a browser): just read the balance rather than
      // telling the server the user owns nothing.
      const fresh = (!reachable && !receipt)
        ? await fetchBalance()
        : await activateEntitlement({ receipt, reachable });
      setBalance(fresh);
      return fresh;
    } catch (error) {
      console.warn('Could not sync entitlement:', error);
      return null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    (async () => { if (active) await syncEntitlement(); })();
    // A renewal, lapse or refund can happen while the app is closed. Re-verify
    // on the plugin's signal rather than trusting what the event carries.
    const off = onEntitlementChanged(() => { syncEntitlement(); });
    return () => { active = false; off(); };
  }, [syncEntitlement]);

  // Ads are for free users, and never over a live conversation.
  useEffect(() => {
    const live = screen === 'live-face' || screen === 'live-auto' || screen === 'live-offline';
    if (tier === 'free' && metered && !live && balance) showFreeTierBanner();
    else hideFreeTierBanner();
  }, [tier, screen, balance]);

  function goTab(t) {
    setTab(t);
    if (t === 'home') setScreen('home');
    else if (t === 'live') setScreen('live-setup');
    else if (t === 'history') setScreen('history');
    else if (t === 'settings') setScreen('settings');
  }

  // One funnel for every route into a live session, so tier routing sits beside
  // the microphone disclosure rather than being repeated at each call site.
  //
  //   free → the on-device screen, when the pair has a model. Same gestures,
  //          no network, no cost. When it has no model, that is the one honest
  //          moment to ask for money.
  //   pro  → the cloud screen, unless this month's minutes are gone.
  function enterSession(cfg) {
    setSessionConfig(cfg);
    setLangA(cfg.langA);
    setLangB(cfg.langB);

    // The switch is the user's explicit instruction, so it is checked before
    // tier. If the pair has no on-device model we fall through rather than
    // failing — OfflineSwitch has already said so on the home screen.
    if (offlineMode && canTranslateOffline(cfg.langA, cfg.langB)) {
      return setScreen('live-offline');
    }

    if (!cloudAllowed) {
      if (canTranslateOffline(cfg.langA, cfg.langB)) return setScreen('live-offline');
      setPaywallReason('unsupported');
      return setScreen('paywall');
    }
    if (metered && secondsLeft <= 0) {
      setPaywallReason('exhausted');
      return setScreen('paywall');
    }
    setScreen('live-face');
  }

  // Every route into a live screen funnels through here — the home card and the
  // setup screen both call it — and the live screens request the microphone from
  // a mount effect. So this is the one place the disclosure has to sit if it is
  // to reliably precede the OS permission prompt.
  function handleStart(cfg) {
    if (hasMicConsent()) return enterSession(cfg);
    setPendingMicConfig(cfg);
  }

  function handleStop() {
    setSessionConfig(null);
    setScreen('history');
    setTab('history');
  }

  function handleError(type) {
    setErrorType(type);
    setScreen('error');
  }

  function handleRetry() {
    if (errorType === 'mic') {
      // Open browser settings hint — we can't programmatically open settings
      // Navigate back to setup so user can try again after granting permission
      setScreen('live-setup');
    } else {
      setScreen(sessionConfig ? 'live-setup' : 'home');
    }
  }

  const showTabBar = screen === 'home' || screen === 'history' || screen === 'settings';

  let content;
  switch (screen) {
    case 'home':
      content = (
        <WelcomeScreen
          langA={langA}
          langB={langB}
          onStart={() => {
            // Straight into the session with the home card's language pair;
            // the Translate tab still opens the full setup screen.
            setTab('live');
            handleStart({
              mode: 'face-to-face',
              micMode: 'hold',
              nameA: 'Person A',
              nameB: 'Person B',
              langA,
              langB,
            });
          }}
          onPickA={() => { setLangPickSide('a'); setScreen('lang-pick'); }}
          onPickB={() => { setLangPickSide('b'); setScreen('lang-pick'); }}
          onSwap={() => { setLangA(langB); setLangB(langA); }}
          tier={tier}
          secondsLeft={secondsLeft}
          offlineMode={offlineMode}
          onOfflineChange={setOfflineMode}
          onUpgrade={() => { setPaywallReason(tier === 'pro' && secondsLeft <= 0 ? 'exhausted' : 'upsell'); setScreen('paywall'); }}
        />
      );
      break;

    case 'lang-pick':
      content = (
        <LangScreen
          targetName={langPickSide === 'a' ? 'Person A' : 'Person B'}
          variant={langPickSide === 'a' ? 'amber' : 'teal'}
          selected={langPickSide === 'a' ? langA : langB}
          onBack={() => setScreen('home')}
          onSelect={code => { if (langPickSide === 'a') setLangA(code); else setLangB(code); setScreen('home'); }}
        />
      );
      break;

    case 'live-setup':
      content = (
        <SessionSetupScreen
          langA={langA}
          langB={langB}
          onBack={() => { setTab('home'); setScreen('home'); }}
          onStart={handleStart}
          onLangPick={() => {}}
        />
      );
      break;

    case 'live-face':
      content = (
        <FaceToFaceLiveScreen
          config={sessionConfig}
          budgetSeconds={metered ? secondsLeft : Infinity}
          onUsage={(sessionId, seconds) => reportUsage(sessionId, seconds).then(b => b && setBalance(b))}
          onExhausted={() => { setPaywallReason('exhausted'); setScreen('paywall'); }}
          onStop={handleStop}
          onError={handleError}
        />
      );
      break;

    case 'history':
      content = (
        <HistoryScreen
          onBack={() => { setTab('home'); setScreen('home'); }}
          onSession={id => { setDetailSessionId(id); setScreen('detail'); }}
        />
      );
      break;

    case 'detail':
      content = <SessionDetailScreen sessionId={detailSessionId} onBack={() => setScreen('history')} />;
      break;

    case 'settings':
      content = <SettingsScreen onStartOffline={() => setScreen('live-offline')} tier={tier} secondsLeft={secondsLeft} onUpgrade={() => { setPaywallReason(tier === 'pro' && secondsLeft <= 0 ? 'exhausted' : 'upsell'); setScreen('paywall'); }} />;
      break;

    case 'live-offline':
      // Two ways in: chosen from Settings, or routed here for a free-tier user
      // by enterSession(). Either way it is the on-device engine end to end —
      // it opens no peer connection, so it cannot disturb a server session.
      content = (
        <OfflineLiveScreen
          langA={langA}
          langB={langB}
          onBack={() => { setTab('home'); setScreen('home'); }}
        />
      );
      break;

    case 'paywall':
      content = (
        <PaywallScreen
          reason={paywallReason}
          balance={balance}
          langA={langA}
          langB={langB}
          onClose={() => { setTab('home'); setScreen('home'); }}
          onPurchased={fresh => {
            setBalance(fresh);
            hideFreeTierBanner();
            // Straight into the session they were trying to start.
            if (sessionConfig && (fresh?.seconds_remaining ?? 0) > 0) setScreen('live-face');
            else { setTab('home'); setScreen('home'); }
          }}
        />
      );
      break;

    case 'error':
      content = (
        <ErrorScreen
          type={errorType}
          onRetry={handleRetry}
          onBack={() => setScreen('home')}
        />
      );
      break;

    default:
      content = null;
  }

  return (
    <div style={{
      position: 'relative',
      height: '100dvh',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      background: T.bg,
    }}>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {content}
      </div>
      {showTabBar && (
        <TabBar active={tab} onChange={goTab} />
      )}
      {pendingMicConfig && (
        <MicDisclosure
          onAccept={() => {
            const cfg = pendingMicConfig;
            setPendingMicConfig(null);
            enterSession(cfg);
          }}
          onCancel={() => setPendingMicConfig(null)}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// Mount
// ─────────────────────────────────────────────

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(React.createElement(App));
