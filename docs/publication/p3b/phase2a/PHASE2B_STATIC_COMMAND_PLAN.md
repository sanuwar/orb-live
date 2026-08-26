# Phase 2B Static Command Plan

**None of these commands was executed.** They were constructed by reading committed source text only.

## Two decisive static findings

**1. M48, M49, M52, and M53 have no output-redirect argument.** Their results path is hardcoded as `RESULTS_DIR = ROOT / "artifacts" / ... / "results"`, and each also **overwrites its own committed artifact-hash manifest** under `docs/research/` via `COMMITTED_HASHES_PATH.write_text(...)`.

Run from the main checkout, these commands would destroy both the Phase-1-verified result artifacts **and the very manifests that supply the frozen expected hashes.** The only available mitigation is that `ROOT = Path(__file__).resolve().parents[1]`, so an **isolated worktree redirects every write into that worktree**. Redirection is a property of *where the command runs*, not of any flag it accepts.

**2. M43 and M45 are the opposite.** They accept `--output-dir`, `--cache-dir`, and `--checkpoint-dir`, so they redirect cleanly. But they both `pickle.load` **and** `pickle.dump` checkpoints, so a default-path run would **write into the cache tree**. They must be pointed at scratch, or run with `--no-checkpoint`.

The naive "JSON is safe, pickles are risky" ordering is therefore too simple: the JSON milestones carry the worse *write* hazard, and the pickle milestones carry the better *redirection* story.

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
| Cache roots | `research-capital-target-strategy-family/daily-bar-cache/sip` |
| Expected output | `artifacts/research-capital-target-strategy-family/results/study_results.json` |
| Expected hash | `3d6f79b14919249242deaf8de79ea354b92ef51014a6f7a8a4ab4c983fd84dc8` |
| Pickle | No |
| Fetch on cache miss | No |
| Redirect safe | Worktree only; rewrites the worktree M48 manifest and provenance manifest |
| Status | `READY_WITH_LIMITATIONS` |

| Item | M49 / TI-08 |
|---|---|
| Worktree commit | `3c8ef8ec3d57f74da07eac151b4b4ce32423adb6` |
| Entry point | `python -m scripts.research_orb_practical_core_satellite_portfolio` |
| Arguments | `--feed sip`; omit `--fetch-missing`. `--parity-only` runs every integrity and standalone-parity gate **without reading combined-portfolio economics** -- the safest first stage |
| Cache roots | **three trees**: M48 `daily-bar-cache/sip`, M43 `period-checkpoint` (pickles), and `research-universe-expansion/bar-cache` (3.39 GB) |
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
| Arguments | `--output-dir <SCRATCH>/out-m43 --cache-dir <CACHE_EXPOSURE> --checkpoint-dir <SCRATCH>/ckpt-m43`; omit `--fetch-missing`. `--no-checkpoint` avoids checkpoint writes entirely but forfeits the frozen checkpoints |
| Cache roots | `research-profit-protection-exit/period-checkpoint` |
| Expected output | none predeclared |
| Expected hash | **NONE -- no committed expected-output hash exists** |
| Pickle | **Yes -- both load and dump** |
| Fetch on cache miss | No |
| Redirect safe | **Yes**, fully parameterized |
| Status | `BLOCKED_FOR_PHASE2B` |

| Item | M45 / TI-04 |
|---|---|
| Worktree commit | `051def4f9cce366ef128613941e634863b8efa07` |
| Entry point | `python -m scripts.research_orb_later_first_entry_recovery` |
| Arguments | same redirect triple as M43 |
| Cache roots | `research-later-first-entry-recovery/period-checkpoint` |
| Expected hash | **NONE** |
| Pickle | **Yes -- load and dump** |
| Redirect safe | **Yes** |
| Status | `BLOCKED_FOR_PHASE2B` |

| Item | M50 -- verification only |
|---|---|
| Worktree commit | `7c5fc9b3cb8f1fd4ab529b3151016ee7abf89641` |
| Scope | **No regeneration proposed.** M50 computed no economics; only its stage-0 gate exists |
| Expected hash | `02e8482839cdd378a7fe42dbef5f89bb4ba770cced190147b507602a2865982c` (stage-0) |
| Status | `READY_WITH_LIMITATIONS` -- pin and result verification only, no rerun |

## Why M43 and M45 are BLOCKED

Both fail the brief's own test, since static inspection cannot prove all five required properties:

1. **No predeclared expected output exists**, so a rerun has no success criterion beyond weak report reconciliation -- which is itself a Phase-2B parsing activity, not something Phase 2A can settle.
2. **They write pickles.** With `--checkpoint-dir` pointed at scratch this is contained, but proving containment requires establishing that no other write path exists, which reading the argument surface alone does not do.
3. **No explicit output-to-generation-source linkage exists**, so even a clean run proves less than the equivalent M48-M53 run.

They are blocked pending explicit decisions on all three, **not** because their caches are missing. Both caches are present and baselined.

## The unresolved cache-exposure problem

This affects **every** candidate and is the single largest open item.

An isolated worktree contains no caches: `artifacts/` is gitignored and absent from Git. The caches must therefore be made visible to the worktree, and every obvious mechanism has a problem:

- **Copying** is prohibited by the Phase-1 and Phase-2A boundaries, and would duplicate roughly 3.4 GB.
- **Symlink or directory junction** avoids copying, but creating one is a filesystem write adjacent to the cache tree, and its read-only character is not statically provable.
- **Running in the main checkout instead** removes the isolation and reinstates the overwrite hazard for M48-M53.
- **Pointing `--cache-dir` at the real tree** works for M43 and M45 only; the other four accept no such flag.

**Phase 2B cannot begin until an operator selects and authorizes a specific exposure mechanism.** Phase 2A deliberately does not choose one, because that would settle a safety-relevant question by convenience.

## Environment and dependency evidence

**Not established.** No committed lockfile, container digest, or interpreter-version record ties any milestone's results to an environment. `requirements.txt` exists but is not pinned per milestone. A Phase-2B mismatch could therefore stem from dependency drift, and nothing committed would distinguish that from a cache or code problem. This is a real limit on what any rerun can conclude.
