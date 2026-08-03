# F2 — Intra-acquisition consistency: design, and why it is not implemented yet

Resolves the design half of [issue #45](https://github.com/sanuwar/orb-live/issues/45).
**No code changes accompany this document.** It exists so the decision is
reviewable now rather than argued about inside a later pull request.

## The defect, precisely

`acquire_read_only_snapshot` reads each permitted tool **exactly once**. There
is no second observation to compare against, so a position or order-set change
occurring *between* the reads is invisible. Two snapshot fields are populated
from proxies rather than from what their names describe:

| Field | Name implies | Actually populated from |
| --- | --- | --- |
| `order_state_changed_during_acquisition` | an order state changed mid-cycle | the **market session** changed mid-cycle |
| `position_observations_disagreed` | two position observations conflicted | a **duplicate symbol within one response** |

Market-session change *is* genuinely detected: `classify_market_session` is
sampled before and after the reads. The order and position halves are not.

## Why this is safe today and unsafe later

Both flags are conservative in one direction only. Each can push a decision
toward `UNKNOWN_BROKER_STATE` and neither can push it toward safety:

```python
# src/read_only_broker_state.py — decide_broker_state, step 4
if (
    snapshot.order_state_changed_during_acquisition
    or snapshot.position_observations_disagreed
    or snapshot.position is PositionClassification.CONTRADICTORY
    or snapshot.market_session is MarketSession.CHANGED_DURING_ACQUISITION
):
    return result(BrokerStateDecision.UNKNOWN_BROKER_STATE)
```

A missed change therefore cannot manufacture `VERIFIED_FLAT` or
`VERIFIED_ACTIVE_PROTECTION`. It can only fail to *escalate* a snapshot that
the ordinary interpretation rules would then classify normally.

For an attended, single-cycle, zero-write observation with an operator
watching, that is acceptable. The current mitigation is the **2-second
acquisition-span cap** (`SnapshotFreshnessPolicy.maximum_span_seconds`): a
cycle whose reads spread wider is rejected as `SPAN_TOO_WIDE` and never yields
a safe decision. That **bounds the window** in which an undetected change could
occur. It does not detect one.

It stops being acceptable the moment an unattended runtime acts on *"the stop
was armed when I looked"* with nobody watching. At that point a two-second
window is a bound on exposure, not a proof of consistency.

## Why implementation is deferred rather than done now

Not scheduling convenience — three specific couplings, all of which force
decisions that a one-line fix would make silently.

### 1. It re-pins the held checkpoint

`src/read_only_observation_runtime.py` is one of five files in
`PINNED_IMPLEMENTATION_FILES`. Changing it changes
`checkpoint_implementation_digest`, invalidating the pin recorded for the held
real-account checkpoint. That pin should move **once**, deliberately, carrying
every pinned-file change together — not once per improvement.

### 2. It contradicts the one-call ceiling

A second orders read violates `maximum_calls_per_tool=1`, which is enforced in
three places that currently agree with each other:

- `RestrictedReadOnlyReader.maximum_calls_per_tool`
- `ReadOnlyObservationPlan.maximum_calls_per_tool`
- the manifest's `allowed_broker_operations` contract, reviewed on the basis
  that the checkpoint performs exactly three calls

Raising the ceiling to 2 is defensible, but it is a **capability change**. It
widens what an approved manifest authorizes, and it must be reviewed as such
rather than arriving as a side effect of a bug fix.

### 3. Re-reading fights the freshness policy

The obvious fix — re-read and compare — widens the read span, which is exactly
what `maximum_span_seconds` exists to prevent. Detecting change and observing
one coherent moment pull in opposite directions. That is a real trade-off, not
an oversight, and it needs an explicit decision rather than whichever behaviour
falls out of the implementation.

## Proposed design

### Shape

Add a bounded **confirmation read** of orders only, at the end of the cycle:

```
t0  ── monotonic()
    ── get_accounts
    ── get_equity_positions
    ── get_equity_orders          (observation A)
    ── get_equity_orders          (observation B, confirmation)
t1  ── monotonic()
    ── classify_market_session    (already sampled at t0 and t1)
```

Positions are deliberately **not** re-read. Position changes are a consequence
of order execution, so an order-set change is the leading indicator; re-reading
positions would double the added span for a strictly weaker signal.

### Comparison

Compare A and B on the attributed, normalized projection only — never on raw
broker payloads:

- the set of related order identities (attributed by exact symbol),
- each one's normalized state,
- the executable-sell count,
- the protective-candidate count.

Any difference sets `order_state_changed_during_acquisition` **truthfully**.

### Field semantics

Rename to describe what is measured, or make the current names true. Both are
acceptable; leaving a name that overstates its guarantee is not:

| Current | Proposed |
| --- | --- |
| `order_state_changed_during_acquisition` | keep the name, populate it from the A/B comparison |
| `position_observations_disagreed` | rename to `position_response_self_contradictory` |
| *(new)* | `market_session_changed_during_acquisition` — the signal currently misfiled into the first field |

`ReadOnlyBrokerSnapshot` lives in `src/read_only_broker_state.py`, also pinned,
so the rename belongs in the same re-pin window.

### Policy on detected change

**Report, never retry.** A detected change yields `UNKNOWN_BROKER_STATE` and
the cycle ends. A retry would widen the span, consume a second manifest
session, and risk an unbounded loop against a genuinely churning account.
Retrying is a decision for the unattended scheduler in Milestone 10, which can
reason about backoff and budget; it is not a decision for an observation
primitive.

### Budget

Four calls per cycle instead of three. `maximum_calls_per_tool` becomes 2 for
`get_equity_orders` and stays 1 for the other two — a per-tool ceiling, not a
uniform one, so `get_accounts` and `get_equity_positions` cannot silently gain
a second call. Span cap stays at 2 seconds; if a fourth read cannot fit inside
it, `SPAN_TOO_WIDE` is the correct answer.

## Acceptance tests this design must ship with

- an order appearing between A and B sets the flag and yields `UNKNOWN_BROKER_STATE`
- an order **disappearing** between A and B does the same
- a state transition on the same order between A and B does the same
- an unchanged order set leaves the flag false and permits the ordinary decision
- a change on an **unrelated symbol** does *not* set the flag
- the confirmation read failing degrades the order component to `UNAVAILABLE`
  rather than being treated as "no change"
- the fourth read is counted, ceilinged, and refused on a third orders call
- span still enforced: four reads exceeding 2s yields `SPAN_TOO_WIDE`
- no additional `call_tool` site is dynamic — all four remain literal

## Safety invariants that must survive

- zero placement, cancellation, replacement, and strategy-activation paths
- exactly three permitted tools; the confirmation read reuses `get_equity_orders`
  and adds **no** new capability
- approved-state count stays `0`; nothing here approves anything
- evidence stays allowlisted; the comparison result is a boolean, never a diff
  of broker payloads
- `strategy.enabled` stays `false`

## Sequencing

**IMPLEMENTED.** This design landed in the re-pin window, batched with the
authoritative-calendar delegation, lifecycle-model adoption, the manifest BOM
fix, and the authorization-ordering correction, so the digest moved once for
all five.

The window was opened at the cheapest possible moment: the only authorization
manifest ever issued had already been spent by the completed 2026-08-03
checkpoint, so reissuing against a new digest invalidated nothing outstanding.

| Digest | Value |
| --- | --- |
| Pre-re-pin (historical) | `8516057134acc1540bfa90f76fc9f5663588fcff53d5d08d07d20d4c79131faf` |
| Current | `3c972092693a41524d2406fb700ff9cdab4f635bad3474630a6882c3ac6b3b1c` |

Shipped as designed: the confirmation read is orders-only, the comparison is on
the attributed normalized projection, the per-tool ceiling is 2 for
`get_equity_orders` and 1 for the others, detected change reports rather than
retries, and the span cap still wins over the fourth read. Every acceptance test
listed above ships with it.

**What remains outside the guarantee, deliberately:** positions are still read
once, so intra-cycle *position* consistency is not established — only order
consistency is. An unattended caller that needs the former does not have it.
