function factsFor(sessions) { return sessions.flatMap((session) => session.evaluations.flatMap((evaluation) => evaluation.filterEvidence === 'AVAILABLE' ? evaluation.filterFacts : [])) }
function rate(numerator, denominator) { return denominator ? numerator / denominator : null }

export function sessionAnalytics(automatic) {
  const sessions = Array.isArray(automatic?.sessions) ? automatic.sessions : []
  const candidate = sessions.filter((item) => item.terminalState === 'CANDIDATE_CAPTURED').length
  const noCandidate = sessions.filter((item) => item.terminalState === 'WINDOW_COMPLETE_NO_CANDIDATE').length
  const evaluations = sessions.reduce((total, item) => total + (Number.isInteger(item.evaluationCount) ? item.evaluationCount : 0), 0)
  const facts = factsFor(sessions)
  const rejected = facts.filter((fact) => fact.failedRule !== 'UNAVAILABLE')
  const filterRejections = Object.entries(rejected.reduce((all, fact) => ({ ...all, [fact.failedRule]: (all[fact.failedRule] ?? 0) + 1 }), {})).sort(([left], [right]) => left.localeCompare(right)).map(([rule, count]) => ({ rule, count, coverage: facts.length }))
  const symbols = Object.entries(facts.reduce((all, fact) => ({ ...all, [fact.symbol]: { evaluations: (all[fact.symbol]?.evaluations ?? 0) + 1, candidates: (all[fact.symbol]?.candidates ?? 0) + (fact.state === 'VALID_DECISION' ? 1 : 0), rejections: (all[fact.symbol]?.rejections ?? 0) + (fact.failedRule === 'UNAVAILABLE' ? 0 : 1) } }), {})).sort(([left], [right]) => left.localeCompare(right)).map(([symbol, value]) => ({ symbol, ...value, candidateFrequency: rate(value.candidates, value.evaluations) }))
  return { sessions: sessions.length, candidate, noCandidate, evaluations, candidateFrequency: rate(candidate, sessions.length), filterFactCoverage: facts.length, filterRejections, symbols }
}