// Vocare Mobile Translation App
// Real-time two-way voice translation via WebRTC

const { useState, useEffect, useRef, useCallback, useMemo } = React;

// ─────────────────────────────────────────────
// Design tokens (mirrors CSS vars in vocare.html)
// ─────────────────────────────────────────────

const T = {
  teal:        'oklch(0.60 0.13 187)',
  tealLight:   'oklch(0.94 0.05 187)',
  tealMid:     'oklch(0.75 0.10 187)',
  amber:       'oklch(0.47 0.20 22)',
  amberLight:  'oklch(0.95 0.05 22)',
  bg:          'oklch(0.97 0.01 240)',
  surface:     '#ffffff',
  surface2:    'oklch(0.96 0.008 240)',
  textPrimary: 'oklch(0.14 0.02 240)',
  textMuted:   'oklch(0.52 0.02 240)',
  textFaint:   'oklch(0.75 0.01 240)',
  border:      'oklch(0.90 0.01 240)',
  error:       'oklch(0.52 0.18 22)',
  errorLight:  'oklch(0.96 0.04 22)',
  success:     'oklch(0.52 0.14 158)',
};

// ─────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────

const LANGUAGES = [
  { code: 'en', flag: '🇺🇸', label: 'English' },
  { code: 'zh', flag: '🇨🇳', label: 'Mandarin' },
  { code: 'ja', flag: '🇯🇵', label: 'Japanese' },
  { code: 'ko', flag: '🇰🇷', label: 'Korean' },
  { code: 'es', flag: '🇪🇸', label: 'Spanish' },
  { code: 'fr', flag: '🇫🇷', label: 'French' },
  { code: 'de', flag: '🇩🇪', label: 'German' },
  { code: 'ar', flag: '🇦🇪', label: 'Arabic' },
];

function getLang(code) {
  return LANGUAGES.find(l => l.code === code) || { code, flag: '🌐', label: code };
}

// ─────────────────────────────────────────────
// WebRTC helpers
// ─────────────────────────────────────────────

async function fetchIceServers() {
  try {
    const r = await fetch('/api/ice');
    const d = await r.json();
    return d.iceServers || [];
  } catch {
    return [];
  }
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

async function hangupPcId(pcId, { keepalive = false } = {}) {
  if (!pcId) return;
  try {
    await fetch('/api/hangup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pc_id: pcId }),
      keepalive,
    });
  } catch (_) {}
}

function fireAndForgetHangup(pcId) {
  if (!pcId) return;
  const body = JSON.stringify({ pc_id: pcId });
  if (navigator.sendBeacon) {
    try {
      const blob = new Blob([body], { type: 'application/json' });
      if (navigator.sendBeacon('/api/hangup', blob)) return;
    } catch (_) {}
  }
  void hangupPcId(pcId, { keepalive: true });
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
    { id: 'live',    icon: 'mic',     label: 'Live' },
    { id: 'history', icon: 'history', label: 'History' },
  ];
  return (
    <div style={{
      display: 'flex',
      background: T.surface,
      borderTop: `1px solid ${T.border}`,
      paddingBottom: 'env(safe-area-inset-bottom, 0px)',
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
              padding: '10px 0 8px',
              gap: 3,
              color: isActive ? T.teal : T.textFaint,
              transition: 'color 0.15s',
            }}
          >
            <Icon name={t.icon} size={22} />
            <span style={{ fontSize: 10, fontWeight: isActive ? 600 : 400 }}>{t.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────
// WelcomeScreen
// ─────────────────────────────────────────────

function WelcomeScreen({ langA, langB, onStart, onLangPair, onHistory, onSession }) {
  const [sessions, setSessions] = useState([]);
  const langAInfo = getLang(langA);
  const langBInfo = getLang(langB);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const r = await fetch('/api/translation/sessions');
        const d = await r.json();
        if (active) setSessions(d.sessions || []);
      } catch {}
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { active = false; clearInterval(id); };
  }, []);

  const topSessions = sessions.slice(0, 3);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflowY: 'auto', background: T.bg }}>
      {/* Hero header — solid teal, bottom corners rounded */}
      <div style={{
        background: T.teal,
        padding: '52px 24px 32px',
        borderRadius: '0 0 32px 32px',
        color: '#fff',
        flexShrink: 0,
      }}>
        {/* Logo row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
          <div style={{
            width: 38, height: 38,
            background: 'rgba(255,255,255,0.15)',
            borderRadius: 12,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0,
          }}>
            <Icon name="translate" size={21} color="#fff" />
          </div>
          <div>
            <h1 style={{ fontSize: 20, fontWeight: 700, letterSpacing: -0.3, lineHeight: 1.1 }}>Vocare</h1>
            <p style={{ fontSize: 11, opacity: 0.65, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1.2 }}>Voice Translation</p>
          </div>
        </div>

        {/* Tagline */}
        <p style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.2, marginBottom: 10 }}>
          Speak freely.<br />Understand instantly.
        </p>
        <p style={{ fontSize: 14, opacity: 0.70, lineHeight: 1.5, marginBottom: 24 }}>
          Real-time translation for face-to-face conversations.
        </p>

        {/* Language pair selector — inside hero */}
        <button
          onClick={onLangPair}
          style={{
            width: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 16px',
            background: 'rgba(255,255,255,0.15)',
            border: '1px solid rgba(255,255,255,0.25)',
            borderRadius: 16,
            cursor: 'pointer',
            color: '#fff',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 17 }}>{langAInfo.flag}</span>
            <span style={{ fontSize: 14, fontWeight: 600 }}>
              {langAInfo.code === 'auto' ? 'AUTO' : langAInfo.label}
            </span>
            <Icon name="swap" size={16} color="rgba(255,255,255,0.7)" />
            <span style={{ fontSize: 17 }}>{langBInfo.flag}</span>
            <span style={{ fontSize: 14, fontWeight: 600 }}>
              {langBInfo.code === 'auto' ? 'AUTO' : langBInfo.label}
            </span>
          </div>
          <Icon name="chevron" size={18} color="rgba(255,255,255,0.7)" />
        </button>
      </div>

      {/* Start button — outside hero, in bg */}
      <div style={{ padding: '20px 24px 0' }}>
        <button
          onClick={onStart}
          style={{
            width: '100%',
            padding: '18px',
            background: T.teal,
            color: '#fff',
            border: 'none',
            borderRadius: 18,
            fontSize: 17,
            fontWeight: 700,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            boxShadow: '0 4px 20px oklch(0.60 0.13 187 / 0.35)',
          }}
        >
          <Icon name="mic" size={20} color="#fff" />
          Start Translation
        </button>
      </div>

      {/* Recent sessions */}
      <div style={{ padding: '24px 16px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <h2 style={{ fontSize: 12, fontWeight: 700, color: T.textMuted, textTransform: 'uppercase', letterSpacing: 0.6 }}>
            Recent Sessions
          </h2>
          {sessions.length > 3 && (
            <button onClick={onHistory} style={{ fontSize: 13, color: T.teal, fontWeight: 500 }}>
              View all
            </button>
          )}
        </div>

        {topSessions.length === 0 ? (
          <div style={{
            padding: '32px 16px',
            textAlign: 'center',
            color: T.textFaint,
            fontSize: 14,
          }}>
            No sessions yet. Start a translation above.
          </div>
        ) : (
          topSessions.map(s => (
            <SessionCard key={s.session_id} session={s} onClick={() => onSession(s.session_id)} />
          ))
        )}
      </div>

      <div style={{ height: 16 }} />
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
        width: '100%',
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        padding: '14px 16px',
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: 14,
        marginBottom: 10,
        cursor: 'pointer',
        textAlign: 'left',
        animation: 'fade-in-up 0.3s ease-out both',
      }}
    >
      <div style={{
        width: 40, height: 40,
        background: isLive ? T.tealLight : T.surface2,
        borderRadius: 12,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        flexShrink: 0,
      }}>
        <Icon name={isLive ? 'mic' : 'history'} size={18} color={isLive ? T.teal : T.textFaint} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: T.textPrimary, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {session.topic || session.caller_name || 'Translation Session'}
          </span>
          {isLive && (
            <span style={{ fontSize: 11, fontWeight: 700, color: T.teal, background: T.tealLight, padding: '2px 7px', borderRadius: 99 }}>
              LIVE
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: T.textMuted }}>{lang.flag} {session.caller_name}</span>
          {session.participant_count != null && (
            <span style={{ fontSize: 12, color: T.textFaint }}>· {session.participant_count} people</span>
          )}
          {durationStr && (
            <span style={{ fontSize: 12, color: T.textFaint }}>· {durationStr}</span>
          )}
        </div>
      </div>
      <Icon name="chevron" size={16} color={T.textFaint} style={{ flexShrink: 0, marginTop: 2 }} />
    </button>
  );
}

function formatDuration(secs) {
  if (secs == null) return '';
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// ─────────────────────────────────────────────
// SessionSetupScreen
// ─────────────────────────────────────────────

function SessionSetupScreen({ langA, langB, onBack, onStart, onLangPick }) {
  const [mode, setMode] = useState('my-device');      // 'my-device' | 'face-to-face'
  const [micMode, setMicMode] = useState('always');   // 'hold' | 'always'
  const [nameA, setNameA] = useState('Person A');
  const [nameB, setNameB] = useState('Person B');
  const [localLangA, setLocalLangA] = useState(langA);
  const [localLangB, setLocalLangB] = useState(langB);
  const [pickingFor, setPickingFor] = useState(null); // null | 'a' | 'b'

  const langAInfo = getLang(localLangA);
  const langBInfo = getLang(localLangB);

  const showInfo = mode === 'face-to-face' && micMode === 'hold';

  function handleStart() {
    onStart({
      mode,
      micMode,
      nameA,
      nameB: mode === 'face-to-face' ? nameB : nameA,
      langA: localLangA,
      langB: localLangB,
    });
  }

  if (pickingFor !== null) {
    return (
      <LangScreen
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
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '52px 16px 16px',
        background: T.surface,
        borderBottom: `1px solid ${T.border}`,
      }}>
        <button onClick={onBack} style={{ padding: 4, color: T.textMuted }}>
          <Icon name="chevron-left" size={24} />
        </button>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>Session Setup</h1>
      </div>

      <div style={{ padding: '20px 16px', display: 'flex', flexDirection: 'column', gap: 20 }}>
        {/* Device Setup */}
        <Section title="Device Setup">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <ToggleCard
              active={mode === 'my-device'}
              title="My Device"
              subtitle="Single connection"
              icon="mic"
              onClick={() => setMode('my-device')}
            />
            <ToggleCard
              active={mode === 'face-to-face'}
              title="Face-to-Face"
              subtitle="Split screen"
              icon="swap"
              onClick={() => setMode('face-to-face')}
            />
          </div>
        </Section>

        {/* Microphone Control */}
        <Section title="Microphone Control">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <ToggleCard
              active={micMode === 'always'}
              title="Always On"
              subtitle="Auto-detect"
              icon="volume"
              onClick={() => setMicMode('always')}
            />
            <ToggleCard
              active={micMode === 'hold'}
              title="Hold to Speak"
              subtitle="Push-to-talk"
              icon="pause"
              onClick={() => setMicMode('hold')}
            />
          </div>
        </Section>

        {/* Info tip */}
        {showInfo && (
          <div style={{
            padding: '12px 14px',
            background: T.tealLight,
            borderRadius: 12,
            display: 'flex',
            gap: 10,
            animation: 'fade-in 0.2s ease-out',
          }}>
            <Icon name="alert" size={18} color={T.teal} style={{ flexShrink: 0, marginTop: 1 }} />
            <p style={{ fontSize: 13, color: T.teal, lineHeight: 1.5 }}>
              In face-to-face mode each person holds the button while speaking. Hand the device to the other person to reply.
            </p>
          </div>
        )}

        {/* People */}
        <Section title="People">
          <PersonRow
            label="Person A"
            name={nameA}
            lang={localLangA}
            onNameChange={setNameA}
            onLangTap={() => setPickingFor('a')}
          />
          {mode === 'face-to-face' && (
            <PersonRow
              label="Person B"
              name={nameB}
              lang={localLangB}
              onNameChange={setNameB}
              onLangTap={() => setPickingFor('b')}
              style={{ marginTop: 10 }}
            />
          )}
          {mode === 'my-device' && (
            <div style={{ marginTop: 10 }}>
              <PersonRow
                label="Person B"
                name={nameB}
                lang={localLangB}
                onNameChange={setNameB}
                onLangTap={() => setPickingFor('b')}
                dimmed
              />
            </div>
          )}
        </Section>

        {/* Start button */}
        <button
          onClick={handleStart}
          style={{
            width: '100%',
            padding: '16px',
            background: T.teal,
            color: '#fff',
            border: 'none',
            borderRadius: 16,
            fontSize: 16,
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
          }}
        >
          <Icon name="mic" size={20} color="#fff" />
          Start Session
        </button>

        <div style={{ height: 24 }} />
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div>
      <h3 style={{ fontSize: 12, fontWeight: 700, color: T.textMuted, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 10 }}>
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

function PersonRow({ label, name, lang, onNameChange, onLangTap, dimmed = false, style = {} }) {
  const langInfo = getLang(lang);
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      padding: '12px 14px',
      background: T.surface,
      border: `1px solid ${T.border}`,
      borderRadius: 12,
      opacity: dimmed ? 0.55 : 1,
      ...style,
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: T.textFaint, width: 56, flexShrink: 0 }}>
        {label}
      </div>
      <input
        value={name}
        onChange={e => onNameChange(e.target.value)}
        disabled={dimmed}
        style={{
          flex: 1,
          fontSize: 14,
          fontWeight: 500,
          color: T.textPrimary,
          background: 'none',
          border: 'none',
          outline: 'none',
          fontFamily: "'DM Sans', sans-serif",
        }}
      />
      <button
        onClick={onLangTap}
        disabled={dimmed}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '4px 10px',
          background: T.surface2,
          border: `1px solid ${T.border}`,
          borderRadius: 99,
          fontSize: 13,
          fontWeight: 500,
          color: T.textPrimary,
          cursor: 'pointer',
          flexShrink: 0,
        }}
      >
        <span>{langInfo.flag}</span>
        <span>{langInfo.label}</span>
        <Icon name="chevron" size={12} color={T.textFaint} />
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────
// PersonNameTag — colored square tile with flag, name and lang
// Used in FaceToFace and Auto live screens
// ─────────────────────────────────────────────

function PersonNameTag({ langInfo, name, color, pressing }) {
  const textColor = pressing ? '#fff' : T.textPrimary;
  const subColor = pressing ? 'rgba(255,255,255,0.75)' : T.textMuted;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{
        width: 28, height: 28,
        background: pressing ? 'rgba(255,255,255,0.22)' : color + '22',
        borderRadius: 8,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        flexShrink: 0,
        transition: 'background 0.2s',
      }}>
        <span style={{ fontSize: 16 }}>{langInfo.flag}</span>
      </div>
      <div>
        <p style={{ fontSize: 14, fontWeight: 600, color: textColor, transition: 'color 0.2s' }}>{name}</p>
        <p style={{ fontSize: 12, color: subColor, transition: 'color 0.2s' }}>{langInfo.label}</p>
      </div>
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
  const streamRef = useRef(null);
  const timerRef = useRef(null);
  const pollRef = useRef(null);
  const sessionIdRef = useRef(null);
  const mountedRef = useRef(true);

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
      const sessionRes = await fetch('/api/translation/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caller_name: nameA, caller_language: langA, topic: 'Translation Session' }),
      });
      if (!sessionRes.ok) throw new Error('Session create failed');
      const { session_id } = await sessionRes.json();
      if (!mountedRef.current) return;
      sessionIdRef.current = session_id;
      setSessionId(session_id);

      // 4. Create peer connections
      const pcCfg = { iceServers, iceTransportPolicy: 'relay' };
      const pcA = new RTCPeerConnection(pcCfg);
      const pcB = new RTCPeerConnection(pcCfg);
      pcARef.current = pcA;
      pcBRef.current = pcB;

      // 5. Clone mic tracks (both start disabled)
      const trackA = stream.getTracks()[0].clone();
      const trackB = stream.getTracks()[0].clone();
      trackA.enabled = false;
      trackB.enabled = false;
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
      const ansA = await fetch('/api/translation/offer', {
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
      const ansB = await fetch('/api/translation/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id, language: langB, name: nameB, sdp: offerB.sdp, type: offerB.type }),
      });
      if (!ansB.ok) throw new Error('Offer B failed');
      const ansBData = await ansB.json();
      pcBIdRef.current = ansBData.pc_id;
      await pcB.setRemoteDescription({ sdp: ansBData.sdp, type: ansBData.type });

    } catch (err) {
      console.error('FaceToFace setup error:', err);
      if (!mountedRef.current) return;
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        onError('mic');
      } else {
        setPhase('error');
        setConnState('Connection failed');
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
        const r = await fetch(`/api/translation/poll?session_id=${sessionIdRef.current}`);
        if (!r.ok || !mountedRef.current) return;
        const d = await r.json();
        if (d.events && d.events.length > 0) {
          const turnEvents = d.events.filter(e => e.type === 'turn');
          if (turnEvents.length > 0) {
            turnEvents.forEach(ev => {
              const isA = ev.original_lang === langA;
              if (isA) setTurnsB(prev => [...prev, ev]);
              else setTurnsA(prev => [...prev, ev]);
            });
          }
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
    }
  }

  // PTT handlers
  const pressBStart = useCallback(() => {
    if (trackBRef.current) trackBRef.current.enabled = true;
    if (trackARef.current) trackARef.current.enabled = false;
    setPressB(true);
  }, []);
  const pressBEnd = useCallback(() => {
    if (trackBRef.current) trackBRef.current.enabled = false;
    setPressB(false);
  }, []);
  const pressAStart = useCallback(() => {
    if (trackARef.current) trackARef.current.enabled = true;
    if (trackBRef.current) trackBRef.current.enabled = false;
    setPressA(true);
  }, []);
  const pressAEnd = useCallback(() => {
    if (trackARef.current) trackARef.current.enabled = false;
    setPressA(false);
  }, []);

  const lastTurnA = turnsA[turnsA.length - 1];
  const lastTurnB = turnsB[turnsB.length - 1];
  const elapsedStr = formatDuration(elapsed);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.bg, overflow: 'hidden' }}>
      {/* Hidden audio elements */}
      <audio ref={audioRefA} autoPlay playsInline style={{ display: 'none' }} />
      <audio ref={audioRefB} autoPlay playsInline style={{ display: 'none' }} />

      {/* Person B panel — top half, rotated 180° */}
      <div style={{
        flex: 1,
        transform: 'rotate(180deg)',
        background: pressB ? T.teal : T.bg,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '24px 24px 20px',
        transition: 'background 0.2s',
        position: 'relative',
      }}>
        {/* Person info */}
        <div style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 10 }}>
          <PersonNameTag langInfo={langBInfo} name={nameB} color={T.teal} pressing={pressB} />
          {pressB && (
            <div style={{ marginLeft: 'auto' }}>
              <Waveform color={pressB ? '#fff' : T.teal} active={true} height={24} />
            </div>
          )}
        </div>

        {/* Last phrase */}
        <div style={{ width: '100%' }}>
          {lastTurnB && (
            <ConvTurn turn={lastTurnB} colorHex={T.teal} />
          )}
          {phase === 'connecting' && !lastTurnB && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <TypingDots color={pressB ? 'rgba(255,255,255,0.5)' : T.textFaint} />
              <span style={{ fontSize: 13, color: pressB ? 'rgba(255,255,255,0.5)' : T.textFaint }}>{connState}</span>
            </div>
          )}
        </div>

        {/* Hold to speak button */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
          <div style={{ position: 'relative', width: 68, height: 68, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {pressB && <PulseRing color={pressB ? 'rgba(255,255,255,0.6)' : T.teal} size={68} />}
            <button
              onPointerDown={pressBStart}
              onPointerUp={pressBEnd}
              onPointerLeave={pressBEnd}
              disabled={phase !== 'connected'}
              style={{
                width: 68, height: 68,
                borderRadius: '50%',
                background: pressB ? 'rgba(255,255,255,0.25)' : T.teal + '18',
                border: `2px solid ${pressB ? 'rgba(255,255,255,0.6)' : T.teal}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'all 0.15s',
                cursor: phase === 'connected' ? 'pointer' : 'default',
                touchAction: 'none',
                position: 'relative',
                zIndex: 1,
              }}
            >
              <Icon name="mic" size={26} color={pressB ? '#fff' : T.teal} />
            </button>
          </div>
          <span style={{ fontSize: 11, color: pressB ? 'rgba(255,255,255,0.6)' : T.textFaint, fontWeight: 500 }}>
            {phase === 'connected' ? 'Hold to speak' : connState}
          </span>
        </div>
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
        {/* Flags + divider */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 15 }}>{langAInfo.flag}</span>
          <span style={{ fontSize: 12, color: T.textFaint }}>↕</span>
          <span style={{ fontSize: 15 }}>{langBInfo.flag}</span>
        </div>

        {/* Elapsed timer */}
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

        {/* Stop button — 28px red */}
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

      {/* Person A panel — bottom half */}
      <div style={{
        flex: 1,
        background: pressA ? T.amber : T.bg,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '20px 24px 32px',
        transition: 'background 0.2s',
        position: 'relative',
      }}>
        {/* Person info */}
        <div style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 10 }}>
          <PersonNameTag langInfo={langAInfo} name={nameA} color={T.amber} pressing={pressA} />
          {pressA && (
            <div style={{ marginLeft: 'auto' }}>
              <Waveform color={pressA ? '#fff' : T.amber} active={true} height={24} />
            </div>
          )}
        </div>

        {/* Last phrase */}
        <div style={{ width: '100%' }}>
          {lastTurnA && (
            <ConvTurn turn={lastTurnA} colorHex={T.amber} />
          )}
          {phase === 'connecting' && !lastTurnA && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <TypingDots color={pressA ? 'rgba(255,255,255,0.5)' : T.textFaint} />
              <span style={{ fontSize: 13, color: pressA ? 'rgba(255,255,255,0.5)' : T.textFaint }}>{connState}</span>
            </div>
          )}
        </div>

        {/* Hold to speak button */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
          <div style={{ position: 'relative', width: 68, height: 68, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {pressA && <PulseRing color={pressA ? 'rgba(255,255,255,0.6)' : T.amber} size={68} />}
            <button
              onPointerDown={pressAStart}
              onPointerUp={pressAEnd}
              onPointerLeave={pressAEnd}
              disabled={phase !== 'connected'}
              style={{
                width: 68, height: 68,
                borderRadius: '50%',
                background: pressA ? 'rgba(255,255,255,0.25)' : T.amber + '18',
                border: `2px solid ${pressA ? 'rgba(255,255,255,0.6)' : T.amber}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'all 0.15s',
                cursor: phase === 'connected' ? 'pointer' : 'default',
                touchAction: 'none',
                position: 'relative',
                zIndex: 1,
              }}
            >
              <Icon name="mic" size={26} color={pressA ? '#fff' : T.amber} />
            </button>
          </div>
          <span style={{ fontSize: 11, color: pressA ? 'rgba(255,255,255,0.6)' : T.textFaint, fontWeight: 500 }}>
            {phase === 'connected' ? 'Hold to speak' : connState}
          </span>
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

      const sessionRes = await fetch('/api/translation/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caller_name: nameA, caller_language: langA, topic: 'Auto Translation' }),
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

      const ans = await fetch('/api/translation/auto-offer', {
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
        setPhase('error');
        setConnState('Connection failed');
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
        const r = await fetch(`/api/translation/poll?session_id=${sessionIdRef.current}`);
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
// LangScreen — language pair picker
// ─────────────────────────────────────────────

function LangScreen({ onBack, onSelect }) {
  const [query, setQuery] = useState('');

  const filtered = query.trim()
    ? LANGUAGES.filter(l =>
        l.label.toLowerCase().includes(query.toLowerCase()) ||
        l.code.toLowerCase().includes(query.toLowerCase())
      )
    : LANGUAGES;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.surface, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '52px 16px 16px',
        borderBottom: `1px solid ${T.border}`,
        flexShrink: 0,
      }}>
        <button onClick={onBack} style={{ padding: 4, color: T.textMuted }}>
          <Icon name="chevron-left" size={24} />
        </button>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>Select Language</h1>
      </div>

      {/* Search */}
      <div style={{ padding: '12px 16px', borderBottom: `1px solid ${T.border}`, flexShrink: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '10px 14px',
          background: T.surface2,
          borderRadius: 12,
          border: `1px solid ${T.border}`,
        }}>
          <Icon name="search" size={18} color={T.textFaint} />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search languages…"
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

      {/* Auto-detect option */}
      <button
        onClick={() => onSelect('auto')}
        style={{
          display: 'flex', alignItems: 'center', gap: 14,
          padding: '14px 20px',
          borderBottom: `1px solid ${T.border}`,
          width: '100%',
          textAlign: 'left',
          flexShrink: 0,
        }}
      >
        <div style={{
          width: 40, height: 40,
          background: T.tealLight,
          borderRadius: 12,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Icon name="globe" size={20} color={T.teal} />
        </div>
        <div>
          <p style={{ fontSize: 15, fontWeight: 600, color: T.textPrimary }}>Auto-detect</p>
          <p style={{ fontSize: 13, color: T.textMuted }}>Detect language automatically</p>
        </div>
      </button>

      {/* Language list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {filtered.map(lang => (
          <button
            key={lang.code}
            onClick={() => onSelect(lang.code)}
            style={{
              display: 'flex', alignItems: 'center', gap: 14,
              padding: '14px 20px',
              width: '100%',
              borderBottom: `1px solid ${T.border}`,
              textAlign: 'left',
            }}
          >
            <span style={{ fontSize: 28, lineHeight: 1 }}>{lang.flag}</span>
            <div style={{ flex: 1 }}>
              <p style={{ fontSize: 15, fontWeight: 500, color: T.textPrimary }}>{lang.label}</p>
              <p style={{ fontSize: 12, color: T.textMuted, textTransform: 'uppercase' }}>{lang.code}</p>
            </div>
            <Icon name="chevron" size={16} color={T.textFaint} />
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

function HistoryScreen({ onBack, onSession }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const r = await fetch('/api/translation/sessions');
        const d = await r.json();
        if (active) {
          setSessions(d.sessions || []);
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
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '52px 16px 16px',
        background: T.surface,
        borderBottom: `1px solid ${T.border}`,
        flexShrink: 0,
      }}>
        <button onClick={onBack} style={{ padding: 4, color: T.textMuted }}>
          <Icon name="chevron-left" size={24} />
        </button>
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>Session History</h1>
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
          <div style={{ padding: '16px' }}>
            {live.length > 0 && (
              <>
                <p style={{ fontSize: 11, fontWeight: 700, color: T.textMuted, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 10 }}>Live</p>
                {live.map(s => <SessionCard key={s.session_id} session={s} onClick={() => onSession(s.session_id)} />)}
              </>
            )}
            {ended.length > 0 && (
              <>
                {live.length > 0 && <div style={{ height: 8 }} />}
                <p style={{ fontSize: 11, fontWeight: 700, color: T.textMuted, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 10 }}>Past</p>
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
      background: T.bg,
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

      <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 8, textAlign: 'center', color: T.textPrimary }}>
        {isMic ? 'Microphone Access Required' : 'Connection Failed'}
      </h2>
      <p style={{ fontSize: 14, color: T.textMuted, textAlign: 'center', lineHeight: 1.6, maxWidth: 280, marginBottom: 32 }}>
        {isMic
          ? 'Vocare needs microphone access to translate speech. Please allow microphone access in your browser settings.'
          : 'Unable to connect to the translation server. Please check your connection and try again.'}
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: '100%', maxWidth: 280 }}>
        <button
          onClick={onRetry}
          style={{
            padding: '14px',
            background: T.teal,
            color: '#fff',
            borderRadius: 14,
            fontSize: 15,
            fontWeight: 600,
          }}
        >
          {isMic ? 'Open Settings' : 'Try Again'}
        </button>
        <button
          onClick={onBack}
          style={{
            padding: '14px',
            background: T.surface,
            color: T.textPrimary,
            borderRadius: 14,
            fontSize: 15,
            fontWeight: 600,
            border: `1px solid ${T.border}`,
          }}
        >
          Go Back
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
  const [langPickCallback, setLangPickCallback] = useState(null);

  function goTab(t) {
    setTab(t);
    if (t === 'home') setScreen('home');
    else if (t === 'live') setScreen('live-setup');
    else if (t === 'history') setScreen('history');
  }

  function handleStart(cfg) {
    setSessionConfig(cfg);
    setLangA(cfg.langA);
    setLangB(cfg.langB);
    if (cfg.mode === 'face-to-face' && cfg.micMode === 'hold') {
      setScreen('live-face');
    } else {
      setScreen('live-auto');
    }
  }

  function handleStop() {
    setSessionConfig(null);
    setScreen('home');
    setTab('home');
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

  const showTabBar = screen === 'home' || screen === 'history';

  let content;
  switch (screen) {
    case 'home':
      content = (
        <WelcomeScreen
          langA={langA}
          langB={langB}
          onStart={() => { setTab('live'); setScreen('live-setup'); }}
          onLangPair={() => setScreen('lang-home')}
          onHistory={() => { setTab('history'); setScreen('history'); }}
          onSession={() => {}}
        />
      );
      break;

    case 'lang-home':
      content = (
        <LangScreen
          onBack={() => setScreen('home')}
          onSelect={code => {
            // cycle: set A first, then B
            setLangA(code);
            setScreen('home');
          }}
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

    case 'live-auto':
      content = (
        <AutoLiveScreen
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
          onSession={() => {}}
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
    </div>
  );
}

// ─────────────────────────────────────────────
// Mount
// ─────────────────────────────────────────────

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(React.createElement(App));
