import { chronological } from './journalModel'

const SOURCES = new Set(['AUTOMATIC_RUNTIME'])
const STAGES = new Set(['COMPLETE', 'RUN_STARTED', 'CANDIDATE_CAPTURED'])
const FILTER_STATES = new Set(['VALID_DECISION', 'NO_SIGNAL', 'REJECTED', 'DATA_INVALID', 'DATA_INCOMPLETE', 'PROVIDER_FAILURE'])
const FILTER_RULES = new Set(['OPENING_GAP', 'OPENING_RELATIVE_VOLUME', 'SPY_ABOVE_VWAP', 'OPENING_RANGE_BREAKOUT', 'ENTRY_WINDOW', 'OTHER', 'UNAVAILABLE'])

// eslint-disable-next-line no-control-regex
function safe(value, maximum = 128) { return typeof value === 'string' && value.length > 0 && value.length <= maximum && !/[<>\u0000-\u001f]/.test(value) }
function safeFacts(value) {
  if (!Array.isArray(value) || value.length > 10) return null
  const facts = value.map((fact) => {
    if (!fact || !safe(fact.symbol, 12) || !FILTER_STATES.has(fact.state) || !FILTER_RULES.has(fact.failedRule)) throw new Error('unsafe')
    return Object.freeze({ symbol: fact.symbol, state: fact.state, failedRule: fact.failedRule })
  })
  return Object.freeze(facts)
}
function safeSessions(value) {
  if (!Array.isArray(value)) return []
  return Object.freeze(value.map((session) => {
    if (!session || !safe(session.sessionId) || !safe(session.terminalState) || !Number.isInteger(session.evaluationCount) || session.evaluationCount < 0 || session.evaluationCount > 17) throw new Error('unsafe')
    const evaluations = (Array.isArray(session.evaluations) ? session.evaluations : []).map((evaluation) => {
      if (!evaluation || !safe(evaluation.scheduledAt, 48) || !['AVAILABLE', 'UNAVAILABLE'].includes(evaluation.filterEvidence)) throw new Error('unsafe')
      const filterFacts = safeFacts(evaluation.filterFacts)
      if (filterFacts === null || (evaluation.filterEvidence === 'AVAILABLE' && filterFacts.length === 0)) throw new Error('unsafe')
      return Object.freeze({ scheduledAt: evaluation.scheduledAt, filterEvidence: evaluation.filterEvidence, filterFacts })
    })
    return Object.freeze({ sessionId: session.sessionId, startedAt: session.startedAt, completedAt: session.completedAt, terminalState: session.terminalState, stopReason: session.stopReason, evaluationCount: session.evaluationCount, evaluations: Object.freeze(evaluations) })
  }))
}

export function parseAutomaticProjection(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.schemaVersion !== 1 || !Array.isArray(value.records) || !Array.isArray(value.events)) return { status: 'AUTOMATIC_EVIDENCE_UNAVAILABLE', sessions: [], records: [], events: [] }
  if (value.status === 'NO_AUTOMATIC_EVIDENCE') return { status: value.status, sessions: [], records: [], events: [] }
  try {
    const records = value.records.map((record) => {
      if (!record || record.source !== 'AUTOMATIC_RUNTIME' || !SOURCES.has(record.source) || !safe(record.journalRecordId) || !safe(record.runId) || !safe(record.status) || !STAGES.has(record.commissioningStage) || !safe(record.eventTimestamp, 48)) throw new Error('unsafe')
      return Object.freeze({ ...record, automaticallyImported: true })
    })
    const events = value.events.map((event) => {
      if (!event || !safe(event.eventId) || !safe(event.eventType) || !safe(event.occurredAt, 48) || !safe(event.severity) || !safe(event.component) || !safe(event.detailCode) || event.source !== 'AUTOMATIC_RUNTIME') throw new Error('unsafe')
      return Object.freeze({ ...event })
    }).sort((left, right) => right.occurredAt.localeCompare(left.occurredAt) || right.eventId.localeCompare(left.eventId))
    return { status: 'AUTOMATIC_EVIDENCE_AVAILABLE', sessions: safeSessions(value.sessions), records: chronological(records), events: Object.freeze(events) }
  } catch { return { status: 'AUTOMATIC_EVIDENCE_UNAVAILABLE', sessions: [], records: [], events: [] } }
}