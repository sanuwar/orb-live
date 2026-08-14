import { useEffect, useState } from 'react'
import { checkLocalReadiness, readLocalControlStatus, startLocalMorningWatch, stopLocalMorningWatch } from '../operations/localControlClient'
import { formatEasternTime } from '../formatters/easternTime'

const initial = { available: false, process_state: 'CONTROL_SERVICE_UNAVAILABLE', watcher_result: 'NONE', readiness: null, last_run: null }
const label = (value, fallback = 'N/A') => value ? value.replaceAll('_', ' ') : fallback
function lastResult(run) {
  if (!run) return 'NOT STARTED'
  if (run.result === 'NOT_RUN_OUTSIDE_ENTRY_WINDOW') return 'NOT RUN — OUTSIDE ENTRY WINDOW'
  return label(run.result)
}
export default function MorningControl() {
  const [state, setState] = useState(initial); const [pending, setPending] = useState(false)
  async function apply(operation) { setPending(true); const result = await operation(); setPending(false); setState({ available: result.available, process_state: result.payload.process_state ?? 'CONTROL_SERVICE_UNAVAILABLE', ...result.payload }) }
  useEffect(() => { let active = true; readLocalControlStatus().then((result) => { if (active) setState({ available: result.available, process_state: result.payload.process_state ?? 'CONTROL_SERVICE_UNAVAILABLE', ...result.payload }) }); return () => { active = false } }, [])
  const running = ['RUNNING', 'STOP_REQUESTED'].includes(state.process_state); const ready = state.readiness?.ready === true
  return <section className="morning-control" aria-labelledby="morning-control-title"><header><div><span className="section-label">Local control only</span><h2 id="morning-control-title">Morning control</h2><p>Fixed readiness and watcher operations on this PC. No broker-write or commissioning action is available here.</p></div><span className={`control-state control-state-${state.process_state.toLowerCase().replaceAll('_', '-')}`}>{label(state.process_state)}</span></header>
    <dl className="control-facts"><div><dt>Readiness</dt><dd>{label(state.readiness?.status, 'NOT CHECKED')}</dd></div><div><dt>Current watcher</dt><dd>{label(state.watcher_result, 'NONE')}</dd></div><div><dt>Started</dt><dd>{state.started_at ? formatEasternTime(state.started_at) : 'NOT STARTED'}</dd></div><div><dt>Last run</dt><dd>{lastResult(state.last_run)}</dd></div></dl>
    {state.last_run && <dl className="control-facts"><div><dt>Last started</dt><dd>{formatEasternTime(state.last_run.started_at)}</dd></div><div><dt>Finished</dt><dd>{formatEasternTime(state.last_run.finished_at)}</dd></div><div><dt>Terminal state</dt><dd>{label(state.last_run.terminal_state, 'N/A')}</dd></div><div><dt>Stop reason</dt><dd>{label(state.last_run.stop_reason, 'N/A')}</dd></div></dl>}
    {state.readiness?.blockers?.length > 0 && <p className="control-blockers">Blocked: {state.readiness.blockers.join(', ')}</p>}
    <div className="control-actions"><button type="button" className="secondary-button" disabled={pending || !state.available} onClick={() => void apply(checkLocalReadiness)}>Check readiness</button><button type="button" className="primary-button" disabled={pending || !state.available || !ready || running} onClick={() => void apply(startLocalMorningWatch)}>Start morning watch</button><button type="button" className="secondary-button" disabled={pending || !state.available || !running} onClick={() => void apply(stopLocalMorningWatch)}>Stop watch</button><button type="button" className="secondary-button" disabled={pending || !state.available} onClick={() => void apply(readLocalControlStatus)}>Refresh status</button></div>
  </section>
}
