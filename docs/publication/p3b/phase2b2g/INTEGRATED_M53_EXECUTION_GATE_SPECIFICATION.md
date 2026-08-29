# Integrated M53 Execution-Gate Specification

## What the gate is

`scripts/publication/m53_execution_gate.py` is a deterministic, verification-only preflight. It consumes one explicit JSON manifest plus an optional set of filesystem inputs, produces one leak-free evidence record, and ends with exactly one decision.

It is **not** a launcher. It contains no code path that imports, executes, launches, simulates, regenerates, or fetches anything. It starts no subprocess, opens no socket, and imports no ORB-LIVE project module — not even to read a constant. Every value it needs about frozen source, parameters, inputs, caches, guard, and output arrives through the committed manifest, through the `FROZEN_REFERENCE` transcription of the merged Phase 2A/2B records, or through hashing bytes it was pointed at.

## Design commitments

**Verification-only by construction, not by convention.** `execution_authorized` and `m53_executed` are assigned the literal Python `False` at their only two assignment sites in the module. No option, argument, manifest field, environment variable, or profile can change either. `tests/test_publication_m53_execution_gate.py` asserts this by walking the module's own AST rather than trusting the prose.

**Absence is failure, not a skipped check.** Supplying no historical checkout does not mean "the checkout is fine"; it means the gate blocks on materialization. Every filesystem input is optional to *pass*, and mandatory to *pass the gate*.

**Paths are relative, contained, and platform-neutral.** Every manifest path must be a relative POSIX path: no backslash separators, no absolute or drive-qualified form, no UNC or device path, no `.` or `..` segment, no empty segment, and no Windows reserved device name (`NUL`, `CON`, `COM1`, …). Row identities must not collide when case-folded, because on a case-insensitive filesystem two such rows name one file and the closure would not contain the distinct number of files it declares. Behind that rule, each resolved target is confirmed to lie inside the root it was resolved against. Both layers exist because a manifest that could name a file outside the isolated checkout would defeat the isolation this gate exists to enforce.

**Every value is type- and range-checked.** String lists are checked element by element for type, emptiness, and duplicates; byte counts must be positive; identity strings must be non-blank; digests must be canonical lowercase hex of the right length. A non-string entry in a string list previously reached path arithmetic and raised.

**Unexpected failure is still a decision.** Any internal exception is caught and turned into a `BLOCKED_EVIDENCE_CONFIGURATION` record carrying an `internal_error` finding with **only the exception type name** — never its message, which could contain a filesystem path. There is no input for which the gate produces a traceback and no record, and none for which it produces a partially successful one.

**Manifest driven, reference reconciled.** The manifest is schema-checked (required fields, exact types, unknown fields rejected, duplicate JSON keys rejected, duplicate row identities rejected, `NaN`/`Infinity` rejected, booleans rejected where integers are required, unknown schema versions rejected). A manifest declaring the real `M53_PHASE2B2A` profile is then reconciled field by field against `FROZEN_REFERENCE`, which is transcribed from the merged Phase 2A/2B-1/2B-2A/2B-2N records. A synthetic test manifest declares `SYNTHETIC_TEST_ONLY` and is exempt from that reconciliation — and its profile is recorded on every evidence record it produces, so a synthetic result can never be presented as a real one.

**Deterministic and leak-free evidence.** The record carries no timestamp, no absolute path, no username, no credential, no raw market data, and no cache content. Supplied paths appear only as SHA-256 digests of their normalized text. Two runs over identical inputs therefore produce byte-identical evidence, and a test asserts exactly that.

## The seven check categories

### 1. Source closure and materialization

Reconciles the manifest's 30-file closure against the reviewed Phase 2B-2A closure: the historical commit must be `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865`, the closure must contain exactly 30 files, and exactly 2 of them must declare `crlf` materialization. The closure is **reused, not recreated** — the manifest's rows were parsed mechanically out of `docs/publication/p3b/phase2b2a/M53_SOURCE_CLOSURE_AND_MATERIALIZATION.md` rather than retyped.

Each row is checked for internal materialization consistency, which is a real invariant rather than a restatement. Historical Git blobs in this closure are LF-only, so an `lf` row must materialize to exactly the Git-object bytes and digest, and a `crlf` row must materialize to strictly *more* bytes with a *different* digest. A row claiming `lf` while declaring a different materialized hash fails; so does a `crlf` row that does not grow.

When a checkout is supplied, every one of the 30 files is hashed on disk and must equal its declared materialized digest and size. Absent, ambiguous, unreadable, or outside-the-checkout files fail. "Ambiguous" is keyed on a resolved location differing from the literal one, which covers symlinks, Windows directory junctions, and other reparse points, and covers a linked **parent directory** as well as a linked leaf file.

### 2. M52 handoff

Only the original frozen historical M52 artifact is eligible: SHA-256 `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9`, 34,124 bytes, at the wrapper's fixed path. The M52 classification must remain exactly `EXACT_BYTE_REPRODUCTION_FAIL` plus `RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH`; softening or dropping either label fails.

The manifest must declare `regenerated_artifact_eligible: false`. Because the Phase 2B-1 regenerated M52 artifact is scratch-only diagnostic evidence outside this repository, it is excluded by rule rather than by hash: **any** handoff digest other than the frozen historical one is refused. That is strictly stronger than a denylist, since it also refuses an artifact nobody anticipated.

### 3. Frozen parameters

The manifest must declare 49 records, 5,018 bytes, and digest `7c2b2da4168333c198e44bb573655d995c129931ac2ae8b973a0985b77e4a0ad`. Any one of the three differing fails. The gate checks these mechanically against the merged Phase 2B-2A record; it does not re-derive the 49 lines, and this document does not claim the parameters are verified beyond that mechanical reconciliation succeeding.

### 4. Caches and isolation

The manifest's three cache trees must match the merged Phase 2A/2B counts, byte totals, and aggregate digests exactly: `daily_bar` 942 files / 177,025,917 bytes / `0698d51e…`, `openfigi` 5,243 / 786,008 / `b2ad68c9…`, `sec_nport` 29 / 25,130,891 / `889fb5dc…`. `fetch_missing` must be `false`, original exposure must be forbidden, a scratch root must be required, and the preserved Phase 2B-1 scratch evidence must be declared untouched.

When a scratch root is supplied, each role subtree is re-measured with the Phase 2A aggregate algorithm — SHA-256 over LF-joined, lexicographically sorted lines `SHA256(relative_path_utf8):byte_size:SHA256(file_bytes)`, unspaced as Phase 2B-1 confirmed the frozen values require. File bytes are hashed opaquely and never decoded, parsed, or retained. A symlinked file inside a scratch tree makes the tree unmeasurable and fails.

Original-cache exposure is checked structurally: the scratch root must not sit inside the repository working tree, must not contain or be contained by any declared original cache root, and the historical checkout must not sit inside the repository working tree.

**This gate does not copy any cache.** It verifies scratch copies a future launcher would have made. No scratch copy exists in this PR, which is one reason the current decision is blocked.

### 5. Guard and executables

Uses only the guard merged through PR #383, at `scripts/publication/m53_network_guard/`. The gate requires: the scope string `PROCESS_SCOPED_PYTHON_GUARD` (declaring it an OS sandbox fails), the trusted external pre-launch verification flag, `guard_ready` as the required readiness event, `normal_interpreter_exit` as the required terminal event, `verify_guard_run` as the required post-run verifier, the exact activation handshake token, all eight `ORB_M53_GUARD_*` variables, `ORB_M53_GUARD_DISABLE` declared forbidden and absent from the live environment, and all twenty credential/proxy names in the cleared set. Omitting any single one of those names fails, individually.

Both guard files are pinned by content. The guard directory must be a real directory outside the historical checkout in both directions, and each exposed file must hash to its declared materialized digest.

Python and Git must both be pinned by executable **file bytes**. A symlinked interpreter is resolved and its target's bytes pinned rather than refused, because a virtual-environment interpreter is normally a symlink and refusing one would block a legitimate launch without adding protection — content, not path shape, is the control. An unsupplied, absent, unresolvable, or unreadable executable fails.

### 6. Output criterion

The complete expected artifact is `a3d3ebbd62f45331115a74583f7f8c378e9f2e786fb693b12865fff8860122c1` / 28,715 bytes; `/generated_at` is the sole canonical exclusion; the expected canonical payload is `9747dec356d11d8cdbcb30d3ac3838fe962dad85674c0c4c635029c094b8a412` / 22,055 bytes. Adding an exclusion, removing the only one, changing either digest or size, making the canonical payload not smaller than the complete artifact, or making the two digests identical all fail.

`raw_and_canonical_reported_separately` must be `true`. **Exact-byte reproduction is not weakened here**: raw-byte equality and canonical-payload equality remain two separate classifications, and a semantic match never conceals an exact-byte failure.

### 7. Evidence configuration

The frozen command takes no argument after the module name, so any `argv_after_module` entry fails. The guard evidence log basename is fixed at `m53-network-guard.jsonl`. The evidence directory must be a real, absolute, non-symlink directory, and the evidence log must **not** already exist there. The M53 output artifact must not already exist in the checkout. A pre-existing execution or output path is a failure, never something to overwrite.

## Reparse-point and privilege note

Creating a symlink on Windows requires Developer Mode or elevation, and creating a directory junction requires an external tool, so three symlink tests skip on ordinary accounts. The property they probe — a resolved location differing from the literal one — is covered on **every** platform by tests that redirect `realpath` for a leaf file, a parent directory, a scratch root, a guard directory, and an aliased original cache root. Junction and reparse behaviour is therefore tested everywhere, not only where symlinks can be created.

A **hard link** is deliberately not treated as ambiguous: it is indistinguishable from an ordinary file and has identical content, and content is what the gate verifies.

## Decision precedence

When several categories block, the decision names the earliest in this fixed order, so the same findings always yield the same decision:

`source_or_materialization` → `input_artifact` → `parameters` → `cache_or_isolation` → `guard_or_executable` → `output_criterion` → `evidence_configuration`

Source and input precede the rest because a wrong source or a wrong historical input invalidates everything downstream. Every finding in every category is still reported in full; precedence selects only the label.

## What a passing gate would and would not mean

A `READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION` decision would mean: the frozen source, materialization, historical input, parameters, cache evidence, isolation structure, guard configuration, executable pins, output criterion, and evidence paths were all mechanically verified against the merged reviewed records at the moment the gate ran.

It would **not** mean M53 has been reproduced, that anyone is authorized to run it, that the strategy is profitable or deployable, that the guard is an operating-system sandbox, that Git commits constitute formal preregistration, or that the reproduction is scientifically valid. Those limits are stated in `EXECUTION_AUTHORIZATION_BOUNDARY.md` and `REMAINING_REPRODUCIBILITY_LIMITATIONS.md`.
