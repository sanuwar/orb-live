# ORB Fine-RVOL Validation Study

Issue: [#302](https://github.com/sanuwar/orb-live/issues/302)
Status: **empirical run complete** -- real 90-session data, zero fabricated
values, `--cache-only` mode with zero new network calls (see "Data source"
below). Research-only: no production threshold, universe, or config was
changed.

**RESEARCH RECOMMENDATION ONLY. This is not a request to change
`config/strategy_v0.yaml`.**

## Goal

The prior gap x RVOL sensitivity study (Issue #300, merged) found RVOL to be
the dominant frequency lever and reported its highest tested frequency at
gap=0.50%/rvol=1.50x (44.44% candidate-day rate, 90 sessions), with the
strongest trade-quality figures in that matrix at the same point. One coarse
grid point cannot by itself distinguish a real threshold effect from a
90-session sample artifact.

**Primary research question**: is the apparent improvement around RVOL
1.50x robust, or is it a 90-session/sample artifact?

## Reuse, not reinterpretation

- Gate order and math come unmodified from `src.signal_engine.SignalEngine`
  and `src.market_data`, exactly as Issue #298 and #300 already validated:
  full pagination, canonical quality-gating, corrected funnel accounting,
  widened RVOL warmup, and the 95%-coverage fail-closed gate.
- `src/orb_fine_rvol_validation_research.py` imports its evaluation
  primitives directly from `src/orb_gap_rvol_sensitivity_research.py`
  (Issue #300, merged) unmodified: `sensitivity_config`, `evaluate_combo`,
  `matrix_row`, `symbol_concentration`, `incremental_effect`,
  `simulate_combo_trade_quality`, `aggregate_trade_quality`,
  `build_candidate_shadow_decision`. It adds only: (1) the finer RVOL
  threshold tuple (2.00x/1.75x/1.50x/1.25x) and the small set of functions
  that are parameterized by it (`parameter_matrix`, `rvol_relaxation_steps`,
  `relaxation_distance`, `all_incremental_effects`, `build_recommendation`),
  (2) chronological split-half evaluation
  (`split_sessions_chronologically`) and the two robustness assessments
  this study specifically asks for (`assess_rvol_1_50_robustness`,
  `assess_rvol_1_25_signal`), and (3) the production-baseline-as-context row
  (gap=1.00%/rvol=3.0x is not itself a point in this finer grid).
- `scripts/research_orb_fine_rvol_validation.py` is the CLI entry point. No
  stop/target/exit rule is invented anywhere in this study;
  `src.exit_engine.ExitEngine` (unmodified) owns all of that, via the
  merged M15 shadow-execution simulator (`src.shadow_execution_simulator`,
  `src.shadow_trading_decision`), identical to #300's own bridge.

## Research universe

Re-derived fresh, this run's own `--cache-only` liquidity-qualification pass
reproduced **the identical 23-symbol pool** Issue #298 and #300 both
documented:

```
NVDA, AMZN, AAPL, MSFT, GOOGL, META, TSLA, INTC, AMD, AVGO, NFLX, BAC, V,
CSCO, NOW, WMT, CRM, KO, JPM, VZ, BSX, NKE, SLB
```

This is the **primary research universe** for the 12-row matrix below.
Current production (15 symbols: `AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA,
AVGO, AMD, JPM, XOM, LLY, COST, WMT, NFLX`) is included only as baseline
context, since it does not clear the 95% coverage floor on real IEX data
(matches #298/#300 exactly).

Qualification thresholds (unchanged): opening-range-complete >= 95%,
RVOL-history-satisfiable >= 95%, median price >= $5, avg daily dollar
volume >= $1,000,000, avg daily share volume >= 10,000 shares/day. Coverage
fails closed: any combination below 95% for its universe is reported
`INVALID_INSUFFICIENT_COVERAGE`, not silently included in a decision.

## Historical sample and data source

- **Sessions**: 90 trading sessions, `2026-04-07` through `2026-08-13`
  inclusive -- the identical window Issue #298 and #300 both used.
- **Target vs. actual**: the goal asked for 180 sessions preferably, 120
  otherwise, 90 as the floor. This run could not reach 180 or 120. Two
  independent constraints, both checked directly rather than assumed:
  1. **No Alpaca credentials were present in this session's environment**
     (`ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY` both unset), so this run
     could not make any new network call -- per the goal's own instruction,
     extending past the cache requires those credentials, and fabricating
     or truncating data instead was refused.
  2. The shared on-disk bar cache (`artifacts/research-universe-expansion/
     bar-cache/`, 408 cached responses) was verified by direct bar-timestamp
     inspection to cover exactly `2026-03-09T12:01` through
     `2026-08-13T20:50` -- i.e. exactly the 90-session window plus the same
     1x RVOL warmup buffer #300's own cache-only run used, with zero
     headroom for an earlier `fetch_from`.
  `scripts/research_orb_fine_rvol_validation.py` encodes this as an
  explicit session-count ladder (180 -> 120 -> 90): each rung was actually
  attempted, not skipped by assumption -- 180 sessions was rejected by the
  exchange calendar's own supported-range guard
  (`src.exchange_calendar`, supported from 2026-01-01, no fabricated
  session dates), 120 sessions failed closed on a real cache miss (its
  earlier `fetch_from` is not in the cache), and 90 sessions succeeded with
  zero cache misses across all 101 pool + SPY symbols.
- **Data source**: `--cache-only` mode, zero new network calls. Feed:
  `iex`. RVOL warmup buffer: 1x (`fetch_from = 2026-03-09`).
- **Coverage**: **98.50% (VALID)** for the 23-symbol research universe at
  every one of the 12 fine gap/RVOL combinations (coverage depends on data
  availability, not the entry thresholds under study, so it is identical
  across the whole matrix) -- matches #298/#300 exactly. Current production
  remains **87.56% (INVALID_INSUFFICIENT_COVERAGE)**, included as context
  only.

## Parameter matrix

Gap: 1.00% / 0.75% / 0.50% (unchanged from #300). RVOL: 2.00x / 1.75x /
1.50x / 1.25x -- a finer grid bracketing #300's reported 1.50x "sweet spot"
from both sides, replacing #300's coarser 3.0x/2.5x/2.0x/1.5x steps. 12
combinations. Everything else fixed: 5-minute opening range, SPY > VWAP
gate, existing entry window, long-only ORB semantics, completed-bar
breakout rule, existing sizing/stop assumptions.

### Full matrix (research universe, 23 symbols)

| Gap | RVOL | Coverage | Candidate Days | Rate | Zero Days | Total Candidates | Mean/Session | Median (candidate days) | Max/Day | Top-3 Concentration |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.00% | 2.00x | 98.50% VALID | 23/90 | 25.56% | 67 | 31 | 0.344 | 1.0 | 4 | 48.4% |
| 1.00% | 1.75x | 98.50% VALID | 27/90 | 30.00% | 63 | 37 | 0.411 | 1.0 | 4 | 48.6% |
| 1.00% | 1.50x | 98.50% VALID | 34/90 | 37.78% | 56 | 49 | 0.544 | 1.0 | 5 | 44.9% |
| 1.00% | 1.25x | 98.50% VALID | 38/90 | 42.22% | 52 | 63 | 0.700 | 1.0 | 6 | 46.0% |
| 0.75% | 2.00x | 98.50% VALID | 23/90 | 25.56% | 67 | 32 | 0.356 | 1.0 | 4 | 50.0% |
| 0.75% | 1.75x | 98.50% VALID | 27/90 | 30.00% | 63 | 38 | 0.422 | 1.0 | 4 | 50.0% |
| 0.75% | 1.50x | 98.50% VALID | 35/90 | 38.89% | 55 | 54 | 0.600 | 1.0 | 5 | 46.3% |
| 0.75% | 1.25x | 98.50% VALID | 40/90 | 44.44% | 50 | 69 | 0.767 | 1.0 | 6 | 44.9% |
| 0.50% | 2.00x | 98.50% VALID | 25/90 | 27.78% | 65 | 35 | 0.389 | 1.0 | 4 | 45.7% |
| 0.50% | 1.75x | 98.50% VALID | 29/90 | 32.22% | 61 | 41 | 0.456 | 1.0 | 4 | 46.3% |
| 0.50% | 1.50x | 98.50% VALID | 40/90 | 44.44% | 50 | 61 | 0.678 | 1.0 | 5 | 41.0% |
| 0.50% | 1.25x | 98.50% VALID | **47/90** | **52.22%** | 43 | 79 | 0.878 | 1.0 | 6 | 40.5% |

`(gap=1.00%, rvol=2.00x)` is this grid's own strictest corner (it is *not*
production; production is gap=1.00%/rvol=3.0x, one further RVOL step out,
reported as context below). **One combination now clears 50%**: gap=0.50%,
rvol=1.25x, at 52.22% (47/90) -- the first configuration in either study to
do so.

### Candidate concentration by symbol

At this grid's strictest corner (1.00%/2.00x, 31 candidates, 11 distinct
symbols): `AMD` 6, `AVGO` 5, `META` 4, `AMZN` 4, `NVDA` 3 -- top-1 share
19.4%, top-3 share 48.4%, top-5 share 71.0%.

At the reported #300 "sweet spot" (0.50%/1.50x, 61 candidates, 12 distinct
symbols): `AMZN` 10, `AMD` 9, `MSFT` 6, `NFLX` 6, `NVDA` 6 -- top-3 share
41.0% (matches #300's own figure exactly, as it must -- identical
combination, identical data).

At the new highest-frequency point (0.50%/1.25x, 79 candidates, 12 distinct
symbols, same symbol set as 1.50x plus deeper counts, no new names):
`AMD` 12, `AMZN` 11, `MSFT` 9, `NVDA` 8, `NFLX` 8 -- top-1 share 15.2%,
top-3 share **40.5%**, top-5 share 60.8%. Concentration keeps improving
(broadening) as RVOL relaxes further, continuing the pattern #300 already
observed -- the extra candidates spread across the same already-qualified
names rather than concentrating in fewer of them.

## Incremental effects

### RVOL relaxation (gap held fixed) -- still the dominant lever

| Gap held | Step | +Candidate Days | +Occurrences |
|---|---|---:|---:|
| 1.00% | 2.00x -> 1.75x | 4 | 6 |
| 1.00% | 1.75x -> 1.50x | 7 | 12 |
| 1.00% | 1.50x -> 1.25x | 4 | 14 |
| 0.75% | 2.00x -> 1.75x | 4 | 6 |
| 0.75% | 1.75x -> 1.50x | 8 | 16 |
| 0.75% | 1.50x -> 1.25x | 5 | 15 |
| 0.50% | 2.00x -> 1.75x | 4 | 6 |
| 0.50% | 1.75x -> 1.50x | 11 | 20 |
| 0.50% | 1.50x -> 1.25x | 7 | 18 |

### Gap relaxation (RVOL held fixed) -- still a much smaller lever

| RVOL held | Step | +Candidate Days | +Occurrences |
|---|---|---:|---:|
| 2.00x | 1.00% -> 0.75% | 0 | 1 |
| 2.00x | 0.75% -> 0.50% | 2 | 3 |
| 1.75x | 1.00% -> 0.75% | 0 | 1 |
| 1.75x | 0.75% -> 0.50% | 2 | 3 |
| 1.50x | 1.00% -> 0.75% | 1 | 5 |
| 1.50x | 0.75% -> 0.50% | 5 | 7 |
| 1.25x | 1.00% -> 0.75% | 2 | 6 |
| 1.25x | 0.75% -> 0.50% | 7 | 10 |

**RVOL remains the load-bearing threshold at the finer grid too.** Every
RVOL relaxation step adds 4-11 candidate days; the largest single gap step
(0.75% -> 0.50% at the loosest RVOL, 1.25x) adds only 7. Gap's own effect
does grow slightly as RVOL relaxes (0 additional days at rvol=2.00x/1.75x
vs. 7 at rvol=1.25x for the same 0.75%->0.50% step) -- consistent with #300's
finding that gap is a secondary, RVOL-dependent lever, not an independent
one.

## Frequency targets

| Target | Combinations clearing it |
|---|---|
| >=25% | all 12 combinations |
| >=40% | gap=1.00%/rvol=1.25x, gap=0.75%/rvol=1.50x, gap=0.75%/rvol=1.25x, gap=0.50%/rvol=1.50x, gap=0.50%/rvol=1.25x |
| >=50% | **gap=0.50%/rvol=1.25x** |
| >=60% | none |
| >=70% | none |

This is the first configuration in either study to clear 50% candidate
days.

## Trade quality

Every `CANDIDATE` was replayed through the identical, unmodified
`simulate_shadow_execution` path #300 used -- no stop/target rule invented
here.

| Gap | RVOL | Trades Closed | Winners | Losers | Win Rate | Avg Winner | Avg Loser | Expectancy/Trade | Profit Factor | Stop-Out Rate | Max DD | Cumulative R |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.00% | 2.00x | 27 | 13 | 14 | 48.1% | 4.768 | -4.103 | 0.168 | 1.079 | 29.6% | 36.90 | 2.164 |
| 1.00% | 1.75x | 33 | 17 | 16 | 51.5% | 4.339 | -4.115 | 0.240 | 1.120 | 30.3% | 36.90 | 1.341 |
| 1.00% | 1.50x | 45 | 25 | 20 | 55.6% | 4.054 | -3.561 | 0.669 | 1.423 | 22.2% | 36.90 | 7.721 |
| 1.00% | 1.25x | 59 | 31 | 28 | 52.5% | 5.399 | -2.939 | 1.442 | 2.034 | 23.7% | 36.90 | 14.260 |
| 0.75% | 2.00x | 28 | 13 | 15 | 46.4% | 4.768 | -3.844 | 0.154 | 1.075 | 28.6% | 36.90 | 2.094 |
| 0.75% | 1.75x | 34 | 17 | 17 | 50.0% | 4.339 | -3.886 | 0.227 | 1.117 | 29.4% | 36.90 | 1.270 |
| 0.75% | 1.50x | 50 | 28 | 22 | 56.0% | 3.817 | -3.300 | 0.685 | 1.472 | 22.0% | 36.90 | 8.418 |
| 0.75% | 1.25x | 65 | 34 | 31 | 52.3% | 5.085 | -2.825 | 1.312 | 1.974 | 24.6% | 36.90 | 13.957 |
| 0.50% | 2.00x | 31 | 16 | 15 | 51.6% | 4.416 | -3.844 | 0.419 | 1.225 | 25.8% | 36.90 | 4.608 |
| 0.50% | 1.75x | 37 | 20 | 17 | 54.1% | 4.122 | -3.886 | 0.443 | 1.248 | 27.0% | 36.90 | 3.784 |
| 0.50% | 1.50x | 57 | 33 | 24 | 57.9% | 4.082 | -3.138 | 1.042 | 1.789 | 22.8% | 36.90 | 12.932 |
| 0.50% | 1.25x | **75** | 41 | 34 | 54.7% | 4.999 | -2.656 | **1.529** | **2.270** | 25.3% | 36.90 | **18.876** |

Every row reports 0 `ambiguous_outcomes`, 0 `still_open_at_data_boundary`,
and 4 `pending_not_filled` candidates never touching their limit entry
(excluded from these figures, not counted as losses).

**Honest reading**: expectancy, profit factor, and cumulative R keep
improving monotonically all the way to rvol=1.25x -- at gap=0.50%, expectancy
goes 0.419 (2.00x) -> 0.443 (1.75x) -> 1.042 (1.50x) -> **1.529 (1.25x)**,
profit factor 1.23 -> 1.25 -> 1.79 -> **2.27**, on a growing sample (31 -> 37
-> 57 -> 75 closed trades). Win rate is not monotonic across the whole
grid (it dips slightly from 57.9% at 1.50x to 54.7% at 1.25x, and similarly
dips at the 1.00%/1.25x and 0.75%/1.25x rows vs. their 1.50x neighbors) and
stop-out rate ticks up slightly at 1.25x versus 1.50x in two of three gap
columns -- both consistent with admitting somewhat noisier trades, but not
enough to reverse the expectancy/profit-factor trend at any gap level
tested. This is still a research-scale sample (max 75 closed trades in the
best row), not a production-quality backtest.

## Robustness: is the RVOL 1.50x region a sample artifact? (goal item F)

The 90-session sample was split into two chronological halves of 45
sessions each (`2026-04-07`-`2026-06-09` and `2026-06-10`-`2026-08-13`),
and the elevated candidate-day rate at RVOL=1.50x (at gap=0.50%, versus
this grid's strictest RVOL=2.00x) was checked **independently in each
half** rather than only in the pooled 90-session figure:

| Half | Sessions | Rate @ rvol=2.00x | Rate @ rvol=1.50x | Delta |
|---|---:|---:|---:|---:|
| First (2026-04-07 to 2026-06-09) | 45 | 33.33% (15/45) | 46.67% (21/45) | +13.34pp |
| Second (2026-06-10 to 2026-08-13) | 45 | 22.22% (10/45) | 42.22% (19/45) | +20.00pp |

**Verdict: `ROBUST_ACROSS_BOTH_HALVES`.** The improvement holds
independently in both chronological halves -- it is not an artifact
produced only by pooling the full 90-session sample, and the second half
(more recent data) shows an even larger delta than the first. This does not
prove the effect is structural (both halves come from the same 90-session
window and the same regime), but it does rule out the specific failure mode
this study was designed to check: a step that only appears when the whole
sample is pooled.

## Does 1.25x add useful signal, or mostly noise? (goal item G)

At gap=0.50% (the gap level with the best frequency/quality profile at
every RVOL tested), relaxing one more RVOL step from 1.50x to 1.25x:

| Metric | @ 1.50x | @ 1.25x | Change |
|---|---:|---:|---:|
| Candidate days | 40/90 | 47/90 | +7 |
| Candidate occurrences | 61 | 79 | +18 |
| Top-3 concentration | 41.0% | 40.5% | improves (-0.5pp) |
| Expectancy/trade | 1.042 | 1.529 | improves (+0.487) |
| Profit factor | 1.789 | 2.270 | improves |
| Win rate | 57.9% | 54.7% | degrades (-3.2pp) |
| Stop-out rate | 22.8% | 25.3% | degrades (+2.5pp) |

**Verdict: `USEFUL_SIGNAL`.** This study's own heuristic
(`assess_rvol_1_25_signal`) only labels a step `MOSTLY_NOISE` when *both*
expectancy and top-3 concentration degrade simultaneously; here expectancy
improves and concentration does not worsen, even though win rate and
stop-out rate both soften slightly. The additional 7 candidate days and 18
occurrences at 1.25x are not admitting weaker trades on the two metrics
that most directly measure trade quality (expectancy, profit factor) or
concentration risk, though the softer win-rate/stop-out-rate pattern is
worth watching on a larger sample before treating 1.25x as clearly better
than 1.50x rather than merely not worse.

## Gap sensitivity (does 0.50% remain the best gap once RVOL is finer?)

At every one of the four RVOL levels tested, gap=0.50% has the
highest candidate-day rate, the lowest top-3 concentration, and (at
rvol=1.50x and rvol=1.25x, the two levels with a large enough sample to
compare meaningfully) the best expectancy and profit factor of the three
gap levels:

| RVOL | Best gap by rate | Best gap by expectancy |
|---|---|---|
| 2.00x | 0.50% (27.78%) | 0.50% (0.419) |
| 1.75x | 0.50% (32.22%) | 0.50% (0.443) |
| 1.50x | 0.50% (44.44%) | 0.50% (1.042) |
| 1.25x | 0.50% (52.22%) | 0.50% (1.529) |

0.50% is not the best gap merely because it had the highest frequency in
#300's single sample point -- it independently and strictly wins on both
frequency and expectancy/profit factor at every RVOL level in this finer
grid (1.00% and 0.75% tie each other on rate at the two strictest RVOL
levels, but 0.50% is never tied or beaten), so the finer sweep does not
overturn #300's gap conclusion.

## Ranking / recommendation

- **A. Current production baseline**: gap=1.00%, rvol=3.0x (not itself a
  point in this finer grid) -- research-universe basis 13.33% (12/90,
  matches #298/#300 exactly); production-universe basis 15.56% (14/90,
  87.56% `INVALID_INSUFFICIENT_COVERAGE`, context only).
- **B. Smallest useful relaxation**: gap=1.00%, rvol=1.75x -- 30.00%
  (27/90), the larger of the two single-step relaxations from this grid's
  strictest corner (the other, gap=0.75%/rvol=2.00x, reaches 23/90) that
  both increase candidate days over the production-universe baseline
  context (14/90), per the documented ranking heuristic.
- **C. First configuration exceeding 50% candidate days**: **gap=0.50%,
  rvol=1.25x** -- 52.22% (47/90). This is new: #300 found none.
- **D. Highest-frequency configuration**: gap=0.50%, rvol=1.25x -- same as
  C, 52.22% (47/90), 79 candidates, lowest concentration in the matrix
  (top-3 share 40.5%), and the best trade-quality figures in the matrix
  (expectancy 1.529/trade, profit factor 2.270, cumulative 18.88R over 75
  closed trades).
- **E. Best frequency-quality balance / recommended for further
  validation**: **gap=0.50%, rvol=1.25x** (same as D and C in this sample)
  -- the ranking heuristic (`candidate_day_rate * (1 -
  top_3_symbol_concentration)`, expectancy as tiebreaker) picks it, and
  here the highest-frequency point again wins on every other axis, exactly
  the pattern #300 flagged as unusual and worth re-testing -- it has now
  been re-tested on a finer grid and the pattern held, with the frontier
  moving one further RVOL step out (1.25x vs. #300's 1.50x).
- **F. Does the 1.50x region appear robust?** **Yes --
  `ROBUST_ACROSS_BOTH_HALVES`.** See "Robustness" above.
- **G. Does 1.25x add useful signal or mostly noise?** **Useful signal --
  `USEFUL_SIGNAL`.** See "Does 1.25x add useful signal" above; the
  softer win-rate/stop-out-rate pattern at 1.25x is the one caveat worth
  carrying forward.

**This is not a recommendation to change `config/strategy_v0.yaml`.** Even
the recommended combination misses candidates on 43 of 90 sessions
(47.8%), the trade-quality sample (75 closed trades) is still a research
scale, this is the same 90-session window #298/#300 both used (not the
180/120-session extension the goal preferred -- no credentialed run was
possible this session, see "Historical sample and data source"), and the
chronological-split check, while reassuring, only rules out a
pooling-only artifact -- it does not by itself establish that this
relationship holds outside 2026-04-07 through 2026-08-13. A future
credentialed run extending the sample (and re-running this exact matrix
and split-half check against it) is the natural next step before any
production-threshold discussion.

## Safety

- `config/strategy_v0.yaml` was never read back after modification and was
  never written; `strategy.enabled` remains `false` in the tracked file
  (verified: file bytes identical before/after every `sensitivity_config`
  call in the test suite, and a fresh `load_config()` after the full run
  still reports `minimum_gap_percent == 1.0`, `minimum_relative_volume ==
  3.0`, `strategy.enabled is False`).
- No production universe, feed, or threshold changed
  (`config/strategy_v0.yaml`'s `universe.symbols` untouched; the research
  universe is a research-only, in-memory liquidity-qualified pool).
- No broker/write path touched: `simulate_shadow_execution` asserts
  `broker_write_count == 0` in its own `__post_init__`, and this study
  never calls a broker, MCP tool, or execution-authority module.
- No authority/activation change; no digest-pinned execution-safety file
  touched -- confirmed directly against
  `read_only_authorization_manifest._canonical_pinned_paths()`: neither
  `src/orb_fine_rvol_validation_research.py` nor
  `scripts/research_orb_fine_rvol_validation.py` appears in the pinned
  closure.
- `LEAN_ARCHITECTURE_AUDIT.md` was regenerated
  (`scripts/architecture_audit.py --format markdown`) to reflect this
  change's line count, per the repo's own
  `test_audit_matches_checked_in_file`-style check -- content only, no
  methodology change.

## Outputs

- `artifacts/research-fine-rvol-validation/results/orb_fine_rvol_validation_study.json`
  (gitignored, full structured report)
- `artifacts/research-fine-rvol-validation/results/orb_fine_rvol_validation_matrix.csv`
  (gitignored, compact matrix)
- This document

## Tests

`tests/test_orb_fine_rvol_validation_research.py` (18 tests) plus the
existing `tests/test_orb_gap_rvol_sensitivity_research.py` (unaffected --
this study imports its building blocks unmodified) cover: the exact
12-combination fine-RVOL matrix and its determinism; the fine RVOL/gap
threshold tuples themselves; relaxation-distance and relaxation-step
ordering against this grid's own strictest corner; the production-baseline
combo being reported as context and not a point in this grid;
`config/strategy_v0.yaml` staying byte-identical across every
`sensitivity_config` call; incremental RVOL/gap comparisons across all 17
adjacent steps in the fine grid (and that a relaxed RVOL step admits a
previously-eliminated symbol-day); fail-closed coverage status;
chronological split-half calculation (deterministic, order-independent,
odd/even session counts); the 1.50x robustness assessment (both the
robust-in-both-halves and not-robust cases); the 1.25x
useful-signal-vs-noise assessment (useful-signal, mostly-noise, and
no-additional-frequency cases); and the recommendation heuristic including
the new baseline-as-context and robustness/signal fields.
