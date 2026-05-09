// History page — Georges River Council

const HISTORY_RECORDS = [
  { id: 'GRC-4980', caller: 'Michael Chen',    date: '19 Apr 2026, 14:32', duration: '04:12', sentiment: 'Positive',   outcome: 'Resolved',   agent: 'Aria v2',   intent: 'Rates Payment' },
  { id: 'GRC-4971', caller: 'Sandra Okafor',   date: '19 Apr 2026, 14:11', duration: '09:44', sentiment: 'Frustrated', outcome: 'Escalated',  agent: 'George v1', intent: 'DA Dispute' },
  { id: 'GRC-4963', caller: 'James Torrens',   date: '19 Apr 2026, 13:58', duration: '02:30', sentiment: 'Neutral',    outcome: 'Resolved',   agent: 'Aria v2',   intent: 'Waste Query' },
  { id: 'GRC-4944', caller: 'Anh Nguyen',      date: '19 Apr 2026, 13:40', duration: '06:17', sentiment: 'Positive',   outcome: 'Resolved',   agent: 'Aria v2',   intent: 'Animal Reg.' },
  { id: 'GRC-4931', caller: 'Brenda Walsh',    date: '19 Apr 2026, 13:22', duration: '11:05', sentiment: 'Critical',   outcome: 'Escalated',  agent: 'George v1', intent: 'Noise Complaint' },
  { id: 'GRC-4918', caller: 'Derek Papadopoulos', date: '19 Apr 2026, 13:04', duration: '03:51', sentiment: 'Neutral', outcome: 'Resolved',   agent: 'Aria v2',   intent: 'Events Info' },
  { id: 'GRC-4902', caller: 'Yuki Tanaka',     date: '19 Apr 2026, 12:47', duration: '05:28', sentiment: 'Positive',   outcome: 'Resolved',   agent: 'River Bot', intent: 'Recycling' },
  { id: 'GRC-4889', caller: 'Claire Dumont',   date: '19 Apr 2026, 12:29', duration: '07:14', sentiment: 'Frustrated', outcome: 'Escalated',  agent: 'George v1', intent: 'Parking Fine' },
];

const SENT_STYLE = {
  Positive:   { color: '#00A9A5', bg: '#D0F2F1', icon: 'sentiment_satisfied' },
  Neutral:    { color: '#767676', bg: '#F4F5F6', icon: 'sentiment_neutral' },
  Frustrated: { color: '#D4860A', bg: '#FDF3E1', icon: 'sentiment_dissatisfied' },
  Critical:   { color: '#C8232C', bg: '#F9E6E7', icon: 'sentiment_very_dissatisfied' },
};

const OUTCOME_STYLE = {
  Resolved: { color: '#00A9A5', bg: '#D0F2F1' },
  Escalated:{ color: '#C8232C', bg: '#F9E6E7' },
};

const WEEKLY = [
  { day: 'MON', ai: 70, esc: 15 },
  { day: 'TUE', ai: 85, esc: 22 },
  { day: 'WED', ai: 60, esc: 18 },
  { day: 'THU', ai: 45, esc: 10 },
  { day: 'FRI', ai: 95, esc: 28 },
  { day: 'SAT', ai: 50, esc: 8  },
  { day: 'SUN', ai: 35, esc: 5  },
];

function HistoryPage() {
  const [query, setQuery] = React.useState('');

  const shown = HISTORY_RECORDS.filter(r =>
    !query || r.caller.toLowerCase().includes(query.toLowerCase()) || r.id.includes(query)
  );

  return (
    <div style={{ padding: '28px 36px', display: 'flex', flexDirection: 'column', gap: 24, minHeight: 'calc(100vh - 64px)', paddingBottom: 40 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.1em', color: '#aaa', textTransform: 'uppercase', marginBottom: 4 }}>
            Intelligence / <span style={{ color: '#C8232C' }}>Call History</span>
          </div>
          <div style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 26, color: '#1A1A1A', letterSpacing: '-0.02em' }}>History & Insights</div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button style={{
            padding: '8px 16px', border: '1px solid #E8E9EB', borderRadius: 9,
            fontSize: 12, fontWeight: 700, color: '#767676',
            display: 'flex', alignItems: 'center', gap: 6
          }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>download</span> Export Dataset
          </button>
          <button style={{
            padding: '8px 16px', background: '#C8232C', borderRadius: 9,
            fontSize: 12, fontWeight: 700, color: '#fff',
            display: 'flex', alignItems: 'center', gap: 6
          }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>calendar_month</span> Last 7 Days
          </button>
        </div>
      </div>

      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 280px', gap: 20 }}>

        {/* Weekly chart */}
        <div style={{ gridColumn: '1 / 3', background: '#fff', borderRadius: 14, padding: '24px', boxShadow: '0 2px 12px rgba(0,0,0,0.05)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
            <div>
              <div style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 15, color: '#1A1A1A' }}>Weekly Call Volume</div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 4 }}>
                <span style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 30, color: '#1A1A1A', letterSpacing: '-0.02em' }}>8,924</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#00A9A5', background: '#D0F2F1', padding: '2px 8px', borderRadius: 5 }}>+11.8%</span>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 16, fontSize: 10, fontWeight: 700, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.06em', alignItems: 'center' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: '#00A9A5', display: 'inline-block' }} />AI Resolved</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: '#C8232C', display: 'inline-block' }} />Escalated</span>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, height: 140, position: 'relative' }}>
            {/* Y-axis lines */}
            {[100, 75, 50, 25].map(y => (
              <div key={y} style={{
                position: 'absolute', left: 0, right: 0,
                bottom: `${y}%`, borderTop: '1px dashed #F0F1F3',
                pointerEvents: 'none'
              }} />
            ))}
            {WEEKLY.map((col, i) => (
              <div key={col.day} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, height: '100%', justifyContent: 'flex-end', zIndex: 1 }}>
                <div style={{ width: '100%', maxWidth: 48, display: 'flex', flexDirection: 'column', gap: 2, justifyContent: 'flex-end', alignItems: 'center', height: '100%' }}>
                  <div style={{ width: '60%', background: '#F9E6E7', borderRadius: '3px 3px 0 0', height: `${col.esc}%` }} />
                  <div style={{ width: '100%', background: '#00A9A5', borderRadius: '3px 3px 0 0', height: `${col.ai}%` }} />
                </div>
                <span style={{ fontSize: 9, fontWeight: 800, color: '#aaa', marginTop: 6, letterSpacing: '0.06em' }}>{col.day}</span>
              </div>
            ))}
          </div>
        </div>

        {/* CSAT */}
        <div style={{ background: '#F9E6E7', borderRadius: 14, padding: '24px', position: 'relative', overflow: 'hidden' }}>
          <div style={{ fontSize: 10, fontWeight: 800, color: '#C8232C', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>Avg. CSAT Score</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginBottom: 20 }}>
            <span style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 44, color: '#C8232C', letterSpacing: '-0.03em' }}>4.71</span>
            <span style={{ fontSize: 16, fontWeight: 600, color: '#E8796C' }}>/5.0</span>
          </div>
          {[
            { label: 'Citizen Satisfaction', pct: 91 },
            { label: 'AI Confidence Score', pct: 87 },
            { label: 'First Call Resolution', pct: 83 },
          ].map(s => (
            <div key={s.label} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontWeight: 700, color: '#C8232C', marginBottom: 4 }}>
                <span>{s.label}</span><span>{s.pct}%</span>
              </div>
              <div style={{ height: 5, borderRadius: 3, background: 'rgba(200,35,44,0.15)', overflow: 'hidden' }}>
                <div style={{ width: `${s.pct}%`, height: '100%', background: '#C8232C', borderRadius: 3 }} />
              </div>
            </div>
          ))}
          <div style={{ marginTop: 12, display: 'flex', justifyContent: 'space-between', fontSize: 10, fontWeight: 700, color: '#E8796C', borderTop: '1px solid rgba(200,35,44,0.15)', paddingTop: 10 }}>
            <span>Benchmark: 4.50</span>
            <span style={{ background: 'rgba(200,35,44,0.12)', padding: '2px 8px', borderRadius: 4, color: '#C8232C' }}>OPTIMAL</span>
          </div>
          <span className="material-symbols-outlined" style={{ position: 'absolute', right: -12, bottom: -12, fontSize: 90, color: '#C8232C', opacity: 0.06 }}>sentiment_very_satisfied</span>
        </div>
      </div>

      {/* Table */}
      <div style={{ background: '#fff', borderRadius: 14, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
        {/* Toolbar */}
        <div style={{ padding: '14px 20px', borderBottom: '1px solid #F0F1F3', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: '#FAFAFA' }}>
          <div style={{ position: 'relative' }}>
            <span className="material-symbols-outlined" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', fontSize: 16, color: '#aaa' }}>search</span>
            <input
              placeholder="Filter by caller or ID…"
              value={query}
              onChange={e => setQuery(e.target.value)}
              style={{
                background: '#fff', border: '1px solid #E8E9EB', borderRadius: 9,
                padding: '7px 14px 7px 32px', fontSize: 12, fontWeight: 500,
                outline: 'none', width: 260
              }}
            />
          </div>
          <span style={{ fontSize: 11, fontWeight: 600, color: '#aaa' }}>Showing {shown.length} of {HISTORY_RECORDS.length} records</span>
        </div>

        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #F0F1F3' }}>
              {['Date / Time','Caller','Duration','Sentiment','Outcome','Intent','Processor'].map(h => (
                <th key={h} style={{ padding: '12px 18px', textAlign: 'left', fontSize: 9, fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#aaa', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => {
              const ss = SENT_STYLE[r.sentiment] || SENT_STYLE.Neutral;
              const os = OUTCOME_STYLE[r.outcome] || OUTCOME_STYLE.Resolved;
              return (
                <tr key={r.id}
                  style={{ borderBottom: '1px solid #F8F9FA', transition: 'background 0.1s', cursor: 'pointer' }}
                  onMouseEnter={e => e.currentTarget.style.background = '#FAFAFA'}
                  onMouseLeave={e => e.currentTarget.style.background = ''}>
                  <td style={{ padding: '13px 18px' }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: '#1A1A1A' }}>{r.date}</div>
                    <div style={{ fontSize: 10, fontFamily: 'monospace', color: '#aaa', marginTop: 2 }}>{r.id}</div>
                  </td>
                  <td style={{ padding: '13px 18px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <div style={{
                        width: 32, height: 32, borderRadius: 9, background: '#F9E6E7', flexShrink: 0,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 10, fontWeight: 800, color: '#C8232C'
                      }}>{r.caller.split(' ').map(n => n[0]).join('')}</div>
                      <span style={{ fontSize: 13, fontWeight: 600, color: '#1A1A1A' }}>{r.caller}</span>
                    </div>
                  </td>
                  <td style={{ padding: '13px 18px', fontSize: 13, fontWeight: 600, color: '#767676', fontVariantNumeric: 'tabular-nums' }}>{r.duration}</td>
                  <td style={{ padding: '13px 18px' }}>
                    <span style={{
                      fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.05em',
                      color: ss.color, background: ss.bg, padding: '4px 10px', borderRadius: 6,
                      display: 'inline-flex', alignItems: 'center', gap: 4
                    }}>
                      <span className="material-symbols-outlined ms-fill" style={{ fontSize: 12 }}>{ss.icon}</span>
                      {r.sentiment}
                    </span>
                  </td>
                  <td style={{ padding: '13px 18px' }}>
                    <span style={{
                      fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.05em',
                      color: os.color, background: os.bg, padding: '4px 10px', borderRadius: 6
                    }}>{r.outcome}</span>
                  </td>
                  <td style={{ padding: '13px 18px', fontSize: 12, color: '#5A5F6B', fontWeight: 600 }}>{r.intent}</td>
                  <td style={{ padding: '13px 18px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#767676', fontWeight: 600 }}>
                      <span className="material-symbols-outlined ms-fill" style={{ fontSize: 14, color: '#00A9A5' }}>smart_toy</span>
                      {r.agent}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {/* Pagination */}
        <div style={{ padding: '12px 20px', borderTop: '1px solid #F0F1F3', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: '#FAFAFA' }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: '#aaa' }}>Page 1 of 42</span>
          <div style={{ display: 'flex', gap: 4 }}>
            {['PREV','1','2','3','...','NEXT'].map((p, i) => (
              <button key={i} style={{
                padding: '5px 10px', borderRadius: 6, fontSize: 10, fontWeight: 800,
                background: p === '1' ? '#C8232C' : '#fff',
                color: p === '1' ? '#fff' : '#767676',
                border: '1px solid #E8E9EB',
                opacity: p === 'PREV' ? 0.4 : 1
              }}>{p}</button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { HistoryPage });
