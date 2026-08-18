# M43: ORB Profit-Protection Exit Experiment

**Issue:** [#336](https://github.com/sanuwar/orb-live/issues/336)
**Branch:** `agent/m43-profit-protection-exit-research`
**Depends on:** Issue #325 (CHALLENGER config `GAP_050_RVOL_175`), Issue #330/M41 (`WAITED_FIRST_VALID_0950`/`CURRENT_FIRST_VALID` entry policies, reused unmodified), Issue #332+#334/M42 (WAITED classified MIXED/PROVISIONAL — the reason this milestone exists)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

## Purpose

This is EXIT research only. M42 found `WAITED_FIRST_VALID_0950` MIXED/PROVISIONAL: R-positive with an adequate sample, but not beating `CURRENT_FIRST_VALID` on the one fully independent period tested. Earlier R-normalized MFE analysis found a substantial fraction of eventual losers first reach favorable excursion, including roughly the +0.50R region. This milestone asks one question: **can a small, predeclared stop-management policy protect some of that favorable excursion without materially destroying the strategy's R-normalized expectancy?**

Nothing about the entry side was touched: no gap/RVOL threshold, no 09:50 cutoff, no candidate ranking/universe, no second trades, no sector logic, no position sizing.

## Entry Policies

- **PRIMARY:** `WAITED_FIRST_VALID_0950` on `GAP_050_RVOL_175` — M41/M42's own implementation reused byte-for-byte (`early_pool`, `enrich_with_decision_time`, `select_first_candidate_per_day`, the SignalEngine re-evaluation inside what M41 calls `build_wait_row`). Verified in `test_entry_policy_candidate_identity_matches_m41_exactly` and `test_baseline_arm_is_byte_identical_to_m41_outcome_through_the_full_pipeline` (candidate identity and BASELINE-arm outcome are identical to M41's `build_all_arms_rows` output, through the real SignalEngine/simulator pipeline, not just at the unit level).
- **Sensitivity benchmark:** `CURRENT_FIRST_VALID` — reported throughout because M42 classified WAITED as MIXED/PROVISIONAL, not VALIDATED.

## Risk Unit

For every trade, `1R = entry_price - initial_stop_price`. Every threshold, stop level, and result in this study is expressed in R; no dollar/per-share metric was used to choose or evaluate anything.

## Frozen Exit Arms

Exactly four, predeclared before any P_MINUS_2..P3 result was read:

- **A. BASELINE** — existing simulator/`ExitEngine` behavior unchanged (move stop to breakeven at +1.00R, exit at +2.00R target, time exit 15:45 ET). No research modification: the `BASELINE` arm is the pinned, unmodified `simulate_shadow_execution` result reused outright.
- **B. BE_AFTER_050R** — once a COMPLETED bar reaches maximum favorable excursion ≥ +0.50R, the stop moves to entry price, effective beginning with the NEXT bar.
- **C. PROFIT_LADDER** — completed-bar MFE milestones ≥+0.50R → next-bar stop ≥ entry; ≥+0.75R → next-bar stop ≥ entry+0.25R; ≥+1.00R → next-bar stop ≥ entry+0.50R. Stop only moves up.
- **D. STRUCTURE_TRAIL_AFTER_1R** — baseline stop behavior before +1.00R; after a completed bar reaches ≥+1.00R, next-bar stop = `max(existing_stop, entry_price, prior_completed_bar_low)`, continuing on completed-bar lows only. Stop only rises, never falls.

### Why a research-only replay, not a production change

`src/exit_engine.py`'s `ExitEngine.evaluate` raises if a position's current stop is above its entry price — production's pinned V0 exit engine cannot represent the state that `PROFIT_LADDER`/`STRUCTURE_TRAIL_AFTER_1R` deliberately create. Rather than weaken that invariant (forbidden), `src/orb_profit_protection_exit_research.py` never constructs a `PositionState`/calls `ExitEngine` for the three protection arms. Instead it calls the unmodified, pinned `simulate_shadow_execution` once per candidate as a policy-independent REFERENCE (entry-fill mechanics, market-data validation, and the profit target are all functions of the *initial* stop only, never the evolving one, so the reference is authoritative for every arm), then re-walks only the bars strictly after the reference entry bar for the three protection arms, replicating the identical conservative same-bar priority order (ambiguous-if-both-touched > stop > target > time-exit > else advance the stop for the next bar only). No pinned file changed; the trusted digest above is identical before and after this milestone.

### Critical same-bar rule

If a bar both reaches a protection threshold and touches the stop that was valid *entering* that bar, the OLD stop remains authoritative for that bar — protection changes are only effective starting the next bar. Verified directly by `test_same_bar_rule_old_stop_authoritative_when_threshold_and_stop_touch_together`.

## Periods

All six existing historical periods, reused unchanged: P_MINUS_2 (2021-03-31..2022-03-29), P_MINUS_1 (2022-03-30..2023-03-30), P0 (2023-03-31..2024-04-02), P1 (2024-04-03..2025-04-02), P2 (2025-04-03..2026-04-06), P3 (2026-04-07..2026-08-13). No new period. Cache-first — `cache integrity: 3053 usable, 0 removed` before the run; zero new fetches (`--cache-only`, the default).

## Tests

`tests/test_orb_profit_protection_exit_research.py` — 19 tests covering: no protection before threshold (byte-identical to BASELINE), the exact +0.50R boundary, the +0.75R/+1.00R ladder tiers and that the stop never loosens on a pullback, structure-trail's baseline-before-1R / completed-bar-low ratcheting after 1R, the same-bar rule, target/stop conservative ambiguity ordering preserved for protection arms, time exit unchanged, DATA_INVALID/no-entry paths identical across every arm, deterministic output, no-lookahead (a stop touch ends the walk before a later bar with an artificially huge range is ever read), R-multiple math, the reporting helpers (`max_drawdown_r_and_streak_from_rows`, `full_stop_out_rate`, `protection_diagnostics`), and two full-pipeline integration tests proving entry-policy candidate identity and BASELINE-arm outcomes are byte-identical to M41's own `build_all_arms_rows` through the real SignalEngine/simulator pipeline. All 19 pass, plus the full M41/M42/simulator/`ExitEngine`/architecture-audit regression suites (209 tests) and the complete local suite (see Safety section).

## Empirical Results

**Empirical runtime:** 16,081.6s (≈4.47 hours), cache-only, zero network fetches.

### PRIMARY (WAITED_FIRST_VALID_0950) — pooled across all six periods

| Arm | Closed | Expectancy_r | PF_R | Cumulative_r | Win rate | Full -1R stop rate | Max DD_r | Longest losing streak | R/month | Trades/month |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 122 | 0.1603 | 1.468 | 19.562 | 52.5% | 27.05% | 5.412 | 5 | 0.304 | 1.898 |
| **BE_AFTER_050R** | 122 | **0.1911** | **1.727** | **23.318** | 44.3% | **21.31%** | **4.991** | 6 | **0.363** | 1.898 |
| PROFIT_LADDER | 122 | 0.1819 | 1.692 | 22.191 | 52.5% | 21.31% | 4.991 | 4 | 0.345 | 1.898 |
| STRUCTURE_TRAIL_AFTER_1R | 122 | 0.0851 | 1.248 | 10.377 | 55.7% | 27.05% | 5.778 | 4 | 0.161 | 1.898 |

Gross profit_r / gross loss_r: BASELINE 61.384 / 41.821; BE_AFTER_050R 55.371 / 32.053; PROFIT_LADDER 54.244 / 32.053; STRUCTURE_TRAIL_AFTER_1R 52.199 / 41.821. Average winner_r / average loser_r: BASELINE +0.959 / −0.721; BE_AFTER_050R +1.025 / −0.471; PROFIT_LADDER +0.848 / −0.553; STRUCTURE_TRAIL_AFTER_1R +0.768 / −0.774.

`BE_AFTER_050R` is the leading protection arm (highest pooled expectancy_r among the three).

### BE_AFTER_050R vs BASELINE (deltas)

delta expectancy_r **+0.0308**, delta PF_R **+0.260**, delta cumulative_r **+3.756**, delta R/month **+0.058**, delta max_drawdown_r **−0.421** (reduced), delta full-stop-out rate **−5.74 points** (0.2705→0.2131, a **21.2% relative reduction**).

### Protection diagnostics — BE_AFTER_050R (paired against BASELINE, same 122 candidate occurrences)

- Activated on **72/122** trades (**59.0%** activation rate).
- Of BASELINE's **33** full -1R stop-outs: **7** converted to breakeven, 0 to a small win, 0 to a reduced loss (**7 total, 21.2%** of baseline full losses).
- **10** baseline winners clipped by the earlier protective stop, for **6.013R** given up in total.
- **15** trades improved, **10** worsened, **97** unchanged (paired mean delta **+0.0308R/trade**, matching the pooled expectancy delta exactly, as expected for a paired comparison over the same trade set).

### Symbol robustness — BE_AFTER_050R (primary/WAITED)

Best R contributor: **AAPL**, **27.96%** of cumulative R. Expectancy_r excluding AAPL: **+0.1615** (still solidly positive). Expectancy_r excluding TSLA: **+0.1698**. Top-3 contributors (AAPL, TSLA, MSFT) account for **71.2%** of cumulative R — concentrated, as expected from a 9-symbol P_MINUS_1/P_MINUS_2 cohort and a 122-trade pooled sample, but the edge is not solely dependent on any one symbol.

### Robustness — period-by-period (WAITED, BASELINE → BE_AFTER_050R)

| Period | BASELINE closed / exp_r / dd_r | BE_AFTER_050R closed / exp_r / dd_r | Paired mean delta_r |
|---|---|---|---:|
| P_MINUS_2 | 22 / 0.193 / 2.039 | 22 / 0.286 / 2.039 | +0.0935 |
| P_MINUS_1 | 23 / 0.447 / 3.892 | 23 / 0.355 / 3.000 | −0.0923 |
| P0 | 21 / 0.041 / 2.633 | 21 / 0.145 / 2.438 | +0.1046 |
| P1 | 16 / 0.142 / 3.445 | 16 / 0.233 / 3.000 | +0.0903 |
| P2 | 30 / 0.036 / 5.022 | 30 / 0.014 / 4.991 | −0.0220 |
| P3 | 10 / 0.082 / 1.000 | 10 / 0.166 / 1.000 | +0.0840 |

Paired mean delta is non-negative in **4 of 6** periods (P_MINUS_2, P0, P1, P3); negative on P_MINUS_1 (the one period M42 already flagged as WAITED's weakest independent showing) and P2 (near-flat, −0.022R). Pooled unweighted result above combines all six equally.

### Deployment-relevance (recency-weighted) view

Predeclared exponential weighting (half-life 504 trading sessions, anchor 2026-08-13), reused unmodified from M41 — weighted `expectancy_r`/`win_rate`/`PF_R` only, never a weighted cumulative-R:

| Arm | Weighted expectancy_r | Weighted win rate | Weighted PF_R |
|---|---:|---:|---:|
| BASELINE | 0.1132 | 53.8% | 1.334 |
| BE_AFTER_050R | 0.1457 | 44.2% | 1.556 |
| PROFIT_LADDER | 0.1416 | 51.2% | 1.541 |
| STRUCTURE_TRAIL_AFTER_1R | 0.0569 | 55.4% | 1.168 |

Directionally identical to the pooled unweighted view: `BE_AFTER_050R` remains the leading protection arm under recency weighting too.

## Predeclared Classification

Criteria frozen before any P_MINUS_2..P3 result was read (see `scripts/research_orb_profit_protection_exit.py:classify_leading_arm` / `DEFAULT_SUPPORT_CRITERIA`). `SUPPORTED` requires all eight:

| # | Check | BE_AFTER_050R (WAITED, PRIMARY) |
|---|---|---|
| 1 | Pooled expectancy_r > 0 | ✅ 0.1911 |
| 2 | Pooled PF_R > 1 | ✅ 1.727 |
| 3 | Expectancy_r not more than 0.01R below BASELINE | ✅ +0.0308 (beats BASELINE) |
| 4 | R/month ≥ 90% of BASELINE | ✅ 0.363 vs 0.304 (119%) |
| 5 | Meaningful risk improvement (dd_r −15% OR full-stop-rate −20%) | ✅ full-stop rate −21.2% (dd_r only −7.8%, but either qualifies) |
| 6 | Does not increase max_drawdown_r | ✅ 4.991 ≤ 5.412 |
| 7 | Expectancy_r excluding best symbol (AAPL) > 0 | ✅ +0.1615 |
| 8 | Paired protection delta ≥ 0 in at least 4 periods | ✅ 4/6 (P_MINUS_2, P0, P1, P3) |

**All eight conditions hold. `BE_AFTER_050R` on `WAITED_FIRST_VALID_0950` classifies as SUPPORTED.**

## CURRENT_FIRST_VALID Sensitivity

Same four exit arms replayed against `CURRENT_FIRST_VALID`, same frozen thresholds, no reselection:

| Arm | Closed | Expectancy_r | PF_R | Cumulative_r | Full -1R stop rate | Max DD_r |
|---|---:|---:|---:|---:|---:|---:|
| BASELINE | 304 | 0.0776 | 1.189 | 23.605 | 35.53% | 13.307 |
| **BE_AFTER_050R** | 304 | **0.0869** | **1.287** | **26.428** | **25.99%** | **12.818** |
| PROFIT_LADDER | 304 | 0.0767 | 1.253 | 23.318 | 25.99% | 13.100 |
| STRUCTURE_TRAIL_AFTER_1R | 303 | 0.0000255 | 1.000 | 0.008 | 35.64% | 23.430 |

`BE_AFTER_050R` classifies **MIXED** on CURRENT_FIRST_VALID: seven of eight checks pass (expectancy positive, PF_R>1, within 0.01R of baseline, R/month≥90%, meaningful risk improvement — full-stop rate falls 26.8% relatively, drawdown not increased, excluding-best-symbol expectancy positive), but the paired-delta-in-≥4-periods check fails (**3 of 6** periods non-negative: P0, P2, P_MINUS_2).

**Directionally consistent with the primary result:** `BE_AFTER_050R` is the leading protection arm on both entry policies, improves expectancy/PF_R/full-stop-rate/drawdown on both, and is the only protection arm that is R-positive-and-improving on both. The one axis where CURRENT_FIRST_VALID falls short of PRIMARY's own bar is period-count consistency (3/6 vs 4/6) — expected, since `CURRENT_FIRST_VALID`'s much larger, more frequent trade set is more exposed to P1's and P3's specific drawdowns that the WAITED-filtered set avoids by construction.

## No Combination Mining

No hybrid of `BE_AFTER_050R` and any other arm was constructed or considered. The selected policy is exactly one of the four frozen arms tested here (`BE_AFTER_050R`), unmodified from its predeclared definition.

## Selected / Not-Selected Exit Policy

**`BE_AFTER_050R` is SUPPORTED on the PRIMARY entry policy (`WAITED_FIRST_VALID_0950`)** and directionally consistent (MIXED, same leading arm) on the `CURRENT_FIRST_VALID` sensitivity benchmark. `PROFIT_LADDER` is a close second (SUPPORTED-adjacent numbers, slightly lower expectancy/PF_R than `BE_AFTER_050R` on both entry policies) but was not selected as leading since `BE_AFTER_050R` has the higher pooled expectancy_r on the PRIMARY entry policy, per this study's own predeclared selection rule. `STRUCTURE_TRAIL_AFTER_1R` underperforms BASELINE on both entry policies (lower expectancy_r, lower PF_R, similar-or-worse full-stop rate) and is not a candidate for adoption from this evidence.

**This is a research finding, not an activation.** `strategy.enabled` remains `false`. No production exit/simulator/`ExitEngine` behavior changed. Any future production adoption of `BE_AFTER_050R` is a separate, explicitly-authorized milestone.

## Next Milestone

Per the frozen decision structure: SUPPORTED on PRIMARY, directionally consistent on the sensitivity benchmark → the natural next step is a dedicated **production-adoption proposal milestone** for `BE_AFTER_050R` (a controlled, explicitly-authorized change to move the live stop-management rule from "breakeven at +1.00R only" to "breakeven at +0.50R"), separate from this research milestone and requiring its own operator authorization before touching any pinned execution file. No further exit-threshold sweep is authorized by this result (0.40/0.60/0.65/0.80 etc. were never tried and remain out of scope).

## Safety

`strategy.enabled` remains `false` throughout. No broker-write code was added. No unattended/live authority change. No production exit/simulator/threshold behavior changed — `src/orb_profit_protection_exit_research.py` is a research-only replay/wrapper that calls the pinned `simulate_shadow_execution` unmodified for BASELINE and every non-entering trade, and only re-walks post-entry bars for the three protection arms (never constructing a `PositionState`/calling `ExitEngine`, since two of the three arms move the stop above entry, which `ExitEngine` correctly forbids for the pinned V0 engine). Trusted implementation digest confirmed unchanged before and after this milestone: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`.

## Structured Artifacts

Generated under `artifacts/research-profit-protection-exit/results/` (gitignored, regenerable from cache via `python scripts/research_orb_profit_protection_exit.py`): `orb_profit_protection_exit_report.json` (full structured report), `orb_profit_protection_exit_summary.csv`. A per-period simulation checkpoint cache (`artifacts/research-profit-protection-exit/period-checkpoint/`, also gitignored) lets the ~4.5-hour empirical stage be skipped on a rerun if only the cheap reporting/classification code changes; delete it (or pass `--no-checkpoint`) to force a clean recompute.
