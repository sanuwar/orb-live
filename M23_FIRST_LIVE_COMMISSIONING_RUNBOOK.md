# M23 First Live Commissioning Runbook

This runbook prepares one attended, one-share commissioning lifecycle. It is
not authorization to submit an order.

## Preconditions

- An operator is present with the approved local Robinhood session and Alpaca
  market-data credentials configured locally. Never put credentials, cookies,
  tokens, or account numbers in commands, evidence, or chat.
- Main/release SHA and implementation digest match the reviewed evidence.
- The account fingerprint matches the commissioned account and the suspension
  assessment is fresh `CLEAR`.
- The M22 unattended policy remains disabled. This is attended commissioning,
  not unattended operation.
- Use only the fixed watchlist and exactly one share. Stop if any account,
  source, policy, market-data, lifecycle, or protection fact is ambiguous.

## Candidate and final non-writing gate

During the regular-session ORB window, capture a genuine candidate:

```powershell
python scripts/run_live_shadow_session.py --mode LIVE_SHADOW --production --symbols AAPL,MSFT,NVDA,AMD,AMZN,META,GOOGL,TSLA,AVGO,NFLX
```

Preserve the live-shadow evidence. It must contain an authentic
`LONG_CANDIDATE`; a rejection or no-signal is not a commissioning input.

Build the existing review, dry-run, preflight, safety evaluation, and handoff
artifacts using their saved evidence. Then run the final non-writing rehearsal:

```powershell
python scripts/run_supervised_micro_live_rehearsal.py --handoff <sanitized-handoff.json>
```

The required stopping point is
`READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION` immediately before
`RobinhoodCommissioningAdapter._call()`.

For the one bounded production read-only verification, use the existing runner
with an absolute, operator-managed suspension database path:

```powershell
python scripts/run_production_read_only_rehearsal.py --production-read-only --live-shadow <live-shadow-evidence.json> --handoff <sanitized-handoff.json> --release-sha <current-40-character-sha> --suspension-db <absolute-path-outside-repository> --ack acknowledges_loss_may_exceed_plan --ack operator_present --ack robinhood_ui_open --ack no_second_trade
```

That command may observe only the authoritative account fingerprint and
suspension state. It stops before `RobinhoodCommissioningAdapter._call()`.
Preserve the candidate, review, dry-run, preflight, safety, handoff,
rehearsal, account snapshot, and suspension evidence.

## Explicit attended write authorization

Do not cross the adapter boundary without a separate explicit operator
authorization. At action time, reconfirm the fresh safety decision, source and
implementation identity, account fingerprint, suspension `CLEAR`, one-share
quantity, action/ref identity, and all required acknowledgements. A stale or
ambiguous fact aborts the attempt. Once an entry write is attempted, do not
seek a second trade that day.

## After an authorized entry

Read authoritative broker state. Reconcile the entry before preparing a stop.
Only a fully reconciled filled quantity may be protected. Reuse the original
candidate stop plan; do not recalculate it. Obtain a fresh protective-stop
safety decision and a separate explicit attended authorization for the stop.
After stop submission, reconcile the working stop and preserve M20
authoritative grounding evidence. A missing, rejected, cancelled, unknown, or
insufficient stop while a position remains open is an immediate stop/abort;
do not automate retry, replace, cancel, or flatten.

For a later target/time exit, preserve protection until its cancellation is
authoritatively confirmed, then obtain fresh exit safety and explicit attended
authorization. Never assume OCO behavior. Preserve final fill/reconciliation
evidence and do not take a second trade that day.

## Later pilot activation

Only after one complete authenticated lifecycle, use the M23 readiness gate to
produce `ACTIVATION_ELIGIBLE` evidence. That status does not enable the M22
policy. A future explicitly authorized activation step must revalidate the
account, policy, source SHA, implementation digest, risk limits, and expiry.
