import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MorningFunnelPanel from '../components/MorningFunnelPanel'
import CandidateAttentionPanel from '../components/CandidateAttentionPanel'
import AutomaticEventFeed from '../components/AutomaticEventFeed'
import { JournalContext } from '../journal/journalContext'
import { parseAutomaticProjection } from '../journal/runtimeProjection'

const gate = (openingGap, openingRvol, spyVwap, orbBreakout) => ({ openingGap, openingRvol, spyVwap, orbBreakout })
const aapl = { symbol: 'AAPL', state: 'REJECTED', failedRule: 'OPENING_GAP', rejectionReason: 'Opening gap is below the required threshold.', terminalRejectionReason: 'Opening gap is below the required threshold.', spyVwapEnabled: true, gates: gate('REJECTED', 'NOT_EVALUATED', 'NOT_EVALUATED', 'NOT_EVALUATED'), observedGapPct: '0.5', requiredGapPct: '1.0', observedOpeningRvol: '0', requiredOpeningRvol: '3.0' }
const nvda = { symbol: 'NVDA', state: 'REJECTED', failedRule: 'OPENING_RANGE_BREAKOUT', rejectionReason: 'Completed bar has not broken above the opening-range high.', terminalRejectionReason: null, spyVwapEnabled: true, gates: gate('PASSED', 'PASSED', 'PASSED', 'WAITING'), observedGapPct: '1.4', requiredGapPct: '1.0', observedOpeningRvol: '3.7', requiredOpeningRvol: '3.0' }
const funnel = { status: 'AVAILABLE', startingSymbolCount: 2, survivorsAfterGap: 1, survivorsAfterOpeningRvol: 1, survivorsAfterSpyVwap: 1, survivorsAfterBreakout: 0, spyVwapEnabled: true, gapMinimumPct: '1.0', openingRvolMinimum: '3.0', aliveSymbols: ['NVDA'], rejectedSymbols: [{ symbol: 'AAPL', reason: 'Opening gap is below the required threshold.', failedRule: 'OPENING_GAP' }], unavailableSymbols: [] }
const session = (evaluation = { scheduledAt: '2026-08-14T14:00:00Z', filterEvidence: 'AVAILABLE', filterFacts: [aapl, nvda], funnel }, startedAt = '2026-08-14T13:40:00Z') => ({ sessionId: 'watch-safe', startedAt, completedAt: '2026-08-14T14:00:00Z', terminalState: 'WINDOW_COMPLETE_NO_CANDIDATE', stopReason: 'NO_SURVIVORS_AFTER_STATIC_FILTERS', evaluationCount: 1, evaluations: [evaluation] })
function renderPanel(automatic) { return render(<JournalContext.Provider value={{ automatic }}><MorningFunnelPanel /></JournalContext.Provider>) }

describe('Operations Morning Funnel', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-08-14T15:00:00Z')) })
  afterEach(() => vi.useRealTimers())

  it('renders mixed runtime-owned survivors, waits, and exact static rejection reasons', () => {
    renderPanel({ status: 'AUTOMATIC_EVIDENCE_AVAILABLE', sessions: [session()], records: [], events: [] })
    expect(screen.getByRole('heading', { name: 'Latest authentic symbol progression' })).toBeVisible()
    expect(screen.getByText(/Aug 14, 2026 · 10:00:00 AM ET/)).toBeVisible()
    expect(screen.getByText('Opening gap is below the required threshold.')).toBeVisible()
    expect(screen.getByText('WAITING - Completed bar has not broken above the opening-range high.')).toBeVisible()
    expect(screen.getByText('WAITING')).toBeVisible()
    expect(screen.getAllByText('NOT EVALUATED').length).toBeGreaterThan(0)
    expect(screen.getAllByText('NVDA')).toHaveLength(2)
  })

  it('fails closed for stale, incomplete, and missing projection evidence', () => {
    const stale = renderPanel({ sessions: [session(undefined, '2026-08-13T13:40:00Z')], records: [], events: [] })
    expect(screen.getByRole('heading', { name: 'STALE' })).toBeVisible()
    stale.unmount()
    const incompleteEvaluation = { scheduledAt: '2026-08-14T14:00:00Z', filterEvidence: 'UNAVAILABLE', filterFacts: [], funnel: null }
    const incomplete = renderPanel({ sessions: [session(incompleteEvaluation)], records: [], events: [] })
    expect(screen.getByRole('heading', { name: 'EVIDENCE INCOMPLETE' })).toBeVisible()
    incomplete.unmount()
    renderPanel({ sessions: [], records: [], events: [] })
    expect(screen.getByRole('heading', { name: 'FUNNEL EVIDENCE UNAVAILABLE' })).toBeVisible()
  })

  it('accepts the explicit funnel allowlist and preserves legacy projections as unavailable facts', () => {
    const payload = { schemaVersion: 1, status: 'AUTOMATIC_EVIDENCE_AVAILABLE', sessions: [session()], records: [], events: [] }
    payload.sessions[0].evaluations[0].funnel.account_id = 'must-not-survive'
    expect(parseAutomaticProjection(payload).sessions[0].evaluations[0].funnel.aliveSymbols).toEqual(['NVDA'])
    expect(parseAutomaticProjection(payload).sessions[0].evaluations[0].funnel).not.toHaveProperty('account_id')
    const legacy = structuredClone(payload)
    legacy.sessions[0].evaluations[0].filterFacts = [{ symbol: 'AAPL', state: 'REJECTED', failedRule: 'OPENING_GAP' }]
    delete legacy.sessions[0].evaluations[0].funnel
    expect(parseAutomaticProjection(legacy).sessions[0].evaluations[0].filterFacts[0].gates.openingGap).toBe('UNAVAILABLE')
  })

  it('remains read-only and keeps Candidate Status and Events as separate owned surfaces', () => {
    const automatic = { status: 'AUTOMATIC_EVIDENCE_AVAILABLE', sessions: [session()], records: [], events: [{ eventId: 'safe', eventType: 'SCANNER_EVALUATED', occurredAt: '2026-08-14T14:00:00Z', severity: 'INFO', component: 'morning_watcher', detailCode: 'SCANNER_EVALUATED', source: 'AUTOMATIC_RUNTIME' }] }
    render(<JournalContext.Provider value={{ automatic }}><CandidateAttentionPanel /><MorningFunnelPanel /><AutomaticEventFeed /></JournalContext.Provider>)
    expect(screen.getByText('SESSION COMPLETE - NO CANDIDATE')).toBeVisible()
    expect(screen.getByText(/SCANNER_EVALUATED/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /approve|authorize|entry/i })).not.toBeInTheDocument()
  })
})
