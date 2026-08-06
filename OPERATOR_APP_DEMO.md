# ORB-LIVE operator application demo

The demo creates synthetic, privacy-safe snapshots and a disposable SQLite read model; it never contacts a broker or external network.

```text
python scripts/create_operator_demo.py --output-root .runtime/operator-demo --as-of 2026-08-05T12:00:00-04:00
python scripts/run_operator_demo.py --output-root .runtime/operator-demo --host 127.0.0.1 --port 8765 --as-of 2026-08-05T12:00:00-04:00
```

Open the printed loopback URL. The application is read-only and non-authoritative. Remove only the verified demo directory when finished; synthetic data contains no accounts, orders, symbols, credentials, or private paths.
