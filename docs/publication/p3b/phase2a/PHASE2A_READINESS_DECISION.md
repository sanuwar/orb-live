# Phase 2A Readiness Decision

**Evidence cutoff:** main at `20156b5dc420abd1ea735a699fe0b457f794f626`.

## Per-milestone status

| Milestone | Status | Basis |
|---|---|---|
| M48 | `READY_WITH_LIMITATIONS` | Both source pins verify historically and at `main`; exact expected hash frozen; JSON caches; no output redirect, so worktree-only |
| M49 | `READY_WITH_LIMITATIONS` | Both pins verify; exact hash frozen; but three cache trees including 3.39 GB and M43 pickles; worktree-only |
| M50 | `READY_WITH_LIMITATIONS` | Both pins verify; stage-0 hash frozen; **verification only, no regeneration proposed** — M50 never computed economics |
| M52 | `READY_WITH_LIMITATIONS` | Both pins verify historically; **one pin has drifted at `main`, so the historical worktree is mandatory**; exact hash frozen |
| M53 | `READY_WITH_LIMITATIONS` | All three pins verify historically and at `main`; exact hash frozen; must run after M52 |
| M43 | `BLOCKED_FOR_PHASE2B` | No expected-output hash; pickle load **and** dump; no explicit output-to-generation-source linkage |
| M45 | `BLOCKED_FOR_PHASE2B` | Same three reasons |
| M44, M46, M47 | `BLOCKED_FOR_PHASE2B` | Same, and their instances are non-executable regardless (see below) |

**No milestone is `READY_FOR_SEPARATE_PHASE2B_AUTHORIZATION` without limitations**, because the cache-exposure mechanism is unresolved for every candidate.

## Reconciled TI-01 to TI-11

| TI | Milestone | Phase-2B status |
|---|---|---|
| TI-01, TI-02, TI-03 | M43 | `BLOCKED_FOR_PHASE2B` |
| TI-04 | M45 | `BLOCKED_FOR_PHASE2B` |
| TI-05, TI-06, TI-07 | M48 | `READY_WITH_LIMITATIONS` |
| TI-08 | M49 | `READY_WITH_LIMITATIONS` |
| TI-09 | M52 | `READY_WITH_LIMITATIONS` (historical worktree mandatory) |
| TI-10, TI-11 | M53 | `READY_WITH_LIMITATIONS` (after M52) |

**7 of 11 are `READY_WITH_LIMITATIONS`; 4 of 11 are `BLOCKED_FOR_PHASE2B`.**

This is a reduction from Phase 1's 11 `CANDIDATE_AVAILABLE_FOR_PHASE2`, and the two counts answer different questions. Phase 1 asked whether the caches exist — they all do, and that finding stands. Phase 2A asked whether a regeneration command can be constructed that provably stays offline, writes only to scratch, leaves caches untouched, uses frozen parameters, and has a success criterion. For TI-01 through TI-04 it cannot, principally because **no expected-output hash was ever committed for M43 or M45**.

DO-01 (M44), DO-02 (M46), and SN-01 (M47) remain `NOT_APPLICABLE` for statistical purposes and are additionally blocked on the same provenance grounds.

## Recommended Phase-2B execution order

The proposed order was **1. M52/M53 → 2. M48 → 3. M49 → 4. M43/M45**. Assessment: **keep positions 1–3, and drop position 4 entirely for now.**

| Position | Milestone | Rationale |
|---|---|---|
| 1 | **M52, then M53** | Strongest evidence: exact hashes, verified pins, JSON-only caches, no pickles. M53 must follow M52 because it reads the M52 result for baseline parity and hard-fails without it. M52's mandatory historical worktree also exercises the worktree mechanism on the best-instrumented milestone first |
| 2 | **M48** | Same JSON/exact-hash profile, single cache tree, and `--data-only` offers a genuine dry-run stage before any study runs |
| 3 | **M49** | Deferred by complexity, not risk of the wrong kind: three cache trees, 3.39 GB, and it loads M43 pickles. `--parity-only` should run first as its own gate |
| — | **M43, M45** | **Removed from the sequence.** Running them last does not address why they are blocked. Deferring is not a fix for having no expected-output hash |

One change to the proposed order's rationale is worth recording. The stated ordering assumed pickles are the dominant risk and JSON milestones the safe ones. Static inspection inverts part of that: **M43 and M45 are the only fully redirectable commands** (`--output-dir`, `--cache-dir`, `--checkpoint-dir`), while **M48, M49, M52, and M53 accept no output redirection at all** and overwrite their own committed manifests. The JSON group carries the more dangerous *write* profile; the pickle group carries the more dangerous *provenance and deserialization* profile. The order stands, but for partly different reasons than assumed.

## What Phase 2A established

1. **All 11 source pins verify at their generation commits** — no unexplained historical mismatch, no hard stop.
2. **The three apparent mismatches are line-ending artifacts**, demonstrated by reproducing each expected hash exactly under CRLF, not asserted.
3. **One pin has genuinely drifted at `main`** (M52's shared module, superseded by M53), making the historical worktree mandatory for M52.
4. **Five exact expected-result hashes are frozen** before any regeneration is authorized.
5. **Twelve cache trees are baselined** — 9,394 files, ~3.4 GB — with no mutation during hashing.
6. **Every regeneration CLI is cache-only by default**, a statically verifiable offline guarantee.
7. **Two structural hazards were found**: hardcoded output paths that overwrite committed manifests, and pickle-writing checkpoint paths.
8. **M46's CLI surface is three `m46_*` scripts**, not the assumed naming convention — resolved by inspection rather than convention.

## What Phase 2A did not establish

1. **Nothing about historical cache integrity.** The new digests are `PRE_PHASE2_LOCAL_BASELINE_ONLY`.
2. **No statistical executability.** A verified source pin says nothing about whether a hypothesis can be tested. The 13 unresolved P3A decisions — six blocking all execution — remain untouched.
3. **No proof that any original run used a given source tree**, except where a committed manifest explicitly links them. For M43–M47 that linkage does not exist.
4. **No environment or dependency provenance.** No lockfile or container digest ties results to an environment.
5. **No authorized cache-exposure mechanism**, which blocks every candidate from actually starting.

## Standing safety state

`strategy.enabled=false`. M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL`, zero transactions, empty base and stress positions, ledger blob `ce8f022d1fc9f734042de4c08feef35b9de00465` — untouched by this milestone. **Phase 2B remains unauthorized.**
