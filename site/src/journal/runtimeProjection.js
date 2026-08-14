import { chronological } from './journalModel'

const SOURCES = new Set(['AUTOMATIC_RUNTIME'])
const STAGES = new Set(['COMPLETE', 'RUN_STARTED', 'CANDIDATE_CAPTURED'])
const FILTER_STATES = new Set(['VALID_DECISION', 'NO_SIGNAL', 'REJECTED', 'DATA_INVALID', 'DATA_INCOMPLETE', 'PROVIDER_FAILURE'])
const FILTER_RULES = new Set(['OPENING_GAP', 'OPENING_RELATIVE_VOLUME', 'SPY_ABOVE_VWAP', 'OPENING_RANGE_BREAKOUT', 'ENTRY_WINDOW', 'OTHER', 'UNAVAILABLE'])
const GATE_STATES = new Set(['PASSED', 'REJECTED', 'WAITING', 'NOT_EVALUATED', 'UNAVAILABLE'])
const DECIMAL = /^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?$/

// eslint-disable-next-line no-control-regex
function safe(value, maximum = 128) { return typeof value === 'string' && value.length > 0 && value.length <= maximum && !/[<>\u0000-\u001f]/.test(value) }
function optionalSafe(value, maximum = 128) { return value === null || safe(value, maximum) }
function optionalDecimal(value) { return value === null || (typeof value === 'string' && DECIMAL.test(value)) }
function legacyGates() { return Object.freeze({ openingGap: 'UNAVAILABLE', openingRvol: 'UNAVAILABLE', spyVwap: 'UNAVAILABLE', orbBreakout: 'UNAVAILABLE' }) }
function safeFacts(value) {
  if (!Array.isArray(value) || value.length > 10) return null
  const facts = value.map((fact) => {
    if (!fact || !safe(fact.symbol, 12) || !FILTER_STATES.has(fact.state) || !FILTER_RULES.has(fact.failedRule)) throw new Error('unsafe')
    if (fact.gates === undefined) return Object.freeze({ symbol: fact.symbol, state: fact.state, failedRule: fact.failedRule, rejectionReason: null, terminalRejectionReason: null, spyVwapEnabled: null, gates: legacyGates(), observedGapPct: null, requiredGapPct: null, observedOpeningRvol: null, requiredOpeningRvol: null })
    const gateKeys = ['openingGap', 'openingRvol', 'spyVwap', 'orbBreakout']
    if (!fact.gates || Object.keys(fact.gates).length !== gateKeys.length || !gateKeys.every((key) => GATE_STATES.has(fact.gates[key])) || !optionalSafe(fact.rejectionReason) || !optionalSafe(fact.terminalRejectionReason) || ![true, false, null].includes(fact.spyVwapEnabled) || ![fact.observedGapPct, fact.requiredGapPct, fact.observedOpeningRvol, fact.requiredOpeningRvol].every(optionalDecimal)) throw new Error('unsafe')
    return Object.freeze({ symbol: fact.symbol, state: fact.state, failedRule: fact.failedRule, rejectionReason: fact.rejectionReason, terminalRejectionReason: fact.terminalRejectionReason, spyVwapEnabled: fact.spyVwapEnabled, gates: Object.freeze({ openingGap: fact.gates.openingGap, openingRvol: fact.gates.openingRvol, spyVwap: fact.gates.spyVwap, orbBreakout: fact.gates.orbBreakout }), observedGapPct: fact.observedGapPct, requiredGapPct: fact.requiredGapPct, observedOpeningRvol: fact.observedOpeningRvol, requiredOpeningRvol: fact.requiredOpeningRvol })
  })
  return Object.freeze(facts)
}
function safeCount(value) { return value === null || (Number.isInteger(value) && value >= 0 && value <= 10) }
function safeSymbols(value) { return Array.isArray(value) && value.length <= 10 && value.every((symbol) => safe(symbol, 12)) && new Set(value).size === value.length }
function safeFunnel(value, facts) {
  if (value === null || value === undefined) return null
  const counts = ['startingSymbolCount', 'survivorsAfterGap', 'survivorsAfterOpeningRvol', 'survivorsAfterSpyVwap', 'survivorsAfterBreakout']
  if (!['AVAILABLE', 'INCOMPLETE'].includes(value.status) || !counts.every((key) => safeCount(value[key])) || value.startingSymbolCount !== facts.length || ![true, false, null].includes(value.spyVwapEnabled) || !optionalDecimal(value.gapMinimumPct) || !optionalDecimal(value.openingRvolMinimum) || !safeSymbols(value.aliveSymbols) || !safeSymbols(value.unavailableSymbols) || !Array.isArray(value.rejectedSymbols) || value.rejectedSymbols.length > 10) throw new Error('unsafe')
  const rejectedSymbols = value.rejectedSymbols.map((item) => {
    if (!item || !safe(item.symbol, 12) || !safe(item.reason) || !['OPENING_GAP', 'OPENING_RELATIVE_VOLUME'].includes(item.failedRule)) throw new Error('unsafe')
    return Object.freeze({ symbol: item.symbol, reason: item.reason, failedRule: item.failedRule })
  })
  return Object.freeze({ status: value.status, startingSymbolCount: value.startingSymbolCount, survivorsAfterGap: value.survivorsAfterGap, survivorsAfterOpeningRvol: value.survivorsAfterOpeningRvol, survivorsAfterSpyVwap: value.survivorsAfterSpyVwap, survivorsAfterBreakout: value.survivorsAfterBreakout, spyVwapEnabled: value.spyVwapEnabled, gapMinimumPct: value.gapMinimumPct, openingRvolMinimum: value.openingRvolMinimum, aliveSymbols: Object.freeze([...value.aliveSymbols]), unavailableSymbols: Object.freeze([...value.unavailableSymbols]), rejectedSymbols: Object.freeze(rejectedSymbols) })
}
function safeSessions(value) {
  if (!Array.isArray(value)) return []
  return Object.freeze(value.map((session) => {
    if (!session || !safe(session.sessionId) || !safe(session.terminalState) || !Number.isInteger(session.evaluationCount) || session.evaluationCount < 0 || session.evaluationCount > 17) throw new Error('unsafe')
    const evaluations = (Array.isArray(session.evaluations) ? session.evaluations : []).map((evaluation) => {
      if (!evaluation || !safe(evaluation.scheduledAt, 48) || !['AVAILABLE', 'UNAVAILABLE'].includes(evaluation.filterEvidence)) throw new Error('unsafe')
      const filterFacts = safeFacts(evaluation.filterFacts)
      if (filterFacts === null || (evaluation.filterEvidence === 'AVAILABLE' && filterFacts.length === 0)) throw new Error('unsafe')
      const funnel = safeFunnel(evaluation.funnel, filterFacts)
      if (evaluation.filterEvidence === 'UNAVAILABLE' && funnel !== null) throw new Error('unsafe')
      return Object.freeze({ scheduledAt: evaluation.scheduledAt, filterEvidence: evaluation.filterEvidence, filterFacts, funnel })
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
