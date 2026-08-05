# Host process lifecycle runbook

This is an offline operator contract for a future supervised read-only host. It does not install a service, launch a process, acquire an operating-system lock, create an approval mechanism, or authorize deployment.

## Manual startup

- Record the release commit SHA and implementation digest.
- Validate the read-only deployment profile and lifecycle contract.
- Verify strategy is disabled, approvals are empty, and no stale modeled lock requires review.
- Assign the operator and alert recipient before any start decision.
- Start only after the bounded preflight passes. Startup is manual and single-instance.

The existing bounded runner, scheduler, liveness monitor, and suspension controller remain the runtime authorities. This document does not duplicate their state or heartbeat logic.

## Crash recovery

Do not restart automatically. Inspect durable state, the last heartbeat, suspension state, partial-cycle evidence, and alert delivery. Require operator review. If release identity, ownership, or state is ambiguous, remain stopped and fail closed.

## Rollback boundary

Stop the current process through the controlled shutdown procedure, preserve evidence, and do not delete durable state. Validate the prior release identity and perform a fresh preflight review. Never roll back into a configuration that enables strategy, writes, live readiness, or unattended readiness.
