# Subprocess Allowlist

Static audit basis: historical commit `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865`, entrypoint `scripts/research_momentum_risk_control.py`, and the Phase 2B-2A 30-file source closure. The M53 entrypoint has exactly two `subprocess.run` calls. The M53 module has none. No `os.system`/spawn/exec, multiprocessing, ctypes, direct socket, async networking, requests, httpx, urllib, broker client, shell=True, dynamic command-line network tool, or native-network call was found on the statically declared M53 path. Indirect closure code must remain subject to the network guard; native bypass remains a residual limitation.

Only these source argv vectors are approved:

1. `git rev-parse HEAD`
2. `git status --porcelain -- src/cross_sectional_momentum_research.py src/momentum_risk_control_research.py scripts/research_momentum_risk_control.py`

The match is exact and positional, on a list or tuple only. A string command, an extra or reordered argument, a differing case (`head` for `HEAD`), a trailing empty argument, an inserted `-c` override, a different subcommand, and any remote or transport subcommand (`fetch`, `ls-remote`, `clone`, `push`, `pull`) are all denied and logged.

## What an approved child actually receives

The wrapper accepts those literal vectors only and then **overwrites** rather than trusts the caller's request:

- `shell` is forced to `False`, and a truthy `shell` denies before that.
- `executable` is forced to the pinned absolute Git path, whose **file-byte** SHA-256 was verified against `ORB_M53_GUARD_GIT_SHA256` at activation. A caller-supplied `executable` other than that exact path denies.
- `cwd` is forced to the resolved checkout, and a caller `cwd` that does not resolve to the checkout — including `None` — denies before that.
- `close_fds` is forced to `True`.
- `env` is replaced with `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=<checkout>/.git/info/empty-global-config`, `GIT_ATTR_NOSYSTEM=1`, and, on Windows only, `SystemRoot` for loader operation. Nothing else is inherited: no `PATH`, no proxy variable, no credential variable, no `GIT_*` override supplied by the caller. The controlled global-config file must exist in the checkout and be non-symlink, or the child is denied.
- `preexec_fn`, `pass_fds`, `startupinfo`, `creationflags`, `restore_signals`, `user`, `group`, `extra_groups`, `umask`, `process_group`, and any keyword outside the recognised set are denied as `unsupported_child_process_option`. This closes the path where an otherwise-exact Git vector smuggles arbitrary parent-side code or inherited descriptors into the child.

Remote, transport, config override, hook, alias, dynamic lookup, and all near-match argv are denied and logged with argument count and digest only. If the executable cannot be pinned and content-verified this way, execution remains blocked.

## Everything else

Every other descendant vector is denied before launch: `subprocess.run`/`Popen`/`call`/`check_output` (including a saved pre-patch `Popen` reference, caught by the audit hook), `shell=True`, `os.system`, the whole `os.exec*` family, `os.spawn*`, `os.posix_spawn`, `os.startfile`, `os.fork`, `os.forkpty`, `pty.spawn`, and `multiprocessing.Process.start`. `os.exec*` and `os.startfile` matter specifically because they never pass through `subprocess.Popen` at all.
