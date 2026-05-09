// Locality Heatmap + Friction Point Tracker — Georges River Council

const { useState: useHeatState, useEffect: useHeatEffect } = React;

const SUBURB_DATA = [
  { name: 'Hurstville',    calls: 342, x: 292, y: 145, volume: 'critical' },
  { name: 'Kogarah',       calls: 289, x: 358, y: 104, volume: 'critical' },
  { name: 'Mortdale',      calls: 187, x: 232, y: 178, volume: 'high'     },
  { name: 'Penshurst',     calls: 156, x: 215, y: 145, volume: 'high'     },
  { name: 'Oatley',        calls: 143, x: 218, y: 215, volume: 'high'     },
  { name: 'Beverly Hills', calls: 98,  x: 188, y: 118, volume: 'medium'   },
  { name: 'Riverwood',     calls: 87,  x: 162, y: 148, volume: 'medium'   },
  { name: 'Peakhurst',     calls: 76,  x: 185, y: 188, volume: 'low'      },
  { name: 'Blakehurst',    calls: 65,  x: 315, y: 215, volume: 'low'      },
  { name: 'Carlton',       calls: 54,  x: 340, y: 138, volume: 'low'      },
  { name: 'Allawah',       calls: 48,  x: 325, y: 125, volume: 'low'      },
  { name: 'Lugarno',       calls: 44,  x: 258, y: 252, volume: 'low'      },
];

const VOL_COLORS = {
  critical: { fill: '#9E1B22', stroke: '#C8232C', label: 'Critical (250+)' },
  high:     { fill: '#C8232C', stroke: '#E8796C', label: 'High (150–249)'  },
  medium:   { fill: '#E8796C', stroke: '#F0B8B0', label: 'Medium (80–149)' },
  low:      { fill: '#F0B8B0', stroke: '#F9E6E7', label: 'Low (<80)'       },
};

function bubbleR(calls) {
  return Math.max(8, Math.sqrt(calls) * 1.72);
}

// Stacked bar data per suburb
const FRICTION_SUBURBS = ['Hurstville', 'Mortdale', 'Oatley', 'Kogarah', 'Penshurst', 'Beverly Hills'];
const FRICTION_CATS = [
  { key: 'zoning',   label: 'Zoning / DAs',  color: '#1A3A5C' },
  { key: 'bins',     label: 'Missed Bins',    color: '#C8232C' },
  { key: 'bulk',     label: 'Bulky Waste',    color: '#E8A020' },
  { key: 'parking',  label: 'Parking',        color: '#9B59B6' },
  { key: 'events',   label: 'Events',         color: '#00A9A5' },
  { key: 'noise',    label: 'Noise',          color: '#E8796C' },
];
const FRICTION_DATA = {
  Hurstville:    { zoning: 48, bins: 12, bulk: 20, parking: 18, events: 8,  noise: 10 },
  Mortdale:      { zoning: 22, bins: 38, bulk: 18, parking: 12, events: 5,  noise: 8  },
  Oatley:        { zoning: 18, bins: 20, bulk: 52, parking: 14, events: 6,  noise: 12 },
  Kogarah:       { zoning: 30, bins: 18, bulk: 15, parking: 44, events: 12, noise: 9  },
  Penshurst:     { zoning: 20, bins: 15, bulk: 41, parking: 16, events: 7,  noise: 8  },
  'Beverly Hills':{ zoning: 14, bins: 10, bulk: 16, parking: 12, events: 9, noise: 38 },
};

const CONSISTENT_ISSUES = [
  { suburb: 'Hurstville',    type: 'Zoning / DAs',  count: 48, action: 'Deploy additional DA counter staff during peak hours.' },
  { suburb: 'Mortdale',      type: 'Missed Bins',   count: 38, action: 'Verify contractor route compliance and check Monday schedule.' },
  { suburb: 'Oatley',        type: 'Bulky Waste',   count: 52, action: 'Update website FAQ and send SMS notification to affected streets.' },
  { suburb: 'Kogarah',       type: 'Parking',       count: 44, action: 'Review parking enforcement schedule and signage.' },
  { suburb: 'Penshurst',     type: 'Bulky Waste',   count: 41, action: 'Update website FAQ and send SMS notification to affected streets.' },
  { suburb: 'Beverly Hills', type: 'Noise',         count: 38, action: 'Notify noise enforcement team and check nearby permits.' },
];

function LocalityHeatmap() {
  const [activeFilter, setActiveFilter] = useHeatState('All Calls');
  const [hovered, setHovered] = useHeatState(null);
  const [animated, setAnimated] = useHeatState(false);

  useHeatEffect(() => {
    setTimeout(() => setAnimated(true), 100);
  }, []);

  const topSuburbs = [...SUBURB_DATA].sort((a, b) => b.calls - a.calls).slice(0, 6);
  const maxCalls = topSuburbs[0].calls;

  return (
    <div style={{ background: '#fff', borderRadius: 14, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
      {/* Section header */}
      <div style={{
        padding: '18px 24px', borderBottom: '1px solid #F0F1F3',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center'
      }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
            <span className="material-symbols-outlined ms-fill" style={{ fontSize: 18, color: '#C8232C' }}>layers</span>
            <span style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 15, color: '#1A1A1A' }}>Locality Heatmap</span>
          </div>
          <div style={{ fontSize: 11, color: '#767676' }}>Georges River LGA — live call volume by suburb</div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {['All Calls', 'English Only', 'CALD'].map(f => (
            <button key={f} onClick={() => setActiveFilter(f)} style={{
              padding: '5px 14px', borderRadius: 20, fontSize: 11, fontWeight: 700,
              background: activeFilter === f ? '#C8232C' : '#F4F5F6',
              color: activeFilter === f ? '#fff' : '#767676',
              border: `1px solid ${activeFilter === f ? '#C8232C' : '#E8E9EB'}`,
              transition: 'all 0.15s'
            }}>{f}</button>
          ))}
        </div>
      </div>

      {/* Body: map + sidebar */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 260px' }}>
        {/* SVG Map */}
        <div style={{ position: 'relative', background: '#F0F8F8', borderRight: '1px solid #F0F1F3' }}>
          <svg viewBox="0 0 520 320" style={{ width: '100%', display: 'block' }}>
            {/* Background */}
            <rect width="520" height="320" fill="#EDF5F5" />

            {/* Styled land areas — impressionistic LGA shape */}
            <path d="M 30,30 L 490,30 L 490,290 L 30,290 Z" fill="#F5F5F0" />

            {/* Georges River — winding teal waterway */}
            <path d="M 30,265 Q 80,250 120,245 Q 165,240 200,248 Q 240,258 275,252 Q 310,246 345,258 Q 385,272 420,268 Q 455,264 490,270"
              fill="none" stroke="#00A9A5" strokeWidth="14" strokeLinecap="round" opacity="0.25" />
            <path d="M 30,265 Q 80,250 120,245 Q 165,240 200,248 Q 240,258 275,252 Q 310,246 345,258 Q 385,272 420,268 Q 455,264 490,270"
              fill="none" stroke="#00A9A5" strokeWidth="5" strokeLinecap="round" opacity="0.55" />

            {/* Cooks River hint — north */}
            <path d="M 30,85 Q 80,78 130,82 Q 175,86 210,78"
              fill="none" stroke="#00A9A5" strokeWidth="4" strokeLinecap="round" opacity="0.3" />

            {/* Suburb area blobs (very subtle) */}
            {SUBURB_DATA.map(s => (
              <circle key={s.name + '-area'} cx={s.x} cy={s.y}
                r={bubbleR(s.calls) * 2.2}
                fill={VOL_COLORS[s.volume].fill}
                opacity="0.06" />
            ))}

            {/* Grid lines (faint) */}
            {[1,2,3,4].map(i => (
              <React.Fragment key={i}>
                <line x1={i*104} y1="0" x2={i*104} y2="320" stroke="#E0E0E0" strokeWidth="0.5" />
                <line x1="0" y1={i*64} x2="520" y2={i*64} stroke="#E0E0E0" strokeWidth="0.5" />
              </React.Fragment>
            ))}

            {/* Suburb labels (faint background text) */}
            {SUBURB_DATA.slice(0, 8).map(s => (
              <text key={s.name + '-lbl'} x={s.x} y={s.y - bubbleR(s.calls) - 5}
                fontSize="7" fontFamily="Inter" fontWeight="600" fill="#aaa"
                textAnchor="middle" style={{ pointerEvents: 'none' }}>
                {s.name.toUpperCase()}
              </text>
            ))}

            {/* Bubbles */}
            {SUBURB_DATA.map(s => {
              const r = bubbleR(s.calls);
              const vc = VOL_COLORS[s.volume];
              const isHov = hovered === s.name;
              return (
                <g key={s.name} style={{ cursor: 'pointer' }}
                  onMouseEnter={() => setHovered(s.name)}
                  onMouseLeave={() => setHovered(null)}>
                  {/* Pulse ring */}
                  {(s.volume === 'critical' || s.volume === 'high') && (
                    <circle cx={s.x} cy={s.y} r={r + 6}
                      fill="none" stroke={vc.fill} strokeWidth="1.5" opacity={isHov ? 0.5 : 0.2} />
                  )}
                  <circle cx={s.x} cy={s.y}
                    r={animated ? (isHov ? r * 1.15 : r) : 0}
                    fill={vc.fill}
                    stroke={isHov ? '#fff' : vc.stroke}
                    strokeWidth={isHov ? 2.5 : 1.5}
                    opacity={isHov ? 1 : 0.82}
                    style={{ transition: 'r 0.3s ease, opacity 0.2s' }} />
                  {/* Call count inside large bubbles */}
                  {r > 16 && (
                    <text x={s.x} y={s.y + 1} fontSize={r > 22 ? "9" : "7"}
                      fontFamily="Inter" fontWeight="800" fill="#fff" textAnchor="middle"
                      dominantBaseline="middle" style={{ pointerEvents: 'none' }}>
                      {s.calls}
                    </text>
                  )}
                </g>
              );
            })}

            {/* Tooltip */}
            {hovered && (() => {
              const s = SUBURB_DATA.find(d => d.name === hovered);
              if (!s) return null;
              const tx = s.x > 380 ? s.x - 110 : s.x + 14;
              const ty = s.y > 240 ? s.y - 52 : s.y - 8;
              return (
                <g>
                  <rect x={tx} y={ty} width={100} height={40} rx="6" fill="#1A1A1A" opacity="0.88" />
                  <text x={tx + 8} y={ty + 14} fontSize="9" fontFamily="Manrope" fontWeight="800" fill="#fff">{s.name}</text>
                  <text x={tx + 8} y={ty + 27} fontSize="10" fontFamily="Manrope" fontWeight="900" fill="#E8796C">{s.calls} calls</text>
                  <text x={tx + 8} y={ty + 38} fontSize="7" fontFamily="Inter" fontWeight="600" fill="#aaa">{s.volume.toUpperCase()}</text>
                </g>
              );
            })()}

            {/* Legend */}
            <g transform="translate(18, 265)">
              <rect x="-4" y="-14" width="128" height="60" rx="6" fill="white" opacity="0.92" />
              <text x="0" y="-2" fontSize="7" fontFamily="Inter" fontWeight="800" fill="#aaa" letterSpacing="1">VOLUME SCALE</text>
              {Object.entries(VOL_COLORS).map(([k, v], i) => (
                <g key={k} transform={`translate(0, ${i * 11 + 8})`}>
                  <circle cx="5" cy="0" r="4" fill={v.fill} />
                  <text x="13" y="4" fontSize="7.5" fontFamily="Inter" fontWeight="600" fill="#5A5F6B">{v.label}</text>
                </g>
              ))}
            </g>

            {/* Compass */}
            <g transform="translate(488, 42)">
              <circle cx="0" cy="0" r="14" fill="white" opacity="0.9" />
              <text x="0" y="-5" fontSize="8" fontFamily="Inter" fontWeight="800" fill="#1A1A1A" textAnchor="middle">N</text>
              <path d="M 0,-12 L 3,-2 L 0,0 Z" fill="#C8232C" />
              <path d="M 0,12 L -3,2 L 0,0 Z" fill="#aaa" />
            </g>

            {/* Zoom controls */}
            <g transform="translate(18, 30)">
              <rect x="0" y="0" width="22" height="22" rx="4" fill="white" stroke="#E8E9EB" strokeWidth="1" />
              <text x="11" y="15" fontSize="14" fontFamily="Inter" fontWeight="300" fill="#1A1A1A" textAnchor="middle">+</text>
              <rect x="0" y="26" width="22" height="22" rx="4" fill="white" stroke="#E8E9EB" strokeWidth="1" />
              <text x="11" y="40" fontSize="14" fontFamily="Inter" fontWeight="300" fill="#1A1A1A" textAnchor="middle">−</text>
            </g>
          </svg>
        </div>

        {/* Top suburbs sidebar */}
        <div style={{ padding: '20px 18px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#aaa', marginBottom: 4 }}>Top Suburbs</div>
          {topSuburbs.map((s, i) => {
            const vc = VOL_COLORS[s.volume];
            const barW = (s.calls / maxCalls) * 100;
            return (
              <div key={s.name}
                style={{ cursor: 'pointer', padding: '8px 10px', borderRadius: 8, transition: 'background 0.15s',
                  background: hovered === s.name ? '#F9F9F9' : 'transparent' }}
                onMouseEnter={() => setHovered(s.name)}
                onMouseLeave={() => setHovered(null)}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{
                      width: 20, height: 20, borderRadius: '50%', background: vc.fill,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 9, fontWeight: 900, color: '#fff', flexShrink: 0
                    }}>{i + 1}</span>
                    <span style={{ fontSize: 13, fontWeight: 700, color: '#1A1A1A' }}>{s.name}</span>
                  </div>
                  <span style={{ fontSize: 13, fontWeight: 800, color: vc.fill, fontVariantNumeric: 'tabular-nums' }}>{s.calls}</span>
                </div>
                <div style={{ height: 4, borderRadius: 2, background: '#F0F1F3', overflow: 'hidden' }}>
                  <div style={{
                    width: `${barW}%`, height: '100%',
                    background: `linear-gradient(90deg, ${vc.fill}, ${vc.stroke})`,
                    borderRadius: 2, transition: 'width 0.8s ease'
                  }} />
                </div>
              </div>
            );
          })}

          {/* Live indicator */}
          <div style={{ marginTop: 'auto', padding: '10px 10px', background: '#D0F2F1', borderRadius: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="live-blink" style={{ width: 7, height: 7, borderRadius: '50%', background: '#00A9A5', display: 'inline-block', flexShrink: 0 }} />
            <span style={{ fontSize: 10, fontWeight: 700, color: '#007A77' }}>Refreshing live · updated {new Date().toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' })}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function FrictionTracker() {
  const maxTotal = Math.max(...FRICTION_SUBURBS.map(s => Object.values(FRICTION_DATA[s]).reduce((a,b)=>a+b,0)));

  return (
    <div style={{ background: '#fff', borderRadius: 14, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: '18px 24px', borderBottom: '1px solid #F0F1F3', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, background: '#F9E6E7', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span className="material-symbols-outlined" style={{ fontSize: 17, color: '#C8232C' }}>troubleshoot</span>
        </div>
        <div>
          <div style={{ fontFamily: 'Manrope', fontWeight: 800, fontSize: 15, color: '#1A1A1A' }}>Friction Point Tracker</div>
          <div style={{ fontSize: 11, color: '#767676' }}>Area vs. issue correlation — this week</div>
        </div>
      </div>

      <div style={{ padding: '24px' }}>
        {/* Stacked bar chart */}
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 20, height: 160, marginBottom: 12, paddingBottom: 4 }}>
          {FRICTION_SUBURBS.map(suburb => {
            const data = FRICTION_DATA[suburb];
            const total = Object.values(data).reduce((a,b)=>a+b,0);
            const heightPct = (total / maxTotal) * 100;
            return (
              <div key={suburb} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', height: '100%', justifyContent: 'flex-end' }}>
                <div style={{ fontSize: 9, fontWeight: 700, color: '#aaa', marginBottom: 4 }}>{total}</div>
                <div style={{
                  width: '100%', height: `${heightPct}%`, display: 'flex', flexDirection: 'column-reverse',
                  borderRadius: '4px 4px 0 0', overflow: 'hidden', minHeight: 8
                }}>
                  {FRICTION_CATS.map(cat => {
                    const val = data[cat.key];
                    const segPct = (val / total) * 100;
                    return (
                      <div key={cat.key} title={`${cat.label}: ${val}`} style={{
                        width: '100%', height: `${segPct}%`,
                        background: cat.color, minHeight: val > 0 ? 2 : 0,
                        transition: 'height 0.6s ease'
                      }} />
                    );
                  })}
                </div>
                <div style={{
                  fontSize: 9, fontWeight: 700, color: '#767676', marginTop: 7, textAlign: 'center',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', width: '100%'
                }}>{suburb}</div>
              </div>
            );
          })}
        </div>

        {/* Legend */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', marginBottom: 22, paddingTop: 6, borderTop: '1px solid #F0F1F3' }}>
          {FRICTION_CATS.map(cat => (
            <span key={cat.key} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, fontWeight: 600, color: '#5A5F6B' }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: cat.color, display: 'inline-block' }} />
              {cat.label}
            </span>
          ))}
        </div>

        {/* Consistent Issues */}
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#C8232C', marginBottom: 12 }}>
            Consistent Issues — Action Required
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {CONSISTENT_ISSUES.map((issue, i) => {
              const cat = FRICTION_CATS.find(c => c.label === issue.type);
              return (
                <div key={i} style={{
                  padding: '10px 14px', borderRadius: 9, border: '1px solid #F9E6E7',
                  background: '#FFFAFB', display: 'flex', alignItems: 'flex-start', gap: 10
                }}>
                  <span className="material-symbols-outlined ms-fill" style={{ fontSize: 15, color: '#C8232C', flexShrink: 0, marginTop: 1 }}>error</span>
                  <div style={{ fontSize: 12, color: '#5A5F6B', lineHeight: 1.5 }}>
                    <strong style={{ color: '#1A1A1A' }}>{issue.suburb}</strong>
                    {': Consistent '}
                    <strong style={{ color: cat?.color || '#C8232C' }}>{issue.type}</strong>
                    {` inquiries (${issue.count} this week). Suggested action: ${issue.action}`}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { LocalityHeatmap, FrictionTracker });
