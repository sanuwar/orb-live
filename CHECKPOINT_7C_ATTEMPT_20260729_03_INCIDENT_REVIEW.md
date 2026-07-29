# Checkpoint 7C Attempt 20260729-03 Incident Review

## Sanitized incident record

- Date: 2026-07-29.
- Retired Run ID: `cp7c-live-aapl-one-share-20260729-03`.
- Original reason code: `AMBIGUOUS_STOP_SUBMISSION`.
- Retained sanitized evidence SHA-256:
  `f6222dfe071db9ba624ec561efca898ff9b5ac5a1fa3acaf400a09bff8e48100`.
- The entry reached the programmatically confirmed `ENTRY_FILLED` state.
- Stop review completed and the stop-place write boundary was reached once.
- Entry-place attempts: 1.
- Stop-place attempts: 1.
- Stop-place retries: 0.
- Stop-cancel attempts: 0.
- Exit-place attempts: 0.
- Post-stop order reads: 0.
- Post-stop position reads: 0.
- Latest confirmed program stage: `STOP_REVIEWED` in `STOP_PROTECTION`.

The retained evidence proves that the stop result did not complete validated
response handling. It does not retain enough sanitized detail to distinguish
a transport exception from a malformed or policy-inconsistent response.
Therefore, the exact initiating subtype is unrecoverable. The narrowest
defensible cause is a possible accepted stop write followed by an unvalidated
result, combined with an orchestration defect that immediately required
operator control instead of running bounded read-only stop reconciliation.

## Operator-reported recovery

The following are dated operator observations, not programmatically verified
facts:

- the one-share AAPL entry filled;
- one AAPL protective sell stop appeared pending;
- the operator canceled that stop and observed a canceled status;
- the operator manually sold one AAPL share and observed the sale fill;
- the operator observed the account returned flat.

No inference about the historical response body or transport outcome is made
from those observations.

## Repair

The repaired path:

- permanently retires the failed Run ID;
- distinguishes malformed stop responses from transport-ambiguous results;
- captures a transient pre-stop order baseline;
- never repeats the stop-place write;
- performs bounded read-only order and position reconciliation;
- requires one uniquely attributable fixed-policy stop and internally
  consistent order/position quantities;
- adopts a uniquely attributable pending stop without another place call;
- recognizes a uniquely confirmed filled stop with a flat position without
  canceling or selling again;
- keeps multiple, missing, partial, cancellation-pending, conflicting, or
  timed-out outcomes in `OPERATOR_REQUIRED`;
- records only sanitized classifications, write counters, reconciliation
  counters, and operator actions.

## Offline verification

Focused regression coverage includes nested stop response parsing, accepted
working states, filled state, delayed visibility, baseline differential,
multiple matches, wrong policy fields, missing stop, inconsistent position,
timeout, partial fill, cancellation states, one-shot writes, redaction,
permanent Run-ID retirement, session closure, canonical hashing, and the
runtime call-site guard.

The broker-free repaired-stop rehearsal used Run ID
`cp7c-fake-stop-reconciliation-repair-20260729-01` and reached
`VERIFIED_FLAT` and `COMPLETE` with one entry place, one stop place, zero stop
retries, one stop cancel, one exit place, and no emergency handoff. Its
sanitized evidence metadata is:

- SHA-256:
  `26223cdc338c6d711469e549e8c9b809c71aeca9147597156782bb1316d315de`;
- bytes: 3,450;
- lines: 1.

A separate filled-stop fake scenario reached `VERIFIED_FLAT` and `COMPLETE`
with no stop cancel, no exit place, and no emergency handoff.

## Remaining operational risk

The historical initiating subtype remains unrecoverable. Real stop response
shape, real order-state timing, broker latency, cancellation behavior, and
manual emergency handling still require live validation. A future attempt
requires the merged reviewed repair, a new unused Run ID, a new wrapper, and
separate attended authorization. The failed wrapper and Run ID must never be
reused.
