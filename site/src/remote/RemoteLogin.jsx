import { useState } from 'react'

export default function RemoteLogin({ onLogin, unavailable = false }) {
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  async function submit(event) {
    event.preventDefault()
    setPending(true)
    await onLogin(password)
    setPassword('')
    setPending(false)
  }
  return <main className="remote-login"><section className="remote-login-card" aria-labelledby="remote-login-title"><span className="section-label">ORB-LIVE</span><h1 id="remote-login-title">Remote monitoring sign in</h1><p>Authenticated sanitized monitoring only. Trading control remains on the local Windows PC.</p><form onSubmit={submit}><label htmlFor="remote-password">Password</label><input id="remote-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength="12"/><button type="submit" disabled={pending}>{pending ? 'Signing in...' : 'Sign in'}</button></form>{unavailable && <p role="alert">Sign in failed or the monitoring service is unavailable.</p>}</section></main>
}
