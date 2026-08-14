import { easternDateKey } from '../formatters/easternTime'

function timestamp(value) { return typeof value === 'string' && !Number.isNaN(Date.parse(value)) ? value : null }
function newest(items, field) { return [...items].filter((item) => timestamp(item[field])).sort((left, right) => right[field].localeCompare(left[field]))[0] ?? null }
function currentSession(sessions, today) { return newest(sessions.filter((session) => easternDateKey(session.startedAt ?? session.completedAt) === today), 'startedAt') }

export function candidateAttention(automatic, now = new Date()) {
  const sessions = Array.isArray(automatic?.sessions) ? automatic.sessions : []
  const records = Array.isArray(automatic?.records) ? automatic.records : []
  const today = easternDateKey(now)
  const session = currentSession(sessions, today)
  if (session?.terminalState === 'WINDOW_COMPLETE_NO_CANDIDATE') return { state: 'NO_CANDIDATE', session }
  const candidate = newest(records.filter((record) => record?.source === 'AUTOMATIC_RUNTIME' && record.commissioningStage === 'CANDIDATE_CAPTURED'), 'eventTimestamp')
  if (candidate && easternDateKey(candidate.eventTimestamp) !== today) return { state: 'STALE', session }
  if (session?.terminalState === 'CANDIDATE_CAPTURED') {
    if (!candidate || !/^[A-Z]{1,12}$/.test(candidate.symbol ?? '') || !timestamp(candidate.eventTimestamp)) return { state: 'INCOMPLETE', session }
    return { state: 'ACTIVE', session, candidate: { symbol: candidate.symbol, timestamp: candidate.eventTimestamp } }
  }
  if (candidate && easternDateKey(candidate.eventTimestamp) === today) return { state: 'INCOMPLETE', session }
  return { state: 'WAITING', session }
}