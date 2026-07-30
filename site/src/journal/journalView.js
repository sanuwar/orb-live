import { sumDecimalStrings } from './journalModel'
import { chronological } from './journalModel'

export function filterRecords(records, filters = {}) {
  return chronological(records).filter((record) => {
    if (filters.symbol && record.symbol !== filters.symbol.trim().toUpperCase()) return false
    if (filters.source && record.source !== filters.source) return false
    if (filters.environment && record.environment !== filters.environment) return false
    if (filters.status && record.status !== filters.status) return false
    if (filters.runId && record.runId !== filters.runId.trim()) return false
    if (filters.operator === 'yes' && !record.operatorInterventionRequired) return false
    if (filters.operator === 'no' && record.operatorInterventionRequired) return false
    if (filters.from && record.eventTimestamp < new Date(`${filters.from}T00:00:00`).toISOString()) return false
    if (filters.to && record.eventTimestamp > new Date(`${filters.to}T23:59:59.999`).toISOString()) return false
    return true
  })
}

export function journalSummary(records) {
  const runIds = new Set(records.filter((record) => record.commissioningStage === 'VERIFIED_FLAT' && record.runId).map((record) => record.runId))
  const pnlRecords = records.filter((record) => record.realizedProfitLoss != null)
  return {
    total: records.length,
    agent: records.filter((record) => record.source === 'AGENT').length,
    manual: records.filter((record) => record.source !== 'AGENT').length,
    operatorRequired: records.filter((record) => record.operatorInterventionRequired).length,
    verifiedFlatRuns: runIds.size,
    completePnlCount: pnlRecords.length,
    realizedPnl: sumDecimalStrings(pnlRecords.map((record) => record.realizedProfitLoss)),
  }
}

export function recordsForRun(records, runId) {
  return chronological(records.filter((record) => record.runId === runId || record.relatedRecordId === runId))
}
