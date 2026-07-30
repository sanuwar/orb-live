import PageHeader from '../components/PageHeader'
import JournalRecordCard from '../components/JournalRecordCard'
import { useJournal } from '../journal/journalContext'
import { journalSummary } from '../journal/journalView'

export default function JournalOverviewPage() {
  const { records } = useJournal()
  const summary = journalSummary(records)
  const recent = [...records].sort((a, b) => b.eventTimestamp.localeCompare(a.eventTimestamp)).slice(0, 5)
  return <div className="page journal-page">
    <PageHeader eyebrow="Local journal" title="Trade journal" lead="A sanitized, append-only record of agent evidence, manual trades, and operator recovery. The trade journal is not a broker execution interface." />
    <section aria-labelledby="journal-summary"><div className="section-heading"><span className="section-label">Overview</span><h2 id="journal-summary">What the local record establishes</h2></div>
      <div className="journal-metrics">
        <article><strong>{summary.total}</strong><span>Total records</span></article>
        <article><strong>{summary.agent}</strong><span>Agent records</span></article>
        <article><strong>{summary.manual}</strong><span>Manual records</span></article>
        <article><strong>{summary.operatorRequired}</strong><span>Operator-required incidents</span></article>
        <article><strong>{summary.verifiedFlatRuns}</strong><span>Verified-flat runs</span></article>
        <article><strong>{summary.realizedPnl ?? 'Not available'}</strong><span>Realized P&amp;L (complete evidence)</span></article>
      </div>
    </section>
    <section aria-labelledby="recent-activity"><div className="section-heading section-heading-row"><div><span className="section-label">Timeline</span><h2 id="recent-activity">Recent activity</h2></div><a href="#/journal-records">View all records</a></div>
      {recent.length ? <div className="journal-card-grid">{recent.map((record) => <JournalRecordCard key={record.journalRecordId} record={record} />)}</div> : <div className="journal-empty"><h3>No journal records yet</h3><p>Import sanitized fake evidence or add a manual journal record. Neither action can contact a broker.</p></div>}
    </section>
  </div>
}
