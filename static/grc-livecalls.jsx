// Live Calls page — Georges River Council

const MOCK_CALLS = [
  { id: 'GRC-5021', caller: 'Daniel Nguyen',   duration: '02:14', sentiment: 'Positive',  intent: 'Rates Payment',    agent: 'Aria v2',    flag: null },
  { id: 'GRC-5019', caller: 'Janet Okafor',    duration: '07:48', sentiment: 'Frustrated', intent: 'DA Dispute',       agent: 'Aria v2',    flag: 'Frustration Peak' },
  { id: 'GRC-5017', caller: 'Martin Kwan',     duration: '01:05', sentiment: 'Neutral',   intent: 'Waste Collection', agent: 'George v1',  flag: null },
  { id: 'GRC-5015', caller: 'Priya Sharma',    duration: '11:22', sentiment: 'Frustrated', intent: 'Parking Fine',     agent: 'George v1',  flag: 'Policy Loop' },
  { id: 'GRC-5012', caller: 'Alex Thornton',   duration: '04:31', sentiment: 'Positive',  intent: 'Animal Reg.',      agent: 'Aria v2',    flag: null },
  { id: 'GRC-5010', caller: 'Linda Marchetti', duration: '03:17', sentiment: 'Critical',  intent: 'Noise Complaint',  agent: 'George v1',  flag: 'Legal Mention' },
  { id: 'GRC-5008', caller: 'Sam Beaumont',    duration: '00:52', sentiment: 'Neutral',   intent: 'Pool Cert.',       agent: 'Aria v2',    flag: null },
  { id: 'GRC-5005', caller: 'Frances Wu',      duration: '05:40', sentiment: 'Positive',  intent: 'Events Info',      agent: 'George v1',  flag: null },
];

const SENT_COLOR = {
  'Positive':   { text: '#00A9A5', bg: '#D0F2F1' },
  'Neutral':    { text: '#767676', bg: '#F4F5F6' },
  'Frustrated': { text: '#D4860A', bg: '#FDF3E1' },
  'Critical':   { text: '#C8232C', bg: '#F9E6E7' },
};

function LiveCallsPage({ onTakeover }) {
  const [filter, setFilter] = React.useState('All');
  const [tick, setTick] = React.useState(0);

  React.useEffect(() => {
    const t = setInterval(() => setTick(n => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const filters = ['All', 'Flagged', 'AI Managed', 'Critical'];
  const shown = MOCK_CALLS.filter(c => {
    if (filter === 'Flagged')    return !!c.flag;
    if (filter === 'AI Managed') return !c.flag;
    if (filter === 'Critical')   return c.sentiment === 'Critical' || c.sentiment === 'Frustrated';
    return true;
  });

  return (
    <div style={{ padding: '28px 36px', display: 'flex', flexDirection: 'column', gap: 22, minHeight: 'calc(100vh - 64px)' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.1em', color: '#aaa', textTransform: 'uppercase', marginBottom: 4 }}>
            Command / <span style={{ color: '#C8232C' }}>Live Calls</span>
          </div>
          <div style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 26, color: '#1A1A1A', letterSpacing: '-0.02em' }}>Live Calls Monitor</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="live-blink" style={{ width: 8, height: 8, borderRadius: '50%', background: '#00A9A5', display: 'inline-block' }} />
          <span style={{ fontSize: 12, fontWeight: 700, color: '#00A9A5' }}>{MOCK_CALLS.length} Active Sessions</span>
        </div>
      </div>

      {/* Filter pills */}
      <div style={{ display: 'flex', gap: 8 }}>
        {filters.map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: '6px 16px', borderRadius: 20, fontSize: 11, fontWeight: 700,
            background: filter === f ? '#C8232C' : '#fff',
            color: filter === f ? '#fff' : '#767676',
            border: `1px solid ${filter === f ? '#C8232C' : '#E8E9EB'}`,
            transition: 'all 0.15s'
          }}>{f}</button>
        ))}
      </div>

      {/* Table */}
      <div style={{ background: '#fff', borderRadius: 14, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#FAFAFA', borderBottom: '1px solid #F0F1F3' }}>
              {['Caller Identity','Session ID','Live Duration','Sentiment','Intent','AI Agent','Actions'].map(h => (
                <th key={h} style={{ padding: '12px 18px', textAlign: 'left', fontSize: 9, fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#aaa', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((call, i) => {
              const sc = SENT_COLOR[call.sentiment] || SENT_COLOR['Neutral'];
              return (
                <tr key={call.id} style={{ borderBottom: '1px solid #F8F9FA', transition: 'background 0.1s' }}
                  onMouseEnter={e => e.currentTarget.style.background = '#FAFAFA'}
                  onMouseLeave={e => e.currentTarget.style.background = ''}>
                  <td style={{ padding: '14px 18px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <div style={{
                        width: 34, height: 34, borderRadius: 10, background: '#F9E6E7', flexShrink: 0,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 10, fontWeight: 800, color: '#C8232C'
                      }}>{call.caller.split(' ').map(n=>n[0]).join('')}</div>
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: '#1A1A1A' }}>{call.caller}</div>
                        {call.flag && (
                          <div style={{ fontSize: 10, color: '#C8232C', fontWeight: 700 }}>⚑ {call.flag}</div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td style={{ padding: '14px 18px' }}>
                    <span style={{
                      fontSize: 10, fontWeight: 800, fontFamily: 'monospace',
                      color: '#767676', background: '#F4F5F6', padding: '3px 8px', borderRadius: 5
                    }}>{call.id}</span>
                  </td>
                  <td style={{ padding: '14px 18px', fontSize: 13, fontWeight: 700, color: '#1A1A1A', fontVariantNumeric: 'tabular-nums' }}>
                    {call.duration}
                  </td>
                  <td style={{ padding: '14px 18px' }}>
                    <span style={{
                      fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.06em',
                      color: sc.text, background: sc.bg, padding: '4px 10px', borderRadius: 6
                    }}>{call.sentiment}</span>
                  </td>
                  <td style={{ padding: '14px 18px' }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#5A5F6B' }}>{call.intent}</span>
                  </td>
                  <td style={{ padding: '14px 18px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600, color: '#5A5F6B' }}>
                      <span className="material-symbols-outlined ms-fill" style={{ fontSize: 14, color: '#00A9A5' }}>smart_toy</span>
                      {call.agent}
                    </div>
                  </td>
                  <td style={{ padding: '14px 18px' }}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button onClick={() => onTakeover && onTakeover(call)} style={{
                        background: call.flag ? '#C8232C' : '#F4F5F6',
                        color: call.flag ? '#fff' : '#767676',
                        fontSize: 10, fontWeight: 800, padding: '6px 12px', borderRadius: 7, letterSpacing: '0.04em'
                      }}>{call.flag ? 'TAKE OVER' : 'MONITOR'}</button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {shown.length === 0 && (
          <div style={{ padding: '48px', textAlign: 'center', color: '#aaa', fontSize: 13 }}>
            No calls match this filter.
          </div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { LiveCallsPage });
