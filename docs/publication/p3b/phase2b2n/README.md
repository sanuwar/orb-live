# Publication P3B Phase 2B-2N

Readiness: `READY_FOR_INDEPENDENT_REVIEW`.

This publication-support package resolves the Phase 2B-2A design blocker by providing an isolated **process-scoped CPython startup guard** for a future, separately authorized M53 invocation. It is not an operating-system sandbox. It neither imports nor executes M53, M52, or any ORB-LIVE project module. It changes no strategy, configuration, cache, parameter, market-data, broker, or statistical behavior.

The guard lives outside the historical M53 source closure at `scripts/publication/m53_network_guard/`. A future launcher must:

1. Externally hash both `guard_core.py` and `sitecustomize.py` before launch and compare them to the reviewed commit — the guard cannot establish this about itself.
2. Expose only that directory first on `PYTHONPATH`, without `-S`.
3. Set every `ORB_M53_GUARD_*` variable described in `GUARD_DESIGN_AND_ACTIVATION.md`, including the two guard-file digests and the content digests of the pinned Python and Git executables.
4. Supply a fresh, non-existent evidence file path.
5. After the run, require `verify_guard_run()` to pass — which demands the affirmative `guard_ready` record, a terminal `normal_interpreter_exit`, an intact untruncated chain, and zero denied events.

`sitecustomize` verifies its token, both guard-file SHA-256 values, the pinned executable content hashes, and the bootstrap import path before any target import; it then opens durable evidence, installs subprocess and network controls, and only afterwards records `guard_ready`. An `activation` record alone is **not** a ready state and a launcher must not treat it as one. `ORB_M53_GUARD_TOKEN` is a configuration handshake committed in this repository, not a secret.

## Documents

- `GUARD_DESIGN_AND_ACTIVATION.md` — launcher contract, bootstrap integrity, executable pinning, activation vs. readiness.
- `NETWORK_THREAT_MODEL.md` — what is denied, in which layer, and the explicit residual limits.
- `SUBPROCESS_ALLOWLIST.md` — the two approved Git vectors and the controlled child.
- `EVIDENCE_LOG_SPECIFICATION.md` — record format, verification, and what the hash chain cannot prove.
- `TEST_AND_CAPABILITY_REPORT.md` — focused test results and coverage.
- `M53_NETWORK_GATE_READINESS_DECISION.md` — the readiness label and remaining gates.

This workstream is not authorization to run M53. It produced no M53 artifact.
