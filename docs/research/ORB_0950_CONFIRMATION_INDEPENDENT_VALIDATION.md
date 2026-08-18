# M42: Independent Validation of the 09:50 First-Candidate Confirmation Policy

**Issue:** [#332](https://github.com/sanuwar/orb-live/issues/332)
**Branch:** `agent/m42-independent-0950-confirmation-validation`
**Depends on:** Issue #325 (CHALLENGER config `GAP_050_RVOL_175`), Issue #330/M41 (`WAITED_FIRST_VALID_0950` policy discovery, reused unmodified)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

## Purpose

M41 found that `WAITED_FIRST_VALID_0950` was the strongest and most robust of five candidate-selection/timing arms across P_MINUS_1/P0-P3. Because those five periods were used to *discover* that finding, they cannot independently validate it. This milestone answers exactly one question on one completely untouched period ("P_MINUS_2"): **does `WAITED_FIRST_VALID_0950` retain a defensible advantage over `CURRENT_FIRST_VALID`?**

This milestone is deliberately narrow: no candidate ranking, no fallback candidate, no second trade, no new timing variant, no threshold search, no exit/profit-protection work.

## Frozen Design

- **Config:** `GAP_050_RVOL_175` only (gap≥0.50%, RVOL≥1.75x) — no other combination evaluated.
- **Two policies only:**
  - `CURRENT_FIRST_VALID` — today's existing baseline (full 09:40-11:00 window, first-valid chronologically, immediate execution).
  - `WAITED_FIRST_VALID_0950` — identify the first candidate qualifying at or before 09:50 ET, preserve that identity, do not enter immediately; at 09:50, re-run the real, unmodified `SignalEngine` on the SAME symbol using the first realistically available bar at/after 09:50; if it still qualifies, enter at that realistic price; if not, **no trade that day — never fall back to another candidate**, even if a later-qualifying candidate would still be valid.
- **Implementation reuse (not reimplementation):** `WAITED_FIRST_VALID_0950` is built by calling M41's own `early_pool`, `enrich_with_decision_time`, and `build_wait_row` directly from `src/orb_candidate_selection_timing_research.py`, unmodified. Verified programmatically: `test_m42_waited_policy_matches_m41_implementation_on_identical_fixture` and its CURRENT-policy counterpart run M41's `build_all_arms_rows` and M42's `build_both_policies_rows` on the identical synthetic fixture and assert the resulting decision/outcome fields are byte-identical (modulo the `period`/`view` labels, which are expected to differ).
- **Universe:** the same fixed #320 `ANCHORED_COHORT` (AAPL, AMD, AMZN, BAC, GOOGL, INTC, MSFT, NVDA, TSLA) used for P_MINUS_1, reused unchanged — not re-derived, not chosen based on P_MINUS_2's own P&L. **Methodological caveat preserved, not fixed**: this cohort was established from later periods, so its backward application into P_MINUS_2 may carry survivorship/universe-selection bias.

### P_MINUS_2 (frozen, derived programmatically before any P&L was read)

252 research-calendar trading sessions strictly before P_MINUS_1's own start date (2022-03-30):

**P_MINUS_2_START = 2021-03-30**
**P_MINUS_2_END = 2022-03-29**
**SESSION_COUNT = 252**

Confirmed non-overlapping with P_MINUS_1/P0/P1/P2/P3 via a dedicated test.

### 2021 research calendar extension

`src/orb_research_historical_calendar.py` was re-verified as **not** part of `PINNED_IMPLEMENTATION_CLOSURE` before being touched. Extended following the file's own established 2022/2023/2024/2025 representation style: 9 full holidays for 2021 (no Juneteenth — 2021 predates NYSE's first observance of it in 2022) plus one previously-undocumented closure, 2021-12-31 (New Year's Day 2022 observed, since 2022-01-01 fell on a Saturday), which only became relevant once the floor was extended back far enough to reach it. One early close (day after Thanksgiving only — Independence Day fell on a Sunday and Christmas Eve was itself the observed Christmas Day full holiday that year, so neither of the usual bridge half-days applies to 2021). Source: NYSE published 2021 holiday/early-close schedule, computed and cross-checked programmatically (weekday/nth-weekday arithmetic plus the historically known 2021 Easter date for Good Friday), not inferred from weekday alone.

### Predeclared classification (frozen before reading P_MINUS_2 results)

Minimum adequate sample: WAITED closed trades ≥ 15, else `INSUFFICIENT_SAMPLE`.

`VALIDATED` requires **all seven**: (1) WAITED expectancy_r>0, (2) WAITED PF_R>1, (3) closed≥15, (4) WAITED expectancy_r > CURRENT expectancy_r, (5) WAITED PF_R > CURRENT PF_R, (6) WAITED max_drawdown_r≤15R, (7) WAITED expectancy_r excluding its best R-contributing symbol >0.

`MIXED`: WAITED expectancy_r>0 and PF_R>1 and adequate sample, but one or more of the remaining VALIDATED conditions fails.

`FAILED`: WAITED expectancy_r≤0 OR PF_R≤1.

## Data

Alpaca IEX preflight (SPY + AAPL, small slice around P_MINUS_2_START) succeeded before any full fetch: real historical bars returned, session-aligned (first eligible bar exactly at 09:30 ET), freshness policy accepted the data. Full fetch then required (P_MINUS_2 is genuinely new, unlike P_MINUS_1/P0-P3 which were already cached): cache integrity before=2859 usable/0 removed, after=2956 usable/0 removed — **97 new cache files** for the 9-symbol universe + SPY, zero broadening of the universe, zero refetch of any prior period's data.

**Empirical runtime:** 2508.2s (≈42 minutes) — as designed, much smaller than M41 (one period, two policies, no rankers, M41's precompute/execution machinery reused directly).

## Results

### CURRENT_FIRST_VALID

closed=47, expectancy_r=+0.2820, PF_R=1.825, cumulative_r=+13.256, gross_profit_r=29.315, gross_loss_r=16.060, average_winner_r=+1.396, average_loser_r=−0.618, win_rate=44.7%, stop_out_rate=44.7%, max_drawdown_r=6.155, longest losing streak=7.
Frequency: 18.65% of 252 sessions traded, ≈3.92 trades/month, ≈1.10 R/month.

### WAITED_FIRST_VALID_0950

closed=22, expectancy_r=+0.1928, PF_R=1.637, cumulative_r=+4.242, gross_profit_r=10.906, gross_loss_r=6.664, average_winner_r=+1.212, average_loser_r=−0.513, win_rate=40.9%, stop_out_rate=36.4%, max_drawdown_r=6.325, longest losing streak=4.
Frequency: 8.73% of 252 sessions traded, ≈1.83 trades/month, ≈0.35 R/month.

### Deltas (WAITED vs. CURRENT)

- Expectancy_r delta: **−0.0892** (WAITED lower)
- PF_R delta: **−0.188** (WAITED lower)
- Max-drawdown delta: +0.170R (essentially the same, WAITED marginally higher)
- R/month delta: −0.751 (WAITED trades far less often, so cumulative pace is materially lower)

### WAIT-specific diagnostics

- 35 days had a candidate by 09:50; 22 survived re-validation (**62.9% survival rate**); 13 were rejected at 09:50; 0 days had missing decision-time data.
- Average wait duration from first signal to 09:50: **7.5 minutes**.
- First-candidate signal-time distribution: 14 at 09:40, 4 at 09:41, 2 at 09:42, 4 at 09:43, 3 at 09:44, 1 at 09:45, 1 at 09:46, 3 at 09:47, 3 at 09:48 — heavily front-loaded at the entry window's own start.
- 25 days: CURRENT trades, WAITED does not (13 explicit 09:50 rejections + 12 days where CURRENT's eventual winner qualified after 09:50, outside WAITED's early pool entirely).
- 22 days both policies trade — **every single one** on the same symbol (100% same-symbol overlap): whenever WAITED enters, it is trading the identical name CURRENT would have picked too.
- Paired R delta (both-trade days): mean **−0.197R** per day in WAITED's favor... i.e. against WAITED, consistent with the overall underperformance.
- Win/loss transitions on the 22 paired days: 8 both win, 11 both lose, 2 where waiting turned a CURRENT win into a WAITED loss, 1 where waiting turned a CURRENT loss into a WAITED win — net unfavorable to waiting on this period (2 turned bad vs. 1 turned good).

### Symbol robustness (WAITED)

Best R contributor: **NVDA**, contributing **79.8%** of WAITED's combined cumulative R — a much higher concentration than seen in prior studies. Top 3: NVDA, MSFT, TSLA. Expectancy_r excluding NVDA: **+0.0476** — still positive, but only barely (the `not_single_symbol_dependent` check technically passes, by a thin margin). Excluding AAPL: +0.2606. Excluding TSLA: +0.2301.

## Classification

**MIXED.** Checks: `expectancy_r_positive`=True, `profit_factor_r_above_1`=True, `adequate_sample`=True, `acceptable_drawdown`=True, `not_single_symbol_dependent`=True (thin margin), **`expectancy_r_beats_current`=False**, **`profit_factor_r_beats_current`=False**.

On this one independent, untouched period, `WAITED_FIRST_VALID_0950` retains a positive R edge (expectancy_r>0, PF_R>1, adequate sample of 22 closed trades, acceptable drawdown) but does **not** outperform `CURRENT_FIRST_VALID` — the opposite of M41's own P0-P3/P_MINUS_1 pattern, where WAITED consistently beat CURRENT. This is a genuinely important, non-favorable independent-evidence result that was not adjusted or reinterpreted after being read.

## Whether the 09:50 confirmation independently validates

**No — not fully.** Per the predeclared rule, MIXED (not VALIDATED) is the honest classification: the policy is not disproven (still R-positive with adequate sample), but this independent period does not corroborate M41's finding that waiting-and-revalidating beats the immediate baseline. The 09:50 core is labeled **PROVISIONAL**, not validated.

## Next Milestone

Per the frozen decision structure: MIXED with WAITED still R-positive and PF_R>1 → **`PROFIT_PROTECTION_EXIT_EXPERIMENT`**, with the 09:50 core explicitly labeled **PROVISIONAL** rather than fully validated. No further timing/candidate-selection sweep is authorized by this result (per the milestone's own anti-paralysis rule — MIXED-with-positive-edge routes forward, not into another timing search).

## Anti-Data-Mining / Anti-Paralysis Confirmation

No 09:45/09:55/other-duration variant was tested. No best-candidate/fallback/ranking logic was introduced — candidate identity, once established by 09:50, was never switched (verified directly: `test_first_candidate_identity_cannot_switch_no_fallback_on_rejection`). No new gap/RVOL/universe was evaluated. No exit/stop/target/profit-protection change was made. The classification and decision structure were frozen before P_MINUS_2 results were read and were not modified afterward.

## Structured Artifacts

Generated under `artifacts/research-0950-confirmation-independent-validation/results/` (gitignored, regenerable from cache via `python scripts/research_orb_0950_confirmation_independent_validation.py`): `orb_0950_confirmation_independent_validation_report.json` — full structured report (all fields above).
