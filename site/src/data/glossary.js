export const glossaryTerms = [
  {
    term: 'Opening range',
    definition:
      'The high and low established during a fixed period just after the market opens. ORB-LIVE uses the first five minutes.',
  },
  {
    term: 'Gap',
    definition:
      'The percentage difference between a new session’s opening price and the prior session’s close.',
  },
  {
    term: 'VWAP',
    definition:
      'Volume-weighted average price: an intraday reference that gives more influence to prices traded with greater volume.',
  },
  {
    term: 'R',
    definition:
      'A unit of planned trade risk. If entry-to-stop distance is one unit, a 2R target is twice that distance.',
  },
  {
    term: 'Saga',
    definition:
      'A durable workflow that records a multi-step broker action, its dependencies, attempts, and uncertain outcomes.',
  },
  {
    term: 'Reconciliation',
    definition:
      'Comparing the system’s durable expectations with a fresh external observation and flagging differences.',
  },
  {
    term: 'Idempotency',
    definition:
      'A design property that lets the same intent be handled repeatedly without creating duplicate effects.',
  },
  {
    term: 'ref_id',
    definition:
      'A locally generated reference that binds one action to one approved payload. It is not treated as proof of broker lookup support.',
  },
  {
    term: 'Fail closed',
    definition:
      'Stop or reject when required evidence is missing, stale, conflicting, or unknown instead of assuming a safe result.',
  },
  {
    term: 'Shadow mode',
    definition:
      'A mode that evaluates hypothetical strategy decisions without authorizing or placing trades.',
  },
  {
    term: 'Commissioned identity',
    definition:
      'The account context explicitly approved for an attended checkpoint and verified without exposing identifying details.',
  },
  {
    term: 'Protective stop',
    definition:
      'An exit order intended to limit downside after an entry fills. Its actual active status and covered quantity must be verified.',
  },
  {
    term: 'Partial fill',
    definition:
      'An order outcome where only part of the requested quantity has traded, leaving quantity and protection questions to resolve.',
  },
  {
    term: 'Operator resolution',
    definition:
      'A durable, evidence-based human decision used when automation cannot safely classify an outcome.',
  },
]
