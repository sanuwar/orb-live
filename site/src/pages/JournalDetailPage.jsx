import PageHeader from '../components/PageHeader'
import JournalBadge from '../components/JournalBadge'
import { useJournal } from '../journal/journalContext'
import { chronological, displayValue } from '../journal/journalModel'

export default function JournalDetailPage({ detailKey }) {
  const { records } = useJournal()
  const decoded = decodeURIComponent(detailKey ?? '')
  const anchor = records.find((record) => record.journalRecordId === decoded || record.runId === decoded)
  const runId = anchor?.runId
  const timeline = chronological(records.filter((record) => runId ? record.runId === runId : record.journalRecordId === decoded || record.relatedRecordId === decoded))
  const verifiedFlat = timeline.some((record) => record.commissioningStage === 'VERIFIED_FLAT')
  const final = timeline.at(-1)
  return <div className="page journal-page"><PageHeader eyebrow="Run detail" title={runId ? `Run ${runId}` : 'Journal record'} lead="A normalized timeline. Missing attribution and financial values remain explicit rather than inferred." />
    {!timeline.length ? <section className="journal-empty"><h2>Record not found</h2><p>The local journal does not contain this record.</p></section> : <>
      <section className="journal-detail-summary"><JournalBadge tone={verifiedFlat ? 'success' : 'warning'}>{verifiedFlat ? 'VERIFIED_FLAT' : 'NOT VERIFIED FLAT'}</JournalBadge><h2>{final?.status}</h2><p>Final outcome: {displayValue(final?.reason ?? final?.commissioningStage)}</p><p>Evidence reference: {final?.evidenceSha256 ? `${final.evidenceSha256.slice(0, 12)}…` : 'Not available'}</p></section>
      <section><div className="section-heading"><span className="section-label">Chronology</span><h2>Complete event timeline</h2></div><ol className="journal-timeline">{timeline.map((record) => <li key={record.journalRecordId}><time dateTime={record.eventTimestamp}>{new Date(record.eventTimestamp).toLocaleString()}</time><div><JournalBadge>{record.source}</JournalBadge><h3>{record.commissioningStage}</h3><p>{displayValue(record.reason)}</p><small>Initiator: {record.initiator || 'UNPROVEN'} · Status: {record.status}</small></div></li>)}</ol></section>
    </>}
  </div>
}
