# Static operator dashboard export

Export one local, standalone dashboard from the existing read-only console:

```text
python scripts/export_operator_dashboard.py --runtime-root <root> --as-of 2026-08-05T12:00:00-04:00 --output-root <safe-output> --include-json
```

The exporter calls the existing console projection, escapes all rendered
values, and writes a self-contained HTML file, an optional JSON snapshot, and
an exact, hashed export index. Publication is atomic and completed exports are
not overwritten. No server, browser, JavaScript, network, broker, credential,
or durable-store write is used.

The export is evidence display only. Even exit code zero does not authorize
deployment, broker access, live trading, or unattended operation.
