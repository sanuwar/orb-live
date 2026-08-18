# M42: Independent Validation of the 09:50 First-Candidate Confirmation Policy

**Issue:** [#332](https://github.com/sanuwar/orb-live/issues/332) (original), [#334](https://github.com/sanuwar/orb-live/issues/334) (calendar correction)
**Branch:** `agent/m42-correct-2021-calendar-rerun` (corrective; original `agent/m42-independent-0950-confirmation-validation`, merged as PR #333)
**Depends on:** Issue #325 (CHALLENGER config `GAP_050_RVOL_175`), Issue #330/M41 (`WAITED_FIRST_VALID_0950` policy discovery, reused unmodified)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

## ⚠️ Correction Notice

The result originally published by PR #333 is **SUPERSEDED**. That run's research calendar incorrectly treated **2021-12-31 as an NYSE full-day closure** ("New Year's Day 2022 observed," since 2022-01-01 fell on a Saturday). NYSE historical market-data evidence confirms 2021-12-31 was a regular trading session. The mistake was applying the general "Saturday holiday → observed the preceding Friday" rule to New Year's Day specifically — this repository's own pinned, verified production calendar (`src/exchange_calendar.py`, `FULL_HOLIDAYS`, lines 190-192) already documents the correct rule: *"a holiday on a Saturday is observed the preceding Friday... with New Year's Day the standing exception, which is why there is no entry for a Friday before a Saturday 1 January."* This exception was not correctly applied when the research-only calendar was extended into 2021 for the original M42 run.

Because P_MINUS_2 is defined as *exactly* 252 sessions strictly before P_MINUS_1's start, incorrectly excluding 2021-12-31 shifted the whole window by one session. The section below marked **[SUPERSEDED — PR #333]** is preserved verbatim for provenance; the **corrected result (Issue #334)** follows it and is authoritative.

## Purpose

M41 found that `WAITED_FIRST_VALID_0950` was the strongest and most robust of five candidate-selection/timing arms across P_MINUS_1/P0-P3. Because those five periods were used to *discover* that finding, they cannot independently validate it. This milestone answers exactly one question on one completely untouched period ("P_MINUS_2"): **does `WAITED_FIRST_VALID_0950` retain a defensible advantage over `CURRENT_FIRST_VALID`?**

This milestone is deliberately narrow: no candidate ranking, no fallback candidate, no second trade, no new timing variant, no threshold search, no exit/profit-protection work. The Issue #334 correction is calendar-only — no trading rule of any kind was changed.

## Frozen Design (unchanged by the correction)

- **Config:** `GAP_050_RVOL_175` only (gap≥0.50%, RVOL≥1.75x) — no other combination evaluated.
- **Two policies only:**
  - `CURRENT_FIRST_VALID` — today's existing baseline (full 09:40-11:00 window, first-valid chronologically, immediate execution).
  - `WAITED_FIRST_VALID_0950` — identify the first candidate qualifying at or before 09:50 ET, preserve that identity, do not enter immediately; at 09:50, re-run the real, unmodified `SignalEngine` on the SAME symbol using the first realistically available bar at/after 09:50; if it still qualifies, enter at that realistic price; if not, **no trade that day — never fall back to another candidate**, even if a later-qualifying candidate would still be valid.
- **Implementation reuse (not reimplementation):** `WAITED_FIRST_VALID_0950` is built by calling M41's own `early_pool`, `enrich_with_decision_time`, and `build_wait_row` directly from `src/orb_candidate_selection_timing_research.py`, unmodified. Verified programmatically: `test_m42_waited_policy_matches_m41_implementation_on_identical_fixture` and its CURRENT-policy counterpart assert byte-identical decision/outcome fields against M41's own `build_all_arms_rows` on an identical synthetic fixture.
- **Universe:** the same fixed #320 `ANCHORED_COHORT` (AAPL, AMD, AMZN, BAC, GOOGL, INTC, MSFT, NVDA, TSLA) used for P_MINUS_1, reused unchanged. **Methodological caveat preserved, not fixed**: this cohort was established from later periods, so its backward application into P_MINUS_2 may carry survivorship/universe-selection bias.

### Predeclared classification (frozen before either run's P&L was read; unchanged by the correction)

Minimum adequate sample: WAITED closed trades ≥ 15, else `INSUFFICIENT_SAMPLE`.

`VALIDATED` requires **all seven**: (1) WAITED expectancy_r>0, (2) WAITED PF_R>1, (3) closed≥15, (4) WAITED expectancy_r > CURRENT expectancy_r, (5) WAITED PF_R > CURRENT PF_R, (6) WAITED max_drawdown_r≤15R, (7) WAITED expectancy_r excluding its best R-contributing symbol >0.

`MIXED`: WAITED expectancy_r>0 and PF_R>1 and adequate sample, but one or more of the remaining VALIDATED conditions fails.

`FAILED`: WAITED expectancy_r≤0 OR PF_R≤1.

---

## [SUPERSEDED — PR #333] Original Result (2021-12-31 incorrectly excluded)

P_MINUS_2_START = 2021-03-30, P_MINUS_2_END = 2022-03-29, 252 sessions.

CURRENT_FIRST_VALID: closed=47, expectancy_r=+0.2820, PF_R=1.825, cumulative_r=+13.256, max_drawdown_r=6.155, longest losing streak=7.
WAITED_FIRST_VALID_0950: closed=22, expectancy_r=+0.1928, PF_R=1.637, cumulative_r=+4.242, max_drawdown_r=6.325, longest losing streak=4.
Classification: MIXED. Next milestone: PROFIT_PROTECTION_EXIT_EXPERIMENT (PROVISIONAL).

**This result is invalid** — its 252-session window omitted a real trading day (2021-12-31) and is superseded by the corrected run below.

---

## Corrected Design and Result (Issue #334)

### 2021 research calendar correction

`src/orb_research_historical_calendar.py` re-verified as **not** part of `PINNED_IMPLEMENTATION_CLOSURE` before being touched (again). `RESEARCH_FULL_HOLIDAYS_2021` no longer includes 2021-12-31; it is now correctly treated as a regular trading session (09:30-16:00 ET), matching the exact rule already documented and verified in the repository's own pinned production calendar. 2021-11-26 (early close) and 2021-12-24 (Christmas observed) are unchanged. No other year was touched. A dedicated regression test (`test_2021_12_31_is_a_regular_trading_session_not_new_years_2022_observed`) proves this directly.

### P_MINUS_2 (corrected, derived programmatically before the corrected P&L was read)

**P_MINUS_2_START = 2021-03-31** (moved forward by exactly one trading session from the incorrect 2021-03-30, since one additional real trading day now falls inside the 252-session window)
**P_MINUS_2_END = 2022-03-29** (unchanged, as expected)
**SESSION_COUNT = 252**

Confirmed non-overlapping with P_MINUS_1/P0/P1/P2/P3 via a dedicated test.

### Data

97 new cache files fetched for the 9-symbol universe + SPY (cache integrity before=2956 usable/0 removed, after=3053 usable/0 removed) — the shifted exact fetch window requires a fresh request even though most underlying calendar days overlap with the prior (superseded) fetch; zero broadening of the universe, zero refetch of P_MINUS_1/P0/P1/P2/P3 data.

**Empirical runtime:** 2502.9s (≈41.7 minutes).

### CURRENT_FIRST_VALID (corrected)

closed=**48** (was 47), expectancy_r=**+0.2553** (was +0.2820), PF_R=**1.718** (was 1.825), cumulative_r=**+12.256** (was +13.256), gross_profit_r=29.315, gross_loss_r=17.060, average_winner_r=+1.396, average_loser_r=−0.632, win_rate=43.8%, stop_out_rate=45.8%, max_drawdown_r=6.155 (unchanged), longest losing streak=7 (unchanged).
Frequency: 19.05% of 252 sessions traded, ≈4.00 trades/month, ≈1.021 R/month.

The single newly-included session (2021-12-31) added exactly one CURRENT_FIRST_VALID trade, a full stop-out (r_multiple = −1.0), which is why cumulative_r fell by exactly 1.0 versus the superseded run.

### WAITED_FIRST_VALID_0950 (unchanged by the correction)

closed=22, expectancy_r=+0.1928, PF_R=1.637, cumulative_r=+4.242, gross_profit_r=10.906, gross_loss_r=6.664, average_winner_r=+1.212, average_loser_r=−0.513, win_rate=40.9%, stop_out_rate=36.4%, max_drawdown_r=6.325, longest losing streak=4.
Frequency: 8.73% of 252 sessions traded, ≈1.83 trades/month, ≈0.354 R/month.

2021-12-31 produced no early-pool (≤09:50) candidate for the WAITED policy, so every WAITED figure is numerically identical to the superseded run — only CURRENT_FIRST_VALID's figures changed.

### Deltas — defined as `WAITED − CURRENT` throughout (negative means against WAITED)

- Expectancy_r delta: **−0.0625** (against WAITED)
- PF_R delta: **−0.082** (against WAITED)
- Max-drawdown delta: **+0.170R** (WAITED's own drawdown is marginally higher; essentially unchanged from the superseded run since CURRENT's drawdown didn't move)
- R/month delta: **−0.668** (against WAITED — WAITED trades far less often, so its cumulative pace is materially lower)

### WAIT-specific diagnostics (unchanged except where CURRENT is the counterparty)

- 35 days had a candidate by 09:50; 22 survived re-validation (**62.9% survival rate**); 13 were rejected at 09:50; 0 days had missing decision-time data.
- Average wait duration from first signal to 09:50: **7.5 minutes**.
- First-candidate signal-time distribution: 14 at 09:40, 4 at 09:41, 2 at 09:42, 4 at 09:43, 3 at 09:44, 1 at 09:45, 1 at 09:46, 3 at 09:47, 3 at 09:48.
- **26** days (was 25): CURRENT trades, WAITED does not — the extra day is 2021-12-31 itself.
- 22 days both policies trade — **every single one** on the same symbol (100% same-symbol overlap).
- Paired R delta (`WAITED − CURRENT` on both-trade days): mean **−0.197R** — against WAITED, consistent with the overall underperformance. (2021-12-31 is not among these 22 days, since WAITED never traded it, so this figure is identical to the superseded run.)
- Win/loss transitions on the 22 paired days: 8 both win, 11 both lose, 2 where waiting turned a CURRENT win into a WAITED loss, 1 where waiting turned a CURRENT loss into a WAITED win — net unfavorable to waiting on this period.

### Symbol robustness (WAITED; unchanged by the correction)

Best R contributor: **NVDA**, contributing **79.8%** of WAITED's combined cumulative R. Top 3: NVDA, MSFT, TSLA. Expectancy_r excluding NVDA: **+0.0476** — still positive, but only barely (the `not_single_symbol_dependent` check technically passes, by a thin margin). Excluding AAPL: +0.2606. Excluding TSLA: +0.2301.

## Classification (corrected)

**MIXED** — same verdict as the superseded run, on corrected underlying numbers. Checks: `expectancy_r_positive`=True, `profit_factor_r_above_1`=True, `adequate_sample`=True, `acceptable_drawdown`=True, `not_single_symbol_dependent`=True (thin margin), **`expectancy_r_beats_current`=False**, **`profit_factor_r_beats_current`=False**.

On this one independent, untouched period, `WAITED_FIRST_VALID_0950` retains a positive R edge (expectancy_r>0, PF_R>1, adequate sample of 22 closed trades, acceptable drawdown) but does **not** outperform `CURRENT_FIRST_VALID` — the opposite of M41's own P0-P3/P_MINUS_1 pattern, where WAITED consistently beat CURRENT. This is a genuinely important, non-favorable independent-evidence result that was not adjusted or reinterpreted after being read, in either the original or the corrected run.

## Whether the 09:50 confirmation independently validates

**No — not fully.** Per the predeclared rule, MIXED (not VALIDATED) is the honest classification, on the corrected data exactly as it was on the (now-superseded) original data: the policy is not disproven (still R-positive with adequate sample), but this independent period does not corroborate M41's finding that waiting-and-revalidating beats the immediate baseline. The 09:50 core is labeled **PROVISIONAL**, not validated.

## Next Milestone

Per the frozen decision structure: MIXED with WAITED still R-positive and PF_R>1 → **`PROFIT_PROTECTION_EXIT_EXPERIMENT`**, with the 09:50 core explicitly labeled **PROVISIONAL** rather than fully validated. No further timing/candidate-selection sweep is authorized by this result.

## Anti-Data-Mining / Anti-Paralysis Confirmation

No 09:45/09:55/other-duration variant was tested. No best-candidate/fallback/ranking logic was introduced — candidate identity, once established by 09:50, was never switched (verified directly). No new gap/RVOL/universe was evaluated. No exit/stop/target/profit-protection change was made in either the original milestone or this calendar correction. The classification and decision structure were frozen before P_MINUS_2 results were read (both times) and were not modified afterward.

## Structured Artifacts

Generated under `artifacts/research-0950-confirmation-independent-validation/results/` (gitignored, regenerable from cache via `python scripts/research_orb_0950_confirmation_independent_validation.py`): `orb_0950_confirmation_independent_validation_report.json` — full structured report reflecting the corrected result above.
