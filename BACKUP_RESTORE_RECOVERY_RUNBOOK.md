# Backup, restore, and recovery runbook

This package is an offline integrity inventory for future supervised read-only recovery. It does not schedule backups, run a backup service, upload files, access credentials, or authorize deployment.

## Backup preparation

- Stop at a verified bounded boundary and record the release SHA and implementation digest.
- Validate the deployment profile and lifecycle contract.
- Inventory the fixed scheduler, suspension, runner, liveness, alert, acknowledgment, evidence, and identity artifacts.
- Preserve suspension, ambiguity, replay-prevention, alert, and acknowledgment state.
- Compute deterministic content hashes and publish one immutable package atomically.
- Verify the package before any external operator-owned storage decision.

## Restore preparation

- Assign the operator and identify the exact target release.
- Validate package identity, schema, artifact inventory, restore order, and every hash.
- Restore only into an isolated temporary path first.
- Reopen or integrity-check each durable store through its existing contract and verify all safety invariants.
- Confirm no replay, no favorable conversion of adverse state, and no acknowledgment-as-clearance.
- Require manual review before any restart.

## Failure handling

Reject partial packages, missing artifacts, changed hashes, cross-release packages, unknown schemas, conflicting restore orders, unsafe paths, and private-field leakage. Preserve the source package for investigation. Do not retry automatically, clear suspension, clear ambiguity, or change runtime state.
