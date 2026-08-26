# P3B Phase 2A — Source-Pin Verification and Non-Executing Regeneration Plan

**Evidence cutoff:** main at `20156b5dc420abd1ea735a699fe0b457f794f626`.
**Issue:** #374. **Scope:** `docs/publication/p3b/phase2a/` only, Markdown only.

## Purpose

Prepare and verify everything a later, separately authorized Phase 2B regeneration run would need — **without executing any of it.**

## Non-execution statement

**No ORB-LIVE project module was imported. No strategy or research CLI was executed, with or without `--help`. No pickle was deserialized. No raw market-data row, trade, holding, price, return, or equity series was parsed. No simulation, backtest, regeneration, or statistical analysis was run. No market, benchmark, factor, SEC, OpenFIGI, or broker data was fetched. No broker or account was accessed. No parameter, threshold, or sweep was changed. No cache file was modified, copied, normalized, renamed, repaired, or deleted.** `strategy.enabled` remains `false`; M55 was neither run nor advanced.

## What was actually done

1. **Static reading** of committed source, manifests, reports, and Git history — text only, via `git cat-file`, `git ls-tree`, `git log`, and `rg`.
2. **Git-object hash comparisons** between committed manifest pins and the exact source blobs at the relevant commits.
3. **Opaque byte-level SHA-256 hashing** of existing local cache files, purely to establish a pre-Phase-2 baseline. Bytes were read and hashed; they were never deserialized or semantically interpreted.
4. **Static construction** of proposed Phase-2B commands and stop conditions, none of which was executed.

## Terminology

| Term | Meaning here |
|---|---|
| `HISTORICAL_COMMIT_PIN_MATCH` | The manifest's expected source hash matches that file's content at the milestone's own `generation_git_head_sha`. |
| `CURRENT_MAIN_PIN_MATCH` | The same expected hash matches that file's content at current `main`. |
| `PRE_PHASE2_LOCAL_BASELINE_ONLY` | An aggregate digest computed now, over local bytes. It fixes the current state so later mutation is detectable. It is **not** evidence of historical originality or vendor integrity. |
| `READY_FOR_SEPARATE_PHASE2B_AUTHORIZATION` | Static preparation is complete; execution still requires separate explicit authorization. |
| `BLOCKED_FOR_PHASE2B` | Static inspection could not prove the command stays offline, writes only to scratch, leaves caches untouched, uses frozen parameters, and avoids broker access. |

## Deliverables

| File | Purpose |
|---|---|
| `README.md` | This file. |
| `SOURCE_PIN_VERIFICATION.md` | Per-milestone pin results at the historical commit and at current main. |
| `EXPECTED_RESULT_HASH_FREEZE.md` | Predeclared expected result hashes and their limits. |
| `CACHE_BASELINE_DIGESTS.md` | Aggregate cache roots, counts, sizes, digest algorithm, digests, limitations. |
| `PHASE2B_STATIC_COMMAND_PLAN.md` | Exact unexecuted commands, worktrees, inputs, scratch outputs. |
| `PHASE2B_RISK_AND_STOP_CONDITIONS.md` | Pickle, dependency, cache-miss-fetch, output-path, licensing, security, mutation risks and mandatory stops. |
| `PHASE2A_READINESS_DECISION.md` | Per-milestone status and the reconciled TI-01–TI-11 count. |

## What Phase 2A does not establish

It does not make any hypothesis statistically executable. It does not convert cache presence or a new local digest into historical-integrity evidence. It does not prove that any original historical run used a particular source tree beyond what a committed manifest explicitly links. Phase 2B remains unauthorized.
