const SESSION_ENDPOINT = '/api/remote/session'
const PROJECTION_ENDPOINT = '/api/remote/projection'
const LOGIN_ENDPOINT = '/api/auth/login'
const LOGOUT_ENDPOINT = '/api/auth/logout'

async function readJson(response) {
  try { return await response.json() } catch { return { status: 'REMOTE_RESPONSE_INVALID' } }
}

async function request(path, options = {}) {
  try {
    const response = await fetch(path, { cache: 'no-store', credentials: 'same-origin', ...options })
    return { available: true, ok: response.ok, status: response.status, payload: await readJson(response) }
  } catch {
    return { available: false, ok: false, status: 0, payload: { status: 'CONNECTION_UNAVAILABLE' } }
  }
}

export function readRemoteSession() { return request(SESSION_ENDPOINT) }
export function readRemoteProjection() { return request(PROJECTION_ENDPOINT) }
export function loginRemote(password) {
  return request(LOGIN_ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) })
}
export function logoutRemote(csrfToken) {
  return request(LOGOUT_ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ORB-CSRF': csrfToken }, body: '{}' })
}

export { LOGIN_ENDPOINT, LOGOUT_ENDPOINT, PROJECTION_ENDPOINT, SESSION_ENDPOINT }
