# Guard Design and Activation

`sitecustomize.py` imports only `guard_core.activate()`. It is loaded by the standard Python `site` phase before a requested target module. Unlike normal `sitecustomize` error handling, the shim uses `os._exit(78)` when activation raises; CPython must not continue to a target after an activation failure. This is a **process-scoped CPython guard**, not an operating-system sandbox.

## Launcher-supplied configuration

A future launcher must use an isolated guard directory that is outside the historical checkout and set these absolute, validated values before starting Python:

- `ORB_M53_GUARD_LOG`: fresh `m53-network-guard.jsonl` below an existing non-symlink temporary evidence directory.
- `ORB_M53_GUARD_TOKEN`: exactly `M53_NETWORK_DENIAL_GUARD_V1`.
- `ORB_M53_GUARD_EXPECTED_SHA256`: SHA-256 of the exposed `guard_core.py` file bytes.
- `ORB_M53_GUARD_EXPECTED_SITECUSTOMIZE_SHA256`: SHA-256 of the exposed `sitecustomize.py` file bytes.
- `ORB_M53_GUARD_CHECKOUT`: existing non-symlink isolated M53 checkout.
- `ORB_M53_GUARD_GIT_EXECUTABLE`: absolute path to an existing Git executable.
- `ORB_M53_GUARD_GIT_SHA256`: SHA-256 of that Git executable's **file bytes**.
- `ORB_M53_GUARD_PYTHON_SHA256`: SHA-256 of the running interpreter's **file bytes** (`sys.executable`).

`ORB_M53_GUARD_DISABLE` is forbidden and its mere presence is an activation failure. The target must be launched normally (not `python -S`) with the guard directory first in `PYTHONPATH`; no ORB-LIVE path is imported by the guard itself.

## Bootstrap and import integrity

Both guard files are covered by the frozen integrity check: `guard_core.py` and its sibling `sitecustomize.py` are hashed by content and compared against the two pinned values before any control is installed. In addition, `activate()` proves the intended pair actually ran:

- `guard_core.__file__` must not be a symlink, and its directory must contain the non-symlink sibling `sitecustomize.py`;
- the module registered in `sys.modules["sitecustomize"]` must resolve to exactly that sibling file, so a `sitecustomize` shadow in the historical checkout, the current directory, `site-packages`, or another `PYTHONPATH` entry cannot be the shim that ran;
- the guard directory must be the **first** `PYTHONPATH` entry, which is what makes the intended `guard_core` win resolution;
- the guard directory must be outside the historical checkout in both directions.

**Self-verification alone cannot establish bootstrap integrity.** A wholly substituted guard pair could also supply its own matching expected digests, because the pinned values arrive through the same environment the substituted code can read. The launcher must therefore additionally perform a **trusted external pre-launch hash check**, outside the guarded interpreter, covering both guard files:

1. Compute SHA-256 of `<guard_dir>/guard_core.py` and `<guard_dir>/sitecustomize.py` with a tool that is not the guarded process.
2. Compare both against the values recorded in the reviewed guard commit for this branch.
3. Only on an exact match, export them as `ORB_M53_GUARD_EXPECTED_SHA256` / `ORB_M53_GUARD_EXPECTED_SITECUSTOMIZE_SHA256` and launch.
4. After the run, confirm every evidence record carries those same two digests.

Steps 1–3 are a launcher precondition, not something the guard can prove about itself.

## Executable provenance

`ORB_M53_GUARD_GIT_SHA256` and `ORB_M53_GUARD_PYTHON_SHA256` are required, and both are checked against the hash of the executable **file bytes** before any target import; an absent, malformed, or mismatched pin is an activation failure. A symlinked executable is resolved and its target's bytes are pinned rather than refused, because a venv or toolchain interpreter is normally a symlink and refusing one would block a legitimate launch without adding protection; content pinning, not path shape, is the control. Evidence fields are named for what they contain: `executable_content_sha256` and `git_executable_content_sha256` hash file bytes, while `executable_path_sha256` hashes only the path string and exists solely to correlate paths without disclosing them.

## Activation versus readiness

Activation and readiness are separate records. The `activation` record is written before any control is installed and reports `socket_rules_loaded=false` / `subprocess_rules_loaded=false`; it proves only that configuration and bootstrap verification were accepted. The affirmative `guard_ready` record is written **only after** the audit hook, the `subprocess.Popen` wrapper, every socket monkeypatch, and the `multiprocessing` block are all installed. A launcher must require `guard_ready`, never treat `activation` as a ready state, and use `verify_guard_run()` rather than `verify_evidence_log()` alone.

## Activation token

`ORB_M53_GUARD_TOKEN` is a **configuration handshake**, not a secret. Its value is committed in this repository. It exists to make an accidental or partially configured launch fail closed, and it grants no authenticity property whatsoever.

## Side effects

The guard's only timing side effects are startup validation (including two guard-file hashes and two executable hashes, computed once and cached), one evidence fsync per security event, Python audit hooks, socket method replacement, and constrained Git child environments. It cannot alter M53 research parameters or arithmetic because it is outside the historical source closure, imports no project code, and has no access to M53 objects beyond intercepting forbidden process/network operations. This is a static compatibility statement; no M53 runtime behaviour has been observed.
