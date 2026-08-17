# ORB C_BASE Fourth-Period Baseline Validation (Issue #321)

Research-only. Final planned baseline-extension study before deciding
whether C_BASE deserves exit-management research. Adds one further
independent period, **P0** (2023-03-31..2024-04-02, immediately preceding
P1), to Issue #319/PR #320's three-period C_BASE analysis. No threshold
search, no production change, no trailing-stop implementation, no $30K
capital-path experiment performed here.

**This document was corrected in place after a metric-unit error was
found in the initial draft** (same PR, same branch, before merge -- see
below). The original draft classified `BASELINE_SUPPORTED_FOR_EXIT_RESEARCH`
using dollars-per-share expectancy/profit-factor as if they were R
multiples. The corrected, risk-normalized (R-based) analysis below
reverses that conclusion to `BASELINE_NOT_SUPPORTED`.

## Metric-unit correction (read this first)

`src/orb_regime_diagnostic_research.py::trade_quality_from_rows` -- the
shared aggregation function every study in this research line uses --
computed `expectancy_per_trade`, `average_winner`, `average_loser`, and
`profit_factor` from each trade's `realized_pnl` (raw dollars per share).
`cumulative_r` was separately computed from `r_multiple` (`realized_pnl`
divided by that trade's own initial risk, `entry_price - initial_stop_price`).
**These are two different units.** `realized_pnl`-based "expectancy" is
not comparable across trades with different stop widths -- a $10 winner on
a $0.20-wide stop and a $10 winner on a $10-wide stop have wildly
different risk, but identical price-per-share P&L. For any conclusion
about **fixed-risk position sizing** (which this study's own next-step
question, a profit-protection *exit* experiment sized in R, requires),
the risk-normalized figure is the one that matters, and the two can
disagree in sign.

**Root cause**: a naming/labeling gap, not a simulator defect. The
underlying `realized_pnl`, `r_multiple`, and `maximum_favorable_excursion`
values computed by the unmodified `simulate_shadow_execution` were always
correct; `trade_quality_from_rows` simply never exposed an R-normalized
expectancy/profit-factor pair, and this research line's own prose
(including this document's own first draft) treated the dollar-based
figures as if they served that role.

**Fix**: `trade_quality_from_rows` now returns BOTH units explicitly,
never conflated:

| Price/per-share (legacy names kept for backward compatibility) | R-normalized (PRIMARY for fixed-risk decisions) |
|---|---|
| `expectancy_per_trade` / `expectancy_price_per_share` | `expectancy_r_per_trade` |
| `profit_factor` / `profit_factor_price_per_share` | `profit_factor_r` |
| `average_winner` / `average_winner_price_per_share` | `average_winner_r` |
| `average_loser` / `average_loser_price_per_share` | `average_loser_r` |
| -- | `gross_profit_r`, `gross_loss_r` |
| `cumulative_r` (unaffected by the bug -- always R-based) | (unchanged) |

Every existing merged caller (#304/#310/#317/#319/#320) continues to read
identical values from the legacy field names -- this is a strictly
additive fix, not a breaking change, and none of those merged PRs'
underlying trade counts, win rates, or simulator outcomes were affected.
Only the *interpretation* of "expectancy"/"PF" as risk-normalized was
wrong, and only where that interpretation was implicitly assumed.

**Simulator outcomes were NOT affected.** `realized_pnl`, `r_multiple`,
`maximum_favorable_excursion`, entry/exit/stop prices, and every other
`ShadowExecutionResult` field are unchanged -- this correction only adds
a previously-missing risk-normalized aggregation on top of data that was
always correct.

## Efficiency (unaffected by the correction)

P1/P2/P3 were NOT recomputed from minute bars. P2/P3's R-normalized
figures were reconstructed from #316's own retained per-trade CSV
(`orb_regime_diagnostic_trades.csv`, config `B_0.50gap_1.50rvol` =
this study's C_BASE) -- exact, not estimated. P1's R-normalized figures
required one additional cache-only recomputation (no network fetch; 9
symbols, ~35 minutes) since no per-trade P1 artifact survived #320's own
run to reconstruct from otherwise. P0 was recomputed once network-fetched
initially, then re-run cache-only after the code fix landed to pick up
the new fields (~49 minutes, no network). The algebraic recombination
method (`combine_trade_quality_aggregates`) was verified exact -- in BOTH
units -- against #320's and #316's own real-row ground truth to full
floating-point precision before use (see this module's own test suite).

## P0 (fourth period)

| | |
|---|---|
| First session | 2023-03-31 |
| Last session | 2024-04-02 |
| Sessions | 252 (preferred target met) |
| Reference day (window ends strictly before) | 2024-04-03 (P1's own first session) |
| Overlap with P1/P2/P3 | none (confirmed programmatically before any fetch) |
| Calendar source | `src.orb_research_historical_calendar`, extended from a 2024-01-01 floor to 2023-01-01 (sourced 2023 NYSE holiday/early-close table; only 2 half-days that year -- Christmas Eve 2023 fell on a Sunday) |
| Coverage | 99.09% (VALID) |

Freshness policy derived from the actual requested window (not a
hardcoded magic number, per this study's own goal document): P0 lookback
start 2023-03-03, `max_age_seconds` = 1293 days (1263-day actual age +
30-day margin), printed and validated before the fetch step.

## P0 universe views

**VIEW A (native)**: 7 symbols -- `NVDA, AAPL, MSFT, TSLA, AMZN, GOOGL, AMD`.
**VIEW B (anchored cohort)**: #320's fixed 9-symbol cohort, reused
unchanged. Both views produced identical results (BAC/INTC, the cohort's
extra symbols, had zero P0 candidates -- a genuine data outcome).

## Core metrics -- BOTH units, per period (native view, all-candidates)

| Period | Closed | Win Rate | Exp (price/share) | PF (price/share) | **Exp (R)** | **PF (R)** |
|---|---|---|---|---|---|---|
| P0 | 97 | 35.1% | -0.229 | 0.771 | **-0.127** | **0.752** |
| P1_THIRD | 84 | 39.3% | +0.023 | 1.016 | **-0.022** | **0.949** |
| P2_OOS | 150 | 40.7% | +0.258 | 1.156 | **+0.005** | **1.014** |
| P3_DISCOVERY | 57 | 57.9% | +1.042 | 1.789 | **+0.227** | **1.838** |

**P1's own sign flips**: positive in price terms (+0.023), negative in R
terms (-0.022). This is the exact class of mistake the correction fixes
-- P1 was never actually a (barely) profitable period on a risk-normalized
basis. P2's price-based edge (+0.258, looked clearly positive) also
compresses to nearly breakeven in R terms (+0.005, PF_R 1.014) --
materially weaker than the price-based figure suggested. P0 and P3 keep
the same sign in both units, but P0's magnitude is proportionally worse
in R terms (-0.127R average loss per trade is a substantial, not
marginal, risk-normalized loss).

### One-trade-per-day (native view, both units)

| Period | Closed | Exp (price) | PF (price) | **Exp (R)** | **PF (R)** |
|---|---|---|---|---|---|
| P0 | 73 | -0.201 | 0.797 | **-0.101** | **0.805** |
| P1_THIRD | 64 | +0.033 | 1.022 | **-0.062** | **0.861** |
| P2_OOS | 93 | +0.816 | 1.544 | **+0.142** | **1.427** |
| P3_DISCOVERY | 39 | +0.712 | 1.506 | **+0.216** | **1.727** |

P1's one-trade-per-day view also flips sign (+0.033 price vs -0.062 R).

### Symbol contribution (P0, native) -- R-based ranking can differ from price-based

| Symbol | Closed | Exp (price) | **Exp (R)** | PF (R) |
|---|---|---|---|---|
| GOOGL | 18 | -0.114 | **+0.059** | 1.139 |
| AMD | 19 | +0.178 | **-0.050** | 0.892 |
| NVDA | 18 | +0.026 | **-0.037** | 0.927 |
| AMZN | 13 | -0.357 | -0.140 | 0.738 |
| TSLA | 14 | -0.574 | -0.305 | 0.519 |
| MSFT | 9 | -1.039 | -0.425 | 0.302 |
| AAPL | 6 | -0.336 | -0.313 | 0.375 |

**GOOGL and AMD/NVDA swap sign between units within P0 itself.** GOOGL is
the only symbol with a positive R-expectancy in P0 despite a negative
price-per-share figure; AMD looks like P0's best price-per-share
contributor (+0.178) but is R-negative (-0.050). This is a concrete,
same-period illustration of why the two units cannot be used
interchangeably.

## Profit-protection MFE diagnostic (unaffected by the correction)

MFE/R figures were always R-based (derived from `maximum_favorable_excursion`
and each trade's own risk, never from `realized_pnl` directly) and did
not need correction. P0's own 39.7%-of-losers-reach-+0.50R finding still
corroborates #320's combined 41.5% -- this evidence is unchanged.

| Threshold | P0 losers reaching it | Fraction |
|---|---|---|
| +0.25R | 36 / 63 | 57.1% |
| +0.50R | 25 / 63 | **39.7%** |
| +0.75R | 15 / 63 | 23.8% |
| +1.00R | 8 / 63 | 12.7% |

Clears the predeclared 30% meaningful-MFE bar
(`p0_meaningful_mfe_fraction = True`) -- this criterion (#7 below) still
passes; it is the other six that do not.

## Four-period table (both units; full 16-row detail in the CSV)

| Period | View | Selection | Closed | Exp (price) | PF (price) | **Exp (R)** | **PF (R)** |
|---|---|---|---|---|---|---|---|
| P0 | native | all-candidates | 97 | -0.229 | 0.771 | -0.127 | 0.752 |
| P0 | native | one-trade-per-day | 73 | -0.201 | 0.797 | -0.101 | 0.805 |
| P1_THIRD | native | all-candidates | 84 | +0.023 | 1.016 | -0.022 | 0.949 |
| P2_OOS | native | all-candidates | 150 | +0.258 | 1.156 | +0.005 | 1.014 |
| P3_DISCOVERY | native | all-candidates | 57 | +1.042 | 1.789 | +0.227 | 1.838 |

Maximum drawdown for P1-P3 remains unavailable from the reused #320
aggregates (no surviving per-trade ordering) -- P0's own is 30.08
(all-candidates) / 20.69 (one-trade-per-day), computed from real rows.

## Full-history view (P0+P1+P2+P3, both units, exact algebraic recombination)

| | Closed | Exp (price) | PF (price) | **Exp (R)** | **PF (R)** |
|---|---|---|---|---|---|
| ALL_CANDIDATES | 388 | +0.200 | 1.145 | **-0.0011** | **0.997** |
| ONE_TRADE_PER_DAY | 269 | +0.339 | 1.252 | **+0.038** | **1.094** |
| Excluding TSLA (overall best R-contributor) | 327 | +0.049 | 1.040 | **-0.023** | **0.946** |
| Excluding best symbol (same: TSLA) | 327 | +0.049 | 1.040 | **-0.023** | **0.946** |

**The combined four-period edge is, in risk-normalized terms, essentially
zero-to-slightly-negative** (expectancy -0.0011R/trade, PF_R 0.997) --
not the clearly-positive +0.200/1.145 the price-based figures showed.
Only the one-trade-per-day view clears both R-based bars (+0.038R,
PF_R 1.094), and even that is a thin edge. TSLA remains the largest
single R-contributor overall (as it was in price terms), but excluding it
pushes the combined R-expectancy further negative (-0.023R, PF_R 0.946)
rather than merely thinning a positive edge.

- Fraction of the four periods with positive R-expectancy: **50%** (2/4 -- only P2 and P3; P0 and P1 are both negative in R terms)
- Fraction with PF_R>1: **50%** (2/4)

## Quarterly stability (merged, R-based)

14 quarters, all adequately sampled. `expectancy_r_per_trade` is exact
for every quarter (derived from `cumulative_r`, which was never affected
by the bug); `profit_factor_r` is unavailable for the four quarters
wholly inside P1 (2024-Q2..2025-Q1 -- no surviving per-trade P1 data to
split by quarter) and is excluded from, not counted against, the PF_R
fraction below.

- Fraction of adequately-sampled quarters with positive R-expectancy: **50%** (7/14)
- Fraction with PF_R>1 (of the 10 quarters where PF_R is known): **50%** (5/10)
- Best quarter (R): **2025-Q1** (expectancy_r +0.404) -- same quarter as the price-based best
- Worst quarter (R): **2023-Q4** (expectancy_r -0.430) -- a *different* quarter than the price-based worst (2024-Q4); 2023-Q4 falls inside P0
- Longest negative-R-expectancy streak: **2 quarters** -- notably shorter than the 5-quarter losing streak the (uncorrected) price-based view showed; the R-based picture is more evenly split, not concentrated in one long bad stretch

## Predeclared baseline conclusion (R-based, corrected)

All seven criteria fixed in `classify_baseline_conclusion` before P0 was
computed; **R-normalized metrics are PRIMARY**, per this correction:

| # | Criterion | R-based result |
|---|---|---|
| 1 | Combined expectancy_r > 0 | -0.0011 -- **FAIL** |
| 2 | Combined PF_R > 1 | 0.997 -- **FAIL** |
| 3 | One-trade-per-day expectancy_r > 0 and PF_R > 1 | +0.038 / 1.094 -- **PASS** |
| 4 | Excluding best symbol still expectancy_r > 0 and PF_R > 1 | -0.023 / 0.946 -- **FAIL** |
| 5 | At least 3 of 4 periods R-non-negative | only 2 of 4 (P2, P3) -- **FAIL** |
| 6 | No catastrophic P0 instability | -0.127 > -1.0 and 0.752 > 0.5 -- **PASS** |
| 7 | MFE behavior meaningfully present | 39.7% >= 30% -- **PASS** |

Four of seven fail. Combined expectancy_r is not clearly positive either
(-0.0011, essentially breakeven-to-negative), so this does not qualify
for REGIME_DEPENDENCE_CONFIRMED (which requires a clearly positive
combined edge despite instability) -- it falls to:

### **Verdict: BASELINE_NOT_SUPPORTED**

*(Corrected from the original draft's `BASELINE_SUPPORTED_FOR_EXIT_RESEARCH`,
which was computed from price-per-share metrics.)* On a risk-normalized
basis, C_BASE's combined four-period edge is not defensibly positive.
P2's apparent price-based strength was mostly a units artifact (nearly
breakeven in R); P1 flips sign entirely; only P0 and P1 stay directionally
consistent between units, and both are negative. The MFE evidence (#7)
and the one-trade-per-day view (#3) are the only criteria that still
pass -- real signal, but not enough on their own.

## Next milestone

Per the fixed rule (BASELINE_NOT_SUPPORTED -> PAUSE_STRATEGY_REFINEMENT,
no further condition):

### **Recommendation: PAUSE_STRATEGY_REFINEMENT**

*(Corrected from the original draft's `PROFIT_PROTECTION_EXIT_EXPERIMENT`.)*
The underlying C_BASE edge, measured correctly in risk-normalized terms,
is not established combined across four independent periods. Refining
exits on a strategy whose entry edge is not itself demonstrated is not
justified by this evidence. The $30K capital-path experiment remains
queued and is **not** advanced by this result either.

## Documents affected by the same price/R labeling ambiguity (reported for a later correction sweep, not rewritten here)

None of the documents below contain a literal mislabeled figure (e.g.
"+0.258R" attached to a dollar-based number); the ambiguity is that every
one of them reports "expectancy"/"profit factor" without stating the
unit, alongside a separately-reported "cumulative R" that IS R-based --
a reader could reasonably (but incorrectly) assume the two are the same
unit, exactly as this document's own first draft did.

- `docs/research/ORB_GAP_RVOL_SENSITIVITY_STUDY.md` (Issue #300)
- `docs/research/ORB_FINE_RVOL_VALIDATION_STUDY.md` (Issue #302)
- `docs/research/ORB_OUT_OF_SAMPLE_VALIDATION.md` (Issue #304 -- already superseded by the next doc)
- `docs/research/ORB_OUT_OF_SAMPLE_VALIDATION_CORRECTION.md` (Issue #313)
- `docs/research/ORB_REGIME_DIAGNOSTIC.md` (Issue #310/#315)
- `docs/research/ORB_RVOL_BAND_THIRD_PERIOD_VALIDATION.md` (Issue #317)
- `docs/research/ORB_BASELINE_STABILITY_DECOMPOSITION.md` (Issue #319)

Not affected: `docs/research/ORB_UNIVERSE_EXPANSION_STUDY.md` (explicitly
computes no P&L/expectancy figures).

## Reproducing

```
python scripts/research_orb_baseline_fourth_period_validation.py --fetch-missing
```

(Or `--cache-only`, the default, once the on-disk cache is populated.)
Fetches/simulates P0 only; P1-P3 are reused from
`src/orb_baseline_fourth_period_research.py::PRIOR_PERIODS_REFERENCE`/
`PRIOR_QUARTERS_REFERENCE`.

Structured outputs (`artifacts/research-baseline-fourth-period/results/`):
`orb_baseline_fourth_period_report.json` (full detail, both units),
`orb_baseline_fourth_period_combined_summary.json`,
`orb_baseline_fourth_period_four_period_comparison.csv` (16 rows, both
units), `orb_baseline_fourth_period_p0_matrix.csv`,
`orb_baseline_fourth_period_symbols.csv` (7 rows, both units),
`orb_baseline_fourth_period_mfe_summary.csv`.
