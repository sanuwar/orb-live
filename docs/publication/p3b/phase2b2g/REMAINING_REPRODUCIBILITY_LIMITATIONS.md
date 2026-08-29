# Remaining Reproducibility Limitations

Every limitation below survives a `READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION` decision. None is closed by this package, and none should be described as closed elsewhere.

## 1. The historical environment is unknown and unrecoverable

The gate pins the source, parameters, input, caches, guard, executables, and output criterion. It does not pin the historical OS image, Python build, dependency resolution, clock, locale, filesystem behaviour, or process state, because no record of them exists. A later run records *its own* environment; it does not recreate the original one. A byte-exact result would therefore be evidence of a reproducible pipeline under a recorded environment, not proof that the environment was irrelevant.

## 2. Cache originality is not established

No committed hash exists anywhere in the repository for any raw cache file. The frozen aggregate digests fix the **current local state** so that mutation becomes detectable; they are not evidence that these bytes are the bytes the original runs consumed, nor of vendor-side integrity. This limit is unchanged from Phase 1 and is not upgraded by anything here.

## 3. The guard is process-scoped, not a sandbox

`scripts/publication/m53_network_guard/` denies Python-level network and descendant-process operations inside one CPython interpreter. It is not an OS sandbox, firewall, container, or machine-wide isolation. It does not contain native code (`ctypes`, a C extension, or a raw syscall), a deliberately hostile in-process target, anything outside the interpreter, or an already-open inherited connection. Its evidence chain is tamper-evident, not cryptographically authenticated: an actor with write access to the finished log can rewrite it into a self-consistent chain.

## 4. Guard self-verification is necessarily incomplete

A wholly substituted guard pair could supply its own matching expected digests, because the pinned values arrive through the same environment the substituted code reads. That is why the trusted external pre-launch hash check is a launcher precondition rather than a guard capability. This package pins the digests that check must compare against — Phase 2B-2N required the check but recorded no values — but the check itself still happens outside anything this repository executes.

## 5. Guard materialization depends on checkout configuration

The repository has `core.autocrlf=true` and no root `.gitattributes`, so an ordinary Windows checkout materializes both guard files with CRLF endings and different digests. The manifest pins the immutable LF Git-object bytes and requires LF materialization. A launcher that exposes CRLF bytes will fail the gate — correctly, because "the reviewed guard commit" contains LF bytes and the external check must be well defined against it. This is a real operational constraint on any future launcher, not a theoretical one.

## 6. M52 remains an exact-byte reproduction failure

Selecting the original historical M52 artifact as the handoff preserves the copied `/baseline_parity` values and makes the M53 criterion well defined. It does **not** repair, erase, or reclassify M52's `EXACT_BYTE_REPRODUCTION_FAIL`. The M53 chain therefore rests on an upstream artifact that this project could not reproduce byte for byte, and any M53 result must be read with that in view.

## 7. Frozen parameters are reconciled, not re-derived

The gate checks the 49-record count, the 5,018-byte serialization, and the `7c2b2da4…` digest against the merged Phase 2B-2A record. This phase did not re-derive the 49 lines from historical source. The parameters are "verified" only in the sense that the mechanical reconciliation succeeded.

## 8. The static closure is a source-load closure, not an execution claim

The 30-file closure is the static Python import closure of the entrypoint at the historical commit. It is not a claim about what actually executes, and indirect closure members remain subject to the guard precisely because static analysis cannot settle runtime behaviour. No runtime M53 compatibility has been observed, because M53 has never been run under the guard.

## 9. The gate proves configuration, not science

A passing gate establishes that inputs match recorded values at the moment it ran. It does not establish that the research design is sound, that the result is statistically meaningful, that multiplicity was handled, that the sample suffices, or that the strategy is profitable, deployable, or production-ready. Reproducibility and validity are different properties, and only the first is in scope here.

## 10. Git commits are not preregistration

The criterion in this package is frozen before any regenerated M53 artifact exists, which makes it prospective *for reproducibility evaluation*. Time-stamped version control is not a preregistration registry, carries no independent custody, and must never be described as one.

## 11. Time-of-check to time-of-use

The gate verifies state at the instant it runs. Nothing prevents a change between the gate pass and the invocation. That is why the protocol requires a fresh gate pass immediately before invocation, pre- and post-command cache integrity checks, and post-run source and parameter re-verification — mitigations that narrow the window rather than eliminate it.

## 12. Hard links and in-flight modification are not detected

A hard link is indistinguishable from an ordinary file and carries identical content, so the gate does not treat one as ambiguous; content, not inode identity, is what it verifies. Separately, a file could change between the moment the gate classifies a path and the moment it hashes it, and a scratch tree could change during the walk. The gate records a snapshot and the protocol requires pre- and post-command cache integrity checks around any later run; neither eliminates the window.

## 13. Evidence is deterministic and tamper-evident, not authenticated

The gate holds no key and performs no attestation. Identical inputs produce byte-identical evidence, which makes accidental divergence detectable, and the `--output` write stages and replaces so a reader never sees a half-written record. `os.replace` is atomic within a filesystem; it is not a durability guarantee across power loss, and nothing here claims one. Anyone who can write to the evidence file can replace it wholesale.

## Publication interpretation

What this gate supports is one narrow methodological claim:

> A time-stamped, version-controlled workflow can preserve and mechanically enforce source, data, parameter, output, provenance, and safety boundaries **before** a historical reproduction is authorized.

The evidence for that claim is the gate itself: an explicit manifest of frozen values, reconciled mechanically against merged records; a fail-closed decision procedure with a fixed precedence; deterministic leak-free evidence; and a statically provable inability to execute the thing it gates. That claim is about *process*, and it is the only claim this package makes.
