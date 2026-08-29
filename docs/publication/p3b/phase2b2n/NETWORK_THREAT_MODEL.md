# Network Threat Model

## What this guard is

A **process-scoped CPython guard**. It denies Python-level network and descendant-process operations inside one interpreter before the corresponding operating-system call is made. It is not an OS sandbox, a firewall, a container, or machine-wide isolation, and it is not claimed to be.

The design target is **accidental Python-level network access** on the historical M53 path: a cache miss that silently falls through to a fetch branch, a proxy variable that redirects a library, a helper that shells out. Against that class it is intended to be complete and fail-closed.

## Network denial

Two layers act before the operating system.

**Audit-hook layer (primary).** `socket.__new__` is denied for every address family except `AF_UNIX`, so no internet socket object can be constructed at all — including one rewrapped from an inherited descriptor, one created by `asyncio`, and one created through the C `_socket` type rather than the Python `socket` wrapper. Every other `socket.*` audit event is denied **by prefix**, so a socket audit event introduced by a future CPython release fails closed rather than passing silently. `urllib.Request` and the `ftplib`/`smtplib`/`imaplib`/`poplib`/`nntplib`/`telnetlib`/`webbrowser` connect events are denied by name.

**Monkeypatch layer (defence in depth).** `connect`, `connect_ex`, `bind`, `send`, `sendall`, `sendto`, `sendmsg`, `sendfile`, `dup`, and `makefile` are replaced on `socket.socket`, and `getaddrinfo`, `gethostbyname`, `gethostbyname_ex`, `gethostbyaddr`, `getnameinfo`, `create_connection`, `create_server`, and `socketpair` are replaced on the `socket` module. This layer is not redundant: **CPython raises no audit event for `socket.send`/`sendall`**, so an already-open socket object would otherwise remain writable from Python. `socketpair` is replaced because on Windows it is implemented over loopback.

HTTP/HTTPS libraries (`urllib`, and anything else that descends to sockets) are stopped at these layers; no request reaches a resolver or a transport.

Proxy and credential variables are cleared as defence in depth, never as the network control.

## Descendant-process denial

All `subprocess.Popen` construction is denied unless it matches one of the two exact historical Git vectors; the audit hook denies `subprocess.Popen` independently, so even a reference to the pre-patch `Popen` class captured before the wrapper is caught. `os.system`, `os.posix_spawn`, `os.spawn`, **`os.exec`** (the entire `os.exec*` family, which replaces the process image and never reaches `subprocess`), **`os.startfile`** (all arities), `os.fork`, `os.forkpty`, and `pty.spawn` audit events are denied, and `multiprocessing.BaseProcess.start` is blocked. Descendant Python, PowerShell, cmd, curl, wget, and arbitrary executable attempts are therefore denied before launch. An approved Git vector may not carry `preexec_fn`, `pass_fds`, `startupinfo`, `creationflags`, `restore_signals`, `user`/`group`/`umask`/`process_group`, or any unrecognised child-process option, and its `env`, `cwd`, `shell`, `close_fds`, and executable are overwritten by the guard rather than taken from the caller.

Replacing a protected guard or proxy/credential environment variable through `os.putenv`/`os.unsetenv` triggers a denied event. A bypass variable, missing or mismatched token/hash/executable pin, invalid checkout or Git identity, failed bootstrap verification, or any evidence failure is an activation failure.

## Residual limits — what this does not protect against

- **Native code.** `ctypes`, a C extension, or any code that issues a raw syscall bypasses CPython's socket, audit, and subprocess surfaces entirely. Nothing here contains it.
- **A hostile in-process target.** The guard shares an address space with the code it guards. A target that deliberately attacks the guard — restoring saved socket methods from `guard_core._original_socket_methods`, setting the thread-local subprocess permit, or writing to a descriptor with `os.write` — is outside the design target. Audit hooks cannot be removed once installed, which raises the cost, but this is a mitigation, not containment. The static closure recorded in `SUBPROCESS_ALLOWLIST.md` is what supports the assumption that the M53 path is not hostile.
- **OS-level bypass.** Anything outside this interpreter: another process on the machine, a scheduled task, an already-open connection inherited by the operating system, a proxy configured at the system level.
- **Log authenticity.** See `EVIDENCE_LOG_SPECIFICATION.md`; the hash chain is tamper-evident, not cryptographically authenticated.
- **Interpreter substitution.** A different or embedded interpreter, or `python -S`, does not run the guard at all. Detecting that is the launcher's job, via the pinned `ORB_M53_GUARD_PYTHON_SHA256` and the required `guard_ready` evidence.

The future M53 launch therefore depends on the recorded static closure, an isolated `PYTHONPATH` with the guard directory first, no `-S`, the externally verified guard-directory hashes, and post-run `verify_guard_run()`. These are explicit launcher preconditions, not proof against every native-code escape.
