// Synthetic Calls page - Georges River Council

function SyntheticCallsPage() {
  const [scenarios, setScenarios] = React.useState([]);
  const [selected, setSelected] = React.useState({});
  const [generated, setGenerated] = React.useState([]);
  const [generatedSelected, setGeneratedSelected] = React.useState({});
  const [jobs, setJobs] = React.useState([]);
  const [activeJobId, setActiveJobId] = React.useState(null);
  const [activeJob, setActiveJob] = React.useState(null);
  const [liveCall, setLiveCall] = React.useState(null);
  const [audioTick, setAudioTick] = React.useState(0);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [prompt, setPrompt] = React.useState('Test short caller responses and noisy bin address pickup.');
  const [residentPersona, setResidentPersona] = React.useState('A realistic Georges River Council resident. They are polite but a little impatient, speak naturally, and give concise answers.');
  const [residentGoal, setResidentGoal] = React.useState('Choose English, ask for bin collection help, provide 50 Warraba Street Hurstville, confirm the address if it is correct, then ask one follow-up about council events.');
  const [evaluate, setEvaluate] = React.useState(false);
  const [options, setOptions] = React.useState({
    gain: 1,
    noise: 0,
    background_voice: '',
    background_gain: 0.25,
    speed: 1,
    listen_secs: 14,
    autonomous_turns: 6,
    autonomous_agent_timeout: 18
  });

  React.useEffect(() => {
    refreshScenarios();
    refreshJobs();
  }, []);

  React.useEffect(() => {
    const t = setInterval(() => {
      refreshJobs();
      if (activeJobId) {
        refreshJob(activeJobId);
        refreshLiveCall();
      }
    }, 1200);
    return () => clearInterval(t);
  }, [activeJobId, activeJob?.current_monitor_id, activeJob?.last_monitor_id]);

  async function api(path, init) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  async function refreshScenarios() {
    try {
      const data = await api('/api/synthetic/scenarios');
      setScenarios(data.scenarios || []);
      setSelected(prev => {
        if (Object.keys(prev).length) return prev;
        const next = {};
        (data.scenarios || []).slice(0, 3).forEach(s => next[s.id] = true);
        return next;
      });
    } catch (err) {
      setError(err.message || 'Could not load scenarios');
    }
  }

  async function refreshJobs() {
    try {
      const data = await api('/api/synthetic/jobs');
      setJobs(data.jobs || []);
    } catch (err) {
      setError(err.message || 'Could not load jobs');
    }
  }

  async function refreshJob(jobId) {
    try {
      const data = await api(`/api/synthetic/jobs/${jobId}`);
      setActiveJob(data);
      refreshLiveCall(data);
    } catch (err) {
      setError(err.message || 'Could not load job');
    }
  }

  async function refreshLiveCall(jobOverride) {
    const sourceJob = jobOverride || activeJob;
    const monitorId = sourceJob?.current_monitor_id || sourceJob?.last_monitor_id || sourceJob?.results?.[sourceJob.results.length - 1]?.call_id;
    if (!monitorId) {
      setLiveCall(null);
      return;
    }
    const wantedId = monitorId.includes(':') ? monitorId : `telnyx:${monitorId}`;
    try {
      const data = await api('/api/live-calls');
      const calls = Array.isArray(data.calls) ? data.calls : [];
      setLiveCall(calls.find(call => call.id === wantedId) || null);
    } catch (err) {
      setError(err.message || 'Could not load live call');
    }
  }

  function selectedIds() {
    return Object.keys(selected).filter(id => selected[id]);
  }

  async function runSelected() {
    const ids = selectedIds();
    if (!ids.length) {
      setError('Select at least one scenario.');
      return;
    }
    await runPayload({ scenario_ids: ids });
  }

  async function runAll() {
    const next = {};
    scenarios.forEach(s => next[s.id] = true);
    setSelected(next);
    await runPayload({ scenario_ids: scenarios.map(s => s.id) });
  }

  async function runGenerated() {
    const chosen = generated.filter(s => generatedSelected[s.id]);
    if (!chosen.length) {
      setError('Select at least one generated scenario first.');
      return;
    }
    await runPayload({ scenarios: chosen });
  }

  async function runAutonomousResident() {
    const scenario = {
      id: `autonomous_resident_${Date.now()}`,
      description: 'LLM-driven resident persona call',
      autonomous: true,
      persona: residentPersona,
      goal: residentGoal,
      max_turns: Number(options.autonomous_turns),
      expectations: [
        'resident agent engages in multiple turns like a realistic GRC caller',
        'main agent handles the chosen goal without unnecessary repetition',
        'latency between caller and agent turns is recorded'
      ]
    };
    await runPayload({ scenarios: [scenario] });
  }

  async function runPayload(extra) {
    setLoading(true);
    setError('');
    try {
      const job = await api('/api/synthetic/run', {
        method: 'POST',
        body: JSON.stringify({
          ...extra,
          evaluate,
          options: normalizedOptions()
        })
      });
      setActiveJobId(job.id);
      setActiveJob(job);
      setLiveCall(null);
      setAudioTick(tick => tick + 1);
      await refreshJobs();
    } catch (err) {
      setError(err.message || 'Could not start synthetic call run');
    } finally {
      setLoading(false);
    }
  }

  async function generateScenarios() {
    setLoading(true);
    setError('');
    try {
      const data = await api('/api/synthetic/generate', {
        method: 'POST',
        body: JSON.stringify({ prompt, count: 4 })
      });
      const nextGenerated = data.scenarios || [];
      setGenerated(nextGenerated);
      const nextSelected = {};
      nextGenerated.forEach(s => nextSelected[s.id] = true);
      setGeneratedSelected(nextSelected);
    } catch (err) {
      setError(err.message || 'Could not generate scenarios');
    } finally {
      setLoading(false);
    }
  }

  function normalizedOptions() {
    return {
      ...options,
      gain: Number(options.gain),
      noise: Number(options.noise),
      background_gain: Number(options.background_gain),
      speed: Number(options.speed),
      listen_secs: Number(options.listen_secs),
      autonomous_turns: Number(options.autonomous_turns),
      autonomous_agent_timeout: Number(options.autonomous_agent_timeout),
      resident_persona: residentPersona,
      resident_goal: residentGoal
    };
  }

  function setOpt(key, value) {
    setOptions(prev => ({ ...prev, [key]: value }));
  }

  const job = activeJob || jobs[0] || null;
  const passCount = job?.results?.filter(r => r.status === 'PASS').length || 0;
  const totalDone = job?.results?.length || 0;
  const total = job?.total || 0;

  return (
    <div style={{ padding: '28px 36px', display: 'flex', flexDirection: 'column', gap: 22, minHeight: 'calc(100vh - 64px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 16 }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.1em', color: '#aaa', textTransform: 'uppercase', marginBottom: 4 }}>
            QA / <span style={{ color: '#C8232C' }}>Synthetic Calls</span>
          </div>
          <div style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 26, color: '#1A1A1A' }}>Synthetic Call Lab</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="material-symbols-outlined" style={{ fontSize: 18, color: '#00A9A5' }}>science</span>
          <span style={{ fontSize: 12, fontWeight: 800, color: '#00A9A5' }}>
            {job ? `${totalDone}/${total} Complete` : `${scenarios.length} Cases`}
          </span>
        </div>
      </div>

      {error && (
        <div style={{ background: '#F9E6E7', color: '#C8232C', border: '1px solid #F2CDD0', borderRadius: 8, padding: '10px 12px', fontSize: 12, fontWeight: 700 }}>
          {error}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(380px, 0.95fr) minmax(420px, 1.05fr)', gap: 18, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <section style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
            <PanelHeader icon="fact_check" title="Test Cases" subtitle={`${selectedIds().length} selected`} />
            <div style={{ padding: 16, display: 'flex', gap: 8, flexWrap: 'wrap', borderBottom: '1px solid #F0F1F3' }}>
              <ActionButton icon="play_arrow" label="Run Selected" primary onClick={runSelected} disabled={loading} />
              <ActionButton icon="playlist_play" label="Run All" onClick={runAll} disabled={loading || !scenarios.length} />
              <ActionButton icon="select_all" label="Select All" onClick={() => {
                const next = {};
                scenarios.forEach(s => next[s.id] = true);
                setSelected(next);
              }} />
              <ActionButton icon="deselect" label="Clear" onClick={() => setSelected({})} />
            </div>
            <div style={{ maxHeight: 430, overflowY: 'auto' }}>
              {scenarios.map(s => (
                <ScenarioRow
                  key={s.id}
                  scenario={s}
                  checked={!!selected[s.id]}
                  onChange={() => setSelected(prev => ({ ...prev, [s.id]: !prev[s.id] }))}
                />
              ))}
              {!scenarios.length && <EmptyState text="No scenarios found." />}
            </div>
          </section>

          <section style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
            <PanelHeader icon="tune" title="Audio Conditions" subtitle="Caller simulation" />
            <div style={{ padding: 16, display: 'grid', gap: 12 }}>
              <ToggleRow label="LLM evaluator" checked={evaluate} onChange={() => setEvaluate(v => !v)} />
              <RangeRow label="Caller gain" value={options.gain} min="0.2" max="1.5" step="0.05" onChange={v => setOpt('gain', v)} />
              <RangeRow label="Noise" value={options.noise} min="0" max="0.12" step="0.01" onChange={v => setOpt('noise', v)} />
              <RangeRow label="Speed" value={options.speed} min="0.75" max="1.25" step="0.05" onChange={v => setOpt('speed', v)} />
              <RangeRow label="Listen seconds" value={options.listen_secs} min="6" max="30" step="1" onChange={v => setOpt('listen_secs', v)} />
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 800, color: '#767676', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Background voice</span>
                <textarea
                  value={options.background_voice}
                  onChange={e => setOpt('background_voice', e.target.value)}
                  placeholder="Optional overlapping speech..."
                  style={inputStyle({ minHeight: 68, resize: 'vertical' })}
                />
              </label>
            </div>
          </section>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <section style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
            <PanelHeader icon="record_voice_over" title="Resident Agent" subtitle="Autonomous call" />
            <div style={{ padding: 16, display: 'grid', gap: 12 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 800, color: '#767676', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Persona</span>
                <textarea
                  value={residentPersona}
                  onChange={e => setResidentPersona(e.target.value)}
                  style={inputStyle({ minHeight: 82, resize: 'vertical' })}
                />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 800, color: '#767676', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Goal</span>
                <textarea
                  value={residentGoal}
                  onChange={e => setResidentGoal(e.target.value)}
                  style={inputStyle({ minHeight: 82, resize: 'vertical' })}
                />
              </label>
              <RangeRow label="Max resident turns" value={options.autonomous_turns} min="2" max="12" step="1" onChange={v => setOpt('autonomous_turns', v)} />
              <RangeRow label="Agent wait timeout" value={options.autonomous_agent_timeout} min="8" max="35" step="1" onChange={v => setOpt('autonomous_agent_timeout', v)} />
              <ActionButton icon="smart_toy" label="Run Resident Agent" primary onClick={runAutonomousResident} disabled={loading || !residentPersona.trim() || !residentGoal.trim()} />
            </div>
          </section>

          <LiveConversationPanel job={job} liveCall={liveCall} />
          <LiveAudioPanel job={job} audioTick={audioTick} onRefresh={() => setAudioTick(tick => tick + 1)} />

          <section style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
            <PanelHeader icon="auto_awesome" title="Generate Cases" subtitle="Use current LLM" />
            <div style={{ padding: 16, display: 'grid', gap: 12 }}>
              <textarea
                value={prompt}
                onChange={e => setPrompt(e.target.value)}
                style={inputStyle({ minHeight: 92, resize: 'vertical' })}
              />
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <ActionButton icon="auto_fix_high" label="Generate" primary onClick={generateScenarios} disabled={loading || !prompt.trim()} />
                <ActionButton icon="play_arrow" label="Run Generated" onClick={runGenerated} disabled={loading || !generated.some(s => generatedSelected[s.id])} />
                <ActionButton icon="select_all" label="Select Generated" onClick={() => {
                  const next = {};
                  generated.forEach(s => next[s.id] = true);
                  setGeneratedSelected(next);
                }} disabled={!generated.length} />
              </div>
              {!!generated.length && (
                <div style={{ display: 'grid', gap: 8 }}>
                  {generated.map(s => (
                    <GeneratedScenario
                      key={s.id}
                      scenario={s}
                      checked={!!generatedSelected[s.id]}
                      onChange={() => setGeneratedSelected(prev => ({ ...prev, [s.id]: !prev[s.id] }))}
                    />
                  ))}
                </div>
              )}
            </div>
          </section>

          <section style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
            <PanelHeader icon="monitor_heart" title="Run Results" subtitle={job ? job.status : 'Idle'} />
            <div style={{ padding: 16 }}>
              {job ? (
                <JobView job={job} passCount={passCount} totalDone={totalDone} total={total} onSelectJob={setActiveJobId} jobs={jobs} />
              ) : (
                <EmptyState text="Run a synthetic call test to see results here." />
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function PanelHeader({ icon, title, subtitle }) {
  return (
    <div style={{ padding: '14px 16px', borderBottom: '1px solid #F0F1F3', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <span className="material-symbols-outlined" style={{ fontSize: 18, color: '#C8232C' }}>{icon}</span>
        <div style={{ fontSize: 14, fontWeight: 900, color: '#1A1A1A' }}>{title}</div>
      </div>
      <div style={{ fontSize: 10, fontWeight: 800, color: '#8A8F98', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{subtitle}</div>
    </div>
  );
}

function ActionButton({ icon, label, primary, disabled, onClick }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '8px 12px', borderRadius: 7,
      background: disabled ? '#F4F5F6' : primary ? '#C8232C' : '#F4F5F6',
      color: disabled ? '#AAA' : primary ? '#fff' : '#5A5F6B',
      fontSize: 11, fontWeight: 900, letterSpacing: '0.04em', textTransform: 'uppercase',
      cursor: disabled ? 'not-allowed' : 'pointer'
    }}>
      <span className="material-symbols-outlined" style={{ fontSize: 15 }}>{icon}</span>
      {label}
    </button>
  );
}

function ScenarioRow({ scenario, checked, onChange }) {
  return (
    <label style={{ display: 'flex', gap: 11, padding: '13px 16px', borderBottom: '1px solid #F8F9FA', cursor: 'pointer' }}>
      <input type="checkbox" checked={checked} onChange={onChange} style={{ marginTop: 3, width: 15, height: 15, accentColor: '#C8232C' }} />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 900, color: '#1A1A1A' }}>{scenario.id}</div>
        <div style={{ marginTop: 3, fontSize: 12, color: '#5A5F6B', lineHeight: 1.4 }}>{scenario.description}</div>
        <div style={{ marginTop: 7, display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          {(scenario.utterances || []).map((u, i) => (
            <span key={i} style={{ fontSize: 10, fontWeight: 700, color: '#767676', background: '#F4F5F6', borderRadius: 5, padding: '3px 6px' }}>{u}</span>
          ))}
        </div>
      </div>
    </label>
  );
}

function GeneratedScenario({ scenario, checked, onChange }) {
  return (
    <label style={{ border: '1px solid #E8E9EB', borderRadius: 8, padding: 11, background: '#FAFAFA', display: 'flex', gap: 10, cursor: 'pointer' }}>
      <input type="checkbox" checked={checked} onChange={onChange} style={{ marginTop: 2, width: 15, height: 15, accentColor: '#C8232C' }} />
      <div>
        <div style={{ fontSize: 12, fontWeight: 900, color: '#1A1A1A' }}>{scenario.id}</div>
        <div style={{ marginTop: 3, fontSize: 12, color: '#5A5F6B', lineHeight: 1.4 }}>{scenario.description}</div>
        <div style={{ marginTop: 8, fontSize: 11, color: '#767676' }}>{(scenario.utterances || []).join(' / ')}</div>
      </div>
    </label>
  );
}

function ToggleRow({ label, checked, onChange }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <span style={{ fontSize: 12, fontWeight: 800, color: '#5A5F6B' }}>{label}</span>
      <input type="checkbox" checked={checked} onChange={onChange} style={{ width: 16, height: 16, accentColor: '#C8232C' }} />
    </label>
  );
}

function RangeRow({ label, value, min, max, step, onChange }) {
  return (
    <label style={{ display: 'grid', gap: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 12, fontWeight: 800, color: '#5A5F6B' }}>{label}</span>
        <span style={{ fontSize: 11, fontWeight: 900, color: '#C8232C', fontVariantNumeric: 'tabular-nums' }}>{value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(e.target.value)} />
    </label>
  );
}

function LiveConversationPanel({ job, liveCall }) {
  const monitorId = job?.current_monitor_id || job?.last_monitor_id || '';
  const transcript = liveCall && Array.isArray(liveCall.transcript) ? liveCall.transcript : [];
  const isRunning = job && ['queued', 'running'].includes(job.status);
  const subtitle = liveCall ? liveCall.status : isRunning ? 'Waiting for stream' : 'Idle';
  return (
    <section style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
      <PanelHeader icon="forum" title="Live Resident Conversation" subtitle={subtitle} />
      <div style={{ padding: 16, display: 'grid', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 800, color: '#767676', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {monitorId || 'Start an autonomous resident call to attach the live transcript.'}
          </div>
          <span style={{
            flexShrink: 0,
            fontSize: 10,
            fontWeight: 900,
            color: liveCall?.status === 'active' ? '#007A77' : '#767676',
            background: liveCall?.status === 'active' ? '#D0F2F1' : '#F4F5F6',
            borderRadius: 5,
            padding: '4px 7px',
            textTransform: 'uppercase'
          }}>
            {liveCall?.status || (isRunning ? 'connecting' : 'none')}
          </span>
        </div>
        <div style={{
          minHeight: 220,
          maxHeight: 360,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          background: '#FAFAFA',
          border: '1px solid #F0F1F3',
          borderRadius: 8,
          padding: 10
        }}>
          {transcript.map((line, index) => {
            const isAgent = line.speaker === 'agent';
            return (
              <div key={`${line.timestamp || index}-${index}`} style={{
                alignSelf: isAgent ? 'flex-end' : 'flex-start',
                maxWidth: '88%',
                background: isAgent ? '#F9E6E7' : '#fff',
                border: `1px solid ${isAgent ? '#F2CDD0' : '#E8E9EB'}`,
                borderRadius: 8,
                padding: '8px 10px'
              }}>
                <div style={{
                  fontSize: 9,
                  fontWeight: 900,
                  color: isAgent ? '#C8232C' : '#007A77',
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  marginBottom: 4
                }}>{isAgent ? 'Main agent' : 'Resident agent'}</div>
                <div style={{ fontSize: 12, lineHeight: 1.4, fontWeight: 650, color: '#1A1A1A', whiteSpace: 'pre-wrap' }}>{line.text}</div>
              </div>
            );
          })}
          {!transcript.length && (
            <div style={{ margin: 'auto', textAlign: 'center', color: '#8A8F98', fontSize: 12, fontWeight: 800 }}>
              {isRunning ? 'Call is starting. Transcript lines will appear here.' : 'No live autonomous conversation selected.'}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function LiveAudioPanel({ job, audioTick, onRefresh }) {
  const hasJob = !!job?.id;
  const isRunning = job && ['queued', 'running'].includes(job.status);
  const audioUrl = hasJob ? `/api/synthetic/jobs/${job.id}/audio.wav?v=${audioTick}` : '';
  const subtitle = job?.audio_available ? (isRunning ? 'Live snapshot' : 'Ready') : isRunning ? 'Recording' : 'Idle';
  return (
    <section style={{ background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.05)', overflow: 'hidden' }}>
      <PanelHeader icon="graphic_eq" title="Synthetic Call Audio" subtitle={subtitle} />
      <div style={{ padding: 16, display: 'grid', gap: 10 }}>
        {hasJob ? (
          <>
            <audio
              key={`${job.id}-${audioTick}`}
              controls
              preload="none"
              src={audioUrl}
              style={{ width: '100%' }}
            />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <ActionButton icon="refresh" label="Refresh Audio" onClick={onRefresh} />
              <a
                href={audioUrl}
                download={`${job.id}.wav`}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '8px 12px',
                  borderRadius: 7,
                  background: '#F4F5F6',
                  color: '#5A5F6B',
                  fontSize: 11,
                  fontWeight: 900,
                  letterSpacing: '0.04em',
                  textTransform: 'uppercase',
                  textDecoration: 'none'
                }}
              >
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>download</span>
                Download WAV
              </a>
              <span style={{ fontSize: 11, fontWeight: 700, color: '#8A8F98' }}>
                {isRunning ? 'Refresh to hear the latest captured audio.' : 'Full captured synthetic call audio.'}
              </span>
            </div>
          </>
        ) : (
          <EmptyState text="Run a synthetic call to capture playable audio." />
        )}
      </div>
    </section>
  );
}

function JobView({ job, passCount, totalDone, total, jobs, onSelectJob }) {
  const pct = total ? Math.round((totalDone / total) * 100) : 0;
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 900, color: '#1A1A1A' }}>{job.id}</div>
          <div style={{ marginTop: 3, fontSize: 11, color: '#8A8F98' }}>{job.target}</div>
        </div>
        <StatusPill status={job.status} />
      </div>
      <div style={{ height: 8, background: '#F4F5F6', borderRadius: 999, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: job.status === 'failed' ? '#C8232C' : '#00A9A5', transition: 'width 0.2s' }} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
        <Metric label="Passed" value={`${passCount}/${total}`} />
        <Metric label="Done" value={`${totalDone}/${total}`} />
        <Metric label="Evaluate" value={job.evaluate ? 'On' : 'Off'} />
      </div>
      {job.error && <div style={{ color: '#C8232C', fontSize: 12, fontWeight: 700 }}>{job.error}</div>}
      <div style={{ display: 'grid', gap: 10 }}>
        {(job.results || []).map(result => <ResultBlock key={result.id} result={result} />)}
        {!(job.results || []).length && <EmptyState text="Waiting for first scenario result." />}
      </div>
      {!!jobs.length && (
        <select value={job.id} onChange={e => onSelectJob(e.target.value)} style={inputStyle()}>
          {jobs.map(j => <option key={j.id} value={j.id}>{j.status} - {j.id}</option>)}
        </select>
      )}
    </div>
  );
}

function ResultBlock({ result }) {
  const pass = result.status === 'PASS';
  const transcript = result.transcript || [];
  const issues = result.evaluation?.issues || [];
  const residentTurns = result.metrics?.resident_turns || [];
  const turnLatencies = result.metrics?.turn_latencies || [];
  const agentAudioTurns = result.metrics?.agent_audio_transcripts || [];
  return (
    <div style={{ border: '1px solid #E8E9EB', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ padding: '10px 12px', background: pass ? '#F2FBFA' : '#FFF7F7', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 900, color: '#1A1A1A' }}>{result.id}</div>
          <div style={{ marginTop: 2, fontSize: 11, color: '#767676' }}>{result.evaluation?.summary || result.description}</div>
        </div>
        <StatusPill status={result.status} />
      </div>
      <div style={{ padding: 12, display: 'grid', gap: 8 }}>
        {issues.map((issue, i) => <div key={i} style={{ fontSize: 11, color: '#C8232C', fontWeight: 700 }}>{issue}</div>)}
        {!!residentTurns.length && (
          <div style={{ display: 'grid', gap: 4, background: '#FAFAFA', border: '1px solid #F0F1F3', borderRadius: 7, padding: 8 }}>
            <div style={{ fontSize: 10, fontWeight: 900, color: '#8A8F98', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Resident agent turns</div>
            {residentTurns.slice(-6).map((turn, i) => (
              <div key={i} style={{ fontSize: 11, color: '#5A5F6B' }}>
                <b>{turn.turn}.</b> {turn.utterance}
              </div>
            ))}
          </div>
        )}
        {!!agentAudioTurns.length && (
          <div style={{ display: 'grid', gap: 4, background: '#FAFAFA', border: '1px solid #F0F1F3', borderRadius: 7, padding: 8 }}>
            <div style={{ fontSize: 10, fontWeight: 900, color: '#8A8F98', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Main agent audio heard by resident STT</div>
            {agentAudioTurns.slice(-4).map((turn, i) => (
              <div key={i} style={{ fontSize: 11, color: '#5A5F6B' }}>
                <b>{turn.label || 'audio'}.</b> {turn.text}
              </div>
            ))}
          </div>
        )}
        {!!turnLatencies.length && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {turnLatencies.slice(-6).map((turn, i) => (
              <span key={i} style={{ fontSize: 10, fontWeight: 800, color: '#007A77', background: '#D0F2F1', borderRadius: 5, padding: '3px 6px' }}>
                reply {turn.latency_secs}s
              </span>
            ))}
          </div>
        )}
        {transcript.slice(-8).map((line, i) => (
          <div key={i} style={{ fontSize: 12, lineHeight: 1.4 }}>
            <b style={{ color: line.speaker === 'agent' ? '#C8232C' : '#007A77' }}>{line.speaker}:</b> {line.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div style={{ background: '#FAFAFA', border: '1px solid #F0F1F3', borderRadius: 8, padding: 10 }}>
      <div style={{ fontSize: 9, fontWeight: 900, color: '#AAA', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 16, fontWeight: 900, color: '#1A1A1A' }}>{value}</div>
    </div>
  );
}

function StatusPill({ status }) {
  const normalized = (status || 'idle').toLowerCase();
  const color = normalized === 'pass' || normalized === 'complete' ? '#00A9A5' : normalized === 'running' || normalized === 'queued' ? '#D4860A' : normalized === 'fail' || normalized === 'failed' ? '#C8232C' : '#767676';
  const bg = normalized === 'pass' || normalized === 'complete' ? '#D0F2F1' : normalized === 'running' || normalized === 'queued' ? '#FDF3E1' : normalized === 'fail' || normalized === 'failed' ? '#F9E6E7' : '#F4F5F6';
  return <span style={{ fontSize: 10, fontWeight: 900, color, background: bg, borderRadius: 5, padding: '4px 8px', textTransform: 'uppercase' }}>{status}</span>;
}

function EmptyState({ text }) {
  return <div style={{ padding: 24, textAlign: 'center', color: '#8A8F98', fontSize: 13, fontWeight: 700 }}>{text}</div>;
}

function inputStyle(extra = {}) {
  return {
    width: '100%',
    border: '1px solid #E8E9EB',
    borderRadius: 8,
    padding: '9px 10px',
    fontSize: 12,
    fontWeight: 600,
    color: '#1A1A1A',
    outline: 'none',
    background: '#fff',
    ...extra
  };
}

Object.assign(window, { SyntheticCallsPage });
