# Checkpoint 7C Attempt 2026-07-29-02 Incident Review

## Sanitized incident facts

- Retired Run ID: `cp7c-live-aapl-one-share-20260729-02`
- Date: 2026-07-29
- Original reason: `AMBIGUOUS_ENTRY_SUBMISSION`
- Retained evidence SHA-256:
  `4fd7e112a457ca1b5209025e17ab176bc553405811c42b62aed55764842f0787`
- Latest confirmed program stage: `ENTRY_PLACE`
- Entry review accepted: yes
- Entry write boundary reached: yes
- Entry-place attempts: 1
- Entry-place retries: 0
- Post-place program reconciliation reads: 0

The operator observed that the one-share AAPL entry filled, manually sold one
share, observed that manual sale fill, and observed the account returned flat.
These are dated operator observations, not programmatically verified facts.

## Narrowest defensible cause

The retained sanitized evidence proves that the adapter did not return a
validated place result to the orchestrator. It does not distinguish whether
the transport raised after submission, the returned response envelope or order
object failed schema validation, or the returned order identity failed fixed
policy validation. It also does not preserve a returned order state. The exact
initiating ambiguity subtype is therefore unrecoverable.

The blocking program defect is grounded: every possible entry-write ambiguity
immediately transitioned to operator control. The program performed no bounded
read-only order or position reconciliation after the write boundary, so it
could not recognize a uniquely attributable filled entry that became visible
after submission.

## Repair

The adapter now distinguishes a returned malformed entry response from a
transport-ambiguous entry result using sanitized categories. A valid
`data.order` response in a recognized working or filled state is accepted as a
submission result and remains subject to read-only verification.

After any possible entry-write ambiguity, the orchestrator never repeats the
write. It performs bounded order and position reads and compares new orders
against the complete preflight order-ID baseline held transiently in memory.
It proceeds only when exactly one new fixed-policy AAPL buy has recognized,
internally consistent state and quantity data consistent with the latest
position. Multiple candidates, missing attribution, inconsistent position or
order data, and timeout remain `OPERATOR_REQUIRED`.

A one-share position without a uniquely matching validated order is recorded
but is not sufficient to proceed to protection, because exact attribution and
validated fill data remain unavailable.

The failed wrapper and Run ID are permanently retired.

## Verification

Focused regression coverage includes valid nested `data.order` working and
filled responses, malformed and transport-ambiguous responses, delayed fill
visibility, unique order reconciliation, position-only observation,
multiple-match rejection, missing-order rejection, conflicting position,
timeout, one-shot write limits, redaction, session closure, Run-ID retirement,
canonical source hashing, and the runtime call-site guard.

The broker-free incident rehearsal
`cp7c-fake-entry-reconciliation-repair-20260729-01` completed as
`VERIFIED_FLAT` and `COMPLETE`. It used one entry place, one stop place, one
stop cancel, and one exit place, with zero entry retries and zero emergency
handoffs. Its sanitized evidence metadata is:

- SHA-256:
  `e6b0177cd939dae2274a50e3f7f06d7cf2868bf6f91841bc32e58e8940276333`
- Bytes: 3,193
- Lines: 1

## Remaining risks

The real place-response shape and initiating ambiguity subtype from this
attempt remain unproven. Real response timing, delayed order visibility,
broker state transitions, cancellation behavior, latency, gaps, halts, and
slippage remain live operational risks. Read-only reconciliation reduces
uncertainty but cannot create atomic protection or eliminate ambiguous broker
outcomes.
