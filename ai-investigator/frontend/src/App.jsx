import { useEffect, useState } from 'react'
import './App.css'

const API = 'http://localhost:8090'

// No hardcoded tickets: the queue loads live from GET /api/incidents.
// (Old curated samples removed — they referenced deleted rows and misled.)

function Pill({ tone, children }) {
  return <span className={`pill ${tone}`}>{children}</span>
}

function toneFor(status) {
  const s = String(status || '').toUpperCase()
  if (['ACTIVE', 'COMPLETED', 'SENT', 'SUCCESS', 'VALIDATED', 'CONFIRMED'].includes(s)) return 'green'
  if (s.includes('FAIL') || ['BLOCKED', 'SUSPENDED'].includes(s)) return 'red'
  if (['PENDING', 'VALIDATING', 'IN_PROGRESS', 'RETRYING', 'RESERVED'].includes(s)) return 'amber'
  return 'navy'
}

export default function App() {
  const [text, setText] = useState('')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [liveTickets, setLiveTickets] = useState(null)
  const [rca, setRca] = useState(null)
  const [fixes, setFixes] = useState(null)
  const [theme, setTheme] = useState(() => localStorage.getItem('ai-theme') || 'light')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('ai-theme', theme)
  }, [theme])

  useEffect(() => {
    fetch(`${API}/api/incidents`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && d.tickets && d.tickets.length) setLiveTickets(d.tickets)
      })
      .catch(() => {})
  }, [])

  const queue = liveTickets || []

  function toggleTheme() {
    setTheme((t) => (t === 'light' ? 'dark' : 'light'))
  }

  function downloadRca() {
    if (!rca) return
    const blob = new Blob([rca.markdown], { type: 'text/markdown' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `RCA-${rca.rca.incident}.md`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  async function investigate() {
    const query = text.trim()
    if (!query || loading) return
    setLoading(true)
    setError('')
    setResult(null)
    setRca(null)
    setFixes(null)
    const body = JSON.stringify({ incident_text: query, mode: 'llm' })
    const post = (path) => fetch(`${API}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
    }).then((r) => {
      if (!r.ok) throw new Error(`backend ${r.status}`)
      return r.json()
    })
    try {
      // One click runs everything: investigation + RCA + fix proposals.
      const [inv, rcaRes, dataRes, codeRes] = await Promise.all([
        post('/api/investigate'),
        post('/api/rca'),
        post('/api/datafix'),
        post('/api/codefix'),
      ])
      setResult(inv)
      setRca({ rca: rcaRes.rca, markdown: rcaRes.markdown })
      setFixes({ data: dataRes, code: codeRes })
    } catch (e) {
      setError(`Investigator backend unreachable — is it running on :8090? (${e.message})`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="page">
      <header className="hero">
        <button className="theme-toggle" onClick={toggleTheme} title="Toggle dark / light mode">
          {theme === 'light' ? '🌙 Dark' : '☀️ Light'}
        </button>
        <p className="eyebrow">AI Production Incident Investigator · Phase 14</p>
        <h1>Paste the ticket. Get the trace.</h1>
        <p>Pastes a Jira-style incident, extracts every ID, searches the five service logs, correlates the request, checks the databases (read-only) and names the suspect service.</p>
      </header>

      <section className="card">
        <h2>Incident</h2>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder="INC-10101 Order ORD-… stuck…" />
        <div className="btn-row mode-row">
          <button className="btn" onClick={investigate} disabled={loading || !text.trim()}>
            {loading ? 'Investigating…' : 'Investigate'}
          </button>
          <span className="muted">AI reasoning over live evidence — no canned answers.</span>
        </div>
        <div className="btn-row">
          <span className="muted">Pick a ticket below — its text loads above, then Investigate.</span>
        </div>
        {error && <div className="alert error">{error}</div>}
      </section>

      <section className="card">
        <h2>Incident queue{liveTickets ? ` — live from the databases (${liveTickets.length})` : ' — waiting for backend…'}</h2>
        {!liveTickets && <p className="explainer">Start the investigator backend (:8090) — the queue builds itself from current failures.</p>}
        {liveTickets && <p className="explainer">Live tickets built fresh from current FAILED/stuck rows — resolved ones disappear, new ones appear. Click to load.</p>}
        <div className="jira-grid">
          {queue.map((t) => (
            <button key={t.id} className="jira" onClick={() => { setText(t.text); setResult(null); window.scrollTo({ top: 0, behavior: 'smooth' }) }} title={t.text}>
              <div className="jira-top"><strong>{t.id}</strong><span className={`pill ${t.priority === 'P2' ? 'red' : 'amber'}`}>{t.priority}</span></div>
              <div className="jira-title">{t.title}</div>
              <div className="jira-meta">🧑 {t.reporter}{t.created ? ` · 🕒 ${t.created}` : ''}</div>
              <div className="jira-meta">📦 {t.order} · ⚠️ {t.error}</div>
              <div className="jira-meta"><span className="pill navy">{t.status}</span></div>
            </button>
          ))}
        </div>
      </section>

      {loading && (
        <section className="card" aria-busy="true">
          <div className="shimmer">Reading logs, checking records, connecting the dots…</div>
        </section>
      )}

      {result && result.needs_info && (
        <section className="card">
          <h2>Need more details 🔍</h2>
          <p>{result.message}</p>
          {Object.entries(result.candidates || {}).map(([k, rows]) => (
            <div key={k}>
              <h3>{k}</h3>
              <div className="btn-row">
                {rows.map((r, i) => {
                  const id = r.order_number || r.request_number || r.notification_number || r.reservation_number;
                  return (
                    <button key={i} className="btn small ghost"
                      onClick={() => setText(`Investigate ${id} (status ${r.status || r.resource_status || ''})`)}>
                      {id} · {r.status || r.resource_status}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </section>
      )}

      {result && !result.needs_info && (
        <>
          <section className="card verdict">
            <h2>Verdict — decided by the AI from live evidence</h2>
            <p className="explainer">Data = fix the stored information. Code = fix the program. Nothing here is pre-written.</p>
            {!result.verdict && <div className="alert error">{result.message || 'Add the LLM key to get a verdict.'}</div>}
            {result.verdict && (
            <div className="verdict-row">
              <div>
                <div className="muted">Fix type</div>
                <div className="fix-badge">{result.verdict?.fix_type || '…'}</div>
              </div>
              <div>
                <div className="muted">When it happened</div>
                <div className="fix-badge dim" style={{fontSize:'0.8rem'}}>{rca ? `${rca.rca.window.first || '?'} → ${rca.rca.window.last || '?'}` : '…'}</div>
              </div>
              <div>
                <div className="muted">Root cause</div>
                <div className="fix-badge dim" style={{fontSize:'0.85rem'}}>{result.verdict.root_cause}</div>
              </div>
              <div>
                <div className="muted">Confidence</div>
                <div className="fix-badge dim">{result.verdict.confidence}</div>
              </div>
            </div>
            )}
          </section>

          <section className="card">
            <div className="section-head"><h2>AI conclusion {result.has_error === false && <span className="pill amber">no error described — state-based only</span>}</h2></div>
            <p className="explainer">In the model's own words, grounded in the journey and records below.</p>
            {result.verdict
              ? <p><strong>{result.verdict.summary}</strong></p>
              : <p className="muted">No verdict yet — the model needs its key (see message above).</p>}
            <p className="muted">
              Services holding failed rows: {(result.owner?.services || []).join(', ') || 'none'}
              {' '}· {result.log_stats.lines} log lines, {result.log_stats.direct_hits} direct hits
              {' '}· Correlations: {result.correlations.join(', ') || 'none'}
            </p>
            <p className="muted">{result.safety}</p>
            <h2>Where the logs were read from</h2>
            <p className="muted">Mode: <code>{result.log_sources?.mode}</code> — pull the same lines yourself:</p>
            <ul className="logsrc">
              {(result.log_sources?.files || []).map((f) => <li key={f}><code>{f}</code></li>)}
            </ul>
          </section>

          <section className="card">
            <h2>Model details {result.llm?.enabled ? <span className="pill green">{result.llm.model}</span> : <span className="pill">no model connected</span>}</h2>
            {!result.llm?.enabled && <p className="muted">{result.llm?.reason} Add LLM_API_KEY to ai-investigator/backend/.env to enable.</p>}
            {result.llm?.enabled && result.llm.analysis && (
              <>
                <p><strong>{result.llm.analysis.summary}</strong></p>
                <p>Root cause: <code>{result.llm.analysis.root_cause}</code> · Confidence: {result.llm.analysis.confidence} · Fix type: {result.llm.analysis.fix_type}</p>
                <h3>Evidence chain</h3>
                <ul>{(result.llm.analysis.evidence_refs || []).map((e, i) => <li key={i}>{e}</li>)}</ul>
                {result.llm.analysis.data_fix_sql && (
                  <>
                    <h3>Proposed data fix (needs human approval)</h3>
                    <pre>{result.llm.analysis.data_fix_sql}</pre>
                    <p className="muted">Validate: <code>{result.llm.analysis.data_fix_validation}</code><br />Rollback: {result.llm.analysis.data_fix_rollback}</p>
                  </>
                )}
                {result.llm.analysis.code_fix && (
                  <>
                    <h3>Proposed code fix — {result.llm.analysis.code_fix.service} / {result.llm.analysis.code_fix.area}</h3>
                    <p className="muted">Before:</p>
                    <pre>{result.llm.analysis.code_fix.before}</pre>
                    <p className="muted">After:</p>
                    <pre>{result.llm.analysis.code_fix.after}</pre>
                  </>
                )}
                <h3>Next steps</h3>
                <ul>{(result.llm.analysis.next_steps || []).map((s, i) => <li key={i}>{s}</li>)}</ul>
              </>
            )}
          </section>

          <div className="grid two">
            <details className="card" open>
              <summary className="sum">IDs + stored records (technical — open to inspect)</summary>
              <h2>Extracted entities</h2>
            <p className="explainer">Names and numbers pulled out of your ticket — everything below is traced from these.</p>
              {Object.entries(result.entities).map(([k, v]) => (
                <p key={k}><code>{k}</code>: {v.join(', ')}</p>
              ))}
              <h2>Database evidence (SELECT only)</h2>
              <p className="explainer">Current stored state for those IDs. Read fresh now — nothing was changed.</p>
              {Object.entries(result.db_evidence).map(([k, rows]) => (
                <div key={k}>
                  <h3>{k}</h3>
                  <pre>{JSON.stringify(rows, null, 1).slice(0, 1200)}</pre>
                </div>
              ))}
            </details>
            <section className="card">
              <h2>Service journey</h2>
              <p className="explainer">Every step your request took, in time order, across all five services.</p>
              <ul className="timeline">
                {result.journey.map((j, i) => (
                  <li key={i}><strong>{j.service}</strong> · {j.event} <Pill tone={toneFor(j.status)}>{j.status}</Pill><br /><span className="t">{j.time}</span></li>
                ))}
              </ul>
            </section>
          </div>

          <details className="card">
            <summary className="sum">Relevant log lines ({result.log_lines.length}) — the exact proof</summary>
            <pre className="logs">{result.log_lines.map((l) => `[${l.service}] ${l.line}`).join('\n')}</pre>
          </details>

          <section className="card">
            <h2>Root Cause Analysis</h2>
            <p className="explainer">Written automatically with the investigation — impact, cause, timeline, recommendation.</p>
            <div className="btn-row">
              {rca && <button className="btn small ghost" onClick={downloadRca}>Download .md</button>}
            </div>
            {rca && (
              <>
                <h3>{rca.rca.incident} — {rca.rca.confidence} confidence</h3>
                <p><strong>Impact:</strong> {rca.rca.impact}</p>
                <p><strong>Affected:</strong> order {rca.rca.affected.order} ({rca.rca.affected.order_status}),
                  customer {JSON.stringify(rca.rca.affected.customer)}, service {rca.rca.affected.service}</p>
                <p><strong>Root cause:</strong> <code>{rca.rca.root_cause}</code></p>
                <h3>Contributing factors</h3>
                <ul>{rca.rca.contributing_factors.map((c, i) => <li key={i}>{c}</li>)}</ul>
                <h3>Timeline ({rca.rca.timeline.length} events, {rca.rca.window.first} → {rca.rca.window.last})</h3>
                <ul className="timeline">
                  {rca.rca.timeline.map((t, i) => (
                    <li key={i}><strong>{t.service}</strong> · {t.event} ({t.status})<br /><span className="t">{t.time}</span></li>
                  ))}
                </ul>
                <h3>Recommendation</h3>
                <p>{rca.rca.recommendation}</p>
              </>
            )}
          </section>

          {fixes && (
            <>
              <section className="card">
                <h2>Temporary data fix <span className="pill amber">proposal only — human approval required</span></h2>
                <p className="explainer">Unblocks the customer <em>right now</em>. Copy, check, approve — the tool itself can never run these.</p>
                {fixes.data.fixes.map((f, i) => (
                  <div key={i} className="fix">
                    <h3>{f.kind.toUpperCase()} · {f.title}</h3>
                    <p>{f.why}</p>
                    {f.sql.map((s, j) => <pre key={j}>{s}</pre>)}
                    {f.operation && <p>Operation: <code>{f.operation}</code></p>}
                    <p className="muted">Validate: {f.validation}<br />Rollback: {f.rollback}</p>
                  </div>
                ))}
                {fixes.data.llm_sql && (<><h3>LLM-drafted SQL</h3><pre>{fixes.data.llm_sql}</pre></>)}
              </section>
              <section className="card">
                <h2>Permanent code fix <span className="pill navy">proposal only — review + tests + deploy</span></h2>
                <p className="explainer">Stops it happening again. A developer reviews, tests and ships this — never the AI.</p>
                {fixes.code.fixes.map((f, i) => (
                  <div key={i} className="fix">
                    <h3>{f.title}</h3>
                    <p className="muted">{f.service} · {f.area}</p>
                    <p>{f.problem}</p>
                    <h3>Before</h3><pre>{f.before}</pre>
                    <h3>After</h3><pre>{f.after}</pre>
                    <p><strong>Tests:</strong></p>
                    <ul>{f.tests.map((t, j) => <li key={j}>{t}</li>)}</ul>
                    <p className="muted"><strong>Risks:</strong> {f.risks}<br /><strong>Deploy:</strong> {f.deploy_notes}</p>
                  </div>
                ))}
                {fixes.code.llm_code_fix && (<><h3>LLM-drafted fix</h3><pre>{JSON.stringify(fixes.code.llm_code_fix, null, 1)}</pre></>)}
              </section>
            </>
          )}
        </>
      )}
    </main>
  )
}
