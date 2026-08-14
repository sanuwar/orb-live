import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { JournalContext } from '../journal/journalContext'
import RemoteApp from '../remote/RemoteApp'
import RemoteLogin from '../remote/RemoteLogin'
import RemoteOperationsPage from '../remote/RemoteOperationsPage'
import * as client from '../remote/remoteClient'

vi.mock('../remote/remoteClient', async () => {
  const actual = await vi.importActual('../remote/remoteClient')
  return { ...actual, readRemoteSession: vi.fn(), readRemoteProjection: vi.fn(), loginRemote: vi.fn(), logoutRemote: vi.fn() }
})

const projection = {
  sourceStatus: 'AUTOMATIC_EVIDENCE_AVAILABLE',
  freshness: { status: 'CURRENT', lastUpdatedAt: '2026-08-14T14:20:00Z' },
  watcher: { currentState: 'NOT_RUNNING', observedAt: '2026-08-14T14:20:00Z', lastRun: { result: 'CANDIDATE', terminalState: 'CANDIDATE_CAPTURED', startedAt: '2026-08-14T13:40:00Z', finishedAt: '2026-08-14T14:17:00Z', stopReason: 'CANDIDATE_CAPTURED' } },
  sessions: [{ sessionId: 'watch-safe', startedAt: '2026-08-14T13:40:00Z', completedAt: '2026-08-14T14:17:00Z', terminalState: 'CANDIDATE_CAPTURED', stopReason: 'CANDIDATE_CAPTURED', evaluationCount: 1, evaluations: [{ scheduledAt: '2026-08-14T14:15:00Z', filterEvidence: 'UNAVAILABLE', filterFacts: [] }] }],
  records: [{ source: 'AUTOMATIC_RUNTIME', status: 'CANDIDATE_CAPTURED', commissioningStage: 'CANDIDATE_CAPTURED', symbol: 'NVDA', reason: 'LONG_CANDIDATE', eventTimestamp: '2026-08-14T14:17:00Z' }],
  events: [{ eventId: 'event-safe', eventType: 'CANDIDATE_DETECTED', occurredAt: '2026-08-14T14:17:00Z', severity: 'REVIEW', component: 'strategy', detailCode: 'LONG_CANDIDATE', source: 'AUTOMATIC_RUNTIME' }],
}
const automatic = { status: projection.sourceStatus, sessions: projection.sessions, records: projection.records, events: projection.events }

describe('authenticated remote read-only monitoring', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.setSystemTime(new Date('2026-08-14T15:00:00Z')); window.location.hash = '#/operations'; localStorage.clear() })

  it('renders a login before any monitoring surface', () => {
    render(<RemoteLogin onLogin={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'Remote monitoring sign in' })).toBeVisible()
    expect(screen.queryByText('NVDA')).not.toBeInTheDocument()
  })

  it('shows authenticated read-only watcher, last run, candidate, events, freshness, and ET timestamps', () => {
    render(<JournalContext.Provider value={{ automatic }}><RemoteOperationsPage projection={projection}/></JournalContext.Provider>)
    expect(screen.getByText('REMOTE MONITORING - READ ONLY')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'LIVE / CURRENT' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'NOT_RUNNING' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'CANDIDATE', level: 2 })).toBeVisible()
    expect(screen.getByText('NVDA')).toBeVisible()
    expect(screen.getAllByText('Aug 14, 2026 · 10:17:00 AM ET').length).toBeGreaterThan(1)
    expect(screen.getByText(/CANDIDATE_DETECTED/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /start|stop|readiness|approve|authorize|entry/i })).not.toBeInTheDocument()
  })

  it('fails candidate attention closed when the remote projection is stale', () => {
    const stale = { ...projection, freshness: { status: 'STALE', lastUpdatedAt: '2026-08-14T13:00:00Z' } }
    render(<JournalContext.Provider value={{ automatic }}><RemoteOperationsPage projection={stale}/></JournalContext.Provider>)
    expect(screen.getByRole('heading', { name: 'CANDIDATE EVIDENCE STALE' })).toBeVisible()
    expect(document.title).toBe('ORB-LIVE')
  })

  it('shows no evidence and connection failure without retaining an active candidate', () => {
    const none = { sourceStatus: 'NO_AUTOMATIC_EVIDENCE', freshness: { status: 'NO_EVIDENCE', lastUpdatedAt: null }, watcher: { currentState: 'UNAVAILABLE', observedAt: null, lastRun: null }, sessions: [], records: [], events: [] }
    const emptyAutomatic = { status: none.sourceStatus, sessions: [], records: [], events: [] }
    render(<JournalContext.Provider value={{ automatic: emptyAutomatic }}><RemoteOperationsPage projection={none} connectionStatus="CONNECTION_UNAVAILABLE"/></JournalContext.Provider>)
    expect(screen.getByRole('heading', { name: 'CONNECTION UNAVAILABLE' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'WAITING FOR QUALIFYING SETUP' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'CANDIDATE DETECTED' })).not.toBeInTheDocument()
  })
  it('authenticates through the server session and never stores the password', async () => {
    client.readRemoteSession.mockResolvedValue({ ok: false })
    client.loginRemote.mockResolvedValue({ ok: true, payload: { csrfToken: 'remote-session-csrf' } })
    client.readRemoteProjection.mockResolvedValue({ ok: true, payload: projection })
    render(<RemoteApp />)
    const input = await screen.findByLabelText('Password')
    fireEvent.change(input, { target: { value: 'correct horse battery staple' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('NVDA')).toBeVisible()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
    expect(client.loginRemote).toHaveBeenCalledWith('correct horse battery staple')
    expect(localStorage.length).toBe(0)
  })

  it('keeps the remote dependency tree free of local control and remote mutations', () => {
    const root = join(dirname(fileURLToPath(import.meta.url)), '..')
    const sources = ['remote/RemoteApp.jsx', 'remote/RemoteLogin.jsx', 'remote/RemoteOperationsPage.jsx', 'remote/RemotePerformancePage.jsx', 'remote/remoteClient.js', 'remoteMain.jsx'].map((name) => readFileSync(join(root, name), 'utf8')).join('\n')
    expect(sources).not.toMatch(/localControlClient|127\.0\.0\.1:8766|api\/local-control|MorningControl|startLocalMorningWatch|stopLocalMorningWatch|checkLocalReadiness/)
    expect(sources).not.toMatch(/approve entry|authorize broker|commission order/i)
    expect(client.SESSION_ENDPOINT).toBe('/api/remote/session')
    expect(client.PROJECTION_ENDPOINT).toBe('/api/remote/projection')
    expect(client.LOGIN_ENDPOINT).toBe('/api/auth/login')
    expect(client.LOGOUT_ENDPOINT).toBe('/api/auth/logout')
  })
})
