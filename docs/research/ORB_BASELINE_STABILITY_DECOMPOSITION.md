# ORB C_BASE Baseline Stability Decomposition (Issue #319)

Research-only. Issue #317/PR #318 found the proposed RVOL 1.50x-1.99x band
did not generalize to a third, independent period. Before considering any
further strategy-rule change, this study asks a prior question: does the
underlying **C_BASE** configuration (gap>=0.50%/RVOL>=1.50x, otherwise
canonical `orb_long_v0`) have a reasonably persistent edge across the three
already-established periods, and is period-to-period deterioration
primarily a **time/market-regime effect** or is it materially explained by
**different liquidity-qualified universes** across periods? No threshold
search, no production change, no capital-path or trailing-stop experiment
performed here. `strategy.enabled` stays `false`.

Cross-validation: this study's own independent computation for C_BASE on
P2/P3 (closed trades, win rate, expectancy, profit factor) reproduces the
merged Issue #310/#315 (PR #316) figures for the equivalent config
(their "B", 0.50%/1.50x) exactly -- confirming the pipeline is correctly
wired before interpreting the new P1/decomposition results.

> **Metric-unit note (Issue #321/PR #322).** Legacy "expectancy"/"profit
> factor" throughout this document (including the `REGIME_DEPENDENT_EDGE`
> classification below, e.g. P1 "expectancy 0.023", P2 "expectancy
> 0.258", P3 "expectancy 1.042") are computed from raw price-per-share
> realized P&L -- they are **NOT** R-normalized. Risk-normalized
> profitability must be evaluated using R-based metrics
> (`expectancy_r_per_trade`/`profit_factor_r`, introduced by PR #322).
> **Risk-normalized interpretation of the `REGIME_DEPENDENT_EDGE`
> classification below is provisional pending a dedicated R-normalized
> re-evaluation** -- Issue #321's own follow-up study (the fourth
> independent period, P0) found that P1's price-based expectancy (+0.023,
> read as marginally positive here) is actually **negative** in R terms
> (-0.022), and a materially different four-period R-normalized picture
> overall; the same reinterpretation risk applies to the P1/P2/P3
> comparison in this document, which has not itself been rerun in R
> terms. No trade counts, win rates, or dollar-based figures in this
> document have changed -- this is a labeling clarification only, not a
> rerun. See `docs/research/ORB_BASELINE_FOURTH_PERIOD_VALIDATION.md`
> for the corrected R-based methodology and figures.

## Periods (fixed, no boundary changes)

| | First | Last | Sessions | Calendar |
|---|---|---|---|---|
| P1_THIRD | 2024-04-03 | 2025-04-02 | 252 | RESEARCH-ONLY (Issue #317's 2024 extension) |
| P2_OOS | 2025-04-03 | 2026-04-06 | 252 | RESEARCH-ONLY (2025 portion) |
| P3_DISCOVERY | 2026-04-07 | 2026-08-13 | 90 | production (`src.exchange_calendar`) |

Zero overlap confirmed programmatically before any evaluation.

## Configurations

**C_BASE** (primary): gap>=0.50%, RVOL>=1.50x -- the identical
`ParameterCombo` Issue #317's own module already defines as `CONFIG_C`.
**B_BASE** (optional comparator): gap>=0.75%, RVOL>=1.50x (`CONFIG_B`).
Everything else canonical: 5-minute opening range, SPY>VWAP, completed-bar
breakout, long-only, 09:40-11:00 entry window, unmodified simulator/exit
rules. The failed RVOL 1.50x-1.99x band is not re-tested as a candidate
improvement here -- shown only as prior context above.

## VIEW A: period-native universe

Each period's own liquidity-qualified universe, computed from in-period
bars only (unchanged methodology from #304/#310/#317):

| Period | Universe size | Symbols |
|---|---|---|
| P1_THIRD | 9 | NVDA, AAPL, TSLA, AMZN, MSFT, GOOGL, AMD, BAC, INTC |
| P2_OOS | 17 | NVDA, TSLA, AAPL, AMZN, MSFT, GOOGL, META, AVGO, AMD, INTC, BAC, WMT, CSCO, KO, NKE, VZ, T |
| P3_DISCOVERY | 23 | NVDA, AMZN, AAPL, MSFT, GOOGL, META, TSLA, INTC, AMD, AVGO, NFLX, BAC, V, CSCO, NOW, WMT, CRM, KO, JPM, VZ, BSX, NKE, SLB |

P1's 9-symbol universe is a strict subset of P2's 17, which is itself a
strict subset of P3's 23 -- universe size grows monotonically with period
recency in this data, not incidentally.

## VIEW B: common-cohort diagnostic universe

Fixed intersection of `SymbolLiquidityProfile.eligible` across all three
periods -- eligibility/data-quality only, never P&L:

```
AAPL, AMD, AMZN, BAC, GOOGL, INTC, MSFT, NVDA, TSLA  (9 symbols)
```

Because P1's native universe happens to be a subset of both P2's and
P3's, the cohort equals P1's own native universe exactly. This is a
genuine, unforced result of the eligibility computation, not a design
choice -- diagnostic only, never a proposed deployable universe.

## Core metrics: C_BASE by period and view

| Period | View | Universe | Cand. Days | Rate | Closed | Win Rate | Expectancy | PF | Cum R | Stop-Out |
|---|---|---|---|---|---|---|---|---|---|---|
| P1_THIRD | NATIVE | 9 | 67/252 | 26.59% | 84 | 39.3% | 0.023 | 1.016 | -1.850 | 47.62% |
| P1_THIRD | COHORT | 9 | 67/252 | 26.59% | 84 | 39.3% | 0.023 | 1.016 | -1.850 | 47.62% |
| P2_OOS | NATIVE | 17 | 94/252 | 37.30% | 150 | 40.7% | 0.258 | 1.156 | 0.822 | 41.33% |
| P2_OOS | COHORT | 9 | 83/252 | 32.94% | 112 | 37.5% | 0.029 | 1.020 | -6.163 | 42.86% |
| P3_DISCOVERY | NATIVE | 23 | 40/90 | 44.44% | 57 | 57.9% | 1.042 | 1.789 | 12.932 | 22.81% |
| P3_DISCOVERY | COHORT | 9 | 31/90 | 34.44% | 40 | 62.5% | 1.358 | 2.042 | 15.360 | 15.00% |

P1's COHORT view equals its NATIVE view exactly (cohort == P1's own
universe, see above). P2's cohort-restricted result drops sharply
(expectancy 0.258 -> 0.029, PF 1.156 -> 1.020) -- close to P1's level.
P3's cohort-restricted result stays strong, in fact strengthens slightly
(expectancy 1.042 -> 1.358, PF 1.789 -> 2.042).

### One-trade-per-day (C_BASE, NATIVE)

| Period | Closed | Win Rate | Expectancy | PF |
|---|---|---|---|---|
| P1_THIRD | 64 | 40.6% | 0.033 | 1.022 |
| P2_OOS | 93 | 47.3% | 0.816 | 1.544 |
| P3_DISCOVERY | 39 | 56.4% | 0.712 | 1.506 |

Restricting to the first candidate of the day improves every period,
consistent with #304/#307/#316's own repeated finding.

### Monthly distribution and symbol contribution

Structured outputs: `orb_baseline_stability_symbol_contribution.csv` (29
rows). Top-3 P&L contributors and concentration (C_BASE, NATIVE):

| Period | Top-3 share | Best contributor | 2nd | 3rd |
|---|---|---|---|---|
| P1_THIRD | 19.5x total\* | MSFT (+16.23) | TSLA (+13.30) | NVDA (+7.91) |
| P2_OOS | 1.82x total | TSLA (+36.88) | META (+21.33) | AAPL (+12.13) |
| P3_DISCOVERY | 0.86x total | MSFT (+20.33) | TSLA (+19.48) | AMZN (+11.30) |

\* P1's total realized P&L is only +1.92 (near breakeven), so the top-3
share ratio is not a meaningful percentage there -- reported honestly as
computed rather than clamped, same caveat as Issue #317's own report.

## B_BASE comparator (gap>=0.75%/RVOL>=1.50x)

Optional comparator only -- not the primary config, included for context:

| Period | View | Closed | Win Rate | Expectancy | PF | Cum R |
|---|---|---|---|---|---|---|
| P1_THIRD | NATIVE | 73 | 38.4% | -0.099 | 0.932 | -1.936 |
| P2_OOS | NATIVE | 126 | 40.5% | 0.178 | 1.104 | -1.787 |
| P2_OOS | COHORT | 96 | 35.4% | -0.273 | 0.831 | -12.641 |
| P3_DISCOVERY | NATIVE | 50 | 56.0% | 0.685 | 1.472 | 8.418 |
| P3_DISCOVERY | COHORT | 36 | 61.1% | 0.918 | 1.660 | 10.360 |

Same qualitative pattern as C_BASE: P1 weak-to-negative, P2's edge is
fragile and turns negative under the cohort restriction, P3 stays strong
under both views. This comparator is not used in the stability
classification or next-experiment decision (both are C_BASE-only per the
study's own scope) -- shown only as corroborating context.

## Rolling stability (C_BASE, NATIVE, combined chronology, fixed calendar quarters)

10 of 10 quarters cleared the predeclared 8-closed-trade adequacy floor
(no quarter marked inadequate):

| Quarter | Closed | Win Rate | Expectancy | PF | Cum R |
|---|---|---|---|---|---|
| 2024-Q2 | 18 | 44.4% | -0.022 | 0.983 | 0.647 |
| 2024-Q3 | 28 | 28.6% | -0.347 | 0.729 | -6.988 |
| 2024-Q4 | 20 | 40.0% | -0.741 | 0.712 | -2.788 |
| 2025-Q1 | 18 | 50.0% | **1.491** | **4.768** | 7.279 |
| 2025-Q2 | 36 | 27.8% | 0.428 | 1.340 | -1.144 |
| 2025-Q3 | 40 | 40.0% | -0.100 | 0.945 | 0.310 |
| 2025-Q4 | 36 | 50.0% | 0.770 | 1.481 | 3.383 |
| 2026-Q1 | 35 | 45.7% | 0.120 | 1.064 | -0.841 |
| 2026-Q2 | 41 | 58.5% | 0.679 | 1.425 | 9.708 |
| 2026-Q3 | 19 | 52.6% | 1.414 | 2.633 | 2.338 |

- Fraction of adequately-sampled quarters with positive expectancy: **60%** (6/10)
- Fraction with PF>1: **60%** (6/10)
- Best quarter: **2025-Q1** (expectancy 1.491, PF 4.768) -- notably the LAST quarter of P1_THIRD, not part of P2 or P3
- Worst quarter: **2024-Q4** (expectancy -0.741, PF 0.712) -- the second quarter of P1_THIRD
- Longest sequence of negative-expectancy quarters: **3** (2024-Q2, Q3, Q4 -- the first three quarters of P1_THIRD, consecutively)

No quarter/month filter is built from this -- descriptive only. The
pattern is genuinely noisy quarter-to-quarter, not a clean two-regime
split: P1_THIRD itself contains both its own worst 3-quarter stretch AND
its best single quarter (2025-Q1, right before the P1/P2 boundary).

## Universe effect decomposition

Direct native-vs-cohort comparison, C_BASE:

| Period | Native Expectancy | Cohort Expectancy | Delta (native - cohort) |
|---|---|---|---|
| P1_THIRD | 0.023 | 0.023 | 0.000 |
| P2_OOS | 0.258 | 0.029 | **+0.228** |
| P3_DISCOVERY | 1.042 | 1.358 | **-0.316** |

**Answers to the specific questions:**

1. **Does P1 remain weak under the common cohort?** Yes -- P1's cohort
   result is identical to its native result (cohort == P1's own
   universe); it was never a wider-universe P1 to begin with.
2. **Do P2/P3 remain stronger when restricted to the same cohort?** Mixed.
   P2 does **not** remain stronger -- its edge collapses from 0.258 to
   0.029 (96% of the P1-vs-P2 native gap and 89% of the P2 native
   expectancy itself disappears under cohort restriction). P3 **does**
   remain (and slightly strengthens) -- 1.042 native vs 1.358 cohort.
3. **How much of the expectancy difference survives universe
   normalization?** For P1-vs-P2: almost none (0.235 native gap shrinks to
   0.006 under cohort). For P1-vs-P3: nearly all of it (1.019 native gap
   vs 1.335 cohort gap -- P3's edge over P1 is not explained by universe
   composition at all).
4. **Is the edge driven by symbols that did not exist/qualify in P1?** For
   P2, largely yes -- META, AVGO, WMT, CSCO, KO, NKE, VZ, T (P2's
   non-cohort symbols) contributed enough net edge that removing them
   nearly erases P2's advantage over P1. For P3, no -- P3's edge holds on
   the identical 9-symbol cohort P1 itself uses.
5. **Does removing TSLA materially alter the conclusion?** P1: yes --
   excluding TSLA flips native expectancy from +0.023 to -0.184 (negative).
   P2: expectancy drops from 0.258 to 0.014 but stays barely positive
   (does not flip). P3: expectancy drops from 1.042 to 0.768, stays
   strongly positive.
6. **Does removing the single best contributor materially alter the
   conclusion?** P1: yes, flips negative (best contributor is MSFT here,
   not TSLA; excluding it: expectancy -0.191). P2: best contributor is
   TSLA (same as above). P3: best contributor is MSFT; excluding it,
   expectancy drops to 0.766 but stays strongly positive.

**Single-symbol dependence (native view):** P1_THIRD = **True** (flips
negative excluding either TSLA or its single best contributor). P2_OOS =
**False** by the strict flip-to-negative test, though the drop is large
(0.258 -> 0.014, a ~95% reduction) -- reported as a real near-dependence
nuance, not smoothed into a clean "independent" reading. P3_DISCOVERY =
**False**, and comfortably so (1.042 -> 0.766, stays well above breakeven).

## Entry edge vs exit edge (MFE diagnostic, descriptive only)

C_BASE, NATIVE view, all three periods combined chronologically (291
closed trades, no simulator change, no trailing stop implemented):

| | Winners (127) | Losers (164) |
|---|---|---|
| MFE/R mean | 1.244 | 0.490 |
| MFE/R median | 1.208 | 0.378 |
| MFE/R min-max | 0.239 - 2.214 | 0.000 - 1.708 |
| Realized R mean (winners only) | 0.976 | -- |
| Realized R median (winners only) | 0.826 | -- |

**Fraction of eventual losing trades that first reached a given
favorable-excursion level before later becoming losses:**

| Threshold | Losers reaching it | Fraction |
|---|---|---|
| +0.25R | 105 / 164 | **64.0%** |
| +0.50R | 68 / 164 | **41.5%** |
| +0.75R | 37 / 164 | 22.6% |
| +1.00R | 22 / 164 | 13.4% |

Against the predeclared meaningful-MFE bar (>=30% of losers reaching
+0.50R before reversing): **41.5% clears it** --
`mfe_meaningful_fraction = True`. A substantial share of eventual losses
were, at some point, meaningfully in favor before reversing -- this is
the kind of evidence a profit-protection/trailing-stop experiment would
need to be justified, though (see Stability Classification below)
stability itself is not yet resolved enough to prioritize that experiment
next.

## Combined full-history view (descriptive context only, not a new backtest sample)

C_BASE, NATIVE, all three periods concatenated chronologically:

| | Closed | Win Rate | Expectancy | PF | Cum R | Max context |
|---|---|---|---|---|---|---|
| ALL_CANDIDATES | 291 | 43.6% | 0.344 | 1.227 | 11.904 | -- |
| ONE_TRADE_PER_DAY | 196 | 46.9% | 0.540 | 1.366 | 17.578 | -- |
| Excluding TSLA | 244 | 42.2% | 0.124 | 1.090 | 0.648 | drops sharply, stays barely positive |
| Excluding best symbol (TSLA) | 244 | 42.2% | 0.124 | 1.090 | 0.648 | identical to excluding TSLA -- TSLA is the single best contributor across the full combined history |

TSLA's combined-history contribution is large but not the entire
combined-history edge -- expectancy stays positive (0.124, PF 1.090)
without it, unlike P1_THIRD alone where removing it flips negative.

## Predeclared stability classification

Criteria fixed in `src/orb_baseline_stability_research.py::classify_stability`
before interpreting any period's result: INSUFFICIENT_EVIDENCE if any
period has fewer than 15 closed trades (none did here -- 84/150/57);
STABLE_EDGE requires positive expectancy and PF>1 in every period with no
single-symbol dependence; REGIME_DEPENDENT_EDGE requires at least one
period materially positive (expectancy > 0.1) and at least one weak/
negative, with that spread surviving common-cohort normalization;
otherwise WEAK_OR_NO_EDGE.

- Native expectancy: P1=0.023, P2=0.258, P3=1.042 -- material-positive
  periods (>0.1): **P2, P3**. Weak/negative: **P1**.
- Under the common cohort: P2's expectancy collapses to 0.029 (no longer
  material), P3's stays at 1.358 (still material), P1 stays at 0.023
  (still weak). The material-vs-weak split (P3 material, P1 weak) **survives
  cohort normalization** even though P2 itself does not remain material.

### **Verdict: REGIME_DEPENDENT_EDGE**

C_BASE shows a genuine, cohort-normalization-surviving split between a
strongly positive regime (P3_DISCOVERY, robust to both universe
restriction and symbol removal) and a weak-to-flat regime (P1_THIRD,
single-symbol dependent). P2_OOS sits in between and is itself
substantially explained by universe composition (its native edge nearly
vanishes under the fixed cohort) -- P2 is not cleanly "strong" once
universe effects are controlled for. This is not STABLE_EDGE (P1 and,
under the cohort, P2 do not clear the bar) and not WEAK_OR_NO_EDGE (P3's
edge is real and survives every sensitivity check run here).

## Next-experiment decision

Per the fixed rules: PROFIT_PROTECTION_EXIT_EXPERIMENT requires
STABLE_EDGE (not met -- verdict is REGIME_DEPENDENT_EDGE) even though the
MFE evidence itself would have cleared its own bar. CAPITAL_PATH_EXPERIMENT
requires sufficient overall stability (not met). MORE_BASELINE_VALIDATION
applies when evidence is promising in some periods but stability is
unresolved -- exactly this case.

### **Recommendation: MORE_BASELINE_VALIDATION**

The underlying C_BASE edge is not uniformly weak (P3 is genuinely strong
and cohort-robust) but is not yet demonstrated stable across time/universe
either (P1 is single-symbol dependent and near-breakeven; P2's apparent
strength is largely a universe-composition artifact). Neither the
profit-protection exit experiment nor the $30K capital-path experiment is
recommended yet. **Neither is performed here.**

## Reproducing

```
python scripts/research_orb_baseline_stability_decomposition.py
```

(Cache-only by default; all three periods' bars were already fetched by
prior merged studies, so this normally requires no network access. Pass
`--fetch-missing` for any cache gap.)

Structured outputs (`artifacts/research-baseline-stability/results/`):
`orb_baseline_stability_decomposition_report.json` (full detail),
`orb_baseline_stability_period_comparison.csv` (12 rows),
`orb_baseline_stability_common_cohort_comparison.csv` (3 rows),
`orb_baseline_stability_quarterly.csv` (10 rows),
`orb_baseline_stability_symbol_contribution.csv` (29 rows),
`orb_baseline_stability_mfe_summary.csv` (7 rows).
