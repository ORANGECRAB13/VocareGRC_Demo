const { useState, useEffect, useRef, useCallback } = React;

// ── Language options ──────────────────────────────────────────────────────────
const LANGUAGES = [
  { value: 'en', label: 'English',  flag: '🇦🇺' },
  { value: 'zh', label: 'Mandarin', flag: '🇨🇳' },
];

// ── Shared style helpers ──────────────────────────────────────────────────────
const S = {
  center:  { minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F4F5F6' },
  card:    { background: '#fff', padding: 40, borderRadius: 16, textAlign: 'center', border: '1px solid #E8E9EB', width: 400 },
  label:   { display: 'block', fontSize: 13, fontWeight: 700, color: '#444', marginBottom: 6 },
  input:   { flex: 1, padding: '12px 14px', border: '1px solid #ddd', borderRadius: 8, fontSize: 14, width: '100%', outline: 'none' },
  modeBtn: (active, color) => ({
    flex: 1, padding: '14px 10px',
    border: `2px solid ${active ? color : '#E8E9EB'}`,
    borderRadius: 10, background: active ? color + '18' : '#fff',
    cursor: 'pointer', textAlign: 'center', transition: 'all 0.15s',
  }),
  modeBtnIcon:  (active, color) => ({ fontSize: 28, color: active ? color : '#bbb', display: 'block', marginBottom: 4 }),
  modeBtnTitle: (active, color) => ({ fontSize: 13, fontWeight: 700, color: active ? color : '#555' }),
  modeBtnDesc:  { fontSize: 11, color: '#888', marginTop: 3 },
};

function tailWords(text, maxWords = 10) {
  if (!text) return '';
  const words = text.trim().split(/\s+/);
  if (words.length === 1 && text.length > maxWords * 3) {
    return text.slice(-(maxWords * 3));
  }
  return words.length <= maxWords ? text : words.slice(-maxWords).join(' ');
}

function matchesLang(code, target) {
  if (!code || !target) return false;
  return code === target || code.startsWith(target) || target.startsWith(code);
}

// ── WebRTC helpers ────────────────────────────────────────────────────────────
function makePC(iceServers, onStream) {
  const pc = new RTCPeerConnection({ iceServers, iceTransportPolicy: 'relay' });
  pc.ontrack = (evt) => { if (evt.streams[0]) onStream(evt.streams[0]); };
  return pc;
}

async function waitForICE(pc) {
  if (pc.iceGatheringState === 'complete') return;
  await new Promise(resolve => {
    const h = () => { if (pc.iceGatheringState === 'complete') { pc.removeEventListener('icegatheringstatechange', h); resolve(); } };
    pc.addEventListener('icegatheringstatechange', h);
    setTimeout(resolve, 5000);
  });
}

async function doOffer(pc, track, sessionId, name, language) {
  pc.addTrack(track, new MediaStream([track]));
  await pc.setLocalDescription(await pc.createOffer());
  await waitForICE(pc);
  const res = await fetch('/api/translation/offer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, name, language, sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
  });
  if (!res.ok) throw new Error(await res.text());
  const answer = await res.json();
  await pc.setRemoteDescription(answer);
  return answer;
}

async function doAutoOffer(pc, track, sessionId, langA, langB) {
  pc.addTrack(track, new MediaStream([track]));
  await pc.setLocalDescription(await pc.createOffer());
  await waitForICE(pc);
  const res = await fetch('/api/translation/auto-offer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, lang_a: langA, lang_b: langB, sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
  });
  if (!res.ok) throw new Error(await res.text());
  const answer = await res.json();
  await pc.setRemoteDescription(answer);
  return answer;
}

// ── SameDevicePanel (module-level to avoid React remount on every render) ─────
function SameDevicePanel({ name, lang, talking, onStart, onStop, color, darkColor, onAudioMount, autoMode, muted, onToggleMute, liveText, recentText }) {
  // Only wire up a ref callback when this panel owns an audio element
  const audioRefCb = useCallback(node => { if (onAudioMount && node) onAudioMount(node); }, [onAudioMount]);

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 16,
      background: (!autoMode && talking) ? darkColor : color,
      transition: 'background 0.2s', padding: 32,
    }}>
      {/* Name + language */}
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'rgba(255,255,255,0.65)', letterSpacing: 1.5, textTransform: 'uppercase', marginBottom: 4 }}>
          {lang.flag} {lang.label}
        </div>
        {name && <div style={{ fontSize: 20, fontWeight: 800, color: '#fff' }}>{name}</div>}
      </div>

      {(liveText || recentText) && (
        <div style={{
          width: '100%', maxWidth: 420, minHeight: 94,
          padding: '16px 18px', borderRadius: 18,
          background: 'rgba(255,255,255,0.12)',
          border: '1px solid rgba(255,255,255,0.18)',
          backdropFilter: 'blur(8px)',
          overflow: 'hidden',
        }}>
          {autoMode && liveText && (
            <div style={{
              fontSize: 34,
              lineHeight: 1.02,
              fontWeight: 900,
              color: '#fff',
              letterSpacing: '-0.04em',
              textWrap: 'balance',
            }}>
              {liveText}
            </div>
          )}
          {autoMode && recentText && (
            <div style={{
              fontSize: 18,
              lineHeight: 1.25,
              fontWeight: 600,
              color: 'rgba(255,255,255,0.68)',
              letterSpacing: '-0.02em',
              marginTop: liveText ? 8 : 0,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {recentText}
            </div>
          )}
          {!autoMode && recentText && !liveText && (
            <div style={{
              fontSize: 24, lineHeight: 1.15, fontWeight: 800,
              color: 'rgba(255,255,255,0.55)',
              letterSpacing: '-0.03em',
            }}>
              {recentText}
            </div>
          )}
          {!autoMode && recentText && liveText && (
            <div style={{
              fontSize: 20, lineHeight: 1.1, fontWeight: 800,
              color: 'rgba(255,255,255,0.34)',
              letterSpacing: '-0.03em',
              marginBottom: 6,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {recentText}
            </div>
          )}
          {!autoMode && liveText && (
            <div style={{
              fontSize: 34, lineHeight: 1.02, fontWeight: 900,
              color: '#fff',
              letterSpacing: '-0.04em',
              textWrap: 'balance',
            }}>
              {liveText}
            </div>
          )}
        </div>
      )}

      {autoMode ? (
        /* ── Auto-detect: pulsing "listening" indicator ── */
        <>
          <div style={{ position: 'relative', width: 112, height: 112, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {/* Outer pulse ring */}
            {!muted && (
              <div style={{
                position: 'absolute', inset: -12, borderRadius: '50%',
                border: '2px solid rgba(255,255,255,0.35)',
                animation: 'pulse-ring 2.5s ease-out infinite',
              }} />
            )}
            {/* Main circle */}
            <div style={{
              width: 112, height: 112, borderRadius: '50%',
              background: muted ? 'rgba(255,255,255,0.1)' : 'rgba(255,255,255,0.2)',
              border: `3px solid ${muted ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.7)'}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              transition: 'all 0.25s',
            }}>
              <span className="material-symbols-outlined" style={{ fontSize: 48, color: muted ? 'rgba(255,255,255,0.4)' : '#fff' }}>
                {muted ? 'mic_off' : 'mic'}
              </span>
            </div>
          </div>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,0.8)' }}>
            {muted ? 'Muted' : 'Listening automatically…'}
          </div>
          <button
            onClick={onToggleMute}
            style={{
              padding: '8px 20px', borderRadius: 8, fontWeight: 600, fontSize: 12,
              background: 'rgba(255,255,255,0.15)', border: '1px solid rgba(255,255,255,0.4)',
              color: '#fff', cursor: 'pointer',
            }}
          >
            {muted ? '🎤 Unmute' : '🔇 Mute'}
          </button>
        </>
      ) : (
        /* ── PTT: hold-to-speak button ── */
        <>
          <button
            onPointerDown={onStart}
            onPointerUp={onStop}
            onPointerLeave={onStop}
            style={{
              width: 128, height: 128, borderRadius: '50%',
              background: talking ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.12)',
              border: `3px solid ${talking ? '#fff' : 'rgba(255,255,255,0.45)'}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              cursor: 'pointer', touchAction: 'none', transition: 'all 0.12s',
              transform: talking ? 'scale(0.94)' : 'scale(1)',
              boxShadow: talking ? 'inset 0 4px 16px rgba(0,0,0,0.25)' : '0 6px 24px rgba(0,0,0,0.12)',
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 52, color: '#fff' }}>
              {talking ? 'mic' : 'mic_off'}
            </span>
          </button>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'rgba(255,255,255,0.85)' }}>
            {talking ? '🔴 Transmitting…' : 'Hold to speak'}
          </div>
        </>
      )}

      {onAudioMount && <audio ref={audioRefCb} autoPlay playsInline style={{ display: 'none' }} />}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
function TranslateJoinPage({ sessionId }) {
  const [sessionInfo, setSessionInfo] = useState(null);
  const [loadError,   setLoadError]   = useState('');

  // Setup form
  const [deviceMode, setDeviceMode] = useState('two-device'); // 'two-device' | 'same-device'
  const [inputMode,  setInputMode]  = useState('ptt');        // 'ptt' | 'asr'
  const [personA,    setPersonA]    = useState({ name: '', language: 'en' });
  const [personB,    setPersonB]    = useState({ name: '', language: 'zh' });

  // Phase
  const [phase,    setPhase]    = useState('setup');
  const [errorMsg, setErrorMsg] = useState('');

  // Talk / mute state
  const [talkingA,  setTalkingA]  = useState(false);
  const [talkingB,  setTalkingB]  = useState(false);
  const [asrMuted,  setAsrMuted]  = useState(false);
  const [audioLvl,  setAudioLvl]  = useState(0);
  const [sessionTranscript, setSessionTranscript] = useState([]);
  const [liveTranscripts, setLiveTranscripts] = useState({});
  const [livePreviews, setLivePreviews] = useState({});

  // WebRTC refs
  const pcARef  = useRef(null);
  const pcBRef  = useRef(null);
  const trackARef = useRef(null);
  const trackBRef = useRef(null);

  // Buffered incoming streams
  const inStreamA = useRef(null);
  const inStreamB = useRef(null);

  // ── Ref callbacks — applied immediately when element mounts ──────────────
  const onAudioAMount = useCallback(node => {
    if (node && inStreamA.current) { node.srcObject = inStreamA.current; node.play().catch(() => {}); }
  }, []);
  const onAudioBMount = useCallback(node => {
    if (node && inStreamB.current) { node.srcObject = inStreamB.current; node.play().catch(() => {}); }
  }, []);
  const audioARefCb = useCallback(node => {
    if (node && inStreamA.current) { node.srcObject = inStreamA.current; node.play().catch(() => {}); }
  }, []);

  // ── Load session info ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!sessionId) return;
    fetch(`/api/translation/session/${sessionId}`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => {
        setSessionInfo(data);
        setSessionTranscript(data.transcript || []);
        setLiveTranscripts(data.live_transcripts || {});
        setLivePreviews(data.live_previews || {});
      })
      .catch(() => setLoadError('Session not found or has ended.'));
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || phase !== 'connected') return;
    let polling = true;

    const pollEvents = async () => {
      try {
        const res = await fetch(`/api/translation/poll?session_id=${sessionId}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.events?.length) {
          setSessionTranscript(prev => {
            const next = [...prev];
            for (const ev of data.events) {
              if (ev.type === 'turn') next.push(ev);
            }
            return next;
          });
          setLiveTranscripts(prev => {
            const next = { ...prev };
            for (const ev of data.events) {
              if (ev.type === 'live_transcript') next[ev.speaker] = ev;
              if (ev.type === 'turn' && ev.speaker) delete next[ev.speaker];
            }
            return next;
          });
          setLivePreviews(prev => {
            const next = { ...prev };
            for (const ev of data.events) {
              if (ev.type === 'live_preview') next[ev.target_lang] = ev;
              if (ev.type === 'turn' && ev.translated_lang) delete next[ev.translated_lang];
            }
            return next;
          });
        }
      } catch (err) {
        console.error('Translation poll failed', err);
      } finally {
        if (polling) setTimeout(pollEvents, 700);
      }
    };

    pollEvents();
    return () => { polling = false; };
  }, [sessionId, phase]);

  // ── ASR audio level ring (two-device / auto-mode from this component) ─────
  useEffect(() => {
    if (phase !== 'connected' || inputMode !== 'asr') return;
    const track = trackARef.current;
    if (!track) return;
    let rafId, audioCtx, source, analyser;
    try {
      audioCtx = new AudioContext();
      source   = audioCtx.createMediaStreamSource(new MediaStream([track]));
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      const buf = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteFrequencyData(buf);
        setAudioLvl(Math.min(100, buf.reduce((a, b) => a + b, 0) / buf.length * 2.5));
        rafId = requestAnimationFrame(tick);
      };
      rafId = requestAnimationFrame(tick);
    } catch (_) {}
    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      if (source) source.disconnect();
      if (audioCtx) audioCtx.close();
    };
  }, [phase, inputMode]);

  // ── Connect handler ───────────────────────────────────────────────────────
  const handleConnect = async (e) => {
    e.preventDefault();
    setPhase('connecting');
    try {
      const { iceServers } = await fetch('/api/ice').then(r => r.json());

      if (deviceMode === 'same-device' && inputMode === 'asr') {
        // ── Single-connection auto language-detect mode ──────────────────
        const rawStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        const track = rawStream.getAudioTracks()[0];
        track.enabled = true; // always on — auto VAD
        trackARef.current = track;

        const pcA = makePC(iceServers, s => {
          inStreamA.current = s;
          // Both audio mount callbacks reference inStreamA — plays through any mounted element
        });
        pcARef.current = pcA;

        await doAutoOffer(pcA, track, sessionId, personA.language, personB.language);

      } else if (deviceMode === 'same-device' && inputMode === 'ptt') {
        // ── Two connections, PTT mic routing ────────────────────────────
        const rawStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        const rawTrack = rawStream.getAudioTracks()[0];
        const tA = rawTrack;
        const tB = rawTrack.clone();
        tA.enabled = false;
        tB.enabled = false;
        trackARef.current = tA;
        trackBRef.current = tB;

        const pcA = makePC(iceServers, s => {
          inStreamA.current = s;
        });
        const pcB = makePC(iceServers, s => {
          inStreamB.current = s;
        });
        pcARef.current = pcA;
        pcBRef.current = pcB;

        await doOffer(pcA, tA, sessionId, personA.name, personA.language);
        await doOffer(pcB, tB, sessionId, personB.name, personB.language);

      } else {
        // ── Two-device: single participant ──────────────────────────────
        const rawStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        const track = rawStream.getAudioTracks()[0];
        track.enabled = inputMode === 'asr'; // ASR → on; PTT → start muted
        trackARef.current = track;

        const pcA = makePC(iceServers, s => {
          inStreamA.current = s;
        });
        pcARef.current = pcA;

        await doOffer(pcA, track, sessionId, personA.name, personA.language);
      }

      setPhase('connected');
    } catch (err) {
      console.error(err);
      setPhase('error');
      setErrorMsg(err.message || 'Failed to connect. Please try again.');
    }
  };

  // ── Talk controls ─────────────────────────────────────────────────────────
  const startTalkA = useCallback(() => {
    setTalkingA(true); setTalkingB(false);
    if (trackARef.current) trackARef.current.enabled = true;
    if (trackBRef.current) trackBRef.current.enabled = false;
  }, []);
  const stopTalkA = useCallback(() => {
    setTalkingA(false);
    if (trackARef.current) trackARef.current.enabled = false;
  }, []);
  const startTalkB = useCallback(() => {
    setTalkingB(true); setTalkingA(false);
    if (trackBRef.current) trackBRef.current.enabled = true;
    if (trackARef.current) trackARef.current.enabled = false;
  }, []);
  const stopTalkB = useCallback(() => {
    setTalkingB(false);
    if (trackBRef.current) trackBRef.current.enabled = false;
  }, []);
  const toggleAsrMute = useCallback(() => {
    setAsrMuted(prev => {
      const next = !prev;
      if (trackARef.current) trackARef.current.enabled = !next;
      return next;
    });
  }, []);
  const handleLeave = () => {
    [pcARef, pcBRef].forEach(r => r.current?.close());
    [trackARef, trackBRef].forEach(r => r.current?.stop());
    window.location.reload();
  };

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER: load error
  // ─────────────────────────────────────────────────────────────────────────
  if (loadError) return (
    <div style={S.center}>
      <div style={S.card}>
        <span className="material-symbols-outlined" style={{ fontSize: 48, color: 'var(--grc-red)', display: 'block', marginBottom: 16 }}>error</span>
        <h2 style={{ fontWeight: 700, marginBottom: 8 }}>Session Not Found</h2>
        <p style={{ color: '#666', marginBottom: 20 }}>{loadError}</p>
        <button onClick={() => window.location.reload()} style={{ padding: '10px 24px', background: 'var(--grc-teal)', color: '#fff', borderRadius: 8, fontWeight: 600 }}>Go Back</button>
      </div>
    </div>
  );

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER: setup form
  // ─────────────────────────────────────────────────────────────────────────
  if (phase === 'setup') {
    const teal = 'var(--grc-teal)';
    const red  = 'var(--grc-red)';
    const isSameDeviceASR = deviceMode === 'same-device' && inputMode === 'asr';
    return (
      <div style={{ ...S.center, alignItems: 'flex-start', padding: '48px 24px' }}>
        <div style={{ background: '#fff', padding: 40, borderRadius: 16, border: '1px solid #E8E9EB', width: '100%', maxWidth: 520 }}>

          <h1 style={{ fontSize: 22, fontWeight: 800, marginBottom: 4 }}>Join Translation Session</h1>
          <p style={{ color: '#888', fontSize: 13, marginBottom: 32 }}>
            {sessionInfo ? sessionInfo.topic : 'Loading session…'}
          </p>

          <form onSubmit={handleConnect} style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>

            {/* ── Device mode ── */}
            <div>
              <label style={S.label}>Device Setup</label>
              <div style={{ display: 'flex', gap: 10 }}>
                {[
                  { value: 'two-device',  icon: 'smartphone', title: 'My device',    desc: 'Joining on this device only' },
                  { value: 'same-device', icon: 'devices',    title: 'Face-to-face', desc: 'Two people, one device on a table' },
                ].map(opt => {
                  const active = deviceMode === opt.value;
                  return (
                    <button key={opt.value} type="button"
                      onClick={() => setDeviceMode(opt.value)}
                      style={S.modeBtn(active, teal)}>
                      <span className="material-symbols-outlined" style={S.modeBtnIcon(active, 'var(--grc-teal-dark)')}>{opt.icon}</span>
                      <div style={S.modeBtnTitle(active, 'var(--grc-teal-dark)')}>{opt.title}</div>
                      <div style={S.modeBtnDesc}>{opt.desc}</div>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* ── Input mode ── */}
            <div>
              <label style={S.label}>Microphone Control</label>
              <div style={{ display: 'flex', gap: 10 }}>
                {[
                  { value: 'ptt', icon: 'touch_app', title: 'Hold to speak',    desc: deviceMode === 'same-device' ? 'Each person presses their button' : 'Press & hold to transmit audio' },
                  { value: 'asr', icon: 'mic',       title: 'Always listening', desc: deviceMode === 'same-device' ? 'Auto-detects language, no buttons' : 'Speak naturally — mic stays open' },
                ].map(opt => {
                  const active = inputMode === opt.value;
                  return (
                    <button key={opt.value} type="button" onClick={() => setInputMode(opt.value)}
                      style={S.modeBtn(active, red)}>
                      <span className="material-symbols-outlined" style={S.modeBtnIcon(active, red)}>{opt.icon}</span>
                      <div style={S.modeBtnTitle(active, red)}>{opt.title}</div>
                      <div style={S.modeBtnDesc}>{opt.desc}</div>
                    </button>
                  );
                })}
              </div>

              {/* Context-aware tip */}
              {isSameDeviceASR && (
                <div style={{ marginTop: 10, padding: '10px 14px', background: '#E8F8F7', borderRadius: 8, fontSize: 12, color: '#006663', border: '1px solid var(--grc-teal-light)' }}>
                  ✨ <strong>Recommended for meetings:</strong> Place device on the table. Speak naturally in your language — the device auto-detects Chinese or English and plays the translation instantly. No buttons needed.
                </div>
              )}
              {deviceMode === 'same-device' && inputMode === 'ptt' && (
                <div style={{ marginTop: 10, padding: '10px 14px', background: '#F4F5F6', borderRadius: 8, fontSize: 12, color: '#555', border: '1px solid #E8E9EB' }}>
                  📱 Each person presses their half of the screen to speak. Names are shown on screen so each person knows their side.
                </div>
              )}
              {deviceMode === 'two-device' && inputMode === 'asr' && (
                <div style={{ marginTop: 10, padding: '8px 12px', background: '#FFF8E7', borderRadius: 8, fontSize: 12, color: '#666', border: '1px solid #FFE58F' }}>
                  💡 Use headphones to prevent audio feedback in always-listening mode.
                </div>
              )}
            </div>

            {/* ── Language pair (same-device ASR: no names needed, just languages) ── */}
            {isSameDeviceASR ? (
              <div>
                <label style={S.label}>Language Pair</label>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                  <select value={personA.language} onChange={e => setPersonA({ ...personA, language: e.target.value })}
                    style={{ ...S.input, flex: 1 }}>
                    {LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.flag} {l.label}</option>)}
                  </select>
                  <span className="material-symbols-outlined" style={{ fontSize: 22, color: '#aaa', flexShrink: 0 }}>swap_horiz</span>
                  <select value={personB.language} onChange={e => setPersonB({ ...personB, language: e.target.value })}
                    style={{ ...S.input, flex: 1 }}>
                    {LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.flag} {l.label}</option>)}
                  </select>
                </div>
                <div style={{ marginTop: 8, fontSize: 12, color: '#888', textAlign: 'center' }}>
                  The device auto-detects which language is spoken and translates to the other
                </div>
              </div>
            ) : (
              <>
                {/* ── Person A ── */}
                <div>
                  <label style={S.label}>
                    {deviceMode === 'same-device' ? 'Person A — bottom half' : 'Your Name'}
                  </label>
                  <div style={{ display: 'flex', gap: 10 }}>
                    <input
                      type="text" required placeholder="Full name"
                      value={personA.name} onChange={e => setPersonA({ ...personA, name: e.target.value })}
                      style={S.input}
                    />
                    <select value={personA.language} onChange={e => setPersonA({ ...personA, language: e.target.value })}
                      style={{ ...S.input, flex: '0 0 150px', width: 150 }}>
                      {LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.flag} {l.label}</option>)}
                    </select>
                  </div>
                </div>

                {/* ── Person B (same-device PTT only) ── */}
                {deviceMode === 'same-device' && (
                  <div>
                    <label style={S.label}>Person B — top half</label>
                    <div style={{ display: 'flex', gap: 10 }}>
                      <input
                        type="text" required placeholder="Full name"
                        value={personB.name} onChange={e => setPersonB({ ...personB, name: e.target.value })}
                        style={S.input}
                      />
                      <select value={personB.language} onChange={e => setPersonB({ ...personB, language: e.target.value })}
                        style={{ ...S.input, flex: '0 0 150px', width: 150 }}>
                        {LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.flag} {l.label}</option>)}
                      </select>
                    </div>
                  </div>
                )}
              </>
            )}

            <button
              type="submit"
              disabled={!sessionInfo && !!sessionId}
              style={{
                padding: '14px', background: 'var(--grc-teal)', color: '#fff',
                borderRadius: 8, fontWeight: 700, fontSize: 15, marginTop: 4,
                opacity: (!sessionInfo && !!sessionId) ? 0.6 : 1,
                cursor: (!sessionInfo && !!sessionId) ? 'not-allowed' : 'pointer',
              }}
            >
              {isSameDeviceASR
                ? '✨ Start Auto-Translation'
                : deviceMode === 'same-device'
                  ? '🤝 Start Face-to-Face Session'
                  : '→ Join Session'}
            </button>
          </form>
        </div>
      </div>
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER: connecting
  // ─────────────────────────────────────────────────────────────────────────
  if (phase === 'connecting') return (
    <div style={S.center}>
      <div style={{ textAlign: 'center' }}>
        <span className="material-symbols-outlined" style={{ fontSize: 56, color: 'var(--grc-teal)', display: 'block', marginBottom: 16, animation: 'blink 1.4s infinite' }}>wifi</span>
        <div style={{ fontWeight: 700, fontSize: 17, color: '#333' }}>Connecting…</div>
        <div style={{ fontSize: 13, color: '#888', marginTop: 6 }}>Establishing secure WebRTC session</div>
      </div>
    </div>
  );

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER: error
  // ─────────────────────────────────────────────────────────────────────────
  if (phase === 'error') return (
    <div style={S.center}>
      <div style={S.card}>
        <span className="material-symbols-outlined" style={{ fontSize: 48, color: 'var(--grc-red)', display: 'block', marginBottom: 16 }}>error</span>
        <h2 style={{ fontWeight: 700, marginBottom: 8 }}>Connection Failed</h2>
        <p style={{ color: '#666', marginBottom: 20 }}>{errorMsg}</p>
        <button onClick={() => window.location.reload()} style={{ padding: '10px 24px', background: 'var(--grc-teal)', color: '#fff', borderRadius: 8, fontWeight: 600 }}>Try Again</button>
      </div>
    </div>
  );

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER: connected — SAME-DEVICE split view (PTT or auto)
  // ─────────────────────────────────────────────────────────────────────────
  if (phase === 'connected' && deviceMode === 'same-device') {
    const langA   = LANGUAGES.find(l => l.value === personA.language) || LANGUAGES[0];
    const langB   = LANGUAGES.find(l => l.value === personB.language) || LANGUAGES[1];
    const isAuto  = inputMode === 'asr';
    const previewEvents = Object.values(livePreviews);
    const transcriptEvents = Object.values(liveTranscripts);
    const speakerARecent = [...sessionTranscript].reverse().find(t => matchesLang(t.original_lang, personA.language));
    const speakerBRecent = [...sessionTranscript].reverse().find(t => matchesLang(t.original_lang, personB.language));
    const speakerALive = isAuto
      ? previewEvents.find(t => matchesLang(t.speaker, personA.language))
      : transcriptEvents.find(t => matchesLang(t.original_lang, personA.language));
    const speakerBLive = isAuto
      ? previewEvents.find(t => matchesLang(t.speaker, personB.language))
      : transcriptEvents.find(t => matchesLang(t.original_lang, personB.language));

    // Hard mirror rule:
    // speech detected from panel A always renders on panel B,
    // speech detected from panel B always renders on panel A.
    const recentForA = speakerBRecent;
    const recentForB = speakerARecent;
    const liveA = speakerBLive;
    const liveB = speakerALive;

    return (
      <div style={{ height: '100dvh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>

        {/* Person B — rotated 180° for the person across the table */}
        <div style={{ flex: 1, transform: 'rotate(180deg)', display: 'flex', flexDirection: 'column' }}>
          <SameDevicePanel
            name={isAuto ? '' : personB.name}
            lang={langB}
            talking={talkingB}
            onStart={startTalkB}
            onStop={stopTalkB}
            color="var(--grc-teal)"
            darkColor="var(--grc-teal-dark)"
            onAudioMount={isAuto ? null : onAudioBMount}  // auto mode: no element here (avoid double-play)
            autoMode={isAuto}
            muted={asrMuted}
            onToggleMute={toggleAsrMute}
            liveText={tailWords(isAuto ? (liveA?.text || recentForA?.translated) : (liveA ? liveA.original : recentForA?.translated), 10)}
            recentText={tailWords(isAuto ? recentForA?.original : recentForA?.original, 12)}
          />
        </div>

        {/* Divider */}
        <div style={{ height: 8, background: '#111', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
          <div style={{ width: 4, height: 4, borderRadius: '50%', background: '#444' }} />
          <div style={{ width: 36, height: 2, borderRadius: 2, background: '#333' }} />
          <div style={{ width: 4, height: 4, borderRadius: '50%', background: '#444' }} />
        </div>

        {/* Person A — normal orientation */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <SameDevicePanel
            name={isAuto ? '' : personA.name}
            lang={langA}
            talking={talkingA}
            onStart={startTalkA}
            onStop={stopTalkA}
            color="var(--grc-red)"
            darkColor="var(--grc-red-dark)"
            onAudioMount={onAudioAMount}  // single audio element for both PTT and auto mode
            autoMode={isAuto}
            muted={asrMuted}
            onToggleMute={toggleAsrMute}
            liveText={tailWords(isAuto ? (liveB?.text || recentForB?.translated) : (liveB ? liveB.original : recentForB?.translated), 10)}
            recentText={tailWords(isAuto ? recentForB?.original : recentForB?.original, 12)}
          />
        </div>

      </div>
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER: connected — TWO-DEVICE view
  // ─────────────────────────────────────────────────────────────────────────
  if (phase === 'connected' && deviceMode === 'two-device') {
    const lang = LANGUAGES.find(l => l.value === personA.language) || LANGUAGES[0];
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F4F5F6', userSelect: 'none' }}>
        <div style={{ background: '#fff', padding: 48, borderRadius: 16, textAlign: 'center', border: '1px solid #E8E9EB', width: 420 }}>

          {inputMode === 'ptt' ? (
            /* ── PTT Mode ─────────────────────────────────────────── */
            <>
              <div style={{
                width: 84, height: 84, borderRadius: '50%',
                background: talkingA ? 'var(--grc-teal)' : 'var(--grc-teal-light)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                margin: '0 auto 24px', transition: 'all 0.2s',
                transform: talkingA ? 'scale(1.12)' : 'scale(1)',
                boxShadow: talkingA ? '0 0 0 10px rgba(0,169,165,0.18)' : 'none',
              }}>
                <span className="material-symbols-outlined" style={{ fontSize: 40, color: talkingA ? '#fff' : 'var(--grc-teal-dark)' }}>
                  {talkingA ? 'mic' : 'mic_off'}
                </span>
              </div>
              <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 6 }}>Connected</h1>
              <p style={{ color: '#888', marginBottom: 4, fontSize: 14 }}>{sessionInfo?.topic}</p>
              <p style={{ color: '#aaa', fontSize: 13, marginBottom: 28 }}>
                {lang.flag} <strong>{personA.name}</strong> · {lang.label}
              </p>
              <div style={{ marginBottom: 16, fontSize: 13, fontWeight: 600, color: talkingA ? 'var(--grc-teal-dark)' : '#aaa' }}>
                {talkingA ? '🔴 Transmitting…' : 'Muted — hold to speak'}
              </div>
              <button
                onPointerDown={startTalkA}
                onPointerUp={stopTalkA}
                onPointerLeave={stopTalkA}
                style={{
                  padding: '18px 32px',
                  background: talkingA ? 'var(--grc-teal-dark)' : 'var(--grc-teal)',
                  color: '#fff', borderRadius: 10, fontWeight: 700, fontSize: 16,
                  width: '100%', marginBottom: 14, touchAction: 'none',
                  boxShadow: talkingA ? 'inset 0 4px 8px rgba(0,0,0,0.2)' : '0 4px 16px rgba(0,169,165,0.35)',
                  transform: talkingA ? 'translateY(2px) scale(0.99)' : 'none',
                  transition: 'all 0.1s',
                }}
              >
                {talkingA ? '🎙 Release to Mute' : '🎤 Hold to Speak'}
              </button>
            </>
          ) : (
            /* ── ASR Mode ─────────────────────────────────────────── */
            <>
              <div style={{ width: 84, height: 84, margin: '0 auto 24px', position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <div style={{
                  position: 'absolute', inset: -10, borderRadius: '50%',
                  background: asrMuted ? 'transparent' : `rgba(0,169,165,${Math.max(0, audioLvl / 280)})`,
                  transform: `scale(${asrMuted ? 1 : 1 + audioLvl / 200})`,
                  transition: 'transform 0.08s, background 0.08s',
                }} />
                <div style={{
                  position: 'absolute', inset: 0, borderRadius: '50%',
                  background: asrMuted ? 'var(--grc-red-light)' : 'var(--grc-teal-light)',
                  border: `3px solid ${asrMuted ? 'var(--grc-red)' : 'var(--grc-teal)'}`,
                  transition: 'all 0.2s',
                }} />
                <span className="material-symbols-outlined" style={{ fontSize: 38, color: asrMuted ? 'var(--grc-red)' : 'var(--grc-teal-dark)', position: 'relative' }}>
                  {asrMuted ? 'mic_off' : 'mic'}
                </span>
              </div>
              <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 6 }}>Connected</h1>
              <p style={{ color: '#888', marginBottom: 4, fontSize: 14 }}>{sessionInfo?.topic}</p>
              <p style={{ color: '#aaa', fontSize: 13, marginBottom: 20 }}>
                {lang.flag} <strong>{personA.name}</strong> · {lang.label}
              </p>
              <div style={{
                marginBottom: 20, padding: '10px 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
                background: asrMuted ? 'var(--grc-red-light)' : 'var(--grc-teal-light)',
                color: asrMuted ? 'var(--grc-red)' : 'var(--grc-teal-dark)',
              }}>
                {asrMuted ? '🔇 Microphone muted' : '🎙 Listening — speak naturally'}
              </div>
              <button onClick={toggleAsrMute} style={{
                padding: '14px 32px',
                background: asrMuted ? 'var(--grc-red)' : '#F4F5F6',
                color: asrMuted ? '#fff' : '#555',
                borderRadius: 10, fontWeight: 700, fontSize: 15,
                width: '100%', marginBottom: 14,
                border: `1px solid ${asrMuted ? 'var(--grc-red)' : '#E8E9EB'}`,
                transition: 'all 0.15s',
              }}>
                {asrMuted ? '🎤 Unmute Microphone' : '🔇 Mute Microphone'}
              </button>
            </>
          )}

          <button onClick={handleLeave} style={{ padding: '10px 24px', background: 'transparent', color: 'var(--grc-red)', border: '1px solid var(--grc-red)', borderRadius: 8, fontWeight: 600, width: '100%' }}>
            Leave Session
          </button>

          <audio ref={audioARefCb} autoPlay playsInline style={{ display: 'none' }} />
        </div>
      </div>
    );
  }

  return null;
}

Object.assign(window, { TranslateJoinPage });
