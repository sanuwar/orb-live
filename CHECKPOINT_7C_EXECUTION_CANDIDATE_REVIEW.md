# Checkpoint 7C Execution-Candidate Review

## Classification

**EXECUTABLE_CANDIDATE.** The launcher owns one complete commissioning run
through exactly one `RobinhoodCommissioningAdapter` and exactly one
`OneShareCommissioningOrchestrator`. It remains fail closed by default. The
tracked `robinhood_commissioning_session` binding now owns exactly one silently
authenticated Robinhood MCP client/session and closes it after success or
failure. Any request for interactive browser authentication fails closed.

The commissioned account is resolved once through the existing configured
fingerprint validation and agentic-account selector. Its account identifier is
kept only transiently in memory, cached for the adapter's one preflight replay,
and never printed or persisted. Zero, multiple, unavailable, ambiguous, or
mismatched identities fail closed.

The obsolete pre-merge `merge-base` gate is removed. Runtime Git posture now
requires the repository root containing the launcher, branch `main`, identical
`HEAD` and local `origin/main`, a clean tracked tree, empty staging, approved
canonical source hashes, and `strategy.enabled=false`.

## Supported sequence

The only live tools are `get_accounts`, `get_equity_positions`,
`get_equity_orders`, `review_equity_order`, `place_equity_order`, and
`cancel_equity_order`.

The run sequence is: account/position/order preflight; entry review; entry
authorization; one entry place; exact-ID order/position reconciliation; one-share
fill confirmation; stop review; one stop place; exact-ID stop/position
reconciliation; provisional protection observation; flatten authorization; one
stop cancel; exact-ID cancellation reconciliation; if still long one with the
stop terminal, exit review and one exit place; exact-ID exit reconciliation; and
final verification of zero position with no related nonterminal order.

Read-only order/position polling may repeat within monotonic deadlines. Maximum
writes are one entry place, one stop place, one stop cancel, and one exit place.
Writes are serialized and never automatically retried.

## Runtime controls and recovery

Two separate, non-reusable authorizations are required:

- `AUTHORIZE ORB LIVE AAPL ONE SHARE ENTRY PLANNED RISK 15`
- `AUTHORIZE ORB LIVE AAPL ONE SHARE CONTROLLED FLATTEN`

Each binds the safe run ID, current Eastern date, narrow window, AAPL, one whole
share, `planned_risk_usd=15`, acknowledgment that actual loss may exceed planned
risk, continuous attendance, Robinhood UI availability, and no second trade.

The reviewed Python path fixes the execution date at 2026-07-28. Entry
authorization and placement are permitted from 09:35:00 ET through strictly
before 09:50:00 ET; 09:50:00 is rejected. The placement time is checked again
immediately before the one entry write. Once entry has been submitted, the
10:00 nominal attended-window end does not stop protection, reconciliation,
cancellation, emergency handling, controlled flattening, or final flat
verification. No post-cutoff or second-entry path exists. These timing controls
do not guarantee a maximum monetary loss.

Before and after every write boundary, sanitized state is persisted atomically.
An interrupted partial run is never resumed and its run ID is never reused; it
is marked `OPERATOR_REQUIRED` with the last safe state and manual reconciliation
action. Exact transient broker order IDs exist only in memory and must match one
and only one returned record. Missing, duplicate, or conflicting matches fail
closed without symbol/side inference.

## Emergency handoff and evidence

Ambiguous writes, reconciliation deadlines, identifier conflicts, state or
quantity mismatches, unresolved stops, and post-write interruptions invoke one
sanitized emergency interface. It carries only state, reason, position/stop
possibility booleans, the fixed-one-share boolean, and expected operator action.
It never carries account identity, order IDs, prices, raw responses, or secrets.

Atomic evidence contains only run ID, transitions, approved tool names/counts,
booleans, normalized states, conservative classifications, durations, JSON
types, record counts, completion/failure reason, and `planned_risk_usd=15`.

## Still requiring live validation

- actual `place_equity_order` response behavior;
- actual `cancel_equity_order` response behavior;
- real broker order-state transitions and latency;
- emergency manual handoff in the Robinhood UI.

Real-session mode remains unexecuted, and real broker place/cancel response
shapes and state transitions remain unvalidated. `strategy.enabled` remains
false. There is no normal strategy-runner or live `SagaExecutor` binding.
Checkpoint 7C remains **NOT EXECUTED LIVE**; Checkpoint 7D remains not executed;
Milestone 7 remains incomplete.
