# Phase 2A Readiness Decision

**Evidence cutoff:** main at `20156b5dc420abd1ea735a699fe0b457f794f626`.

## Per-milestone status

| Milestone | Status | Basis |
|---|---|---|
| M48 | `READY_WITH_LIMITATIONS` | Both pins reproduce historically and at `main`; exact hash frozen. **Not JSON-only:** the full study `pickle.load`s M43 checkpoints, so it needs two cache trees and inherits deserialization risk. `--data-only` avoids that path but not the exact-output goal. Worktree-only |
| M49 | `READY_WITH_LIMITATIONS` | Both pins verify; exact hash frozen; but three cache trees including 3.39 GB and M43 pickles; worktree-only |
| M50 | `READY_WITH_LIMITATIONS` | Both pins verify; stage-0 hash frozen; **verification only, no regeneration proposed** — M50 never computed economics |
| M52 | `READY_WITH_LIMITATIONS` | Both pins verify historically; **one pin has drifted at `main`, so the historical worktree is mandatory**; exact hash frozen |
| M53 | `READY_WITH_LIMITATIONS` | All three pins verify historically and at `main`; exact hash frozen; must run after M52 |
| M43 | `BLOCKED_PENDING_WEAK_CRITERION_AND_SAFE_CACHE_SNAPSHOT` | Exact hash unavailable, but a **weak reconciliation criterion is now predeclared**. Real blockers: `--cache-dir` deletion exposure via `validate_cache_integrity`, and operator acceptance of the weak criterion. Pickle is **optional** via `--no-checkpoint` |
| M45 | `BLOCKED_PENDING_WEAK_CRITERION_AND_SAFE_CACHE_SNAPSHOT` | Same as M43 |
| M44, M46, M47 | `NOT_APPLICABLE` | Their evaluations are non-executable for statistical recovery (n=2, 20 trades in one period, n=0). They are **not** blocked members of the 11 candidates — they were never among them |

**No milestone is `READY_FOR_SEPARATE_PHASE2B_AUTHORIZATION` without limitations**, because the cache-exposure mechanism is unresolved for every candidate.

## Reconciled TI-01 to TI-11

| TI | Milestone | Phase-2B status |
|---|---|---|
| TI-01, TI-02, TI-03 | M43 | `BLOCKED_PENDING_WEAK_CRITERION_AND_SAFE_CACHE_SNAPSHOT` |
| TI-04 | M45 | `BLOCKED_PENDING_WEAK_CRITERION_AND_SAFE_CACHE_SNAPSHOT` |
| TI-05, TI-06, TI-07 | M48 | `READY_WITH_LIMITATIONS` (pickle-dependent for exact output) |
| TI-08 | M49 | `READY_WITH_LIMITATIONS` |
| TI-09 | M52 | `READY_WITH_LIMITATIONS` (historical worktree mandatory) |
| TI-10, TI-11 | M53 | `READY_WITH_LIMITATIONS` (after M52) |

**7 of 11 are `READY_WITH_LIMITATIONS`; 4 of 11 are `BLOCKED_PENDING_WEAK_CRITERION_AND_SAFE_CACHE_SNAPSHOT`. The candidate count remains 11.**

An earlier draft described TI-01–TI-04 as permanently blocked because no expected-output hash exists. **That was withdrawn.** The brief allowed weaker reconciliation where no hash was committed, and a predeclared criterion now exists in `EXPECTED_RESULT_HASH_FREEZE.md`. Their remaining blockers are tractable and specific: operator acceptance of the weak criterion, an authorized read-only minute-cache snapshot (because `validate_cache_integrity` can delete cache entries), and verified write-path containment.

DO-01 (M44), DO-02 (M46), and SN-01 (M47) are `NOT_APPLICABLE` for Phase-2B statistical recovery. They are **not** blocked members of the 11 — they were never candidates.

## Recommended Phase-2B execution order

The proposed order was **1. M52/M53 → 2. M48 → 3. M49 → 4. M43/M45**. Assessment: **keep positions 1–3, and drop position 4 entirely for now.**

| Position | Milestone | Rationale |
|---|---|---|
| 1 | **M52, then M53** | Strongest evidence: exact hashes, verified pins, JSON-only caches, no pickles. M53 must follow M52 because it reads the M52 result for baseline parity and hard-fails without it. M52's mandatory historical worktree also exercises the worktree mechanism on the best-instrumented milestone first |
| 2 | **M48** | Same JSON/exact-hash profile, single cache tree, and `--data-only` offers a genuine dry-run stage before any study runs |
| 3 | **M49** | Deferred by complexity, not risk of the wrong kind: three cache trees, 3.39 GB, and it loads M43 pickles. `--parity-only` should run first as its own gate |
| 4 | **M43, M45** | **Restored to the sequence**, contrary to an earlier draft that removed them. They run last because they need an authorized minute-cache snapshot and operator acceptance of the weak criterion — sequencing, not disqualification. `--no-checkpoint` makes their pickle use optional |

Two corrections to the ordering rationale. First, the "JSON versus pickle" framing does not survive the call-path audit: **M48's full study is itself pickle-dependent**, loading M43's checkpoints, so only M52/M53 are genuinely pickle-free. Second, **M43/M45's pickle use is optional** (`--no-checkpoint` skips both load and dump), while their **cache-deletion exposure is not** — `validate_cache_integrity` can unlink entries from whatever `--cache-dir` it receives. The order stands, but the dominant M43/M45 hazard is cache deletion, not deserialization.

## What Phase 2A established

1. **All 11 source pins reproduce at their generation commits**, in two evidence classes: **8 exact Git-blob matches, 3 CRLF-transform matches, 0 unexplained**. The three transform matches are consistent with working-tree-byte hashing but do not prove the historical checkout configuration, which Git does not record.
2. **The three apparent mismatches were explained by demonstration**, reproducing each expected hash exactly under a deterministic LF-to-CRLF transformation rather than by assertion.
3. **One pin has genuinely drifted at `main`** (M52's shared module, superseded by M53), making the historical worktree mandatory for M52.
4. **Five exact expected-result hashes are frozen** before any regeneration is authorized.
5. **Twelve cache trees are baselined** — 9,394 files, ~3.4 GB — with no mutation during hashing.
6. **Every regeneration CLI is cache-only by default**, a statically verifiable offline guarantee.
7. **Structural hazards found by call-path audit**: hardcoded output paths that overwrite tracked working-tree files and local gitignored artifacts (committed history is unaffected); `validate_cache_integrity`'s deletion capability on `--cache-dir`; M48's previously unrecorded pickle dependency on M43 checkpoints; and the undefined M52-to-M53 scratch handoff.
8. **M46's CLI surface is three `m46_*` scripts**, not the assumed naming convention — resolved by inspection rather than convention.

## What Phase 2A did not establish

1. **Nothing about historical cache integrity.** The new digests are `PRE_PHASE2_LOCAL_BASELINE_ONLY`.
2. **No statistical executability.** A verified source pin says nothing about whether a hypothesis can be tested. The 13 unresolved P3A decisions — six blocking all execution — remain untouched.
3. **No proof that any original run used a given source tree**, except where a committed manifest explicitly links them. For M43–M47 that linkage does not exist.
4. **No environment or dependency provenance.** No lockfile or container digest ties results to an environment.
5. **No authorized cache-exposure mechanism**, which blocks every candidate from actually starting — and for M43/M45 specifically, exposing the original minute cache is unsafe because `validate_cache_integrity` can delete from it.
6. **No defined M52-to-M53 artifact handoff.** Separate worktrees at different commits do not share generated results, and no copy is authorized.

## Standing safety state

`strategy.enabled=false`. M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL`, zero transactions, empty base and stress positions, ledger blob `ce8f022d1fc9f734042de4c08feef35b9de00465` — untouched by this milestone. **Phase 2B remains unauthorized.**
