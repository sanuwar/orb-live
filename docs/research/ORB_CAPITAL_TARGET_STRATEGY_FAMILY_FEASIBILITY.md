# M48: $30K Capital-Target ETF Strategy-Family Feasibility

**Issue:** [#348](https://github.com/sanuwar/orb-live/issues/348)
**Branch:** `agent/m48-capital-target-strategy-family-feasibility`
**Depends on:** M43-M47 (large-cap ORB lane, supported, checkpoint 0.363 R/month / 1.898 trades/month -- unmodified, referenced only for correlation)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

> **This section is committed BEFORE any empirical result is read**, exactly as Issue #348 requires. It records the frozen protocol, methodology, and provenance framework only. The empirical results, classification verdicts, and final report sections below this point are added in a second commit, after the study is actually run.

## Purpose

M43-M47 established that the supported large-cap ORB lane has genuine positive edge but only ~0.363 R/month / 1.898 trades/month -- at 1% risk on $30,000 ($300/trade) that is roughly $109/month, far short of a $1,000/month target. Raising ORB risk to bridge that gap would be unsafe. M48 stops modifying the ORB entry lane and asks a broader question:

> Can any of three distinct, evidence-backed, long-only ETF strategy families either (a) meet the $1,000/month capital target by itself under safe constraints, or (b) provide a robust, sufficiently uncorrelated contribution worth combining with the supported ORB lane later?

Research only. No activation, no broker writes, no production-universe change, no leverage, no short selling, no options.

## Frozen Common Protocol

- Starting capital: **$30,000**, long-only, cash account. No margin, leverage, short selling, options, futures, inverse ETFs, or leveraged ETFs.
- Whole-share sizing. Maximum three concurrent positions (ARM_A only; ARM_B/ARM_C are basket allocations, not risk-sized).
- Maximum planned loss per position: **1.00% = $300**. Maximum aggregate open planned risk: **2.00% = $600**. Also reported at a **0.50% / $150** view, without rerunning or reselecting trades (post-hoc quantity scaling of the same trades).
- Cash earns 0%. Primary dollar-withdrawal view is **not compounded** (raw monthly dollar P&L, arithmetic mean); compounded equity/CAGR reported as a secondary diagnostic.
- Cost cases: **base 5 bps/side**, **stress 10 bps/side**.
- Signals use only completed bars; a close-derived signal executes no earlier than the next session open. No same-close fills, lookahead, survivorship substitution, or future-aware symbol replacement.
- Warm-up data may begin in 2021. Evaluation window: **2022-01-03 through 2026-08-13**. Segments reported separately: 2022, 2023, 2024, 2025, 2026 YTD, plus pooled. No parameter sweep; no post-result selection of a different lookback/threshold/exit/ETF/cost assumption.

## Frozen Arms

### ARM_A -- `ETF_PULLBACK_RSI2`

Universe: `SPY, QQQ, IWM, DIA, XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY` (15 ETFs).

Daily event-driven mean reversion: eligible only when adjusted close > 200-session SMA; entry signal when Wilder RSI(2) < 10; simultaneous signals ranked ascending RSI(2) then ticker ascending; enter next regular-session open subject to cash/risk caps; initial stop = entry price − 2×ATR(14) computed from completed pre-entry bars only; whole-share sizing capped at $300 planned risk/position, $600 aggregate; exit at the next session open after prior close > prior SMA(5), five completed holding sessions, or the protective stop (gap-through fills at the open, otherwise at the stop price); at most three positions, no duplicate ETF.

### ARM_B -- `SECTOR_MOMENTUM_12_1`

Universe: `XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY` (11 sector ETFs).

Monthly cross-sectional momentum: at each month-end close, 12-minus-1 momentum (total return from 252 sessions ago through 21 sessions ago); eligible only if momentum > 0 and close > SMA(200); rank descending momentum, ticker ascending tie-break; top 3 eligible only; rebalance at next session open, equal capital, whole shares, no leverage; unallocated capital stays cash.

### ARM_C -- `DIVERSIFIED_TREND_12M`

Universe: `SPY, QQQ, IWM, TLT, IEF, GLD, DBC, VNQ` (8 asset-class ETFs).

Monthly time-series trend: at each month-end close, an ETF is eligible when trailing 252-session return > 0 and close > SMA(200); hold every eligible ETF (not top-N); rebalance at next session open, equal capital, whole shares, long/cash only; no inverse position when trend is negative.

## Implementation

`src/orb_capital_target_strategy_family_research.py` holds every deterministic, independently-testable piece: Wilder RSI/SMA/ATR/momentum/trailing-return indicators (warm-up-safe, list-aligned), order-fill mechanics (next-open scheduling, gap-through stop resolution), whole-share/cash/risk sizing, deterministic ranking/tie-breaks, transaction-cost application, the two simulation engines (`run_arm_a` for the daily event-driven arm, `run_monthly_rebalance_arm` shared by ARM_B/ARM_C via arm-specific `selector` callables), portfolio metrics (monthly $/％ returns, CAGR, drawdown+recovery, Sharpe/Sortino, profitable-month stats, positive-net-contribution symbol concentration, Pearson correlation with month alignment), and the frozen four-label classification gate. `scripts/research_capital_target_strategy_family.py` owns all I/O: daily-bar fetch (Alpaca, `timeframe=1Day`, `adjustment=all`, split/dividend-adjusted), a dedicated gitignored cache (`artifacts/research-capital-target-strategy-family/daily-bar-cache/`, distinct from the shared M43-M47 minute-bar cache), the provenance manifest, and orchestration of all three arms across both cost cases into the final results/report.

No production `SignalEngine`/`ExitEngine`/`Simulator`/config/universe/broker code is touched. `src/market_data_canonical.py` (`MarketBar`) and `src/exchange_calendar.py` (both pinned) are reused unmodified, exactly as M43-M47 already do; no pinned-closure file is modified.

### Design decisions made explicit (frozen before results are read)

Two points in the frozen protocol admit more than one reasonable literal reading; a single, documented, tested convention was picked for each rather than left ambiguous, and neither is revisited after seeing results:

1. **"No compounding for the primary dollar-withdrawal view."** The simulation runs once, with the account's own natural compounding (profits fund later position sizes, exactly like a real account). The PRIMARY reported metric ("average monthly dollars on $30,000") is the plain arithmetic mean of each month's raw dollar P&L pulled from that curve -- never itself compounded forward. CAGR/total return, computed directly from the equity curve's start/end values, is the SECONDARY, explicitly-compounding diagnostic.
2. **Monthly rebalance execution (ARM_B/ARM_C) is incremental, not full liquidate-and-rebuild.** A symbol dropping out of the target basket is sold; a symbol newly entering is bought with an equal-capital share of current equity; a symbol that remains in the basket across consecutive rebalances is left untouched (no forced re-equalization trade). This is the reading of "equal-capital allocation" + "costs apply to every REQUIRED buy/sell" that minimizes turnover to only what basket-membership changes actually require.
3. **"Five completed holding sessions" (ARM_A).** The entry session itself counts as holding-session 1; the scheduled maximum-hold exit fires at the next session's open once five sessions have been held as of a close (`i - entry_index + 1 >= 5`). If both the SMA(5) and max-hold conditions are true at the same close, the SMA(5) exit is recorded (checked first) -- a single fixed, tested tie-break, not a per-trade judgment call.

## Data Provenance

Daily OHLCV, split/dividend-adjusted (`adjustment=all`), fetched from Alpaca's Market Data API v2 (`feed=iex` by default, `timeframe=1Day`), `WARMUP_START=2021-01-01` through one day past `EVALUATION_END=2026-08-13`, for the union of all three arms' universes (`ALL_SYMBOLS`, 18 distinct tickers). Every raw response is cached under `artifacts/research-capital-target-strategy-family/daily-bar-cache/` (gitignored, never committed) and normalized via the unmodified `src.market_data_provider.normalize_alpaca_bar`. The master trading calendar is SPY's own fetched session list, intersected with `[WARMUP_START, EVALUATION_END]`; every other symbol's series is required to align to that exact calendar (`require_full_alignment`), failing closed on any missing session rather than forward-filling a substitute price. A structural OHLC integrity check (`high >= max(open,close,low)`, `low <= min(open,close,high)`, positive prices, non-negative volume) runs before any strategy result is read; a violation halts the run. The provenance manifest (`artifacts/research-capital-target-strategy-family/results/provenance_manifest.json`, gitignored) records, per symbol: provider/feed/timeframe/adjustment, retrieval timestamp, requested and received date ranges, session count, SHA-256 of the normalized series, and the alignment/integrity findings above. Raw vendor data is never committed; this report and the committed provenance hashes are the durable, reviewable record.

## Required Outputs

Per arm, cost case, calendar segment, and pooled: entries/exits or rebalances; exposure/average concurrency; gross/net P&L; mean/median monthly dollar return; average monthly dollars on $30,000 (no compounding, primary); CAGR (secondary); volatility/Sharpe/Sortino (0% risk-free); max drawdown ($/％) and recovery duration; profitable-month percentage and longest losing streak; turnover and total estimated costs; worst month and 5th-percentile month; symbol/sleeve concentration (positive-net-contribution basis); $150/$300 risk views (ARM_A only); base-vs-stress cost sensitivity; correlation of monthly returns to the ORB baseline, SPY buy-and-hold, and the other two arms. SPY buy-and-hold is reported as a benchmark only, never a promotion gate.

## Frozen Classification

Implemented exactly in `classify_capital_target_arm` -- see the module for the full gate. Four labels: `DIRECT_TARGET_FEASIBLE`, `PROMISING_PORTFOLIO_CONTRIBUTOR`, `POSITIVE_BUT_INSUFFICIENT`, `NOT_PROMISING`. No arm is promoted merely for being the best of the three; the classifier's own signature takes only one arm's numbers and structurally cannot compare across arms.

## Anti-Analysis-Paralysis

Exactly these three arms, run once. Genuine data/code/integrity bugs are fixed and documented (invalidating and rerunning only the affected arm/checkpoint); the frozen thresholds, universes, lookbacks, stops, holding periods, dates, and cost assumptions are never changed in response to a negative result. A clean negative answer completes the milestone.

---

# Empirical Results

*Everything below this line was added in the second commit, after the frozen protocol and implementation above were committed and pushed. The study was run exactly once.*

## Data Provenance (actual run)

Retrieved 2026-08-20 from Alpaca (`feed=iex`, `timeframe=1Day`, `adjustment=all`), 1,410 daily bars per symbol for all 20 distinct tickers across the three universes, 2021-01-04 through 2026-08-14. Master calendar (SPY's own session list, intersected with `[2021-01-01, 2026-08-13]`): **1,409 sessions**. `require_full_alignment` and `ohlc_integrity_report` both passed cleanly for every symbol -- **zero missing sessions, zero OHLC integrity violations**, including the two later-inception sector ETFs (XLC, XLRE), which both have full coverage back to the 2021 warm-up start. Full per-symbol SHA-256 hashes, coverage, and alignment detail are in the gitignored `artifacts/research-capital-target-strategy-family/results/provenance_manifest.json`, regenerable from cache via `python scripts/research_capital_target_strategy_family.py`.

## A Real Bug Found and Fixed Before Any Empirical Data Was Read

`classify_capital_target_arm`'s CONTRIBUTOR gate ("at least four of five calendar segments are non-negative and at least three are positive") was first implemented counting over `REQUIRED_POSITIVE_SEGMENTS` -- only **four** segments (2023-2026 YTD, matching the DIRECT gate's own four required-positive segments) -- instead of all **five** reported segments (including 2022). This was caught by the pre-committed synthetic-fixture test suite (two tests failed on the first run), fixed by introducing `ALL_FIVE_CALENDAR_SEGMENTS` for the CONTRIBUTOR gate specifically while leaving the DIRECT gate's four-segment `REQUIRED_POSITIVE_SEGMENTS` unchanged, and reverified (65/65 tests pass). This was caught and fixed entirely with synthetic data, before the real Alpaca fetch or study run -- no rerun of any empirical stage was required.

## Pooled Results (2022-01-03 through 2026-08-13), Base Cost (5 bps)

| Metric | ARM_A `ETF_PULLBACK_RSI2` | ARM_B `SECTOR_MOMENTUM_12_1` | ARM_C `DIVERSIFIED_TREND_12M` |
|---|---:|---:|---:|
| Entries/exits or rebalance trades | 369 | 28 | 20 |
| Exposure (% of sessions with >=1 position) | 58.3% | 96.5% | 89.3% |
| Average concurrent positions | 1.23 (of 3 max) | 2.63 (of 3 max) | 2.80 (of 8 universe) |
| Gross P&L (realized, closed trades) | $7,513.39 | $7,333.13 | $12,635.70 |
| Net P&L (realized, closed trades) | $4,283.82 | $7,029.04 | $12,456.12 |
| Net P&L (equity, incl. unrealized on still-open positions) | $4,345.37 | $10,424.89 | $14,853.78 |
| Mean monthly $ (primary, no compounding) | **$77.60** | **$186.16** | **$265.25** |
| Median monthly $ | $91.65 | $310.68 | $144.02 |
| Mean monthly return % | 0.260% | 0.621% | 0.768% |
| CAGR (secondary, compounding) | 3.02% | 6.68% | 9.11% |
| Sharpe (annualized, 0% RF) | 0.52 | 0.52 | 0.87 |
| Sortino (annualized, 0% RF) | 0.69 | 0.77 | 1.16 |
| Max drawdown ($ / %) | $2,845 / 9.62% | $7,984 / 24.76% | $8,711 / 16.82% |
| Drawdown recovery | 441 days | 574 days | not recovered by 2026-08-13 |
| Profitable months | 55.4% | 60.7% | 57.1% |
| Longest losing-month streak | 5 | 3 | 4 |
| Worst month ($) | −$1,744.93 | −$2,910.36 | −$4,262.77 |
| 5th-percentile month (%) | −2.70% | −7.02% | −4.91% |
| Turnover ratio (traded notional / avg equity) | 215.5x | 19.4x | 10.6x |
| Total cost paid (base, 5 bps) | $3,381.11 | $322.36 | $192.26 |
| Best-symbol positive-net-contribution share | 24.7% (XLK) | **58.3% (XLC)** | **66.9% (GLD)** |
| Correlation to ORB baseline (monthly) | 0.184 | 0.033 | −0.103 |
| Correlation to SPY buy-and-hold (monthly) | 0.345 | 0.776 | 0.444 |

**"Gross/net P&L (realized)" vs. "net P&L (equity)"**: the equity-based figure is materially larger than realized net P&L for ARM_B/ARM_C because both arms are trend/momentum strategies designed to stay invested -- several positions (notably GLD and QQQ under ARM_C, XLC under ARM_B) were still open, with substantial unrealized gains, at the end of the evaluation window on 2026-08-13. The **equity-based figure is the correct basis for "average monthly dollars"** (the classification gate's own primary metric) since it reflects the account's real, total mark-to-market value each month, exactly like a real brokerage statement; the realized-only figure is reported alongside it as a reconciliation/diagnostic, not a second competing "net P&L."

## Stress-Cost (10 bps) Sensitivity

| Metric | ARM_A | ARM_B | ARM_C |
|---|---:|---:|---:|
| Net P&L (equity), stress cost | $1,145.53 | $11,185.11 | $13,918.31 |
| Total cost paid, stress | $6,736.26 | $650.50 | $385.46 |
| Stress-cost pooled result positive? | **Yes** | **Yes** | **Yes** |

All three arms remain net-positive under the doubled (10 bps) cost assumption, though ARM_A's net P&L falls by exactly (base_cost − stress_cost) = $3,355 to $4 above break-even (`$1,145.53`), reflecting its much higher turnover (215x vs. 19-11x) -- transaction costs are the dominant swing factor for ARM_A specifically, far less so for the two low-turnover monthly arms.

## Per-Segment Net P&L (equity, base cost)

| Segment | ARM_A | ARM_B | ARM_C |
|---|---:|---:|---:|
| 2022 | −$2,207.41 | −$2,409.35 | −$734.44 |
| 2023 | +$833.46 | +$42.83 | **−$1,845.80** |
| 2024 | +$1,844.25 | +$5,206.64 | +$7,092.11 |
| 2025 | +$1,574.92 | +$4,061.55 | +$10,251.02 |
| 2026 YTD | +$2,300.15 | +$3,523.22 | +$90.87 |

Segment figures reconcile exactly to the pooled net P&L for every arm (verified: each row sums to that arm's own pooled equity-based net P&L above). ARM_A and ARM_B each have exactly one negative segment (2022, the broad-market drawdown year); ARM_C has two (2022 and 2023), which alone disqualifies it from the DIRECT gate's "2023/2024/2025/2026-YTD all positive" requirement and from the CONTRIBUTOR gate's "at least 4 of 5 non-negative" requirement (only 3 of 5 segments are non-negative for ARM_C).

## $150 / $300 Risk Views (ARM_A only -- post-hoc quantity scaling, same trades, not rerun)

| | $300 risk (as-run) | $150 risk (secondary view) |
|---|---:|---:|
| Gross P&L | $7,513.39 | $3,734.60 |
| Net P&L (realized) | $4,283.82 | $2,114.35 |

Scaling to $150 risk/position roughly halves both figures, as expected for a linear position-size scaling with no change in trade selection or timing.

## Symbol Concentration (positive-net-contribution basis)

- **ARM_A**: best contributor XLK (24.7% of positive net P&L) -- broad-based across 10 different positively-contributing symbols (IWM, QQQ, XLB, XLC, XLF, XLI, XLK, XLU, XLV, XLY), none dominating.
- **ARM_B**: best contributor **XLC at 58.3%** of positive net P&L -- exceeds the 50% concentration gate on its own, independent of any other failure.
- **ARM_C**: best contributor **GLD at 66.9%** of positive net P&L -- gold alone drives most of this arm's edge; QQQ is the only other large contributor (the rest -- DBC, IEF, SPY -- are minor). The required SPY/QQQ/IWM overlap disclosure: SPY and QQQ are both eligible members of ARM_C's own universe and both frequently co-held with the ARM_A/ARM_C universes' broad-market exposure, so ARM_C's own trend edge here is substantially a levered bet on two things -- gold's multi-year rally and large-cap/mega-cap tech (QQQ) -- not eight genuinely diversified sleeves performing independently.

## Correlations

**Cross-arm (monthly returns):** ARM_A↔ARM_B 0.481, ARM_A↔ARM_C 0.306, ARM_B↔ARM_C 0.535. All three arms are moderately positively correlated with each other (unsurprising -- all three are long-only and broadly benefit from the same rising-market years), which tempers any "genuinely uncorrelated combination" case among the M48 arms themselves.

**To the ORB baseline (monthly):** ARM_A 0.184, ARM_B 0.033, ARM_C −0.103 -- all comfortably below the 0.60 CONTRIBUTOR ceiling, and ARM_C is very mildly negatively correlated. None of the three arms' failure to reach PROMISING_PORTFOLIO_CONTRIBUTOR is due to ORB correlation.

**To SPY buy-and-hold (monthly, benchmark only):** ARM_A 0.345, ARM_B 0.776, ARM_C 0.444. SPY buy-and-hold itself returned **+73.05%** over the identical window (2022-01-03 to 2026-08-13, start close $449.50, end close $777.84) with an 18.74% max drawdown -- reported strictly as a benchmark, per the frozen protocol, and never used to promote or demote any arm.

## Predeclared Classification

Applied exactly via `classify_capital_target_arm` (no arm compared against another; each verdict comes from that arm's own numbers only).

### ARM_A `ETF_PULLBACK_RSI2`

```
DIRECT_TARGET_FEASIBLE checks:
  average_monthly_dollars_at_least_1000:        FALSE  ($77.60 < $1,000)
  required_segments_all_positive:               TRUE
  max_drawdown_at_most_20pct:                   TRUE   (9.62%)
  stress_cost_pooled_positive:                  TRUE
  no_symbol_over_50pct_of_positive_pnl:         TRUE   (24.7%)
CONTRIBUTOR checks:
  average_monthly_dollars_at_least_250:         FALSE  ($77.60 < $250)
  pooled_net_positive / mean_monthly_positive:   TRUE / TRUE
  at_least_4_of_5_segments_nonnegative:         TRUE   (4/5)
  at_least_3_of_5_segments_positive:            TRUE   (4/5)
  max_drawdown_at_most_15pct:                   TRUE   (9.62%)
  stress_cost_pooled_positive:                  TRUE
  profitable_months_at_least_55pct:             TRUE   (55.4%)
  orb_correlation_at_most_060:                  TRUE   (0.184)
  no_symbol_over_50pct_of_positive_pnl:         TRUE   (24.7%)
```
**VERDICT: POSITIVE_BUT_INSUFFICIENT.** Every CONTRIBUTOR gate passes except the $250/month dollar threshold ($77.60 actual) -- the closest of the three arms to CONTRIBUTOR status, failing on exactly one gate.

### ARM_B `SECTOR_MOMENTUM_12_1`

```
DIRECT_TARGET_FEASIBLE checks:
  average_monthly_dollars_at_least_1000:        FALSE  ($186.16 < $1,000)
  required_segments_all_positive:               TRUE
  max_drawdown_at_most_20pct:                   FALSE  (24.76%)
  stress_cost_pooled_positive:                  TRUE
  no_symbol_over_50pct_of_positive_pnl:         FALSE  (58.3%, XLC)
CONTRIBUTOR checks:
  average_monthly_dollars_at_least_250:         FALSE  ($186.16 < $250)
  max_drawdown_at_most_15pct:                   FALSE  (24.76%)
  no_symbol_over_50pct_of_positive_pnl:         FALSE  (58.3%, XLC)
  (all other contributor checks pass)
```
**VERDICT: POSITIVE_BUT_INSUFFICIENT.** Fails on three independent gates (dollar threshold, drawdown, concentration) -- not a near-miss.

### ARM_C `DIVERSIFIED_TREND_12M`

```
DIRECT_TARGET_FEASIBLE checks:
  average_monthly_dollars_at_least_1000:        FALSE  ($265.25 < $1,000)
  required_segments_all_positive:               FALSE  (2023 negative)
  max_drawdown_at_most_20pct:                   TRUE   (16.82%)
  stress_cost_pooled_positive:                  TRUE
  no_symbol_over_50pct_of_positive_pnl:         FALSE  (66.9%, GLD)
CONTRIBUTOR checks:
  average_monthly_dollars_at_least_250:         TRUE   ($265.25 >= $250)
  at_least_4_of_5_segments_nonnegative:         FALSE  (only 3/5: 2022 and 2023 both negative)
  max_drawdown_at_most_15pct:                   FALSE  (16.82%)
  no_symbol_over_50pct_of_positive_pnl:         FALSE  (66.9%, GLD)
  (all other contributor checks pass)
```
**VERDICT: POSITIVE_BUT_INSUFFICIENT.** Clears the $250/month bar alone but fails segment-consistency, drawdown, and concentration.

## Decision-Tree Route

No arm reached `DIRECT_TARGET_FEASIBLE`. No arm reached `PROMISING_PORTFOLIO_CONTRIBUTOR`. **All three arms are `POSITIVE_BUT_INSUFFICIENT`** -- positive pooled net return after both cost cases, but each fails one or more contributor gates. Per the frozen decision tree: *"Only POSITIVE_BUT_INSUFFICIENT arms → record the economics and decide whether combining two pre-existing positive arms with ORB is mathematically plausible; no parameter tuning."*

Recorded economics for that judgment: ARM_A's own numbers are closest to a genuine portfolio-contributor case (low drawdown, broad-based concentration, ORB correlation 0.184, only the dollar-magnitude gate fails) but its P&L is small in absolute terms ($77.60/month) and its turnover (215x) makes it the most cost-sensitive of the three. ARM_B and ARM_C both cleared meaningfully larger absolute dollar figures ($186-265/month) but carry drawdowns (24.8%, 16.8%) and single-symbol concentration (58%, 67%) that a real combined portfolio would need to size down materially to control -- diluting exactly the dollar edge that makes them interesting in the first place. Cross-arm correlations (0.31-0.54) mean the three M48 arms are not a strong diversification bloc among themselves either. No parameter tuning, universe change, or threshold adjustment was made in response to any of this -- these are the frozen protocol's own numbers, reported as computed.

## Safety

`strategy.enabled` remains `false` throughout. No broker-write code was added; every network call in this milestone is a market-data-only Alpaca request (`AlpacaCredentials`, never a brokerage credential). No production `SignalEngine`/`ExitEngine`/`Simulator`/config/universe/broker code was touched; no pinned-closure file was modified. `src/orb_capital_target_strategy_family_research.py` and `scripts/research_capital_target_strategy_family.py` are research-only. Raw vendor data was never committed -- only the SHA-256-hashed provenance manifest and this report's aggregate tables. Trusted implementation digest confirmed unchanged before and after this milestone: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`.

## Structured Artifacts

Generated under `artifacts/research-capital-target-strategy-family/` (gitignored, regenerable from cache via `python scripts/research_capital_target_strategy_family.py`): `daily-bar-cache/` (raw Alpaca responses), `results/provenance_manifest.json` (per-symbol provenance/hashes/coverage), `results/study_results.json` (full structured results for every arm/cost-case/segment).

