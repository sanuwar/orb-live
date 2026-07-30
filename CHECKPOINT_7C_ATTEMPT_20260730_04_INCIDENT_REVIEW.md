# Checkpoint 7C Attempt 20260730-04 Incident Review

## Sanitized incident record

- Retired Run ID: `cp7c-live-aapl-one-share-20260730-04`
- Date: 2026-07-30
- Sanitized evidence SHA-256: `1def5a3ad4b384731ad49a4bc56818b8f68500cb3c22275b77aafa12e5a031da`
- Launcher result: `OPERATOR_REQUIRED`, exit code 3
- Exact reason: `STOP_CANCEL_TIMEOUT`
- Failure stage: `CANCEL_RECONCILIATION`

The operator reported that one AAPL share was purchased, one protective stop
was placed, flatten authorization was entered, and the account was later flat
with no open AAPL orders. These are dated operator observations, not facts
programmatically verified by this run. The initiator of the terminal exit or
later flattening remains **UNPROVEN**.

## Grounded execution sequence

The retained evidence proves that the entry and protective stop were each
uniquely reconciled. The state advanced through flatten authorization to one
stop-cancel write boundary. The cancel transport result was ambiguous; no
validated cancel response reached the orchestrator. Bounded read-only
reconciliation then completed 24 exact-order reads and 24 position reads. Each
iteration uniquely attributed the protective stop, normalized its state as
`confirmed`, and conservatively classified it as still active. The position
remained long one in those observations. The cancellation never became
terminal before the monotonic deadline, so the run transitioned from
`STOP_CANCEL_RECONCILING` to `OPERATOR_REQUIRED`.

Sanitized write attempts were entry place 1, stop place 1, stop cancel 1, and
exit place 0. Cancel retries were 0. The evidence records two ambiguous-entry
reconciliation polls; the traced source path then performed one exact fill
poll. Stop placement recovery performed one baseline-differential observation
and one exact protection observation. No exit review, exit write, exit
reconciliation, or programmatic final-flat verification occurred.

The launcher exit code and reviewed async session-binding path establish that
the run returned `OPERATOR_REQUIRED` through the session context; a session
close failure would instead have produced a session diagnostic. No private
session or broker value is retained here.

## Root cause and correction

The narrowest supported root cause is:

`STOP_CANCEL_TRANSPORT_AMBIGUOUS` followed by
`STOP_CANCEL_RECONCILIATION_TIMEOUT` while the exact stop remained active.

The evidence does not support treating `confirmed` as terminal cancellation,
repeating cancel, placing an exit, or extending the deadline. The existing
runtime behavior was the required fail-closed response, so no cancellation or
flattening algorithm was weakened or changed. The supported correction is to
retire the Run ID permanently and add a faithful regression proving one cancel,
zero retry, bounded exact-order/position reads, and zero exit while the stop
remains executable. Conservative tests also retain fail-closed behavior for
pending, active, partial, missing, multiple, inconsistent, and timed-out states
and for post-cancel exit/order-position conflicts.

## Broker-free rehearsal

The post-incident fake rehearsal used Run ID
`cp7c-fake-post-buy-repair-20260730-01`. An ambiguous cancel was later observed
as terminally canceled; the run completed `VERIFIED_FLAT` and `COMPLETE` with
entry place 1, stop place 1, stop cancel 1, cancel retries 0, exit place 1, no
unresolved order, and no emergency handoff. Sanitized fake evidence metadata:

- SHA-256: `4f235ce5a89a4da25fec8edf192e4a67df083b08b03e28d334687880406f44d0`
- Bytes: 3,794
- Lines: 1

A separate fake stop-filled-during-cancel scenario completed `VERIFIED_FLAT`
and `COMPLETE` with cancel retries 0, exit place 0, and no emergency handoff.

## Remaining risks

Real cancel transport behavior, cancellation propagation latency, terminal
order-state timing, and the emergency manual handoff remain broker-dependent.
The later live flat state and absence of open orders do not recover who caused
the terminal cancellation or final exit. Any later attended attempt requires a
merged reviewed incident record, a new date/window, a new wrapper, a new unused
Run ID, and separate attended authorization. `strategy.enabled` remains false.
