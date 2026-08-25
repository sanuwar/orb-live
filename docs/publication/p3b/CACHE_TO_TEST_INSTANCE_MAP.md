# Cache to Test-Instance Map

Maps every P3A test instance and non-executable evaluation to the caches a Phase-2 regeneration would require. Instance identifiers, families, and eligibility come from `docs/publication/p3a/FORMAL_HYPOTHESIS_CATALOG_CANDIDATE.md`; required series come from `docs/publication/p3a/COMPARATOR_ESTIMAND_AND_OUTCOME_MAP.md`.

## Candidate test instances (TI-01 – TI-11)

| TI | CH / FH | Milestone | Required caches (repo-relative) | Required series | Cache status | Phase-2 status |
|---|---|---|---|---|---|---|
| TI-01 | CH-1 / FH-1 | M43 | `research-profit-protection-exit/period-checkpoint/` (6 pkl) | per-trade R-multiples with session timestamps | `PRESENT_UNVERIFIED` | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-02 | CH-1 / FH-1 | M43 | same as TI-01 | same | `PRESENT_UNVERIFIED` | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-03 | CH-1 / FH-1 | M43 | same as TI-01 | same | `PRESENT_UNVERIFIED` | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-04 | CH-2 / FH-1 | M45 | `research-later-first-entry-recovery/period-checkpoint/` (6 pkl) | per-trade R series **plus** the per-session no-trade record | `PRESENT_UNVERIFIED` | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-05 | CH-4 / FH-2 | M48 | `research-capital-target-strategy-family/daily-bar-cache/` (40 json) | daily portfolio return / equity series | `PRESENT_UNVERIFIED` (raw); results `PRESENT_DIRECT_HASH_MATCH` | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-06 | CH-4 / FH-2 | M48 | same as TI-05 | same | same | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-07 | CH-4 / FH-2 | M48 | same as TI-05 | same | same | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-08 | CH-5 / FH-2 | M49 | **cross-directory, now statically resolved** (three locations, all present — see below) | shared-account daily equity series + per-sleeve attribution | `PARTIAL` (own dir holds results only; inputs confirmed elsewhere) | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-09 | CH-6 / FH-3 | M52 | `research-cross-sectional-momentum/daily-bar-cache/` (942), `sec-nport-cache/` (29), `openfigi-cache/` (5,243) | daily series preserving within-date cross-sectional structure | `PRESENT_UNVERIFIED` (raw); results `PRESENT_DIRECT_HASH_MATCH` | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-10 | CH-7 / FH-3 | M53 | the immutable M52 caches above, by design | daily series for both arms from the identical cache | `PARTIAL` (delegated by design) | `CANDIDATE_AVAILABLE_FOR_PHASE2` |
| TI-11 | CH-7 / FH-3 | M53 | same as TI-10 | same | `PARTIAL` (delegated by design) | `CANDIDATE_AVAILABLE_FOR_PHASE2` |

**All eleven remain candidates. None is `NOT_READY_FOR_PHASE2`, because every required input location is now identified and present.**

That is a statement about *presence*, not integrity. Nine of the eleven depend on raw caches with no directly comparable committed hash. That qualification carries forward to `P3B_PHASE1_READINESS_DECISION.md`.

## TI-08 input locations — statically resolved

An earlier draft of this package recorded TI-08's ORB input location as unresolved while still counting it as a candidate. Those two statements were inconsistent, since `CANDIDATE_AVAILABLE_FOR_PHASE2` asserts that required inputs appear present. The location has now been traced by **static reading of committed source only** — no import, no execution — and all three inputs are confirmed present.

| # | Input | Location resolved from | Repo-relative path | Present |
|---|---|---|---|---|
| 1 | Frozen ORB baseline checkpoints (6 periods) | `ORB_CHECKPOINT_DIR`, `scripts/research_orb_practical_core_satellite_portfolio.py:81` | `artifacts/research-profit-protection-exit/period-checkpoint/` | yes — 6 files, 724 KB |
| 2 | ORB minute bars | `ORB_MINUTE_CACHE_DIR`, same file line 88, consumed flat at line 127 with `feed=ORB_FEED` (`"iex"`, line 89) | `artifacts/research-universe-expansion/bar-cache/` | yes — 3,141 files, 3.2 GB, flat content-addressed `.json` |
| 3 | ETF daily bars (ARM_A / ARM_B) | `m48_cli.cache_dir_for_feed(args.feed)` at line 475, resolving to `CACHE_ROOT / feed` (`scripts/research_capital_target_strategy_family.py:62,80-81`) with `--feed` defaulting to `sip` (line 470) | `artifacts/research-capital-target-strategy-family/daily-bar-cache/sip/` | yes — 20 files |

Two independent cross-checks agree with the trace: M49's committed manifest records `orb_feed: iex`, matching `ORB_FEED = "iex"`; and it records `feed: sip`, matching the CLI's `--feed` default and the presence of a `sip/` subdirectory with no `iex/` sibling.

Note that input 1 is **M43's** checkpoint directory. TI-08 therefore shares a cache tree with TI-01, TI-02, and TI-03 across family boundaries — a dependency between FH-1 and FH-2 that was not visible before this trace.

## Non-executable evaluations

| ID | Milestone | Cache present? | Status | Reason |
|---|---|---|---|---|
| DO-01 | M44 | yes — `research-fallback-candidate-recovery/`, 9 files, 22 MB, `PRESENT_UNVERIFIED` | `NOT_APPLICABLE` | Permanently descriptive-only at n=2 genuine closed trades. Cache presence is irrelevant: no cache can raise the sample size. |
| DO-02 | M46 | yes — `research-small-midcap-momentum-lane/`, 3,282 files, 29 MB, `PRESENT_METADATA_ONLY` | `NOT_APPLICABLE` | Permanently descriptive-only: 20 trades over a single 90-session period. |
| SN-01 | M47 | yes — `research-sequential-second-trade/`, 8 files, 136 KB, `PRESENT_UNVERIFIED` | `NOT_APPLICABLE` | Structurally null at n=0. No estimand is defined on zero observations. |

All three caches exist and are recorded here for completeness and for search-accounting continuity. **Their presence does not make any of them evaluable**, and Phase 2 must not attempt to derive observations from them.

## Cross-family dependency summary

| Family | Instances | Distinct cache trees required |
|---|---|---|
| FH-1 | TI-01–TI-04 | 2 (M43, M45 period checkpoints) |
| FH-2 | TI-05–TI-08 | 1 shared (M48 `daily-bar-cache/sip/`) + two additional trees for TI-08 alone: M43's period checkpoints and the `research-universe-expansion/bar-cache/` minute cache |
| FH-3 | TI-09–TI-11 | 1 shared (the M52 cross-sectional-momentum tree) |

The sharing within FH-2 and FH-3 is the same structural dependence P3A already flagged for multiplicity purposes: TI-10 and TI-11 are paired against TI-09 on the identical cache, and TI-08 reuses TI-05–TI-07's arms. The blast radius of a Phase-2 failure differs by tree, and is set out precisely in `P3B_PHASE1_READINESS_DECISION.md`.

## Missing or ambiguous dependencies

- **No cache directory is missing, and no input location remains unresolved.** TI-08's three input locations were traced statically and all are present (table above).
- **The resolution surfaced a cross-family dependency**: TI-08 consumes M43's period-checkpoint directory, which TI-01 through TI-03 also depend on.
- **Factor series remain absent entirely.** No Fama–French or UMD data exists in any cache. P2 classified this `NOT_AVAILABLE` and Phase 1 confirms no local cache changes that: any factor-attribution component of TI-08–TI-11 remains blocked on a new, license-reviewed acquisition outside P3B scope.
- **Benchmark series** for the contested-comparator instances (TI-05–TI-09) would be drawn from the same Alpaca-derived caches and inherit both their unverified status and the redistribution constraint recorded in P2's licensing matrix.
