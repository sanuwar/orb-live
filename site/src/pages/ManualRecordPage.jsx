import { useRef, useState } from 'react'
import PageHeader from '../components/PageHeader'
import { useJournal } from '../journal/journalContext'
import { JournalValidationError } from '../journal/journalModel'

const blank = { recordType: 'MANUAL_TRADE', symbol: '', side: 'BUY', quantity: '', eventTimestamp: '', status: 'COMPLETE', executionPrice: '', runId: '', note: '' }
function submissionToken() { return globalThis.crypto?.randomUUID?.() ?? `manual-${Date.now()}-${Math.random().toString(16).slice(2)}` }
export default function ManualRecordPage() {
  const { addManualRecord } = useJournal(); const [form, setForm] = useState(blank); const [result, setResult] = useState(null); const [errors, setErrors] = useState({}); const token = useRef(submissionToken())
  const update = (event) => { if (result) setResult(null); setForm((value) => ({ ...value, [event.target.name]: event.target.value })) }
  const submit = (event) => {
    event.preventDefault(); setErrors({})
    try {
      const saved = addManualRecord(form, { submissionKey: token.current })
      setResult(saved.appended ? 'Manual journal record saved.' : 'This submission was already recorded.')
      if (saved.appended) setForm(blank)
    } catch (error) {
      setResult(null); setErrors(error instanceof JournalValidationError ? error.fields : { form: 'Record could not be validated.' })
    }
  }
  return <div className="page journal-page"><PageHeader eyebrow="Manual record" title="Add a trade or recovery note" lead="This form appends a local journal entry only. It cannot place, review, cancel, or flatten an order and cannot initialize a broker session." />
    <section className="journal-form-card"><form onSubmit={submit} noValidate>
      {errors.form && <p className="form-error" role="alert">{errors.form}</p>}
      <div className="journal-form-grid">
        <label>Record type<select name="recordType" value={form.recordType} onChange={update}><option>MANUAL_TRADE</option><option>MANUAL_RECOVERY</option></select></label>
        <label>Symbol<input name="symbol" value={form.symbol} onChange={update} maxLength="12" aria-describedby="symbol-error" />{errors.symbol && <small id="symbol-error" className="form-error">{errors.symbol}</small>}</label>
        <label>Side<select name="side" value={form.side} onChange={update}><option>BUY</option><option>SELL</option></select></label>
        <label>Quantity<input name="quantity" inputMode="decimal" value={form.quantity} onChange={update} maxLength="32" />{errors.quantity && <small className="form-error">{errors.quantity}</small>}</label>
        <label>Date and time<input name="eventTimestamp" type="datetime-local" value={form.eventTimestamp} onChange={update} />{errors.eventTimestamp && <small className="form-error">{errors.eventTimestamp}</small>}</label>
        <label>Status<input name="status" value={form.status} onChange={update} maxLength="64" />{errors.status && <small className="form-error">{errors.status}</small>}</label>
        <label>Execution price (optional)<input name="executionPrice" inputMode="decimal" value={form.executionPrice} onChange={update} maxLength="32" />{errors.executionPrice && <small className="form-error">{errors.executionPrice}</small>}</label>
        <label>Related Run ID (optional)<input name="runId" value={form.runId} onChange={update} maxLength="128" />{errors.runId && <small className="form-error">{errors.runId}</small>}</label>
        <label className="journal-form-wide">Operator note (optional)<textarea name="note" value={form.note} onChange={update} maxLength="500" rows="5" />{errors.note && <small className="form-error">{errors.note}</small>}</label>
      </div>
      <div className="journal-boundary"><strong>Journal only</strong><span>No broker or MCP action is reachable from this form.</span></div>
      <button className="primary-button" type="submit">Save journal record</button>
      {result && <p className="form-success" role="status">{result}</p>}
    </form></section>
  </div>
}
