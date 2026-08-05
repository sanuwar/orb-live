# Bounded synthetic market-day shadow session

`src/bounded_shadow_session.py` is a projection-only wrapper around the public
offline rehearsal and exchange-calendar contracts. It runs one finite rehearsal
under a caller-supplied new temporary root, adds pre-market, regular-session,
early-close, and unsupported-date calendar boundaries, and returns one fixed
JSON-safe report.

It is not a daemon, service, scheduler, process supervisor, or live-market
integration. It does not sleep, poll, create threads, access brokers, load
credentials, consume manifests, or authorize action. The durable files belong
to the existing rehearsal and remain beneath the supplied temporary directory.

Example:

```text
python scripts/run_bounded_shadow_session.py --root <new-temporary-directory>
```

The report is offline evidence only. `live_ready`, `unattended_ready`,
`protection_grounded`, `activation_approved`, and
`permits_automated_action` are always false.
