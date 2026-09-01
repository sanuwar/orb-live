# Executable and Guard Pin Record

## Guard materialization

Both guard files were materialized from **immutable Git-object LF bytes** at merged main `e99aea9c50088c20ca8e4ee6ad7741d745ff9b56`, read with `git cat-file blob`, into a scratch guard directory outside the historical checkout.

| File | SHA-256 | Bytes | Materialization |
|---|---|---:|---|
| `guard_core.py` | `df625207220042ed88405b6f57dcbe357571f9fbe58df3a782ceebc39e4d47b9` | 23,251 | LF |
| `sitecustomize.py` | `34fdd6c487d7a53449deccf0be56d508b5dabf8e4503d50f9daad8b21f2313dc` | 620 | LF |

Each file was verified three times: the Git-object bytes were hashed before writing, compared against the frozen Phase 2B-2G manifest values, and the written file was re-read and hashed afterwards. All three agree for both files, and both objects were confirmed **LF-only** before writing.

This is why Git-object bytes were used rather than an ordinary checkout. The repository has `core.autocrlf=true` and no root `.gitattributes`, so a normal Windows checkout materializes these files with CRLF endings and different digests (`ac0938d1…` / 23,749 and `73f9620d…` / 638). Those CRLF forms are **not** accepted by the frozen LF materialization policy. Reading the immutable object makes the launcher's trusted external pre-launch check well defined, because "the reviewed guard commit" contains LF bytes regardless of any checkout's EOL configuration.

## External pre-launch verification

The frozen manifest requires `external_pre_launch_verification_required: true`, and the gate enforces that the manifest declares it. The verification performed here — hashing the exposed guard files with a tool that is not the guarded interpreter, and comparing against the values recorded in the reviewed guard commit — is exactly that check, performed for the first time against pinned values that now exist. Phase 2B-2N required this check but recorded no values to compare against; Phase 2B-2G pinned them, and this phase executes the comparison.

## Shadow audit

A guard is only meaningful if the intended pair is the pair that would load. Two independent conditions were proven:

- The historical checkout contains **zero** occurrences of `sitecustomize.py` and **zero** of `guard_core.py` outside `.git`, so it cannot shadow either module on `PYTHONPATH`.
- The guard directory is **outside the historical checkout in both directions** — neither contains the other, verified on resolved paths.

## Guard was not activated

The guard was materialized and hashed. It was **not activated, not imported, and not executed**, and no `ORB_M53_GUARD_*` variable was set. **No test involved a real network connection**; nothing in this phase opened a socket. Activation belongs to a separately authorized M53 run, not to a materialization workstream.

## Executable content pins

Both executables a later M53 run would use were resolved and pinned by **file-byte content**, not by path.

| Executable | Resolved basename | Content SHA-256 | Content bytes | Requested path equals resolved target |
|---|---|---|---:|---|
| Python | `python.exe` | `0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14` | 274,424 | yes |
| Git | `git.exe` | `11ce9bfcb40927767c474326ee87477079e1cc24a9782e69936b3351b6c3c205` | 4,238,208 | **no** |

The Git entry is the case the merged guard policy anticipates: the requested path differs from the resolved content target, because the Git executable on `PATH` is reached through a toolchain indirection. The reviewed policy resolves such an indirection and pins the **target's bytes** rather than refusing it, since a virtual-environment or toolchain interpreter is normally reached this way and refusing one would block a legitimate launch without adding protection. Content, not path shape, is the control — and the distinction between requested path and resolved target is recorded rather than collapsed.

Both targets were confirmed to be existing regular files; a missing, ambiguous, or non-regular target would have stopped the phase with `BLOCKED_GUARD_OR_EXECUTABLE`.

**Neither executable was used to run M53.** No package was installed, removed, or updated, and no environment was modified.

## Publication-safety of this record

Only resolved basenames, content digests, and byte counts appear here. No absolute path, directory, username, or environment value is recorded, in this document or in the committed gate evidence.
