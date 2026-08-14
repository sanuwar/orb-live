import { easternDateKey } from '../formatters/easternTime'

function timestamp(value) { return typeof value === 'string' && !Number.isNaN(Date.parse(value)) ? value : null }
function newest(items, field) { return [...items].filter((item) => timestamp(item[field])).sort((left, right) => right[field].localeCompare(left[field]))[0] ?? null }

export function morningFunnel(automatic, now = new Date()) {
  const sessions = Array.isArray(automatic?.sessions) ? automatic.sessions : []
  const latest = newest(sessions, 'startedAt')
  if (!latest) return { state: 'NO_EVIDENCE' }
  if (easternDateKey(latest.startedAt ?? latest.completedAt) !== easternDateKey(now)) return { state: 'STALE', session: latest }
  const evaluation = latest.evaluations?.at(-1)
  if (!evaluation || evaluation.filterEvidence !== 'AVAILABLE' || !evaluation.funnel || evaluation.funnel.status !== 'AVAILABLE') return { state: 'INCOMPLETE', session: latest, evaluation }
  return { state: 'AVAILABLE', session: latest, evaluation, funnel: evaluation.funnel }
}
