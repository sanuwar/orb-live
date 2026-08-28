# Subprocess Allowlist

Static audit basis: historical commit `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865`, entrypoint `scripts/research_momentum_risk_control.py`, and the Phase 2B-2A 30-file source closure. The M53 entrypoint has exactly two `subprocess.run` calls. The M53 module has none. No `os.system`/spawn, multiprocessing, ctypes, direct socket, async networking, requests, httpx, urllib, broker client, shell=True, dynamic command-line network tool, or native-network call was found on the statically declared M53 path. Indirect closure code must remain subject to the network guard; native bypass remains a residual limitation.

Only these source argv vectors are approved:

1. `git rev-parse HEAD`
2. `git status --porcelain -- src/cross_sectional_momentum_research.py src/momentum_risk_control_research.py scripts/research_momentum_risk_control.py`

The wrapper accepts those literal vectors only, requires `shell=False`, exact checkout working directory, and rewrites execution to the pinned absolute Git executable. Its child environment is reduced to `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=<checkout>/.git/info/empty-global-config`, `GIT_ATTR_NOSYSTEM=1`, and Windows `SystemRoot` only when required for loader operation. The controlled global-config file must exist and be non-symlink. Remote, transport, config override, hook, alias, dynamic lookup, and all near-match argv are denied and logged. If the executable cannot be pinned this way, execution remains blocked.
