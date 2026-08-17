# ORB Out-of-Sample Historical Validation (Issue #304)

> **⚠ SUPERSEDED (Issue #313).** The trade-quality figures below (closed
> trades, win rate, expectancy, profit factor, max drawdown, robustness
> classification, and the final decision `RESEARCH_CONFIGURATION_NOT_ROBUST`)
> were computed from a bug (Issue #311, fixed by merged PR #312) that
> silently truncated 2025 simulated trades to `DATA_INVALID`, leaving an
> effective ~63-session subset instead of the full 252-session period.
> Candidate-frequency numbers (candidate days, candidate-day rate, total
> candidates) below are unaffected and confirmed unchanged on rerun.
> **See `docs/research/ORB_OUT_OF_SAMPLE_VALIDATION_CORRECTION.md` for the
> corrected trade-quality results and superseding final decision
> (`READY_FOR_PRODUCTION_PARAMETER_REVIEW`).** This document is left intact
> below as the historical record of what was originally measured and why
> it was wrong -- not silently rewritten.

> **Metric-unit note (Issue #321/PR #322), independent of the calendar
> bug above.** "Expectancy"/"Profit Factor" below are computed from raw
> price-per-share realized P&L -- they are **NOT** R-normalized. This is
> a separate, later-discovered issue from the SUPERSEDED calendar bug
> and applies equally to the corrected document
> (`ORB_OUT_OF_SAMPLE_VALIDATION_CORRECTION.md`). No numbers in this
> document have been changed by this note.

Research-only. Answers one question: **if ORB-LIVE had actually followed
the current business logic over a historical period it was never tuned
on, would it have been profitable?**

Issues #300 and #302 (both merged) discovered/selected promising gap/RVOL
thresholds -- notably gap=0.50%/rvol=1.25x -- entirely from one 90-session
window, 2026-04-07 through 2026-08-13 (the "discovery period"). This study
evaluates the same five pre-registered configurations against an earlier
period that was never used to select those thresholds, with zero overlap.
No production threshold, universe, or config changes here; `strategy.enabled`
stays `false`.

## Out-of-sample period

| | |
|---|---|
| First session | 2025-04-03 |
| Last session | 2026-04-06 |
| Sessions | 252 (target met) |
| Reference day (window ends strictly before) | 2026-04-07 |
| Overlap with discovery period (2026-04-07..2026-08-13) | none |
| Calendar source | `src.orb_research_historical_calendar` (RESEARCH-ONLY; isolated from production runtime and the digest-pinned checkpoint closure) |
| Session-open/bar-eligibility resolution | injectable calendar seam (Issue #305, merged), bound to the research-only 2025 provider |
| Data source | Alpaca IEX minute bars, credentialed fetch, on-disk cache |
| Coverage | 98.18% (VALID) for every configuration |

## Universe

17-symbol liquidity-qualified universe, ranked by `rank_liquidity_qualified_symbols`
computed **entirely from bars observed within this study's own out-of-sample
session dates** (not the discovery period's universe, not hardcoded):

```
NVDA, TSLA, AAPL, AMZN, MSFT, GOOGL, META, AVGO, AMD, INTC, BAC, WMT, CSCO, KO, NKE, VZ, T
```

**Documented limitation:** the candidate pool this ranking draws from
(`UNIVERSE_POOL`, ~100 mega/large-cap symbols) is a static, hand-curated
list authored 2026-08 from general market-cap knowledge. It is not derived
from either period's own liquidity data, but its authorship date is after
the out-of-sample period, so a symbol's presence in the pool could in
principle reflect information not perfectly available in real time within
2025. This is the unavoidable universe-selection limitation the study goal
asked to be documented explicitly; the ranking *within* the pool is
genuinely computed from in-period data only.

## Configurations tested (fixed, no sweep)

| Config | Gap | RVOL |
|---|---|---|
| A (baseline) | 1.00% | 3.00x |
| B | 0.75% | 1.50x |
| C | 0.50% | 1.50x |
| D | 0.75% | 1.25x |
| E (research leader) | 0.50% | 1.25x |

Everything else held canonical: 5-minute opening range, SPY>VWAP,
completed-bar breakout, long-only, canonical entry window, unmodified
shadow-execution simulator/exit rules.

## Results

| Config | Candidate Days | Rate | Trades | Win Rate | Expectancy | PF | Max DD |
|---|---|---|---|---|---|---|---|
| A | 25/252 | 9.92% | 5 | 20.0% | -3.407 | 0.031 | 17.58 |
| B | 83/252 | 32.94% | 34 | 47.1% | 0.053 | 1.026 | 19.08 |
| C | 94/252 | 37.30% | 38 | 44.7% | -0.013 | 0.993 | 20.08 |
| D | 93/252 | 36.90% | 43 | 44.2% | -0.410 | 0.822 | 26.60 |
| E | 110/252 | 43.65% | 50 | 42.0% | -0.428 | 0.799 | 31.74 |

Candidate-day rate scales with relaxation exactly as in the discovery
period (A lowest, E highest). Trade quality does **not**: only B shows a
(marginal) positive edge out-of-sample. A has too few closed trades for
any verdict; C, D, and E all show expectancy and profit factor at or below
breakeven.

## Candidate multiplicity distribution

| Config | 0 | 1 | 2 | 3+ |
|---|---|---|---|---|
| A | 227 | 20 | 5 | 0 |
| B | 169 | 54 | 22 | 7 |
| C | 158 | 57 | 26 | 11 |
| D | 159 | 55 | 20 | 18 |
| E | 142 | 65 | 19 | 26 |

## One-trade-per-day vs all-candidates

Current ORB-LIVE v1 is bounded around one entry attempt per day.
`ONE_TRADE_PER_DAY` selects the first valid qualifying breakout
chronologically each day (by decision timestamp, never by later P&L) --
the smallest deterministic, decision-time-only rule. `ALL_CANDIDATES` is
**research-only and is not current production behavior**; it exists to
show how much additional opportunity/risk coexists on multi-candidate
days.

| Config | View | Trades | Win Rate | Expectancy | PF | Max DD |
|---|---|---|---|---|---|---|
| A | ALL_CANDIDATES | 5 | 20.0% | -3.851 | 0.034 | 15.95 |
| A | ONE_TRADE_PER_DAY | 4 | 25.0% | -3.851 | 0.034 | 15.95 |
| B | ALL_CANDIDATES | 34 | 47.1% | 0.053 | 1.026 | 19.08 |
| B | ONE_TRADE_PER_DAY | 26 | 53.8% | 0.363 | 1.183 | 14.04 |
| C | ALL_CANDIDATES | 38 | 44.7% | -0.013 | 0.993 | 20.08 |
| C | ONE_TRADE_PER_DAY | 27 | 51.9% | 0.205 | 1.108 | 17.93 |
| D | ALL_CANDIDATES | 43 | 44.2% | -0.410 | 0.822 | 26.60 |
| D | ONE_TRADE_PER_DAY | 27 | 48.1% | 0.336 | 1.180 | 19.65 |
| E | ALL_CANDIDATES | 50 | 42.0% | -0.428 | 0.799 | 31.74 |
| E | ONE_TRADE_PER_DAY | 28 | 46.4% | 0.185 | 1.103 | 20.71 |

Notable: for every configuration, the one-trade-per-day view outperforms
the all-candidates view -- restricting to the first daily breakout
improved win rate, expectancy, and profit factor across the board,
and B/C/D/E all cross PF>1 under this bound even where C/D/E do not
under ALL_CANDIDATES. This suggests later same-day candidates carry
disproportionately weaker quality, not that "more trades" would have
helped -- worth flagging for future candidate-ranking research, not
acted on here.

## Discovery period vs out-of-sample

| | Discovery (90 sessions, 2026-04-07..08-13) | Out-of-sample (252 sessions, 2025-04-03..2026-04-06) | Delta |
|---|---|---|---|
| **C (0.50%/1.50x)** candidate-day rate | 44.44% | 37.30% | -7.14 pp |
| C win rate | 57.9% | 44.7% | -13.2 pp |
| C expectancy/trade | 1.042 | -0.013 | -1.055 |
| C profit factor | 1.789 | 0.993 | -0.795 |
| **E (0.50%/1.25x)** candidate-day rate | 52.22% | 43.65% | -8.57 pp |
| E win rate | 54.7% | 42.0% | -12.7 pp |
| E expectancy/trade | 1.529 | -0.428 | -1.957 |
| E profit factor | 2.270 | 0.799 | -1.471 |
| E closed trades | 75 | 50 | -25 |

Measured honestly: both configurations degrade substantially out-of-sample
-- expectancy flips from strongly positive to negative, profit factor
drops from well above 2 to below 1. This is the "measure degradation
honestly" outcome the study goal explicitly anticipated as a possibility.

## Robustness classification

Criteria fixed in `src/orb_out_of_sample_validation_research.py::classify_robustness`
before any out-of-sample number was computed: INSUFFICIENT_EVIDENCE if
coverage isn't VALID or fewer than 20 closed trades exist; otherwise
NOT_ROBUST if expectancy <= 0 or profit_factor <= 1 (quality breaking is
disqualifying on its own); ROBUST requires positive expectancy, PF>1,
candidate_day_rate >= 25%, top-3 concentration <= 75%, and out-of-sample
frequency retaining >= 50% of the discovery-period rate; otherwise MIXED.

- **0.50% / 1.25x (E): NOT_ROBUST.** Expectancy and profit factor both
  fail (expectancy -0.428, PF 0.799) despite frequency (43.65%) and
  concentration (top-3 41.6%) both being fine.
- **0.50% / 1.50x (C): NOT_ROBUST.** Same failure mode: expectancy -0.013,
  PF 0.993 -- essentially breakeven-to-negative, despite adequate
  frequency and concentration.
- 0.75%/1.50x (B): **ROBUST** -- the only configuration clearing all five
  checks, though marginally (expectancy 0.053, PF 1.026).
- 0.75%/1.25x (D): NOT_ROBUST (expectancy -0.410, PF 0.822).
- 1.00%/3.00x (A): INSUFFICIENT_EVIDENCE (5 closed trades, below the
  20-trade floor).

## Decision

**RESEARCH_CONFIGURATION_NOT_ROBUST**

Both configurations of primary interest -- C (0.50%/1.50x) and E
(0.50%/1.25x) -- classify NOT_ROBUST out-of-sample: positive expectancy
and profit factor above 1 in the 90-session discovery period did not
generalize to an independent 252-session period. This does not authorize
any production threshold change, and none is made here.

If a threshold pair is to proceed toward production consideration at all,
B (0.75%/1.50x) is the only one that cleared every robustness check here,
though marginally -- it would itself need further validation (e.g. a
second independent out-of-sample window) before any production-change
review, not a promotion on this evidence alone.

## Best configuration (research heuristic, not a recommendation)

B (0.75%/1.50x) is the only configuration with a positive, above-1
profit factor out-of-sample. It is not "the winner" of a sweep -- it is
the one surviving configuration among five pre-registered candidates.

## Reproducing

```
python scripts/research_orb_out_of_sample_validation.py --sessions 252 --cache-only
```

Structured outputs: `artifacts/research-out-of-sample-validation/results/orb_out_of_sample_validation_study.json`
(full detail: per-config rows, trade quality, one-trade-per-day vs
all-candidates, discovery comparison, robustness) and
`orb_out_of_sample_validation_matrix.csv` (compact five-row summary).
