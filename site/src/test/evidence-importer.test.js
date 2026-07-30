import { describe, expect, it } from 'vitest'
import { importSanitizedEvidence, parseSanitizedEvidence } from '../journal/evidenceImporter'
import { createJournalStore } from '../journal/journalStore'

const digest = { digest: async () => new Uint8Array(32).buffer }
function memoryStorage() { const data = new Map(); return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) } }
function evidence(environment = 'FAKE') { return JSON.stringify({ schemaVersion: 1, environment, runId: 'fake-run-001', events: [
  { eventKey: 'start', stage: 'RUN_STARTED', eventTimestamp: '2026-01-01T14:30:00Z', status: 'COMPLETE', initiator: 'AGENT' },
  { eventKey: 'entry', stage: 'ENTRY_RECONCILED', eventTimestamp: '2026-01-01T14:31:00Z', status: 'FILLED', symbol: 'ZZTEST', side: 'BUY', quantity: '1' },
  { eventKey: 'stop', stage: 'PROTECTIVE_STOP_RECONCILED', eventTimestamp: '2026-01-01T14:32:00Z', status: 'ACTIVE', symbol: 'ZZTEST', side: 'SELL', quantity: '1' },
  { eventKey: 'flat', stage: 'VERIFIED_FLAT', eventTimestamp: '2026-01-01T15:00:00Z', status: 'COMPLETE' },
] }) }

describe('sanitized agent evidence importer', () => {
  it.each(['FAKE', 'LIVE'])('distinguishes %s evidence', async (environment) => {
    const result = await parseSanitizedEvidence(evidence(environment), { digest, createdTimestamp: '2026-01-01T15:01:00Z' })
    expect(result.status).toBe('imported'); expect(result.records.every((record) => record.environment === environment)).toBe(true)
  })
  it('is idempotent by evidence hash and stable event key', async () => {
    const store = createJournalStore(memoryStorage()); const first = await importSanitizedEvidence(evidence(), store, { digest }); const second = await importSanitizedEvidence(evidence(), store, { digest })
    expect(first.importedCount).toBe(4); expect(second.status).toBe('already imported'); expect(store.discover()).toHaveLength(4)
  })
  it.each(['{', '[]', JSON.stringify({ schemaVersion: 2, events: [] })])('rejects malformed or unsupported evidence', async (payload) => { expect((await parseSanitizedEvidence(payload, { digest })).status).toBe('rejected') })
  it.each(['account_number', 'broker_order_id', 'access_token', 'refreshToken', 'fingerprint', 'raw_response', 'authorization_phrase', 'request_headers', 'oauth_url', 'cookie'])('rejects forbidden field %s without echoing its value', async (field) => {
    const payload = JSON.stringify({ schemaVersion: 1, environment: 'FAKE', runId: 'fake-run', events: [], [field]: 'PRIVATE-VALUE' })
    const result = await parseSanitizedEvidence(payload, { digest }); expect(result).toMatchObject({ status: 'rejected', code: 'PRIVATE_FIELD_REJECTED', records: [] }); expect(JSON.stringify(result)).not.toContain('PRIVATE-VALUE')
  })
  it('demonstrates operator-required evidence followed by manual recovery to verified flat', async () => {
    const store = createJournalStore(memoryStorage())
    const payload = JSON.stringify({ schemaVersion: 1, environment: 'FAKE', runId: 'fake-recovery-run', events: [
      { eventKey: 'entry', stage: 'ENTRY_RECONCILED', eventTimestamp: '2026-01-01T14:30:00Z', status: 'FILLED' },
      { eventKey: 'stop', stage: 'PROTECTIVE_STOP_SUBMITTED', eventTimestamp: '2026-01-01T14:31:00Z', status: 'SUBMITTED' },
      { eventKey: 'ambiguous', stage: 'STOP_CANCELLATION_PENDING', eventTimestamp: '2026-01-01T14:32:00Z', status: 'UNKNOWN' },
      { eventKey: 'operator', stage: 'OPERATOR_REQUIRED', eventTimestamp: '2026-01-01T14:33:00Z', status: 'OPERATOR_REQUIRED', operatorInterventionRequired: true },
    ] })
    await importSanitizedEvidence(payload, store, { digest })
    const { createManualRecord } = await import('../journal/journalModel')
    store.append(createManualRecord({ recordType: 'MANUAL_RECOVERY', symbol: 'ZZTEST', side: 'SELL', quantity: '1', eventTimestamp: '2026-01-01T15:00:00Z', status: 'VERIFIED_FLAT', runId: 'fake-recovery-run', note: 'Sanitized fake recovery' }, { submissionKey: 'fake-recovery-form' }))
    expect(store.discover().map((record) => record.commissioningStage)).toEqual(['ENTRY_RECONCILED', 'PROTECTIVE_STOP_SUBMITTED', 'STOP_CANCELLATION_PENDING', 'OPERATOR_REQUIRED', 'VERIFIED_FLAT'])
  })
  it('supports the operator-required incident sequence without inferring initiators', async () => {
    const payload = JSON.stringify({ schemaVersion: 1, environment: 'FAKE', runId: 'fake-incident', events: [
      { eventKey: 'entry', stage: 'ENTRY_RECONCILED', eventTimestamp: '2026-01-01T14:30:00Z', status: 'FILLED' },
      { eventKey: 'stop', stage: 'PROTECTIVE_STOP_SUBMITTED', eventTimestamp: '2026-01-01T14:31:00Z', status: 'SUBMITTED' },
      { eventKey: 'pending', stage: 'STOP_CANCELLATION_PENDING', eventTimestamp: '2026-01-01T14:32:00Z', status: 'UNKNOWN' },
      { eventKey: 'operator', stage: 'OPERATOR_REQUIRED', eventTimestamp: '2026-01-01T14:33:00Z', status: 'OPERATOR_REQUIRED', operatorInterventionRequired: true },
    ] })
    const result = await parseSanitizedEvidence(payload, { digest }); expect(result.records.map((record) => record.commissioningStage)).toEqual(['ENTRY_RECONCILED', 'PROTECTIVE_STOP_SUBMITTED', 'STOP_CANCELLATION_PENDING', 'OPERATOR_REQUIRED']); expect(result.records[2].initiator).toBe('UNPROVEN')
  })
})
