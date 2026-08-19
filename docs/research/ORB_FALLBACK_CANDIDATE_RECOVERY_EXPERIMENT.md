# M44: ORB Fallback-Candidate Trade-Frequency Recovery Experiment

**Issue:** [#340](https://github.com/sanuwar/orb-live/issues/340)
**Branch:** `agent/m44-fallback-candidate-recovery`
**Depends on:** Issue #336/M43 (`BE_AFTER_050R` protection, supported), Issue #338/M43.5 (date-indexed research path), Issue #330/M41-M42 (`WAITED_FIRST_VALID_0950` primary, entry policy reused byte-for-byte)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

## Purpose

One material question only: when the primary pre-09:50 candidate (`WAITED_FIRST_VALID_0950`) fails the frozen 09:50 revalidation, can a profitable trade day be recovered by taking the earliest OTHER candidate that was already present in the pre-09:50 pool and is independently valid at 09:50? This is entry-frequency research only — no gap/RVOL/universe/exit-rule change, no ranking, no second trade, no new checkpoint. It exists because the current primary policy produces only ~1.9 trades/month against an economic objective of roughly 17-20 quality trades/month on a $30,000 account at 1% fixed risk; this experiment tests whether fallback recovery is *one* legitimate source of additional trade days, not the whole frequency answer.

## Policies Compared

- **BASELINE = `WAITED_FIRST_VALID_0950`** (unchanged): build the pre-09:50 candidate pool (M41's `early_pool`, unmodified), select the first candidate by qualifying-timestamp order, re-evaluate that same candidate at 09:50 with fresh data. If valid, enter it; if invalid, no trade.
- **`FALLBACK_FIRST_VALID_0950`** (the one experimental policy): same pool, same ordering (original qualifying signal timestamp ascending, symbol ascending tie-break — `select_first_candidate_per_day`'s own tie-break, reused identically). Re-evaluate the primary at 09:50; if valid, enter it and stop (fallback never inspected). If invalid, iterate the *remaining* already-existing pre-09:50 candidates in the same frozen order, re-evaluating each independently with fresh 09:50 data, and take the first that is valid. No ranking by RVOL/gap/breakout-strength/sector; no "best" candidate; a still-valid primary is never abandoned.

Both policies use exactly one exit arm throughout: **`BE_AFTER_050R`** (M43's supported protection: once a completed bar reaches ≥+0.50R favorable excursion, the stop moves to entry beginning the next bar; same-bar rule preserved). No exit-rule variant is compared — M43 already answered the exit question.

## Implementation

`src/orb_fallback_candidate_recovery_research.py` reuses M41's `early_pool`/`group_by_day`/`enrich_with_decision_time`/`DecisionTimeCandidate` unmodified, and M43's own `_waited_shadow_decision` (the exact SignalEngine re-evaluation + shadow-decision construction mechanism `WAITED_FIRST_VALID_0950` already uses) unmodified, called once per attempted candidate. `build_day_outcomes` computes ONE shared per-day attempt sequence — re-evaluating candidates primary-first and stopping at the first approval — from which BASELINE's row (`attempts[0]`) and FALLBACK's row (`attempts[-1]`) are both derived; no SignalEngine call is ever duplicated between the two policies, and BASELINE's own evaluation is never redone for FALLBACK. The M43.5 (Issue #338) date-indexed bar-retrieval path is used throughout via `index_market_bars_by_date`.

`src/market_data.py` (pinned) was never touched. No pinned production file changed.

## Periods

The same six established periods, cache-only, `--fetch-missing` never used: P_MINUS_2 (2021-03-31..2022-03-29), P_MINUS_1 (2022-03-30..2023-03-30), P0 (2023-03-31..2024-04-02), P1 (2024-04-03..2025-04-02), P2 (2025-04-03..2026-04-06), P3 (2026-04-07..2026-08-13). `cache integrity: 3053 usable, 0 removed` before the run.

## Runtime and Bottleneck

**Empirical runtime: 7,523.5s (≈2h05m)**, cache-only, zero new fetches, per-period checkpointing (a restart would not have lost completed periods).

A genuinely large, surprising bottleneck was found and is reported here rather than fixed (out of this milestone's scope, per its own explicit instruction not to add another infrastructure optimization milestone): `src/orb_gap_rvol_sensitivity_research.py`'s `build_candidate_shadow_decision` calls its own **un-indexed** `_session_bars` (a full linear rescan+sort of the entire per-symbol multi-month bar history) plus a market-data-digest SHA-256 hash over every session bar, on **every attempted candidate** — not just the primary. M43.5 (Issue #338) indexed the equivalent hot paths in `orb_universe_expansion_research.py` and `orb_profit_protection_exit_research.py`, but `build_candidate_shadow_decision`'s own `_session_bars` was out of that milestone's scope and remains unindexed. Since FALLBACK re-evaluates additional candidates only on primary-failure days, this cost scales with (primary-failure days × pool size), which was large enough on the two most recent, largest-universe periods (P2/P3) to dominate the run. This is a candidate for a future, separately-scoped M43.5-style optimization, not something addressed in M44.

## Baseline (M43) Parity

`WAITED_FIRST_VALID_0950` + `BE_AFTER_050R` pooled result reproduces M43's own primary exactly: 122 closed trades, expectancy_r=0.1911, PF_R=1.727, cumulative_r=23.318, R/month=0.363, max_drawdown_r=4.991, full-stop rate=21.31% — bit-for-bit the same as M43's merged result. Confirmed both by the empirical run and by `test_baseline_matches_m43_waited_primary_exactly` (byte/field-identical outcome comparison against M43's own `build_all_entry_and_exit_rows`).

## Primary Metrics — Pooled (all six periods)

| Metric | BASELINE | FALLBACK |
|---|---:|---:|
| **trades/month** | 1.898 | **2.007** |
| **expectancy_r/trade** | 0.1911 | 0.1909 |
| **R/month** | 0.363 | **0.383** |
| **max_drawdown_r** | 4.991 | **4.574** |
| closed trades | 122 | 129 |
| trade days | 122 | 129 |
| % sessions with a trade | 9.04% | 9.56% |
| PF_R | 1.727 | 1.768 |
| cumulative_r | 23.318 | 24.624 |
| gross_profit_r | 55.371 | 56.723 |
| gross_loss_r | 32.053 | 32.099 |
| win rate | 44.26% | 44.96% |
| average_winner_r | +1.025 | +1.019 |
| average_loser_r | −0.471 | −0.464 |
| full −1R stop rate | 21.31% | 20.16% |
| longest losing streak | 6 | 6 |

(1,350 total pooled sessions across all six periods; % sessions with a trade = closed_trades / 1,350.)

## Fallback-Mechanics Diagnostics

| Diagnostic | Value |
|---|---:|
| Days with any pre-09:50 candidate | 209 |
| Days primary survives 09:50 | 124 |
| Days primary fails 09:50 | 85 |
| — of which no-decision-time-data (honest gap) | 0 |
| — of which SignalEngine-invalid (real rejection) | 85 |
| Primary-failure days with ≥1 additional early candidate | 21 |
| **Days recovered by fallback** (fallback selected a non-primary candidate that was SignalEngine-approved) | **9** |
| Recovery rate among primary-failure days | 10.59% |
| Recovery rate among primary-failure days *with alternatives* | 42.86% |
| Fallback candidate position: second | 8 |
| Fallback candidate position: third | 1 |
| Fallback candidate position: fourth+ | 0 |
| Fallback symbols | AAPL×1, AMD×1, AVGO×2, GOOGL×1, MSFT×3, NVDA×1 |
| Days with alternatives where all failed | 12 |

**Critical honesty check — SignalEngine approval ≠ a real trade.** Of the 9 days SignalEngine approved a fallback candidate at 09:50, only **2** actually produced a closed trade. The other 7 resolved `DATA_INVALID` when `simulate_shadow_execution` (pinned, unmodified) attempted to walk the fallback candidate's own post-decision bars — a genuine market-data-quality gap specific to that non-primary candidate's session data, not a strategy rejection and not fabricated as one. This is exactly why the fallback-only trade-quality sample below is n=2, not n=9, and it is the dominant reason for this study's classification.

## Fallback-Only Trade Quality (the 2 genuinely recovered, closed trades)

| Metric | Value |
|---|---:|
| n | 2 |
| expectancy_r | +0.0426 |
| PF_R | ∞ (no losses) |
| cumulative_r | +0.0853 |
| win rate | 50.0% |
| full-stop rate | 0.0% |
| max_drawdown_r | 0.0 |
| R/month contribution | 0.0013 |

Both trades are from P3: `AVGO` (2026-05-26, STOPPED at exactly r_multiple=0.0 — `BE_AFTER_050R` had already moved the stop to breakeven before the stop-out, so this is a protected breakeven exit, not a full loss; `is_winner=False`) and `GOOGL` (2026-08-03, TIME_EXIT, r_multiple=+0.0853, winner). This sample is far too small to support any conclusion about the fallback mechanism's own edge — see classification below.

## Period Robustness

| Period | BASELINE trades/mo | BASELINE exp_r | BASELINE R/mo | BASELINE maxDD | FALLBACK trades/mo | FALLBACK exp_r | FALLBACK R/mo | FALLBACK maxDD | Fallback-only n | Fallback-only exp_r | Fallback-only cum_r |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P_MINUS_2 | 1.833 | 0.2863 | 0.5249 | 2.039 | 1.833 | 0.2863 | 0.5249 | 2.039 | 0 | — | — |
| P_MINUS_1 | 1.917 | 0.3552 | 0.6807 | 3.000 | 2.083 | 0.3409 | 0.7102 | 3.000 | 2 | — | — |
| P0 | 1.750 | 0.1454 | 0.2544 | 2.438 | 1.750 | 0.1454 | 0.2544 | 2.438 | 0 | — | — |
| P1 | 1.333 | 0.2326 | 0.3101 | 3.000 | 1.417 | 0.2189 | 0.3101 | 3.000 | 1 | — | — |
| P2 | 2.500 | 0.0140 | 0.0350 | 4.991 | 2.667 | 0.0402 | 0.1073 | 4.574 | 2 | — | — |
| P3 | 2.333 | 0.1656 | 0.3865 | 1.000 | 2.800 | 0.1451 | 0.4064 | 1.000 | 2 | +0.0426 | +0.0853 |

(Fallback-only expectancy_r/cumulative_r are `—` where n<2 makes a per-period expectancy computation uninformative on its own — see the pooled fallback-only figures above; individual-period breakdowns are diagnostic only, not a basis for a regime-derived rule, per this study's own scope boundary.) Two periods (P_MINUS_2, P0) recovered zero trade days at all; P2 (the largest, most recent universe) recovered the most (2 SignalEngine-approved candidates, though its own DATA_INVALID rate meant neither closed).

## Symbol Robustness (fallback-only recovered trades, n=2)

| Metric | Value |
|---|---:|
| Best R contributor | GOOGL (100% of cumulative R) |
| Expectancy_r excluding best contributor | 0.0 |
| Expectancy_r excluding AAPL | 0.0426 (AAPL not present in this n=2 sample) |
| Expectancy_r excluding TSLA | 0.0426 (TSLA not present) |
| Top-3 concentration | 100% (only 2 symbols total exist in the sample) |

With only 2 trades, symbol concentration is definitionally total and uninformative — reported as required, not treated as a finding. Diagnostics only; no symbol exclusion is introduced anywhere in this study.

## Recency-Weighted View (secondary; half-life 504 sessions, anchor 2026-08-13)

| | Weighted expectancy_r | Weighted win rate | Weighted PF_R |
|---|---:|---:|---:|
| BASELINE | 0.1457 | 44.23% | 1.556 |
| FALLBACK | 0.1487 | 45.50% | 1.613 |
| FALLBACK-only (n=2) | 0.0440 | 51.62% | ∞ |

Directionally consistent with the pooled unweighted view; the fallback-only row is not a basis for any conclusion given its sample size. No weighted cumulative-R was computed (per this study's own frozen scope, reused unmodified from M41).

## Economic Target Tracking

| | Value |
|---|---:|
| Baseline trades/month | 1.898 |
| Fallback-policy trades/month | 2.007 |
| Absolute gain | +0.109 trades/month |
| Percentage gain | +5.74% |
| Remaining gap to 17 trades/month | 14.99 |
| Remaining gap to 20 trades/month | 17.99 |

**Simple fixed-risk research arithmetic only** ($30,000 account, 1% fixed risk → 1R = $300; `monthly_equivalent_usd = R_per_month × $300`). **NOT a chronological capital simulation, NOT a live-return forecast, NOT the final $1K feasibility study. No compounding; risk % held fixed.**

| | R/month | Monthly-equivalent USD (simple arithmetic) |
|---|---:|---:|
| BASELINE | 0.363 | $108.82 |
| FALLBACK | 0.383 | $114.91 |

Fallback recovery alone closes roughly 0.6% of the gap to 17 trades/month and 0.5% of the gap to 20 trades/month. This experiment was never expected to close the frequency gap alone — see "Frozen Next-Step Decision" below.

## Predeclared Classification

Criteria frozen before any P_MINUS_2..P3 result was read (see `src/orb_fallback_candidate_recovery_research.py::classify_fallback_recovery`, `MIN_FALLBACK_ONLY_INSUFFICIENT_N = 10`).

**Fallback-only closed trades = 2, below the 10-trade INSUFFICIENT_EVIDENCE floor.**

```
VERDICT: INSUFFICIENT_EVIDENCE
reason: fallback-only closed trades=2 below 10
```

Per the frozen rule, no criterion below n=10 is even evaluated — this is not "positive but small," it is a floor violation, and the study does not manufacture more history or weaken the threshold to force a verdict.

## Why the Combined-Policy Numbers Still Look Favorable

The pooled FALLBACK-policy numbers (trades/month +5.7%, R/month +5.5%, max_drawdown_r −8.4%) look directionally positive, but per this study's own predeclared rule, **the classification is governed by the fallback-only sample size, not by the combined-policy metrics** — a policy-level improvement built on 2 genuine trades (out of 9 nominally "approved" but 7 DATA_INVALID) is not evidence the fallback mechanism itself has an edge; it may just as easily be noise. Reporting the combined numbers is required by this study's own metric list, but they are explicitly **not** the basis for the verdict.

## Frozen Next-Step Decision

**INSUFFICIENT_EVIDENCE** → per the frozen decision tree: record insufficient evidence, do not manufacture more history or weaken criteria, and move to **LATER-FIRST-ENTRY RECOVERY** as the next frequency question. This is **not begun in this branch** — a separate milestone, separate issue, separate branch, and separate PR are required, exactly as M44's own scope boundary requires.

## No Combination / Ranking Mining

No fallback-ranking variant (highest-RVOL, highest-gap, strongest-breakout, sector-based) was tested. No alternate checkpoint (09:45/09:47/09:52) was tested. No second trade, no post-09:50 new candidate, no broader universe, no threshold change, no alternative stop rule, no risk sizing or portfolio allocation was touched anywhere in this milestone. Interesting-idea items go into the future-research queue only, never into this milestone's implementation.

## Safety

`strategy.enabled` remains `false` throughout. No broker-write code was added. No production entry/exit/candidate-ranking/threshold/universe behavior changed. `src/orb_fallback_candidate_recovery_research.py` and `scripts/research_orb_fallback_candidate_recovery.py` are research-only, reusing M41/M43/M43.5 helpers unmodified; `src/market_data.py` (pinned) was never touched. Trusted implementation digest confirmed unchanged before and after this milestone: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`.

## Structured Artifacts

Generated under `artifacts/research-fallback-candidate-recovery/results/` (gitignored, regenerable from cache via `python scripts/research_orb_fallback_candidate_recovery.py`): `orb_fallback_candidate_recovery_report.json` (full structured report), `orb_fallback_candidate_recovery_summary.csv`. A per-period simulation checkpoint cache (`artifacts/research-fallback-candidate-recovery/period-checkpoint/`, also gitignored) lets the ~2-hour empirical stage be skipped on a rerun if only the cheap reporting/classification code changes; delete it (or pass `--no-checkpoint`) to force a clean recompute.
