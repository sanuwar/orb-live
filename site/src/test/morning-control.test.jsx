import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MorningControl from '../components/MorningControl'
import {
  checkLocalReadiness,
  readLocalControlStatus,
  startLocalMorningWatch,
  stopLocalMorningWatch,
} from '../operations/localControlClient'

vi.mock('../operations/localControlClient', () => ({
  checkLocalReadiness: vi.fn(),
  readLocalControlStatus: vi.fn(),
  startLocalMorningWatch: vi.fn(),
  stopLocalMorningWatch: vi.fn(),
}))

const unavailable = {
  available: false,
  ok: false,
  payload: { process_state: 'CONTROL_SERVICE_UNAVAILABLE', readiness: null },
}
const ready = {
  available: true,
  ok: true,
  payload: {
    service: 'LOCAL_CONTROL_ONLY',
    process_state: 'NOT_RUNNING',
    watcher_result: 'NONE',
    readiness: { status: 'READY_FOR_MORNING_WATCH', ready: true, blockers: [] },
  },
}

describe('MorningControl', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    readLocalControlStatus.mockResolvedValue(unavailable)
    checkLocalReadiness.mockResolvedValue(ready)
    startLocalMorningWatch.mockResolvedValue(ready)
    stopLocalMorningWatch.mockResolvedValue(ready)
  })

  it('labels the surface local-only and fails closed when unavailable', async () => {
    render(<MorningControl />)
    expect(screen.getByText('Local control only')).toBeVisible()
    expect(await screen.findByText('CONTROL_SERVICE_UNAVAILABLE')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Check readiness' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Start morning watch' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop watch' })).toBeDisabled()
    expect(screen.queryByText(/remote control/i)).not.toBeInTheDocument()
  })

  it('renders blocked readiness and never enables start', async () => {
    readLocalControlStatus.mockResolvedValue(ready)
    checkLocalReadiness.mockResolvedValue({
      available: true,
      ok: true,
      payload: {
        ...ready.payload,
        readiness: {
          status: 'BLOCKED_BY_LOCAL_CONFIGURATION',
          ready: false,
          blockers: ['ALPACA_API_KEY_ID_MISSING'],
        },
      },
    })
    render(<MorningControl />)
    await screen.findByText('READY_FOR_MORNING_WATCH')
    fireEvent.click(screen.getByRole('button', { name: 'Check readiness' }))
    expect(await screen.findByText(/ALPACA_API_KEY_ID_MISSING/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Start morning watch' })).toBeDisabled()
    expect(startLocalMorningWatch).not.toHaveBeenCalled()
  })

  it('enables only stop while the owned watcher is running', async () => {
    readLocalControlStatus.mockResolvedValue({
      ...ready,
      payload: { ...ready.payload, process_state: 'RUNNING', watcher_result: 'RUNNING' },
    })
    render(<MorningControl />)
    expect((await screen.findAllByText('RUNNING')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Start morning watch' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop watch' })).toBeEnabled()
  })

  it('renders a completed terminal result without enabling stop', async () => {
    readLocalControlStatus.mockResolvedValue({
      ...ready,
      payload: {
        ...ready.payload,
        process_state: 'COMPLETE',
        watcher_result: 'NO_CANDIDATE',
        terminal_state: 'WINDOW_COMPLETE_NO_CANDIDATE',
      },
    })
    render(<MorningControl />)
    expect(await screen.findByText('COMPLETE')).toBeVisible()
    expect(screen.getByText('NO_CANDIDATE')).toBeVisible()
    expect(screen.getByText('WINDOW_COMPLETE_NO_CANDIDATE')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Start morning watch' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop watch' })).toBeDisabled()
  })

  it('invokes only the fixed readiness operation', async () => {
    readLocalControlStatus.mockResolvedValue(ready)
    render(<MorningControl />)
    await screen.findByText('READY_FOR_MORNING_WATCH')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Check readiness' }))
    })
    await waitFor(() => expect(checkLocalReadiness).toHaveBeenCalledOnce())
  })
})
