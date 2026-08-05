# Controlled shutdown and restart

The lifecycle contract requires controlled operator shutdown. It is a reviewable procedure, not a service or process-control implementation.

## Shutdown

1. Stop accepting new cycles.
2. Allow the current bounded cycle to finish or fail closed.
3. Flush durable evidence and record the final heartbeat.
4. Confirm the alert outbox state and record a shutdown receipt.
5. Release the modeled process ownership and verify the process has stopped.

## Restart

- After clean shutdown, validate the deployment profile, lifecycle contract, release identity, and preflight before a manual restart.
- After a crash, inspect durable state and require recovery review before restart.
- After ambiguous state or stale ownership, do not restart until review resolves the identity and evidence boundary.
- Acknowledgment closes an operator obligation only; it never clears suspension or creates permission.

No command in this runbook contains credentials, private paths, broker data, destructive actions, or an electronic approval mechanism.
