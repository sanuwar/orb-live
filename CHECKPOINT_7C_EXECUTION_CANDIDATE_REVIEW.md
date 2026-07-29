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
authorization; one entry place; order/position reconciliation; one-share
fill confirmation; stop review; one stop place; exact-ID stop/position
reconciliation; provisional protection observation; flatten authorization; one
stop cancel; exact-ID cancellation reconciliation; if still long one with the
stop terminal, exit review and one exit place; exact-ID exit reconciliation; and
final verification of zero position with no related nonterminal order.

Read-only order/position polling may repeat within monotonic deadlines. Maximum
writes are one entry place, one stop place, one stop cancel, and one exit place.
Writes are serialized and never automatically retried.

After an entry-place result that may have crossed the broker write boundary but
did not yield a validated response, the entry write is never repeated. Bounded
read-only reconciliation compares subsequent orders against the preflight
order-ID baseline retained transiently in memory. The flow may proceed only
when exactly one new fixed-policy AAPL buy is uniquely attributable and its
recognized state, quantities, and latest position are internally consistent.
A position-only observation is retained but is insufficient to proceed without
the matching validated order and fill data. Multiple matches, missing
attribution, inconsistency, or timeout require operator control.

## Runtime controls and recovery

Two separate, non-reusable authorizations are required:

- `AUTHORIZE ORB LIVE AAPL ONE SHARE ENTRY PLANNED RISK 15`
- `AUTHORIZE ORB LIVE AAPL ONE SHARE CONTROLLED FLATTEN`

Each binds the safe run ID, current Eastern date, narrow window, AAPL, one whole
share, `planned_risk_usd=15`, acknowledgment that actual loss may exceed planned
risk, continuous attendance, Robinhood UI availability, and no second trade.

The reviewed Python path fixes the execution date at 2026-07-28. Entry
authorization and placement are permitted from 15:30:00 ET through strictly
before 15:45:00 ET; 15:45:00 is rejected. Placement time is checked again
immediately before the one entry write. After entry submission, only
reconciliation and risk-reducing operations remain. The existing flow proceeds
directly from observed protection to stop cancellation and controlled
flattening; unresolved entry or protection at the mandatory 15:50:00 risk-off
time requires operator control. Automated writes stop at the 15:55:00
verified-flat deadline, and any unresolved state requires immediate manual
Robinhood control. No post-cutoff or second-entry path exists. These timing
controls do not guarantee a maximum monetary loss or assume normal execution
after 16:00.

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

Attempt 05 (`cp7c-live-aapl-one-share-20260728-05`) failed after
`ENTRY_REVIEWED`, during entry-authorization acquisition or input handling and
before any broker write boundary. The prior generic exception handling did not
retain enough sanitized detail to distinguish invalid input from an input-reader
failure. The wrapper and Run ID are permanently retired. The reviewed repair
adds bounded authorization validity and stage-specific sanitized diagnostics
without exposing raw exception text or broker-private values.

Attempt `cp7c-live-aapl-one-share-20260729-02` reached the entry write boundary
once and returned `AMBIGUOUS_ENTRY_SUBMISSION`. The retained evidence proves
that no validated place result reached the orchestrator and that no post-write
order or position read occurred. It cannot recover whether the initiating
subcause was transport failure, response-schema rejection, or response-identity
rejection. The operator subsequently observed the entry fill, manually exited
one share, and observed a flat account; those remain operator observations.
The reviewed repair adds the conservative post-write reconciliation described
above, permanent Run-ID retirement, and distinct sanitized ambiguity
categories. A complete broker-free incident rehearsal reached `VERIFIED_FLAT`
and `COMPLETE` with one write per role and no emergency handoff.

Attempt `cp7c-live-aapl-one-share-20260729-03` reached the confirmed one-share
entry fill, completed stop review, and crossed the stop-place write boundary
once. The retained evidence records zero post-stop order reads and zero
post-stop position reads. It cannot distinguish a transport exception from a
malformed or policy-inconsistent response because the historical path collapsed
those subtypes. The reviewed repair now classifies them separately and performs
bounded baseline-differential read-only stop reconciliation without repeating
the write. Only one exact, uniquely attributable, internally consistent stop
may proceed. A uniquely confirmed filled stop with a flat position completes
without a cancel or another sell; all multiple, missing, partial, cancellation-
pending, conflicting, or timed-out outcomes require operator control. The
failed wrapper and Run ID are permanently retired. Broker-free pending-stop and
filled-stop rehearsals both reached `VERIFIED_FLAT` and `COMPLETE` with no
emergency handoff.

Another live attempt requires all of the following:

- the reviewed repair merged to `main`;
- a successful post-incident fake rehearsal;
- a new execution date and attended window;
- a new single-use wrapper;
- a new unused Run ID;
- separate attended entry and flatten authorizations.
Real-session mode remains unexecuted, and real broker place/cancel response
shapes and state transitions remain unvalidated. `strategy.enabled` remains
false. There is no normal strategy-runner or live `SagaExecutor` binding.
Checkpoint 7C remains **NOT EXECUTED LIVE**; Checkpoint 7D remains not executed;
Milestone 7 remains incomplete.
