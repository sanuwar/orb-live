# M53 Network Gate Readiness Decision

## READY_FOR_INDEPENDENT_REVIEW

The sole Phase 2B-2A `BLOCKED_CACHE_OR_ISOLATION` execution blocker now has a concrete, committed, test-covered **process-scoped** guard design ready for separate review. This label authorizes neither M53 nor cache materialization.

A later workstream must independently review the guard commit/blob/SHA-256, materialize the historical checkout, supply a new evidence directory, perform the **trusted external pre-launch hash check over both guard files**, pin the Python and Git executables by content hash, launch normally through an isolated `PYTHONPATH`, verify `guard_ready` evidence rather than `activation` alone, run `verify_guard_run()` afterwards, and separately authorize any M53 invocation.

## Compatibility basis

Static only. The historical M53 source path requires only the two exact local Git provenance vectors documented in `SUBPROCESS_ALLOWLIST.md`; the guard covers both and no additional static descendant is needed. The M53 pinned commit/materialization specification is untouched, the guard is outside the 30-file historical closure, and `generated_at` remains the sole prospectively excluded M53 field recorded by Phase 2B-2A.

**No runtime M53 compatibility is claimed.** No M52 or M53 module was imported, executed, or observed in this workstream, and no regenerated M53 artifact was created or observed. Compatibility is the static source closure plus the guard's own tests — nothing more. Runtime overhead and CPython/native limitations are recorded in `GUARD_DESIGN_AND_ACTIVATION.md` and `NETWORK_THREAT_MODEL.md`.

## Independent adversarial review (Phase 2B-2N, second pass)

An independent review of the first implementation found and corrected the following material defects on this branch; deterministic adversarial tests were added for each.

1. `sitecustomize.py` was outside the frozen integrity check — only `guard_core.py` was hashed. Both files are now hashed, pinned, verified, and recorded on every evidence record.
2. Nothing verified which `guard_core`/`sitecustomize` actually loaded, so a module in the historical checkout, the current directory, `site-packages`, or another `PYTHONPATH` entry could shadow the intended pair. `activate()` now proves the sibling shim ran, requires the guard directory to be the first `PYTHONPATH` entry, and requires it to be outside the checkout. Because self-verification cannot be complete, the trusted external pre-launch hash check is now a documented launcher requirement.
3. The cleared environment set omitted eight of the sixteen names in the Phase 2B-2A credential/proxy list, including every lowercase proxy spelling and the `ALPACA_*` aliases. The set now covers that list plus the `FTP_PROXY` and APCA endpoint aliases, and `pop` replaced `del` so Windows case-folded names cannot raise mid-clear.
4. `executable_sha256` hashed the **path string**, not the executable. Fields are now `executable_content_sha256` (file bytes) and `executable_path_sha256` (path), and `ORB_M53_GUARD_GIT_SHA256` / `ORB_M53_GUARD_PYTHON_SHA256` content pins are required and verified before target import.
5. Network denial had gaps: the audit list named two events CPython does not raise (`socket.send`, `socket.gethostbyname_ex`) while omitting real ones (`socket.__new__`, `socket.bind`, `socket.sendmsg`, `socket.gethostbyaddr`), and `sendall`/`sendmsg`/`sendfile`/`dup`/`makefile`/`socketpair` were unpatched. Internet socket **construction** is now denied outright, remaining `socket.*` audit events are denied by prefix so future events fail closed, and the send-family monkeypatches remain because CPython raises no audit event for `send`/`sendall`.
6. Descendant denial omitted `os.exec` and `os.startfile`, both of which launch or replace a process image without passing through `subprocess.Popen`. Those, plus `os.fork`, `os.forkpty`, and `pty.spawn`, are now denied, and an approved Git vector may no longer carry `preexec_fn`, `pass_fds`, `startupinfo`, `creationflags`, or any unrecognised child-process option.
7. The `activation` record asserted `socket_rules_loaded=true` and `subprocess_rules_loaded=true` **before** any control was installed. Activation now reports `false`, and a separate affirmative `guard_ready` record is written only after every hook and monkeypatch is installed.
8. The verifier only checked the chain. `verify_guard_run()` now additionally fails on an absent file, a truncated record, a missing `guard_ready`, a `guard_failure`, any `denied_*` event, or a missing terminal `normal_interpreter_exit`.
9. Documentation overstated the guard. The hash chain is now qualified as tamper-evident but not authenticated, the activation token is described as a configuration handshake rather than a secret, "process-scoped guard" is used throughout in place of any sandbox implication, and the hostile-in-process-target limit is stated explicitly.

## Remaining gates

Independent review, merge, a separate operator authorization, fresh pre-run verification of source/materialization, the original-M52 handoff, the parameter digest, scratch-cache integrity, cleared credentials, the external guard-file hash check, and post-run evidence verification. None of them is satisfied by this package.
