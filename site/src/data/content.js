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
    summary: 'Build a bounded, read-only view of the session.',
    question: 'What is happening?',
  },
  {
    slug: 'narrow',
    number: '02',
    title: 'Narrow',
    summary: 'Filter many possibilities into a qualified candidate.',
    question: 'Does it fit the strategy?',
  },
  {
    slug: 'authorize',
    number: '03',
    title: 'Authorize',
    summary: 'Apply risk gates and bind explicit operator intent.',
    question: 'May this specific plan proceed?',
  },
  {
    slug: 'protect',
    number: '04',
    title: 'Protect',
    summary: 'Manage lifecycle state around bounded exposure.',
    question: 'Is the position managed safely?',
  },
  {
    slug: 'prove',
    number: '05',
    title: 'Prove',
    summary: 'Reconcile outcomes and preserve evidence.',
    question: 'What can the evidence establish?',
  },
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
