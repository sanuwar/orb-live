import PageHeader from '../components/PageHeader'
import JournalOverviewPage from './JournalOverviewPage'
import { useJournal } from '../journal/journalContext'

export default function PerformancePage() {
  const { automatic } = useJournal()
  const available = automatic.status === 'AUTOMATIC_EVIDENCE_AVAILABLE'
  return <div className="page performance-page">
    <PageHeader eyebrow="Performance" title="Sanitized journal, events, and evidence." lead="Automatic runtime records are read from the local sanitized projection; manual records and explicit evidence import remain available." />
    <section className="operations-unavailable"><span className="section-label">Automatic runtime records</span><h2>{available ? 'AUTOMATIC EVIDENCE AVAILABLE' : 'NO AUTOMATIC EVIDENCE'}</h2><p>{available ? `${automatic.records.length} projected records are shown alongside manual records.` : 'Start the local control service and complete a watcher session, or use manual import for debug evidence.'}</p></section>
    <nav className="section-nav" aria-label="Performance sections"><a href="#/journal">Journal overview</a><a href="#/journal-records">Trades & events</a><a href="#/manual-record">Add manual record</a><a href="#/evidence-import">Import sanitized evidence</a></nav>
    <JournalOverviewPage />
  </div>
}