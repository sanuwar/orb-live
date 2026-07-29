# Checkpoint 7C Attempt 20260729-04 Incident Review

## Sanitized incident record

- Date: 2026-07-29.
- Retired Run ID: `cp7c-live-aapl-one-share-20260729-04`.
- Original reason code: `AMBIGUOUS_STOP_CANCEL`.
- Retained sanitized evidence SHA-256:
  `b9f23dc1b6f669cc86e1832cee05a78ff52dac3d718664b84458fa6734e37cae`.
- The entry reached the programmatically confirmed filled state.
- The protective stop was uniquely reconciled and provisionally observed.
- Flatten authorization completed and the stop-cancel write boundary was
  reached once.
- Entry-place attempts: 1.
- Stop-place attempts: 1.
- Stop-cancel attempts: 1.
- Exit-place attempts: 0.
- Cancel retries: 0.
- Post-cancel order reads: 0.
- Post-cancel position reads: 0.
- Latest confirmed program stage: cancel submission within
  `CANCEL_RECONCILIATION`; the last persisted pre-failure commissioning state
  was `EXIT_AUTHORIZATION_REQUIRED`.

The retained evidence proves that the cancel adapter did not return a validated
cancel result to the orchestrator. It does not preserve whether the transport
raised after possible submission or whether a returned response failed schema
validation, and it does not establish whether a response contained an accepted
request flag. The exact initiating subtype is therefore unrecoverable. It also
cannot establish who caused the later terminal cancellation.

## Operator-reported recovery

The operator reported that one AAPL entry filled, one protective stop was
placed, the risk-off cancellation path was attempted, the stop was later shown
as canceled, the position was manually returned flat, and no AAPL orders
remained open. These are dated operator observations, not programmatically
verified evidence. Whether the agent cancel request or a manual operator action
caused the terminal cancellation is **UNPROVEN**.

## Blocking defect and repair

The blocking defect was grounded independently of the unrecoverable initiating
subtype: every adapter-level ambiguous cancel result immediately transitioned
to operator control. No bounded read-only order or position reconciliation ran
after the possible cancel write boundary, so a delayed terminal cancellation
could not be recognized.

The repair:

- permanently retires the failed Run ID;
- distinguishes malformed cancel responses from transport-ambiguous results;
- treats request acceptance only as asynchronous request acceptance, never as
  terminal cancellation;
- captures the transient pre-cancel stop/order and position baseline;
- never repeats the stop-cancel write;
- performs bounded read-only order and position reconciliation against the
  exact transient stop identifier;
- requires unique fixed-policy stop attribution and internally consistent
  position and quantity state;
- permits one exit only after terminal cancellation and a latest validated
  one-share-long position;
- submits no exit when a filled stop is uniquely reconciled with a flat
  position;
- keeps pending, active, partial, missing, multiple, conflicting, inconsistent,
  or timed-out outcomes in `OPERATOR_REQUIRED`;
- records only sanitized categories, states, counters, and required operator
  actions.

## Offline verification

Focused regression coverage includes accepted-request semantics,
`pending_cancelled`, terminal cancellation, partial-fill remainder cancellation,
delayed read-only reconciliation, filled-stop flatness, one-share-long exit,
still-active and missing stops, multiple matches, conflicting state, timeout,
one-shot writes, zero retries, redaction, session safety, Run-ID retirement,
canonical source hashing, and the runtime call-site guard.

The broker-free repaired-cancel rehearsal used Run ID
`cp7c-fake-stop-cancel-repair-20260729-01` and reached `VERIFIED_FLAT` and
`COMPLETE` with one entry place, one stop place, one stop cancel, one exit place,
zero cancel retries, no unresolved orders, and no emergency handoff. Its
sanitized evidence metadata is:

- SHA-256:
  `2757bdbf254adfc05aa9dc4ba4863f33876ccb4a799eb1bf4d45d52cc14f065b`;
- bytes: 3,796;
- lines: 1.

A separate broker-free stop-filled-during-cancel scenario reached
`VERIFIED_FLAT` and `COMPLETE` with one cancel attempt, zero cancel retries,
zero exit places, and no emergency handoff.

## Remaining operational risks

The historical cancel transport/response subtype and terminal-cancellation
initiator remain unproven. Real cancel response shape, order-state timing,
broker latency, manual intervention timing, gaps, halts, and slippage remain
live operational risks. The repair reduces uncertainty but cannot make
cancellation synchronous, create atomic protection, or eliminate broker races.
The failed wrapper and Run ID must never be reused.
