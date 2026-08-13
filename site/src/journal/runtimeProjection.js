import { chronological } from './journalModel'

const SOURCES = new Set(['AUTOMATIC_RUNTIME'])
const STAGES = new Set(['COMPLETE', 'RUN_STARTED', 'CANDIDATE_CAPTURED'])

// eslint-disable-next-line no-control-regex
function safe(value, maximum = 128) { return typeof value === 'string' && value.length > 0 && value.length <= maximum && !/[<>\u0000-\u001f]/.test(value) }

export function parseAutomaticProjection(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.schemaVersion !== 1 || !Array.isArray(value.records) || !Array.isArray(value.events)) return { status: 'AUTOMATIC_EVIDENCE_UNAVAILABLE', records: [], events: [] }
  if (value.status === 'NO_AUTOMATIC_EVIDENCE') return { status: value.status, records: [], events: [] }
  try {
    const records = value.records.map((record) => {
      if (!record || record.source !== 'AUTOMATIC_RUNTIME' || !SOURCES.has(record.source) || !safe(record.journalRecordId) || !safe(record.runId) || !safe(record.status) || !STAGES.has(record.commissioningStage) || !safe(record.eventTimestamp, 48)) throw new Error('unsafe')
      return Object.freeze({ ...record, automaticallyImported: true })
    })
    const events = value.events.map((event) => {
      if (!event || !safe(event.eventId) || !safe(event.eventType) || !safe(event.occurredAt, 48) || !safe(event.severity) || !safe(event.component) || !safe(event.detailCode) || event.source !== 'AUTOMATIC_RUNTIME') throw new Error('unsafe')
      return Object.freeze({ ...event })
    }).sort((left, right) => right.occurredAt.localeCompare(left.occurredAt) || right.eventId.localeCompare(left.eventId))
    return { status: value.status === 'ALREADY_PROJECTED' ? 'AUTOMATIC_EVIDENCE_AVAILABLE' : 'AUTOMATIC_EVIDENCE_AVAILABLE', records: chronological(records), events: Object.freeze(events) }
  } catch { return { status: 'AUTOMATIC_EVIDENCE_UNAVAILABLE', records: [], events: [] } }
}
