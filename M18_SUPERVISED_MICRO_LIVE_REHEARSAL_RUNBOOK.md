# Supervised one-share commissioning rehearsal

This rehearsal is a non-writing check of saved, sanitized M17 handoff evidence. It cannot submit, cancel, replace, flatten, or otherwise mutate broker state.

## Preconditions

- An authentic saved `AttendedCommissioningHandoff` JSON artifact is available.
- The handoff is `HANDOFF_READY`, has a fresh `ALLOW` safety decision, and binds exactly one AAPL share.
- The current release/source SHA, account-fingerprint observation, suspension CLEAR observation, candidate freshness, and the four attended acknowledgements have been independently verified in their governed sources.

## Command

```powershell
python scripts/run_supervised_micro_live_rehearsal.py --handoff <sanitized-handoff.json>
```

For deterministic test data only, add `--fixture`. That label does not establish any live fact.

## Inspect

Confirm all of the following in the sanitized output artifact and console summary:

- `rehearsal_state=READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION`
- `quantity=1`, `side=buy`, and `order_type=market`
- entry, stop, and target intent match the handoff
- `safety_decision_result=ALLOW` and `safety_decision_freshness=FRESH`
- `broker_write_count=0`, `execution_authorized=false`, and `protection_grounded=false`
- the reported broker boundary is `RobinhoodCommissioningAdapter._call()`

## Final attended boundary and abort conditions

The rehearsal stops before `RobinhoodCommissioningAdapter._call()`. A human must separately re-confirm the four operator acknowledgements, action identity, source/release SHA, candidate freshness, account fingerprint, suspension CLEAR status, and fresh allowing safety decision before any explicit attended write authorization.

Abort immediately if the handoff is blocked or stale, any source identity changes, the account fingerprint does not match, suspension is non-CLEAR or stale, the decision is not fresh ALLOW, or the proposed action is not exactly one share.

## After an explicitly attended entry

The governed commissioning sequence is: entry placement, fill reconciliation, protective-stop review, a fresh stop safety decision, explicit attended stop authorization, stop placement, and stop reconciliation. The rehearsal does not assert that a broker-side protection order exists; preserve the candidate, review, dry-run, preflight, live-input, safety, handoff, rehearsal, and later broker reconciliation evidence for the operator record.
