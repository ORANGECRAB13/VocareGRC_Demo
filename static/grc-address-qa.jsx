// Address QA Lab - Georges River Council

function AddressQAPage() {
  const [corpus, setCorpus] = React.useState({ streets: [], suburbs: [], street_count: 0, suburb_count: 0 });
  const [variants, setVariants] = React.useState({ names: {}, name_count: 0, variant_count: 0, path: '' });
  const [type, setType] = React.useState('street');
  const [namesText, setNamesText] = React.useState('Warraba Street\nAllambee Crescent\nHurstville');
  const [runs, setRuns] = React.useState(5);
  const [variantTarget, setVariantTarget] = React.useState(20);
  const [maxNames, setMaxNames] = React.useState(3);
  const [testText, setTestText] = React.useState('fifty warboss street hurstvile');
  const [generateResult, setGenerateResult] = React.useState(null);
  const [finalizeResult, setFinalizeResult] = React.useState(null);
  const [search, setSearch] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [isNarrow, setIsNarrow] = React.useState(() => window.innerWidth <= 980);

  React.useEffect(() => {
    loadCorpus();
    loadVariants();
  }, []);

  React.useEffect(() => {
    const onResize = () => setIsNarrow(window.innerWidth <= 980);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  async function api(path, init) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  async function loadCorpus() {
    try {
      setCorpus(await api('/api/address-qa/corpus?limit=600'));
    } catch (err) {
      setError(err.message || 'Could not load address corpus');
    }
  }

  async function loadVariants() {
    try {
      setVariants(await api('/api/address-qa/variants'));
    } catch (err) {
      setError(err.message || 'Could not load variants');
    }
  }

  function parsedNames() {
    return namesText
      .split('\n')
      .map(item => item.trim())
      .filter(Boolean);
  }

  async function generateVariants() {
    setLoading(true);
    setError('');
    setGenerateResult(null);
    try {
      const data = await api('/api/address-qa/generate-variants', {
        method: 'POST',
        body: JSON.stringify({
          type,
          names: parsedNames(),
          runs: Number(runs),
          variant_target: Number(variantTarget),
          max_names: Number(maxNames)
        })
      });
      setGenerateResult(data);
      await loadVariants();
    } catch (err) {
      setError(err.message || 'Could not generate variants');
    } finally {
      setLoading(false);
    }
  }

  async function finalizeAddress() {
    setLoading(true);
    setError('');
    setFinalizeResult(null);
    try {
      const data = await api('/api/address-qa/finalize', {
        method: 'POST',
        body: JSON.stringify({ text: testText })
      });
      setFinalizeResult(data);
    } catch (err) {
      setError(err.message || 'Could not finalize address');
    } finally {
      setLoading(false);
    }
  }

  const variantRows = Object.entries(variants.names || {})
    .map(([name, entry]) => ({ name, ...entry }))
    .filter(row => {
      const q = search.trim().toLowerCase();
      if (!q) return true;
      const haystack = [
        row.name,
        row.type,
        ...(row.observed_tts_stt || []),
        ...(row.variants || []).map(item => item.value || item)
      ].join(' ').toLowerCase();
      return haystack.includes(q);
    })
    .sort((a, b) => a.name.localeCompare(b.name));

  const inputStyle = {
    width: '100%',
    border: '1px solid #E1E4E8',
    borderRadius: 8,
    padding: '10px 12px',
    fontSize: 13,
    color: '#1A1A1A',
    background: '#FFFFFF',
    outline: 'none'
  };
  const labelStyle = { fontSize: 10, fontWeight: 900, color: '#767676', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 };
  const sectionStyle = { background: '#FFFFFF', border: '1px solid #E8E9EB', borderRadius: 8, boxShadow: 'var(--shadow-card)', padding: 18 };

  return (
    <div style={{ padding: '28px 36px', display: 'flex', flexDirection: 'column', gap: 20, minHeight: 'calc(100vh - 64px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.1em', color: '#aaa', textTransform: 'uppercase', marginBottom: 4 }}>
            QA / <span style={{ color: '#C8232C' }}>Address Understanding</span>
          </div>
          <div style={{ fontFamily: 'Manrope', fontWeight: 900, fontSize: 26, color: '#1A1A1A' }}>Address QA Lab</div>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <AddressQAMetricPill icon="signpost" label={`${corpus.street_count || 0} Streets`} />
          <AddressQAMetricPill icon="location_city" label={`${corpus.suburb_count || 0} Suburbs`} />
          <AddressQAMetricPill icon="rule" label={`${variants.variant_count || 0} Variants`} />
        </div>
      </div>

      {error && (
        <div style={{ background: '#F9E6E7', color: '#C8232C', border: '1px solid #F2CDD0', borderRadius: 8, padding: '10px 12px', fontSize: 12, fontWeight: 700 }}>
          {error}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? '1fr' : 'minmax(320px, 0.9fr) minmax(360px, 1.1fr)', gap: 18, alignItems: 'start' }}>
        <section style={sectionStyle}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
            <div>
              <div style={{ fontFamily: 'Manrope', fontSize: 18, fontWeight: 900 }}>Variant Generator</div>
              <div style={{ fontSize: 12, color: '#767676', marginTop: 3 }}>Maya TTS, ElevenLabs STT, then LLM expansion.</div>
            </div>
            <button onClick={generateVariants} disabled={loading} style={addressQAPrimaryButton(loading)}>
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>auto_fix_high</span>
              Generate
            </button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? 'repeat(2, minmax(0, 1fr))' : '1fr 1fr 1fr 1fr', gap: 10, marginBottom: 12 }}>
            <AddressQAField label="Type">
              <select value={type} onChange={e => setType(e.target.value)} style={inputStyle}>
                <option value="street">Street</option>
                <option value="suburb">Suburb</option>
              </select>
            </AddressQAField>
            <AddressQAField label="Runs">
              <input type="number" min="1" max="10" value={runs} onChange={e => setRuns(e.target.value)} style={inputStyle} />
            </AddressQAField>
            <AddressQAField label="Variants">
              <input type="number" min="5" max="30" value={variantTarget} onChange={e => setVariantTarget(e.target.value)} style={inputStyle} />
            </AddressQAField>
            <AddressQAField label="Max Names">
              <input type="number" min="1" max="20" value={maxNames} onChange={e => setMaxNames(e.target.value)} style={inputStyle} />
            </AddressQAField>
          </div>

          <div style={labelStyle}>Names</div>
          <textarea
            value={namesText}
            onChange={e => setNamesText(e.target.value)}
            rows={7}
            style={{ ...inputStyle, resize: 'vertical', lineHeight: 1.5 }}
          />

          {generateResult && (
            <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(generateResult.generated || []).map(item => (
                <AddressQAResultBlock key={item.name} title={item.name} subtitle={`${item.variants?.length || 0} variants`}>
                  <AddressQAMiniList title="Observed STT" items={item.observed || []} empty="No alternate STT observations." />
                  <AddressQAMiniList title="Variants" items={item.variants || []} empty="No variants generated." />
                  {!!item.errors?.length && <AddressQAMiniList title="Errors" items={item.errors} empty="" tone="error" />}
                </AddressQAResultBlock>
              ))}
            </div>
          )}
        </section>

        <section style={sectionStyle}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
            <div>
              <div style={{ fontFamily: 'Manrope', fontSize: 18, fontWeight: 900 }}>Finalize Tester</div>
              <div style={{ fontSize: 12, color: '#767676', marginTop: 3 }}>Paste a noisy transcript and inspect the selected GRC address.</div>
            </div>
            <button onClick={finalizeAddress} disabled={loading} style={addressQAPrimaryButton(loading)}>
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>travel_explore</span>
              Test
            </button>
          </div>

          <div style={labelStyle}>Caller Transcript</div>
          <textarea
            value={testText}
            onChange={e => setTestText(e.target.value)}
            rows={4}
            style={{ ...inputStyle, resize: 'vertical', lineHeight: 1.5 }}
          />

          {finalizeResult && (
            <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
                <AddressQAInfoTile label="Variant Rewrite" value={finalizeResult.variant_rewrite || '-'} />
                <AddressQAInfoTile label="Corrected Query" value={finalizeResult.corrected || '-'} />
                <AddressQAInfoTile label="Selected Address" value={finalizeResult.selected_address || '-'} strong />
                <AddressQAInfoTile label="Lookup" value={finalizeResult.lookup?.success ? 'Successful' : 'No result'} strong={finalizeResult.lookup?.success} />
              </div>

              {!!finalizeResult.variant_matches?.length && (
                <AddressQAResultBlock title="Variant Matches" subtitle={`${finalizeResult.variant_matches.length} applied`}>
                  {(finalizeResult.variant_matches || []).map(match => (
                    <div key={`${match.variant}-${match.canonical}`} style={{ fontSize: 12, color: '#5A5F6B', marginBottom: 6 }}>
                      <strong style={{ color: '#1A1A1A' }}>{match.variant}</strong> -&gt; {match.canonical}
                    </div>
                  ))}
                </AddressQAResultBlock>
              )}

              <AddressQAResultBlock title="Candidate Ranking" subtitle={`${finalizeResult.candidates?.length || 0} candidates`}>
                {(finalizeResult.candidates || []).slice(0, 8).map(candidate => (
                  <div key={candidate.address} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '7px 0', borderBottom: '1px solid #F0F1F3' }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: '#1A1A1A' }}>{candidate.address}</span>
                    <span style={{ fontSize: 11, fontWeight: 800, color: '#00A9A5' }}>{candidate.qa_score ?? candidate.score ?? '-'}</span>
                  </div>
                ))}
              </AddressQAResultBlock>

              {finalizeResult.voice_response && (
                <AddressQAResultBlock title="Voice Response" subtitle="Bin lookup output">
                  <div style={{ fontSize: 13, color: '#1A1A1A', lineHeight: 1.5 }}>{finalizeResult.voice_response}</div>
                </AddressQAResultBlock>
              )}
            </div>
          )}
        </section>
      </div>

      <section style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontFamily: 'Manrope', fontSize: 18, fontWeight: 900 }}>Variant Dictionary</div>
            <div style={{ fontSize: 11, color: '#767676', marginTop: 3 }}>{variants.path || 'No dictionary file yet'}</div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search variants" style={{ ...inputStyle, width: 220 }} />
            <button onClick={loadVariants} style={addressQASecondaryButton()}>
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>refresh</span>
            </button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
          {variantRows.slice(0, 24).map(row => (
            <div key={row.name} style={{ border: '1px solid #EEF0F2', borderRadius: 8, padding: 12, background: '#FBFBFC' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
                <div style={{ fontWeight: 900, fontSize: 13, color: '#1A1A1A' }}>{row.name}</div>
                <span style={{ fontSize: 10, fontWeight: 900, color: '#767676', textTransform: 'uppercase' }}>{row.type || 'name'}</span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {(row.variants || []).slice(0, 20).map(item => (
                  <span key={item.value || item} style={{ background: '#FFFFFF', border: '1px solid #E8E9EB', borderRadius: 6, padding: '4px 7px', fontSize: 11, color: '#5A5F6B' }}>
                    {item.value || item}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function AddressQAMetricPill({ icon, label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, background: '#FFFFFF', border: '1px solid #E8E9EB', borderRadius: 8, padding: '8px 10px' }}>
      <span className="material-symbols-outlined" style={{ fontSize: 17, color: '#C8232C' }}>{icon}</span>
      <span style={{ fontSize: 12, fontWeight: 900, color: '#1A1A1A' }}>{label}</span>
    </div>
  );
}

function AddressQAField({ label, children }) {
  return (
    <label style={{ minWidth: 0 }}>
      <div style={{ fontSize: 10, fontWeight: 900, color: '#767676', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>{label}</div>
      {children}
    </label>
  );
}

function AddressQAResultBlock({ title, subtitle, children }) {
  return (
    <div style={{ border: '1px solid #EEF0F2', borderRadius: 8, padding: 12, background: '#FBFBFC' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 900, color: '#1A1A1A' }}>{title}</div>
        {subtitle && <div style={{ fontSize: 11, fontWeight: 800, color: '#767676' }}>{subtitle}</div>}
      </div>
      {children}
    </div>
  );
}

function AddressQAMiniList({ title, items, empty, tone }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 10, fontWeight: 900, color: tone === 'error' ? '#C8232C' : '#767676', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 5 }}>{title}</div>
      {items.length ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {items.map(item => (
            <span key={item} style={{ background: '#FFFFFF', border: '1px solid #E8E9EB', borderRadius: 6, padding: '4px 7px', fontSize: 11, color: tone === 'error' ? '#C8232C' : '#5A5F6B' }}>
              {item}
            </span>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: '#9CA3AF' }}>{empty}</div>
      )}
    </div>
  );
}

function AddressQAInfoTile({ label, value, strong }) {
  return (
    <div style={{ border: '1px solid #EEF0F2', borderRadius: 8, padding: 11, background: '#FBFBFC', minWidth: 0 }}>
      <div style={{ fontSize: 10, fontWeight: 900, color: '#767676', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 5 }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: strong ? 900 : 700, color: strong ? '#C8232C' : '#1A1A1A', overflowWrap: 'anywhere' }}>{value}</div>
    </div>
  );
}

function addressQAPrimaryButton(disabled) {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    background: disabled ? '#D7D9DD' : '#C8232C',
    color: '#FFFFFF',
    borderRadius: 8,
    padding: '10px 13px',
    fontSize: 12,
    fontWeight: 900,
    cursor: disabled ? 'not-allowed' : 'pointer'
  };
}

function addressQASecondaryButton() {
  return {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 40,
    height: 40,
    background: '#FFFFFF',
    color: '#C8232C',
    border: '1px solid #E8E9EB',
    borderRadius: 8,
    fontWeight: 900
  };
}
