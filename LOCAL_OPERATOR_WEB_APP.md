# Local read-only operator web app

Run the local browser interface with an explicit deterministic timestamp:

```text
python scripts/run_operator_web_app.py --runtime-root <safe-runtime-root> --host 127.0.0.1 --port 8765 --as-of 2026-08-05T12:00:00-04:00
```

Routes are `GET /`, `GET /api/snapshot`, `GET /healthz`, and `GET /about`.
The app binds only to loopback, serves server-rendered HTML, uses no external
assets or outbound network, and performs no durable writes. It is display-only:
it cannot acknowledge, clear, approve, activate, trade, or authorize unattended
operation. Press Ctrl+C for clean shutdown.
