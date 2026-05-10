// Sidebar + Header shell components

const { useState, useEffect } = React;

const NAV = [
  { id: 'dashboard',  icon: 'dashboard',      label: 'Dashboard' },
  { id: 'live-calls', icon: 'headset_mic',     label: 'Live Calls',  badge: true },
  { id: 'agents',     icon: 'smart_toy',       label: 'AI Agents' },
  { id: 'history',    icon: 'history',         label: 'History' },
  { id: 'translation', icon: 'g_translate',    label: 'Live Translation' },
];

function useIsMobileShell() {
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 768);
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return isMobile;
}

function Sidebar({ activePage, onNavigate, style = 'light' }) {
  const isMobile = useIsMobileShell();
  const isDark = style === 'dark';
  const isRed  = style === 'red';

  const bg      = isDark ? '#111827' : isRed ? '#C8232C' : '#FFFFFF';
  const border  = isDark ? '#1f2937' : isRed ? '#9E1B22' : '#F0F1F3';
  const logoTxt = isDark || isRed ? '#fff' : '#1A1A1A';
  const navBase = isDark || isRed ? 'rgba(255,255,255,0.08)' : '#F4F5F6';
  const navActiveBg = isDark ? '#C8232C' : isRed ? 'rgba(255,255,255,0.2)' : '#C8232C';
  const navTxt  = isDark || isRed ? 'rgba(255,255,255,0.55)' : '#767676';
  const navActiveTxt = '#fff';
  const divider = isDark ? '#1f2937' : isRed ? '#9E1B22' : '#F0F1F3';

  if (isMobile) {
    return (
      <nav style={{
        position: 'fixed', left: 0, right: 0, bottom: 0, height: 64,
        background: bg, borderTop: `1px solid ${border}`, zIndex: 30,
        display: 'grid', gridTemplateColumns: `repeat(${NAV.length}, 1fr)`,
        padding: '6px 6px calc(6px + env(safe-area-inset-bottom))',
        boxShadow: '0 -8px 24px rgba(0,0,0,0.08)'
      }}>
        {NAV.map(item => {
          const active = activePage === item.id || (item.id === 'live-calls' && activePage === 'takeover');
          return (
            <button key={item.id} onClick={() => onNavigate(item.id)} style={{
              minWidth: 0, borderRadius: 10, background: active ? navActiveBg : 'transparent',
              color: active ? navActiveTxt : navTxt, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', gap: 2, fontSize: 9,
              fontWeight: active ? 800 : 700
            }}>
              <span className="material-symbols-outlined" style={{ fontSize: 19 }}>{item.icon}</span>
              <span style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.label.replace('Live ', '')}
              </span>
            </button>
          );
        })}
      </nav>
    );
  }

  return (
    <aside style={{
      position: 'fixed', top: 0, left: 0, width: 240, height: '100vh',
      background: bg, borderRight: `1px solid ${border}`,
      display: 'flex', flexDirection: 'column', zIndex: 20,
      transition: 'background 0.3s'
    }}>
      {/* Brand */}
      <div style={{
        height: 64, display: 'flex', alignItems: 'center',
        padding: '0 14px', borderBottom: `1px solid ${divider}`, gap: 9, cursor: 'pointer'
      }} onClick={() => onNavigate('dashboard')}>
        <img
          src="/static/grc-icon.png"
          alt="GRC"
          style={{
            height: 40, width: 40, objectFit: 'contain', flexShrink: 0,
            filter: isDark || isRed ? 'brightness(0) invert(1)' : 'none',
            transition: 'filter 0.3s'
          }}
        />
        <div style={{ lineHeight: 1.1 }}>
          <div style={{
            fontFamily: 'Manrope', fontWeight: 900, fontSize: 12.5,
            color: isDark || isRed ? '#fff' : '#C8232C',
            letterSpacing: '0.03em', textTransform: 'uppercase'
          }}>Georges River</div>
          <div style={{
            fontFamily: 'Manrope', fontWeight: 800, fontSize: 11,
            color: isDark || isRed ? 'rgba(255,255,255,0.75)' : '#00A9A5',
            letterSpacing: '0.12em', textTransform: 'uppercase'
          }}>Council</div>
        </div>
      </div>

      {/* Section label */}
      <div style={{ padding: '20px 20px 8px', fontSize: 9, fontWeight: 800, letterSpacing: '0.12em', color: navTxt, textTransform: 'uppercase' }}>
        Command Centre
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: '0 10px', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {NAV.map(item => {
          const active = activePage === item.id || (item.id === 'live-calls' && activePage === 'takeover');
          return (
            <button key={item.id} onClick={() => onNavigate(item.id)} style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '10px 14px', borderRadius: 10, width: '100%',
              background: active ? navActiveBg : 'transparent',
              color: active ? navActiveTxt : navTxt,
              fontWeight: active ? 700 : 500, fontSize: 13,
              transition: 'all 0.15s', textAlign: 'left',
              position: 'relative'
            }}
            onMouseEnter={e => { if (!active) e.currentTarget.style.background = navBase; }}
            onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}>
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{item.icon}</span>
              {item.label}
              {item.badge && activePage !== 'live-calls' && (
                <span className="live-blink" style={{
                  marginLeft: 'auto', width: 7, height: 7,
                  borderRadius: '50%', background: '#C8232C',
                  ...(isDark || isRed ? { background: '#fff' } : {})
                }} />
              )}
            </button>
          );
        })}
      </nav>

      {/* System status */}
      <div style={{
        margin: '0 10px 10px',
        background: isDark ? 'rgba(255,255,255,0.05)' : isRed ? 'rgba(255,255,255,0.12)' : '#F4F5F6',
        borderRadius: 12, padding: '12px 14px'
      }}>
        <div style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.12em', color: navTxt, textTransform: 'uppercase', marginBottom: 10 }}>System Health</div>
        {[
          { label: 'AI Engine', ok: true },
          { label: 'Telephony', ok: true },
          { label: 'Sentiment Model', ok: true },
        ].map(s => (
          <div key={s.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 500, color: isDark || isRed ? 'rgba(255,255,255,0.6)' : '#5A5F6B' }}>{s.label}</span>
            <span style={{
              fontSize: 9, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.06em',
              color: '#00A9A5', background: isDark ? 'rgba(0,169,165,0.15)' : '#D0F2F1',
              padding: '2px 7px', borderRadius: 4
            }}>Online</span>
          </div>
        ))}
      </div>

      {/* User */}
      <div style={{
        padding: '12px 14px', borderTop: `1px solid ${divider}`,
        display: 'flex', alignItems: 'center', gap: 10
      }}>
        <div style={{
          width: 34, height: 34, borderRadius: 10, flexShrink: 0,
          background: isDark || isRed ? 'rgba(255,255,255,0.15)' : '#F9E6E7',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, fontWeight: 800, color: isDark || isRed ? '#fff' : '#C8232C'
        }}>AU</div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: logoTxt, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>Admin User</div>
          <div style={{ fontSize: 10, color: navTxt, fontWeight: 600, letterSpacing: '0.04em' }}>System Ops</div>
        </div>
      </div>
    </aside>
  );
}

function Header({ activePage }) {
  const [dark, setDark] = useState(false);
  const [now, setNow] = useState(new Date());
  const isMobile = useIsMobileShell();

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    document.documentElement.style.filter = dark ? 'invert(1) hue-rotate(180deg)' : '';
  }, [dark]);

  const pageLabels = {
    'dashboard': 'Dashboard',
    'live-calls': 'Live Calls Monitor',
    'agents': 'AI Agent Profiles',
    'history': 'History & Insights',
    'translation': 'Live Translation',
  };

  return (
    <header style={{
      position: 'fixed', top: 0, left: isMobile ? 0 : 240, right: 0, height: isMobile ? 56 : 64,
      background: 'rgba(244,245,246,0.85)', backdropFilter: 'blur(12px)',
      borderBottom: '1px solid #E8E9EB', zIndex: 10,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: isMobile ? '0 12px' : '0 36px'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="material-symbols-outlined live-blink" style={{ fontSize: 14, color: '#C8232C' }}>radio_button_checked</span>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#767676' }}>
          {pageLabels[activePage] || 'Live Command'}
        </span>
        <span style={{ fontSize: 11, color: '#bbb', margin: '0 4px', display: isMobile ? 'none' : 'inline' }}>·</span>
        <span style={{ fontSize: 11, fontWeight: 600, color: '#aaa', fontVariantNumeric: 'tabular-nums', display: isMobile ? 'none' : 'inline' }}>
          {now.toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {/* Search */}
        <div style={{ position: 'relative', display: isMobile ? 'none' : 'block' }}>
          <span className="material-symbols-outlined" style={{
            position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)',
            fontSize: 16, color: '#aaa'
          }}>search</span>
          <input placeholder="Search sessions…" style={{
            background: '#fff', border: '1px solid #E8E9EB', borderRadius: 20,
            padding: '6px 16px 6px 32px', fontSize: 12, fontWeight: 500,
            color: '#1A1A1A', outline: 'none', width: 220
          }} />
        </div>
        {/* Notif */}
        <button style={{
          width: 34, height: 34, borderRadius: 10, background: '#fff',
          border: '1px solid #E8E9EB', display: 'flex', alignItems: 'center', justifyContent: 'center',
          position: 'relative', color: '#767676'
        }}>
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>notifications</span>
          <span style={{
            position: 'absolute', top: 6, right: 6, width: 7, height: 7,
            borderRadius: '50%', background: '#C8232C', border: '2px solid #F4F5F6'
          }} />
        </button>
        {/* Dark toggle */}
        <button onClick={() => setDark(!dark)} style={{
          width: 34, height: 34, borderRadius: 10, background: '#fff',
          border: '1px solid #E8E9EB', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#767676'
        }}>
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{dark ? 'light_mode' : 'dark_mode'}</span>
        </button>
      </div>
    </header>
  );
}

Object.assign(window, { Sidebar, Header });
