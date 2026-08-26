# Phase 2 Authorization Requirements

**Phase 2 is not authorized by this package and was not performed.** This document states what it would require.

## Why Phase 2 needs separate authorization

Phase 2 reproduces historical strategy outputs from the existing caches to confirm that regeneration reproduces the committed result hashes. **Even with every parameter frozen, no market-data fetch, and an expected-identical output, that is a historical simulation rerun.** Executing committed strategy and research code over cached market data is strategy execution regardless of whether the result is already known.

For M43, M44, M45, and M47 there is a second, sharper reason: their caches are Python pickles, and **pickle deserialization is code-execution-capable and may import or invoke referenced globals and classes**. Loading them is not passive data inspection, and Phase 2 must treat it as authorized code execution carrying both security and compatibility risk.

## Operations Phase 2 would require

| # | Operation | Constitutes a historical simulation rerun? |
|---|---|---|
| 1 | Load M43/M45 period-checkpoint pickles | **Yes** — deserialization is code-execution-capable |
| 2 | Import and execute `src/orb_*` and `src/cross_sectional_momentum_research.py` and their CLIs | **Yes** |
| 3 | Re-run each milestone's generation path over its cached bars | **Yes** |
| 4 | Recompute the results JSON and compare its SHA-256 to the committed manifest field | The comparison itself is not; producing the input to it is |
| 5 | Compare `generation_source_file_sha256` against current tracked source files (M48–M53 only) | No — this is a tracked-file hash comparison and could be done today |
| 6 | ~~Resolve TI-08's ORB minute-input location~~ | **Already done.** Resolved statically in the Phase-1 correction pass; all three input trees confirmed present. No longer a Phase-2 item. |

Operation 5 is the only item that could be performed without new authorization. It is deliberately left for Phase 2 so that source-pin verification happens alongside the regeneration it is meant to protect, rather than as an isolated result that could be mistaken for integrity evidence.

## Inputs and hashes that must be frozen before Phase 2 begins

1. The exact commit Phase 2 runs at, recorded before execution.
2. For M48, M49, M50, M52, M53: the committed `generation_source_file_sha256` maps, verified against the tracked source files at that commit **before** any regeneration.
3. The five committed result hashes as the expected outputs — recorded in `CACHE_INVENTORY_PHASE1.md` and not to be re-derived from whatever Phase 2 produces.
4. A pre-execution census of every cache directory's file count and aggregate size, so any mutation during Phase 2 is detectable afterwards.
5. For M43–M47: an explicit acknowledgement that **no explicit output-to-generation-source linkage exists**. Indirect Git-tree provenance does exist, so Phase 2 should run from each milestone's own result commit or worktree and record the source blob hashes it used — that establishes exactly what code the rerun executed. What it cannot establish is that the original historical run used precisely that complete research-source tree.

## Safety and no-fetch constraints binding Phase 2

- **No market, benchmark, factor, SEC, OpenFIGI, or broker data fetch**, under any circumstance, including to repair a gap discovered mid-run. A cache found incomplete is a finding, not a fetch trigger.
- **No parameter search, threshold sweep, or tuning of any kind.**
- **No cache mutation.** No file may be altered, normalized, renamed, moved, copied, repaired, truncated, or deleted. Regeneration must write to a scratch location, never over a cache.
- **No raw data may enter Git**, and no archive of raw cache data may be created.
- `strategy.enabled` stays `false`; no broker or account access; M55 is not run or advanced.
- Results are recorded as pass/fail per milestone with hashes, not as performance figures.

## What Phase 2 can and cannot establish

**Can:** whether regeneration from the existing caches reproduces the committed result hashes, per milestone; and for M48–M53, whether it ran against the pinned source versions.

**Cannot:** make any instance statistically executable. That additionally requires the 13 unresolved decisions in `docs/publication/p3a/STATISTICAL_DECISION_REGISTER.md` to be resolved by a qualified external finance/econometrics reviewer — six of which block all execution — followed by a separate, time-stamped protocol-freezing commit. Phase 2 success would satisfy one precondition of three.

**Cannot, for M43–M47:** establish that the *original historical run* used precisely the research-source tree the rerun executes. Running from the milestone's own Git commit does establish what code the **rerun** used, and the trusted implementation digest `f79587b9…` covers the pinned implementation closure — but neither closes the gap to an explicit historical generation-source manifest, which was never committed for these milestones.

## Authorization statement required

Phase 2 may begin only on **explicit operator authorization** that names it as a historical simulation rerun and accepts the constraints above. Phase 1 does not grant it, and no document in `docs/publication/p3b/` should be read as granting it.
