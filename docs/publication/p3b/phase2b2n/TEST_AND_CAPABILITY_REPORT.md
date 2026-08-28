# Test and Capability Report

Focused test command: `python -m pytest -q tests/test_publication_m53_network_guard.py`.

Result: **25 passed in 10.48s** on Windows CPython 3.12. Tests use only pytest temporary directories and temporary local Git repositories. No test resolves a real DNS name, connects to a real host, or sends a packet: controlled `.invalid` values are intercepted by the guard’s monkeypatched DNS/socket layer before an operating-system networking operation.

The suite covers startup-before-target import; normal no-network completion; missing, existing, relative, and absent-parent evidence destinations; invalid token/hash; DNS, connect, connect_ex, UDP sendto, and urllib HTTP denial; arbitrary/shell/child-Python/PowerShell/cmd/curl descendants; exact local Git rev-parse and status; near-match/config/alias/hook Git denial; wrong cwd; secret/raw-cache/username redaction; evidence tamper detection; no project import in guard source; and repeat-run structural determinism except timestamp/PID/hash-chain fields.

Capability result: `READY_FOR_INDEPENDENT_REVIEW`. Supported CPython supplies pre-operation audit events and mutable Python socket/subprocess surfaces used here; the temporary local Git test proves absolute Git pinning and controlled child environment on this platform. This is not a claim that native bypass code or a different interpreter is contained; those remain launcher-review and platform-capability preconditions.
