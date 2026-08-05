# Recovery validation checklist

Human guidance only. This checklist cannot change runtime state and does not authorize deployment.

- [ ] Source release SHA, implementation digest, and pinned-file count match.
- [ ] Deployment profile and lifecycle contract identities match.
- [ ] All ten required artifacts are present exactly once.
- [ ] Restore order and schema identities are exact.
- [ ] Every artifact hash and package digest verifies.
- [ ] Restore is isolated under an approved temporary root.
- [ ] Suspension and ambiguous state remain preserved and review-required.
- [ ] Replay prevention, alert delivery state, and acknowledgment awareness remain intact.
- [ ] Partial cycle and stale heartbeat remain fail-closed.
- [ ] Strategy disabled; approvals empty; protection ungrounded; activation unapproved.
- [ ] Automated permission, live readiness, and unattended readiness remain false.
- [ ] No credentials, broker, network, manifest, checkpoint, or private data were used.

I acknowledge that this package verifies offline recovery evidence only and does not authorize deployment, broker access, strategy activation, live trading, or unattended operation.
