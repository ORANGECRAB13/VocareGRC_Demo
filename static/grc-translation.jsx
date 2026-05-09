const { useState, useEffect, useRef } = React;

function TranslationPage() {
  const [sessions, setSessions] = useState([]);
  const [selectedSession, setSelectedSession] = useState(null);
  const [sessionData, setSessionData] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ callerName: '', callerLang: 'zh', topic: '' });
  const transcriptEndRef = useRef(null);

  // Poll sessions list
  useEffect(() => {
    const fetchSessions = async () => {
      try {
        const res = await fetch('/api/translation/sessions');
        const data = await res.json();
        setSessions(data.sessions);
      } catch (err) {
        console.error("Failed to fetch sessions", err);
      }
    };
    fetchSessions();
    const interval = setInterval(fetchSessions, 2000);
    return () => clearInterval(interval);
  }, []);

  // Poll selected session events
  useEffect(() => {
    if (!selectedSession) return;
    let polling = true;

    const fetchSessionFull = async () => {
      try {
        const res = await fetch(`/api/translation/session/${selectedSession}`);
        if (res.ok) {
          const data = await res.json();
          setSessionData({
            ...data,
            live_transcripts: data.live_transcripts || {}
          });
        }
      } catch (err) {
        console.error("Failed to fetch full session", err);
      }
    };

    const pollEvents = async () => {
      try {
        const res = await fetch(`/api/translation/poll?session_id=${selectedSession}`);
        if (res.ok) {
          const data = await res.json();
          if (data.events && data.events.length > 0) {
            setSessionData(prev => {
              if (!prev) return prev;
              const newTranscript = [...prev.transcript];
              const newLive = { ...(prev.live_transcripts || {}) };
              let nextStatus = prev.status;
              for (const ev of data.events) {
                if (ev.type === 'turn') {
                  newTranscript.push(ev);
                  if (ev.speaker) delete newLive[ev.speaker];
                } else if (ev.type === 'live_transcript') {
                  newLive[ev.speaker] = ev;
                } else if (ev.type === 'status') {
                  nextStatus = ev.status;
                }
              }
              return { ...prev, status: nextStatus, transcript: newTranscript, live_transcripts: newLive };
            });
            setTimeout(() => {
              transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
            }, 100);
          }
        }
      } catch (err) {
        console.error("Poll failed", err);
      } finally {
        if (polling) {
          setTimeout(pollEvents, 1000);
        }
      }
    };

    fetchSessionFull().then(() => {
      if (polling) pollEvents();
    });

    return () => { polling = false; };
  }, [selectedSession]);

  const handleCreate = async (e) => {
    e.preventDefault();
    try {
      const res = await fetch('/api/translation/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          caller_name: createForm.callerName,
          caller_language: createForm.callerLang,
          topic: createForm.topic
        })
      });
      const data = await res.json();
      setShowCreate(false);
      setCreateForm({ callerName: '', callerLang: 'zh', topic: '' });
      setSessionData(null);
      setSelectedSession(data.session_id);
    } catch (err) {
      console.error("Failed to create session", err);
    }
  };

  return (
    <div style={{ padding: 24, display: 'flex', gap: 24, height: 'calc(100vh - 64px)' }}>
      {/* Sidebar: Session List */}
      <div style={{ width: 320, display: 'flex', flexDirection: 'column', background: '#fff', borderRadius: 12, border: '1px solid #E8E9EB', overflow: 'hidden' }}>
        <div style={{ padding: 16, borderBottom: '1px solid #E8E9EB', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700 }}>Live Translation</h2>
          <button 
            onClick={() => setShowCreate(true)}
            style={{ padding: '6px 12px', background: 'var(--grc-red)', color: '#fff', borderRadius: 6, fontSize: 12, fontWeight: 600 }}
          >
            + New
          </button>
        </div>
        
        <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
          {sessions.length === 0 ? (
            <div style={{ padding: 20, textAlign: 'center', color: '#888', fontSize: 13 }}>No active sessions</div>
          ) : (
            sessions.map(s => (
              <div 
                key={s.session_id} 
                onClick={() => setSelectedSession(s.session_id)}
                style={{ 
                  padding: 12, margin: '4px 0', borderRadius: 8, cursor: 'pointer',
                  background: selectedSession === s.session_id ? '#F9E6E7' : 'transparent',
                  border: `1px solid ${selectedSession === s.session_id ? '#C8232C' : 'transparent'}`
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <strong style={{ fontSize: 14 }}>{s.caller_name}</strong>
                  <span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: s.status === 'live' ? '#D0F2F1' : '#eee', color: s.status === 'live' ? '#007A77' : '#666' }}>
                    {s.status}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: '#666' }}>{s.topic} • {s.lang.toUpperCase()}</div>
                <div style={{ fontSize: 11, color: '#aaa', marginTop: 8 }}>{s.duration} • {s.participant_count} participants</div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Main Content: Transcript */}
      <div style={{ flex: 1, background: '#fff', borderRadius: 12, border: '1px solid #E8E9EB', display: 'flex', flexDirection: 'column' }}>
        {selectedSession && sessionData ? (
          <>
            <div style={{ padding: '16px 24px', borderBottom: '1px solid #E8E9EB', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 4px' }}>Session {sessionData.session_id}</h2>
                <div style={{ fontSize: 13, color: '#666' }}>{sessionData.topic}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>Join Link:</div>
                <a href={`/translate/${sessionData.session_id}`} target="_blank" style={{ fontSize: 14, color: 'var(--grc-teal)', textDecoration: 'none', fontWeight: 600 }}>
                  /translate/{sessionData.session_id}
                </a>
              </div>
            </div>
            
            <div style={{ flex: 1, overflowY: 'auto', padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
              {sessionData.live_transcripts && Object.values(sessionData.live_transcripts).length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                  {Object.values(sessionData.live_transcripts).map((t) => (
                    <div key={`live-${t.speaker}`} style={{ padding: '10px 14px', borderRadius: 999, background: '#FFF8E7', border: '1px solid #FFE58F', color: '#7A5D00', maxWidth: '100%' }}>
                      <span style={{ fontSize: 11, fontWeight: 800, textTransform: 'uppercase', marginRight: 8 }}>
                        Live {t.speaker_name}
                      </span>
                      <span style={{ fontSize: 13, fontWeight: 600 }}>{t.original}</span>
                    </div>
                  ))}
                </div>
              )}
              {sessionData.transcript && sessionData.transcript.length > 0 ? sessionData.transcript.map((t, i) => (
                <div key={i} style={{ background: '#F4F5F6', padding: 16, borderRadius: 12, maxWidth: '80%', alignSelf: i % 2 === 0 ? 'flex-start' : 'flex-end' }}>
                  <div style={{ fontSize: 11, color: '#888', marginBottom: 4, textTransform: 'uppercase', fontWeight: 600 }}>
                    {t.speaker_name} ({t.original_lang})
                  </div>
                  <div style={{ fontSize: 15, color: '#111', marginBottom: 8 }}>{t.original}</div>
                  <div style={{ height: 1, background: '#E8E9EB', margin: '8px 0' }} />
                  <div style={{ fontSize: 11, color: 'var(--grc-teal)', marginBottom: 4, textTransform: 'uppercase', fontWeight: 600 }}>
                    Translated ({t.translated_lang})
                  </div>
                  <div style={{ fontSize: 15, color: '#333' }}>{t.translated}</div>
                </div>
              )) : (
                <div style={{ margin: 'auto', color: '#888' }}>Waiting for conversation...</div>
              )}
              <div ref={transcriptEndRef} />
            </div>
          </>
        ) : (
          <div style={{ margin: 'auto', color: '#888', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 48, color: '#ddd' }}>forum</span>
            Select a session or create a new one
          </div>
        )}
      </div>

      {/* Create Modal */}
      {showCreate && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}>
          <div style={{ background: '#fff', padding: 24, borderRadius: 12, width: 400 }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 16 }}>Create Translation Session</h2>
            <form onSubmit={handleCreate} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Caller Name</label>
                <input 
                  type="text" required 
                  value={createForm.callerName} onChange={e => setCreateForm({...createForm, callerName: e.target.value})}
                  style={{ width: '100%', padding: '8px 12px', border: '1px solid #ddd', borderRadius: 6 }} 
                />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Caller Language</label>
                <select 
                  value={createForm.callerLang} onChange={e => setCreateForm({...createForm, callerLang: e.target.value})}
                  style={{ width: '100%', padding: '8px 12px', border: '1px solid #ddd', borderRadius: 6 }}
                >
                  <option value="zh">Mandarin (zh)</option>
                  <option value="en">English (en)</option>
                </select>
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Topic</label>
                <input 
                  type="text" required 
                  value={createForm.topic} onChange={e => setCreateForm({...createForm, topic: e.target.value})}
                  style={{ width: '100%', padding: '8px 12px', border: '1px solid #ddd', borderRadius: 6 }} 
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12, marginTop: 8 }}>
                <button type="button" onClick={() => setShowCreate(false)} style={{ padding: '8px 16px', borderRadius: 6, color: '#666', fontWeight: 600 }}>Cancel</button>
                <button type="submit" style={{ padding: '8px 16px', borderRadius: 6, background: 'var(--grc-red)', color: '#fff', fontWeight: 600 }}>Create Session</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { TranslationPage });
