import { useEffect, useState } from 'react'
import { checkLocalReadiness, readLocalControlStatus, startLocalMorningWatch, stopLocalMorningWatch } from '../operations/localControlClient'

const initial = { available: false, process_state: 'CONTROL_SERVICE_UNAVAILABLE', watcher_result: 'UNKNOWN', readiness: null }
export default function MorningControl() {
  const [state, setState] = useState(initial)
  const [pending, setPending] = useState(false)
  async function apply(operation) {
    setPending(true)
    const result = await operation()
    setPending(false)
    setState({ available: result.available, ...result.payload })
  }
  useEffect(() => { void apply(readLocalControlStatus) }, [])
  const running = ['RUNNING', 'STOP_REQUESTED'].includes(state.process_state)
  const ready = state.readiness?.ready === true
  const startDisabled = pending || !state.available || !ready || state.process_state !== 'NOT_RUNNING'
  const stopDisabled = pending || !state.available || !running
  return <section className="morning-control" aria-labelledby="morning-control-title">
    <header><div><span className="section-label">Local control only</span><h2 id="morning-control-title">Morning control</h2><p>Fixed readiness and watcher operations on this PC. No broker-write or commissioning action is available here.</p></div><span className={`control-state control-state-${state.process_state.toLowerCase().replaceAll('_', '-')}`}>{state.process_state}</span></header>
    <dl className="control-facts"><div><dt>Readiness</dt><dd>{state.readiness?.status ?? 'NOT CHECKED'}</dd></div><div><dt>Watcher result</dt><dd>{state.watcher_result ?? 'UNKNOWN'}</dd></div><div><dt>Started</dt><dd>{state.started_at ?? '—'}</dd></div><div><dt>Terminal state</dt><dd>{state.terminal_state ?? '—'}</dd></div></dl>
    {state.readiness?.blockers?.length > 0 && <p className="control-blockers">Blocked: {state.readiness.blockers.join(', ')}</p>}
    <div className="control-actions"><button type="button" className="secondary-button" disabled={pending || !state.available} onClick={() => void apply(checkLocalReadiness)}>Check readiness</button><button type="button" className="primary-button" disabled={startDisabled} onClick={() => void apply(startLocalMorningWatch)}>Start morning watch</button><button type="button" className="secondary-button" disabled={stopDisabled} onClick={() => void apply(stopLocalMorningWatch)}>Stop watch</button><button type="button" className="secondary-button" disabled={pending || !state.available} onClick={() => void apply(readLocalControlStatus)}>Refresh status</button></div>
  </section>
}