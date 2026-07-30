import { useState } from 'react'
import PageHeader from '../components/PageHeader'
import JournalRecordCard from '../components/JournalRecordCard'
import { useJournal } from '../journal/journalContext'
import { filterRecords } from '../journal/journalView'

const initial = { symbol: '', source: '', environment: '', status: '', runId: '', from: '', to: '', operator: '' }
export default function JournalRecordsPage() {
  const { records } = useJournal(); const [filters, setFilters] = useState(initial)
  const filtered = filterRecords(records, filters)
  const update = (event) => setFilters((value) => ({ ...value, [event.target.name]: event.target.value }))
  return <div className="page journal-page"><PageHeader eyebrow="Search" title="Trades and events" lead="Filter normalized local journal records without exposing raw evidence." />
    <section><form className="journal-filters" aria-label="Journal filters">
      <label>Symbol<input name="symbol" value={filters.symbol} onChange={update} maxLength="12" /></label>
      <label>Source<select name="source" value={filters.source} onChange={update}><option value="">All</option><option>AGENT</option><option>MANUAL</option><option>MANUAL_RECOVERY</option></select></label>
      <label>Environment<select name="environment" value={filters.environment} onChange={update}><option value="">All</option><option>LIVE</option><option>FAKE</option><option>MANUAL</option></select></label>
      <label>Status<input name="status" value={filters.status} onChange={update} maxLength="64" /></label>
      <label>Run ID<input name="runId" value={filters.runId} onChange={update} maxLength="128" /></label>
      <label>From<input name="from" type="date" value={filters.from} onChange={update} /></label>
      <label>To<input name="to" type="date" value={filters.to} onChange={update} /></label>
      <label>Operator intervention<select name="operator" value={filters.operator} onChange={update}><option value="">All</option><option value="yes">Required</option><option value="no">Not required</option></select></label>
      <button type="button" className="secondary-button" onClick={() => setFilters(initial)}>Clear</button>
    </form></section>
    <section aria-live="polite"><p>{filtered.length} record{filtered.length === 1 ? '' : 's'} shown</p>{filtered.length ? <div className="journal-card-grid">{filtered.map((record) => <JournalRecordCard key={record.journalRecordId} record={record} />)}</div> : <div className="journal-empty"><h2>No matching records</h2><p>Adjust filters or add a local journal entry.</p></div>}</section>
  </div>
}
