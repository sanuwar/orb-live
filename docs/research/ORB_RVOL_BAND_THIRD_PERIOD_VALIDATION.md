# ORB RVOL 1.50x-1.99x Band: Third-Period Validation (Issue #317)

Research-only. Answers one question: does restricting RVOL to the
**1.50x-1.99x band** -- the one signal the merged regime diagnostic (Issue
#310/#315, PR #316) found improved expectancy for two independent
configurations in both the 90-session discovery period and the 252-session
out-of-sample period -- hold on a **third, fully independent** historical
period? This is validation only: exactly two configurations, each in
exactly two fixed variants (BASE, BAND), no threshold tuning, no new
filters, no neighboring-band search. `strategy.enabled` stays `false`; no
production threshold, universe, or config change is made here.

> **Metric-unit note (Issue #321/PR #322).** Legacy "expectancy"/"profit
> factor" throughout this document (including the predeclared
> VALIDATED/MIXED/FAILED criteria below) are computed from raw
> price-per-share realized P&L -- they are **NOT** R-normalized.
> Risk-normalized profitability must be evaluated using R-based metrics
> (`expectancy_r_per_trade`/`profit_factor_r`, introduced by PR #322).
> Both B_BAND and C_BAND classified `FAILED` here on clearly negative
> price-per-share figures (e.g. -0.446, -0.250); a risk-normalized rerun
> was not performed, so this document does not claim the R-normalized
> magnitude, only that the price-based verdict itself should not be read
> as already risk-normalized. No trade counts, win rates, or dollar-based
> figures in this document have changed -- this is a labeling
> clarification only, not a rerun. See
> `docs/research/ORB_BASELINE_FOURTH_PERIOD_VALIDATION.md` for the
> corrected R-based methodology.

## Third period

| | |
|---|---|
| First session | 2024-04-03 |
| Last session | 2025-04-02 |
| Sessions | 252 (preferred target met) |
| Reference day (window ends strictly before) | 2025-04-03 (the out-of-sample study's own first session) |
| Overlap with out-of-sample period (2025-04-03..2026-04-06) | none (adjacent, zero gap, zero overlap by construction) |
| Overlap with discovery period (2026-04-07..2026-08-13) | none |
| Calendar source | `src.orb_research_historical_calendar` (RESEARCH-ONLY; isolated from production runtime and the digest-pinned checkpoint closure) -- **extended in this study from a 2025-01-01 floor to 2024-01-01** to reach this window; see "Calendar extension" below |
| Data source | Alpaca IEX minute bars, credentialed fetch (`ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY` from environment, never printed), on-disk cache |
| Coverage | 97.88% (VALID) for both configurations |

## Calendar extension

The research-only historical calendar (`src/orb_research_historical_calendar.py`,
never pinned, isolated from production and the checkpoint closure) covered
only 2025-01-01 onward before this study. The 252-session window ending
immediately before the out-of-sample period's own first session
(2025-04-03) reaches back to 2024-04-03 -- outside that coverage. Per this
study's own instruction, extending a *pinned* production file was ruled
out; instead `RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE` was moved back to
2024-01-01 and a `RESEARCH_FULL_HOLIDAYS_2024`/`RESEARCH_EARLY_CLOSE_DAYS_2024`
table (standard, publicly published NYSE 2024 holiday/early-close schedule)
was added alongside the existing 2025 table, with 15 new tests (holiday/
early-close boundary checks, a year-end 2024/2025 boundary continuity
check, disjointness of the two years' holiday sets, and a direct
non-overlap check for this exact third-period window). No pinned file
changed; `checkpoint_implementation_digest` is unaffected.

## Universe

9-symbol liquidity-qualified universe, ranked by `rank_liquidity_qualified_symbols`
computed **entirely from bars observed within this study's own third-period
session dates** (not the OOS or discovery periods' universes, not
hardcoded) -- the same non-look-ahead methodology the corrected OOS study
(#304/#307/#314) and the regime diagnostic (#310/#315/#316) already use:

```
NVDA, AAPL, TSLA, AMZN, MSFT, GOOGL, AMD, BAC, INTC
```

**Documented limitation** (same one carried forward from the prior two
studies): the candidate pool this ranking draws from
(`all_pool_symbols_with_production`, ~100 mega/large-cap symbols) is a
static, hand-curated list authored 2026-08, after this third period. A
symbol's presence in the pool could in principle reflect information not
perfectly available in real time within 2024-2025. The ranking *within*
the pool is genuinely computed from in-period data only. The
smaller 9-symbol universe here (vs. 17 for the OOS period) reflects a
narrower set clearing the fixed liquidity thresholds over this specific
252-session window, not a methodology change.

## Configurations tested (fixed, no sweep)

| Config | Gap | RVOL base (BASE) | RVOL band (BAND) |
|---|---|---|---|
| B | 0.75% | >=1.50x | >=1.50x AND <2.00x |
| C | 0.50% | >=1.50x | >=1.50x AND <2.00x |

BAND is a strict post-hoc filter of BASE's own candidates by the canonical
`SignalDecision.relative_volume` the signal engine already computed --
never a second engine run with a different threshold. Everything else held
canonical: 5-minute opening range, SPY>VWAP, completed-bar breakout,
long-only, canonical 09:40-11:00 entry window, unmodified shadow-execution
simulator/exit rules.

## Results (all four cells)

| Config | Candidate Days | Rate | Occurrences | Closed | Win Rate | Avg Winner | Avg Loser | Expectancy | PF | Cum R | Max DD | Stop-Out | Longest Losing Streak |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B_BASE | 59/252 | 23.41% | 76 | 73 | 38.36% | 3.537 | -2.361 | -0.099 | 0.932 | -1.936 | 44.80 | 47.95% | 9 |
| B_BAND | 29/252 | 11.51% | 33 | 32 | 34.38% | 2.738 | -2.114 | -0.446 | 0.679 | -3.380 | 21.08 | 46.88% | 6 |
| C_BASE | 67/252 | 26.59% | 87 | 84 | 39.29% | 3.596 | -2.289 | 0.023 | 1.016 | -1.850 | 42.74 | 47.62% | 11 |
| C_BAND | 36/252 | 14.29% | 40 | 39 | 33.33% | 3.340 | -2.044 | -0.250 | 0.817 | -5.448 | 22.27 | 48.72% | 9 |

Sample sizes: all four cells clear the predeclared 15-closed-trade minimum
(32-84 closed trades each).

### Monthly distribution and symbol contribution

Structured outputs: `orb_rvol_band_third_period_validation_monthly.csv`
(46 rows) and `orb_rvol_band_third_period_validation_symbols.csv` (28
rows). For both BAND cells, per-symbol P&L is broadly negative or
near-breakeven across most of the 9-symbol universe (not concentrated in
one bad actor):

| Symbol | B_BAND closed | B_BAND P&L | C_BAND closed | C_BAND P&L |
|---|---|---|---|---|
| AAPL | 2 | -2.140 | 3 | -2.400 |
| AMD | 4 | -2.335 | 4 | -2.335 |
| AMZN | 5 | -7.630 | 6 | -8.955 |
| GOOGL | 3 | -0.850 | 4 | -1.860 |
| MSFT | 4 | +1.585 | 5 | +0.345 |
| NVDA | 6 | +4.055 | 6 | +4.055 |
| TSLA | 8 | -6.950 | 11 | +1.415 |

NVDA is the best contributor in both BAND cells but is small relative to
total closed-trade count (6/32 for B, 6/39 for C) -- this is broad
underperformance, not one symbol dragging down an otherwise-profitable
set.

### One-trade-per-day view

| Config | View | Occurrences | Closed | Win Rate | Expectancy | PF |
|---|---|---|---|---|---|---|
| B_BASE | ONE_TRADE_PER_DAY | 59 | 56 | 37.50% | -0.231 | 0.856 |
| B_BAND | ONE_TRADE_PER_DAY | 29 | 28 | 32.14% | -0.491 | 0.664 |
| C_BASE | ONE_TRADE_PER_DAY | 67 | 64 | 40.63% | 0.033 | 1.022 |
| C_BAND | ONE_TRADE_PER_DAY | 36 | 35 | 31.43% | -0.263 | 0.815 |

Restricting to one trade per day does not rescue BAND on this period
either -- it stays negative for both configs, consistent with the
all-candidates view.

## BAND effect (BASE vs BAND, this period)

| Metric | B: BASE | B: BAND | Delta | C: BASE | C: BAND | Delta |
|---|---|---|---|---|---|---|
| Trade count | 73 | 32 | -41 | 84 | 39 | -45 |
| Candidate-day rate | 23.41% | 11.51% | -11.90 pp | 26.59% | 14.29% | -12.30 pp |
| Win rate | 38.36% | 34.38% | -3.98 pp | 39.29% | 33.33% | -5.95 pp |
| Expectancy | -0.099 | -0.446 | -0.347 | 0.023 | -0.250 | -0.272 |
| Profit factor | 0.932 | 0.679 | -0.254 | 1.016 | 0.817 | -0.200 |
| Cumulative R | -1.936 | -3.380 | -1.445 | -1.850 | -5.448 | -3.598 |
| Max drawdown | 44.80 | 21.08 | -23.72 | 42.74 | 22.27 | -20.47 |
| Stop-out rate | 47.95% | 46.88% | -1.07 pp | 47.62% | 48.72% | +1.10 pp |

**On this period, BAND is not merely "no better" than BASE -- it is
measurably worse on both expectancy and profit factor for both
configurations**, trading roughly half as often for a worse per-trade
outcome. The one favorable BAND effect is a smaller maximum drawdown
(a direct consequence of fewer trades, not better trade selection).

## Three-period comparison (central output)

| Config | Period | Closed Trades | Win Rate | Expectancy | PF | Cum R | Top-3 PnL Share |
|---|---|---|---|---|---|---|---|
| B_BAND | Discovery (90 sessions) | 22 | 68.2% | 1.361 | 3.002 | 6.324 | 70.5% |
| B_BAND | Out-of-sample (252 sessions) | 56 | 42.9% | 1.027 | 1.870 | 4.656 | 131.9%\* |
| B_BAND | **Third period (252 sessions)** | **32** | **34.4%** | **-0.446** | **0.679** | **-3.380** | -33.6%\* |
| C_BAND | Discovery (90 sessions) | 26 | 65.4% | 1.785 | 3.627 | 8.324 | 81.5% |
| C_BAND | Out-of-sample (252 sessions) | 70 | 41.4% | 0.784 | 1.619 | 3.990 | 114.9%\* |
| C_BAND | **Third period (252 sessions)** | **39** | **33.3%** | **-0.250** | **0.817** | **-5.448** | -59.7%\* |

\* Top-3 P&L share exceeds 100% or is negative when total realized P&L is
small/negative and individual contributors are mixed-sign -- reported
honestly as computed, not clamped or smoothed; it stops being a
meaningful "concentration" read once total P&L is near zero (this
happens for OOS too, not only the third period).

**The band's edge, positive and directionally consistent across the prior
two periods for both configurations independently, reverses sign entirely
on this third period.** This is the opposite finding from what the
regime diagnostic's own third-period-validation recommendation was hoping
to confirm, reported as measured.

## Symbol concentration and single-symbol dependence (third period)

| Config | Total P&L | Best contributor | Excl. best symbol | Excl. TSLA |
|---|---|---|---|---|
| B_BAND | -14.265 | NVDA (+4.055) | **-18.320 (still negative)** | **-7.315 (still negative)** |
| C_BAND | -9.735 | NVDA (+4.055) | **-13.790 (still negative)** | **-11.150 (still negative)** |

Unlike the prior two periods (where both configs stayed profitable after
removing their best contributor), **this period's BAND result is
unprofitable regardless of which symbol is removed** -- the failure is
broad, not attributable to one name's absence or presence.

## Predeclared classification (fixed before interpreting third-period results)

Six criteria, from `src/orb_rvol_band_third_period_research.py::classify_band_validation`,
fixed before any third-period number was computed: (1) sufficient sample
(>=15 closed trades); (2) positive expectancy in the third period; (3)
profit_factor>1 in the third period; (4) BAND not materially worse than
BASE on both expectancy and PF; (5) not dependent on one symbol; (6)
direction consistent with both prior periods. FAILED is assigned
whenever expectancy<=0 or profit_factor<=1 on their own, regardless of the
other checks.

| Config | Verdict | Failing checks |
|---|---|---|
| B_BAND | **FAILED** | positive_expectancy, profit_factor_above_1, band_not_materially_worse_than_base, not_dependent_on_one_symbol, direction_consistent_with_prior_periods (all fail) |
| C_BAND | **FAILED** | same five |

No criterion was loosened after seeing these results.

## Answers to the specific questions

1. **Does the RVOL 1.50x-1.99x band hold on a third, independent period?**
   No. Both configurations reverse from consistently positive (both prior
   periods) to negative expectancy and sub-1 profit factor.
2. **Is BAND at least not worse than BASE here?** No -- BASE itself is
   weak-to-flat on this period (B: -0.099 expectancy, C: +0.023), and BAND
   is worse than BASE on both expectancy and PF for both configs.
3. **Is the third-period failure driven by one symbol?** No -- both
   configs stay unprofitable after removing either their best contributor
   or TSLA specifically.
4. **Is the third-period failure a low-sample artifact?** No -- all four
   cells clear the 15-closed-trade minimum by a wide margin (32-84 closed
   trades).

## Final decision

**BAND_NOT_VALIDATED**

Both B_BAND and C_BAND classify FAILED on the third, independent period:
negative expectancy and profit_factor<=1, BAND performing worse than BASE,
and no single-symbol explanation. This does not authorize any production
threshold change, and none is made here. This also **reverses** the
regime diagnostic's (#310/#315/#316) own targeted-follow-up recommendation
-- the honest reading is that the RVOL 1.50x-1.99x band, positive and
consistent across two periods, does not generalize to a third,
independent one.

**The $30K capital-path experiment is NOT recommended to move forward
next** on this evidence. Recommended next step (not implemented here): if
this line of research continues, treat the two-period "PROMISING" result
as provisionally falsified rather than pursue a variant of the same band;
a materially different research question (not a neighboring-band search
of this same signal, which the anti-data-mining rules for this study
explicitly forbid) would be needed before any further parameter-review
consideration.

## Reproducing

```
python scripts/research_orb_rvol_band_third_period_validation.py --fetch-missing
```

(Or `--cache-only`, the default, once the on-disk cache is populated.)

Structured outputs:
`artifacts/research-rvol-band-third-period/results/orb_rvol_band_third_period_validation_report.json`
(full detail: per-cell trade quality, monthly/symbol breakdowns, BAND
effect, three-period comparison, classification),
`orb_rvol_band_third_period_validation_matrix.csv` (4-row summary),
`orb_rvol_band_third_period_validation_monthly.csv` (46 rows), and
`orb_rvol_band_third_period_validation_symbols.csv` (28 rows).
