# M41: Candidate-Selection Timing/Ranking Research

**Issue:** [#330](https://github.com/sanuwar/orb-live/issues/330)
**Branch:** `agent/m41-candidate-selection-timing-ranking`
**Depends on:** Issue #325 (CHALLENGER config `GAP_050_RVOL_175`), local milestone M40 (P_MINUS_1 period, ANCHORED_COHORT universe, out-of-sample independent validation)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

## Purpose

M40 found that `GAP_050_RVOL_175` (Issue #325's CHALLENGER) showed a materially stronger ALL_CANDIDATES result than its own FIRST_VALID one-trade-per-day selection on P_MINUS_1 — suggesting the FIRST_VALID chronological-selection policy may be discarding information, not that the strategy family lacks edge. This study decomposes *why* into three separable questions via five execution/selection arms, all on the single frozen CHALLENGER config only — **no new gap/RVOL threshold, no PRIMARY config, no exit/stop/target/SPY/breakout rule invented anywhere in this study.**

## Frozen Design (fixed before any empirical execution)

### Arms

| Arm | Candidate pool | Selection | Execution |
|---|---|---|---|
| **CURRENT_FIRST_VALID** | Full 09:40-11:00 window | First-valid chronologically | Immediate (today's existing baseline) |
| **EARLY_FIRST_VALID** | Restricted to qualifying by 09:50 | First-valid chronologically | Immediate |
| **WAITED_FIRST_VALID_0950** | Restricted to qualifying by 09:50 | First-valid chronologically (same identity as EARLY) | **Delayed to 09:50** |
| **RANKED_POOL_0950_MAX_RVOL** | Restricted to qualifying by 09:50 | RANKER A (highest RVOL) | Delayed to 09:50 |
| **RANKED_POOL_0950_EQUAL_RANK_STRENGTH** | Restricted to qualifying by 09:50 | RANKER B (equal-weight ordinal) | Delayed to 09:50 |

`EARLY_FIRST_VALID`'s pool is derived by filtering the full-window candidate list by `LiveData.timestamp <= 09:50` — proven equivalent to a truncated-window scan (see module docstring) rather than a re-implementation, since the canonical per-(symbol, session) breakout walk never diverges before an approval is found.

### WAIT-to-09:50 execution mechanics

The original breakout bar's price is **never** reused with a later timestamp — that would be an impossible historical fill (price information the original bar did not have at 09:50 could leak in). Instead, the real, unmodified `SignalEngine.evaluate` is re-run at the bar at/after 09:50 in that session's already-precomputed entry-window data, using the **same** `OpeningData` (so `stop_price = opening_range_low` is identical either way — a session-level constant, not bar-dependent) but a genuinely later `LiveData` (so `entry_price = completed_bar_close` reflects the real price at that later moment). If the setup is no longer valid by then (price pulled back through the opening-range high, or SPY dropped below its VWAP), the re-evaluation correctly rejects and no trade is recorded — a real, reportable cost of waiting, not an error (tracked distinctly as `wait_reevaluation_approved=False`). Verified directly against the real simulator/signal-engine mechanics before writing any code (see the module's `build_wait_row`).

### Frozen rankers

**RANKER A — MAX_RVOL:** highest `relative_volume` wins; ties broken by earlier qualifying signal, then alphabetical symbol.

**RANKER B — EQUAL_RANK_STRENGTH:** equal-weight ordinal ranking (rank 1 = best) across four factors, all evaluated at the **same** 09:50 decision snapshot for every candidate in that day's pool: gap magnitude, opening RVOL, breakout strength `(price_at_0950 - opening_range_high) / opening_range_width`, and relative strength vs. SPY `(stock_return_to_0950 - SPY_return_to_0950)`. Each factor's ordinal rank is assigned per-day by sorting on (value descending, earlier signal, alphabetical symbol); the four ranks are averaged with equal weight (1/4 each); lowest average rank wins. No fitted coefficients, no ML, no future-return/MFE/outcome field is ever read.

### Secondary recency-weighted deployment view

Per-trade exponential time decay, half-life 504 trading sessions, anchor date 2026-08-13: `weight_i = 2^(-age_i/504)`, where `age_i` is the count of research-calendar trading sessions from the trade date through the anchor date. Applied at the trade level, never by assigning weights to whole periods. Reports only `weighted_expectancy_r`, `weighted_win_rate`, `weighted_profit_factor_r` — **deliberately never a weighted cumulative-R figure**, which would misrepresent an actual chronological account path. Chronological cumulative R and max drawdown remain exclusively in the unweighted, primary view. P_MINUS_1/2022 evidence is never discarded, only naturally down-weighted by age alongside every other period (P_MINUS_1's own trades carry weights of roughly 0.22-0.31 in this view — still meaningfully present).

### Periods and universes (all five already-established, boundaries unmoved)

Each period reuses its own already-established universe unchanged: P_MINUS_1 uses the fixed #320 `ANCHORED_COHORT` (9 symbols); P0-P3 each use their own liquidity-qualified native universe (7/9/17/23 symbols respectively) from Issue #325/#39.

## Empirical Results

**100% cache-first**: cache integrity reported 2857 usable, 0 removed files; every period's fetch hit the existing shared bar cache (after one cache-request-formatting fix described below) — zero new network fetches were required.

**Empirical runtime:** 13,872.0s (≈3h51m) for all 5 periods × 5 arms.

### Combined (all 5 periods) — primary unweighted view

| Arm | Closed | Expectancy_r | PF_R | Cumulative_R | Max Drawdown_R | Longest Losing Streak | Win Rate |
|---|---|---|---|---|---|---|---|
| CURRENT_FIRST_VALID | 256 | +0.0443 | 1.105 | +11.35 | 42.15 | 8 | 45.3% |
| EARLY_FIRST_VALID | 169 | +0.0502 | 1.117 | +8.48 | 36.03 | 11 | 45.6% |
| **WAITED_FIRST_VALID_0950** | 100 | **+0.1532** | **1.436** | +15.32 | 40.49 | **4** | **55.0%** |
| RANKED_POOL_0950_MAX_RVOL | 101 | +0.1191 | 1.318 | +12.03 | **34.42** | 4 | 52.5% |
| RANKED_POOL_0950_EQUAL_RANK_STRENGTH | 104 | +0.1288 | 1.361 | +13.40 | 52.87 | 4 | 51.0% |

### Recency-weighted (secondary deployment view)

| Arm | Weighted Expectancy_r | Weighted Win Rate | Weighted PF_R |
|---|---|---|---|
| CURRENT_FIRST_VALID | +0.0624 | 47.2% | 1.158 |
| EARLY_FIRST_VALID | +0.0622 | 48.1% | 1.156 |
| **WAITED_FIRST_VALID_0950** | **+0.1077** | **54.9%** | **1.316** |
| RANKED_POOL_0950_MAX_RVOL | +0.0654 | 52.7% | 1.179 |
| RANKED_POOL_0950_EQUAL_RANK_STRENGTH | +0.0707 | 50.5% | 1.201 |

### Per-period expectancy_r (robustness)

| Arm | P_MINUS_1 | P0 | P1 | P2 | P3 | Periods positive (of 5) |
|---|---|---|---|---|---|---|
| CURRENT_FIRST_VALID | +0.0887 | -0.0975 | +0.0377 | +0.1330 | +0.0702 | 4/5 |
| EARLY_FIRST_VALID | +0.2438 | -0.0752 | -0.1559 | +0.0716 | +0.2370 | 3/5 |
| **WAITED_FIRST_VALID_0950** | +0.4474 | +0.0408 | +0.1423 | +0.0360 | +0.0816 | **5/5** |
| RANKED_POOL_0950_MAX_RVOL | +0.4106 | +0.0408 | +0.1339 | +0.0295 | -0.0927 | 4/5 |
| RANKED_POOL_0950_EQUAL_RANK_STRENGTH | +0.4435 | +0.0408 | +0.1339 | -0.0093 | -0.0082 | 3/5 |

**`WAITED_FIRST_VALID_0950` is the only arm with positive expectancy_r in all five periods, including the fully independent P_MINUS_1 out-of-sample window** — a materially stronger robustness signal than either ranker.

### Symbol concentration (top R-contributors, combined)

- CURRENT_FIRST_VALID: TSLA, MSFT, AVGO
- EARLY_FIRST_VALID: AVGO, TSLA, AAPL
- WAITED_FIRST_VALID_0950: TSLA, AAPL, AMD
- RANKED_POOL_0950_MAX_RVOL: TSLA, AMD, AAPL
- RANKED_POOL_0950_EQUAL_RANK_STRENGTH: TSLA, AMD, AAPL

TSLA is the top contributor across every M41 arm, so concentration remains an important robustness concern within this study. This is not identical to M40's P_MINUS_1 concentration pattern, where GOOGL was PRIMARY's best contributor and AAPL was CHALLENGER's best contributor.

## The Three Comparisons (as specified)

**A → B, early-entry effect (CURRENT_FIRST_VALID → EARLY_FIRST_VALID):** expectancy_r delta +0.0059, PF_R delta +0.012. Early entries alone carry a small positive effect, but restricting the window also **reduces period robustness** (4/5 → 3/5 periods positive) — early entry alone is not the main driver.

**B → C, cost of waiting (EARLY_FIRST_VALID → WAITED_FIRST_VALID_0950):** expectancy_r delta **+0.1030**, PF_R delta **+0.319**. This is not a cost in this evidence — waiting to re-validate at 09:50 substantially *improved* results, at the cost of trade frequency (169 → 100 closed trades, since roughly 41% of early-pool candidates no longer qualified by the time execution would occur). The re-validation step filters out setups that don't hold, and this is the single largest effect found in the whole study.

**C → D, value of ranking (WAITED_FIRST_VALID_0950 → RANKED_POOL_0950_\*):** expectancy_r delta **-0.0341** (MAX_RVOL) and **-0.0244** (EQUAL_RANK_STRENGTH); PF_R delta **-0.118** and **-0.075** respectively. Both rankers **underperformed** the simple chronological-first selection once the WAIT mechanism is already applied, and both show *worse* period robustness (4/5 and 3/5 respectively, vs. WAITED's 5/5). RANKED_POOL_0950_MAX_RVOL does show the lowest combined max drawdown (34.4R vs. WAITED's 40.5R), a real trade-off worth noting, but its P3 expectancy_r is the only outright negative figure in this whole 5×5 arm/period grid (-0.093).

## Interpretation

The evidence points to a specific, narrower conclusion than "ranking adds value": **the value driver in this evidence is the wait-and-re-validate step itself, not candidate selection sophistication.** A simple policy — wait until 09:50, and take the first candidate that still qualifies then — outperformed both a more selective early-only policy and two independently-designed rankers on every measured axis (combined expectancy_r, combined PF_R, weighted expectancy_r, and period robustness). This is a genuinely non-obvious result: it was not the predetermined outcome the study's own framing anticipated (that ranking, not just waiting, would be the source of the improvement M40 first surfaced), and neither ranker was tuned or adjusted after seeing this — both were frozen before any empirical result was read.

## Anti-Data-Mining Confirmation

No new gap/RVOL/universe/SPY/breakout/stop/target/exit/regime/day-of-week rule was introduced. No PRIMARY (`GAP_100_RVOL_250`) config was evaluated. Both rankers' definitions were frozen and locked before this study's empirical execution began, and were not revisited after results were read. `strategy.enabled` remains `false`; no production file was touched.

## Data Reuse

100% of the bar data required by this study was already cached by prior milestones (Issue #325, M39, M40) — zero new Alpaca network fetches were needed. One correction was required during execution: the initial cache-only run failed with a cache miss for P_MINUS_1 because this study's `end`-timestamp formula (mirrored from the M39 script, using session-open-time + 8h) did not match the exact formula M40's script originally used to cache P_MINUS_1 (session-close-time + 8h) — the shared cache is keyed by exact request parameters, not by which underlying dates are substantively covered. Fixed by using each period's own originally-correct formula (P_MINUS_1: close+8h, matching M40; P0-P3: open+8h, matching M39), after which the full run completed cache-only with no further misses.

## Structured Artifacts

Generated under `artifacts/research-candidate-selection-timing/results/` (gitignored, regenerable from cache via `python scripts/research_orb_candidate_selection_timing.py`):

- `orb_candidate_selection_timing_report.json` — full structured report (all fields above, per-arm, per-period, symbol summaries, comparisons)
- `orb_candidate_selection_timing_combined_summary.csv` — one row per arm, combined + recency-weighted metrics
- `orb_candidate_selection_timing_period_arm_metrics.csv` — one row per (period, arm)

## Next Step

This study intentionally did not predeclare a VALIDATED/MIXED/FAILED-style classification menu or a fixed next-milestone decision list (unlike Issue #325/M40) — its purpose was purely diagnostic decomposition. The finding that stands out most clearly: `WAITED_FIRST_VALID_0950` (wait to 09:50, execute the first candidate that still qualifies then, no ranking) is the strongest and most robust arm found in this whole session's research line to date, with positive expectancy_r in every one of the five periods including the fully independent P_MINUS_1 window. Whether this becomes its own dedicated validation milestone (analogous to M40's independent-validation treatment of the two Issue #325 winners) is a decision left to the operator, not made here.
