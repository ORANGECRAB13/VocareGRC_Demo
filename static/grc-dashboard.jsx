// Dashboard page — Georges River Council

const CALL_TOPICS = [
  { label: 'Rates & Payments',        pct: 28, color: '#C8232C' },
  { label: 'Development Applications', pct: 19, color: '#00A9A5' },
  { label: 'Waste & Recycling',        pct: 16, color: '#767676' },
  { label: 'Parking Infringements',    pct: 12, color: '#E8796C' },
  { label: 'Animal Registration',      pct: 9,  color: '#4ECDC4' },
  { label: 'Other Services',           pct: 16, color: '#D0F2F1' },
];

const FLAGGED = [
  { initials: 'RP', name: 'Rachel Park',    id: '#GRC-4821', flag: 'Frustration Peak', flagType: 'error',   duration: '07:14', intent: 'DA Dispute' },
  { initials: 'TM', name: 'Trevor Morris',  id: '#GRC-4798', flag: 'Policy Loop',      flagType: 'warning', duration: '11:32', intent: 'Rates Query' },
  { initials: 'SL', name: 'Sandra Lau',     id: '#GRC-4755', flag: 'Legal Mention',    flagType: 'error',   duration: '03:45', intent: 'Noise Complaint' },
];

const BARS_24H = [38,32,28,24,20,18,22,35,58,75,88,92,85,96,100,93,88,82,70,60,52,48,42,38];

function KPICard({ label, value, sub, subColor, icon, delay }) {
  return (
    <div className={`animate-in delay-${delay}`} style={{
      background: '#fff', borderRadius: 14, padding: '20px 22px',
      boxShadow: '0 2px 12px rgba(0,0,0,0.05)', display: 'flex',
      flexDirection: 'column', justifyContent: 'space-between', height: 148
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <span style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#767676' }}>{label}</span>
        <span className="material-symbols-outlined" style={{ fontSize: 20, color: '#D0D0D0' }}>{icon}</span>
      </div>
      <div>
        <div style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 32, color: '#1A1A1A', letterSpacing: '-0.02em' }}>{value}</div>
        <div style={{ fontSize: 11, fontWeight: 600, color: subColor || '#767676', marginTop: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
          {sub}
        </div>
      </div>
    </div>
  );
}

function GRCDashboard({ onNavigate }) {
  return (
    <div style={{ padding: '28px 36px', maxWidth: 1480, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 24 }}>

      {/* Surge Alert */}
      <div className="animate-in" style={{
        background: '#fff', borderRadius: 14, padding: '16px 22px',
        borderLeft: '4px solid #C8232C', boxShadow: '0 2px 12px rgba(0,0,0,0.05)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ background: '#F9E6E7', borderRadius: 10, padding: '10px', display: 'flex' }}>
            <span className="material-symbols-outlined" style={{ fontSize: 22, color: '#C8232C' }}>warning</span>
          </div>
          <div>
            <div style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 14, color: '#1A1A1A' }}>Surge Alert Active</div>
            <div style={{ fontSize: 12, color: '#767676', marginTop: 2 }}>Call volume is 38% above the 24-hour moving average — AI auto-scaling enabled.</div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={{
            background: '#C8232C', color: '#fff', padding: '8px 18px', borderRadius: 8,
            fontSize: 12, fontWeight: 700
          }}>Deploy Reserve Agents</button>
          <button style={{
            background: '#F4F5F6', color: '#767676', padding: '8px 18px', borderRadius: 8,
            fontSize: 12, fontWeight: 600
          }}>Dismiss</button>
        </div>
      </div>

      {/* KPIs */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        <KPICard delay={1} label="Total Calls Today" value="3,247" icon="call"
          sub={<><span className="material-symbols-outlined" style={{fontSize:13,color:'#00A9A5'}}>trending_up</span> +18.2% vs yesterday</>}
          subColor="#00A9A5" />
        <KPICard delay={2} label="Avg Handle Time" value="3m 48s" icon="schedule"
          sub={<><span className="material-symbols-outlined" style={{fontSize:13,color:'#C8232C'}}>trending_up</span> +11s slower</>}
          subColor="#C8232C" />
        <KPICard delay={3} label="CSAT Score" value="91.4%" icon="sentiment_very_satisfied"
          sub={<><span className="material-symbols-outlined" style={{fontSize:13,color:'#00A9A5'}}>check_circle</span> Target: 90% · On track</>}
          subColor="#00A9A5" />
        <KPICard delay={4} label="AI Resolution Rate" value="79 / 21" icon="smart_toy"
          sub={
            <div style={{ width: '100%' }}>
              <div style={{ width: '100%', height: 5, borderRadius: 3, background: '#F0F1F3', overflow: 'hidden', marginTop: 6 }}>
                <div style={{ width: '79%', height: '100%', background: '#00A9A5', borderRadius: 3 }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, fontWeight: 700, color: '#aaa', marginTop: 5, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                <span>AI Resolved</span><span>Escalated</span>
              </div>
            </div>
          } />
      </div>

      {/* Main row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 340px', gap: 20 }}>

        {/* Volume chart */}
        <div style={{ background: '#fff', borderRadius: 14, padding: '24px', boxShadow: '0 2px 12px rgba(0,0,0,0.05)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 24 }}>
            <div>
              <div style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 15, color: '#1A1A1A' }}>Call Volume — 24 Hours</div>
              <div style={{ fontSize: 11, color: '#767676', marginTop: 3 }}>Today vs surge threshold</div>
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              {['24H','7D','30D'].map((t,i) => (
                <button key={t} style={{
                  padding: '4px 10px', borderRadius: 6, fontSize: 10, fontWeight: 700,
                  background: i===0 ? '#C8232C' : '#F4F5F6',
                  color: i===0 ? '#fff' : '#767676'
                }}>{t}</button>
              ))}
            </div>
          </div>
          {/* Chart */}
          <div style={{ position: 'relative', height: 160 }}>
            {/* Surge threshold line at 70% */}
            <div style={{
              position: 'absolute', top: '15%', left: 0, right: 0,
              borderTop: '1.5px dashed #F9E6E7', zIndex: 2,
              display: 'flex', alignItems: 'center'
            }}>
              <span style={{
                background: '#C8232C', color: '#fff', fontSize: 8, fontWeight: 800,
                padding: '2px 7px', borderRadius: 4, letterSpacing: '0.06em',
                position: 'relative', top: -8, marginLeft: 8
              }}>SURGE THRESHOLD</span>
            </div>
            <div style={{
              position: 'absolute', inset: 0, display: 'flex', alignItems: 'flex-end', gap: 3, padding: '0 4px'
            }}>
              {BARS_24H.map((h, i) => {
                const isSurge = h >= 85;
                const isHigh  = h >= 65 && !isSurge;
                return (
                  <div key={i} style={{
                    flex: 1, borderRadius: '3px 3px 0 0',
                    height: `${h}%`,
                    background: isSurge ? '#C8232C' : isHigh ? '#00A9A5' : '#D0F2F1',
                    transition: 'opacity 0.2s',
                    opacity: 0.9
                  }} />
                );
              })}
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 9, fontWeight: 700, color: '#bbb', letterSpacing: '0.04em' }}>
            <span>12AM</span><span>6AM</span><span>12PM</span><span>6PM</span><span>11PM</span>
          </div>
        </div>

        {/* Call topics donut-style */}
        <div style={{ background: '#fff', borderRadius: 14, padding: '24px', boxShadow: '0 2px 12px rgba(0,0,0,0.05)' }}>
          <div style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 15, color: '#1A1A1A', marginBottom: 4 }}>Top Call Reasons</div>
          <div style={{ fontSize: 11, color: '#767676', marginBottom: 20 }}>Classified by AI intent detection</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {CALL_TOPICS.map(t => (
              <div key={t.label}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#1A1A1A' }}>{t.label}</span>
                  <span style={{ fontSize: 12, fontWeight: 800, color: t.color }}>{t.pct}%</span>
                </div>
                <div style={{ height: 6, borderRadius: 3, background: '#F4F5F6', overflow: 'hidden' }}>
                  <div style={{ width: `${t.pct}%`, height: '100%', background: t.color, borderRadius: 3, transition: 'width 0.8s ease' }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Status snapshot */}
        <div style={{ background: '#fff', borderRadius: 14, padding: '24px', boxShadow: '0 2px 12px rgba(0,0,0,0.05)', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 15, color: '#1A1A1A', marginBottom: 4 }}>Status Snapshot</div>
          {[
            { label: 'Active AI Calls', value: 127, color: '#00A9A5', bg: '#D0F2F1', icon: 'headset_mic', pulse: true },
            { label: 'Needs Intervention', value: 9,   color: '#C8232C', bg: '#F9E6E7', icon: 'person_search' },
            { label: 'Idle Agents',        value: 6,   color: '#767676', bg: '#F4F5F6', icon: 'coffee' },
            { label: 'Queued Callers',     value: 14,  color: '#D4860A', bg: '#FDF3E1', icon: 'hourglass_top' },
          ].map(s => (
            <div key={s.label} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              background: s.bg, borderRadius: 10, padding: '12px 14px'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span className="material-symbols-outlined ms-fill" style={{ fontSize: 18, color: s.color }}>{s.icon}</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#1A1A1A' }}>{s.label}</span>
              </div>
              <span style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 22, color: s.color }}>{s.value}</span>
            </div>
          ))}

          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.1em', color: '#aaa', textTransform: 'uppercase', marginBottom: 8 }}>Georges River LGA Coverage</div>
            <div style={{ borderRadius: 10, overflow: 'hidden', background: '#D0F2F1', height: 110 }}>
              <svg viewBox="0 0 280 110" width="100%" height="100%" style={{ display: 'block' }}>
                {/* Stylised southern Sydney suburb map hint */}
                <rect width="280" height="110" fill="#EBF9F8" />
                {/* River */}
                <path d="M 20,80 Q 60,65 100,72 Q 140,80 180,68 Q 220,56 260,60" fill="none" stroke="#00A9A5" strokeWidth="3" strokeLinecap="round" opacity="0.5"/>
                <path d="M 20,88 Q 60,73 100,80 Q 140,88 180,76 Q 220,64 260,68" fill="none" stroke="#00A9A5" strokeWidth="2" strokeLinecap="round" opacity="0.3"/>
                {/* Hurstville cluster */}
                <circle cx="160" cy="50" r="14" fill="#C8232C" opacity="0.12"/>
                <circle cx="160" cy="50" r="6" fill="#C8232C" opacity="0.85"/>
                {/* Kogarah */}
                <circle cx="200" cy="38" r="8" fill="#C8232C" opacity="0.10"/>
                <circle cx="200" cy="38" r="4" fill="#C8232C" opacity="0.8"/>
                {/* Mortdale */}
                <circle cx="120" cy="52" r="3.5" fill="#00A9A5" opacity="0.9"/>
                {/* Oatley */}
                <circle cx="90" cy="60" r="3" fill="#00A9A5" opacity="0.8"/>
                {/* Peakhurst */}
                <circle cx="130" cy="38" r="2.5" fill="#767676" opacity="0.7"/>
                <text x="152" y="32" fontSize="7" fontFamily="Inter" fontWeight="700" fill="#C8232C" opacity="0.8">Hurstville</text>
                <text x="192" y="22" fontSize="6" fontFamily="Inter" fontWeight="600" fill="#C8232C" opacity="0.7">Kogarah</text>
              </svg>
            </div>
          </div>
        </div>
      </div>

      {/* Locality Heatmap + Friction Tracker */}
      <LocalityHeatmap />
      <FrictionTracker />

      {/* Flagged calls */}
      <div style={{ background: '#fff', borderRadius: 14, padding: '24px', boxShadow: '0 2px 12px rgba(0,0,0,0.05)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div>
            <div style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 15, color: '#1A1A1A' }}>Flagged Calls Requiring Attention</div>
            <div style={{ fontSize: 11, color: '#767676', marginTop: 2 }}>High-priority escalations flagged by AI sentiment analysis</div>
          </div>
          <button onClick={() => onNavigate('live-calls')} style={{
            display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, color: '#C8232C'
          }}>View All <span className="material-symbols-outlined" style={{fontSize:16}}>arrow_forward</span></button>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #F0F1F3' }}>
              {['Caller Identity','Intent','Flag Reason','Duration','Action'].map(h => (
                <th key={h} style={{ paddingBottom: 10, textAlign: 'left', fontSize: 9, fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#aaa' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {FLAGGED.map((row, i) => (
              <tr key={i} style={{ borderBottom: '1px solid #F8F8F8' }}>
                <td style={{ padding: '14px 0' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{
                      width: 34, height: 34, borderRadius: 10, background: '#F9E6E7',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 10, fontWeight: 800, color: '#C8232C'
                    }}>{row.initials}</div>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: '#1A1A1A' }}>{row.name}</div>
                      <div style={{ fontSize: 10, color: '#aaa', fontWeight: 600 }}>{row.id}</div>
                    </div>
                  </div>
                </td>
                <td>
                  <span style={{
                    fontSize: 11, fontWeight: 600, color: '#00A9A5',
                    background: '#D0F2F1', padding: '4px 10px', borderRadius: 6
                  }}>{row.intent}</span>
                </td>
                <td>
                  <span style={{
                    fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.06em',
                    color: row.flagType === 'error' ? '#C8232C' : '#D4860A',
                    background: row.flagType === 'error' ? '#F9E6E7' : '#FDF3E1',
                    padding: '4px 10px', borderRadius: 6,
                    display: 'inline-flex', alignItems: 'center', gap: 4
                  }}>
                    <span className="material-symbols-outlined" style={{fontSize:12}}>{row.flagType==='error'?'warning':'quiz'}</span>
                    {row.flag}
                  </span>
                </td>
                <td style={{ fontSize: 13, color: '#767676', fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{row.duration}</td>
                <td>
                  <button onClick={() => onNavigate('live-calls')} style={{
                    background: '#C8232C', color: '#fff', fontSize: 11, fontWeight: 800,
                    padding: '7px 16px', borderRadius: 8, letterSpacing: '0.04em'
                  }}>TAKE OVER</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

Object.assign(window, { GRCDashboard });
