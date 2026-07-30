import { describe, expect, it } from 'vitest'
import { JournalValidationError, chronological, createAgentRecord, createManualRecord, decimalText, sumDecimalStrings } from '../journal/journalModel'
import { createJournalStore } from '../journal/journalStore'

function memoryStorage() { const data = new Map(); return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) } }
const manual = { recordType: 'MANUAL_TRADE', symbol: ' aapl ', side: 'BUY', quantity: '1.2500', eventTimestamp: '2026-01-01T10:00', status: 'COMPLETE', executionPrice: '10.500', runId: '', note: 'local note' }

describe('journal model and store', () => {
  it('discovers an empty append-only local store', () => { expect(createJournalStore(memoryStorage()).discover()).toEqual([]) })
  it('creates Decimal-safe normalized manual records', () => {
    const record = createManualRecord(manual, { submissionKey: 'form-1', createdTimestamp: '2026-01-01T15:01:00.000Z' })
    expect(record.symbol).toBe('AAPL'); expect(record.quantity).toBe('1.25'); expect(record.executionPrice).toBe('10.5'); expect(typeof record.quantity).toBe('string')
  })
  it('creates manual recovery records with operator intervention', () => {
    const record = createManualRecord({ ...manual, recordType: 'MANUAL_RECOVERY', side: 'SELL', runId: 'fake-run-1' }, { submissionKey: 'form-2' })
    expect(record.source).toBe('MANUAL_RECOVERY'); expect(record.operatorInterventionRequired).toBe(true)
  })
  it('prevents duplicate form submissions', () => {
    const store = createJournalStore(memoryStorage()); const record = createManualRecord(manual, { submissionKey: 'same-form' })
    expect(store.append(record).appended).toBe(1); expect(store.append(record).duplicates).toBe(1); expect(store.discover()).toHaveLength(1)
  })
  it.each([['0', 'quantity'], ['-1', 'quantity'], ['1e3', 'quantity'], ['NaN', 'executionPrice']])('rejects invalid financial input %s', (value, field) => {
    const input = { ...manual, [field]: value }
    expect(() => createManualRecord(input, { submissionKey: `bad-${field}-${value}` })).toThrow(JournalValidationError)
  })
  it('rejects script markup and strips benign tags', () => {
    expect(() => createManualRecord({ ...manual, note: '<script>alert(1)</script>' }, { submissionKey: 'unsafe' })).toThrowError(/UNSAFE_TEXT/)
    expect(createManualRecord({ ...manual, note: '<b>reviewed</b>' }, { submissionKey: 'safe' }).operatorNote).toBe('reviewed')
  })
  it('orders normalized events chronologically and leaves unknown initiator unproven', () => {
    const context = { evidenceSha256: 'a'.repeat(64), environment: 'FAKE', runId: 'fake-run', createdTimestamp: '2026-01-01T00:00:00.000Z' }
    const later = createAgentRecord({ eventKey: '2', stage: 'COMPLETE', eventTimestamp: '2026-01-01T12:00:00Z', initiator: 'UNKNOWN' }, context)
    const earlier = createAgentRecord({ eventKey: '1', stage: 'RUN_STARTED', eventTimestamp: '2026-01-01T10:00:00Z' }, context)
    expect(chronological([later, earlier]).map((record) => record.commissioningStage)).toEqual(['RUN_STARTED', 'COMPLETE'])
    expect(later.initiator).toBe('UNPROVEN')
  })
  it('sums financial strings exactly without binary floating point', () => { expect(sumDecimalStrings(['9007199254740993.01', '-0.01', '2'])).toBe('9007199254740995') })
  it('does not use floating-point arithmetic for persisted decimals', () => { expect(decimalText('9007199254740993.0100', 'value')).toBe('9007199254740993.01') })
})
