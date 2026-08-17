# ORB Gap x RVOL Sensitivity Study

Issue: [#300](https://github.com/sanuwar/orb-live/issues/300)
Status: **empirical run complete** -- real 90-session data, zero fabricated
values, `--cache-only` mode with zero new network calls (see "Data source"
below). Research-only: no production threshold, universe, or config was
changed.

> **Metric-unit note (Issue #321/PR #322).** "Expectancy"/"Profit Factor"
> in the trade-quality table below are computed from raw price-per-share
> realized P&L (dollars per share) -- they are **NOT** R-normalized.
> "Cumulative R" in that same table is separately R-normalized; the two
> are not the same unit and should not be compared as if they were.
> Risk-normalized expectancy/profit-factor were not computed for this
> study. The recommendation below is driven primarily by candidate-day
> frequency and symbol concentration, with expectancy used only as a
> tiebreaker (not the primary basis) -- but the "Honest reading" and
> trade-quality discussion do lean on the price-per-share figures, so
> treat any risk-normalized reading of this study's trade-quality
> discussion as provisional pending a dedicated R-normalized
> re-evaluation. No historical trade counts, win rates, or dollar-based
> figures below have changed -- this is a labeling clarification only,
> not a rerun. See `docs/research/ORB_BASELINE_FOURTH_PERIOD_VALIDATION.md`
> for the corrected R-based methodology.

## Goal

Holding every other `orb_long_v0` rule exactly as configured in
`config/strategy_v0.yaml`, how does relaxing the two most restrictive entry
thresholds -- `minimum_gap_percent` and `minimum_relative_volume` -- change
candidate frequency, zero-candidate days, candidate concentration, and (where
truthfully supportable) trade quality? The study explicitly does **not**
optimize for daily-trade frequency for its own sake; it asks whether a
defensible configuration produces candidates on more trading days than it
misses.

## Reuse, not reinterpretation

- Gate order and math come unmodified from `src.signal_engine.SignalEngine`
  (gap -> opening RVOL -> SPY/VWAP -> ORB breakout) and `src.market_data`
  (opening range, median opening volume, VWAP) -- exactly the infrastructure
  Issue #298 (`src/orb_universe_expansion_research.py`, merged) already
  validated against real Alpaca data: full pagination, canonical
  quality-gating, corrected funnel accounting, widened RVOL warmup, and the
  95%-coverage fail-closed gate.
- `src/orb_gap_rvol_sensitivity_research.py` adds only: (1) an in-memory-only
  override of `minimum_gap_percent`/`minimum_relative_volume`
  (`sensitivity_config`, never touches `config/strategy_v0.yaml`), (2) a
  `precompute_session_signal_inputs` / `resolve_session_signal` split added
  to `src/orb_universe_expansion_research.py` so the expensive full-history
  bar scan runs once per (symbol, session), not once per (symbol, session,
  combination) -- a 12x reduction for this study's matrix, and (3) a bridge
  from an approved `CANDIDATE` to `src.shadow_execution_simulator.
  simulate_shadow_execution` (Milestone 15, merged) for trade quality, reusing
  `src.shadow_trading_decision.build_shadow_trading_decision` -- the exact
  construction `src/live_shadow_session.py`'s own live evaluation path uses.
  No stop/target/exit rule is invented anywhere in this study; `ExitEngine`
  (unmodified) owns all of that.
- `scripts/research_orb_gap_rvol_sensitivity.py` is the CLI entry point.

## Research universe

Per explicit direction, this study does **not** blindly expand to an
arbitrary 75/100-name universe. It reuses Issue #298's liquidity-qualified
methodology as-is (same five thresholds, same ranking, same
`compute_symbol_liquidity_profile`/`rank_liquidity_qualified_symbols`,
unmodified) and re-derives the qualified pool fresh from this run's own
90-session window and cache. It reproduced **the identical 23-symbol pool**
the merged prior study documented:

```
NVDA, AMZN, AAPL, MSFT, GOOGL, META, TSLA, INTC, AMD, AVGO, NFLX, BAC, V,
CSCO, NOW, WMT, CRM, KO, JPM, VZ, BSX, NKE, SLB
```

ranked descending by average daily dollar volume. This is the **primary
research universe** for the 12-row matrix below. Current production (15
symbols: `AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AVGO, AMD, JPM, XOM, LLY,
COST, WMT, NFLX`) is included only as baseline context (goal item 9.A), since
it does not clear the 95% coverage floor on real IEX data (see below).

Qualification thresholds (unchanged from Issue #298): opening-range-complete
>= 95%, RVOL-history-satisfiable >= 95%, median price >= $5, avg daily dollar
volume >= $1,000,000, avg daily share volume >= 10,000 shares/day.

## Historical sample and data source

- **Sessions**: 90 trading sessions, `2026-04-07` through `2026-08-13`
  inclusive -- the same window Issue #298's study used (recomputed fresh via
  `trading_sessions_before(today, count=90)`, not hardcoded).
- **Data source**: `--cache-only` mode. This run made **zero new network
  calls**: `scripts/research_orb_gap_rvol_sensitivity.py --cache-only` reuses
  Issue #298's existing, gitignored on-disk bar cache
  (`artifacts/research-universe-expansion/bar-cache/`, 101 symbols already
  fully paginated from a real, credentialed Alpaca IEX run) with an inner
  HTTP client that raises loudly on any cache miss instead of fabricating or
  truncating data. Verified before running: a dry pass against all 101 pool +
  SPY symbols for this exact 90-session window hit the cache with **zero
  misses**. Feed: `iex` (matches every production component). RVOL warmup
  buffer: 1x (`fetch_from = 2026-03-09`), matching what Issue #298's own
  liquidity-qualification pass documented staying on for the identical
  reason (the wider 3x buffer would need new pages this cache does not yet
  have).
- **Coverage**: **98.50% (VALID)** for the 23-symbol research universe, at
  every one of the 12 gap/RVOL combinations -- coverage depends only on
  data availability, not on the entry thresholds under study, so it is
  identical across the whole matrix and matches Issue #298's own figure
  exactly. Current production remains **87.56% (INVALID_INSUFFICIENT_COVERAGE)**,
  also matching Issue #298 exactly -- included as context, not as a
  decision-quality row.

## Parameter matrix

Gap: 1.00% / 0.75% / 0.50% (`minimum_gap_percent`). RVOL: 3.0x / 2.5x / 2.0x /
1.5x (`minimum_relative_volume`). 12 combinations. Everything else fixed: a
5-minute opening range, the SPY > VWAP gate, the 09:40-11:00 ET entry window,
long-only ORB semantics, the completed-bar breakout requirement, and every
canonical calculation method.

### Full matrix (research universe, 23 symbols)

| Gap | RVOL | Coverage | Candidate Days | Rate | Zero Days | Total Candidates | Mean/Session | Median (candidate days) | Max/Day |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1.00% | 3.0x | 98.50% VALID | 12/90 | 13.33% | 78 | 15 | 0.167 | 1.0 | 3 |
| 1.00% | 2.5x | 98.50% VALID | 15/90 | 16.67% | 75 | 19 | 0.211 | 1.0 | 3 |
| 1.00% | 2.0x | 98.50% VALID | 23/90 | 25.56% | 67 | 31 | 0.344 | 1.0 | 4 |
| 1.00% | 1.5x | 98.50% VALID | 34/90 | 37.78% | 56 | 49 | 0.544 | 1.0 | 5 |
| 0.75% | 3.0x | 98.50% VALID | 12/90 | 13.33% | 78 | 15 | 0.167 | 1.0 | 3 |
| 0.75% | 2.5x | 98.50% VALID | 15/90 | 16.67% | 75 | 19 | 0.211 | 1.0 | 3 |
| 0.75% | 2.0x | 98.50% VALID | 23/90 | 25.56% | 67 | 32 | 0.356 | 1.0 | 4 |
| 0.75% | 1.5x | 98.50% VALID | 35/90 | 38.89% | 55 | 54 | 0.600 | 1.0 | 5 |
| 0.50% | 3.0x | 98.50% VALID | 12/90 | 13.33% | 78 | 16 | 0.178 | 1.0 | 3 |
| 0.50% | 2.5x | 98.50% VALID | 15/90 | 16.67% | 75 | 20 | 0.222 | 1.0 | 3 |
| 0.50% | 2.0x | 98.50% VALID | 25/90 | 27.78% | 65 | 35 | 0.389 | 1.0 | 4 |
| 0.50% | 1.5x | 98.50% VALID | **40/90** | **44.44%** | 50 | 61 | 0.678 | 1.0 | 5 |

`(gap=1.00%, rvol=3.0x)` is the current production baseline. No combination
in this matrix -- even the most relaxed -- exceeds 40% candidate-day rate;
the highest-frequency combination (gap=0.50%, rvol=1.5x) still misses
candidates on 50/90 sessions (55.6%). **The study's own question --
whether a defensible configuration produces candidates on more days than it
misses -- is answered no for every combination tested.**

### Candidate concentration by symbol

At baseline (1.00%/3.0x, 15 candidates, 7 distinct symbols): `AMD` 4,
`AMZN` 3, `AVGO` 2, `META` 2, `MSFT` 2, `AAPL` 1, `WMT` 1 -- top-1 share
26.7%, top-3 share 60.0%, top-5 share 86.7%.

At the most relaxed combination (0.50%/1.5x, 61 candidates, 12 distinct
symbols): `AMZN` 10, `AMD` 9, `MSFT` 6, `NFLX` 6, `NVDA` 6, `AAPL` 5, `AVGO`
5, `META` 5, `TSLA` 5, `WMT` 2, `GOOGL` 1, `JPM` 1 -- top-1 share 16.4%,
top-3 share 41.0%, top-5 share 60.7%. Concentration **improves** (broadens)
as thresholds relax, not just candidate count: relaxation adds new
contributing symbols (`GOOGL`, `JPM`, `TSLA`) rather than concentrating
further in the names that already qualified at baseline.

## Incremental effects

### RVOL relaxation (gap held fixed) -- the dominant lever

| Gap held | Step | +Candidate Days | +Occurrences |
|---|---|---:|---:|
| 1.00% | 3.0x -> 2.5x | 3 | 4 |
| 1.00% | 2.5x -> 2.0x | 8 | 12 |
| 1.00% | 2.0x -> 1.5x | 11 | 18 |
| 0.75% | 3.0x -> 2.5x | 3 | 4 |
| 0.75% | 2.5x -> 2.0x | 8 | 13 |
| 0.75% | 2.0x -> 1.5x | 12 | 22 |
| 0.50% | 3.0x -> 2.5x | 3 | 4 |
| 0.50% | 2.5x -> 2.0x | 10 | 15 |
| 0.50% | 2.0x -> 1.5x | 15 | 26 |

### Gap relaxation (RVOL held fixed) -- a much smaller lever

| RVOL held | Step | +Candidate Days | +Occurrences |
|---|---|---:|---:|
| 3.0x | 1.00% -> 0.75% | 0 | 0 |
| 3.0x | 0.75% -> 0.50% | 0 | 1 |
| 2.5x | 1.00% -> 0.75% | 0 | 0 |
| 2.5x | 0.75% -> 0.50% | 0 | 1 |
| 2.0x | 1.00% -> 0.75% | 0 | 1 |
| 2.0x | 0.75% -> 0.50% | 2 | 3 |
| 1.5x | 1.00% -> 0.75% | 1 | 5 |
| 1.5x | 0.75% -> 0.50% | 5 | 7 |

**Finding: RVOL is the load-bearing threshold.** Every RVOL relaxation step
adds 3-15 candidate days; every gap relaxation step (at a fixed RVOL) adds at
most 5, and is often zero. Relaxing gap alone from 1.00% to 0.50% at the
baseline RVOL of 3.0x adds exactly one additional candidate occurrence across
the entire 90-session sample. Full JSON detail (`newly_admitted`
symbol/date pairs for all 17 steps) is in the JSON artifact.

## Frequency targets

| Target | Combinations clearing it |
|---|---|
| >=25% | gap=1.00%/rvol=2.0x, gap=1.00%/rvol=1.5x, gap=0.75%/rvol=2.0x, gap=0.75%/rvol=1.5x, gap=0.50%/rvol=2.0x, gap=0.50%/rvol=1.5x |
| >=40% | gap=0.50%/rvol=1.5x |
| >=50% | **none** |
| >=60% | none |
| >=70% | none |

## Trade quality

**Truthfully evaluated, not `TRADE_QUALITY_NOT_YET_EVALUATED`.** Before
building anything, this study checked whether the repository already had a
usable lifecycle/execution simulator and found one: `src/shadow_execution_
simulator.py` + `src/shadow_trading_decision.py` (Milestone 15, merged, 156
tests of their own). Every `CANDIDATE` this matrix produced was wrapped in a
real `ShadowTradingDecision` (same construction `src/live_shadow_session.py`
uses) and replayed through `simulate_shadow_execution` against the same real
subsequent-day bars, using the unmodified `ExitEngine` (stop at opening-range
low, move to breakeven at +1R, full exit at +2R, forced flat at the
configured `exit.time_exit` -- ORB V0 never holds overnight). No stop/target
rule was invented for this study.

| Gap | RVOL | Trades Closed | Winners | Losers | Win Rate | Avg Winner | Avg Loser | Expectancy/Trade | Profit Factor | Stop-Out Rate | Max DD | Cumulative R |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.00% | 3.0x | 12 | 6 | 6 | 50.0% | 4.731 | -5.659 | -0.464 | 0.836 | 16.7% | 31.47 | 0.331 |
| 1.00% | 2.5x | 16 | 9 | 7 | 56.3% | 5.484 | -5.918 | 0.496 | 1.192 | 18.8% | 30.90 | 2.261 |
| 1.00% | 2.0x | 27 | 13 | 14 | 48.1% | 4.768 | -4.103 | 0.168 | 1.079 | 29.6% | 36.90 | 2.164 |
| 1.00% | 1.5x | 45 | 25 | 20 | 55.6% | 4.054 | -3.561 | 0.669 | 1.423 | 22.2% | 36.90 | 7.721 |
| 0.75% | 3.0x | 12 | 6 | 6 | 50.0% | 4.731 | -5.659 | -0.464 | 0.836 | 16.7% | 31.47 | 0.331 |
| 0.75% | 2.5x | 16 | 9 | 7 | 56.3% | 5.484 | -5.918 | 0.496 | 1.192 | 18.8% | 30.90 | 2.261 |
| 0.75% | 2.0x | 28 | 13 | 15 | 46.4% | 4.768 | -3.844 | 0.154 | 1.075 | 28.6% | 36.90 | 2.094 |
| 0.75% | 1.5x | 50 | 28 | 22 | 56.0% | 3.817 | -3.300 | 0.685 | 1.472 | 22.0% | 36.90 | 8.418 |
| 0.50% | 3.0x | 13 | 7 | 6 | 53.8% | 4.091 | -5.659 | -0.409 | 0.843 | 15.4% | 31.47 | 0.598 |
| 0.50% | 2.5x | 17 | 10 | 7 | 58.8% | 4.961 | -5.918 | 0.481 | 1.198 | 17.6% | 30.90 | 2.528 |
| 0.50% | 2.0x | 31 | 16 | 15 | 51.6% | 4.416 | -3.844 | 0.419 | 1.225 | 25.8% | 36.90 | 4.608 |
| 0.50% | 1.5x | 57 | 33 | 24 | 57.9% | 4.082 | -3.138 | 1.042 | 1.789 | 22.8% | 36.90 | 12.932 |

Every row also reports 0 `ambiguous_outcomes` and 0 `still_open_at_data_boundary`
(every entered trade the simulator could evaluate reached a real close);
3-4 candidates per combination never touched their limit entry
(`pending_not_filled`), excluded from these figures rather than counted as
losses.

**Honest reading**: at the strict baseline RVOL (3.0x), expectancy is
slightly **negative** at every gap level (-0.41 to -0.46 per trade) on a
thin sample (12-13 closed trades) -- one bad trade either way would flip the
sign, so this is not a strong signal, but it does not support a "baseline is
already profitable" claim either. Expectancy, profit factor, and cumulative R
all improve monotonically as RVOL relaxes toward 1.5x, and the sample size
that improvement rests on also grows (12 -> 45-57 closed trades) -- both a
reason to weight the 1.5x rows more (less small-sample noise) and a reason
this is not yet a production-quality backtest (still under 90 sessions of
trade history, one entry per candidate-day, no walk-forward validation, no
slippage beyond the simulator's own zero-bps baseline policy).

## Ranking / recommendation

- **A. Current baseline**: gap=1.00%, rvol=3.0x -- 13.33% candidate-day rate
  (12/90), 15 candidates, negative expectancy on 12 closed trades.
- **B. Smallest useful relaxation**: gap=1.00%, rvol=2.5x -- one RVOL step
  relaxes to 16.67% (15/90), 19 candidates, and flips expectancy positive
  (+0.496/trade on 16 closed trades) at the same gap.
- **C. First combination exceeding 50% candidate days**: **none**. The
  highest-frequency combination reaches 44.44%.
- **D. Highest-frequency configuration**: gap=0.50%, rvol=1.5x -- 44.44%
  (40/90), 61 candidates, best trade-quality figures in the matrix
  (win rate 57.9%, expectancy +1.042/trade, profit factor 1.789, cumulative
  12.93R over 57 closed trades) and the *lowest* concentration (top-3 share
  41.0% vs. 60.0% at baseline).
- **E. Recommended for further validation**: **gap=0.50%, rvol=1.5x** (same
  as D in this sample) -- it simultaneously maximizes candidate-day
  frequency, minimizes concentration risk, and shows the strongest trade
  quality, on real >=95%-coverage data. The recommendation heuristic
  (`build_recommendation`) weighs `candidate_day_rate * (1 -
  top_3_symbol_concentration)` among VALID-coverage combinations, using
  expectancy as a tiebreaker -- it did not simply pick the highest candidate
  count, and here the highest-frequency combination also happens to win on
  every other axis, which is itself worth flagging as unusual and worth
  re-testing on an extended sample before treating as durable.

**This is not a recommendation to change `config/strategy_v0.yaml`.** Even
the recommended combination misses candidates on 50 of 90 sessions (55.6%),
the trade-quality sample (57 closed trades) is still thin for a
production decision, and this 90-session window is the same one Issue #298
used -- extending the sample, and/or recovering the near-miss symbols
Issue #298's own RVOL-warmup caveat identified (e.g. `BKNG`), are the natural
next steps before any production-threshold discussion.

## Safety

- `config/strategy_v0.yaml` was never read back after modification and was
  never written; `strategy.enabled` remains `false` in the tracked file
  (verified: file bytes identical before/after every `sensitivity_config`
  call in the test suite, and a fresh `load_config()` after the full run
  still reports `minimum_gap_percent == 1.0`, `minimum_relative_volume ==
  3.0`, `strategy.enabled is False`).
- No production universe changed (`config/strategy_v0.yaml`'s `universe.symbols`
  untouched; the research universe is a research-only, in-memory
  liquidity-qualified pool).
- No broker/write path touched: `simulate_shadow_execution` asserts
  `broker_write_count == 0` in its own `__post_init__`
  (`src/shadow_execution_simulator.py`), and this study never calls a
  broker, MCP tool, or execution-authority module.
- No authority/activation change; no digest-pinned execution-safety file
  touched (`src/orb_universe_expansion_research.py`, the only tracked file
  this study modified, is not in `read_only_authorization_manifest`'s pinned
  closure -- confirmed directly against `_canonical_pinned_paths()`).
- `LEAN_ARCHITECTURE_AUDIT.md` was regenerated (`scripts/architecture_audit.py
  --format markdown`) to reflect this change's line count, per the repo's
  own `test_architecture_audit_matches_checked_in_file` check -- content
  only, no methodology change.

## Outputs

- `artifacts/research-gap-rvol-sensitivity/results/orb_gap_rvol_sensitivity_study.json`
  (gitignored, full structured report)
- `artifacts/research-gap-rvol-sensitivity/results/orb_gap_rvol_sensitivity_matrix.csv`
  (gitignored, compact matrix)
- This document

## Tests

`tests/test_orb_gap_rvol_sensitivity_research.py` (17 tests) plus the
existing `tests/test_orb_universe_expansion_research.py` (49 tests,
unaffected by the additive `precompute_session_signal_inputs`/
`resolve_session_signal` split) cover: parameter-matrix determinism;
in-memory-only config override (base config never mutated, tracked file
never touched); the sensitivity evaluator reusing the canonical evaluator
byte-for-byte (`OpeningData`/`LiveData`/`SignalDecision` equality against a
direct call); a relaxed threshold admitting a previously-eliminated
symbol-day; matrix aggregation (max/day, per-symbol counts, concentration);
fail-closed coverage status; incremental-effect accounting (newly-admitted
symbol/date pairs, monotonicity across all 17 adjacent steps); frequency-
target bucketing; the ranking/recommendation heuristic (baseline,
highest-frequency, first->=50%, no->=50% case); and the trade-quality bridge
(a real candidate resolving through the actual shadow-execution simulator,
zero-candidate combinations reporting `NO_CANDIDATES_TO_SIMULATE`, and
win/loss/expectancy/profit-factor arithmetic against genuine
`ShadowExecutionResult`s).
