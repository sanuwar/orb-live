export const navigation = [
  { slug: 'overview', label: 'Overview', shortLabel: '01' },
  { slug: 'strategy', label: 'Strategy', shortLabel: '02' },
  { slug: 'system-flow', label: 'System Flow', shortLabel: '03' },
  { slug: 'safety', label: 'Safety', shortLabel: '04' },
  {
    slug: 'development-journey',
    label: 'Development Journey',
    shortLabel: '05',
  },
  {
    slug: 'architecture-glossary',
    label: 'Architecture & Glossary',
    shortLabel: '06',
  },
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

export const milestones = [
  {
    title: 'Strategy specification and signal engine',
    status: 'Completed',
    detail: 'Codified opening-range inputs, filters, and exit intent.',
  },
  {
    title: 'Persistent trade state',
    status: 'Tested',
    detail: 'Added durable lifecycle state, migrations, and event history.',
  },
  {
    title: 'Broker action sagas',
    status: 'Tested',
    detail: 'Modeled claims, attempts, dependencies, uncertainty, and recovery.',
  },
  {
    title: 'Fake transport and simulation',
    status: 'Tested',
    detail: 'Exercised broker-like outcomes without a real broker connection.',
  },
  {
    title: 'Lifecycle orchestration',
    status: 'Tested',
    detail: 'Coordinated entry, protection, and exit state transitions offline.',
  },
  {
    title: 'Simulation and soak testing',
    status: 'Tested',
    detail: 'Repeated deterministic scenarios to expose restart and replay defects.',
  },
  {
    title: 'Read-only broker reconciliation',
    status: 'Prepared',
    detail: 'Built fail-closed comparison of expected and observed state.',
  },
  {
    title: 'Gateway hardening',
    status: 'Completed',
    detail: 'Constrained capability surfaces and protected composition boundaries.',
  },
  {
    title: 'Live-market shadow mode',
    status: 'Prepared',
    detail: 'Separated hypothetical decisions from any executable path.',
  },
  {
    title: 'Attended identity and read-only checks',
    status: 'Completed',
    detail: 'Commissioned bounded observation checkpoints under operator control.',
  },
  {
    title: 'Schema-grounded response parsing',
    status: 'Tested',
    detail: 'Classified advertised lifecycle responses without inventing semantics.',
  },
  {
    title: 'Initialization-only live adapter',
    status: 'Awaiting attended validation',
    detail: 'Offline implementation exists but is not bound to execution.',
  },
  {
    title: 'Current commissioning blockers',
    status: 'Blocked',
    detail: 'Checkpoint 7C and active protective-stop verification remain unresolved.',
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
