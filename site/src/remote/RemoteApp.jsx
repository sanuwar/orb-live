import { useEffect, useMemo, useState } from 'react'
import { JournalContext } from '../journal/journalContext'
import RemoteLogin from './RemoteLogin'
import RemoteOperationsPage from './RemoteOperationsPage'
import RemotePerformancePage from './RemotePerformancePage'
import { loginRemote, logoutRemote, readRemoteProjection, readRemoteSession } from './remoteClient'

const emptyProjection = { sourceStatus: 'NO_AUTOMATIC_EVIDENCE', sessions: [], records: [], events: [], freshness: { status: 'NO_EVIDENCE', lastUpdatedAt: null }, watcher: { currentState: 'UNAVAILABLE', observedAt: null, lastRun: null } }
function automaticFrom(projection) { return { status: projection.sourceStatus, sessions: projection.sessions ?? [], records: projection.records ?? [], events: projection.events ?? [] } }

export default function RemoteApp() {
  const [auth, setAuth] = useState({ state: 'loading', csrfToken: null })
  const [projection, setProjection] = useState(emptyProjection)
  const [connection, setConnection] = useState('CONNECTING')
  const [route, setRoute] = useState(() => window.location.hash === '#/performance' ? 'performance' : 'operations')
  const automatic = useMemo(() => automaticFrom(projection), [projection])
  useEffect(() => {
    let active = true
    readRemoteSession().then((result) => { if (!active) return; if (result.ok) setAuth({ state: 'authenticated', csrfToken: result.payload.csrfToken }); else setAuth({ state: 'anonymous', csrfToken: null }) })
    const onHash = () => setRoute(window.location.hash === '#/performance' ? 'performance' : 'operations')
    window.addEventListener('hashchange', onHash)
    return () => { active = false; window.removeEventListener('hashchange', onHash) }
  }, [])
  useEffect(() => {
    if (auth.state !== 'authenticated') return undefined
    let active = true
    async function refresh() {
      const result = await readRemoteProjection()
      if (!active) return
      if (result.ok) { setProjection(result.payload); setConnection('CONNECTED') } else { setProjection(emptyProjection); setConnection('CONNECTION_UNAVAILABLE') }
    }
    refresh()
    const timer = window.setInterval(refresh, 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [auth.state])
  async function login(password) {
    const result = await loginRemote(password)
    if (!result.ok) { setAuth({ state: 'failed', csrfToken: null }); return }
    setAuth({ state: 'authenticated', csrfToken: result.payload.csrfToken })
  }
  async function logout() {
    await logoutRemote(auth.csrfToken)
    setProjection(emptyProjection)
    setConnection('CONNECTING')
    setAuth({ state: 'anonymous', csrfToken: null })
  }
  if (auth.state !== 'authenticated') return <RemoteLogin onLogin={login} unavailable={auth.state === 'failed'} />
  return <JournalContext.Provider value={{ automatic }}><div className="remote-shell"><header className="remote-topbar"><a className="remote-brand" href="#/operations">ORB-LIVE</a><nav aria-label="Remote monitoring"><a aria-current={route === 'operations' ? 'page' : undefined} href="#/operations">Operations</a><a aria-current={route === 'performance' ? 'page' : undefined} href="#/performance">Performance</a></nav><button type="button" onClick={logout}>Log out</button></header><div className="remote-readonly-banner">REMOTE MONITORING - READ ONLY</div><main id="main-content" className="main-content">{route === 'performance' ? <RemotePerformancePage automatic={automatic}/> : <RemoteOperationsPage projection={projection} connectionStatus={connection}/>}</main></div></JournalContext.Provider>
}
