import { sessionAnalytics } from '../analytics/sessionAnalytics'
import { formatEasternTime } from '../formatters/easternTime'

function shown(value, available) { return available ? value : 'N/A' }

export default function RemotePerformancePage({ automatic }) {
  const metrics = sessionAnalytics(automatic)
  const available = automatic.status === 'AUTOMATIC_EVIDENCE_AVAILABLE'
  const latest = automatic.sessions?.at(-1)
  return <div className="page performance-page"><header className="page-header"><span className="eyebrow">Performance</span><h1>Sanitized runtime history</h1><p>Historical analytics use only authenticated allowlisted projection evidence. Missing lifecycle evidence is never presented as performance.</p></header><section aria-labelledby="remote-metrics"><h2 id="remote-metrics">Observed sessions</h2><div className="journal-metrics"><article><strong>{shown(metrics.sessions, available)}</strong><span>Sessions</span></article><article><strong>{shown(metrics.candidate, available)}</strong><span>Candidate sessions</span></article><article><strong>{shown(metrics.noCandidate, available)}</strong><span>No-candidate sessions</span></article><article><strong>{metrics.candidateFrequency === null ? 'N/A' : `${(metrics.candidateFrequency * 100).toFixed(0)}%`}</strong><span>Candidate frequency</span></article><article><strong>{shown(metrics.evaluations, available)}</strong><span>Evaluations</span></article></div><p>Filter-fact coverage: {available ? metrics.filterFactCoverage : 'N/A'}. Latest completed session: {formatEasternTime(latest?.completedAt)}.</p></section><section className="operations-unavailable"><span className="section-label">Trade performance</span><h2>INSUFFICIENT LIVE TRADE EVIDENCE</h2><p>No P&amp;L, win rate, or R-multiple is inferred without a completed authentic lifecycle.</p></section></div>
}
