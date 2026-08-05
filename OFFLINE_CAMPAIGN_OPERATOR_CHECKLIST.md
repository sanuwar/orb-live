# Offline campaign operator checklist

- [ ] Confirm campaign ID: `________________________________`
- [ ] Confirm campaign index hash: `________________________________`
- [ ] Confirm all eight fixed scenarios are present exactly once.
- [ ] Confirm commissioning run IDs and occurrence identities are unique.
- [ ] Confirm every bundle hash is verified and no partial output exists.
- [ ] Confirm adverse outcomes remain adverse, including review, suspension,
      ambiguity, failed-closed, and stale outcomes.
- [ ] Confirm the privacy audit passed and no private or account data was used.
- [ ] Confirm `strategy.enabled=false` and approvals are empty.
- [ ] Confirm automated permission, live readiness, and unattended readiness
      are all false.
- [ ] Confirm protection grounding is incomplete and activation approval absent.
- [ ] Confirm no broker session, credentials, manifest, checkpoint, or live
      write was used.

I acknowledge that this package verifies offline evidence only and does not
authorize broker access, strategy activation, live trading, or unattended
operation.
