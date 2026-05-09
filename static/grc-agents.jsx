// Agents page — Georges River Council (with live WebRTC call panel)

const { useState: useAgentState, useEffect: useAgentEffect, useRef: useAgentRef, useCallback: useAgentCb } = React;

const AGENT_PROFILES = [
  {
    name: 'Aria v2',
    role: 'General Enquiries',
    icon: 'support_agent',
    status: 'Live',
    latency: '210ms',
    latencyPct: 90,
    llm: 'GPT-4o',
    stt: 'Deepgram Nova-2',
    tts: 'ElevenLabs Turbo',
    calls: 82,
    csat: '94%',
  },
  {
    name: 'George v1',
    role: 'Development & Planning',
    icon: 'domain',
    status: 'Live',
    latency: '280ms',
    latencyPct: 76,
    llm: 'Claude 3.5 Sonnet',
    stt: 'Deepgram Nova-2',
    tts: 'Azure Neural',
    calls: 45,
    csat: '88%',
  },
  {
    name: 'River Bot',
    role: 'Waste & Environment',
    icon: 'recycling',
    status: 'Idle',
    latency: '—',
    latencyPct: 0,
    llm: 'GPT-4o Mini',
    stt: 'OpenAI Whisper',
    tts: 'Google WaveNet',
    calls: 0,
    csat: '—',
  },
];

// ── WebRTC call hook ─────────────────────────────────────────────────────────

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

function useAgentCall() {
  const [callState, setCallState] = useAgentState('idle'); // idle | connecting | connected | error
  const [transcript, setTranscript] = useAgentState([]);
  const [pttActive, setPttActive] = useAgentState(false);
  const [statusMsg, setStatusMsg] = useAgentState('');
  const [micMode, setMicModeState] = useAgentState('hold'); // 'hold' | 'always'

  const pcRef        = useAgentRef(null);
  const dcRef        = useAgentRef(null);
  const pcIdRef      = useAgentRef(null);
  const localTrackRef = useAgentRef(null);
  const localStreamRef = useAgentRef(null);
  const remoteAudioRef = useAgentRef(null);
  const pingRef      = useAgentRef(null);
  const discTimerRef = useAgentRef(null);
  const activeCallRef = useAgentRef(0);
  const connectingRef = useAgentRef(false);
  const micModeRef = useAgentRef('hold');

  const addLine = (speaker, text) =>
    setTranscript(prev => [...prev, { speaker, text, id: Date.now() + Math.random() }]);

  const cleanup = useAgentCb((opts = {}) => {
    const { notifyServer = false, resetTranscript = false, bumpGeneration = false, updateState = true } = opts;
    if (bumpGeneration) activeCallRef.current += 1;

    const pc = pcRef.current;
    const pcId = pcIdRef.current;
    const stream = localStreamRef.current;
    const audio = remoteAudioRef.current;

    if (pingRef.current)      { clearInterval(pingRef.current); pingRef.current = null; }
    if (discTimerRef.current) { clearTimeout(discTimerRef.current); discTimerRef.current = null; }
    if (dcRef.current) {
      dcRef.current.onopen = null;
      dcRef.current.onmessage = null;
      try { dcRef.current.close(); } catch (_) {}
    }
    if (pc) {
      pc.ontrack = null;
      pc.onconnectionstatechange = null;
      try { pc.getSenders().forEach(s => s.track && s.track.stop()); } catch (_) {}
      try { pc.getReceivers().forEach(r => r.track && r.track.stop()); } catch (_) {}
      try { pc.close(); } catch (_) {}
    }
    if (stream) {
      try { stream.getTracks().forEach(t => t.stop()); } catch (_) {}
    }
    if (audio) {
      try {
        audio.pause();
        audio.srcObject = null;
      } catch (_) {}
    }
    if (notifyServer && pcId) fireAndForgetHangup(pcId);

    pcRef.current        = null;
    dcRef.current        = null;
    localTrackRef.current = null;
    localStreamRef.current = null;
    remoteAudioRef.current = null;
    pcIdRef.current      = null;
    connectingRef.current = false;
    if (updateState) {
      setPttActive(false);
      if (resetTranscript) setTranscript([]);
    }
  }, []);

  const connect = useAgentCb(async () => {
    if (connectingRef.current || callState === 'connecting' || callState === 'connected') return;
    connectingRef.current = true;

    const stalePcId = pcIdRef.current;
    if (stalePcId) {
      cleanup({ notifyServer: true, resetTranscript: true, bumpGeneration: true });
      await hangupPcId(stalePcId);
    }

    const callToken = activeCallRef.current + 1;
    activeCallRef.current = callToken;
    setCallState('connecting');
    setStatusMsg('Requesting microphone…');
    setTranscript([]);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });

      setStatusMsg('Fetching ICE servers…');
      if (activeCallRef.current !== callToken) {
        stream.getTracks().forEach(t => t.stop());
        return;
      }
      localStreamRef.current = stream;

      let iceServers = [{ urls: 'stun:stun.l.google.com:19302' }];
      try {
        const iceRes = await fetch('/api/ice');
        if (iceRes.ok) {
          const body = await iceRes.json();
          if (body.iceServers?.length) iceServers = body.iceServers;
        }
      } catch (_) {}

      const pc = new RTCPeerConnection({ iceServers, iceTransportPolicy: 'relay' });
      pcRef.current = pc;

      stream.getTracks().forEach(t => pc.addTrack(t, stream));
      localTrackRef.current = stream.getAudioTracks()[0];
      if (localTrackRef.current) {
        localTrackRef.current.enabled = micModeRef.current === 'always';
        if (micModeRef.current === 'always') setPttActive(true);
      }

      pc.addTransceiver('audio', { direction: 'recvonly' });

      const dc = pc.createDataChannel('data', { ordered: true });
      dcRef.current = dc;

      dc.onopen = () => {
        if (activeCallRef.current !== callToken) return;
        pingRef.current = setInterval(() => {
          if (dc.readyState === 'open') dc.send('ping');
        }, 2000);
      };

      dc.onmessage = (e) => {
        if (activeCallRef.current !== callToken) return;
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === 'transcript') {
            addLine(msg.role === 'user' ? 'user' : 'agent', msg.text);
          }
        } catch (_) {}
      };

      pc.ontrack = (event) => {
        if (activeCallRef.current !== callToken) return;
        if (remoteAudioRef.current) {
          remoteAudioRef.current.pause();
          remoteAudioRef.current.srcObject = null;
        }
        const audio = new Audio();
        audio.srcObject = event.streams[0];
        audio.autoplay = true;
        remoteAudioRef.current = audio;
      };

      pc.onconnectionstatechange = () => {
        if (activeCallRef.current !== callToken) return;
        const s = pc.connectionState;
        if (s === 'connected') {
          if (discTimerRef.current) { clearTimeout(discTimerRef.current); discTimerRef.current = null; }
          setCallState('connected');
          setStatusMsg('Neural link established');
          connectingRef.current = false;
        } else if (s === 'failed') {
          cleanup({ notifyServer: true, resetTranscript: true, bumpGeneration: true });
          setCallState('error');
          setStatusMsg('Connection failed');
        } else if (s === 'disconnected') {
          if (!discTimerRef.current) {
            discTimerRef.current = setTimeout(() => {
              if (activeCallRef.current !== callToken) return;
              cleanup({ notifyServer: true, resetTranscript: true, bumpGeneration: true });
              setCallState('idle');
              setStatusMsg('');
            }, 5000);
          }
        }
      };

      // Create offer
      setStatusMsg('Negotiating…');
      await pc.setLocalDescription(await pc.createOffer());
      await new Promise(resolve => {
        const check = () => { if (pc.iceGatheringState === 'complete') resolve(); };
        pc.addEventListener('icegatheringstatechange', check);
        setTimeout(resolve, 1000);
      });

      const pcIdSuffix = window.crypto && window.crypto.randomUUID
        ? window.crypto.randomUUID()
        : `${Date.now()}-${Math.random()}`;
      const pcId = `grc-agents-${pcIdSuffix}`;
      pcIdRef.current = pcId;

      const res = await fetch('/api/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sdp: pc.localDescription.sdp,
          type: pc.localDescription.type,
          pc_id: pcId,
          stt: 'elevenlabs',
          llm: 'mistral',
          tts: 'elevenlabs',
          use_kg: true,
          mode: 'indiv',
        }),
      });

      if (!res.ok) throw new Error(`Offer failed (${res.status})`);
      const answer = await res.json();
      if (activeCallRef.current !== callToken) {
        fireAndForgetHangup(answer.pc_id || pcId);
        try { pc.close(); } catch (_) {}
        return;
      }
      pcIdRef.current = answer.pc_id || pcId;
      await pc.setRemoteDescription(answer);
      connectingRef.current = false;

    } catch (err) {
      console.error('[AgentCall]', err);
      cleanup({ notifyServer: true, resetTranscript: true, bumpGeneration: true });
      setCallState('error');
      setStatusMsg(`Error: ${err.message}`);
    }
  }, [callState, cleanup]);

  const disconnect = useAgentCb(() => {
    cleanup({ notifyServer: true, resetTranscript: true, bumpGeneration: true });
    setCallState('idle');
    setStatusMsg('');
    setTranscript([]);
  }, [cleanup]);

  useAgentEffect(() => {
    const handleUnload = () => {
      const pcId = pcIdRef.current;
      cleanup({ notifyServer: true, bumpGeneration: true, updateState: false });
      if (pcId) fireAndForgetHangup(pcId);
    };
    window.addEventListener('pagehide', handleUnload);
    window.addEventListener('beforeunload', handleUnload);
    return () => {
      window.removeEventListener('pagehide', handleUnload);
      window.removeEventListener('beforeunload', handleUnload);
      cleanup({ notifyServer: true, resetTranscript: true, bumpGeneration: true, updateState: false });
    };
  }, [cleanup]);

  const startPTT = useAgentCb(() => {
    if (localTrackRef.current) { localTrackRef.current.enabled = true; setPttActive(true); }
  }, []);

  const stopPTT = useAgentCb(() => {
    if (localTrackRef.current) { localTrackRef.current.enabled = false; setPttActive(false); }
  }, []);

  const toggleMicMode = useAgentCb(() => {
    const next = micModeRef.current === 'hold' ? 'always' : 'hold';
    micModeRef.current = next;
    setMicModeState(next);
    if (localTrackRef.current) {
      if (next === 'always') {
        localTrackRef.current.enabled = true;
        setPttActive(true);
      } else {
        localTrackRef.current.enabled = false;
        setPttActive(false);
      }
    }
  }, []);

  return { callState, statusMsg, transcript, pttActive, micMode, connect, disconnect, startPTT, stopPTT, toggleMicMode };
}


// ── Call Panel component ─────────────────────────────────────────────────────

function AgentCallPanel({ agent }) {
  const { callState, statusMsg, transcript, pttActive, micMode, connect, disconnect, startPTT, stopPTT, toggleMicMode } = useAgentCall();
  const transcriptRef = useAgentRef(null);

  useAgentEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [transcript]);

  const isIdle       = callState === 'idle';
  const isConnecting = callState === 'connecting';
  const isConnected  = callState === 'connected';
  const isError      = callState === 'error';

  return (
    <div style={{
      background: '#F4F5F6', borderRadius: 12, overflow: 'hidden',
      border: isConnected ? '1.5px solid #00A9A5' : isError ? '1.5px solid #C8232C' : '1.5px solid #F0F1F3',
      transition: 'border-color 0.3s'
    }}>
      {/* Panel header */}
      <div style={{
        padding: '14px 20px',
        background: isConnected ? '#D0F2F1' : isError ? '#F9E6E7' : '#EBEBEB',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        transition: 'background 0.3s'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className={`material-symbols-outlined ms-fill${isConnected ? ' live-blink' : ''}`} style={{
            fontSize: 18,
            color: isConnected ? '#00A9A5' : isError ? '#C8232C' : '#767676'
          }}>
            {isConnected ? 'radio_button_checked' : isError ? 'error' : 'phone_in_talk'}
          </span>
          <div>
            <div style={{ fontSize: 12, fontWeight: 800, fontFamily: 'Manrope', color: '#1A1A1A' }}>
              Live Agent Call — {agent.name}
            </div>
            <div style={{ fontSize: 10, color: isConnected ? '#007A77' : '#767676', fontWeight: 600 }}>
              {isConnected ? statusMsg : isConnecting ? statusMsg : isError ? statusMsg : 'GRC Unified Voice Agent'}
            </div>
          </div>
        </div>
        {/* Status badge */}
        <span style={{
          fontSize: 9, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.08em',
          color: isConnected ? '#00A9A5' : isConnecting ? '#D4860A' : isError ? '#C8232C' : '#767676',
          background: isConnected ? '#B0EAE9' : isConnecting ? '#FDF3E1' : isError ? '#F9E6E7' : '#DDDEDE',
          padding: '3px 10px', borderRadius: 5
        }}>
          {isConnected ? 'Live' : isConnecting ? 'Connecting…' : isError ? 'Error' : 'Standby'}
        </span>
      </div>

      {/* Transcript area — only shown when connected or has history */}
      {(isConnected || transcript.length > 0) && (
        <div ref={transcriptRef} style={{
          height: 160, overflowY: 'auto', padding: '14px 16px',
          background: '#fff', borderBottom: '1px solid #F0F1F3',
          display: 'flex', flexDirection: 'column', gap: 8
        }}>
          {transcript.length === 0 && isConnected && (
            <div style={{ textAlign: 'center', color: '#bbb', fontSize: 12, marginTop: 40 }}>
              {micMode === 'hold' ? 'Hold to Speak and begin…' : 'Start speaking to begin…'}
            </div>
          )}
          {transcript.map(line => (
            <div key={line.id} style={{
              display: 'flex',
              justifyContent: line.speaker === 'user' ? 'flex-end' : 'flex-start'
            }}>
              <div style={{
                maxWidth: '75%', padding: '7px 12px', borderRadius: 10, fontSize: 12, fontWeight: 500,
                background: line.speaker === 'user' ? '#C8232C' : '#F4F5F6',
                color: line.speaker === 'user' ? '#fff' : '#1A1A1A',
                borderBottomRightRadius: line.speaker === 'user' ? 2 : 10,
                borderBottomLeftRadius: line.speaker === 'agent' ? 2 : 10,
              }}>
                {line.text}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Controls */}
      <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 10, background: '#fff' }}>
        {isIdle || isError ? (
          <button onClick={connect} style={{
            flex: 1, background: '#C8232C', color: '#fff', borderRadius: 9,
            padding: '10px 0', fontSize: 12, fontWeight: 800, letterSpacing: '0.04em',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8
          }}>
            <span className="material-symbols-outlined ms-fill" style={{ fontSize: 16 }}>phone</span>
            {isError ? 'Retry Connection' : 'Connect to GRC Agent'}
          </button>
        ) : isConnecting ? (
          <button disabled style={{
            flex: 1, background: '#F4F5F6', color: '#767676', borderRadius: 9,
            padding: '10px 0', fontSize: 12, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8
          }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>hourglass_top</span>
            Connecting…
          </button>
        ) : (
          <>
            {/* Mode toggle pill */}
            <div style={{
              display: 'flex', background: '#EBEBEB', borderRadius: 8, padding: 3, gap: 2, flexShrink: 0
            }}>
              <button
                onClick={() => micMode === 'always' && toggleMicMode()}
                style={{
                  padding: '5px 9px', borderRadius: 6, fontSize: 10, fontWeight: 700,
                  letterSpacing: '0.04em', border: 'none', cursor: micMode === 'hold' ? 'default' : 'pointer',
                  background: micMode === 'hold' ? '#1A1A1A' : 'transparent',
                  color: micMode === 'hold' ? '#fff' : '#767676',
                  transition: 'all 0.15s',
                }}
              >PTT</button>
              <button
                onClick={() => micMode === 'hold' && toggleMicMode()}
                style={{
                  padding: '5px 9px', borderRadius: 6, fontSize: 10, fontWeight: 700,
                  letterSpacing: '0.04em', border: 'none', cursor: micMode === 'always' ? 'default' : 'pointer',
                  background: micMode === 'always' ? '#00A9A5' : 'transparent',
                  color: micMode === 'always' ? '#fff' : '#767676',
                  transition: 'all 0.15s',
                }}
              >Natural</button>
            </div>
            {/* Mic button — PTT or always-on */}
            {micMode === 'hold' ? (
              <button
                onMouseDown={startPTT}
                onMouseUp={stopPTT}
                onTouchStart={startPTT}
                onTouchEnd={stopPTT}
                style={{
                  flex: 1, borderRadius: 9, padding: '10px 0', fontSize: 12, fontWeight: 800,
                  letterSpacing: '0.04em', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                  background: pttActive ? '#00A9A5' : '#D0F2F1',
                  color: pttActive ? '#fff' : '#007A77',
                  border: `2px solid ${pttActive ? '#007A77' : '#B0EAE9'}`,
                  transition: 'all 0.1s', userSelect: 'none', cursor: 'pointer'
                }}>
                <span className="material-symbols-outlined ms-fill" style={{ fontSize: 18 }}>
                  {pttActive ? 'mic' : 'mic_off'}
                </span>
                {pttActive ? 'Speaking…' : 'Hold to Speak'}
              </button>
            ) : (
              <div style={{
                flex: 1, borderRadius: 9, padding: '10px 0', fontSize: 12, fontWeight: 800,
                letterSpacing: '0.04em', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                background: '#D0F2F1', color: '#007A77', border: '2px solid #B0EAE9',
              }}>
                <span className="material-symbols-outlined ms-fill" style={{
                  fontSize: 18, color: '#00A9A5',
                  animation: 'dot-blink 1.5s infinite'
                }}>mic</span>
                Listening…
              </div>
            )}
            {/* End call */}
            <button onClick={disconnect} style={{
              width: 42, height: 42, borderRadius: '50%', background: '#C8232C', color: '#fff', flexShrink: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center'
            }}>
              <span className="material-symbols-outlined ms-fill" style={{ fontSize: 20 }}>call_end</span>
            </button>
          </>
        )}
      </div>
    </div>
  );
}


// ── Agents Page ──────────────────────────────────────────────────────────────

function AgentsPage() {
  const [selected, setSelected] = useAgentState(0);
  const [expressiveness, setExpressiveness] = useAgentState(72);
  const [velocity, setVelocity] = useAgentState(1.1);
  const agent = AGENT_PROFILES[selected];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 'calc(100vh-64px)', paddingBottom: 40 }}>
      {/* Header */}
      <div style={{ padding: '28px 36px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.1em', color: '#aaa', textTransform: 'uppercase', marginBottom: 4 }}>
            Command / <span style={{ color: '#C8232C' }}>AI Agents</span>
          </div>
          <div style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 26, color: '#1A1A1A', letterSpacing: '-0.02em' }}>Intelligence Profiles</div>
          <div style={{ fontSize: 12, color: '#767676', marginTop: 4 }}>Configure and connect to each autonomous agent.</div>
        </div>
        <button style={{
          background: '#C8232C', color: '#fff', padding: '9px 20px', borderRadius: 9,
          fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6
        }}>
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>add</span> Deploy New Agent
        </button>
      </div>

      {/* Grid */}
      <div style={{ padding: '24px 36px', display: 'grid', gridTemplateColumns: '300px 1fr', gap: 20, flex: 1 }}>
        {/* Left: profile list */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {AGENT_PROFILES.map((a, i) => {
            const active = i === selected;
            return (
              <div key={a.name} onClick={() => setSelected(i)} style={{
                background: active ? '#F9E6E7' : '#fff',
                border: `1.5px solid ${active ? '#C8232C' : '#F0F1F3'}`,
                borderRadius: 14, padding: '16px', cursor: 'pointer',
                transition: 'all 0.15s'
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{
                      width: 38, height: 38, borderRadius: 10,
                      background: active ? '#C8232C' : '#F4F5F6',
                      display: 'flex', alignItems: 'center', justifyContent: 'center'
                    }}>
                      <span className="material-symbols-outlined ms-fill" style={{ fontSize: 20, color: active ? '#fff' : '#767676' }}>{a.icon}</span>
                    </div>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 800, color: '#1A1A1A', fontFamily: 'Manrope' }}>{a.name}</div>
                      <div style={{ fontSize: 10, color: '#767676', fontWeight: 600 }}>{a.role}</div>
                    </div>
                  </div>
                  <span style={{
                    fontSize: 9, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.08em',
                    color: a.status === 'Live' ? '#00A9A5' : '#aaa',
                    background: a.status === 'Live' ? '#D0F2F1' : '#F4F5F6',
                    padding: '3px 8px', borderRadius: 5
                  }}>{a.status}</span>
                </div>
                {a.status === 'Live' && (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#aaa', marginBottom: 4 }}>
                      <span>Latency</span><span style={{ fontWeight: 700, color: '#00A9A5' }}>{a.latency}</span>
                    </div>
                    <div style={{ height: 4, borderRadius: 2, background: '#F0F1F3', overflow: 'hidden' }}>
                      <div style={{ width: `${a.latencyPct}%`, height: '100%', background: '#00A9A5', borderRadius: 2 }} />
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11 }}>
                      <span style={{ color: '#767676', fontWeight: 600 }}><b style={{ color: '#1A1A1A' }}>{a.calls}</b> live calls</span>
                      <span style={{ color: '#00A9A5', fontWeight: 700 }}>CSAT {a.csat}</span>
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {/* Token stats */}
          <div style={{
            background: '#F9E6E7', borderRadius: 14, padding: '16px', marginTop: 4, position: 'relative', overflow: 'hidden'
          }}>
            <div style={{ fontSize: 10, fontWeight: 800, color: '#C8232C', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 4 }}>Daily Token Usage</div>
            <div style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 30, color: '#C8232C', letterSpacing: '-0.02em' }}>847K</div>
            <div style={{ fontSize: 11, color: '#D4860A', fontWeight: 700, marginTop: 2, marginBottom: 12 }}>+9.3% vs yesterday</div>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 32, opacity: 0.4 }}>
              {[30,50,40,70,55,85,60,90,72,100].map((h,i) => (
                <div key={i} style={{ flex: 1, background: '#C8232C', borderRadius: '2px 2px 0 0', height: `${h}%` }} />
              ))}
            </div>
            <span className="material-symbols-outlined" style={{
              position: 'absolute', right: -10, bottom: -10, fontSize: 80, color: '#C8232C', opacity: 0.08
            }}>analytics</span>
          </div>
        </div>

        {/* Right: config panel */}
        <div style={{ background: '#fff', borderRadius: 14, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Panel header */}
          <div style={{ padding: '20px 28px', borderBottom: '1px solid #F0F1F3', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <div style={{ width: 46, height: 46, borderRadius: 12, background: '#F9E6E7', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <span className="material-symbols-outlined ms-fill" style={{ fontSize: 24, color: '#C8232C' }}>{agent.icon}</span>
              </div>
              <div>
                <div style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 17, color: '#1A1A1A' }}>Configuration: {agent.name}</div>
                <div style={{ fontSize: 11, color: '#aaa', display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: agent.status === 'Live' ? '#00A9A5' : '#aaa', display: 'inline-block' }} />
                  {agent.status === 'Live' ? 'Neural link established · ' + agent.latency : 'Agent offline'}
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={{ padding: '8px 16px', border: '1px solid #E8E9EB', borderRadius: 8, fontSize: 12, fontWeight: 600, color: '#767676' }}>Reset</button>
              <button style={{ padding: '8px 16px', background: '#C8232C', borderRadius: 8, fontSize: 12, fontWeight: 700, color: '#fff' }}>Apply Changes</button>
            </div>
          </div>

          <div style={{ padding: '28px', display: 'flex', flexDirection: 'column', gap: 28, overflowY: 'auto' }}>
            {/* LLM selector */}
            <div>
              <div style={{ fontSize: 10, fontWeight: 800, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 14 }}>Neural Engine (LLM)</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12 }}>
                {['GPT-4o','Claude 3.5 Sonnet','Gemini 1.5 Pro'].map(llm => {
                  const active = agent.llm === llm;
                  return (
                    <div key={llm} style={{
                      border: `2px solid ${active ? '#C8232C' : '#F0F1F3'}`,
                      borderRadius: 12, padding: '14px', cursor: 'pointer',
                      background: active ? '#F9E6E7' : '#fff', transition: 'all 0.15s'
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                        <div style={{ width: 30, height: 30, borderRadius: 8, background: active ? '#C8232C' : '#F4F5F6', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <span className="material-symbols-outlined" style={{ fontSize: 16, color: active ? '#fff' : '#aaa' }}>psychology</span>
                        </div>
                        {active && <span className="material-symbols-outlined ms-fill" style={{ fontSize: 18, color: '#C8232C' }}>check_circle</span>}
                      </div>
                      <div style={{ fontSize: 12, fontWeight: 800, color: active ? '#C8232C' : '#1A1A1A' }}>{llm}</div>
                      <div style={{ fontSize: 10, color: '#aaa', marginTop: 3 }}>
                        {llm === 'GPT-4o' ? 'High intelligence, low latency' : llm.includes('Claude') ? 'Superior reasoning & nuance' : 'Vast context window'}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* STT / TTS */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
              {[{ label: 'Speech-to-Text (STT)', val: agent.stt }, { label: 'Text-to-Speech (TTS)', val: agent.tts }].map(({ label, val }) => (
                <div key={label}>
                  <div style={{ fontSize: 10, fontWeight: 800, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10 }}>{label}</div>
                  <div style={{ position: 'relative' }}>
                    <select defaultValue={val} style={{
                      width: '100%', background: '#F4F5F6', border: 'none',
                      borderRadius: 9, padding: '10px 36px 10px 14px',
                      fontSize: 12, fontWeight: 600, color: '#1A1A1A', appearance: 'none', outline: 'none'
                    }}>
                      <option>{val}</option>
                    </select>
                    <span className="material-symbols-outlined" style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', fontSize: 16, color: '#aaa', pointerEvents: 'none' }}>expand_more</span>
                  </div>
                </div>
              ))}
            </div>

            {/* Sliders */}
            <div style={{ paddingTop: 4, borderTop: '1px solid #F0F1F3', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 28 }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                  <span style={{ fontSize: 10, fontWeight: 800, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Vocal Expressiveness</span>
                  <span style={{ fontSize: 10, fontWeight: 800, color: '#C8232C', background: '#F9E6E7', padding: '2px 8px', borderRadius: 5 }}>
                    {expressiveness < 33 ? 'Low' : expressiveness < 66 ? 'Medium' : 'High'}
                  </span>
                </div>
                <input type="range" min="0" max="100" value={expressiveness} onChange={e => setExpressiveness(+e.target.value)}
                  style={{ width: '100%', accentColor: '#C8232C' }} />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#bbb', marginTop: 4 }}>
                  <span>Monotone</span><span>Dynamic</span>
                </div>
              </div>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                  <span style={{ fontSize: 10, fontWeight: 800, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Speech Velocity</span>
                  <span style={{ fontSize: 10, fontWeight: 800, color: '#C8232C', background: '#F9E6E7', padding: '2px 8px', borderRadius: 5 }}>{velocity.toFixed(1)}x</span>
                </div>
                <input type="range" min="0.5" max="2.0" step="0.1" value={velocity} onChange={e => setVelocity(+e.target.value)}
                  style={{ width: '100%', accentColor: '#C8232C' }} />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#bbb', marginTop: 4 }}>
                  <span>0.5x</span><span>2.0x</span>
                </div>
              </div>
            </div>

            {/* ── Live Call Panel ── */}
            <div style={{ paddingTop: 4, borderTop: '1px solid #F0F1F3' }}>
              <div style={{ fontSize: 10, fontWeight: 800, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 14 }}>
                Live Call Terminal
              </div>
              <AgentCallPanel agent={agent} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { AgentsPage });
