const KEYS = new Set([
  'schema_version', 'release_commit_sha', 'implementation_digest', 'generated_as_of',
  'operating_mode', 'scheduler_status', 'runner_status', 'suspension_status',
  'heartbeat_status', 'alert_status', 'acknowledgment_status', 'recovery_status',
  'operational_status', 'supervised_readiness_status', 'blocker_reasons',
  'recommended_operator_action', 'strategy_enabled', 'activation_approvals_present',
  'approved_protection_state_count', 'protection_grounded', 'activation_approved',
  'permits_automated_action', 'live_ready', 'unattended_ready', 'evidence_completeness',
  'evidence_freshness', 'source_component_statuses',
])
const COMPONENTS = new Set(['scheduler', 'runner', 'suspension', 'heartbeat', 'alerts', 'acknowledgment', 'operational_status', 'readiness'])
const PRIVATE_KEY = /account|balance|credential|exception|order|password|price|quantity|secret|symbol|token|username|url|ip|path/i
const PRIVATE_VALUE = /:\/\/|c:\\|\/users\/|\\users\\|onedrive|exception:/i
function exactKeys(value, allowed) { return Object.keys(value).length === allowed.size && Object.keys(value).every((key) => allowed.has(key)) }
function privateValue(value) { if (typeof value === 'string') return PRIVATE_VALUE.test(value); if (Array.isArray(value)) return value.some(privateValue); return value && typeof value === 'object' && Object.entries(value).some(([key, item]) => PRIVATE_KEY.test(key) || privateValue(item)) }
function nonEmptyString(value) { return typeof value === 'string' && value.length > 0 }
export function parseOperatorSnapshot(text) {
  let value
  try { value = JSON.parse(text) } catch { return { status: 'rejected', code: 'MALFORMED_SNAPSHOT' } }
  if (!value || typeof value !== 'object' || Array.isArray(value) || privateValue(value)) return { status: 'rejected', code: privateValue(value) ? 'PRIVATE_FIELD_REJECTED' : 'MALFORMED_SNAPSHOT' }
  if (!exactKeys(value, KEYS) || value.schema_version !== 1 || !Array.isArray(value.blocker_reasons) || !exactKeys(value.source_component_statuses, COMPONENTS)) return { status: 'rejected', code: 'UNSUPPORTED_SNAPSHOT' }
  if (!value.blocker_reasons.every(nonEmptyString) || !Object.values(value.source_component_statuses).every(nonEmptyString) || !['release_commit_sha', 'implementation_digest', 'generated_as_of', 'operating_mode', 'scheduler_status', 'runner_status', 'suspension_status', 'heartbeat_status', 'alert_status', 'acknowledgment_status', 'recovery_status', 'operational_status', 'supervised_readiness_status', 'recommended_operator_action', 'evidence_completeness', 'evidence_freshness'].every((key) => nonEmptyString(value[key]))) return { status: 'rejected', code: 'MALFORMED_SNAPSHOT' }
  if (!/^[a-f0-9]{40}$/.test(value.release_commit_sha) || !/^[a-f0-9]{64}$/.test(value.implementation_digest) || Number.isNaN(Date.parse(value.generated_as_of)) || value.approved_protection_state_count !== 0 || ['strategy_enabled', 'activation_approvals_present', 'protection_grounded', 'activation_approved', 'permits_automated_action', 'live_ready', 'unattended_ready'].some((key) => value[key] !== false)) return { status: 'rejected', code: 'FAIL_CLOSED_SAFETY_BOUNDARY' }
  return { status: 'loaded', snapshot: value }
}