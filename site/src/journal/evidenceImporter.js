import { JournalValidationError, chronological, createAgentRecord } from './journalModel'

const DENIED = ['authorization', 'account', 'brokerorder', 'fingerprint', 'accesstoken', 'refreshtoken', 'apikey', 'credential', 'cookie', 'oauth', 'header', 'rawrequest', 'rawresponse', 'browsersession']
const ROOT_KEYS = new Set(['schemaVersion', 'environment', 'runId', 'events'])
const EVENT_KEYS = new Set(['eventKey', 'stage', 'eventTimestamp', 'symbol', 'side', 'quantity', 'status', 'reason', 'executionPrice', 'plannedRisk', 'realizedProfitLoss', 'relatedRecordId', 'operatorInterventionRequired', 'initiator'])

function normalizedKey(key) { return key.toLowerCase().replace(/[^a-z0-9]/g, '') }
function hasDeniedKey(value) {
  if (!value || typeof value !== 'object') return false
  return Object.entries(value).some(([key, child]) => DENIED.some((part) => normalizedKey(key).includes(part)) || hasDeniedKey(child))
}
function exactKeys(value, allowed) { return Object.keys(value).every((key) => allowed.has(key)) }

async function evidenceHash(text, digest = globalThis.crypto?.subtle) {
  if (!digest?.digest) throw new JournalValidationError('HASH_UNAVAILABLE')
  const result = await digest.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(result), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function parseSanitizedEvidence(text, options = {}) {
  let payload
  try { payload = JSON.parse(text) } catch { return { status: 'rejected', code: 'MALFORMED_EVIDENCE', records: [] } }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || hasDeniedKey(payload)) {
    return { status: 'rejected', code: hasDeniedKey(payload) ? 'PRIVATE_FIELD_REJECTED' : 'MALFORMED_EVIDENCE', records: [] }
  }
  if (!exactKeys(payload, ROOT_KEYS) || payload.schemaVersion !== 1) return { status: 'rejected', code: 'UNSUPPORTED_EVIDENCE', records: [] }
  if (!['LIVE', 'FAKE'].includes(payload.environment) || !Array.isArray(payload.events) || !payload.events.length || payload.events.length > 500) return { status: 'rejected', code: 'MALFORMED_EVIDENCE', records: [] }
  if (payload.events.some((event) => !event || typeof event !== 'object' || !exactKeys(event, EVENT_KEYS))) return { status: 'rejected', code: 'UNSAFE_EVIDENCE_SHAPE', records: [] }
  try {
    const evidenceSha256 = await evidenceHash(text, options.digest)
    const context = { environment: payload.environment, runId: payload.runId, evidenceSha256, createdTimestamp: options.createdTimestamp ?? new Date().toISOString() }
    return { status: 'imported', code: 'EVIDENCE_ACCEPTED', evidenceSha256, records: chronological(payload.events.map((event) => createAgentRecord(event, context))) }
  } catch (error) {
    return { status: 'rejected', code: error instanceof JournalValidationError ? error.code : 'EVIDENCE_REJECTED', records: [] }
  }
}

export async function importSanitizedEvidence(text, store, options = {}) {
  const parsed = await parseSanitizedEvidence(text, options)
  if (parsed.status === 'rejected') return parsed
  const result = store.appendMany(parsed.records)
  const status = result.appended === 0 ? 'already imported' : result.appended === parsed.records.length ? 'imported' : 'partially supported'
  return { status, code: status === 'already imported' ? 'EVIDENCE_ALREADY_IMPORTED' : 'EVIDENCE_IMPORTED', evidenceSha256: parsed.evidenceSha256, importedCount: result.appended, duplicateCount: result.duplicates, records: result.records }
}
