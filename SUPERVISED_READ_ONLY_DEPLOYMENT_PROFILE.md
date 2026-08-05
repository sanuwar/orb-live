# Supervised read-only deployment profile

This package defines a deterministic, broker-independent host configuration for a future supervised read-only review. It is configuration evidence only; a valid profile does not authorize deployment, broker access, checkpoint execution, live trading, or unattended operation.

The frozen `ReadOnlyDeploymentProfile` model binds the exact schema version, checked-out release SHA, canonical implementation digest, and pinned-file count. It also validates distinct relative runtime directories, source-read-only repository policy, and the closed execution boundary. It accepts no credentials, account values, symbols, prices, quantities, balances, order identifiers, URLs, or arbitrary environment settings.

Validate one profile offline with:

```text
python scripts/validate_read_only_deployment_profile.py --profile <profile.json>
```

Exit status zero means only `VALID_FOR_OFFLINE_CONFIGURATION_REVIEW`. Identity, safety, path, and malformed-input failures are rejected with fixed nonzero classes. The command does not start ORB-LIVE, write runtime state, access a broker, or use a network.

The valid example keeps broker read-only access unconfigured, writes absent, strategy disabled, approvals empty, protection ungrounded, activation unapproved, automated permission false, manual startup, operator supervision, heartbeat, and alert confirmation required. The invalid examples demonstrate write-capability and release-identity rejection.

Host process supervision, credential scope, network allowlisting, backups, recovery exercises, alert ownership, and external deployment approval remain separate P1 gaps. No profile field creates an approval or readiness state.
