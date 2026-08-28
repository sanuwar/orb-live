# Guard Design and Activation

`sitecustomize.py` imports only `guard_core.activate()`. It is loaded by the standard Python `site` phase before a requested target module. Unlike normal `sitecustomize` error handling, the shim uses `os._exit(78)` when activation raises; CPython must not continue to a target after an activation failure.

A future launcher must use an isolated guard directory that is outside the historical checkout and set these absolute, validated values before starting Python:

- `ORB_M53_GUARD_LOG`: fresh `m53-network-guard.jsonl` below an existing non-symlink temporary evidence directory.
- `ORB_M53_GUARD_TOKEN`: exactly `M53_NETWORK_DENIAL_GUARD_V1`.
- `ORB_M53_GUARD_EXPECTED_SHA256`: SHA-256 of the exposed `guard_core.py`.
- `ORB_M53_GUARD_CHECKOUT`: existing non-symlink isolated M53 checkout.
- `ORB_M53_GUARD_GIT_EXECUTABLE`: existing non-symlink absolute Git executable.

The launcher computes the SHA-256 before launch, preserves the evidence file, then verifies the JSONL hash chain and requires the first record to be `activation` with matching version/hash, socket/subprocess rules loaded, PID, executable, and working directory. Missing activation evidence is a hard stop. `ORB_M53_GUARD_DISABLE` is forbidden. The target must be launched normally (not `python -S`) with the guard directory first in `PYTHONPATH`; no ORB-LIVE path is imported by the guard itself.

The guard’s only timing side effects are startup validation, one evidence fsync per security event, Python audit hooks, socket method replacement, and constrained Git child environments. It cannot alter M53 research parameters or arithmetic because it is outside the historical source closure, imports no project code, and has no access to M53 objects beyond intercepting forbidden process/network operations.
