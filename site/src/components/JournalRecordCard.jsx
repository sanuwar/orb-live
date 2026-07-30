import JournalBadge from './JournalBadge'
import { displayValue } from '../journal/journalModel'

function tone(value) {
  if (value === 'COMPLETE' || value === 'VERIFIED_FLAT') return 'success'
  if (value === 'FAILED') return 'danger'
  if (value === 'OPERATOR_REQUIRED' || value === 'PARTIAL') return 'warning'
  return 'neutral'
}

export default function JournalRecordCard({ record }) {
  const detailKey = record.runId ?? record.journalRecordId
  return (
    <article className="journal-record-card">
      <header>
        <div><JournalBadge tone={record.source === 'AGENT' ? 'info' : 'neutral'}>{record.source}</JournalBadge> <JournalBadge tone={tone(record.commissioningStage)}>{record.commissioningStage}</JournalBadge></div>
        <time dateTime={record.eventTimestamp}>{new Date(record.eventTimestamp).toLocaleString()}</time>
      </header>
      <h3>{displayValue(record.symbol)} · {record.status}</h3>
      <dl className="journal-record-facts">
        <div><dt>Environment</dt><dd>{record.environment}</dd></div>
        <div><dt>Run</dt><dd>{displayValue(record.runId)}</dd></div>
        <div><dt>Side</dt><dd>{displayValue(record.side)}</dd></div>
        <div><dt>Quantity</dt><dd>{displayValue(record.quantity)}</dd></div>
        <div><dt>Price</dt><dd>{displayValue(record.executionPrice)}</dd></div>
        <div><dt>Initiator</dt><dd>{record.initiator}</dd></div>
      </dl>
      <a href={`#/journal-detail/${encodeURIComponent(detailKey)}`}>View timeline</a>
    </article>
  )
}
