import PageHeader from '../components/PageHeader'
import JournalOverviewPage from './JournalOverviewPage'

export default function PerformancePage() {
  return <div className="page performance-page">
    <PageHeader eyebrow="Performance" title="Sanitized journal, events, and evidence." lead="The existing browser-local journal remains unchanged in M25.1. Automatic runtime projection is deliberately deferred to M25.4." />
    <nav className="section-nav" aria-label="Performance sections"><a href="#/journal">Journal overview</a><a href="#/journal-records">Trades & events</a><a href="#/manual-record">Add manual record</a><a href="#/evidence-import">Import sanitized evidence</a></nav>
    <JournalOverviewPage />
  </div>
}