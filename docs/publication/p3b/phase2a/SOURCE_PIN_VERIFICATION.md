# Source-Pin Verification

**Method.** Each manifest's `generation_source_file_sha256` is a SHA-256 over **file content bytes**, not a Git blob identifier. Verification therefore extracted the exact bytes at the relevant commit with `git cat-file blob` and hashed them. No file was modified to make any hash match.

## Headline — by evidence class

An earlier draft reported "11/11 historical match" as a single figure. That overstated three of the eleven, and the classes are now separated.

| Evidence class | Count | Meaning |
|---|---:|---|
| `HISTORICAL_GIT_BLOB_EXACT_MATCH` | **8/11** | The expected hash equals SHA-256 of the exact Git blob bytes at that milestone's generation commit |
| `HISTORICAL_CRLF_TRANSFORM_MATCH` | **3/11** | The expected hash is reproduced exactly by a deterministic LF to CRLF transformation of those blob bytes |
| `UNEXPLAINED_MISMATCH` | **0/11** | — |

**There is no unexplained mismatch and no hard stop.** But the three transform matches are **not** exact historical Git-object matches, and are not described as such.

A Git commit stores the Git blob bytes. Reproducing a manifest hash after transforming LF to CRLF **demonstrates a deterministic explanation consistent with Windows working-tree hashing** — it does not prove the historical checkout configuration, because the repository does not commit that configuration. `core.autocrlf=true` is observed **now**; what it was at each generation moment is recorded nowhere in Git.

**One pin no longer matches current `main`** — M52's `src/cross_sectional_momentum_research.py` — for a documented and expected reason. **Phase 2B must therefore use an isolated historical worktree for M52.**

## The line-ending finding

Three pins did not match the raw Git blob. Rather than assume a defect, the cause was tested: hashing the same bytes with LF translated to CRLF reproduces the expected hash **exactly** in all three cases.

The repository currently has `core.autocrlf = true`, **no `.gitattributes`**, and every pinned source file is CRLF on disk. The most economical explanation is that those manifests hashed **working-tree bytes as checked out on Windows** while Git stores LF.

**Stated precisely:** the three expected hashes are reproduced exactly by deterministic LF to CRLF transformation and are consistent with working-tree-byte hashing, **but the repository does not commit the historical checkout configuration**, so this remains a well-supported explanation rather than proof. It is also unexplained why the other eight reproduce under raw LF given the same current configuration — a residual asymmetry this evidence cannot resolve.

**Consequence for Phase 2B:** any pin re-verification must try both conventions deliberately and record which one reproduced each hash. Comparing raw Git blob hashes alone yields false drift for three of the eleven pins.

## Per-milestone results

`HIST` = reproduced at that milestone's `generation_git_head_sha`. `MAIN-CONVENTION` = reproduced at current `main` **under some checkout/line-ending convention** — it does not assert raw Git-blob equality. `Variant` names the convention required.

| Milestone | Generation commit | Source file | HIST | MAIN-CONVENTION | Variant |
|---|---|---|---|---|---|
| M48 | `f04b68bd46f6b2dc8a6773ebafcddd28b43dfee9` | `scripts/research_capital_target_strategy_family.py` | **MATCH** | **MATCH** | LF |
| M48 | same | `src/orb_capital_target_strategy_family_research.py` | **MATCH** | **MATCH** | LF |
| M49 | `3c8ef8ec3d57f74da07eac151b4b4ce32423adb6` | `scripts/research_orb_practical_core_satellite_portfolio.py` | **MATCH** | **MATCH** | LF |
| M49 | same | `src/orb_practical_core_satellite_portfolio_research.py` | **MATCH** | **MATCH** | CRLF |
| M50 | `7c5fc9b3cb8f1fd4ab529b3151016ee7abf89641` | `scripts/research_orb_untouched_2019_2020_validation.py` | **MATCH** | **MATCH** | LF |
| M50 | same | `src/orb_untouched_2019_2020_validation_research.py` | **MATCH** | **MATCH** | LF |
| M52 | `3d345ebca19c7159678aaad801cdb66299f9ba6b` | `scripts/research_cross_sectional_momentum.py` | **MATCH** | **MATCH** | LF |
| M52 | same | `src/cross_sectional_momentum_research.py` | **MATCH** | **DRIFT** | CRLF (historical) |
| M53 | `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865` | `scripts/research_momentum_risk_control.py` | **MATCH** | **MATCH** | LF |
| M53 | same | `src/cross_sectional_momentum_research.py` | **MATCH** | **MATCH** | CRLF |
| M53 | same | `src/momentum_risk_control_research.py` | **MATCH** | **MATCH** | LF |

**Totals: HIST = 8 `HISTORICAL_GIT_BLOB_EXACT_MATCH` + 3 `HISTORICAL_CRLF_TRANSFORM_MATCH` + 0 `UNEXPLAINED_MISMATCH`. MAIN-CONVENTION = 10/11 reproduced, 1 genuine DRIFT.**

## The one current-main drift, explained

`src/cross_sectional_momentum_research.py` carries **different** expected hashes in the M52 and M53 manifests, because M53 deliberately extended that exact file — adding the `top_n`, `vol_target_annualized`, and `vol_lookback_sessions` parameters while preserving M52's behaviour by default. The M53 pin still matches current `main`; the M52 pin cannot, because M53 legitimately superseded that file.

This is precisely the case the Phase-2A brief anticipated: **a current-main mismatch is not automatically a historical defect.** Both hashes verify at their own generation commits, and the supersession is documented in the M53 report. The operational consequence is concrete:

> **Phase 2B must regenerate M52 from an isolated worktree at `3d345ebca19c7159678aaad801cdb66299f9ba6b`, never from current `main`.**

M48, M49, M50, and M53 could in principle regenerate from `main` on source-pin grounds alone, but the command plan nonetheless specifies an isolated worktree for each, so that no milestone depends on `main` staying still.

## M43–M47: indirect provenance only

These five milestones have **no committed artifact-hash manifest** and therefore **no explicit output-to-generation-source linkage**. What exists is **indirect Git-tree provenance**: the full source tree at each result commit is recoverable, and the wrapper and CLI blob identifiers below are recorded from it.

These are **Git blob identifiers (SHA-1)**, not content SHA-256 pins, and there is no committed expected value to compare them against.

| Milestone | Result commit | Path | Blob (SHA-1, abbreviated) |
|---|---|---|---|
| M43 | `264fdf3cf8ea264b14a974ce4ddaa48185b56599` | `scripts/research_orb_profit_protection_exit.py` | `95c82b0592939af0` |
| M43 | same | `src/orb_profit_protection_exit_research.py` | `a84e5f17965a8f9a` |
| M44 | `864416c829539c1567a23bf67b96f659598402de` | `scripts/research_orb_fallback_candidate_recovery.py` | `41e37592fe4284f2` |
| M44 | same | `src/orb_fallback_candidate_recovery_research.py` | `cc3a34a1c0d4b792` |
| M45 | `051def4f9cce366ef128613941e634863b8efa07` | `scripts/research_orb_later_first_entry_recovery.py` | `103ebf377ef326c2` |
| M45 | same | `src/orb_later_first_entry_recovery_research.py` | `597005ab7b8bbdf3` |
| M46 | `2276ab9c9baba09f89aa1b3be8d9a1e66053e644` | `scripts/m46_screen_and_select.py` | `d802931cb16aaccd` |
| M46 | same | `scripts/m46_run_feasibility_experiment.py` | `59ef1ec67224812c` |
| M46 | same | `scripts/m46_analyze_feasibility_results.py` | `14cbf00dc5de0c06` |
| M46 | same | `src/orb_small_midcap_momentum_research.py` | `6a2ff4deef413226` |
| M47 | `2cfa08bf37924f0f7b01e0d6f31b2e6fb81a4169` | `scripts/research_orb_sequential_second_trade.py` | `247dcab39543f3dd` |
| M47 | same | `src/orb_sequential_second_trade_research.py` | `b6882610a609d7a5` |

**M46 correction worth recording:** the naming convention the other four follow (`scripts/research_orb_<topic>.py`) does **not** exist for M46. Its CLI surface is three separate `m46_*` scripts, identified by statically searching that commit's `scripts/` tree for modules importing `orb_small_midcap_momentum_research`. An assumed path would have been wrong; this was resolved by inspection, not convention.

**These blob identifiers do not prove which complete source tree produced the original historical results.** They record what the tree contained at the result commit. Absent an explicit generation-source manifest, the linkage between output and generating source remains unestablished for M43–M47.

**Consequence:** a Phase-2B rerun of M43–M47 **must** use an isolated worktree at the milestone result commit. Running from `main` would have no defensible provenance basis at all.
