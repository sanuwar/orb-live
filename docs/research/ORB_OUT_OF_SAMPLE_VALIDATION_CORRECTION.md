# ORB Out-of-Sample Validation — Correction (Issue #313)

**This document corrects the trade-quality conclusions in the merged
`docs/research/ORB_OUT_OF_SAMPLE_VALIDATION.md` (Issue #304/PR #307).
That document's own final decision, `RESEARCH_CONFIGURATION_NOT_ROBUST`,
is SUPERSEDED by this correction's `READY_FOR_PRODUCTION_PARAMETER_REVIEW`.**

PR #307 is not reopened, amended, or force-pushed. This is a new,
independent milestone (Issue #313) that reruns the identical study with a
bug fix and reports the corrected truth alongside the original, explicitly
labeled.

> **Metric-unit note (Issue #321/PR #322), independent of the calendar
> bug this document itself corrects.** Legacy "expectancy"/"profit
> factor" throughout this document (including the `ROBUST` classifications
> for C and E below, e.g. "expectancy 0.258, PF 1.156") are computed from
> raw price-per-share realized P&L -- they are **NOT** R-normalized.
> Risk-normalized profitability must be evaluated using R-based metrics
> (`expectancy_r_per_trade`/`profit_factor_r`, introduced by PR #322).
> **Risk-normalized interpretation of the `ROBUST` classifications below
> is provisional pending a dedicated R-normalized re-evaluation of
> configurations C and E** -- the predeclared `classify_robustness`
> criteria were evaluated against price-per-share figures, and a
> risk-normalized rerun could in principle reach a different verdict, the
> same way Issue #321's own R-normalized correction reversed that
> study's price-based conclusion. No trade counts, win rates, or
> dollar-based figures in this document have changed -- this is a
> labeling clarification only, not a rerun. See
> `docs/research/ORB_BASELINE_FOURTH_PERIOD_VALIDATION.md` for the
> corrected R-based methodology.

## What #307 got right

- **Candidate selection and candidate-frequency numbers are unaffected and
  confirmed identical** on rerun: candidate days, candidate-day rate, and
  total candidate occurrences match #307 exactly for all five
  configurations (see "Frequency numbers unchanged" below). Candidate
  selection never calls the buggy function.
- The out-of-sample period (2025-04-03 through 2026-04-06, 252 sessions),
  universe methodology, and liquidity-qualified universe (17 symbols) are
  unchanged and reproduced identically.
- The five configurations, exit rules, and simulator assumptions are
  unchanged -- no new threshold, no new optimization.

## What #307 got wrong

**#307's trade-quality figures (expectancy, profit factor, win rate,
drawdown, robustness classification) were computed from an effective
~63-session subset of the 252-session period, not the full period.**

### Root cause (Issue #311, fixed by merged PR #312)

`src/shadow_execution_simulator.py::_validated_bars` called
`validate_market_bar` without forwarding a calendar provider, so it always
validated subsequent bars against the production-only calendar
(`src.exchange_calendar`, coverage 2026-2027). Any bar dated in 2025 was
classified `MARKET_HOLIDAY`, and the entire simulated trade for that
candidate returned `DATA_INVALID` -- silently excluded from "closed
trades," "win rate," "expectancy," everything trade-quality related.

### Affected metrics

Every trade-quality figure for every configuration: closed trades, wins,
losses, win rate, average winner/loser, expectancy, profit factor,
cumulative R, stop-out rate, max drawdown, robustness classification,
one-trade-per-day vs all-candidates, discovery-vs-OOS comparison.

### Unaffected metrics (confirmed identical on rerun)

Candidate days, candidate-day rate, total candidate occurrences, universe
selection, coverage percentage.

## Validation before accepting the corrected results

Per Issue #313's own goal document, these were checked *before*
interpreting any P&L:

**1. Candidate-frequency numbers match #307 exactly.**

| Config | #307 candidate days | Corrected candidate days | #307 rate | Corrected rate | #307 candidates | Corrected candidates |
|---|---|---|---|---|---|---|
| A | 25/252 | 25/252 | 9.92% | 9.92% | 30 | 30 |
| B | 83/252 | 83/252 | 32.94% | 32.94% | 127 | 127 |
| C | 94/252 | 94/252 | 37.30% | 37.30% | 152 | 152 |
| D | 93/252 | 93/252 | 36.90% | 36.90% | 162 | 162 |
| E | 110/252 | 110/252 | 43.65% | 43.65% | 202 | 202 |

Identical in every field -- exactly as expected, since candidate selection
never touches the buggy code path.

**2/3/4. Closed trades now exist in every 2025 month, and zero
`DATA_INVALID` outcomes are attributable to calendar coverage.**

Closed trades by month, config E (0.50%/1.25x, the largest sample):

| Month | Closed trades |
|---|---|
| 2025-04 | 15 |
| 2025-05 | 10 |
| 2025-06 | 25 |
| 2025-07 | 13 |
| 2025-08 | 12 |
| 2025-09 | 22 |
| 2025-10 | 20 |
| 2025-11 | 24 |
| 2025-12 | 8 |
| 2026-01 | 19 |
| 2026-02 | 10 |
| 2026-03 | 17 |
| 2026-04 | 4 |

No month is silently absent. Under the old (buggy) code, every month from
2025-04 through 2025-12 would have contributed **zero** closed trades
despite candidates existing there in normal numbers. Every configuration
(A-E) was checked the same way; every one now shows closed trades spread
across all thirteen months. `MARKET_HOLIDAY`-attributed `DATA_INVALID`
count is **0** for all five configurations (instrumented directly --
`state_distribution`/`data_invalid_reason_distribution` in the corrected
JSON).

**5. Remaining non-closed outcomes have legitimate, documented reasons** --
a handful of `PENDING_NOT_FILLED` (entry limit never touched) and one
`FILLED_OPEN` (still open at the data boundary) per configuration, both
pre-existing, expected simulator states unrelated to calendar coverage.

**6. Production/default simulator behavior is unchanged** -- proven by
`tests/test_shadow_execution_simulator_calendar_seam.py` (merged in #312):
every call that omits `session_bounds_provider` behaves byte-for-byte as
before.

**7. No look-ahead introduced** -- the fix only changes which *calendar*
a bar's trading date is checked against; it adds no new information a
candidate's own decision-time signal didn't already have.

## Corrected five-configuration result

| Config | Candidate Days | Rate | Trades | Win Rate | Avg Winner | Avg Loser | Expectancy | PF | Cumulative R | Stop-out | Max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A (1.00%/3.00x) | 25/252 | 9.92% | 30 | 40.0% | 2.982 | -3.564 | -0.946 | 0.558 | -1.479 | 33.3% | 29.96 |
| B (0.75%/1.50x) | 83/252 | 32.94% | 126 | 40.5% | 4.642 | -2.859 | 0.178 | 1.104 | -1.787 | 38.9% | 36.40 |
| C (0.50%/1.50x) | 94/252 | 37.30% | 150 | 40.7% | 4.701 | -2.787 | 0.258 | 1.156 | 0.822 | 41.3% | 32.09 |
| D (0.75%/1.25x) | 93/252 | 36.90% | 161 | 40.4% | 4.379 | -2.972 | -0.004 | 0.998 | -2.380 | 42.2% | 40.33 |
| E (0.50%/1.25x) | 110/252 | 43.65% | 199 | 40.2% | 4.434 | -2.800 | 0.108 | 1.065 | 1.618 | 43.7% | 41.17 |

Longest losing streak (config E, ALL_CANDIDATES view): 9 consecutive
losses. Best single day: 2025-11-24 (+25.27). Worst single day: 2025-11-20
(-23.62).

## Old #307 vs corrected -- direct comparison

| Config | Metric | Merged #307 (truncated) | Corrected rerun | Difference |
|---|---|---|---|---|
| A | Closed trades | 5 | 30 | +25 |
| A | Win rate | 20.0% | 40.0% | +20.0 pp |
| A | Expectancy | -3.407 | -0.946 | +2.461 |
| A | Profit factor | 0.031 | 0.558 | +0.527 |
| B | Closed trades | 34 | 126 | +92 |
| B | Win rate | 47.1% | 40.5% | -6.6 pp |
| B | Expectancy | 0.053 | 0.178 | +0.125 |
| B | Profit factor | 1.026 | 1.104 | +0.078 |
| C | Closed trades | 38 | 150 | +112 |
| C | Win rate | 44.7% | 40.7% | -4.0 pp |
| C | Expectancy | -0.013 | 0.258 | +0.271 |
| C | Profit factor | 0.993 | 1.156 | +0.163 |
| D | Closed trades | 43 | 161 | +118 |
| D | Win rate | 44.2% | 40.4% | -3.8 pp |
| D | Expectancy | -0.410 | -0.004 | +0.406 |
| D | Profit factor | 0.822 | 0.998 | +0.176 |
| **E** | **Closed trades** | **50** | **199** | **+149** |
| **E** | **Win rate** | **42.0%** | **40.2%** | **-1.8 pp** |
| **E** | **Expectancy** | **-0.428** | **0.108** | **+0.536** |
| **E** | **Profit factor** | **0.799** | **1.065** | **+0.266** |
| E | Max drawdown | 31.74 | 41.17 | +9.43 |

Every configuration's closed-trade count roughly quadrupled (as expected:
~252/63 ≈ 4x more of the period actually contributed trades). Win rate
drifted down slightly for B-E (the newly-included 2025 months were
somewhat weaker win-rate-wise than the Q1-2026-only subset #307
accidentally measured), but expectancy and profit factor moved
**materially positive** for every non-baseline configuration except D
(which moved from clearly negative to essentially breakeven). Max drawdown
is larger for the full period (expected -- a longer period has more
opportunity to draw down) and should be read as the more representative
figure going forward.

## One-trade-per-day vs all-candidates (corrected)

| Config | View | Trades | Win Rate | Expectancy | PF |
|---|---|---|---|---|---|
| B | ALL_CANDIDATES | 126 | 40.5% | 0.178 | 1.104 |
| B | ONE_TRADE_PER_DAY | 83 | 45.8% | 0.711 | 1.455 |
| C | ALL_CANDIDATES | 150 | 40.7% | 0.258 | 1.156 |
| C | ONE_TRADE_PER_DAY | 93 | 47.3% | 0.816 | 1.544 |
| D | ALL_CANDIDATES | 161 | 40.4% | -0.004 | 0.998 |
| D | ONE_TRADE_PER_DAY | 93 | 41.9% | 0.383 | 1.225 |
| E | ALL_CANDIDATES | 199 | 40.2% | 0.108 | 1.065 |
| E | ONE_TRADE_PER_DAY | 109 | 45.0% | 0.663 | 1.440 |

Same pattern confirmed with corrected, full-period data: restricting to
the first breakout of each day (current ORB-LIVE v1's own bound)
outperforms taking every candidate, for every configuration -- including D,
which flips from breakeven-negative under ALL_CANDIDATES to solidly
positive under ONE_TRADE_PER_DAY. `ONE_TRADE_PER_DAY` selection uses only
the decision-time signal timestamp, never future P&L, matching #307's own
rule exactly.

## Robustness classification (corrected, same fixed criteria as #304/#307)

| Config | Verdict | Notes |
|---|---|---|
| A | NOT_ROBUST | Expectancy and PF still negative/sub-1 with a full, adequate sample (30 trades, was previously INSUFFICIENT_EVIDENCE at n=5) |
| B | **ROBUST** | Was NOT applicable in #307's "primary" pair but marginally positive there too; now solidly positive |
| **C** | **ROBUST** | Was NOT_ROBUST in #307 (expectancy -0.013, PF 0.993); now expectancy 0.258, PF 1.156 |
| D | NOT_ROBUST | Essentially breakeven (expectancy -0.004, PF 0.998) |
| **E** | **ROBUST** | Was NOT_ROBUST in #307 (expectancy -0.428, PF 0.799); now expectancy 0.108, PF 1.065 |

**This materially reverses #307's conclusion for both configurations of
primary interest.** C and E were both classified `NOT_ROBUST` in #307;
both are `ROBUST` under the corrected, full-period evidence.

### Important caveat not part of the predeclared criteria: single-symbol dependence

The predeclared robustness criteria check top-3 symbol concentration
(both C and E pass comfortably: 42.1% and 41.6%, well under the 75%
ceiling) but do not test dependence on the single *best* symbol. Doing
that check here, honestly, rather than only reporting the favorable
top-3 figure:

- **Config E's entire net profitability depends on TSLA.** Total realized
  P&L across all ten traded symbols is +21.51; TSLA alone contributed
  +39.98. Removing TSLA from config E's results leaves the remaining nine
  symbols net **negative** (-18.47).
- **Config C is more resilient to the same test.** Total P&L is +38.68;
  TSLA contributed +36.88. Removing TSLA leaves the remaining symbols only
  marginally positive (+1.80) -- still positive, but barely.

This does not change the `ROBUST` classification above (which is based on
the predeclared, documented criteria), but it is a material fact for
anyone considering a production-parameter review: E's edge is currently a
near-single-stock result; C's is broader but only slightly.

## Discovery vs corrected out-of-sample (C, E)

| | Discovery (90 sessions) | Corrected OOS (252 sessions) | Delta |
|---|---|---|---|
| C candidate-day rate | 44.44% | 37.30% | -7.14 pp |
| C expectancy | 1.042 | 0.258 | -0.784 |
| C profit factor | 1.789 | 1.156 | -0.633 |
| C win rate | 57.9% | 40.7% | -17.2 pp |
| E candidate-day rate | 52.22% | 43.65% | -8.57 pp |
| E expectancy | 1.529 | 0.108 | -1.421 |
| E profit factor | 2.270 | 1.065 | -1.205 |
| E win rate | 54.7% | 40.2% | -14.5 pp |

Both configurations still degrade substantially from the discovery
period to the corrected out-of-sample period -- the discovery-period edge
was genuinely stronger than what generalizes. The corrected picture is
"a real, positive, but much weaker edge that survives out-of-sample,"
not "no edge at all" (the #307 conclusion) and not "the discovery edge
fully holds" (which nobody claimed).

## Final decision

**READY_FOR_PRODUCTION_PARAMETER_REVIEW**

Both C (0.50%/1.50x) and E (0.50%/1.25x) classify `ROBUST` on corrected,
full-252-session out-of-sample evidence: positive expectancy, profit
factor above 1, adequate closed-trade count (150 and 199 respectively),
acceptable candidate-day frequency and top-3 concentration, and frequency
retained relative to the discovery period. This reverses #307's
`RESEARCH_CONFIGURATION_NOT_ROBUST` conclusion.

**This does not authorize any production change.** It recommends that
**C (0.50%/1.50x)** proceed to a separate, explicit production-parameter
review in preference to E, given C's better resilience to the
single-symbol-dependence caveat above (E's edge is currently
TSLA-dependent; C's is not, though only marginally). Any such review
should explicitly re-examine the TSLA-concentration finding before
considering either threshold pair.

## Superseded status of the original document

`docs/research/ORB_OUT_OF_SAMPLE_VALIDATION.md` is annotated with an
explicit SUPERSEDED banner pointing to this document. Its own content is
otherwise left intact as the historical record of what was measured (and
why it was wrong) at the time.

## Reproducing

```
python scripts/research_orb_out_of_sample_validation.py --sessions 252 --cache-only
```

The script now unconditionally supplies
`session_bounds_provider=research_regular_session_bounds` (Issue #312's
seam) into every trade-quality simulation call for the out-of-sample
period. Structured outputs (superseding the #307 artifacts in place, since
they were never committed/tracked): `artifacts/research-out-of-sample-validation/results/orb_out_of_sample_validation_study.json`
(now includes a `calendar_truncation_validation` block per configuration)
and `orb_out_of_sample_validation_matrix.csv`.
