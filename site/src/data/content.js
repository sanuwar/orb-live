export const navigation = [
  { slug: 'overview', label: 'Overview', shortLabel: '01' },
  { slug: 'strategy', label: 'Strategy', shortLabel: '02' },
  { slug: 'system-flow', label: 'System Flow', shortLabel: '03' },
  { slug: 'safety', label: 'Safety', shortLabel: '04' },
  {
    slug: 'build-journey',
    label: 'Build Journey',
    shortLabel: '05',
  },
  {
    slug: 'architecture-glossary',
    label: 'Architecture & Glossary',
    shortLabel: '06',
  },
  { slug: 'journal', label: 'Trade Journal', shortLabel: '07' },
  { slug: 'journal-records', label: 'Trades & Events', shortLabel: '08' },
  { slug: 'manual-record', label: 'Add Manual Record', shortLabel: '09' },
  { slug: 'evidence-import', label: 'Import Evidence', shortLabel: '10' },
]

export const journeyStages = [
  {
    slug: 'observe',
    number: '01',
    title: 'Observe',
    question: 'What is happening?',
    metrics: [
      { value: '15', label: 'symbols' },
      { value: '5m', label: 'opening range' },
      { value: '20d', label: 'volume baseline' },
    ],
  },
  {
    slug: 'narrow',
    number: '02',
    title: 'Narrow',
    question: 'Does it qualify?',
    metrics: [
      { value: '≥1%', label: 'gap' },
      { value: '≥3×', label: 'relative volume' },
      { value: '80m', label: 'entry window' },
    ],
  },
  {
    slug: 'authorize',
    number: '03',
    title: 'Authorize',
    question: 'Is it within limits?',
    metrics: [
      { value: '$15', label: 'planned risk' },
      { value: '$3k', label: 'position cap' },
      { value: '1', label: 'open position' },
    ],
  },
  {
    slug: 'protect',
    number: '04',
    title: 'Protect',
    question: 'How is exposure bounded?',
    metrics: [
      { value: '1R', label: 'breakeven' },
      { value: '2R', label: 'target' },
      { value: '15:45', label: 'time exit' },
    ],
  },
  {
    slug: 'prove',
    number: '05',
    title: 'Prove',
    question: 'What is established?',
    metrics: [
      { value: '0', label: 'verified stop mappings' },
      { value: '0', label: 'dashboard broker calls' },
      { value: '1,609', label: 'tests at dashboard creation' },
    ],
  },
]

export const strategyMeasurements = [
  { stage: 'Observe', value: '15', unit: 'symbols', measure: 'Fixed universe', capture: 'Configuration before the session', operation: 'Ignore every symbol outside the basket' },
  { stage: 'Observe', value: '5', unit: 'minutes', measure: 'Opening range', capture: 'High and low from 09:30–09:35 ET', operation: 'Create the breakout and stop references' },
  { stage: 'Observe', value: '20', unit: 'sessions', measure: 'Volume baseline', capture: 'Historical relative-volume lookback', operation: 'Establish expected participation' },
  { stage: 'Narrow', value: '≥1.0', unit: '% gap', measure: 'Gap threshold', capture: 'Opening price versus prior close', operation: 'Reject smaller opening moves' },
  { stage: 'Narrow', value: '≥3.0', unit: '× volume', measure: 'Participation threshold', capture: 'Current activity versus 20-session baseline', operation: 'Reject low-participation breakouts' },
  { stage: 'Narrow', value: 'SPY > VWAP', unit: 'pass / fail', measure: 'Market context', capture: 'Current SPY price versus intraday VWAP', operation: 'Reject a long setup when the condition is false' },
  { stage: 'Narrow', value: '1', unit: 'minute close', measure: 'Breakout confirmation', capture: 'Completed bar above the opening-range high', operation: 'Reject an unconfirmed price excursion' },
  { stage: 'Narrow', value: '09:40–11:00', unit: 'ET', measure: 'Entry window', capture: 'Timestamp of the confirmed candidate', operation: 'Reject candidates outside the 80-minute window' },
]

export const safetyLimits = [
  { value: 'OFF', label: 'strategy execution', group: 'Default state' },
  { value: '15s', label: 'authorization lifetime', group: 'Authority' },
  { value: '$15', label: 'planned trade risk', group: 'Trade limits' },
  { value: '$3,000', label: 'position value cap', group: 'Trade limits' },
  { value: '1', label: 'open position', group: 'Exposure limits' },
  { value: '2', label: 'entries per day', group: 'Exposure limits' },
  { value: '$45', label: 'daily loss stop', group: 'Loss limits' },
  { value: '$120', label: 'weekly loss stop', group: 'Loss limits' },
  { value: '$300', label: 'drawdown halt', group: 'Loss limits' },
  { value: '0', label: 'margin use', group: 'Prohibited' },
  { value: '0', label: 'short sales', group: 'Prohibited' },
  { value: '0', label: 'average-down actions', group: 'Prohibited' },
  { value: '0', label: 'blind retries after uncertainty', group: 'Prohibited' },
  { value: '0', label: 'verified active-stop mappings', group: 'Current proof' },
]

export const riskConstraints = [
  { value: '$15', label: 'Planned risk per trade' },
  { value: '$3,000', label: 'Maximum position size' },
  { value: '1', label: 'Maximum open position' },
  { value: '0', label: 'Margin, shorts, averaging down' },
]

export const strategySteps = [
  {
    title: 'Fixed watchlist',
    summary: 'Start from a small, predetermined set of liquid companies.',
    detail:
      'The configured universe is fixed before the session. The system does not roam the market looking for arbitrary symbols.',
  },
  {
    title: 'Gap-up qualification',
    summary: 'Look for a meaningful move above the prior close.',
    detail:
      'A candidate must meet the configured minimum opening gap. This narrows attention before breakout evaluation begins.',
  },
  {
    title: 'Five-minute opening range',
    summary: 'Let the first five minutes establish a high and a low.',
    detail:
      'The opening-range boundaries become the reference for entry and initial protection. The system does not react to the first print.',
  },
  {
    title: 'Volume confirmation',
    summary: 'Require unusually strong participation.',
    detail:
      'Relative volume compares current activity with a configured historical lookback so a quiet move does not qualify by price alone.',
  },
  {
    title: 'Market context',
    summary: 'Require the broad market to support the setup.',
    detail:
      'The current design checks whether SPY is above VWAP before accepting a long candidate.',
  },
  {
    title: 'One-minute breakout',
    summary: 'Wait for a completed one-minute confirmation.',
    detail:
      'A candidate is evaluated after a one-minute bar confirms the breakout during the permitted entry window.',
  },
]

export const flowStages = [
  {
    group: 'observe',
    title: 'Market observation',
    description: 'Collect a bounded, read-only view of the session.',
    kind: 'observe',
  },
  {
    group: 'narrow',
    title: 'Eligibility filters',
    description: 'Apply watchlist, gap, volume, and market-context rules.',
    kind: 'observe',
  },
  {
    group: 'observe',
    title: 'Opening-range calculation',
    description: 'Derive the first five-minute high and low.',
    kind: 'hypothetical',
  },
  {
    group: 'narrow',
    title: 'Entry-candidate evaluation',
    description: 'Evaluate a confirmed breakout in shadow logic.',
    kind: 'hypothetical',
  },
  {
    group: 'authorize',
    title: 'Risk and safety gates',
    description: 'Reject anything outside configured boundaries.',
    kind: 'reviewed',
  },
  {
    group: 'authorize',
    title: 'Operator authorization',
    description: 'Bind approved intent to a short-lived envelope.',
    kind: 'reviewed',
  },
  {
    group: 'protect',
    title: 'Broker action review',
    description: 'Review is evidence for a proposed actionâ€”not an order.',
    kind: 'authorized',
  },
  {
    group: 'protect',
    title: 'Execution lifecycle',
    description: 'Durable saga states model outcomes and uncertainty.',
    kind: 'authorized',
  },
  {
    group: 'prove',
    title: 'Reconciliation',
    description: 'Compare durable expectations with broker observations.',
    kind: 'reviewed',
  },
  {
    group: 'prove',
    title: 'Evidence and operator resolution',
    description: 'Escalate ambiguous outcomes instead of guessing.',
    kind: 'reviewed',
  },
]

export const safetyLayers = [
  {
    title: 'Strategy disabled by default',
    detail: 'Persistent configuration keeps strategy execution off.',
  },
  {
    title: 'Fixed risk configuration',
    detail: 'Position, loss, and drawdown limits are predetermined.',
  },
  {
    title: 'Commissioned identity verification',
    detail: 'A mismatched or unverified account context fails closed.',
  },
  {
    title: 'Short-lived authorization',
    detail: 'Approved intent expires rather than becoming standing permission.',
  },
  {
    title: 'Review before placement',
    detail: 'The proposed action must be reviewed before any later write.',
  },
  {
    title: 'Same-reference payload integrity',
    detail: 'A stable reference cannot silently change its approved payload.',
  },
  {
    title: 'Saga persistence and recovery',
    detail: 'Intent and transitions are durable before the next action.',
  },
  {
    title: 'Broker reconciliation',
    detail: 'Observed state is compared with durable expectations.',
  },
  {
    title: 'Operator resolution',
    detail: 'Fresh evidence is required to resolve ambiguous outcomes.',
  },
  {
    title: 'Fail-closed behavior',
    detail: 'Missing, stale, conflicting, or unknown evidence stops progress.',
  },
]

export const buildJourneyPhases = [
  {
    title: 'Foundation', status: 'COMPLETE',
    milestones: [
      { id: 'M13', title: 'Protection-state infrastructure', description: 'Added the evidence, ledger, and assessment foundation needed to ground protection from eligible live evidence.', validation: 'Accepted infrastructure; eligible live evidence remains required.' },
      { id: 'M14', title: 'Safety-governed broker control', description: 'Bound broker execution integration to attended safety, commissioned identity, and fail-closed suspension controls.', validation: 'Attended commissioning boundary established.' },
    ],
  },
  {
    title: 'Real market observation', status: 'COMPLETE',
    milestones: [
      { id: 'M15', title: 'Production market data and live shadow', description: 'Introduced the production market-data boundary and non-writing live-shadow evidence.', validation: 'Strategy decisions remain observational.' },
      { id: 'M16', title: 'Bounded multi-symbol shadow and simulation bridge', description: 'Bounded the watchlist, simulated execution consequences, and connected candidate review inputs without an order path.', validation: 'Commissioning universe fixed at 10 symbols.' },
    ],
  },
  {
    title: 'Safety and broker control', status: 'COMPLETE',
    milestones: [
      { id: 'M17', title: 'Supervised preflight, safety bridge, and handoff', description: 'Built the reviewed preflight and attended-handoff chain for a one-share supervised lifecycle.', validation: 'Explicit acknowledgements and fail-closed review required.' },
      { id: 'M18', title: 'Supervised and production read-only rehearsal', description: 'Added rehearsals that validate readiness and stop before broker mutation.', validation: 'Read-only boundary preserved.' },
      { id: 'M19', title: 'Morning watcher and protection/exit rehearsal', description: 'Added the canonical 09:40–11:00 ET watcher and deterministic window-end termination.', validation: 'No-candidate sessions stop automatically at window end.' },
      { id: 'M20', title: 'Authoritative protection grounding', description: 'Added authoritative observation and assessment plumbing for protection-state evidence.', validation: 'No active protection state is promoted without evidence.' },
    ],
  },
  {
    title: 'Unattended architecture', status: 'COMPLETE',
    milestones: [
      { id: 'M21', title: 'Bounded unattended orchestrator', description: 'Defined a bounded, fail-closed orchestrator while keeping activation inert.', validation: 'No automated action permitted.' },
      { id: 'M22', title: 'Durable bounded pilot runtime and state', description: 'Added durable pilot runtime state and restart controls behind inactive policy.', validation: 'Unattended runtime remains disabled.' },
      { id: 'M23', title: 'Activation evidence and first-live path', description: 'Added activation-readiness evidence and the attended first-live commissioning runbook.', validation: 'Activation evidence is not activation.' },
    ],
  },
  {
    title: 'Live commissioning', status: 'CURRENT',
    milestones: [
      { id: 'M24', title: 'Candidate-to-handoff commissioning CLI', description: 'Made the authentic live-shadow candidate-to-sanitized-handoff chain executable before the read-only rehearsal.', validation: 'Stops at production read-only rehearsal; it does not authorize a write.' },
      { id: 'Operator flow', title: 'Pre-market readiness and daily flow', description: 'Added local-only readiness checks and documented the canonical morning-to-handoff operator path.', validation: 'No broker or market-data connectivity required.' },
    ],
  },
]
export const architectureLayers = [
  {
    title: 'Strategy and signals',
    detail: 'Opening-range rules turn observations into hypothetical candidates.',
  },
  {
    title: 'Risk and authorization',
    detail: 'Limits, safety gates, and approved intent constrain what could proceed.',
  },
  {
    title: 'Lifecycle and saga state',
    detail: 'Durable records model every action, dependency, attempt, and uncertainty.',
  },
  {
    title: 'Broker boundary',
    detail: 'Capabilities are explicit; unsupported behavior remains unavailable.',
  },
  {
    title: 'Reconciliation and recovery',
    detail: 'Fresh observations are compared against persistent expectations.',
  },
  {
    title: 'Evidence and operator oversight',
    detail: 'Ambiguity is recorded and handed to a human with bounded evidence.',
  },
  {
    title: 'Documentation dashboard',
    detail: 'This static frontend explains the system without touching its runtime.',
  },
]
