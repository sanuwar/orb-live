# Phase 2B Static Command Plan

**None of these commands was executed.** They were constructed by reading committed source text only.

## Two decisive static findings

**1. M48, M49, M52, and M53 have no output-redirect argument.** Their results path is hardcoded as `RESULTS_DIR = ROOT / "artifacts" / ... / "results"`, and each also **overwrites its own committed artifact-hash manifest** under `docs/research/` via `COMMITTED_HASHES_PATH.write_text(...)`.

Run from the main checkout, these commands would **overwrite tracked working-tree files and local gitignored result artifacts**. They cannot alter existing Git commit objects: the historical manifests remain recoverable from Git, and Phase 2A's separately frozen hashes in `EXPECTED_RESULT_HASH_FREEZE.md` remain available regardless. The operational rule is unchanged — **running from the main checkout is forbidden** — but the reason is working-tree contamination, not destruction of committed history.

The only available mitigation is that `ROOT = Path(__file__).resolve().parents[1]`, so an **isolated worktree contains every write inside that worktree**. Containment is a property of *where the command runs*, not of any flag it accepts.

**2. M43 and M45 redirect cleanly but can delete cache files.** They accept `--output-dir`, `--cache-dir`, and `--checkpoint-dir`. But `--cache-dir` defaults to `DEFAULT_CACHE_DIR`, which resolves to `artifacts/research-universe-expansion/bar-cache` — the **minute-bar cache**, not the period checkpoints — and both CLIs call `validate_cache_integrity(args.cache_dir)`, which calls `path.unlink(missing_ok=True)` on any `*.json` that fails to parse or lacks a `bars` key. **Pointing `--cache-dir` at the original minute cache therefore does not leave it untouched: that function has deletion capability by design.**

**3. M48's full study is not JSON-only.** `run_full_study` calls `load_orb_monthly_r_series()`, which does `import pickle` and `pickle.load` on the six **M43** period checkpoints. `--data-only` returns before `run_full_study`, so it avoids that path — but the complete exact-output regeneration does not.

An earlier draft of this document framed this as "JSON milestones versus pickle milestones." That split does not survive the call-path audit: M48's full run is pickle-dependent and depends on M43's checkpoints, and M43/M45's pickle use is *optional* (see `--no-checkpoint` below) while their cache-deletion exposure is not.

## Offline guarantee

Every CLI examined defaults `--fetch-missing` to `False` -- M48/M49 explicitly `default=False`, the others via `action="store_true"`. **Omitting the flag is cache-only by construction.** M53's help text states it must not repeat the multi-hour fetch unless a cache file is demonstrably missing.

## Common worktree preamble

Every command assumes an isolated worktree that is never committed or pushed:

`git worktree add --detach <SCRATCH>/wt-<MS> <MILESTONE_COMMIT>`

`<SCRATCH>` is outside the repository. `<CACHE_EXPOSURE>` is the unresolved mechanism described at the end.

## Per-candidate plan

| Item | M52 / TI-09 |
|---|---|
| Worktree commit | `3d345ebca19c7159678aaad801cdb66299f9ba6b` -- **mandatory**, its source pin no longer matches `main` |
| Entry point | `python -m scripts.research_cross_sectional_momentum` |
| Arguments | defaults only; **omit `--fetch-missing`** |
| Cache roots | `research-cross-sectional-momentum/{daily-bar-cache,sec-nport-cache,openfigi-cache}` |
| Expected output | `artifacts/research-cross-sectional-momentum/results/m52_result.json` inside the worktree |
| Expected hash | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` |
| Scratch output | via worktree `ROOT` only -- no flag exists |
| Pickle | No |
| Fetch on cache miss | No, with the flag omitted |
| Redirect safe | Only via worktree; also rewrites the worktree copy of the M52 manifest |
| Status | `READY_WITH_LIMITATIONS` |

| Item | M53 / TI-10, TI-11 |
|---|---|
| Worktree commit | `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865` |
| Entry point | `python -m scripts.research_momentum_risk_control` |
| Arguments | defaults only; omit `--fetch-missing` |
| Cache roots | the same M52 trees, plus it **reads** the M52 result for baseline parity and hard-fails if absent |
| Expected output | `artifacts/research-momentum-risk-control/results/m53_result.json` |
| Expected hash | `a3d3ebbd62f45331115a74583f7f8c378e9f2e786fb693b12865fff8860122c1` |
| Pickle | No |
| Fetch on cache miss | No |
| Redirect safe | Worktree only; rewrites the worktree M53 manifest |
| Status | `READY_WITH_LIMITATIONS` -- **must follow M52**, or be supplied the existing M52 result |

| Item | M48 / TI-05 to TI-07 |
|---|---|
| Worktree commit | `f04b68bd46f6b2dc8a6773ebafcddd28b43dfee9` |
| Entry point | `python -m scripts.research_capital_target_strategy_family` |
| Arguments | `--feed sip`; omit `--fetch-missing`. `--data-only` verifies data and writes the provenance manifest **without running the study** -- a useful dry-run stage |
| Cache roots | **two trees.** Primary statistical series: `research-capital-target-strategy-family/daily-bar-cache/sip`. Complete-artifact regeneration additionally requires **`research-profit-protection-exit/period-checkpoint`** (M43 pickles), consumed by `load_orb_monthly_r_series()` |
| Expected output | `artifacts/research-capital-target-strategy-family/results/study_results.json` |
| Expected hash | `3d6f79b14919249242deaf8de79ea354b92ef51014a6f7a8a4ab4c983fd84dc8` |
| Pickle | **YES for the full run** — `run_full_study` line 433 calls `load_orb_monthly_r_series()`, which imports `pickle` and `pickle.load`s six M43 `*.pkl` checkpoints. **NO for `--data-only`**, which returns at line 755 before `run_full_study` at line 757 |
| Fetch on cache miss | No |
| Redirect safe | Worktree only; overwrites the worktree copies of the M48 manifest and provenance manifest |
| Status | `READY_WITH_LIMITATIONS` — and pickle-dependent, so it inherits the M43 deserialization risk |

| Item | M49 / TI-08 |
|---|---|
| Worktree commit | `3c8ef8ec3d57f74da07eac151b4b4ce32423adb6` |
| Entry point | `python -m scripts.research_orb_practical_core_satellite_portfolio` |
| Arguments | `--feed sip`; omit `--fetch-missing`. `--parity-only` runs every integrity and standalone-parity gate **without reading combined-portfolio economics** -- the safest first stage |
| Cache roots | **three trees**: M48 `daily-bar-cache/sip`, M43 `period-checkpoint` (pickles — note M48's own full run also consumes these), and `research-universe-expansion/bar-cache` (3.39 GB) |
| Expected output | `artifacts/research-practical-core-satellite-portfolio/results/study_results.json` |
| Expected hash | `aaf79b6887307b7ab9ce19a3e1c654f2b011058e8237e13eb7e15754d7a23683` |
| Pickle | **Yes** -- loads M43 frozen ORB checkpoints |
| Fetch on cache miss | No |
| Redirect safe | Worktree only |
| Status | `READY_WITH_LIMITATIONS` -- highest complexity of the JSON group |

| Item | M43 / TI-01 to TI-03 |
|---|---|
| Worktree commit | `264fdf3cf8ea264b14a974ce4ddaa48185b56599` |
| Entry point | `python -m scripts.research_orb_profit_protection_exit` |
| Arguments | `--output-dir <SCRATCH>/out-m43 --cache-dir <SAFE_MINUTE_CACHE_SNAPSHOT> --checkpoint-dir <SCRATCH>/ckpt-m43 --no-checkpoint`; omit `--fetch-missing` |
| Cache roots | **Raw input:** `research-universe-expansion/bar-cache` via `DEFAULT_CACHE_DIR` — the **minute-bar cache**, not the period checkpoints. **Checkpoint load/write location:** `research-profit-protection-exit/period-checkpoint`, which is a derived checkpoint store, not the underlying raw input |
| Expected output | none predeclared |
| Expected hash | **NONE** — weaker report reconciliation only (see `EXPECTED_RESULT_HASH_FREEZE.md`) |
| Pickle | **Optional, not unavoidable.** Line 380 sets `cached = None if args.no_checkpoint else _load_period_checkpoint(...)`; lines 390–391 guard `_save_period_checkpoint` behind `if not args.no_checkpoint`. **`--no-checkpoint` therefore skips both `pickle.load` and `pickle.dump`** |
| Cache deletion risk | **Yes.** Line 372 calls `validate_cache_integrity(args.cache_dir)`, which `path.unlink(missing_ok=True)`s any `*.json` that fails to parse or lacks a `bars` key. Exposing the original minute cache directly does **not** leave it untouched |
| Fetch on cache miss | No |
| Redirect safe | **Yes for outputs and checkpoints.** Not safe for `--cache-dir` unless a read-only snapshot or authorized scratch copy is supplied |
| Status | `BLOCKED_PENDING_WEAK_CRITERION_AND_SAFE_CACHE_SNAPSHOT` |

| Item | M45 / TI-04 |
|---|---|
| Worktree commit | `051def4f9cce366ef128613941e634863b8efa07` |
| Entry point | `python -m scripts.research_orb_later_first_entry_recovery` |
| Arguments | same redirect set as M43, including `--no-checkpoint` |
| Cache roots | **Raw input:** `research-universe-expansion/bar-cache` via `DEFAULT_CACHE_DIR`. **Checkpoint location:** `research-later-first-entry-recovery/period-checkpoint` |
| Expected hash | **NONE** — weaker report reconciliation only |
| Pickle | **Optional** — same `--no-checkpoint` gating as M43 |
| Cache deletion risk | **Yes** — same `validate_cache_integrity` deletion capability |
| Redirect safe | **Yes for outputs and checkpoints**, not for `--cache-dir` without a safe snapshot |
| Status | `BLOCKED_PENDING_WEAK_CRITERION_AND_SAFE_CACHE_SNAPSHOT` |

| Item | M50 -- verification only |
|---|---|
| Worktree commit | `7c5fc9b3cb8f1fd4ab529b3151016ee7abf89641` |
| Scope | **No regeneration proposed.** M50 computed no economics; only its stage-0 gate exists |
| Expected hash | `02e8482839cdd378a7fe42dbef5f89bb4ba770cced190147b507602a2865982c` (stage-0) |
| Status | `READY_WITH_LIMITATIONS` -- pin and result verification only, no rerun |

## Why M43 and M45 are blocked — and why that is not permanent

An earlier draft treated the absence of an expected-output hash as a permanent disqualification and removed M43/M45 from Phase 2B entirely. **That was wrong and is withdrawn.** The original brief explicitly allowed weaker reconciliation for milestones without committed output hashes. Absence of a hash makes the evidence weaker; it does not make regeneration impossible.

Their status is `BLOCKED_PENDING_WEAK_CRITERION_AND_SAFE_CACHE_SNAPSHOT`, gated on three tractable items:

1. **Freeze the weak reconciliation criterion.** A predeclared table now exists in `EXPECTED_RESULT_HASH_FREEZE.md` naming the exact report, commit, blob, fields, and tolerance rule. It needs operator acceptance, not invention.
2. **Authorize a safe minute-cache exposure.** `validate_cache_integrity` can delete cache files, so `--cache-dir` must point at a read-only snapshot or an authorized scratch copy — never the original tree. Phase 2A does not create one.
3. **Verify complete write-path containment**, using the full call-path audit rather than the argparse surface alone.

Their caches are present and baselined; nothing here is a missing-data problem. Note also that the pickle objection has weakened: `--no-checkpoint` skips both `pickle.load` and `pickle.dump`, so deserialization is **optional** for a fresh regeneration.

## Full static call-path audit

Traced from committed source at each milestone commit, beyond the argparse surface.

| Milestone | Reads | Writes | Pickle | Cache validation / removal | Network-capable path and gate | Cross-milestone dependency |
|---|---|---|---|---|---|---|
| M52 | 3 JSON cache trees | worktree `results/m52_result.json`; worktree M52 manifest | none | none observed | fetch helpers gated behind `--fetch-missing` (default off) | none |
| M53 | same 3 trees; **reads M52 result**, hard-fails if absent | worktree `results/m53_result.json`; worktree M53 manifest | none | none observed | same gate | **M52 result artifact** |
| M48 | `daily-bar-cache/sip`; **M43 `*.pkl` checkpoints** | worktree `results/study_results.json`, `provenance_manifest.json`, M48 manifest | **`pickle.load` in `load_orb_monthly_r_series` (full run only)** | none observed | same gate | **M43 checkpoints** |
| M49 | M48 `sip` cache; M43 checkpoints; `universe-expansion/bar-cache` | worktree `results/study_results.json`, provenance, M49 manifest | **`pickle.load`** of M43 checkpoints | none observed | same gate | **M43 checkpoints, M48 cache** |
| M43 | `universe-expansion/bar-cache` via `--cache-dir`; checkpoints via `--checkpoint-dir` | `--output-dir`; `_save_period_checkpoint` unless `--no-checkpoint` | **load and dump, both `--no-checkpoint`-gated** | **`validate_cache_integrity` → `path.unlink`** | `_evaluate_period(..., fetch_missing=args.fetch_missing)`, default off | none |
| M45 | same shape as M43 | same shape as M43 | same gating | **same deletion capability** | same gate | none |

**Correction this audit forces:** an earlier draft said only TI-08 depends on M43's checkpoints. **M48's full study depends on them too**, so TI-05 through TI-08 all inherit that dependency for complete-artifact regeneration. The distinction worth preserving is that M43's checkpoints are **not** the primary statistical series for TI-05–TI-07 — those come from the `sip` daily-bar cache — but they **are** required for the exact-output regeneration.

## Unresolved M52 to M53 artifact handoff

M53 hard-fails without an M52 result at its expected path. **Separate historical worktrees do not share generated artifacts**, and the two milestones sit at different commits (`3d345ebc` and `4c43fe96`), so M53's worktree will not contain the file M52's worktree produced.

Neither obvious route is authorized:

- **Copy the scratch-generated M52 result into the M53 worktree.** This is copying a *generated, hash-verifiable* artifact between two scratch locations — categorically different from copying raw licensed cache data — but it is still an unauthorized copy today.
- **Point M53 at the existing committed-and-verified local M52 result.** That artifact is byte-identical to the frozen expected hash, but using it would mean M53 verifies against a pre-existing artifact rather than the one its own sequence just produced.

**Phase 2B authorization must explicitly permit one defined scratch-only handoff, including the requirement that the M52 result be hash-verified against `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` before M53 consumes it.** No handoff is performed now.

## The unresolved cache-exposure problem

This affects **every** candidate and is the single largest open item.

An isolated worktree contains no caches: `artifacts/` is gitignored and absent from Git. The caches must therefore be made visible to the worktree, and every obvious mechanism has a problem:

- **Copying** is prohibited by the Phase-1 and Phase-2A boundaries, and would duplicate roughly 3.4 GB.
- **Symlink or directory junction** avoids copying, but creating one is a filesystem write adjacent to the cache tree, and its read-only character is not statically provable.
- **Running in the main checkout instead** removes the isolation and reinstates the overwrite hazard for M48-M53.
- **Pointing `--cache-dir` at the real tree** works for M43 and M45 only, and is now **specifically unsafe**: `validate_cache_integrity` can delete entries from whatever directory it is handed. A read-only snapshot or authorized scratch copy is required for those two, not the original tree.

**Phase 2B cannot begin until an operator selects and authorizes a specific exposure mechanism.** Phase 2A deliberately does not choose one, because that would settle a safety-relevant question by convenience.

## Environment and dependency evidence

**Not established.** No committed lockfile, container digest, or interpreter-version record ties any milestone's results to an environment. `requirements.txt` exists but is not pinned per milestone. A Phase-2B mismatch could therefore stem from dependency drift, and nothing committed would distinguish that from a cache or code problem. This is a real limit on what any rerun can conclude.
