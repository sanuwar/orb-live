# Bounded offline commissioning campaign

This integration layer runs the existing `scripts/run_offline_commissioning.py`
command once for the fixed scenarios below, in the listed order:

`SAFE_OFFLINE_COMPLETION`, `OPERATOR_REVIEW_REQUIRED`, `ACTIVE_SUSPENSION`,
`AMBIGUOUS_RESTART`, `MISSED_HEARTBEAT`, `ALERT_DELIVERY_UNCONFIRMED`,
`READINESS_BLOCKED`, and `STALE_EVIDENCE`.

Run it with an output directory outside the repository, for example:

```text
python scripts/run_offline_commissioning_campaign.py --campaign-id campaign-offline-20260803 --output C:\temp\orb-campaign
```

The campaign uses injected timestamps and two finite ticks. It has no sleep,
polling, network, broker, credentials, production data, database, durable
state machine, readiness state, or authorization effect. Each scenario gets a
unique commissioning run and occurrence identity. The final
`commissioning-campaign-index.json` is published only after every bundle and
hash has been verified; incomplete or altered output is rejected closed.

The index records only relative bundle paths, fixed classifications, existing
bundle-index hashes, and false safety/readiness flags. Blocked, suspended,
stale, and review-required scenarios remain adverse outcomes; they are not
converted into readiness or automated-action claims.
