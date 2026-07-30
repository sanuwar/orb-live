export const SOURCES = ['AGENT', 'MANUAL', 'MANUAL_RECOVERY']
export const ENVIRONMENTS = ['LIVE', 'FAKE', 'MANUAL']
export const SIDES = ['BUY', 'SELL']
export const STAGES = [
  'RUN_STARTED', 'ENTRY_REVIEWED', 'ENTRY_AUTHORIZED', 'ENTRY_SUBMITTED',
  'ENTRY_RECONCILED', 'PROTECTIVE_STOP_REVIEWED', 'PROTECTIVE_STOP_SUBMITTED',
  'PROTECTIVE_STOP_RECONCILED', 'STOP_CANCELLATION_REQUESTED',
  'STOP_CANCELLATION_PENDING', 'STOP_CANCELLATION_CONFIRMED', 'STOP_FILLED',
  'EXIT_REVIEWED', 'EXIT_SUBMITTED', 'EXIT_RECONCILED', 'OPERATOR_REQUIRED',
  'MANUAL_RECOVERY', 'VERIFIED_FLAT', 'COMPLETE', 'PARTIAL', 'FAILED',
]
const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d+)?$/
const SYMBOL = /^[A-Z][A-Z0-9.-]{0,11}$/
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const MARKUP = /<[^>]*>/g
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/g
const DANGEROUS = /<\s*\/?\s*(?:script|iframe|object|embed|style|link|meta)\b/i

export class JournalValidationError extends Error {
  constructor(code, fields = {}) {
    super(code)
    this.name = 'JournalValidationError'
    this.code = code
    this.fields = Object.freeze({ ...fields })
  }
}

export function safeText(value, field, max = 256, required = false) {
  const candidate = String(value ?? '').replace(CONTROL, '').trim()
  if (required && !candidate) throw new JournalValidationError('VALIDATION_FAILED', { [field]: 'Required' })
  if (candidate.length > max) throw new JournalValidationError('VALIDATION_FAILED', { [field]: `Maximum ${max} characters` })
  if (DANGEROUS.test(candidate)) throw new JournalValidationError('UNSAFE_TEXT', { [field]: 'Unsafe markup' })
  return candidate.replace(MARKUP, '')
}

export function decimalText(value, field, { required = false, positive = false, signed = false } = {}) {
  const candidate = String(value ?? '').trim()
  if (!candidate && !required) return null
  const unsigned = signed && candidate.startsWith('-') ? candidate.slice(1) : candidate
  if (!DECIMAL.test(unsigned)) throw new JournalValidationError('VALIDATION_FAILED', { [field]: 'Use a decimal value' })
  const [whole, fraction = ''] = unsigned.split('.')
  const cleanWhole = whole.replace(/^0+(?=\d)/, '') || '0'
  const cleanFraction = fraction.replace(/0+$/, '')
  const normalized = `${candidate.startsWith('-') ? '-' : ''}${cleanWhole}${cleanFraction ? `.${cleanFraction}` : ''}`
  if (positive && /^0(?:\.0+)?$/.test(unsigned)) throw new JournalValidationError('VALIDATION_FAILED', { [field]: 'Must be greater than zero' })
  return normalized
}

export function safeId(value, field, required = false) {
  if (value == null || value === '') {
    if (required) throw new JournalValidationError('VALIDATION_FAILED', { [field]: 'Required' })
    return null
  }
  const result = safeText(value, field, 128, required)
  if (!SAFE_ID.test(result)) throw new JournalValidationError('VALIDATION_FAILED', { [field]: 'Invalid identifier' })
  return result
}

export function enumValue(value, choices, field) {
  const result = String(value ?? '').toUpperCase()
  if (!choices.includes(result)) throw new JournalValidationError('VALIDATION_FAILED', { [field]: 'Unsupported value' })
  return result
}

export function timestamp(value, field) {
  const parsed = Date.parse(String(value ?? ''))
  if (Number.isNaN(parsed)) throw new JournalValidationError('VALIDATION_FAILED', { [field]: 'Use a valid date and time' })
  return new Date(parsed).toISOString()
}

function recordId(prefix, key) {
  let hash = 2166136261
  for (const character of key) {
    hash ^= character.charCodeAt(0)
    hash = Math.imul(hash, 16777619)
  }
  return `${prefix}-${(hash >>> 0).toString(16).padStart(8, '0')}`
}

export function createManualRecord(input, options = {}) {
  const type = enumValue(input.recordType, ['MANUAL_TRADE', 'MANUAL_RECOVERY'], 'recordType')
  const submissionKey = safeId(options.submissionKey, 'submissionKey', true)
  const status = safeText(input.status, 'status', 64, true).toUpperCase()
  const eventTimestamp = timestamp(input.eventTimestamp, 'eventTimestamp')
  const symbol = safeText(input.symbol, 'symbol', 12, true).toUpperCase()
  if (!SYMBOL.test(symbol)) throw new JournalValidationError('VALIDATION_FAILED', { symbol: 'Invalid symbol' })
  return Object.freeze({
    journalRecordId: recordId('manual', submissionKey),
    source: type === 'MANUAL_RECOVERY' ? 'MANUAL_RECOVERY' : 'MANUAL',
    environment: 'MANUAL', runId: safeId(input.runId, 'runId'), symbol,
    side: enumValue(input.side, SIDES, 'side'),
    quantity: decimalText(input.quantity, 'quantity', { required: true, positive: true }),
    status, commissioningStage: status === 'VERIFIED_FLAT' ? 'VERIFIED_FLAT' : type === 'MANUAL_RECOVERY' ? 'MANUAL_RECOVERY' : 'COMPLETE',
    reason: type, eventTimestamp,
    createdTimestamp: options.createdTimestamp ?? new Date().toISOString(),
    executionPrice: decimalText(input.executionPrice, 'executionPrice'), plannedRisk: null,
    realizedProfitLoss: null, operatorNote: safeText(input.note, 'note', 500) || null,
    relatedRecordId: safeId(input.relatedRecordId, 'relatedRecordId'), evidenceSha256: null,
    sanitizedImportKey: null, automaticallyImported: false,
    operatorInterventionRequired: type === 'MANUAL_RECOVERY', initiator: 'OPERATOR', submissionKey,
  })
}

export function createAgentRecord(event, context) {
  const eventKey = safeId(event.eventKey, 'eventKey', true)
  const stage = enumValue(event.stage, STAGES, 'stage')
  const stableKey = `${context.evidenceSha256}:${eventKey}`
  const symbol = event.symbol ? safeText(event.symbol, 'symbol', 12).toUpperCase() : null
  if (symbol && !SYMBOL.test(symbol)) throw new JournalValidationError('VALIDATION_FAILED', { symbol: 'Invalid symbol' })
  const initiator = ['AGENT', 'OPERATOR'].includes(String(event.initiator).toUpperCase()) ? String(event.initiator).toUpperCase() : 'UNPROVEN'
  return Object.freeze({
    journalRecordId: recordId('agent', stableKey), source: 'AGENT',
    environment: enumValue(context.environment, ['LIVE', 'FAKE'], 'environment'),
    runId: safeId(context.runId, 'runId', true), symbol,
    side: event.side ? enumValue(event.side, SIDES, 'side') : null,
    quantity: decimalText(event.quantity, 'quantity'),
    status: safeText(event.status ?? stage, 'status', 64, true).toUpperCase(),
    commissioningStage: stage, reason: safeText(event.reason, 'reason', 160) || null,
    eventTimestamp: timestamp(event.eventTimestamp, 'eventTimestamp'),
    createdTimestamp: context.createdTimestamp,
    executionPrice: decimalText(event.executionPrice, 'executionPrice'),
    plannedRisk: decimalText(event.plannedRisk, 'plannedRisk'),
    realizedProfitLoss: decimalText(event.realizedProfitLoss, 'realizedProfitLoss', { signed: true }),
    operatorNote: null, relatedRecordId: safeId(event.relatedRecordId, 'relatedRecordId'),
    evidenceSha256: context.evidenceSha256, sanitizedImportKey: stableKey,
    automaticallyImported: true,
    operatorInterventionRequired: Boolean(event.operatorInterventionRequired || stage === 'OPERATOR_REQUIRED'),
    initiator, submissionKey: null,
  })
}

export function chronological(records) {
  return [...records].sort((a, b) => a.eventTimestamp.localeCompare(b.eventTimestamp) || a.journalRecordId.localeCompare(b.journalRecordId))
}

export function displayValue(value) {
  return value == null || value === '' ? 'Not available' : value
}




export function sumDecimalStrings(values) {
  if (!values.length) return null
  const parsed = values.map((value) => {
    const normalized = decimalText(value, 'value', { required: true, signed: true })
    const negative = normalized.startsWith('-')
    const unsigned = negative ? normalized.slice(1) : normalized
    const [whole, fraction = ''] = unsigned.split('.')
    return { negative, digits: BigInt(`${whole}${fraction}`), scale: fraction.length }
  })
  const scale = Math.max(...parsed.map((item) => item.scale))
  const total = parsed.reduce((sum, item) => {
    const magnitude = item.digits * (10n ** BigInt(scale - item.scale))
    return sum + (item.negative ? -magnitude : magnitude)
  }, 0n)
  const negative = total < 0n
  const digits = (negative ? -total : total).toString().padStart(scale + 1, '0')
  const whole = scale ? digits.slice(0, -scale) : digits
  const fraction = scale ? digits.slice(-scale).replace(/0+$/, '') : ''
  return `${negative ? '-' : ''}${whole}${fraction ? `.${fraction}` : ''}`
}
