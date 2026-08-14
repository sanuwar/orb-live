import AutomaticEventFeed from '../components/AutomaticEventFeed'
import CandidateAttentionPanel from '../components/CandidateAttentionPanel'
import { formatEasternTime } from '../formatters/easternTime'

function Fact({ label, value }) { return <div><dt>{label}</dt><dd>{value}</dd></div> }

export default function RemoteOperationsPage({ projection, connectionStatus = 'CONNECTED' }) {
  const freshness = connectionStatus === 'CONNECTION_UNAVAILABLE' ? 'CONNECTION UNAVAILABLE' : projection.freshness?.status ?? 'NO_EVIDENCE'
  const candidateFreshness = freshness === 'CONNECTION UNAVAILABLE' ? 'STALE' : freshness
  const latest = projection.sessions?.at(-1)
  const watcher = projection.watcher ?? { currentState: 'UNAVAILABLE', lastRun: null }
  const last = watcher.lastRun
  return <div className="page operations-page remote-operations"><header className="page-header"><span className="eyebrow">Operations</span><h1>REMOTE MONITORING - READ ONLY</h1><p>Sanitized projection only. This surface cannot run readiness, start or stop the watcher, authorize entry, or reach broker operations.</p></header>
    <section className={`remote-freshness freshness-${freshness.toLowerCase().replaceAll('_', '-').replaceAll(' ', '-')}`} aria-labelledby="freshness-title"><span className="section-label">Projection freshness</span><h2 id="freshness-title">{freshness === 'CURRENT' ? 'LIVE / CURRENT' : freshness}</h2><p>Last updated: {formatEasternTime(projection.freshness?.lastUpdatedAt)}</p></section>
    <section aria-labelledby="remote-session-title"><span className="section-label">Watcher / session</span><h2 id="remote-session-title">{watcher.currentState}</h2><dl className="operations-facts"><Fact label="Watcher observed" value={formatEasternTime(watcher.observedAt)}/><Fact label="Session result" value={latest?.terminalState ?? 'NO EVIDENCE'}/><Fact label="Session started" value={formatEasternTime(latest?.startedAt)}/><Fact label="Evaluations" value={latest?.evaluationCount ?? 'N/A'}/></dl></section>
    <section aria-labelledby="remote-last-run"><span className="section-label">Last run</span><h2 id="remote-last-run">{last?.result ?? 'NONE'}</h2><dl className="operations-facts"><Fact label="Terminal state" value={last?.terminalState ?? 'N/A'}/><Fact label="Started" value={formatEasternTime(last?.startedAt)}/><Fact label="Finished" value={formatEasternTime(last?.finishedAt)}/><Fact label="Stop reason" value={last?.stopReason ?? 'N/A'}/></dl></section>
    <CandidateAttentionPanel evidenceFreshness={candidateFreshness} />
    <AutomaticEventFeed />
  </div>
}
