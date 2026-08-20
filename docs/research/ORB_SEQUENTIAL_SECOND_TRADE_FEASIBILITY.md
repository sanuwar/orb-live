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

## Two Real Bugs Found and Fixed Before Finalizing

**Bug 1 — wrong gap/RVOL config.** The first empirical run's CLI (`_evaluate_period`) passed the repository's raw default config (`minimum_gap_percent=1.0`, `minimum_relative_volume=3.0`) as `combo_config` into `build_day_results`, instead of the frozen `GAP_050_RVOL_175` combo (`sensitivity_config(base_config, gap_percent=0.50, rvol_multiple=1.75)`) that `evaluate_combo` had already used to build the candidate pool. This silently applied a *stricter* threshold than the frozen baseline to both the 09:50 WAITED re-check and the second-trade scan's fresh `SignalEngine` evaluation. Caught by reproducing the mandatory pooled BASELINE parity check before reading any experimental result (the first run produced only 31 closed baseline trades vs. the required 122). Fixed by building `combo_config` via `sensitivity_config` exactly as the M43 CLI does.

**Bug 2 — wrong universe for the second-trade scan (independent-review finding).** `scan_for_second_candidate`'s own `SignalEngine` was built from `combo_config` unmodified, which carries whatever `universe.symbols` the base config provides — the tracked *production* 15-symbol universe (`AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AVGO, AMD, JPM, XOM, LLY, COST, WMT, NFLX`), not the period's own frozen *native research universe*. Several periods' native universes contain symbols outside that production list — P_MINUS_2/P_MINUS_1/P1: `BAC, INTC`; P2: `BAC, INTC, CSCO, KO, NKE, VZ, T`; P3: `BAC, INTC, V, CSCO, NOW, CRM, KO, VZ, BSX, NKE, SLB` (P0's native universe has no symbols outside the production list). `SignalEngine.evaluate` rejects any symbol outside `config["universe"]["symbols"]` with `"Symbol is not in the approved universe."` *before* gap/RVOL is ever evaluated, so these symbols were silently excluded from the second-trade scan regardless of how genuinely valid their breakout was — the same class of bug M46 hit and fixed for its own universe.

Note: BAC and INTC were never actually outside any *research* universe — they are squarely inside the periods' own frozen native research universes; the bug was that the second-trade scan's `SignalEngine` was evaluating against the unrelated *production config's* default universe instead.

Fixed by building the scan's evaluation config via `research_config_for_universe` (M46's own helper, reused unmodified) against the exact `universe` parameter already passed to `scan_for_second_candidate` for that period, plus a runtime `assert evaluation_config["universe"]["symbols"] == list(universe)` and a new `evaluation_universe` audit field on `SecondScanResult` recording exactly what universe each scan actually ran with. This fix is scoped to the second-trade scan only — the BASELINE trade's own 09:50 `_waited_shadow_decision` re-check is untouched, since changing it would be a retroactive edit to already-supported/frozen M43/M45 baseline behavior, not an M47 addition.

Because this changes empirical scan semantics, all M47 checkpoints were deleted and the full six-period simulation was rerun from the existing cache after both fixes. All results in this report are from that corrected run, with `evaluation_universe` verified equal to the period's native universe for every one of the 15 real second-trade scans that ran (see **Universe-Correctness Audit** below).

## Periods

The same six established periods, cache-only, `--fetch-missing` never used: P_MINUS_2 (2021-03-31..2022-03-29, 252 sessions), P_MINUS_1 (2022-03-30..2023-03-30, 252 sessions), P0 (2023-03-31..2024-04-02, 252 sessions), P1 (2024-04-03..2025-04-02, 252 sessions), P2 (2025-04-03..2026-04-06, 252 sessions), P3 (2026-04-07..2026-08-13, 90 sessions). 1,350 total pooled sessions.

## Baseline (M43/M45) Parity

`WAITED_FIRST_VALID_0950` + `BE_AFTER_050R` pooled result, computed from this milestone's own `build_day_results`/`baseline_rows`, reproduces M43/M45's own primary exactly — **unchanged by the universe fix**, as expected, since that fix is scoped only to the second-trade scan and never touches the baseline's own re-check:

| Metric | Required (frozen) | Reproduced |
|---|---:|---:|
| closed trades | 122 | 122 |
| trades/month | 1.898 | 1.898 |
| expectancy_r | 0.1911 | 0.19112974747208059 |
| PF_R | 1.727 | 1.7274710293702018 |
| cumulative_r | 23.318 | 23.31782919159383 |
| R/month | 0.363 | 0.363 |
| max_drawdown_r | 4.991 | 4.991 |

Bit-for-bit the same as M43/M45's own primary, and bit-for-bit identical to the pre-universe-fix run (as required — a baseline-parity change would have meant the fix leaked into baseline behavior, which it does not).

## Terminology (corrected)

Three distinct counts, not to be conflated:

- **209** = candidate-present days: sessions where the frozen early-window scan produced at least one WAITED-policy candidate. This is *not* "baseline trade days" in the sense of trades actually taken — most of these candidates fail the fresh 09:50 re-check.
- **122** = first trades actually entered and closed (100% of entries close under this policy/exit-arm combination; none are left open).
- **15** = of those 122 entered/closed first trades, the ones that closed strictly before 10:20 ET and are therefore second-trade eligible.

Two distinct, meaningful rates:

- **Candidate-funnel eligibility: 15 / 209 = 7.18%** (of all candidate-present days, how many end up eligible for a second trade).
- **Eligibility among entered first trades: 15 / 122 = 12.30%** (of first trades that actually happened, how many closed early enough to allow a second one).

## Second-Trade Eligibility Funnel — Pooled

| Diagnostic | Value |
|---|---:|
| Candidate-present days | 209 |
| First trades entered and closed | 122 |
| Candidate-present days where the first trade never entered (rejected at 09:50 re-check) | 87 |
| Entered/closed first trades that closed at or after 10:20 ET (second-trade ineligible) | 107 |
| Entered/closed first trades that closed **before 10:20 ET** (second-trade eligible) | 15 |
| Second-trade scans run | 15 |
| Second-trade selections (`SELECTED`) | **0** |
| Second-trade scans with no valid candidate (`NO_VALID_CANDIDATE`) | 15 |
| Second trades entered | 0 |
| Second trades closed | 0 |
| DATA_INVALID / never-entered / never-closed second selections | 0 (none were ever selected) |

Of the 122 first trades that actually entered and closed, only **15 (12.30%)** closed early enough to reach second-trade eligibility — `BE_AFTER_050R` commonly holds a position until the 15:45 time-exit or a later stop/target, so a close strictly before 10:20 ET is the exception. Of the 15 eligible days, **every single scan returned `NO_VALID_CANDIDATE`**, now verified against the corrected period-native universe (see below).

## Universe-Correctness Audit (post-fix)

For every one of the 15 real second-trade scans, `SecondScanResult.evaluation_universe` was asserted (at runtime, during the actual simulation) and independently re-verified (from the saved per-period checkpoints, after the run) to equal that period's own native universe — never the production default:

| Period | Native universe used by the scan | Symbols outside the production default universe |
|---|---|---|
| P_MINUS_2 | AAPL, AMD, AMZN, BAC, GOOGL, INTC, MSFT, NVDA, TSLA | BAC, INTC |
| P_MINUS_1 | AAPL, AMD, AMZN, BAC, GOOGL, INTC, MSFT, NVDA, TSLA | BAC, INTC |
| P0 | NVDA, AAPL, MSFT, TSLA, AMZN, GOOGL, AMD | (none) |
| P1 | NVDA, AAPL, TSLA, AMZN, MSFT, GOOGL, AMD, BAC, INTC | BAC, INTC |
| P2 | NVDA, TSLA, AAPL, AMZN, MSFT, GOOGL, META, AVGO, AMD, INTC, BAC, WMT, CSCO, KO, NKE, VZ, T | BAC, CSCO, INTC, KO, NKE, T, VZ |
| P3 | NVDA, AMZN, AAPL, MSFT, GOOGL, META, TSLA, INTC, AMD, AVGO, NFLX, BAC, V, CSCO, NOW, WMT, CRM, KO, JPM, VZ, BSX, NKE, SLB | BAC, BSX, CRM, CSCO, INTC, KO, NKE, NOW, SLB, V, VZ |

A direct re-run of one representative eligible day with instrumentation (P3, 2026-04-09, META exit 10:01 ET) confirms the fix took effect: `evaluation_config["universe"]["symbols"]` matched P3's full 23-symbol native universe exactly, all 22 other symbols were evaluated (376 total (symbol, minute) pairs across the scan window; BSX and CRM were skipped for `INSUFFICIENT_RVOL_LOOKBACK_HISTORY`, a genuine pre-existing data-completeness gap unrelated to the universe fix), and **every rejection was gap/RVOL-based** (`"Opening gap is below the required threshold."` ×339, `"Opening relative volume is below the required threshold."` ×37) — **zero** `"Symbol is not in the approved universe."` rejections, versus the pre-fix run where BAC/INTC alone accounted for 35 such rejections on a single P_MINUS_2 day. The universe fix is confirmed working; the resulting `NO_VALID_CANDIDATE` outcome is a genuine gap/RVOL result, not a universe artifact.

## Second-Trade Exit-Timestamp Distribution (the 15 eligible days, corrected DST-aware conversion)

| Session date | Period | First symbol | Exit (UTC) | Exit (ET) |
|---|---|---|---:|---:|
| 2026-04-09 | P3 | META | 14:01 | 10:01 EDT |
| 2021-04-14 | P_MINUS_2 | TSLA | 14:02 | 10:02 EDT |
| 2023-11-10 | P0 | AAPL | 15:04 | 10:04 EST |
| 2023-04-24 | P0 | AMZN | 14:05 | 10:05 EDT |
| 2024-10-04 | P1 | AMD | 14:05 | 10:05 EDT |
| 2024-04-22 | P1 | AMD | 14:06 | 10:06 EDT |
| 2026-06-02 | P3 | NVDA | 14:07 | 10:07 EDT |
| 2021-08-04 | P_MINUS_2 | AMD | 14:13 | 10:13 EDT |
| 2022-04-21 | P_MINUS_1 | TSLA | 14:13 | 10:13 EDT |
| 2022-04-27 | P_MINUS_1 | MSFT | 14:15 | 10:15 EDT |
| 2025-09-05 | P2 | TSLA | 14:17 | 10:17 EDT |
| 2022-08-04 | P_MINUS_1 | TSLA | 14:18 | 10:18 EDT |
| 2023-01-18 | P_MINUS_1 | AAPL | 15:18 | 10:18 EST |
| 2025-04-23 | P2 | META | 14:18 | 10:18 EDT |
| 2023-02-23 | P_MINUS_1 | NVDA | 15:19 | 10:19 EST |

Earliest exit: **10:01 ET**. Latest exit: **10:19 ET** — every eligible day's exit is correctly within `[10:01, 10:19]` ET, consistent with the `< 10:20 ET` eligibility rule by construction (an exit at or after 10:20 ET is ineligible by definition and cannot appear in this table). Each UTC timestamp above is converted using its own session date's actual DST offset (`.astimezone(EASTERN)`, EDT March-November / EST November-March), not a fixed UTC-4/UTC-5 assumption; the prior version of this report incorrectly applied a mismatched offset to some rows, producing an impossible "11:19 ET" figure for an eligible (by definition sub-10:20-ET) day — corrected here.

Scan windows ranged from as short as 1 minute (10:19→10:20 ET) to as long as 19 minutes (10:01→10:20 ET); most eligible days had a scan window of 10+ minutes, so the null result is not attributable to unreasonably short windows across the board, though the shortest individual windows are genuinely narrow.

## Per-Period Table

| Period | Sessions | Candidate-present days | First trades entered/closed | trades/mo | exp_r | PF_R | cum_r | max_dd_r | Eligible days | Selected |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P_MINUS_2 | 252 | 35 | 22 | 1.833 | 0.2863 | 2.537 | 6.298 | 2.039 | 2 | 0 |
| P_MINUS_1 | 252 | 34 | 23 | 1.917 | 0.3552 | 2.634 | 8.169 | 3.000 | 5 | 0 |
| P0 | 252 | 38 | 21 | 1.750 | 0.1454 | 1.410 | 3.053 | 2.438 | 2 | 0 |
| P1 | 252 | 31 | 16 | 1.333 | 0.2326 | 1.734 | 3.721 | 3.000 | 2 | 0 |
| P2 | 252 | 53 | 30 | 2.500 | 0.0140 | 1.044 | 0.420 | 4.991 | 2 | 0 |
| P3 | 90 | 18 | 10 | 2.333 | 0.1656 | 2.656 | 1.656 | 1.000 | 2 | 0 |
| **Pooled** | **1,350** | **209** | **122** | **1.898** | **0.1911** | **1.727** | **23.318** | **4.991** | **15** | **0** |

Second-trade eligibility (2, 5, 2, 2, 2, 2 = 15 days) and the resulting `NO_VALID_CANDIDATE` outcome are **unchanged from the pre-universe-fix run**, per period — the fix widened what each scan evaluated but did not change which minute (if any) each scan's own gap/RVOL/breakout gate approved, for any of the 15 eligible days. The null result is present in every period, not driven by one anomalous period.

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

Not computable — zero closed SECOND_ONLY trades. `second_only_symbol_robustness(())` returns `best_r_contributor=None`, `best_r_contribution_share=None`, `expectancy_r_excluding_best_contributor=None`. The concentration formula itself was corrected during independent review: the denominator is each symbol's own **net** cumulative R (summed per symbol, keeping only symbols whose net is positive), not the sum of individual winning trades' R in isolation — a symbol with more losing R than winning R now correctly contributes nothing to either the numerator or the denominator, even if some of its individual trades were winners. Covered by dedicated unit tests, including one fixture that fails under the prior (incorrect) formula and passes under the corrected one.

## Second-Trade Decision-Time Distribution / Time Between First Exit and Second Decision

Not computable — zero selections were ever made, so no second-trade decision timestamps exist to bucket. The first-exit timestamp distribution for the 15 eligible days is tabulated above (corrected DST-aware conversion).

## Recency-Weighted View

Not applicable beyond the pooled unweighted result above: with zero closed SECOND_ONLY trades in any period, a recency-weighted SECOND_ONLY view is definitionally empty, and the combined view's recency-weighted figures are identical to BASELINE's own (already established in M45's report) since the combined view equals BASELINE bit-for-bit.

## Predeclared Classification

Criteria frozen in Issue #346 before any empirical P_MINUS_2..P3 result was read (see `src/orb_sequential_second_trade_research.py::classify_sequential_second_trade`).

```
second_only_n_at_least_30:                        FALSE  (0 < 30)
```

Condition 1 alone determines the outcome under the milestone's own predeclared rule: fewer than 30 genuinely closed SECOND_ONLY trades is `INSUFFICIENT_EVIDENCE` outright, regardless of the remaining nine conditions.

**VERDICT: INSUFFICIENT_EVIDENCE.**

`second_only_n = 0`, far below the 30-trade minimum. This was recomputed honestly after the universe-membership fix (not assumed to remain unchanged) — the fix genuinely altered the second-trade scan's own evaluation config (the P3 rescan above shows universe-membership rejections dropping from a nonzero count to exactly zero) and was rerun through the full six-period simulation, and the result remains n=0. This is not a boundary case (e.g. 25-29) that a slightly larger sample might resolve differently — the eligibility funnel itself is narrow (15 of 122 entered first trades, 12.3%) and every one of those 15 windows independently produced zero qualifying candidates under the frozen 0.50%/1.75× thresholds, now confirmed evaluated against the correct, full period-native universe.

## Why the Result Is Structurally Null, Not a Data Artifact

Two independent factors compound to produce zero second trades:

1. **Narrow eligibility window.** `BE_AFTER_050R` is designed to let winners run to the 15:45 time-exit or a later stop/target; a close strictly before 10:20 ET is the exception, not the rule (only 15 of 122 entered/closed first trades, 12.3%, are second-trade eligible).
2. **Strict entry gate applied fresh at every scan minute, against the correct universe.** The `GAP_050_RVOL_175` gate (0.50% gap, 1.75× RVOL) that makes the frozen baseline's own trade quality strong (PF_R=1.727) is, by rule #15 of the frozen protocol, applied identically to the second-trade scan, now correctly evaluated against each period's own full native universe (not the smaller production default) — and a random 1-19 minute window between 10:01-10:20 ET is a short interval for an *additional*, previously-unqualified large-cap symbol to independently clear that same bar, even across a larger candidate pool.

Both factors are inherent to the frozen mechanism as specified, not implementation defects. This was directly confirmed post-fix by re-running the P3 2026-04-09 scan with instrumentation: 376 fresh (symbol, minute) evaluations across the full 23-symbol native universe, zero universe-membership rejections, every rejection legitimately gap- or RVOL-based.

## No Combination / Cutoff Mining

No alternative scan-end cutoff (10:05/10:10/10:30/10:45/11:00) was tested. No same-symbol re-entry, no simultaneous-position variant, no third trade, no alternative tie-break/ranking, no new gap/RVOL threshold, no different universe beyond the correctness fix above, no small/mid-cap symbols, no regime/day-of-week filter, no alternative exit policy, no different risk percentage, no portfolio-margin or $30K capital-path simulation was touched anywhere in this milestone, regardless of the null result. Per the milestone's own anti-analysis-paralysis instruction: the result is preserved as-is, not tuned.

## Roadmap Recommendation

Per the frozen decision tree: **INSUFFICIENT_EVIDENCE → record the bounded result, do not widen the scan window or universe to manufacture sample size.** Sequential second-trade large-cap ORB is parked with a definitive null finding (not "unknown" — the mechanism was correctly implemented (including a mid-review universe-membership correctness fix), parity-verified, and produced a clean, structurally-explained, and now universe-correctness-audited zero). The large-cap frequency checkpoint remains unchanged from M45: **1.898 trades/month**, far below the 17 trades/month threshold that would deprioritize the liquid small/mid-cap momentum lane (M46, itself already classified NOT_PROMISING and parked). No further large-cap frequency lever is queued by this milestone; any future frequency work should look outside single/sequential-trade mechanics on the existing large-cap universe.

## Safety

`strategy.enabled` remains `false` throughout. No broker-write code was added. No production entry/exit/candidate-ranking/threshold/universe behavior changed — `research_config_for_universe` builds an in-memory-only deep copy and never writes to `config/strategy_v0.yaml`. No production `SignalEngine`/`ExitEngine`/`Simulator` semantics changed. `src/orb_sequential_second_trade_research.py` and `scripts/research_orb_sequential_second_trade.py` are research-only, reusing M41/M43/M43.5/M46 helpers unmodified; `src/market_data.py` and every other pinned-closure file were never touched. The one additive change to `src/orb_profit_protection_exit_research.py` (two new defaulted fields on `ExitArmOutcome` — `entry_timestamp`/`exit_timestamp`, exposing timestamps already computed internally but previously discarded) is not in `PINNED_IMPLEMENTATION_CLOSURE` and is covered by the full M43/M44/M45 regression suite (byte-identical baseline reproduction) plus a dedicated propagation/serialization test. Trusted implementation digest confirmed unchanged before and after this milestone: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`.

## Structured Artifacts

Generated under `artifacts/research-sequential-second-trade/` (gitignored, regenerable from cache via `python scripts/research_orb_sequential_second_trade.py`): per-period simulation checkpoints under `period-checkpoint/`. No structured JSON/CSV report was generated beyond this document and the checkpoint cache, since the null result requires no further derived-metrics artifact beyond what is already tabulated above.
