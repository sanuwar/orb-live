# Publication P3B Phase 2B-2N

Readiness: `READY_FOR_INDEPENDENT_REVIEW`.

This publication-support package resolves the Phase 2B-2A design blocker by providing an isolated CPython startup guard for a future, separately authorized M53 invocation. It neither imports nor executes M53, M52, or any ORB-LIVE project module. It changes no strategy, configuration, cache, parameter, market-data, broker, or statistical behavior.

The guard lives outside the historical M53 source closure at `scripts/publication/m53_network_guard/`. A future launcher must expose only that directory first on `PYTHONPATH` (without `-S`), set every `ORB_M53_GUARD_*` variable described in the activation guide, and require a fresh evidence file. `sitecustomize` verifies its token and `guard_core.py` SHA-256 before any target import, opens durable evidence, installs subprocess and network controls, and logs activation. A launcher must reject execution if the child does not return a verified activation event matching the intended version/hash.

This workstream is not authorization to run M53. It produced no M53 artifact.
