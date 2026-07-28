# Checkpoint 7C Attempt 05 Incident Review

## Incident summary

- Date: 2026-07-28
- Run ID: `cp7c-live-aapl-one-share-20260728-05`
- Launcher invoked: true
- Launcher exit code: 3
- Reported state: `OPERATOR_REQUIRED`
- Original reason code: `UNEXPECTED_ORCHESTRATION_FAILURE`
- Evidence classification: `PARTIAL`
- Failed evidence integrity: SHA-256
  `70cd0a0e1aabcf3fc2b2fb1c6f2cf6b91c69a87b66218fc8eb33e147b622594b`,
  879 bytes, one line

After the failure, the operator manually observed in the Robinhood UI that the
AAPL position quantity was zero and that there were no open AAPL orders. This
is a dated operator observation, not a conclusion derived from program
evidence.

## Forensic finding

The latest confirmed state was `ENTRY_REVIEWED`. The observed transition went
directly from `ENTRY_REVIEWED` to `OPERATOR_REQUIRED`. The evidence records one
entry review call, zero place calls, zero cancel calls, zero write attempts, and
`write_boundary_reached=false`.

The narrowest defensible failure stage is entry-authorization acquisition or
input handling after the entry review and before authorization consumption or
entry placement. No broker write boundary was reached. The retained evidence
cannot distinguish an invalid authorization input from an input-reader
exception because the prior implementation did not persist a sanitized
authorization-input failure category or exception class.

## Root cause and correction

The proven diagnostic defect was that an authorization-provider failure before
any write boundary was collapsed into the generic
`UNEXPECTED_ORCHESTRATION_FAILURE` classification. The exact initiating input
subcause is not recoverable from the retained evidence.

The repair:

- adds allowlisted failure stages, categories, and exception classes;
- records sanitized write-boundary status and per-write counters;
- classifies authorization-input and entry-window failures before a write as
  fail-closed without claiming that a write occurred;
- preserves `OPERATOR_REQUIRED` for ambiguous or unexpected failures at or
  after a possible write boundary;
- persists sanitized diagnostic evidence for session, authentication, account,
  adapter, orchestrator, evidence, and session-close failures;
- permanently rejects the failed Run ID and earlier retired live Run IDs;
- limits attended authorization validity to 15 minutes and the reviewed entry
  cutoff, whichever occurs first.

The authorization-lifetime defect was found during offline reproduction. It
did not produce the incident's observed transition sequence and is therefore
not claimed as the initiating cause of Attempt 05.

## Offline verification

Focused regression coverage includes the incident path, authorization-reader
failure, pre-review and post-review pre-placement failures, ambiguous entry
placement, post-write interruption, session lifecycle failures, evidence
redaction, accurate counters, retired Run IDs, and a full fake session.

The post-incident fake rehearsal used Run ID
`cp7c-fake-post-incident-rehearsal-20260728-01`. It completed as `COMPLETE` and
`VERIFIED_FLAT`, with no unresolved order or emergency handoff. Each permitted
write occurred once: entry place, protective-stop place, protective-stop
cancel, and exit place. Its sanitized evidence SHA-256 is
`2ec363d0a4a5d88d2d4e219565e6ea2effa2001897cb8bb21f11a2e3a368c6c3`;
it is 2,973 bytes and one line.

## Retirement and remaining risk

The wrapper `checkpoint_7c_live_run_20260728_05.ps1` and Run ID
`cp7c-live-aapl-one-share-20260728-05` are permanently retired and must not be
executed or reused.

Real place and cancel response behavior, real order-state transitions, broker
latency, and emergency manual handoff remain live-validation risks. No new live
attempt is authorized by this review.
