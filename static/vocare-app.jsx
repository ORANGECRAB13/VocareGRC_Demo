// Vocare Mobile Translation App
// Real-time two-way voice translation via WebRTC

const { useState, useEffect, useRef, useCallback, useMemo } = React;

// ─────────────────────────────────────────────
// Design tokens (mirrors CSS vars in vocare.html)
// ─────────────────────────────────────────────

const T = {
  teal:        '#58BFB4',
  tealLight:   '#DDF4F1',
  tealMid:     '#8FD5CE',
  amber:       '#F47C36',
  amberLight:  '#FCE4D5',
  bg:          '#FBF3EB',
  surface:     '#FFFFFF',
  surface2:    '#E5EEF2',
  textPrimary: '#3E4C5E',
  textMuted:   '#71808F',
  textFaint:   '#A6B1BA',
  border:      '#D3DEE4',
  error:       '#C52E42',
  errorLight:  '#F8E1E5',
  success:     '#409B76',
  slate:       '#3E4C5E',
  cream:       '#FBF3EB',

  // ── Voca design canvas tokens ──
  navy:        '#3E4C5E',  // brand navy / slate
  gold:        '#FFD37E',  // hero italic + live indicator dot
  tealDeep:    '#2E8E86',  // Person B ink on light surfaces
  tealSoft:    '#7FD3C9',  // Person B ink on navy
  tealPale:    '#9BDCD4',  // Person B ink, brightest (recording)
  amberSoft:   '#FBB07A',  // Person A ink on navy
  amberDeep:   '#D9631F',  // Person A pressed
  panelIdle:   '#48586D',  // live panel resting background
  panelA:      '#B05A24',  // live panel while A records
  panelB:      '#2C7B73',  // live panel while B records

  // Alpha washes lifted verbatim from the canvas
  inkA08:      'rgba(244,124,54,.08)',
  inkA14:      'rgba(244,124,54,.14)',
  inkB09:      'rgba(88,191,180,.09)',
  inkB14:      'rgba(88,191,180,.14)',
  tileA:       'rgba(251,176,122,.18)',
  tileB:       'rgba(88,191,180,.16)',
  hairline:    'rgba(62,76,94,.08)',
  inkMute:     'rgba(62,76,94,.45)',
  creamMute:   'rgba(251,243,235,.45)',
  creamFaint:  'rgba(251,243,235,.4)',
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

// Can this engine fetch its own models, or must the user go to system settings?
function offlineCanSelfDownload() {
  return offlineEngine()?.kind === 'mlkit';
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
// explicit user action. Only meaningful on ML Kit.
async function downloadOfflineModels(codes) {
  const engine = offlineEngine();
  if (engine?.kind !== 'mlkit') {
    throw new Error('This device installs translation languages through system settings.');
  }
  for (const code of codes) {
    await engine.plugin.downloadModel({ language: mlkitCode(code) });
  }
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
      paddingTop: 11, background: 'rgba(251,243,235,.92)', backdropFilter: 'blur(18px)',
      borderTop: '1px solid rgba(62,76,94,.08)', paddingBottom: 'env(safe-area-inset-bottom, 0px)', flexShrink: 0,
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
              padding: 0, gap: 5, color: isActive ? T.amber : 'rgba(62,76,94,.38)',
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
// WelcomeScreen
// ─────────────────────────────────────────────

function WelcomeScreen({ langA, langB, onStart, onPickA, onPickB, onSwap, onHistory, onSession }) {
  const [sessions, setSessions] = useState([]);
  const langAInfo = getLang(langA);
  const langBInfo = getLang(langB);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const history = await loadSessionHistory();
        if (active) setSessions(history);
      } catch {}
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { active = false; clearInterval(id); };
  }, []);

  const topSessions = sessions.slice(0, 3);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflowY: 'auto', background: T.cream }}>
      <div style={{
        background: T.slate,
        padding: '58px 26px 22px',
        borderRadius: '0 0 34px 34px',
        color: T.cream,
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 30, height: 30, background: T.cream, color: T.slate, borderRadius: 9,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0, fontFamily: "'Instrument Serif', serif", fontSize: 19,
          }}>
            A
          </div>
          <h1 style={{ color: T.cream, fontSize: 14, fontWeight: 600, letterSpacing: '.16em', textTransform: 'uppercase' }}>Voca</h1>
        </div>

        <p style={{ fontFamily: "'Instrument Serif', serif", fontSize: 44, fontWeight: 400, letterSpacing: '-.015em', lineHeight: .98, margin: '26px 0 0' }}>
          Speak freely.<br /><span style={{ fontStyle: 'italic', color: '#FFD37E' }}>Understand</span> instantly.
        </p>
        <p style={{ fontSize: 13.5, color: 'rgba(251,243,235,.6)', lineHeight: 1.5, marginTop: 12, maxWidth: 230 }}>
          One phone between two people.
        </p>
      </div>

      <div style={{ padding: '16px 20px 0' }}>
        <div style={{ display: 'flex', alignItems: 'stretch', gap: 8, background: '#fff', border: '1px solid rgba(62,76,94,.09)', borderRadius: 22, padding: 8 }}>
          <button onClick={onPickA} style={{ flex: 1, minWidth: 0, padding: '12px 14px', borderRadius: 15, background: 'rgba(244,124,54,.07)', textAlign: 'left' }}>
            <div style={{ fontSize: 9.5, letterSpacing: '.14em', textTransform: 'uppercase', color: T.amber, fontWeight: 700 }}>Person A</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: T.slate, marginTop: 5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{langAInfo.label}</div>
          </button>
          <button onClick={onSwap} aria-label="Swap languages" style={{ width: 38, display: 'grid', placeItems: 'center', color: 'rgba(62,76,94,.4)', borderRadius: 12 }}><Icon name="swap" size={18} /></button>
          <button onClick={onPickB} style={{ flex: 1, minWidth: 0, padding: '12px 14px', borderRadius: 15, background: T.inkB09, textAlign: 'right' }}>
            <div style={{ fontSize: 9.5, letterSpacing: '.14em', textTransform: 'uppercase', color: T.tealDeep, fontWeight: 700 }}>Person B</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: T.slate, marginTop: 5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{langBInfo.label}</div>
          </button>
        </div>
        <button
          onClick={onStart}
          style={{
            width: '100%', height: 62, marginTop: 12, background: T.amber, color: '#fff', border: 'none', borderRadius: 20,
            fontSize: 17, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 11,
            boxShadow: '0 12px 24px -8px rgba(244,124,54,.6)',
          }}
        >
          <Icon name="mic" size={19} color="#fff" /> Start a session
        </button>
      </div>

      <div style={{ padding: '18px 20px 0' }}>
        {topSessions.length > 0 && (
          <>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 9 }}>
              <h2 style={{ fontSize: 10.5, fontWeight: 700, color: T.inkMute, textTransform: 'uppercase', letterSpacing: '.16em' }}>
                Recent sessions
              </h2>
              <button onClick={onHistory} style={{ fontSize: 12, color: T.amber, fontWeight: 600 }}>All</button>
            </div>
            {topSessions.map(s => (
              <SessionCard key={s.session_id} session={s} onClick={() => onSession(s.session_id)} />
            ))}
          </>
        )}
      </div>

      {/* Canvas footer hint — sits at the base of the scroll flow */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', padding: '24px 34px 24px', minHeight: 96 }}>
        <p style={{ fontSize: 12.5, lineHeight: 1.6, color: T.inkMute, textAlign: 'center' }}>
          Lay the phone flat between you. Hold your half to talk; release and the other half hears it translated.
        </p>
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
        border: '1px solid rgba(62,76,94,.08)', borderRadius: 18, marginBottom: 8, cursor: 'pointer', textAlign: 'left',
      }}
    >
      <div style={{
        width: 38, height: 38, background: isLive ? 'rgba(88,191,180,.12)' : 'rgba(244,124,54,.08)',
        color: isLive ? '#2E8E86' : T.amber, borderRadius: 12, display: 'grid', placeItems: 'center', flexShrink: 0,
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
            <span style={{ fontSize: 10, fontWeight: 700, color: '#2E8E86', background: 'rgba(88,191,180,.12)', padding: '4px 8px', borderRadius: 99, letterSpacing: '.1em' }}>
              Live
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11.5, color: 'rgba(62,76,94,.5)' }}>{session.caller_name}</span>
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
      background: 'rgba(62,76,94,.55)', animation: 'fade-in .2s ease-out',
    }}>
      <div style={{
        width: '100%', background: T.cream, borderRadius: '26px 26px 0 0', padding: '24px 22px 28px',
        boxShadow: '0 -18px 40px -12px rgba(62,76,94,.4)',
      }}>
        <div style={{ width: 46, height: 46, borderRadius: 15, display: 'grid', placeItems: 'center', background: T.slate }}>
          <Icon name="mic" size={21} color={T.cream} />
        </div>
        <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 25, fontWeight: 400, color: T.slate, marginTop: 14 }}>
          Before we turn on the microphone
        </h2>
        <div style={{ fontSize: 14, lineHeight: 1.6, color: 'rgba(62,76,94,.78)', marginTop: 12 }}>
          To translate your conversation, Vocare records audio while you hold the speak
          button and sends it to our servers, where it is transcribed and translated.
        </div>
        <ul style={{ listStyle: 'none', marginTop: 14, display: 'flex', flexDirection: 'column', gap: 9 }}>
          {[
            'Audio is captured only while a speak button is held down — never in the background.',
            'Transcripts of the conversation are saved on this device so you can read them later.',
            'There are no accounts, and nothing is used for advertising or tracking.',
          ].map(line => (
            <li key={line} style={{ display: 'flex', gap: 9, fontSize: 13, lineHeight: 1.5, color: 'rgba(62,76,94,.7)' }}>
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
            width: '100%', height: 48, marginTop: 8, background: 'transparent', color: 'rgba(62,76,94,.6)',
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
        <button onClick={onBack} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(62,76,94,.05)', color: T.slate }}><Icon name="chevron-left" size={17} /></button>
        <h1 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 26, fontWeight: 400, color: T.slate }}>Session setup</h1>
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

        <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(62,76,94,.45)', fontWeight: 700, margin: '22px 0 9px' }}>Microphone</div>
        <div style={{ borderRadius: 18, padding: 15, background: T.slate, border: `1px solid ${T.slate}`, color: T.cream }}>
          <Icon name="mic" size={19} color={T.cream} />
          <div style={{ fontSize: 14.5, fontWeight: 600, marginTop: 8 }}>Hold to speak</div>
        </div>

        <div style={{ marginTop: 22, background: T.slate, borderRadius: 22, padding: '18px 18px 20px', color: T.cream }}>
          <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(251,243,235,.5)', fontWeight: 700 }}>How it works</div>
          <div style={{ fontSize: 13.5, lineHeight: 1.55, color: 'rgba(251,243,235,.78)', marginTop: 10 }}>Lay the phone flat between you. Hold your half to talk; release and the other half hears it translated.</div>
        </div>

        <button
          onClick={handleStart}
          style={{
            width: '100%', height: 62, marginTop: 18, background: T.amber, color: '#fff', border: 'none', borderRadius: 20,
            fontSize: 17, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
            boxShadow: '0 12px 24px -8px rgba(244,124,54,.55)',
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
      <h3 style={{ fontSize: 10.5, fontWeight: 700, color: 'rgba(62,76,94,.45)', textTransform: 'uppercase', letterSpacing: '.16em', margin: '6px 0 9px' }}>
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
  const ink = variant === 'teal' ? '#2E8E86' : T.amber;
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
        <input value={name} onChange={e => onNameChange(e.target.value)} disabled={dimmed} style={{ width: '100%', marginTop: 3, padding: 0, fontSize: 16, fontWeight: 600, color: T.slate, background: 'none', border: 'none', outline: 'none', fontFamily: "'Space Grotesk', sans-serif" }} />
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
  const textColor = T.cream;
  const subColor = T.creamMute;
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
  const tagColor = mine ? T.creamFaint : (side === 'A' ? T.amberSoft : T.tealPale);
  const bg = mine ? 'rgba(251,243,235,.08)' : (side === 'A' ? 'rgba(244,124,54,.9)' : 'rgba(88,191,180,.9)');
  const fg = mine ? 'rgba(251,243,235,.82)' : '#fff';
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
        <div style={{ fontSize: 11.5, color: T.creamFaint, marginTop: 5, fontStyle: 'italic', ...(isZh(sub) ? zhFont : {}) }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// FaceToFaceLiveScreen — real WebRTC, PTT
// ─────────────────────────────────────────────

function FaceToFaceLiveScreen({ config, onStop, onError }) {
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

  function startTimer() {
    if (timerRef.current) return;
    timerRef.current = setInterval(() => {
      if (mountedRef.current) setElapsed(e => {
        elapsedRef.current = e + 1;
        return elapsedRef.current;
      });
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
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.navy, overflow: 'hidden', userSelect: 'none', WebkitUserSelect: 'none', WebkitTouchCallout: 'none' }}>
      {/* Hidden audio elements */}
      <audio ref={audioRefA} autoPlay playsInline style={{ display: 'none' }} />
      <audio ref={audioRefB} autoPlay playsInline style={{ display: 'none' }} />

      {/* Person B panel — top half, rotated 180° */}
      <div style={{
        flex: 1,
        minHeight: 0,
        transform: 'rotate(180deg)',
        background: pressB ? T.panelB : T.panelIdle,
        display: 'flex',
        flexDirection: 'column',
        transition: 'background 0.3s',
        position: 'relative',
      }}>
        {/* Person info */}
        <div style={{ padding: '14px 18px 6px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <PersonNameTag langInfo={langBInfo} name={nameB} tint={T.tileB} ink={T.tealSoft} pressing={pressB} />
          <div style={{ marginLeft: 'auto', fontSize: 10.5, letterSpacing: '.1em', textTransform: 'uppercase', fontWeight: 700, color: pressB ? T.tealPale : T.creamFaint }}>{pressB ? 'Recording' : turnStateB === 'translating' ? 'Translating...' : pressA ? `Listening to ${nameA}` : phase === 'connected' ? hasSpokenB ? 'Ready - speak again' : 'Ready' : connState}</div>
        </div>

        {/* Transcript — newest nearest the mic */}
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column-reverse', gap: 10, padding: '6px 18px 4px' }}>
          {allTurns.slice().reverse().map((t, i) => (
            <LiveTurn key={allTurns.length - 1 - i} turn={t} side="B" mine={!spokenByA(t)} />
          ))}
          {phase === 'connecting' && !lastTurnB && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, alignSelf: 'flex-start' }}>
              <TypingDots color={T.creamFaint} />
              <span style={{ fontSize: 13, color: T.creamMute }}>{connState}</span>
            </div>
          )}
        </div>

        {/* Hold to speak */}
        <div style={{ height: 150, flex: 'none', display: 'grid', placeItems: 'center' }}>
          <div style={{ position: 'relative', width: 104, height: 104, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {pressB && <PulseRing color={T.teal} size={104} />}
            <button
              data-ptt="true"
              onPointerDown={pressBStart}
              onPointerUp={pressBEnd}
              onPointerCancel={pressBEnd}
              onContextMenu={e => e.preventDefault()}
              disabled={phase !== 'connected' || turnStateB === 'translating' || pressA}
              aria-label={turnStateB === 'translating' ? `${nameB} translation in progress` : `${nameB} hold to speak`}
              style={{
                width: 104, height: 104,
                borderRadius: '50%',
                background: pressB ? T.teal : T.inkB14,
                border: `2px solid ${pressB ? '#CDEDE8' : 'rgba(127,211,201,.4)'}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'transform .12s, background .2s, border-color .2s',
                transform: pressB ? 'scale(1.12)' : 'scale(1)',
                cursor: phase === 'connected' && turnStateB === 'ready' && !pressA ? 'pointer' : 'default',
                opacity: turnStateB === 'translating' ? 0.58 : 1,
                touchAction: 'none',
                WebkitTouchCallout: 'none',
                WebkitUserSelect: 'none',
                userSelect: 'none',
                position: 'relative',
                zIndex: 1,
              }}
            >
              <Icon name="mic" size={30} color={pressB ? '#fff' : T.tealPale} />
            </button>
          </div>
        </div>
      </div>

      {/* Center divider — 44px, white bg */}
      <div style={{
        height: 56, background: T.navy,
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        gap: 12,
        flexShrink: 0,
        borderTop: '1px solid rgba(251,243,235,.1)', borderBottom: '1px solid rgba(251,243,235,.1)',
      }}>
        {/* Language pair */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: T.tealSoft }}>{langBInfo.code.toUpperCase()}</span>
          <Icon name="swap" size={15} color="rgba(251,243,235,.35)" />
          <span style={{ fontSize: 11, fontWeight: 700, color: T.amberSoft }}>{langAInfo.code.toUpperCase()}</span>
        </div>

        {/* Elapsed time — the only thing the centre carries */}
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: T.gold, animation: 'dot-blink 1.6s ease-in-out infinite' }} />
          <span style={{ fontSize: 12.5, color: 'rgba(251,243,235,.75)', fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}>{elapsedStr}</span>
        </div>

        <button
          onClick={async () => { await persistSession('ended'); cleanup(); onStop(); }}
          style={{
            height: 34, padding: '0 14px', borderRadius: 12, background: 'rgba(88,191,180,.15)', color: T.tealSoft,
            border: 'none',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
          }}
        >
          <span style={{ width: 9, height: 9, background: 'currentColor', borderRadius: 2, display: 'block' }} />
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>End</span>
        </button>
      </div>

      {/* Person A panel — bottom half */}
      <div style={{
        flex: 1,
        minHeight: 0,
        background: pressA ? T.panelA : T.panelIdle,
        display: 'flex',
        flexDirection: 'column',
        transition: 'background 0.3s',
        position: 'relative',
      }}>
        {/* Person info */}
        <div style={{ padding: '14px 18px 6px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <PersonNameTag langInfo={langAInfo} name={nameA} tint={T.tileA} ink={T.amberSoft} pressing={pressA} />
          <div style={{ marginLeft: 'auto', fontSize: 10.5, letterSpacing: '.1em', textTransform: 'uppercase', fontWeight: 700, color: pressA ? T.amberSoft : T.creamFaint }}>{pressA ? 'Recording' : turnStateA === 'translating' ? 'Translating...' : pressB ? `Listening to ${nameB}` : phase === 'connected' ? hasSpokenA ? 'Ready - speak again' : 'Ready' : connState}</div>
        </div>

        {/* Transcript — newest nearest the mic */}
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column-reverse', gap: 10, padding: '6px 18px 4px' }}>
          {allTurns.slice().reverse().map((t, i) => (
            <LiveTurn key={allTurns.length - 1 - i} turn={t} side="A" mine={spokenByA(t)} />
          ))}
          {phase === 'connecting' && !lastTurnA && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, alignSelf: 'flex-start' }}>
              <TypingDots color={T.creamFaint} />
              <span style={{ fontSize: 13, color: T.creamMute }}>{connState}</span>
            </div>
          )}
        </div>

        {/* Hold to speak */}
        <div style={{ height: 150, flex: 'none', display: 'grid', placeItems: 'center', paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
          <div style={{ position: 'relative', width: 104, height: 104, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {pressA && <PulseRing color={T.amber} size={104} />}
            <button
              data-ptt="true"
              onPointerDown={pressAStart}
              onPointerUp={pressAEnd}
              onPointerCancel={pressAEnd}
              onContextMenu={e => e.preventDefault()}
              disabled={phase !== 'connected' || turnStateA === 'translating' || pressB}
              aria-label={turnStateA === 'translating' ? `${nameA} translation in progress` : `${nameA} hold to speak`}
              style={{
                width: 104, height: 104,
                borderRadius: '50%',
                background: pressA ? T.amber : T.inkA14,
                border: `2px solid ${pressA ? T.amberSoft : 'rgba(251,176,122,.4)'}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'transform .12s, background .2s, border-color .2s',
                transform: pressA ? 'scale(1.12)' : 'scale(1)',
                cursor: phase === 'connected' && turnStateA === 'ready' && !pressB ? 'pointer' : 'default',
                opacity: turnStateA === 'translating' ? 0.58 : 1,
                touchAction: 'none',
                WebkitTouchCallout: 'none',
                WebkitUserSelect: 'none',
                userSelect: 'none',
                position: 'relative',
                zIndex: 1,
              }}
            >
              <Icon name="mic" size={30} color={pressA ? '#fff' : T.amberSoft} />
            </button>
          </div>
        </div>
      </div>
    </div>
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
  const accent = variant === 'teal' ? '#2E8E86' : T.amber;
  const tint = variant === 'teal' ? 'rgba(88,191,180,.1)' : 'rgba(244,124,54,.08)';

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
        padding: '60px 20px 14px', background: '#fff', borderBottom: '1px solid rgba(62,76,94,.08)',
        flexShrink: 0,
      }}>
        <button onClick={onBack} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(62,76,94,.05)', color: T.slate }}>
          <Icon name="chevron-left" size={17} />
        </button>
        <div><div style={{ fontSize: 9.5, letterSpacing: '.14em', textTransform: 'uppercase', fontWeight: 700, color: accent }}>{targetName}</div><h1 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 24, fontWeight: 400, color: T.slate, lineHeight: 1.1 }}>Choose language</h1></div>
      </div>

      {/* Search */}
      <div style={{ padding: '0 20px 14px', background: '#fff', flexShrink: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '11px 13px', background: 'rgba(62,76,94,.05)', borderRadius: 14,
        }}>
          <Icon name="search" size={16} color="rgba(62,76,94,.4)" />
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


function OfflineHalf({ side, code, variant, active, disabled, onPress, onRelease }) {
  return (
    <button
      onPointerDown={() => onPress(side)}
      onPointerUp={() => onRelease(side)}
      onPointerLeave={() => active && onRelease(side)}
      disabled={disabled}
      style={{
        flex: 1, borderRadius: 22, border: 'none', padding: 18,
        background: active ? (variant === 'amber' ? T.amber : '#2E8E86') : '#fff',
        color: active ? '#fff' : T.slate,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8,
        boxShadow: active ? '0 12px 24px -8px rgba(62,76,94,.35)' : 'none',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <Icon name="mic" size={26} color={active ? '#fff' : T.slate} />
      <div style={{ fontSize: 15, fontWeight: 600 }}>{getLang(code).label}</div>
      <div style={{ fontSize: 11.5, opacity: 0.7 }}>{active ? 'Release to translate' : 'Hold to speak'}</div>
    </button>
  );
}

function OfflineLiveScreen({ langA, langB, onBack }) {
  const [turns, setTurns] = useState([]);
  const [holding, setHolding] = useState(null);   // 'a' | 'b' | null
  const [phase, setPhase] = useState('idle');     // idle | listening | translating | speaking
  const [error, setError] = useState(null);
  const [partial, setPartial] = useState('');
  const [picking, setPicking] = useState(null);       // 'a' | 'b' | null
  const [a, setA] = useState(langA);
  const [b, setB] = useState(langB);
  const [sttLocales, setSttLocales] = useState(null); // locales the recognizer knows
  const listenerRef = useRef(null);
  const startRef = useRef(null);

  const SR = window.Capacitor?.Plugins?.SpeechRecognition;
  const TTS = window.Capacitor?.Plugins?.TextToSpeech;

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
  const statusText = {
    idle: 'Hold a button to speak',
    listening: 'Listening…',
    translating: 'Translating on device…',
    speaking: 'Speaking…',
  }[phase];

  // NOTE: Half, Panel and Note live at module scope on purpose. A component
  // declared inside a render is a NEW component type each time, so React
  // unmounts and recreates its DOM. Mid-press that destroys the very node
  // holding the pointer, and the pointerup never reaches the handler — a
  // hold-to-speak button that sometimes never releases.


  // Only languages with an on-device translation model are offered — picking one
  // without a model would produce a button that silently cannot work.
  if (picking) {
    const options = offlineTranslationLanguages();
    const current = picking === 'a' ? a : b;
    const other = picking === 'a' ? b : a;
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.cream }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '60px 22px 14px' }}>
          <button onClick={() => setPicking(null)} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(62,76,94,.05)', color: T.slate }}>
            <Icon name="chevron-left" size={17} />
          </button>
          <h1 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 24, fontWeight: 400, color: T.slate }}>
            {picking === 'a' ? 'This side speaks' : 'Far side speaks'}
          </h1>
        </div>
        <div style={{ padding: '0 20px 10px', fontSize: 12.5, color: 'rgba(62,76,94,.55)' }}>
          Only languages that can translate on this device are shown. Cantonese has no offline model
          on any phone.
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 20px 28px' }}>
          <div style={{ background: '#fff', border: '1px solid rgba(62,76,94,.08)', borderRadius: 18, overflow: 'hidden' }}>
            {options.map((code, i) => {
              const taken = code === other;
              const locale = speechLocale(code).toLowerCase();
              const sttKnown = sttLocales === null
                ? null
                : sttLocales.some(l => l === locale || l.startsWith(code + '-') || l === code);
              return (
                <button
                  key={code}
                  disabled={taken}
                  onClick={() => { picking === 'a' ? setA(code) : setB(code); setPicking(null); setError(null); }}
                  style={{ width: '100%', padding: '13px 15px', display: 'flex', alignItems: 'center', gap: 12, borderTop: i ? '1px solid rgba(62,76,94,.06)' : 'none', textAlign: 'left', opacity: taken ? 0.4 : 1, background: code === current ? 'rgba(244,124,54,.06)' : '#fff' }}
                >
                  <span style={{ fontSize: 17 }}>{getLang(code).flag}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate }}>{getLang(code).label}</div>
                    {sttKnown === false && (
                      <div style={{ fontSize: 11, color: 'rgba(62,76,94,.45)', marginTop: 2 }}>
                        Dictation for this language may need installing
                      </div>
                    )}
                  </div>
                  {code === current && <Icon name="check" size={16} color={T.amber} />}
                  {taken && <span style={{ fontSize: 11.5, color: 'rgba(62,76,94,.45)' }}>Other side</span>}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.cream }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '60px 22px 10px' }}>
        <button onClick={onBack} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(62,76,94,.05)', color: T.slate }}>
          <Icon name="chevron-left" size={17} />
        </button>
        <div style={{ flex: 1 }}>
          <h1 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 24, fontWeight: 400, color: T.slate }}>Offline session</h1>
        </div>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.1em', textTransform: 'uppercase', color: '#2E8E86', background: '#DDF4F1', borderRadius: 99, padding: '5px 9px' }}>On device</span>
      </div>

      <div style={{ padding: '0 20px 6px', fontSize: 12.5, color: 'rgba(62,76,94,.55)' }}>
        Nothing is sent to a server. Speech, translation and the spoken reply all happen on this phone.
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 20px 4px' }}>
        <button onClick={() => setPicking('a')} style={{ flex: 1, padding: '10px 12px', borderRadius: 14, background: '#fff', border: '1px solid rgba(62,76,94,.12)', textAlign: 'left' }}>
          <div style={{ fontSize: 9.5, letterSpacing: '.14em', textTransform: 'uppercase', color: T.amber, fontWeight: 700 }}>This side</div>
          <div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate, marginTop: 2 }}>{getLang(a).label}</div>
        </button>
        <button
          onClick={() => { setA(b); setB(a); }}
          style={{ width: 38, height: 38, borderRadius: 12, background: 'rgba(62,76,94,.05)', border: 'none', color: T.slate, fontSize: 15 }}
          aria-label="Swap languages"
        >⇄</button>
        <button onClick={() => setPicking('b')} style={{ flex: 1, padding: '10px 12px', borderRadius: 14, background: '#fff', border: '1px solid rgba(62,76,94,.12)', textAlign: 'right' }}>
          <div style={{ fontSize: 9.5, letterSpacing: '.14em', textTransform: 'uppercase', color: '#2E8E86', fontWeight: 700 }}>Far side</div>
          <div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate, marginTop: 2 }}>{getLang(b).label}</div>
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 20px' }}>
        {turns.length === 0 && !partial && (
          <div style={{ fontSize: 13.5, color: 'rgba(62,76,94,.45)', textAlign: 'center', marginTop: 28 }}>
            Hold either side and speak. The other side hears it translated.
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} style={{ marginBottom: 14, alignSelf: 'flex-start' }}>
            <div style={{ fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', fontWeight: 700, color: t.side === 'a' ? T.amber : '#2E8E86', marginBottom: 4 }}>
              {getLang(t.from).label} → {getLang(t.to).label}
            </div>
            <div style={{ background: '#fff', border: '1px solid rgba(62,76,94,.1)', borderRadius: 16, padding: '11px 13px' }}>
              <div style={{ fontSize: 14.5, lineHeight: 1.45, color: T.slate }}>{t.translated}</div>
              <div style={{ fontSize: 11.5, color: 'rgba(62,76,94,.45)', marginTop: 5, fontStyle: 'italic' }}>{t.original}</div>
            </div>
          </div>
        ))}
        {partial && (
          <div style={{ fontSize: 13.5, color: 'rgba(62,76,94,.5)', fontStyle: 'italic', marginTop: 6 }}>{partial}</div>
        )}
      </div>

      {error && (
        <div style={{ margin: '0 20px 8px', padding: '10px 12px', background: '#FCE4D5', borderLeft: `3px solid ${T.amber}`, borderRadius: '0 8px 8px 0', fontSize: 12.5, lineHeight: 1.45, color: T.slate }}>
          {error}
        </div>
      )}

      <div style={{ padding: '4px 20px 8px', textAlign: 'center', fontSize: 12, fontWeight: 600, color: 'rgba(62,76,94,.5)' }}>{statusText}</div>
      <div style={{ display: 'flex', gap: 12, padding: '0 20px 28px' }}>
        <OfflineHalf side="a" code={a} variant="amber" active={holding === 'a'}
          disabled={busy && holding !== 'a'} onPress={press} onRelease={release} />
        <OfflineHalf side="b" code={b} variant="teal" active={holding === 'b'}
          disabled={busy && holding !== 'b'} onPress={press} onRelease={release} />
      </div>
    </div>
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
        <h1 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 34, fontWeight: 400, color: T.slate, lineHeight: 1.05 }}>History</h1>
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
                <p style={{ fontSize: 10.5, fontWeight: 700, color: '#2E8E86', textTransform: 'uppercase', letterSpacing: '.16em', margin: '6px 0 9px' }}>In progress</p>
                {live.map(s => <div key={s.session_id} style={{ background: T.slate, borderRadius: 20, padding: 16, marginBottom: 8 }}><button onClick={() => onSession(s.session_id)} style={{ width: '100%', color: T.cream, textAlign: 'left' }}><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: T.teal, animation: 'dot-blink 1.6s ease-in-out infinite' }} /><span style={{ fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', fontWeight: 700, color: T.tealSoft }}>Live · {formatDuration(s.duration)}</span></div><div style={{ fontSize: 17, fontWeight: 600, marginTop: 8 }}>{s.topic || 'Translation Session'}</div><div style={{ fontSize: 12, color: 'rgba(251,243,235,.55)', marginTop: 3 }}>{s.caller_name}</div></button></div>)}
              </>
            )}
            {ended.length > 0 && (
              <>
                {live.length > 0 && <div style={{ height: 8 }} />}
                <p style={{ fontSize: 10.5, fontWeight: 700, color: 'rgba(62,76,94,.45)', textTransform: 'uppercase', letterSpacing: '.16em', margin: '18px 0 9px' }}>Past</p>
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
        <button onClick={onBack} style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(251,243,235,.1)', color: T.cream }}><Icon name="chevron-left" size={17} /></button>
        <h1 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 27, fontWeight: 400, color: T.cream, marginTop: 14, lineHeight: 1.15 }}>{detail?.topic || 'Translation session'}</h1>
        <div style={{ fontSize: 12, color: 'rgba(251,243,235,.55)', marginTop: 6 }}>{detail?.caller_name || sessionId}</div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '18px 20px 40px' }}>
        <div style={{ background: '#fff', border: '1px solid rgba(244,124,54,.18)', borderRadius: 20, padding: 16 }}>
          <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: T.amber, fontWeight: 700 }}>Automated notes</div>
          <div style={{ fontSize: 13.5, lineHeight: 1.5, color: T.slate, marginTop: 11 }}>{transcript.length ? `${transcript.length} translated conversation turn${transcript.length === 1 ? '' : 's'} recorded.` : 'Notes will appear after translated conversation turns are recorded.'}</div>
        </div>
        <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(62,76,94,.45)', fontWeight: 700, margin: '20px 0 10px' }}>Full transcript</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {transcript.map((t, i) => <div key={i} style={{ alignSelf: i % 2 ? 'flex-end' : 'flex-start', maxWidth: '86%' }}><div style={{ fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', fontWeight: 700, color: i % 2 ? '#2E8E86' : T.amber, marginBottom: 4 }}>{t.speaker_name || 'Speaker'}</div><div style={{ background: i % 2 ? '#fff' : 'rgba(244,124,54,.06)', border: `1px solid ${i % 2 ? 'rgba(88,191,180,.2)' : 'rgba(244,124,54,.16)'}`, borderRadius: 16, padding: '11px 13px', fontSize: 14.5, lineHeight: 1.45, color: T.slate }}>{t.original}</div><div style={{ fontSize: 11.5, color: 'rgba(62,76,94,.42)', marginTop: 4, fontStyle: 'italic' }}>{t.translated}</div></div>)}
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
      <div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(62,76,94,.45)', fontWeight: 700, marginBottom: 9 }}>Offline translation</div>
      <div style={{ background: '#fff', border: '1px solid rgba(62,76,94,.08)', borderRadius: 18, overflow: 'hidden' }}>{children}</div>
    </div>
  );
}

function OfflineNote({ children }) {
  return <div style={{ padding: '14px 15px', fontSize: 13, lineHeight: 1.5, color: 'rgba(62,76,94,.6)' }}>{children}</div>;
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
      await downloadOfflineModels(['en', code]);
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
        <div style={{ padding: '13px 15px', borderBottom: '1px solid rgba(62,76,94,.06)', fontSize: 12.5, lineHeight: 1.5, color: 'rgba(62,76,94,.62)' }}>
          {canDownload
            ? 'Download a language to translate it to and from English with no connection. Each is about 30MB, so use Wi-Fi.'
            : 'Languages you have downloaded on this device can be translated to and from English with no connection. Add more in Settings \u203a Apps \u203a Translate \u203a Downloaded Languages.'}
          {' '}Cantonese has no offline model on any phone and always needs a connection.
        </div>
        {supported.map((code, i) => {
          const status = statuses ? statuses[code] : undefined;
          return (
            <div key={code} style={{ padding: '12px 15px', display: 'flex', alignItems: 'center', gap: 12, borderTop: i ? '1px solid rgba(62,76,94,.06)' : 'none' }}>
              <span style={{ fontSize: 17 }}>{getLang(code).flag}</span>
              <div style={{ flex: 1, fontSize: 14.5, fontWeight: 600, color: T.slate }}>{getLang(code).label}</div>
              {statuses === null && <span style={{ fontSize: 12, color: 'rgba(62,76,94,.4)' }}>Checking\u2026</span>}
              {status === 'installed' && <span style={{ fontSize: 12, fontWeight: 600, color: '#2E8E86' }}>On device</span>}
              {status === 'unsupported' && <span style={{ fontSize: 12, color: 'rgba(62,76,94,.35)' }}>Not available</span>}
              {status === 'supported' && (canDownload ? (
                <button
                  onClick={() => handleDownload(code)}
                  disabled={busy === code}
                  style={{ fontSize: 12.5, fontWeight: 600, color: busy === code ? 'rgba(62,76,94,.4)' : T.amber, background: 'transparent', border: 'none', padding: '4px 2px' }}
                >
                  {busy === code ? 'Downloading\u2026' : 'Download'}
                </button>
              ) : (
                <span style={{ fontSize: 12, color: 'rgba(62,76,94,.45)' }}>In iPhone Settings</span>
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
            color: T.slate, border: '1px solid rgba(62,76,94,.14)', borderRadius: 18,
            fontSize: 15, fontWeight: 600, display: 'flex', alignItems: 'center',
            justifyContent: 'center', gap: 8,
          }}
        >
          <Icon name="mic" size={17} color={T.slate} />
          Start an offline session
        </button>
      )}
      {error && <div style={{ fontSize: 12, color: '#C0453B', marginTop: -12, marginBottom: 16, paddingLeft: 2 }}>{error}</div>}
    </div>
  );
}

function SettingsScreen({ onStartOffline }) {
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
  return <div style={{ flex: 1, overflowY: 'auto', background: T.cream }}><div style={{ padding: '62px 22px 14px' }}><h1 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 34, fontWeight: 400, color: T.slate }}>Settings</h1></div><div style={{ padding: '6px 20px 96px' }}>{groups.map(([title, rows]) => <div key={title} style={{ marginBottom: 20 }}><div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(62,76,94,.45)', fontWeight: 700, marginBottom: 9 }}>{title}</div><div style={{ background: '#fff', border: '1px solid rgba(62,76,94,.08)', borderRadius: 18, overflow: 'hidden' }}>{rows.map(([key,label,hint], i) => <button key={key} onClick={() => setValues(v => ({...v, [key]: !v[key]}))} style={{ width: '100%', padding: '14px 15px', display: 'flex', alignItems: 'center', gap: 12, borderTop: i ? '1px solid rgba(62,76,94,.06)' : 'none', textAlign: 'left' }}><div style={{ flex: 1 }}><div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate }}>{label}</div><div style={{ fontSize: 11.5, color: 'rgba(62,76,94,.48)', marginTop: 2 }}>{hint}</div></div><div style={{ width: 44, height: 26, borderRadius: 99, background: values[key] ? T.amber : 'rgba(62,76,94,.16)', padding: 3, display: 'flex', justifyContent: values[key] ? 'flex-end' : 'flex-start' }}><div style={{ width: 20, height: 20, borderRadius: '50%', background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,.25)' }} /></div></button>)}</div></div>)}<OfflineTranslationSection onStart={onStartOffline} /><div style={{ marginBottom: 20 }}><div style={{ fontSize: 10.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(62,76,94,.45)', fontWeight: 700, marginBottom: 9 }}>Legal</div><div style={{ background: '#fff', border: '1px solid rgba(62,76,94,.08)', borderRadius: 18, overflow: 'hidden' }}>{links.map(([label, hint, href], i) => <a key={label} href={href} rel="noopener noreferrer" style={{ width: '100%', padding: '14px 15px', display: 'flex', alignItems: 'center', gap: 12, borderTop: i ? '1px solid rgba(62,76,94,.06)' : 'none', textAlign: 'left', textDecoration: 'none' }}><div style={{ flex: 1 }}><div style={{ fontSize: 14.5, fontWeight: 600, color: T.slate }}>{label}</div><div style={{ fontSize: 11.5, color: 'rgba(62,76,94,.48)', marginTop: 2 }}>{hint}</div></div><Icon name="chevron-left" size={16} color="rgba(62,76,94,.3)" /></a>)}</div></div></div></div>;
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

      <h2 style={{ fontFamily: "'Instrument Serif', serif", fontSize: 28, fontWeight: 400, marginBottom: 8, textAlign: 'center', color: T.navy, lineHeight: 1.1 }}>
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
            boxShadow: '0 12px 24px -8px rgba(244,124,54,.6)',
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

  function goTab(t) {
    setTab(t);
    if (t === 'home') setScreen('home');
    else if (t === 'live') setScreen('live-setup');
    else if (t === 'history') setScreen('history');
    else if (t === 'settings') setScreen('settings');
  }

  function enterSession(cfg) {
    setSessionConfig(cfg);
    setLangA(cfg.langA);
    setLangB(cfg.langB);
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
          onHistory={() => { setTab('history'); setScreen('history'); }}
          onSession={id => { setDetailSessionId(id); setScreen('detail'); }}
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
      content = <SettingsScreen onStartOffline={() => setScreen('live-offline')} />;
      break;

    case 'live-offline':
      // Entered deliberately from Settings. Never routed to automatically, so it
      // can never pre-empt a live server session.
      content = (
        <OfflineLiveScreen
          langA={langA}
          langB={langB}
          onBack={() => { setTab('settings'); setScreen('settings'); }}
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
