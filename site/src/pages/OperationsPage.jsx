import { useState } from 'react'
import MorningControl from '../components/MorningControl'
import AutomaticEventFeed from '../components/AutomaticEventFeed'
import CandidateAttentionPanel from '../components/CandidateAttentionPanel'
import MorningFunnelPanel from '../components/MorningFunnelPanel'
import PageHeader from '../components/PageHeader'
import { parseOperatorSnapshot } from '../operations/operatorSnapshot'
import { formatEasternTime } from '../formatters/easternTime'

const unavailable = 'NO EVIDENCE'
function Fact({ label, value }) { return <div><dt>{label}</dt><dd>{value}</dd></div> }
function SnapshotPanel({ snapshot }) {
  const components = Object.entries(snapshot.source_component_statuses)
  return <>
    <section aria-labelledby="today-title"><div className="section-heading"><span className="section-label">Today / session</span><h2 id="today-title">{snapshot.operational_status}</h2><p>Generated {formatEasternTime(snapshot.generated_as_of)}. Session-specific watcher, entry-window, candidate, and evaluation facts are not present in this projection.</p></div><dl className="operations-facts"><Fact label="Operating mode" value={snapshot.operating_mode}/><Fact label="Scheduler" value={snapshot.scheduler_status}/><Fact label="Runner" value={snapshot.runner_status}/><Fact label="Evidence" value={`${snapshot.evidence_completeness} / ${snapshot.evidence_freshness}`}/></dl></section>
    <section aria-labelledby="readiness-title"><div className="section-heading"><span className="section-label">Readiness</span><h2 id="readiness-title">{snapshot.supervised_readiness_status}</h2></div><dl className="operations-facts"><Fact label="Release SHA" value={snapshot.release_commit_sha}/><Fact label="Implementation digest" value={snapshot.implementation_digest}/><Fact label="Authority" value="ATTENDED_COMMISSIONING_ONLY"/><Fact label="Automated/write authority" value="DISABLED"/></dl></section>
    <section className="operations-unavailable" aria-labelledby="scanner-title"><div className="section-heading"><span className="section-label">Scanner / filters</span><h2 id="scanner-title">{unavailable}</h2><p>This sanitized operator snapshot has no per-symbol, filter, evaluation, or candidate fields. The UI will not infer them.</p></div></section>
    <section className="operations-unavailable" aria-labelledby="candidate-title"><div className="section-heading"><span className="section-label">Candidate / lifecycle</span><h2 id="candidate-title">{unavailable}</h2><p>No candidate, quantity, price, fill, or lifecycle data exists in this projection. No trade status is inferred.</p></div></section>
    <section aria-labelledby="protection-title"><div className="section-heading"><span className="section-label">Protection</span><h2 id="protection-title">NOT GROUNDED</h2><p>Approved protection state count: {snapshot.approved_protection_state_count}. This snapshot establishes no grounded protection.</p></div><dl className="operations-facts"><Fact label="Suspension" value={snapshot.suspension_status}/><Fact label="Recovery" value={snapshot.recovery_status}/><Fact label="Heartbeat" value={snapshot.heartbeat_status}/><Fact label="Alerts" value={snapshot.alert_status}/></dl></section>
    <section aria-labelledby="events-title"><div className="section-heading"><span className="section-label">Events / components</span><h2 id="events-title">Projected component status</h2><p>This standalone snapshot contains component statuses, not a timestamped event or incident feed.</p></div><dl className="operations-facts">{components.map(([label, value]) => <Fact key={label} label={label} value={value}/>)}</dl></section>
    <section aria-labelledby="action-title"><div className="section-heading"><span className="section-label">Artifacts / next action</span><h2 id="action-title">{snapshot.recommended_operator_action}</h2><p>Blockers: {snapshot.blocker_reasons.length ? snapshot.blocker_reasons.join(', ') : 'None recorded'}.</p></div></section>
  </>
}
export default function OperationsPage() {
  const [result, setResult] = useState({ status: 'empty' })
  async function load(event) {
    const file = event.target.files?.[0]
    if (!file) return
    const text = typeof file.text === 'function' ? await file.text() : await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsText(file) })
    setResult(parseOperatorSnapshot(text))
  }
  return <div className="page operations-page"><PageHeader eyebrow="Operations" title="Read-only runtime dashboard." lead="Use the fixed local morning controls and load sanitized operator-snapshot.json evidence. The browser never contacts market-data or broker services." />
    <MorningControl />
    <CandidateAttentionPanel />
    <MorningFunnelPanel />
    <AutomaticEventFeed />
    <section className="operations-loader" aria-labelledby="load-title"><div><span className="section-label">Sanitized projection</span><h2 id="load-title">Load operator snapshot</h2><p>Generated locally by `python scripts/export_operator_dashboard.py --include-json ...`. The file remains in this browser only.</p></div><label className="secondary-button">Choose operator-snapshot.json<input type="file" accept="application/json,.json" onChange={load}/></label></section>
    {result.status === 'empty' && <section className="operations-unavailable"><h2>NO EVIDENCE</h2><p>No sanitized operator snapshot has been loaded. This does not establish readiness, safety, grounded protection, no trade, or session state.</p></section>}
    {result.status === 'rejected' && <section className="operations-unavailable"><h2>BLOCKED</h2><p>Snapshot rejected: {result.code}. No data is displayed.</p></section>}
    {result.status === 'loaded' && <SnapshotPanel snapshot={result.snapshot}/>}</div>
}
