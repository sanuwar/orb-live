# M47: Bounded Sequential Second-Trade Large-Cap ORB Feasibility

**Issue:** [#346](https://github.com/sanuwar/orb-live/issues/346)
**Branch:** `agent/m47-sequential-second-trade-feasibility`
**Depends on:** Issue #336/M43 (`BE_AFTER_050R`, supported, unmodified), Issue #338/M43.5 (date-indexed research path, reused), Issue #332/M42 (`WAITED_FIRST_VALID_0950`, primary, reused byte-for-byte)
**Explicitly not carried forward as baseline:** Issue #340/M44 (`FALLBACK_FIRST_VALID_0950`, INSUFFICIENT_EVIDENCE), Issue #342/M45 (`LATER_FIRST_VALID_1020`, MIXED), Issue #344/M46 (small/mid-cap combined lane, NOT_PROMISING)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

## Purpose

One material question only: on a day where the supported `WAITED_FIRST_VALID_0950` policy enters and *fully closes* its first trade early enough, can one additional, independently valid, non-overlapping large-cap ORB trade materially improve frequency and R/month without destroying the baseline's positive trade quality or materially worsening drawdown? This is explicitly **not** M45's later-first-entry recovery (which replaces the timing of the FIRST trade on baseline no-trade days), not M46's small/mid-cap lane, not same-symbol re-entry, and not a simultaneous-position or portfolio-capital study.

## Policies Compared

- **BASELINE = `WAITED_FIRST_VALID_0950` + `BE_AFTER_050R`** (unchanged): the exact M43/M45 primary, unmodified.
- **`SEQUENTIAL_SECOND_VALID_1020`** (the one experimental policy): run the exact BASELINE first-entry/exit lifecycle unmodified. A second trade is eligible only when the first trade actually entered, actually closed, and closed strictly before 10:20 ET. The scan window is `(first-trade exit timestamp, 10:20 ET]` — strictly after exit, inclusive of 10:20. Every OTHER universe symbol (first-trade symbol excluded) is evaluated fresh via the real, unmodified `SignalEngine` at every completed minute in the window, reusing each symbol's own already-precomputed `SessionSignalInputs.window` — no candidate's gap/RVOL/breakout/approval is ever carried forward from an earlier minute. The earliest minute with at least one independently valid symbol wins; same-minute ties break by ticker ascending; no ranking by RVOL/gap/strength/sector/expected return. The winning candidate is wrapped and simulated through the identical `BE_AFTER_050R` exit arm. Maximum two entries/day, maximum one open position at any instant, no third trade.

## Implementation

`src/orb_sequential_second_trade_research.py` reuses `_waited_shadow_decision`, `build_candidate_shadow_decision`, and `simulate_exit_arm` unmodified for both legs, and reuses each symbol's already-precomputed `SessionSignalInputs.window` (built once per (symbol, session_date) by `precompute_inputs_table`) for the second-trade scan — no bar rescan, no new precompute infrastructure. `scripts/research_orb_sequential_second_trade.py` replicates the established per-milestone CLI wiring (`_period_universe`, `_session_dates_for`, `_session_bounds_provider_for`, `_fetch_period_bars`, `_RaiseOnCacheMiss`) exactly as M44/M45/M46 each already do. No production strategy, universe, `SignalEngine`, `ExitEngine`, or `Simulator` semantics were touched.

## A Real Bug Found and Fixed Before Finalizing

The first empirical run's CLI (`_evaluate_period`) passed the repository's raw default config (`minimum_gap_percent=1.0`, `minimum_relative_volume=3.0`) as `combo_config` into `build_day_results`, instead of the frozen `GAP_050_RVOL_175` combo (`sensitivity_config(base_config, gap_percent=0.50, rvol_multiple=1.75)`) that `evaluate_combo` had already used to build the candidate pool. This silently applied a *stricter* threshold than the frozen baseline to both the 09:50 WAITED re-check and the second-trade scan's fresh `SignalEngine` evaluation. It was caught by reproducing the pooled BASELINE parity check required before reading any experimental result: the first run produced only 31 closed baseline trades (vs. the required 122). Diagnosed by comparing the new pipeline's per-period candidate-day counts (which matched the old, already-verified M43 checkpoint's counts exactly: 35/34/38/31/53/18) against its entered/closed counts (which did not), isolating the discrepancy to the downstream re-evaluation config rather than candidate generation. Fixed at the source (`scripts/research_orb_sequential_second_trade.py` and the test file's own `_run` helper now build `combo_config` via `sensitivity_config` exactly as the M43 CLI does), all checkpoints were cleared and the full six-period simulation was rerun from cache. The corrected run reproduces BASELINE parity exactly (below). All results in this report are from the corrected run.

## Periods

The same six established periods, cache-only, `--fetch-missing` never used: P_MINUS_2 (2021-03-31..2022-03-29, 252 sessions), P_MINUS_1 (2022-03-30..2023-03-30, 252 sessions), P0 (2023-03-31..2024-04-02, 252 sessions), P1 (2024-04-03..2025-04-02, 252 sessions), P2 (2025-04-03..2026-04-06, 252 sessions), P3 (2026-04-07..2026-08-13, 90 sessions). 1,350 total pooled sessions.

## Baseline (M43/M45) Parity

`WAITED_FIRST_VALID_0950` + `BE_AFTER_050R` pooled result, computed from this milestone's own `build_day_results`/`baseline_rows`, reproduces M43/M45's own primary exactly:

| Metric | Required (frozen) | Reproduced |
|---|---:|---:|
| closed trades | 122 | 122 |
| trades/month | 1.898 | 1.898 |
| expectancy_r | 0.1911 | 0.19112974747208059 |
| PF_R | 1.727 | 1.7274710293702018 |
| cumulative_r | 23.318 | 23.31782919159383 |
| R/month | 0.363 | 0.363 |
| max_drawdown_r | 4.991 | 4.991 |

Bit-for-bit the same as M43/M45's own primary.

## Second-Trade Eligibility Funnel — Pooled

| Diagnostic | Value |
|---|---:|
| Baseline trade days (candidate present) | 209 |
| First trades entered (= closed, all entered trades close) | 122 |
| Days first trade closed **before 10:20 ET** (second-trade eligible) | 15 |
| Days ineligible: first trade never entered | 87 |
| Days ineligible: first trade closed at/after 10:20 ET | 107 |
| Second-trade scans run | 15 |
| Second-trade selections (`SELECTED`) | **0** |
| Second-trade scans with no valid candidate (`NO_VALID_CANDIDATE`) | 15 |
| Second trades entered | 0 |
| Second trades closed | 0 |
| DATA_INVALID / never-entered / never-closed second selections | 0 (none were ever selected) |

Only **15 of 209** baseline trade days (7.2%) even reach second-trade eligibility — `BE_AFTER_050R` commonly holds a position until the 15:45 time-exit or a later stop/target, so a close strictly before 10:20 ET is structurally rare. Of the 15 eligible days, **every single scan returned `NO_VALID_CANDIDATE`**: manually re-running the scan for one representative eligible day (2021-04-14, TSLA exit 10:02 ET) confirmed the mechanism genuinely evaluated 121 (symbol, minute) pairs across the remaining universe under the correct frozen 0.50%/1.75× thresholds and found zero approvals — the rejections were exactly what the frozen `GAP_050_RVOL_175` gate is supposed to produce (`"Opening gap is below the required threshold."` for universe-eligible symbols; the "not in approved universe" rejections were for symbols outside that period's native universe, e.g. BAC/INTC for P_MINUS_2 — the same pre-existing universe-membership behavior already baked into the frozen baseline's own 09:50 re-check, not new to this milestone).

## Per-Period Table

| Period | Sessions | Baseline days | Baseline closed | trades/mo | exp_r | PF_R | cum_r | max_dd_r | Eligible days | Selected |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P_MINUS_2 | 252 | 35 | 22 | 1.833 | 0.2863 | 2.537 | 6.298 | 2.039 | 2 | 0 |
| P_MINUS_1 | 252 | 34 | 23 | 1.917 | 0.3552 | 2.634 | 8.169 | 3.000 | 5 | 0 |
| P0 | 252 | 38 | 21 | 1.750 | 0.1454 | 1.410 | 3.053 | 2.438 | 2 | 0 |
| P1 | 252 | 31 | 16 | 1.333 | 0.2326 | 1.734 | 3.721 | 3.000 | 2 | 0 |
| P2 | 252 | 53 | 30 | 2.500 | 0.0140 | 1.044 | 0.420 | 4.991 | 2 | 0 |
| P3 | 90 | 18 | 10 | 2.333 | 0.1656 | 2.656 | 1.656 | 1.000 | 2 | 0 |
| **Pooled** | **1,350** | **209** | **122** | **1.898** | **0.1911** | **1.727** | **23.318** | **4.991** | **15** | **0** |

Second-trade eligibility (2, 5, 2, 2, 2, 2 = 15 days) is present in every period, and every period's scans uniformly return `NO_VALID_CANDIDATE` — the null result is consistent across all six periods, not driven by one anomalous period.

## Three Comparison Views — Pooled

| View | Closed trades | trades/month | expectancy_r | PF_R | cumulative_r | R/month | max_drawdown_r |
|---|---:|---:|---:|---:|---:|---:|---:|
| **BASELINE** (first trades only) | 122 | 1.898 | 0.1911 | 1.727 | 23.318 | 0.363 | 4.991 |
| **SEQUENTIAL_SECOND_VALID_1020** (combined) | 122 | 1.898 | 0.1911 | 1.727 | 23.318 | 0.363 | 4.991 |
| **SECOND_ONLY** (incremental) | 0 | — | — | — | — | — | — |

The combined view is bit-for-bit identical to BASELINE, since the mechanism added exactly zero second trades. No frequency gain, no R/month change, no drawdown change.

## Daily Combined-R Diagnostics

| Diagnostic | Value |
|---|---:|
| Worst single-day combined R | −1.0 (a single baseline full-stop loss; never a two-leg day) |
| Days with two losing legs | 0 |
| Days with two closed legs at all | 0 |
| Can two sequential losses produce a −2R day? | Never observed — no day ever had two closed legs |

## Symbol Contribution / Best-Symbol Concentration (SECOND_ONLY, n=0)

Not computable — zero closed SECOND_ONLY trades. `second_only_symbol_robustness(())` returns `best_r_contributor=None`, `best_r_contribution_share=None`, `expectancy_r_excluding_best_contributor=None` (covered by a dedicated unit test).

## Second-Trade Decision-Time Distribution / Time Between First Exit and Second Decision

Not computable — zero selections were ever made, so no decision timestamps exist to bucket. (The 15 eligible days' own first-exit timestamps ranged from 14:01 UTC to 15:19 UTC, i.e. as early as 10:01 ET and as late as 11:19 ET on the research-calendar-shifted P_MINUS_2/P_MINUS_1 sessions — see the funnel diagnostic above; several eligible windows were nonetheless full ~18-20 minute scans, not truncated ones, which is part of why the null result is not attributable to an unreasonably short window.)

## Recency-Weighted View

Not applicable beyond the pooled unweighted result above: with zero closed SECOND_ONLY trades in any period, a recency-weighted SECOND_ONLY view is definitionally empty, and the combined view's recency-weighted figures are identical to BASELINE's own (already established in M45's report) since the combined view equals BASELINE bit-for-bit.

## Predeclared Classification

Criteria frozen in Issue #346 before any empirical P_MINUS_2..P3 result was read (see `src/orb_sequential_second_trade_research.py::classify_sequential_second_trade`).

```
second_only_n_at_least_30:                        FALSE  (0 < 30)
```

Condition 1 alone determines the outcome under the milestone's own predeclared rule: fewer than 30 genuinely closed SECOND_ONLY trades is `INSUFFICIENT_EVIDENCE` outright, regardless of the remaining nine conditions.

**VERDICT: INSUFFICIENT_EVIDENCE.**

`second_only_n = 0`, far below the 30-trade minimum. This is not a boundary case (e.g. 25-29) that a slightly larger sample might resolve differently — the eligibility funnel itself is narrow (15 of 209 baseline days, 7.2%) and every one of those 15 windows independently produced zero qualifying candidates under the frozen 0.50%/1.75× thresholds.

## Why the Result Is Structurally Null, Not a Data Artifact

Two independent factors compound to produce zero second trades:

1. **Narrow eligibility window.** `BE_AFTER_050R` is designed to let winners run to the 15:45 time-exit or a later stop/target; a close strictly before 10:20 ET is the exception, not the rule (92.8% of baseline trade days are ineligible for a second trade for exactly this reason).
2. **Strict entry gate applied fresh at every scan minute.** The `GAP_050_RVOL_175` gate (0.50% gap, 1.75× RVOL) that makes the frozen baseline's own trade quality strong (PF_R=1.727) is, by rule #15 of the frozen protocol, applied identically to the second-trade scan — and a random 15-20 minute window between 10:00-10:20 ET is a very short interval for an *additional*, previously-unqualified large-cap symbol to independently clear that same bar.

Both factors are inherent to the frozen mechanism as specified, not implementation defects — confirmed by the manual re-scan of the 2021-04-14 eligible day (121 fresh evaluations, correctly rejected for the correct, verifiable reasons) and by the exact baseline-parity reproduction (proving the shared machinery behaves identically to the already-validated M43/M45 pipeline).

## No Combination / Cutoff Mining

No alternative scan-end cutoff (10:05/10:10/10:30/10:45/11:00) was tested. No same-symbol re-entry, no simultaneous-position variant, no third trade, no alternative tie-break/ranking, no new gap/RVOL threshold, no different universe, no small/mid-cap symbols, no regime/day-of-week filter, no alternative exit policy, no different risk percentage, no portfolio-margin or $30K capital-path simulation was touched anywhere in this milestone, regardless of the null result. Per the milestone's own anti-analysis-paralysis instruction: the result is preserved as-is, not tuned.

## Roadmap Recommendation

Per the frozen decision tree: **INSUFFICIENT_EVIDENCE → record the bounded result, do not widen the scan window or universe to manufacture sample size.** Sequential second-trade large-cap ORB is parked with a definitive null finding (not "unknown" — the mechanism was correctly implemented, parity-verified, and produced a clean, structurally-explained zero). The large-cap frequency checkpoint remains unchanged from M45: **1.898 trades/month**, far below the 17 trades/month threshold that would deprioritize the liquid small/mid-cap momentum lane (M46, itself already classified NOT_PROMISING and parked). No further large-cap frequency lever is queued by this milestone; any future frequency work should look outside single/sequential-trade mechanics on the existing large-cap universe.

## Safety

`strategy.enabled` remains `false` throughout. No broker-write code was added. No production entry/exit/candidate-ranking/threshold/universe behavior changed. `src/orb_sequential_second_trade_research.py` and `scripts/research_orb_sequential_second_trade.py` are research-only, reusing M41/M43/M43.5 helpers unmodified; `src/market_data.py` and every other pinned-closure file were never touched. The one additive change to `src/orb_profit_protection_exit_research.py` (two new defaulted fields on `ExitArmOutcome` — `entry_timestamp`/`exit_timestamp`, exposing timestamps already computed internally but previously discarded) is not in `PINNED_IMPLEMENTATION_CLOSURE` and is covered by the full M43/M44/M45 regression suite (byte-identical baseline reproduction). Trusted implementation digest confirmed unchanged before and after this milestone: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`.

## Structured Artifacts

Generated under `artifacts/research-sequential-second-trade/` (gitignored, regenerable from cache via `python scripts/research_orb_sequential_second_trade.py`): per-period simulation checkpoints under `period-checkpoint/`. No structured JSON/CSV report was generated beyond this document and the checkpoint cache, since the null result requires no further derived-metrics artifact beyond what is already tabulated above.
