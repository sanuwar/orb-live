# Supervised operator console

The console is a one-shot, read-only projection of privacy-safe evidence:

```text
python scripts/run_supervised_operator_console.py --runtime-root <root> --as-of 2026-08-05T12:00:00-04:00 --format text
```

The runtime root must contain `config/deployment-profile.json`,
`config/lifecycle-contract.json`, and `evidence/operator-console-evidence.json`.
The first two files are validated through their existing public contracts. The
evidence file is an exact, privacy-safe serialization of existing component
projections (the example is under `examples/read-only-deployment/`); the console
does not create or modify it. Its release SHA and implementation digest bind the
snapshot to the checked-out source.

JSON output has a fixed allowlist and is deterministic. A nonzero exit code
means that review or fail-closed handling is required. Exit code zero means
only that no immediate offline operator action is projected; it never grants
deployment, broker, live-trading, or unattended authorization.

The command requires an injected timezone-aware `--as-of`, performs no network
or broker operation, does not read credentials, does not poll, and exits after
one snapshot.
