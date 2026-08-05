# Offline commissioning run

Run one finite synthetic commissioning session beneath a new temporary output
directory. The command never contacts a network, reads credentials, uses
production data, or grants live or unattended readiness.

```text
python scripts/run_offline_commissioning.py --scenario SAFE_OFFLINE_COMPLETION --run-id commissioning-demo-20260803 --start 2026-08-03T13:59:00+00:00 --tick-count 2 --output C:\Temp\orb-commissioning-demo
```

The output contains only `commissioning-index.json`, `session-report.json`,
`evidence-summary.json`, `readiness-report.json`, and `privacy-audit.json`.
The index binds the run identity, injected timestamps, relative filenames, and
SHA-256 content hashes. Completed bundles are never overwritten; incomplete or
tampered bundles must be removed manually before a new run.

Exit codes are 0 for safe completion, 10 for operator review, 20 for active
suspension, 30 for failed closed, 40 for ambiguous restart, and 50 for invalid
invocation. Every result keeps automated action, live readiness, and unattended
readiness false.
