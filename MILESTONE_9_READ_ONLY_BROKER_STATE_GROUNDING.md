# Milestone 9 — Read-Only Broker-State Grounding

**Status: IN PROGRESS.** This is the offline foundation. The attended
read-only observation checkpoint is designed here but **not executed**, and no
broker state has been approved for active protection.

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

## Definition of done for Milestone 9

This offline PR delivers the framework. Milestone 9 is complete only when **all**
of the following hold:

1. This foundation is reviewed and merged. *(this PR)*
2. A reviewed attended read-only checkpoint executes against a real account,
   performing only the permitted reads and zero writes.
3. Its sanitized observations are recorded, including `NOT_OBSERVED` if no safe
   protective order existed.
4. A human reviews those observations and explicitly approves zero or more
   states, promoting them to `APPROVED_FOR_ACTIVE_PROTECTION`.
5. `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` is updated to match, with the
   consistency test still passing.
6. Reliable position, order, buying-power, and market-session snapshot
   acquisition is wired to real reads.

Steps 2 through 6 are **not** in this PR. Approving zero states remains a
legitimate outcome: if the observations do not justify approval, the mapping
stays empty and unattended operation stays blocked on it.
