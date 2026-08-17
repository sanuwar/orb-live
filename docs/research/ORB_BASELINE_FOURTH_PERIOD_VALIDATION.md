# ORB C_BASE Fourth-Period Baseline Validation (Issue #321)

Research-only. Final planned baseline-extension study before deciding
whether C_BASE deserves exit-management research. Issue #319/PR #320
classified C_BASE (gap>=0.50%/RVOL>=1.50x) as REGIME_DEPENDENT_EDGE
across three periods. This study adds exactly ONE further independent
period, **P0** -- the ~252 sessions immediately preceding P1 -- and asks
whether C_BASE retains enough underlying edge across a longer history to
justify the queued profit-protection/adaptive-stop experiment. No
threshold search, no production change, no trailing-stop implementation,
no $30K capital-path experiment performed here.

**Efficiency**: P1/P2/P3 were NOT recomputed from minute bars. Their
trade-quality aggregates are reused verbatim from the merged #320
artifact, algebraically recombined with P0's freshly-computed real
results (`src/orb_baseline_fourth_period_research.py::combine_trade_quality_aggregates`).
This recombination method was verified exact against #320's own
real-concatenated-row ground truth before use (reproduced #320's combined
291-trade, 0.3436-expectancy figure to full floating-point precision).
Only P0's empirical step required a bar fetch/precompute/simulate pass:
**3226 seconds (~54 minutes)**, versus #320's ~4.5 hours for three
periods -- confirming the reuse-not-recompute design worked.

## P0 (fourth period)

| | |
|---|---|
| First session | 2023-03-31 |
| Last session | 2024-04-02 |
| Sessions | 252 (preferred target met) |
| Reference day (window ends strictly before) | 2024-04-03 (P1's own first session) |
| Overlap with P1/P2/P3 | none (confirmed programmatically before any fetch) |
| Calendar source | `src.orb_research_historical_calendar`, extended in this study from a 2024-01-01 floor to 2023-01-01 (sourced 2023 NYSE holiday/early-close table; only 2 half-days that year -- Christmas Eve 2023 fell on a Sunday, so no separate Christmas-adjacent early close) |
| Coverage | 99.09% (VALID) |
| Empirical runtime | 3226.2s (~54 min) |

## Calendar and freshness (validated before the empirical run)

The research-only historical calendar's floor was extended from
2024-01-01 to 2023-01-01 (no pinned file touched). The freshness policy
was **derived from the actual requested window** rather than another
hardcoded magic number (Issue #318 found an 800-day hardcoded policy
silently too narrow for a study whose own reach exceeded it):

```
P0 lookback start (RVOL 20d): 2023-03-03
freshness policy: max_age_seconds=111628800 (1292.0 days)
  -- covers lookback_start age (1262 days) with a 30-day margin
```

This was printed and validated **before** launching the expensive fetch
step, per the study's own performance requirement -- not discovered hours
into a run.

## P0 universe views

**VIEW A (period-native)**: 7 symbols, computed from P0's own in-period
bars only: `NVDA, AAPL, MSFT, TSLA, AMZN, GOOGL, AMD`.

**VIEW B (anchored common cohort)**: the fixed #320 cohort, reused
unchanged, never recomputed or adjusted by P0's own outcomes:
`AAPL, AMD, AMZN, BAC, GOOGL, INTC, MSFT, NVDA, TSLA` (9 symbols).

P0's native universe is a strict subset of the anchored cohort (missing
BAC and INTC, neither of which cleared P0's own liquidity thresholds).
**VIEW A and VIEW B produced identical results** (99 candidates, 97
closed) -- BAC and INTC, though included in the precomputed inputs table
for the cohort evaluation, simply produced zero qualifying candidates in
P0. This is a genuine data outcome, not a computation error: both symbols
were evaluated, neither happened to gap/RVOL-qualify during this period.

## P0 core metrics (both views identical)

| | |
|---|---|
| Candidate days | 75/252 (29.76%) |
| Candidate occurrences | 99 |
| Closed trades | 97 |
| Win rate | 35.05% |
| Average winner | 2.200 |
| Average loser | -1.541 |
| Expectancy | **-0.229** |
| Profit factor | **0.771** |
| Cumulative R | -12.331 |
| Maximum drawdown | 30.08 |
| Longest losing streak | 9 |
| Stop-out rate | 54.64% |

**P0 is the weakest of all four periods, individually.** Expectancy and
profit factor are both below breakeven -- but not catastrophically so
against the predeclared thresholds (expectancy < -1.0 or PF < 0.5 would
have been classified catastrophic; P0's -0.229/0.771 clear neither bar).

### One-trade-per-day (P0, native)

| | |
|---|---|
| Closed | 73 |
| Win rate | 36.99% |
| Expectancy | -0.201 |
| Profit factor | 0.797 |
| Maximum drawdown | 20.69 |

Restricting to the first candidate of the day improves P0 slightly (as in
every prior period) but does not flip it positive.

### Symbol contribution (P0, native)

| Symbol | Closed | Win Rate | Expectancy | PF |
|---|---|---|---|---|
| AMD | 19 | 36.8% | **0.178** | 1.160 |
| NVDA | 18 | 33.3% | 0.026 | 1.076 |
| GOOGL | 18 | 38.9% | -0.114 | 0.853 |
| AAPL | 6 | 33.3% | -0.336 | 0.481 |
| AMZN | 13 | 38.5% | -0.357 | 0.554 |
| TSLA | 14 | 28.6% | -0.574 | 0.699 |
| MSFT | 9 | 33.3% | -1.039 | 0.367 |

Only AMD and NVDA are (barely) net-positive contributors in P0. Notably,
TSLA -- the single largest positive contributor across the OTHER three
periods combined -- is a net drag here.

## P0 profit-protection diagnostic (MFE, descriptive only, no exit change)

| Threshold | Losers reaching it | Fraction |
|---|---|---|
| +0.25R | 36 / 63 | 57.1% |
| +0.50R | 25 / 63 | **39.7%** |
| +0.75R | 15 / 63 | 23.8% |
| +1.00R | 8 / 63 | 12.7% |

**#320's finding replicates in this fourth, independent period**: 39.7%
of P0's eventual losers reached +0.50R before reversing, close to #320's
combined figure of 41.5%. Winner MFE mean 1.343R (median 1.360R);
realized-R mean 0.976R (median 0.986R, computed from P0's own winners).
This clears the predeclared 30% meaningful-MFE bar
(`p0_meaningful_mfe_fraction = True`).

## Four-period table

| Period | View | Selection | Closed | Expectancy | PF | Win Rate | Cum R | Max DD |
|---|---|---|---|---|---|---|---|---|
| P0 | native | all-candidates | 97 | -0.229 | 0.771 | 35.1% | -12.331 | 30.08 |
| P0 | native | one-trade-per-day | 73 | -0.201 | 0.797 | 37.0% | -7.365 | 20.69 |
| P0 | anchored_cohort | all-candidates | 97 | -0.229 | 0.771 | 35.1% | -12.331 | 30.08 |
| P1_THIRD | native | all-candidates | 84 | 0.023 | 1.016 | 39.3% | -1.850 | unavailable\* |
| P2_OOS | native | all-candidates | 150 | 0.258 | 1.156 | 40.7% | 0.822 | unavailable\* |
| P3_DISCOVERY | native | all-candidates | 57 | 1.042 | 1.789 | 57.9% | 12.932 | unavailable\* |

\* Maximum drawdown for P1-P3 requires chronologically-ordered per-trade
P&L, which #320 did not persist (only aggregate statistics survive its
artifact) -- reported as unavailable rather than recomputed from bars
(the efficiency constraint this study operates under) or estimated.

Full per-view/per-selection detail (16 rows, including cohort views for
P1-P3): `orb_baseline_fourth_period_four_period_comparison.csv`.

## Full-history view (P0+P1+P2+P3, descriptive validation only)

Combined via exact algebraic recombination (native view, C_BASE):

| | Closed | Expectancy | PF | Win Rate | Cum R |
|---|---|---|---|---|---|
| ALL_CANDIDATES | 388 | **0.200** | **1.145** | 41.5% | -0.427 |
| ONE_TRADE_PER_DAY | 269 | **0.339** | **1.252** | 44.2% | 10.213 |
| Excluding TSLA | 327 | 0.049 | 1.040 | 40.7% | -7.407 |
| Excluding best contributor (TSLA) | 327 | 0.049 | 1.040 | 40.7% | -7.407 |

TSLA is the single largest overall contributor across all four periods
combined (confirmed: excluding-TSLA and excluding-best-symbol produce
identical figures). The combined edge survives its removal --
expectancy stays positive (0.049) though it thins substantially,
consistent with #319/#320's own finding that TSLA materially but not
entirely drives net profitability.

- Fraction of the four periods with positive native expectancy: **75%** (3/4 -- only P0 is negative)
- Fraction with PF>1: **75%** (3/4)

## Quarterly stability (merged: P0's own quarters + #320's 10 reused quarters)

14 quarters total (P0 contributes 2023-Q2 through 2024-Q1 as new
entries; 2024-Q2 -- straddled by P0's last two sessions and P1's first
sessions -- is combined via the same exact recombination method, not
duplicated or dropped). All 14 cleared the 8-closed-trade adequacy floor.

| Quarter | Closed | Expectancy | PF |
|---|---|---|---|
| 2023-Q2 | 25 | -0.710 | 0.414 |
| 2023-Q3 | 19 | 0.551 | 1.912 |
| 2023-Q4 | 24 | -0.622 | 0.432 |
| 2024-Q1 | 29 | -0.001 | 0.999 |
| 2024-Q2 | 18 | -0.022 | 0.983 |
| 2024-Q3 | 28 | -0.347 | 0.729 |
| 2024-Q4 | 20 | -0.741 | 0.712 |
| 2025-Q1 | 18 | **1.491** | **4.768** |
| 2025-Q2 | 36 | 0.428 | 1.340 |
| 2025-Q3 | 40 | -0.100 | 0.945 |
| 2025-Q4 | 36 | 0.770 | 1.481 |
| 2026-Q1 | 35 | 0.120 | 1.064 |
| 2026-Q2 | 41 | 0.679 | 1.425 |
| 2026-Q3 | 19 | 1.414 | 2.633 |

- Fraction of adequately sampled quarters with positive expectancy: **50%** (7/14, down from #320's 60%)
- Fraction with PF>1: **50%** (7/14)
- Best quarter: still **2025-Q1** (expectancy 1.491, PF 4.768)
- Worst quarter: still **2024-Q4** (expectancy -0.741, PF 0.712)
- **Longest sequence of negative-expectancy quarters: 5** (2023-Q4 through 2024-Q4 consecutively -- longer than #320's own 3-quarter worst stretch; P0 extends the bad run rather than shortening it)

## Predeclared baseline conclusion

Criteria fixed in `src/orb_baseline_fourth_period_research.py::classify_baseline_conclusion`
before P0 was computed. Seven checks for BASELINE_SUPPORTED_FOR_EXIT_RESEARCH:

| # | Criterion | Result |
|---|---|---|
| 1 | Combined expectancy > 0 | 0.200 > 0 -- **pass** |
| 2 | Combined PF > 1 | 1.145 > 1 -- **pass** |
| 3 | One-trade-per-day expectancy > 0 and PF > 1 | 0.339 / 1.252 -- **pass** |
| 4 | Excluding best symbol still expectancy > 0 and PF > 1 | 0.049 / 1.040 -- **pass** |
| 5 | At least 3 of 4 periods non-negative | P1/P2/P3 non-negative, P0 negative -- exactly 3/4 -- **pass** |
| 6 | No catastrophic P0 instability | -0.229 > -1.0 and 0.771 > 0.5 -- **pass** |
| 7 | MFE behavior meaningfully present | 39.7% >= 30% at +0.50R -- **pass** |

All seven criteria pass without any weakening after seeing P0.

### **Verdict: BASELINE_SUPPORTED_FOR_EXIT_RESEARCH**

The combined four-period edge is positive and survives every predeclared
sensitivity check (one-trade-per-day, best-symbol removal). P0 is the
weakest period on its own -- and materially so, extending the worst
consecutive-quarter losing stretch from 3 to 5 -- but it is not
catastrophic, and 3 of 4 periods individually remain non-negative. The
profit-protection MFE signature (#320's 41.5%, now corroborated by P0's
independent 39.7% at +0.50R) is present in both periods checked this way.

## Next milestone

Per the fixed rule (BASELINE_SUPPORTED_FOR_EXIT_RESEARCH ->
PROFIT_PROTECTION_EXIT_EXPERIMENT, no further condition):

### **Recommendation: PROFIT_PROTECTION_EXIT_EXPERIMENT**

The queued adaptive-stop/profit-protection experiment is now
recommended as the next research milestone. It is **not implemented
here** -- this study is validation only. The $30K capital-path
experiment remains queued behind it (and behind selecting a final
research exit policy), per this study's own explicit scope limit; it is
not authorized or performed by this result.

## Reproducing

```
python scripts/research_orb_baseline_fourth_period_validation.py --fetch-missing
```

(Or `--cache-only`, the default, once the on-disk cache is populated.)
Fetches/simulates P0 only; P1-P3 are reused from
`src/orb_baseline_fourth_period_research.py::PRIOR_PERIODS_REFERENCE`/
`PRIOR_QUARTERS_REFERENCE`, sourced from the merged #320 artifact.

Structured outputs (`artifacts/research-baseline-fourth-period/results/`):
`orb_baseline_fourth_period_report.json` (full detail),
`orb_baseline_fourth_period_combined_summary.json` (four-period combined
figures only), `orb_baseline_fourth_period_four_period_comparison.csv`
(16 rows), `orb_baseline_fourth_period_p0_matrix.csv`,
`orb_baseline_fourth_period_symbols.csv` (7 rows),
`orb_baseline_fourth_period_mfe_summary.csv`.
