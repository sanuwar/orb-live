const CONTROL_ROOT = 'http://127.0.0.1:8766/api/local-control'
const CSRF_TOKEN = 'local-control-v1'

async function request(path, method = 'GET') {
  try {
    const response = await fetch(`${CONTROL_ROOT}${path}`, {
      method,
      cache: 'no-store',
      credentials: 'omit',
      headers: method === 'POST' ? { 'X-ORB-CSRF': CSRF_TOKEN } : undefined,
    })
    const payload = await response.json()
    return { available: true, ok: response.ok, payload }
  } catch {
    return { available: false, ok: false, payload: { process_state: 'CONTROL_SERVICE_UNAVAILABLE', readiness: null } }
  }
}
export function readLocalControlStatus() { return request('/status') }
export function checkLocalReadiness() { return request('/readiness', 'POST') }
export function startLocalMorningWatch() { return request('/start', 'POST') }
export function stopLocalMorningWatch() { return request('/stop', 'POST') }
export { CONTROL_ROOT }