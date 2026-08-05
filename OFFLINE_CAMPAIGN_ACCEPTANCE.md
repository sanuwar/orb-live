# Offline campaign operator acceptance

`verify_offline_campaign_acceptance.py` consumes one completed campaign output
and publishes `offline-campaign-acceptance.json` only after the existing
campaign verifier, bundle hashes, identity bindings, privacy checks, and
closed safety invariants pass.

```text
python scripts/verify_offline_campaign_acceptance.py --campaign-output C:\temp\orb-campaign --acceptance-id acceptance-offline-20260803 --verified-at 2026-08-03T14:00:00+00:00 --output C:\temp\offline-campaign-acceptance.json
```

The result is evidence packaging for offline review only. It does not infer
readiness from a successful scenario, campaign completion, tests, CI, GitHub
activity, or operator acknowledgment. It does not authorize broker access,
strategy activation, live trading, unattended operation, or any other action.
Failures leave no acceptance result, and an existing result is never
overwritten.
