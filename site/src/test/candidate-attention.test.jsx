import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CandidateAttentionPanel from '../components/CandidateAttentionPanel'
import AutomaticEventFeed from '../components/AutomaticEventFeed'
import { JournalContext } from '../journal/journalContext'

const now = new Date('2026-08-14T15:00:00Z')
const session = (terminalState = 'RUNNING') => ({ sessionId: 'watch-safe', startedAt: '2026-08-14T13:40:00Z', completedAt: '2026-08-14T14:00:00Z', terminalState, stopReason: 'SAFE', evaluationCount: 2, evaluations: [{ scheduledAt: '2026-08-14T13:55:00Z', filterEvidence: 'UNAVAILABLE', filterFacts: [] }] })
const candidate = (symbol = 'NVDA', timestamp = '2026-08-14T14:17:00Z') => ({ source: 'AUTOMATIC_RUNTIME', commissioningStage: 'CANDIDATE_CAPTURED', symbol, eventTimestamp: timestamp })
function renderPanel(automatic) { return render(<JournalContext.Provider value={{ automatic }}><CandidateAttentionPanel /></JournalContext.Provider>) }

describe('Operations Candidate Status', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(now); document.title = 'ORB-LIVE' })
  afterEach(() => vi.useRealTimers())
  it('shows the waiting placeholder and authentic session evaluation facts only', () => { renderPanel({ sessions: [session()], records: [], events: [] }); expect(screen.getByRole('heading', { name: 'WAITING FOR QUALIFYING SETUP' })).toBeVisible(); expect(screen.getByText('2')).toBeVisible(); expect(screen.getByText('Aug 14, 2026 · 9:55:00 AM ET')).toBeVisible() })
  it('activates only for an authentic current candidate record and changes document title', () => { renderPanel({ sessions: [session('CANDIDATE_CAPTURED')], records: [candidate()], events: [] }); expect(screen.getByRole('heading', { name: 'CANDIDATE DETECTED' })).toBeVisible(); expect(screen.getByText('NVDA')).toBeVisible(); expect(screen.getByText('Aug 14, 2026 · 10:17:00 AM ET')).toBeVisible(); expect(screen.getAllByText('N/A')).toHaveLength(3); expect(document.title).toBe('CANDIDATE - NVDA - ORB-LIVE'); expect(screen.getByText('REVIEW REQUIRED')).toBeVisible() })
  it('fails closed for missing, stale, and terminal no-candidate evidence', () => { const view = renderPanel({ sessions: [session('CANDIDATE_CAPTURED')], records: [candidate(null)], events: [] }); expect(screen.getByRole('heading', { name: 'CANDIDATE EVIDENCE INCOMPLETE' })).toBeVisible(); view.unmount(); renderPanel({ sessions: [], records: [candidate('NVDA', '2026-08-13T14:17:00Z')], events: [] }); expect(screen.getByRole('heading', { name: 'CANDIDATE EVIDENCE STALE' })).toBeVisible(); expect(document.title).toBe('ORB-LIVE'); view.unmount(); renderPanel({ sessions: [session('WINDOW_COMPLETE_NO_CANDIDATE')], records: [], events: [] }); expect(screen.getByRole('heading', { name: 'SESSION COMPLETE - NO CANDIDATE' })).toBeVisible() })
  it('leaves historical events read-only and exposes no authorization control', () => { const automatic = { status: 'AUTOMATIC_EVIDENCE_AVAILABLE', sessions: [], records: [], events: [{ eventId: 'safe', eventType: 'SCANNER_EVALUATED', occurredAt: '2026-08-14T14:00:00Z', severity: 'INFO', component: 'morning_watcher', detailCode: 'SCANNER_EVALUATED' }] }; render(<JournalContext.Provider value={{ automatic }}><CandidateAttentionPanel /><AutomaticEventFeed /></JournalContext.Provider>); expect(screen.getByText(/SCANNER_EVALUATED/)).toBeVisible(); expect(screen.queryByRole('button', { name: /approve|authorize|entry/i })).not.toBeInTheDocument() })
})