// Live Calls page - Georges River Council

const STATUS_COLOR = {
  active: { text: '#008C89', bg: '#D0F2F1' },
  ended: { text: '#767676', bg: '#F4F5F6' },
};

function parseTime(value) {
  const ts = Date.parse(value || '');
  return Number.isFinite(ts) ? ts : null;
}

function formatDuration(startedAt, endedAt, nowMs) {
  const start = parseTime(startedAt);
  if (!start) return '00:00';
  const end = parseTime(endedAt) || nowMs;
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  const mins = String(Math.floor(seconds / 60)).padStart(2, '0');
  const secs = String(seconds % 60).padStart(2, '0');
  return `${mins}:${secs}`;
}

function shortId(id) {
  if (!id) return 'unknown';
  return id.length > 22 ? `${id.slice(0, 10)}...${id.slice(-8)}` : id;
}

function LiveCallsPage({ onTakeover }) {
  const [filter, setFilter] = React.useState('All');
  const [calls, setCalls] = React.useState([]);
  const [activeCount, setActiveCount] = React.useState(0);
  const [selectedId, setSelectedId] = React.useState(null);
  const [nowMs, setNowMs] = React.useState(Date.now());
  const [lastError, setLastError] = React.useState('');

  React.useEffect(() => {
    let cancelled = false;

    async function fetchCalls() {
      try {
        const res = await fetch('/api/live-calls', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        const nextCalls = Array.isArray(data.calls) ? data.calls : [];
        setCalls(nextCalls);
        setActiveCount(data.active_count || 0);
        setLastError('');
        setSelectedId(current => {
          if (current && nextCalls.some(call => call.id === current)) return current;
          return nextCalls[0] ? nextCalls[0].id : null;
        });
      } catch (err) {
        if (!cancelled) setLastError(err.message || 'Unable to load live calls');
      }
    }

    fetchCalls();
    const poll = setInterval(fetchCalls, 900);
    const clock = setInterval(() => setNowMs(Date.now()), 1000);
    return () => {
      cancelled = true;
      clearInterval(poll);
      clearInterval(clock);
    };
  }, []);

  const filters = ['All', 'Active', 'Ended', 'Phone'];
  const shown = calls.filter(call => {
    if (filter === 'Active') return call.status === 'active';
    if (filter === 'Ended') return call.status === 'ended';
    if (filter === 'Phone') return call.provider !== 'WebRTC';
    return true;
  });
  const selected = calls.find(call => call.id === selectedId) || shown[0] || calls[0] || null;
  const transcript = selected && Array.isArray(selected.transcript) ? selected.transcript : [];

  return (
    <div style={{ padding: '28px 36px', display: 'flex', flexDirection: 'column', gap: 22, minHeight: 'calc(100vh - 64px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 16 }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.1em', color: '#aaa', textTransform: 'uppercase', marginBottom: 4 }}>
            Command / <span style={{ color: '#C8232C' }}>Live Calls</span>
          </div>
          <div style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 26, color: '#1A1A1A' }}>Live Calls Monitor</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="live-blink" style={{ width: 8, height: 8, borderRadius: '50%', background: activeCount ? '#00A9A5' : '#C8CCD2', display: 'inline-block' }} />
          <span style={{ fontSize: 12, fontWeight: 800, color: activeCount ? '#00A9A5' : '#767676' }}>
            {activeCount} Active Session{activeCount === 1 ? '' : 's'}
          </span>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {filters.map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: '6px 16px', borderRadius: 20, fontSize: 11, fontWeight: 700,
            background: filter === f ? '#C8232C' : '#fff',
            color: filter === f ? '#fff' : '#767676',
            border: `1px solid ${filter === f ? '#C8232C' : '#E8E9EB'}`,
            transition: 'all 0.15s'
          }}>{f}</button>
        ))}
        {lastError && (
          <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 700, color: '#C8232C' }}>
            Live feed error: {lastError}
          </span>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(520px, 1.1fr) minmax(360px, 0.9fr)', gap: 18, alignItems: 'start' }}>
        <div style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#FAFAFA', borderBottom: '1px solid #F0F1F3' }}>
                {['Caller', 'Provider', 'Session ID', 'Duration', 'Transcript', 'Actions'].map(h => (
                  <th key={h} style={{ padding: '12px 16px', textAlign: 'left', fontSize: 9, fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#aaa', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map(call => {
                const status = STATUS_COLOR[call.status] || STATUS_COLOR.ended;
                const isSelected = selected && selected.id === call.id;
                const lineCount = Array.isArray(call.transcript) ? call.transcript.length : 0;
                return (
                  <tr key={call.id} onClick={() => setSelectedId(call.id)} style={{
                    borderBottom: '1px solid #F8F9FA',
                    background: isSelected ? '#FFF7F7' : '#fff',
                    cursor: 'pointer',
                    transition: 'background 0.1s'
                  }}>
                    <td style={{ padding: '14px 16px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{
                          width: 34, height: 34, borderRadius: 8, background: '#F9E6E7', flexShrink: 0,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontSize: 10, fontWeight: 900, color: '#C8232C'
                        }}>{(call.provider || 'AI').slice(0, 2).toUpperCase()}</div>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 800, color: '#1A1A1A' }}>{call.caller || 'Phone caller'}</div>
                          <span style={{
                            display: 'inline-block', marginTop: 3, fontSize: 9, fontWeight: 900, textTransform: 'uppercase', letterSpacing: '0.06em',
                            color: status.text, background: status.bg, padding: '3px 8px', borderRadius: 5
                          }}>{call.status || 'unknown'}</span>
                        </div>
                      </div>
                    </td>
                    <td style={{ padding: '14px 16px', fontSize: 12, fontWeight: 700, color: '#5A5F6B' }}>{call.provider || 'Unknown'}</td>
                    <td style={{ padding: '14px 16px' }}>
                      <span title={call.id} style={{
                        fontSize: 10, fontWeight: 800, fontFamily: 'monospace',
                        color: '#767676', background: '#F4F5F6', padding: '3px 8px', borderRadius: 5
                      }}>{shortId(call.id)}</span>
                    </td>
                    <td style={{ padding: '14px 16px', fontSize: 13, fontWeight: 800, color: '#1A1A1A', fontVariantNumeric: 'tabular-nums' }}>
                      {formatDuration(call.started_at, call.ended_at, nowMs)}
                    </td>
                    <td style={{ padding: '14px 16px', fontSize: 12, fontWeight: 700, color: '#5A5F6B' }}>{lineCount} lines</td>
                    <td style={{ padding: '14px 16px' }}>
                      <button onClick={(e) => { e.stopPropagation(); onTakeover && onTakeover(call); }} style={{
                        background: '#F4F5F6',
                        color: '#767676',
                        fontSize: 10, fontWeight: 800, padding: '6px 12px', borderRadius: 7, letterSpacing: '0.04em'
                      }}>MONITOR</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {shown.length === 0 && (
            <div style={{ padding: '48px', textAlign: 'center', color: '#8A8F98', fontSize: 13, fontWeight: 700 }}>
              Waiting for live calls.
            </div>
          )}
        </div>

        <div style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', minHeight: 520, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '16px 18px', borderBottom: '1px solid #F0F1F3', display: 'flex', justifyContent: 'space-between', gap: 14, alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: 10, fontWeight: 900, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#aaa' }}>Live Transcript</div>
              <div style={{ marginTop: 4, fontSize: 14, fontWeight: 900, color: '#1A1A1A' }}>
                {selected ? `${selected.provider || 'Call'} - ${shortId(selected.id)}` : 'No call selected'}
              </div>
            </div>
            {selected && (
              <span style={{
                fontSize: 10, fontWeight: 900, textTransform: 'uppercase', letterSpacing: '0.06em',
                color: (STATUS_COLOR[selected.status] || STATUS_COLOR.ended).text,
                background: (STATUS_COLOR[selected.status] || STATUS_COLOR.ended).bg,
                padding: '5px 9px', borderRadius: 5
              }}>{selected.status}</span>
            )}
          </div>

          <div style={{ padding: 18, overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
            {transcript.map((line, index) => {
              const isAgent = line.speaker === 'agent';
              return (
                <div key={`${line.timestamp}-${index}`} style={{
                  alignSelf: isAgent ? 'flex-end' : 'flex-start',
                  maxWidth: '86%',
                  background: isAgent ? '#F9E6E7' : '#F4F5F6',
                  border: `1px solid ${isAgent ? '#F2CDD0' : '#E8E9EB'}`,
                  borderRadius: 8,
                  padding: '10px 12px'
                }}>
                  <div style={{
                    fontSize: 9, fontWeight: 900, letterSpacing: '0.08em', textTransform: 'uppercase',
                    color: isAgent ? '#C8232C' : '#5A5F6B', marginBottom: 5
                  }}>{isAgent ? 'Agent' : 'Caller'}</div>
                  <div style={{ fontSize: 13, lineHeight: 1.45, fontWeight: 600, color: '#1A1A1A', whiteSpace: 'pre-wrap' }}>{line.text}</div>
                </div>
              );
            })}
            {selected && transcript.length === 0 && (
              <div style={{ margin: 'auto', textAlign: 'center', color: '#8A8F98', fontSize: 13, fontWeight: 700 }}>
                Call connected. Waiting for transcript lines.
              </div>
            )}
            {!selected && (
              <div style={{ margin: 'auto', textAlign: 'center', color: '#8A8F98', fontSize: 13, fontWeight: 700 }}>
                Start or select a call to view the live conversation.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { LiveCallsPage });
