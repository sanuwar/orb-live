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

*Empirical results, per-arm/per-segment metrics, classification verdicts, correlation findings, and the final decision-tree recommendation are added below in a second commit, after the study defined above is run exactly once against real data.*
