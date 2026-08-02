# Milestone 9 — Read-Only Broker-State Grounding

**Status: IN PROGRESS.** The offline foundation and the real read-only
observation runtime are both implemented. The attended checkpoint **has not
been executed**, and no broker state has been approved for active protection.

## Why active protection cannot be inferred from schema

Attended commissioning could rely on a human watching a single share. If a stop
was not really armed, the operator was there. Unattended operation removes that
backstop entirely, so the runtime must be able to assert *this order state means
the stop is resting at the venue and would fire* — and it cannot.

The advertised schema lists order states. It does not say which of them mean a
stop is working. `new`, `queued`, `unconfirmed`, and `confirmed` are all
plausible-sounding and materially different: a stop sitting in `unconfirmed` may
not be at the venue at all. Reading a state name tells you the broker's label,
not the venue's behaviour.

Three of these states were observed in completed attended runs
(`confirmed`, `unconfirmed`, plus terminal `filled` and `cancelled`). **That is
still not grounds for approval.** Seeing a label once, in one account, on one
day, under one set of conditions, does not establish what it guarantees.

## Current mapping: empty, deliberately

`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` in `src/live_order_state_model.py`
is an empty frozenset. Consequently `assess_protective_coverage` can never
return `VERIFIED_ACTIVE_PROTECTION`, and every working state assesses as
indeterminate.

`src/read_only_broker_state.py` derives its approved set from a provenance
registry rather than restating it, and a test asserts the two agree. Today both
are empty.

Attended commissioning is unaffected: it uses a separate
`assess_provisional_protection`, which is what the three completed live runs
recorded. **The completed one-share attended commissioning is not invalidated by
this gap.**

## Provenance framework

Every candidate state carries the honest source of what is known about it:

| Provenance | Meaning | Sufficient for protection? |
| --- | --- | --- |
| `SCHEMA_ONLY` | Appears in the advertised output schema | No |
| `FAKE_ONLY` | Only ever produced by a local fixture | No |
| `HISTORICAL_SANITIZED_EVIDENCE` | Observed in completed sanitized run evidence | No |
| `READ_ONLY_LIVE_OBSERVATION` | Seen by a reviewed read-only checkpoint | No |
| `APPROVED_FOR_ACTIVE_PROTECTION` | Reviewed and approved | **Yes** |

`can_protect` and `can_coexist_with_executable_sell` are recorded as `None`
while unknown. Leaving them unset is honest; guessing them is the failure this
module exists to prevent. A candidate cannot be constructed as approved unless
`can_protect` is true.

`proposed_state_promotion` deliberately **cannot** return
`APPROVED_FOR_ACTIVE_PROTECTION`. The most a single observation earns is
`READ_ONLY_LIVE_OBSERVATION`. Promotion to approved requires human review of
recorded observations — the act of observing is not the act of approving.

## Snapshot model

`ReadOnlyBrokerSnapshot` is a frozen record of one decision cycle. Every field
is a fixed enum, a bool, an integer count, or a monotonic offset. **There is no
field capable of carrying free-form broker text**, which a test asserts
structurally rather than by naming forbidden words.

It carries read availability per component (account, position, order, session),
cash-availability classification, position classification, market session,
related and executable order counts, protective-candidate count, protection
observation, read timestamps, and two contradiction flags.

It distinguishes known-safe, known-unsafe, unknown, stale, conflicting, and
unavailable. **Unknown is never treated as safe.**

## Fail-closed decision model

`decide_broker_state` is pure — no I/O, no session, no broker, no `await`,
asserted by parsing the function rather than reading its prose. It returns
exactly one of:

`VERIFIED_FLAT` · `VERIFIED_ACTIVE_PROTECTION` · `UNPROTECTED_POSITION` ·
`CONFLICTING_EXECUTABLE_ORDERS` · `STALE_BROKER_STATE` ·
`INCOMPLETE_BROKER_STATE` · `UNKNOWN_BROKER_STATE` · `BROKER_READ_UNAVAILABLE`

Only `VERIFIED_FLAT` and `VERIFIED_ACTIVE_PROTECTION` permit an automated caller
to proceed.

Evaluation order is itself a safety property: availability → completeness →
freshness → internal contradiction → interpretation. A conclusion drawn from an
unavailable, partial, stale, or self-contradictory snapshot is worse than no
conclusion.

Specific guarantees, each pinned by test:

- Only an explicitly approved state can yield `VERIFIED_ACTIVE_PROTECTION`. A
  snapshot claiming `OBSERVED_APPROVED_STATE` while nothing is approved yields
  `UNKNOWN_BROKER_STATE` — the claim is refused, not trusted.
- Flat **plus** an executable related sell is `CONFLICTING_EXECUTABLE_ORDERS`,
  never `VERIFIED_FLAT`.
- A held position with no approved protection is `UNPROTECTED_POSITION`, or
  stricter when the state is unapproved or unrecognized.
- Stale, incomplete, or contradictory inputs can never produce a safe result.

## Freshness rules

`SnapshotFreshnessPolicy` defaults to a maximum snapshot age of 5 seconds and a
maximum read span of 2 seconds. Both are validated: positive, at most 60
seconds, and span never exceeding age. A cycle that cannot gather account,
position, order, and session state within a couple of seconds is not observing
one coherent moment, and an unattended system acting on a smeared snapshot is
acting on a state that never existed.

Timestamps that do not move forward across the cycle yield `UNKNOWN`, not a
guess.

## Privacy rules

Snapshot facts are an allowlist of fixed keys and fixed values. Counts are
reported as `none` / `one` / `many` buckets — an exact tally of broker orders is
not decision-relevant and an exact count is one more thing to leak.
`validate_snapshot_facts` rejects any unknown key, any value outside its
allowlist, and any non-mapping input.

Never represented: account numbers, buying-power amounts, balances, order
identifiers, broker symbols, prices, tokens, headers, raw exception text, raw
responses, or request arguments.

## The later read-only observation checkpoint

Designed here, **not executed**. `ReadOnlyObservationPlan` encodes the
constraints in tracked code so they are reviewable now rather than argued about
next to a live terminal.

- Permitted tools: `get_accounts`, `get_equity_positions`, `get_equity_orders` —
  one call each.
- Order placements, cancellations, and replacements are fixed at **zero**, and
  the constructor raises if any is non-zero.
- Write tools are named in `READ_ONLY_CHECKPOINT_PROHIBITED_TOOLS` and rejected
  from the plan by construction.
- No strategy activation and no market-data subscription that could initiate
  trading.

It observes a protective stop **only if one already exists** from separate,
safe circumstances. If none exists it reports `NOT_OBSERVED` and stops.
`manufactures_observation=True` raises: **a live trade placed to generate an
observation would be a write, and this milestone performs none.**

## Zero-write guarantee

Asserted, not claimed:

- the module parses with no `await` and no call to any broker or session method;
- it imports no MCP, client, session, or adapter module;
- `decide_broker_state` contains no I/O call when inspected via its AST;
- the plan object refuses every write-shaped configuration.

## The real observation runtime

`src/read_only_broker_state.py` defines what a snapshot *means*.
`src/read_only_observation_runtime.py` is what actually produces one from a
real broker, and it is deliberately the narrowest thing that can.

### The capability surface is a partition, not a filter

All 49 advertised broker operations are classified in
`MCP_TOOL_CLASSIFICATION`. A tool **absent** from the mapping is refused, so a
newly advertised operation cannot be reached before it is reviewed.

| Capability | Count | May be invoked? |
| --- | --- | --- |
| `READ_ONLY_PERMITTED` | 3 | Yes |
| `UNNECESSARY` | 27 | No |
| `AMBIGUOUS_CAPABILITY` | 4 | No |
| `WRITE_CAPABLE` | 15 | No |

Ambiguity is grouped with proven writes on purpose. "We could not establish
that this is read-only" and "this writes" must produce identical behaviour,
because the cost of being wrong is identical.

`review_equity_order` is the notable case. Attended commissioning permits it;
this checkpoint does not. Its description says non-committing, but this
repository has never proven it leaves no broker-side artifact, and a checkpoint
whose entire claim is *zero writes* cannot rest on a description. The
divergence is deliberate and pinned by test: the write path accepts review
because it is about to write anyway.

### Two things are deliberately not read

- **Cash.** `get_accounts` carries no cash, balance, or buying-power field —
  its record is identity and status only. `get_portfolio` would carry one and
  is classified `UNNECESSARY`. So cash availability is reported `UNKNOWN`,
  honestly, rather than sourced by widening the surface for a value that
  grounding active protection does not need. `decide_broker_state` does not
  gate on it, and `UNKNOWN` is never treated as sufficient.
- **Market session.** Taken from a local exchange-calendar equivalent, not a
  broker or market-data read. A year outside the modelled table, an unmodelled
  early close, or a naive timestamp yields `UNKNOWN` — never "probably open".

This closes step 6 of the definition of done for position, order, and
market-session acquisition, and closes it *honestly* for buying power: the
snapshot reports that it does not know, instead of guessing or reaching
further than a read-only checkpoint should.

### Zero writes, asserted structurally

- The reader exposes exactly `read_accounts`, `read_positions`, `read_orders`
  — no placement, cancellation, replacement, strategy activation, or generic
  `call_tool` passthrough to implement or misuse.
- The module contains **exactly three** `call_tool` sites, each naming a
  module-level constant that resolves to a permitted read at parse time. There
  is no path where a caller-supplied or response-supplied string becomes a
  tool name. The repository-wide call-site guard sees all three as `literal`.
- The module imports nothing write-capable — no adapter, client, session, MCP,
  or orchestrator — so there is no write-capable object in reach to fall back
  to. Asserted by parsing the imports.
- Write-tool names appear in the file **only** as classification literals, and
  a test proves the textual count matches the denylist count exactly.
- Evidence carries `placement_writes`, `cancellation_writes`,
  `replacement_writes`, and `strategy_activations`, all required to be exactly
  zero, and refuses to persist a recorded call to a non-permitted tool.

### Failure is never relabelled

A *recognized* read failure degrades one component to `UNAVAILABLE`. A
capability violation and an unrecognized exception both escape instead, and
become `CAPABILITY_VIOLATION` or `INTERNAL_INVARIANT_FAILURE`. Neither may be
quietly reported as "the broker was unavailable" — that label reads as a
normal, expected condition, and these are not.

### Eleven outcomes, two of which are completions

`OBSERVATION_RECORDED` · `NO_PROTECTIVE_ORDER_OBSERVED` ·
`PROTECTION_AMBIGUOUS` · `ATTRIBUTION_AMBIGUOUS` · `BROKER_READ_UNAVAILABLE` ·
`SNAPSHOT_NOT_FRESH` · `DECLINED_BY_OPERATOR` · `AUTHORIZATION_REJECTED` ·
`PRECONDITION_REJECTED` · `CAPABILITY_VIOLATION` ·
`INTERNAL_INVARIANT_FAILURE`

Classification order is the same safety property as in the decision model:
availability → coherence → attribution → interpretation. Reading a protection
state out of an unavailable, stale, or unattributable snapshot would be worse
than reporting nothing, because it would look like a result.

Attribution is by exact normalized symbol. Nothing is inferred from ordering,
recency, quantity coincidence, or "the only order there is" — an
unattributable observation is reported ambiguous rather than assigned to the
symbol we happened to be asking about. An unrecognized order state counts as
*live*, not harmless.

### Neither completion approves anything

`OBSERVATION_RECORDED` records a state and proposes at most
`READ_ONLY_LIVE_OBSERVATION`. The runner raises rather than emit
`APPROVED_FOR_ACTIVE_PROTECTION`, and a test asserts that running a full clean
checkpoint against an existing `confirmed` stop leaves
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` empty. Recording an observation
is not approving it.

### The launcher

`read_only_observation_checkpoint_launcher.py` is **tracked**, not an
untracked day-of wrapper. A commissioning wrapper carries only a date and a
Run ID; this carries reviewed safety behaviour, so it belongs in review.

Execution requires all of: the
`--i-understand-this-performs-real-broker-reads` acknowledgment,
`ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION=1`, the authorization flag, an
unused Run ID, and two hidden `y/N` answers. Only exact `y` or `Y` approves —
empty, `yes`, `Y `, EOF, and an unreadable prompt all decline. Each stage may
be asked once; a second request is a rejection, not a re-prompt.

Both prompts are answered **before any read occurs**, pinned by a test that
asserts the reader was never called on decline. The session object never
leaves the launcher's session scope: the runtime receives only the restricted
reader. The session factory is imported inside the function so that importing
the launcher — which tests do — never reaches transport code.

## Definition of done for Milestone 9

Milestone 9 is complete only when **all** of the following hold:

1. ~~The offline foundation is reviewed and merged.~~ **Done** — PR #43.
2. A reviewed attended read-only checkpoint executes against a real account,
   performing only the permitted reads and zero writes. **Not done.** The
   runtime and launcher exist and are tested against fakes; **nothing has been
   executed against a real account.**
3. Its sanitized observations are recorded, including `NOT_OBSERVED` if no safe
   protective order existed. **Not done** — follows from step 2.
4. A human reviews those observations and explicitly approves zero or more
   states, promoting them to `APPROVED_FOR_ACTIVE_PROTECTION`. **Not done.**
5. `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` is updated to match, with the
   consistency test still passing. **Not done** — the set is still empty.
6. ~~Reliable position, order, buying-power, and market-session snapshot
   acquisition is wired to real reads.~~ **Done**, with buying power closed
   honestly as `UNKNOWN` rather than by widening the read surface — see above.

Steps 2 through 5 require an operator at a terminal and are **not** software
work. Approving zero states remains a legitimate outcome: if the observations
do not justify approval, the mapping stays empty and unattended operation
stays blocked on it.
