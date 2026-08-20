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

`src/orb_capital_target_strategy_family_research.py` holds every deterministic, independently-testable piece: Wilder RSI/SMA/ATR/momentum/trailing-return indicators (warm-up-safe, list-aligned), order-fill mechanics (next-open scheduling, gap-through stop resolution), whole-share/cash/risk sizing, deterministic ranking/tie-breaks, transaction-cost application, an explicit `Transaction` ledger populated by both engines, the two simulation engines (`run_arm_a` for the daily event-driven arm, `run_monthly_rebalance_arm` shared by ARM_B/ARM_C via arm-specific `selector` callables), portfolio metrics (fixed-notional and compounded monthly $ views, CAGR, drawdown+recovery, Sharpe/Sortino, profitable-month stats, positive-net-contribution symbol concentration, exposure/concurrency tracked directly during simulation, Pearson correlation with month alignment and zero-fill for sparse series), and the frozen four-label classification gate. `scripts/research_capital_target_strategy_family.py` owns all I/O: daily-bar fetch (Alpaca, `timeframe=1Day`, `adjustment=all`, split/dividend-adjusted, `feed=sip`), a feed-specific gitignored cache, the provenance manifest, the committed hashes document, and orchestration of all three arms across both cost cases into the final results/report.

No production `SignalEngine`/`ExitEngine`/`Simulator`/config/universe/broker code is touched. `src/market_data_canonical.py` (`MarketBar`) and `src/exchange_calendar.py` (both pinned) are reused unmodified, exactly as M43-M47 already do; no pinned-closure file is modified.

### Design decisions made explicit (frozen before results are read, two later corrected by independent review)

Several points in the frozen protocol admit more than one reasonable literal reading; a single, documented, tested convention was picked for each. Two of the original picks (1 and 2 below) were found by independent review to fail the protocol's own intent and were corrected mid-milestone -- documented here as CORRECTED, with the original (superseded) reading kept visible for the audit trail. None of these conventions were revisited a second time after that correction, and no protocol threshold/parameter was ever changed in response to a result.

1. **"No compounding for the primary dollar-withdrawal view" -- CORRECTED.** *Original (superseded) reading:* the simulation runs once with the account's own natural compounding, and the primary metric was the plain arithmetic mean of each month's raw dollar P&L pulled directly off that compounding curve. Independent review found this was still compounding in substance (later months operate on a changing, grown equity base) and therefore not a genuine fixed-$30,000 view. *Corrected reading, in effect for all reported results:* the simulation still runs once, naturally compounding (profits fund later position sizes, exactly like a real account) -- but the PRIMARY reported metric ("average monthly dollars on $30,000") is each month's own percentage return applied to the ORIGINAL FIXED $30,000 notional (`fixed_notional_monthly_dollar_pnl`), never to the account's own grown balance. Two consecutive +10% months therefore both contribute exactly $3,000 here, even though the same two months compound the account's own equity from $30,000 to $33,000 to $36,300 (a $3,300 second-month change) in the secondary, explicitly-compounding `monthly_dollar_pnl`/CAGR diagnostic.
2. **Monthly rebalance execution (ARM_B/ARM_C) -- CORRECTED.** *Original (superseded) reading:* incremental only -- a symbol dropping out of the target basket was sold, a symbol newly entering was bought with an equal-capital share of current equity, and a symbol remaining in the basket across consecutive rebalances was left completely untouched (no re-equalization trade), letting its weight drift indefinitely. Independent review found this was not a genuine "equal-capital rebalance" as the frozen protocol requires. *Corrected reading, in effect for all reported results:* every rebalance now (1) sells symbols leaving the basket, (2) computes each target symbol's whole-share equal-capital target quantity from equity marked at that session's open, (3) TRIMS any continuing holding above its target quantity, and (4) buys new positions / TOPS UP continuing holdings below target -- all in ticker-ascending, deterministic order, with costs charged on every actual trade (never on a holding already sitting at target). A top-up combines into one weighted-average-cost lot, keeping the original entry date for holding-session bookkeeping.
3. **"Five completed holding sessions" (ARM_A).** Unchanged. The entry session itself counts as holding-session 1; the scheduled maximum-hold exit fires at the next session's open once five sessions have been held as of a close (`i - entry_index + 1 >= 5`). If both the SMA(5) and max-hold conditions are true at the same close, the SMA(5) exit is recorded (checked first) -- a single fixed, tested tie-break, not a per-trade judgment call.
4. **ARM_A's protective stop is checked on every position still open after that session's entries, including one entered that same session (independent-review correction).** The stop is `entry_open - 2xATR(14)`, always strictly below the entry price, so a same-day gap-through is structurally impossible -- but an intraday touch after entry is not, and the original event order checked stops before processing that day's new entries, so a freshly-entered position could never stop out on its own entry day. Corrected event order: (1) scheduled exits at today's open, (2) new entries at today's open, (3) stop check against today's (open, low) for every position still open -- including one just entered in step 2, (4) close-derived signal scan and scheduled-exit flagging, (5) mark closing equity.
5. **A fixed evaluation-endpoint mark, separate from any real exit (independent-review correction).** Positions still open at the study's true fixed endpoint (2026-08-13) are, for net-P&L/concentration/turnover accounting only, marked at that date's close with a hypothetical liquidation cost applied (`EVALUATION_END_MARK`) -- never a real or simulated strategy exit, and never applied to an interim calendar-segment boundary that merely happens to have open positions mid-flight.
6. **ORB-correlation month alignment (independent-review correction).** The ORB baseline's own monthly R-sum series is sparse (most months have zero closed ORB trades); it is zero-filled across the complete evaluation-window month grid (`fill_missing_months_with_zero`) before correlating against any ETF arm's own (dense) monthly return series, so a no-trade ORB month contributes an explicit 0R rather than silently disappearing from the correlation's inner join.
7. **2021 warm-up data must never trade (independent-review Round 3, Finding 10, BLOCKER).** *Original (superseded) reading:* both simulation engines ran identically across the full `WARMUP_START` (2021-01-01) through `EVALUATION_END` range, so a qualifying 2021 signal opened, held, and closed a real position -- with real cash changes, costs, and P&L -- entirely inside what was supposed to be indicator-only warm-up. *Corrected reading, in effect for all reported results:* every session (including warm-up) still computes indicators and signals unconditionally, but `evaluation_start=2022-01-03` now gates only the EXECUTION step (fills, cost/cash changes, ledger rows, positions) in both engines -- a genuine 2021-12-31 close-derived signal still fills at the first eligible 2022-01-03 open exactly as the frozen protocol requires, but nothing before that date can trade, and a stale blocked warm-up signal can never leak forward (both engines fully recompute their own pending-fill state every session, regardless of whether the previous value ever executed).
8. **Fixed-endpoint marks must resolve by exact date, never a positional index (independent-review Round 3, Finding 11, BLOCKER).** *Original (superseded) reading:* `end_price_by_symbol` used each symbol's own `series.closes[-1]`. Because the cached daily series structurally extends one session past `EVALUATION_END` (through 2026-08-14, one day beyond the true 2026-08-13 endpoint), this silently substituted the WRONG session's close for every still-open position's endpoint mark -- a one-session lookahead reaching into net P&L, endpoint cost, concentration, turnover, reconciliation, stress results, and classification alike. *Corrected reading, in effect for all reported results:* every endpoint price is now resolved by `close_on_exact_date`, an exact-date lookup that fails closed (raises rather than substitutes) on a missing or duplicate date for 2026-08-13 specifically. A dedicated full-pipeline test drastically alters the 2026-08-14 bar and proves every reported metric, classification verdict, and ledger hash is unchanged by it.
9. **The daily position/equity ledger must record real per-position snapshots, not just aggregate equity and position count (independent-review Round 3, Finding 12).** The prior ledger hash covered only `(date, total_equity)` and `(date, position_count)` pairs -- two portfolios holding entirely different symbols/quantities but the same aggregate equity and count would hash identically, so the real ledger could not be independently reconstructed or verified. Corrected with a `PositionSnapshot` recorded once per open position per session (date, symbol, quantity, mark price, market value, planned risk), folded into the position/equity ledger hash; the strategy-transaction, endpoint-mark, and combined-accounting ledger hashes are now also reported separately (rather than one hash mixing real trades with the hypothetical endpoint mark), so the exact accounting basis behind every reported figure is independently reconstructable and verifiable.
10. **The committed hashes document must identify the implementation that actually generated the results, not a stale or in-progress commit (independent-review Round 3, Finding 13).** The prior `generation_git_head_sha` was captured from a working tree with uncommitted results-stage changes on top of it -- a commit reference that did not actually correspond to the code that produced the numbers. Corrected with a strict sequencing: implementation changes (Findings 10-15 and their tests) are committed and pushed FIRST, with no results/report/hashes-document change included; the complete study is then rerun from that exact, clean, already-pushed commit using the existing SIP cache; `generation_git_head_sha` and a `generation_working_tree_clean` flag (scoped to exactly the two source files being hashed, not the whole repository -- since the results/report files being regenerated are SUPPOSED to differ from what's currently committed) are captured directly from that run; results/report/hashes updates are committed separately, afterward. SHA-256 hashes of both source files (`src/orb_capital_target_strategy_family_research.py`, `scripts/research_capital_target_strategy_family.py`) are also recorded in the committed hashes document.
11. **Metrics/counts completeness (independent-review Round 3, Finding 14).** Annualized volatility and downside deviation (same population-stdev/sqrt(12) convention as Sharpe/Sortino) are now reported alongside them. The prior single `trade_or_rebalance_count` conflated closed lots (including partial TRIMs) with genuine rebalance events -- a single scheduled rebalance can close several lots, one per affected symbol. Corrected with `closed_lot_count` plus separately-reported `signal_count`, `scheduled_rebalance_count`, `executed_rebalance_count`, `buy_transaction_count`, and `sell_transaction_count`, all derived directly from the explicit ledgers (never re-labeling closed-lot count as a rebalance count), for both pooled and per-segment views. ARM_A's $150/$300 risk views are now accompanied by an explicit disclosure of what cannot be validly derived post-hoc (see "$150 / $300 Risk Views" below) rather than silently reporting only realized gross/net.
12. **Every window's opening equity must be its TRUE opening equity, not the first session's own closing equity (independent-review Round 4, Finding 16, BLOCKER).** *Original (superseded) reading:* `compute_arm_metrics` was SUPPOSED to prepend the true opening-equity anchor whenever the window's first equity mark did not already sit at `evaluation_start` -- but since both simulation engines mark equity once per session, at the CLOSE of that session, `window_equity[0][0] == evaluation_start` is always true, so that conditional prepend was never actually taken. Every pooled/segment window's "prior" baseline for monthly-return-derived metrics was therefore silently the first window session's OWN post-execution closing equity, not its true pre-session opening equity -- dropping that session's own P&L/costs out of the monthly return/dollar series, Sharpe/Sortino/volatility/downside-deviation, CAGR, drawdown, and profitable-month statistics, while the separately-computed equity-based net P&L already correctly included it. *Corrected reading, in effect for all reported results:* the true opening equity (the immediately preceding session's actual closing equity, or `starting_capital` for the pooled window's own true first session) is now unconditionally prepended, at the same date as the window's own first session, before any monthly-return-derived metric is computed.
13. **Downside deviation must use the frozen 0% MAR (minimum acceptable return) definition (independent-review Round 4, Finding 17, correction).** *Original (superseded) reading:* both Sortino's denominator and the standalone `annualized_downside_deviation_pct` used population standard deviation of ONLY the negative monthly observations -- dividing by the count of negative months only (excluding every non-negative month from the denominator entirely) and measuring spread around the negative subset's OWN mean rather than the 0% MAR. This silently returns exactly zero for two or more IDENTICAL negative months, since their spread around their own (negative) mean is zero, even though those months represent real, nonzero downside. *Corrected reading, in effect for all reported results:* `sqrt(mean(min(r-0,0)^2 over ALL months)) * sqrt(12)` -- every month contributes an explicit shortfall term (zero for a non-negative month), so the denominator is always the full month count, and repeated identical losses are correctly captured as nonzero downside risk.
14. **`signal_count` must never be populated from FILLED orders (independent-review Round 4, Finding 18, correction).** *Original (superseded) reading:* ARM_A's `signal_count` was populated from its real ENTRY transactions -- a fill count, not a genuine pre-fill RSI-signal count (a candidate scanned but never filled, e.g. because the 3-position cap was already reached, was never counted, so the field silently misrepresented "signals filtered by capital/slot availability" as if it were "every raw signal generated"). *Corrected reading, in effect for all reported results:* ARM_A now reports its fill count under the honestly-named `filled_entry_count`, with `signal_count` left `None` (no genuine pre-fill signal ledger currently exists for ARM_A -- never fabricated). ARM_B/ARM_C's `signal_count` was already a genuine pre-fill signal count (the month-end selector runs and records a target basket unconditionally, whether or not it later executes) and is unchanged; those two arms report `filled_entry_count` as `None` (no comparable per-fill "entry" concept for a basket rebalance).
15. **Segment-level outputs must exist for the stress cost case too, and must handle a position carrying across a segment boundary consistently (independent-review Round 4, Finding 19, correction).** *Original (superseded) reading:* calendar-segment metrics (used for the DIRECT/CONTRIBUTOR classification gates and the Per-Segment table) were computed only from the base-cost (5 bps) simulation -- Issue #348 requires outputs "per arm, cost case, calendar segment, and pooled," so the stress-cost case's own segment view was simply missing. Separately, a position still open at an INTERIM segment's own boundary contributed its full unrealized mark-to-market movement to that segment's equity-based net P&L (correctly), while contributing NOTHING to that segment's symbol-level concentration/contribution accounting (since only the TRUE final endpoint gets a hypothetical liquidation mark) -- an internal inconsistency between two views of the same segment. *Corrected reading, in effect for all reported results:* stress-cost segment metrics are now built from the stress simulation's own ledgers, alongside base-cost segment metrics (`results["arms"][arm]["stress_segments"]`, in addition to the existing `"segments"`). Both engines now also record each open position's FULL state at the end of every calendar segment encountered (`segment_end_open_positions`), and a segment window's carrying positions are folded into that segment's own concentration/contribution accounting wherever this can be resolved UNAMBIGUOUSLY from the existing ledgers -- a position untouched during the segment (pure mark-to-market since the last snapshot before it began) or one that entered fresh during the segment with no later topup/trim (mark-to-market since that one entry). A position topped up or trimmed while remaining open at the segment's own end is deliberately left out of this attribution (the shared, running weighted-average cost basis cannot be unambiguously split into "gained before this segment" vs. "gained during it" without per-lot cost history the current ledger does not track); any such gap remains fully visible via `compute_arm_metrics`'s own `reconciliation.residual` for that segment, never silently hidden. This changes internal per-segment structured output (`concentration`, `reconciliation`) but never the segment's own `net_pnl`/`net_pnl_marked` dollar figure, which was already correct and is unaffected.

## Data Provenance

Daily OHLCV, split/dividend-adjusted (`adjustment=all`), fetched from Alpaca's Market Data API v2. **`feed=sip`(the consolidated tape across all US exchanges) is the frozen M48 empirical feed** -- IEX is a single individual exchange whose OHLC can differ materially from the consolidated market, which matters directly here since ARM_A's entries/stops/exits are all driven by daily open/high/low/close; M46 hit the same IEX-completeness problem and switched to SIP, and M48 starts there (see "Corrections from Independent Review" below for the discovery). `WARMUP_START=2021-01-01` through one day past `EVALUATION_END=2026-08-13`, for the union of all three arms' universes (`ALL_SYMBOLS`, **20 distinct tickers**). Every raw response is cached under a feed-specific subdirectory (`artifacts/research-capital-target-strategy-family/daily-bar-cache/sip/`, gitignored, never committed) -- kept permanently separate from the original `daily-bar-cache/` IEX cache, which is left untouched as historical evidence, never read as if it were SIP data -- and normalized via the unmodified `src.market_data_provider.normalize_alpaca_bar`. The master trading calendar is SPY's own fetched session list, intersected with `[WARMUP_START, EVALUATION_END]`; every other symbol's series is required to align to that exact calendar (`require_full_alignment`), failing closed on any missing session rather than forward-filling a substitute price. A structural OHLC integrity check (`high >= max(open,close,low)`, `low <= min(open,close,high)`, positive prices, non-negative volume) runs before any strategy result is read; a violation halts the run. The provenance manifest (`artifacts/research-capital-target-strategy-family/results/provenance_manifest.json`, gitignored) records, per symbol: provider/feed/timeframe/adjustment, retrieval timestamp, requested and received date ranges, session count, SHA-256 of the normalized series, and the alignment/integrity findings above. Raw vendor data is never committed; a small **committed, non-vendor-data hashes file** (`docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json`) carries the durable, reviewable SHA-256 record -- exact feed/timeframe/adjustment parameters, all 20 normalized-series hashes, the master-calendar hash, every arm/cost-case transaction- and position/equity-ledger hash, the `study_results.json` hash, the exact evaluation window, and the generating implementation's git HEAD SHA.

## Required Outputs

Per arm, cost case, calendar segment, and pooled: entries/exits or rebalances; exposure/average concurrency; gross/net P&L; mean/median monthly dollar return; average monthly dollars on $30,000 (no compounding, primary); CAGR (secondary); volatility/Sharpe/Sortino (0% risk-free); max drawdown ($/％) and recovery duration; profitable-month percentage and longest losing streak; turnover and total estimated costs; worst month and 5th-percentile month; symbol/sleeve concentration (positive-net-contribution basis); $150/$300 risk views (ARM_A only); base-vs-stress cost sensitivity; correlation of monthly returns to the ORB baseline, SPY buy-and-hold, and the other two arms. SPY buy-and-hold is reported as a benchmark only, never a promotion gate.

## Frozen Classification

Implemented exactly in `classify_capital_target_arm` -- see the module for the full gate. Four labels: `DIRECT_TARGET_FEASIBLE`, `PROMISING_PORTFOLIO_CONTRIBUTOR`, `POSITIVE_BUT_INSUFFICIENT`, `NOT_PROMISING`. No arm is promoted merely for being the best of the three; the classifier's own signature takes only one arm's numbers and structurally cannot compare across arms.

## Anti-Analysis-Paralysis

Exactly these three arms, run once. Genuine data/code/integrity bugs are fixed and documented (invalidating and rerunning only the affected arm/checkpoint); the frozen thresholds, universes, lookbacks, stops, holding periods, dates, and cost assumptions are never changed in response to a negative result. A clean negative answer completes the milestone.

---

# Empirical Results

*Everything below this line reflects the THRICE-CORRECTED implementation. Round 2 addressed nine independent-review findings (the IEX-feed blocker, the compounding/rebalance/stop-timing/endpoint-accounting blockers, and reporting corrections). Round 3 addressed six further findings (10-15): two additional blockers -- 2021 warm-up data was silently trading, and the fixed-endpoint mark had a one-session lookahead -- plus ledger-completeness, provenance-chain, metrics/count-completeness, and decision-tree-completion corrections. Round 4 (this pass) addressed four further findings (16-19): one more blocker (a window's opening-equity anchor was never actually applied), a downside-deviation formula correction, a signal-count/fill-count mislabeling correction, and a required-output-completeness correction (missing stress-cost segment metrics, inconsistent segment-boundary carry accounting). All findings across all rounds are covered by new or updated tests, and the complete study was rerun after each round from a clean checkout of the exact commit implementing it, against the same SIP cache -- zero network calls since Round 2. The Round 1 (IEX, pre-correction) figures ($77.60 / $186.16 / $265.25 mean monthly dollars for ARM_A/B/C) and the Round 2 (SIP, Findings 1-9 only) figures ($75.23 / $196.72 / $177.26) are both SUPERSEDED and are not reported as headline numbers below; they are cited only where directly relevant to explaining what changed. The Round 3 figures ($78.53 / $196.72 / $177.26) are, for this specific dataset, numerically UNCHANGED by Round 4's corrections (see "Corrections From Independent Review -- Round 4" below for why) and remain the correct pooled dollar figures. `generation_git_head_sha` for this Round 4 result set: `f04b68bd46f6b2dc8a6773ebafcddd28b43dfee9` (verified clean at generation time).*

## Corrections From Independent Review

1. **BLOCKER -- IEX is not a consolidated market feed.** The first empirical pass used `feed=iex` (a single individual exchange) instead of `feed=sip` (the consolidated tape). Corrected: `feed=sip` is now the frozen M48 empirical feed, fetched into its own cache subdirectory (`daily-bar-cache/sip/`), with the original IEX cache preserved untouched as historical evidence. A direct comparison confirms genuinely different data was retrieved: matching-date closes are nearly identical (e.g. GLD 2023-10-23: IEX $182.98 vs. SIP $182.97), but volumes differ by roughly 10-100x (e.g. DIA same date: IEX 41,340 vs. SIP 3,898,317) -- exactly the expected single-venue-vs-consolidated-tape signature.
2. **BLOCKER -- the primary "no-compounding" dollar metric was actually compounded.** See "Design decisions" item 1 above. Corrected via `fixed_notional_monthly_dollar_pnl`; a dedicated test proves two identical +10% months now produce $3,000 and $3,000 in the primary view (vs. $3,000/$3,300 in the still-available secondary compounded view).
3. **BLOCKER -- the monthly "equal-capital rebalance" did not rebalance continuing holdings.** See "Design decisions" item 2 above. Corrected with trim/top-up mechanics; tests prove an overweight continuing holding is trimmed, an underweight one is topped up, continuing holdings finish within one share's value of the equal-capital target, and costs are charged on every such required trade.
4. **BLOCKER -- ARM_A's stop was not active on the entry day.** See "Design decisions" item 4 above. Corrected event order; a dedicated test forces a signal on day D, entry at D+1's open, and a stop breach on D+1's own low, confirming the trade exits same-day at the stop/gap price with both entry and exit costs charged and `holding_sessions=1`.
5. **BLOCKER -- open end-of-window positions were omitted from cost/concentration accounting.** See "Design decisions" item 5 above. Corrected with an explicit `Transaction` ledger for every buy/sell, a daily position/equity ledger, and `evaluation_end_mark_transactions` for the fixed endpoint only; concentration and net P&L now include each still-open position's own marked, cost-adjusted contribution, and turnover/segment costs are computed directly from the ledger (fixing a related bug where every segment had silently reported the WHOLE STUDY's cost total rather than its own).
6. **CORRECTION -- ORB correlation dropped zero-trade months.** See "Design decisions" item 6 above. Corrected via `fill_missing_months_with_zero`; the pooled ORB monthly R series now has exactly 56 entries (January 2022 through August 2026 inclusive), not just the sparse set of months with a closed ORB trade.
7. **CORRECTION -- required durable hashes were not committed.** The prior report pointed only at a gitignored manifest. Corrected: `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json` is now committed, containing the exact SIP feed/timeframe/adjustment parameters, all 20 normalized-series SHA-256 hashes, the master-calendar hash, a transaction-ledger and position/equity-ledger hash for every arm/cost-case combination, the `study_results.json` hash, the exact evaluation window, and the generating git HEAD SHA -- no raw vendor data, no secret.
8. **REPORTING -- metadata and count corrections.** The universe union is **20** distinct tickers, not 18 (fixed above). The PR title/body are updated to reflect completed (corrected) results and accurate draft status once this commit lands.

**A ninth issue was found and fixed during this same review pass, not originally flagged:** exposure/concurrency reconstruction from closed-trade date intervals double-counted a continuing holding that gets TRIMMED across several rebalances (each trim produces its own trade record sharing the position's ORIGINAL entry date), which was observed producing an average-concurrent-positions figure *larger than the arm's own universe size* for ARM_C. Corrected by tracking each session's actual open-position count directly inside both simulation engines (`daily_position_count`) rather than reconstructing it after the fact; a regression test asserts concurrency never exceeds universe size despite trims.

## Corrections From Independent Review -- Round 3 (Findings 10-15)

A second independent-review pass, after Round 2's figures were already reported, found two further blockers and four further corrections. See "Design decisions" items 7-11 above for the full technical description of each; summarized here against what actually changed in the empirical results:

10. **BLOCKER -- 2021 warm-up data was silently trading.** Both engines executed real fills, costs, and P&L on qualifying 2021 signals before the evaluation window even began. Corrected via `evaluation_start` execution gating (Design decisions item 7). This changes trade counts, dates, and dollar figures for every arm relative to Round 2 -- e.g. ARM_A's closed-lot count moves from 354 (Round 2, which conflated the count meaning too -- see Finding 14) to 353 real in-window lots.
11. **BLOCKER -- fixed-endpoint marks had a one-session lookahead.** `end_price_by_symbol` used a positional `closes[-1]`, which silently read 2026-08-14's close (one session past the true 2026-08-13 endpoint) for any position still open at the study's end. Corrected via exact-date resolution (Design decisions item 8); this affects every still-open position's endpoint mark and therefore net P&L (marked), endpoint cost, concentration, turnover, and classification.
12. **CORRECTION -- ledger completeness.** The position/equity ledger hash now covers real per-position daily snapshots, not just aggregate equity/count; the transaction-ledger hash is now reported as three separate hashes (strategy-only, endpoint-mark-only, combined) rather than one hash that silently excluded the endpoint mark (Design decisions item 9).
13. **CORRECTION -- provenance-chain integrity.** `generation_git_head_sha` in the committed hashes document previously referenced a commit that predated the very changes that produced the results it was attached to. Corrected with a strict implementation-commit-then-clean-rerun sequencing, a `generation_working_tree_clean` flag, and SHA-256 hashes of both source files (Design decisions item 10). This result set's `generation_git_head_sha` is `36f3a198438f99618ba6f18d38c11231aeac4b8e`.
14. **CORRECTION -- metrics/count completeness.** Annualized volatility and downside deviation are now reported; closed-lot count, signal count, scheduled/executed-rebalance counts, and buy/sell-transaction counts are now separately reported (never conflated), for pooled and segment views alike (Design decisions item 11). ARM_A's risk views now explicitly disclose what cannot be validly derived post-hoc rather than silently omitting it.
15. **DECISION-TREE COMPLETION -- combination-plausibility upper bound.** All three arms classify `POSITIVE_BUT_INSUFFICIENT` (unchanged from Round 2, though the underlying numbers moved -- see Classification below). Per the frozen decision tree, this requires computing a deliberately generous mathematical upper bound for combining ORB with the two highest standalone arms before considering any M49 portfolio-combination work. See "Decision-Tree Route" below for the computation and verdict.

## Corrections From Independent Review -- Round 4 (Findings 16-19)

A final independent-review pass found one further blocker and three further corrections. See "Design decisions" items 12-15 above for the full technical description of each.

16. **BLOCKER -- a window's opening-equity anchor was never actually applied.** `compute_arm_metrics` intended to prepend the true opening equity whenever a window's first equity mark did not already sit exactly at that window's own start date -- but since both engines mark equity once per session at that session's own close, the first window mark's date always equals the window's own start date, so the prepend was never taken (Design decisions item 12). Recomputed honestly for this dataset: **the fix changes nothing in the pooled headline dollar figures.** Directly confirmed by comparing this run's full structured output against the pre-fix Round 3 run: `mean_monthly_dollar_pnl_fixed_30k`, `gross_pnl`, `net_pnl_marked`, and `max_drawdown_dollars`/`_pct` are all bit-for-bit identical for all three arms. This is not a sign the fix is a no-op in general -- direct unit tests (a synthetic case where the first session closes $100 below the true $30,000 opening) prove the fix genuinely changes the computed monthly return/drawdown when the bug's precondition holds. For this specific real-SIP dataset, that precondition (a trade executing exactly on one of the five window-start anchor dates -- 2022-01-03 pooled/2022-segment start, and the 2023/2024/2025/2026 segment starts) simply never occurred: `annualized_downside_deviation_pct`/`sortino` DID change (Finding 17, below, is unconditional and always recomputes them), while every anchor-sensitive figure did not, which is only possible if none of those five specific dates carried a first-session P&L move for any arm. Sortino/downside-deviation and the new count/segment fields ARE genuinely different from Round 3 (see below); the pooled dollar/drawdown headline figures are not, and are reported as computed, not assumed.
17. **CORRECTION -- downside deviation now uses the frozen 0% MAR formula.** Sortino and `annualized_downside_deviation_pct` are recomputed via `sqrt(mean(min(r-0,0)^2 over ALL months))*sqrt(12)` (Design decisions item 13). This DOES change every arm's reported Sortino and downside-deviation figures -- see the Pooled Results table below.
18. **CORRECTION -- `signal_count` no longer mislabels ARM_A's fill count as a raw signal count.** ARM_A now reports `filled_entry_count=354`; `signal_count` is `None` for ARM_A (Design decisions item 14). ARM_B/ARM_C's `signal_count` (56 for both, a genuine month-end selector count) is unchanged; their `filled_entry_count` is `None`.
19. **CORRECTION -- stress-cost segment metrics, and segment-boundary carry consistency.** `results["arms"][arm]["stress_segments"]` is now populated alongside the existing base-cost `"segments"` for every arm (Design decisions item 15) -- see "Stress-Cost Segment Metrics" below. Segment-level concentration/contribution accounting now also folds in a carrying position's own unrealized movement wherever this is unambiguously resolvable from the existing ledgers; where it is not (a position topped up or trimmed while remaining open at a segment's own end), the gap is left visible via that segment's own `reconciliation.residual` rather than silently omitted or guessed at. This changes internal per-segment structured output (visible in `study_results.json`) but never any segment's own reported `net_pnl`/`net_pnl_marked` dollar figure (already correct, unaffected) -- so the Per-Segment table below is unchanged from Round 3.

## Pooled Results (2022-01-03 through 2026-08-13), Base Cost (5 bps)

| Metric | ARM_A `ETF_PULLBACK_RSI2` | ARM_B `SECTOR_MOMENTUM_12_1` | ARM_C `DIVERSIFIED_TREND_12M` |
|---|---:|---:|---:|
| Closed-lot count | 353 | 88 | 128 |
| Filled entry count (ARM_A only) | 354 | n/a | n/a |
| Signal count (ARM_B/ARM_C only) | n/a | 56 | 56 |
| Scheduled / executed rebalance count | 0 / 0 (n/a, ARM_A) | 56 / 55 | 56 / 49 |
| Buy / sell transaction count | 354 / 353 | 91 / 88 | 107 / 128 |
| Exposure (% of sessions with >=1 position) | 48.5% | 96.5% | 89.1% |
| Average concurrent positions | 0.88 (of 3 max) | 2.79 (of 3 max) | 4.18 (of 8 universe) |
| Gross P&L (realized, closed trades) | $7,406.79 | $9,075.25 | $7,248.54 |
| Net P&L (realized, closed trades) | $4,390.16 | $8,629.61 | $6,862.90 |
| Net P&L (equity, incl. unrealized) | $4,450.37 | $11,423.06 | $10,630.72 |
| **Net P&L (marked -- equity less hypothetical endpoint liquidation cost)** | **$4,445.29** | **$11,402.44** | **$10,610.96** |
| Mean monthly $ on $30,000, **fixed-notional (PRIMARY)** | **$78.53** | **$196.72** | **$177.26** |
| Median monthly $, fixed-notional | $122.94 | $219.67 | $298.83 |
| Mean monthly $, compounded (secondary) | $79.47 | $203.98 | $189.83 |
| Mean monthly return % | 0.262% | 0.656% | 0.591% |
| CAGR (secondary, compounding) | 3.05% | 7.25% | 6.80% |
| Sharpe (annualized, 0% RF) | 0.53 | 0.58 | 0.66 |
| Sortino (annualized, 0% RF, Finding 17 formula) | 0.78 | 0.87 | 1.00 |
| Annualized volatility (Finding 14) | 5.88% | 13.63% | 10.67% |
| Annualized downside deviation (Finding 14/17 formula) | 4.03% | 8.99% | 7.12% |
| Max drawdown ($ / %) | $2,713 / 9.04% | $7,519 / **23.56%** | $8,981 / **26.79%** |
| Drawdown recovery | 230 days | 474 days | 874 days |
| Profitable months | 58.9% | 57.1% | 57.1% |
| Longest losing-month streak | 3 | 7 | 4 |
| Worst month, fixed-notional ($) | −$1,435.78 | −$3,083.18 | −$2,301.42 |
| 5th-percentile month (%) | −3.11% | −5.59% | −5.17% |
| Turnover ratio (ledger notional / avg equity) | 198.3x | 30.1x | 27.3x |
| Total cost paid (base, 5 bps, incl. endpoint mark) | $3,026.75 | $485.47 | $423.28 |
| Hypothetical endpoint-liquidation cost | $5.08 | $20.62 | $19.76 |
| Best-symbol positive-net-contribution share | 22.2% (XLK) | **47.3% (XLC)** | 39.0% (QQQ) |
| Correlation to ORB baseline (monthly) | 0.146 | 0.109 | 0.088 |
| Correlation to SPY buy-and-hold (monthly) | 0.343 | 0.825 | 0.717 |

**Reading the three net-P&L rows.** "Realized" counts only closed trades within the window. "Equity" is the account's actual mark-to-market value change, which includes unrealized gains on anything still open at the window's end -- but does not yet charge a cost for exiting those. "Marked" (the figure used for classification's `pooled_net_base`/`pooled_net_stress` inputs and for symbol concentration) subtracts a hypothetical liquidation cost for whatever is still open at the STUDY's true fixed endpoint (2026-08-13) only -- resolved by exact date (Finding 11), never at an interim segment boundary. Reconciliation check (see `reconciliation` in the structured results): each arm's own per-symbol positive/negative net contributions sum EXACTLY to that arm's marked net P&L (no residual, for all three arms, in this corrected run).

**Reading the count rows (Finding 14, corrected by Finding 18).** "Closed-lot count" is the number of closed positions/partial trims -- never a rebalance-event count. "Filled entry count" (ARM_A only) is every real entry fill -- honestly labeled as a fill count, not a raw signal count, since ARM_A has no ledger of signals that were scanned but never filled (e.g. because the 3-position cap was already reached). "Signal count" (ARM_B/ARM_C only) is a genuine PRE-FILL count: every month-end selector call that got scheduled to execute in-window, whether or not it later produced any transaction. "Scheduled rebalance count" is the number of month-end rebalances due to execute in-window; "executed" is the subset that actually produced at least one transaction (ARM_C's 56 scheduled vs. 49 executed reflects month-ends where the selected basket didn't require any trade, or fell right at the evaluation boundary). ARM_A has no periodic-rebalance concept, so both rebalance counts are 0 by definition.

## Stress-Cost (10 bps) Sensitivity

| Metric | ARM_A | ARM_B | ARM_C |
|---|---:|---:|---:|
| Net P&L (marked), stress cost | $1,039.14 | $10,804.29 | $9,996.36 |
| Total cost paid, stress | $6,042.15 | $963.08 | $841.47 |
| Stress-cost pooled result positive? | **Yes** | **Yes** | **Yes** |

All three arms remain net-positive under the doubled (10 bps) cost assumption. ARM_A's cost load nearly doubles ($3,027 → $6,042) given its very high turnover (198.3x), leaving stress-case net P&L at $1,039 -- still positive, but transaction costs are clearly the dominant swing factor for ARM_A specifically, far less so for the two much-lower-turnover monthly arms (ARM_B 30.1x, ARM_C 27.3x).

## Per-Segment Net P&L (marked, base cost)

| Segment | ARM_A | ARM_B | ARM_C |
|---|---:|---:|---:|
| 2022 | −$2,101.61 | −$2,423.47 | −$3,788.59 |
| 2023 | +$1,172.13 | +$1,302.49 | +$2,135.90 |
| 2024 | +$2,304.93 | +$5,346.40 | +$3,440.69 |
| 2025 | +$771.57 | +$4,205.26 | +$4,861.27 |
| 2026 YTD | +$2,298.28 | +$2,971.77 | +$3,961.69 |

All three arms have exactly one negative segment (2022, the broad-market drawdown year); 2023-2026 YTD are all positive for all three arms, satisfying the DIRECT gate's "four required segments all positive" condition for every arm (that gate still fails each arm on other grounds -- see Classification below). Segment figures reconcile EXACTLY (no residual) to each arm's own pooled marked net P&L above, verified directly against the structured results (e.g. ARM_A's five segments sum to $4,445.29, matching the pooled $4,445.29 to the last cent).

## Stress-Cost Segment Metrics (marked, 10 bps -- Finding 19)

Issue #348 requires segment-level outputs for both cost cases; Round 3 only reported base-cost segments. Stress-cost (10 bps) segment net P&L, from the stress simulation's own ledgers:

| Segment | ARM_A | ARM_B | ARM_C |
|---|---:|---:|---:|
| 2022 | −$2,430.10 | −$2,552.87 | −$3,871.75 |
| 2023 | +$253.66 | +$1,216.20 | +$2,050.61 |
| 2024 | +$1,322.48 | +$5,259.69 | +$3,304.49 |
| 2025 | +$147.73 | +$4,056.74 | +$4,668.94 |
| 2026 YTD | +$1,745.37 | +$2,824.54 | +$3,844.07 |

These five segments sum exactly to each arm's own stress-cost pooled net P&L above (e.g. ARM_A: −2430.10+253.66+1322.48+147.73+1745.37 = $1,039.14, matching the Stress-Cost Sensitivity table). The stress-cost 2022 segment is more negative than base-cost 2022 for every arm (higher cost load compounding the drawdown year), consistent with the pooled base-vs-stress comparison above.

Segment-level `concentration`/`reconciliation` (in the structured results, both base and stress cost cases) also now folds in a carrying position's own unrealized movement wherever a segment boundary is crossed unambiguously (Finding 19) -- a position untouched during the segment, or one that entered fresh with no later topup/trim. Where a position was topped up or trimmed while remaining open at a segment's own end, that gap is left visible via that segment's own `reconciliation.residual` (in `study_results.json`) rather than guessed at; this is a genuine, disclosed limitation of the current per-position ledger (which tracks a running weighted-average cost basis, not per-lot history), most visible for ARM_B/ARM_C in years with frequent within-year rebalancing turnover on the same symbol. This never changes any segment's own reported `net_pnl`/`net_pnl_marked` dollar figure (both tables above are unaffected and unchanged from Round 3).

## $150 / $300 Risk Views (ARM_A only -- post-hoc quantity scaling, same trades, not rerun)

| | $300 risk (as-run) | $150 risk (secondary view) |
|---|---:|---:|
| Gross P&L | $7,406.79 | $3,700.49 |
| Net P&L (realized) | $4,390.16 | $2,194.57 |

Scaling to $150 risk/position roughly halves both figures, as expected for a linear position-size scaling with no change in trade selection or timing.

**Fixed-$30K monthly-dollar view is not reported at either risk cap here (Finding 14).** Only realized gross/net P&L can be validly derived post-hoc by linearly rescaling each closed trade's quantity. A fixed-$30,000-notional monthly-dollar figure at $150 or $300-scaled risk is NOT reported: position sizing, cash constraints, and the concurrent-position limit are all risk-cap-dependent, so a valid fixed-notional monthly series at a different risk cap requires rerunning the full simulation at that cap, not a post-hoc rescale of the as-run $300-cap trade list. The primary $300-cap fixed-$30K monthly figure ($78.53/month) is the Pooled Results table above.

## Symbol Concentration (positive-net-contribution basis, includes the fixed-endpoint mark)

- **ARM_A**: best contributor XLK (22.2% of positive net P&L) -- broad-based across ten different positively-contributing symbols (DIA, IWM, QQQ, SPY, XLB, XLC, XLE, XLK, XLRE, XLV), none close to dominating.
- **ARM_B**: best contributor XLC at 47.3% of positive net P&L -- under the 50% concentration gate, but still the largest single driver by a wide margin over the next-largest (XLK, $2,864 vs. XLC's $6,641); XLF and XLI also now show as meaningfully positive contributors ($2,233 and $456) that were not visible before the Round 3 warm-up-execution-gating and exact-endpoint-date fixes changed which trades and marks fall inside the evaluation window.
- **ARM_C**: best contributor QQQ at 39.0% of positive net P&L, with GLD ($2,077), IWM ($3,587), and SPY ($1,658) all meaningfully positive too. The required SPY/QQQ/IWM overlap disclosure: SPY, QQQ, and IWM are three separate broad-market-beta ETFs in ARM_C's own 8-symbol universe -- three of ARM_C's four positive contributors are large-cap/broad-market exposure (QQQ, IWM, SPY), not independent asset-class bets. GLD is the only genuinely diversifying sleeve among the positive contributors (DBC and IEF's own contributions are negligible or absent from the positive-contributor set in this corrected run).

## Correlations

**Cross-arm (monthly returns):** ARM_A↔ARM_B 0.481, ARM_A↔ARM_C 0.462, ARM_B↔ARM_C 0.795. All three arms are positively correlated with each other, ARM_B/ARM_C strongly so (0.795) -- unsurprising, since both are trend/momentum strategies that tend to be invested through the same rising-market years. This tempers any "genuinely uncorrelated combination" case among the M48 arms themselves.

**To the ORB baseline (monthly, zero-filled across the full 56-month grid):** ARM_A 0.146, ARM_B 0.109, ARM_C 0.088 -- all comfortably below the 0.60 CONTRIBUTOR ceiling and all fairly low in absolute terms. None of the three arms' failure to reach PROMISING_PORTFOLIO_CONTRIBUTOR is due to ORB correlation.

**To SPY buy-and-hold (monthly, benchmark only):** ARM_A 0.343, ARM_B 0.825, ARM_C 0.717. SPY buy-and-hold itself returned **+73.07%** over the identical window (2022-01-03 to 2026-08-13, start close $449.46, end close $777.88) with an 18.76% max drawdown -- reported strictly as a benchmark, per the frozen protocol, and never used to promote or demote any arm.

## Predeclared Classification

Applied exactly via `classify_capital_target_arm` (no arm compared against another; each verdict comes from that arm's own numbers only). `pooled_net_base`/`pooled_net_stress`/segment inputs all use the MARKED net P&L basis (equity less hypothetical endpoint liquidation cost); the dollar-threshold gates use the fixed-notional PRIMARY monthly mean.

### ARM_A `ETF_PULLBACK_RSI2`

```
DIRECT_TARGET_FEASIBLE checks:
  average_monthly_dollars_at_least_1000:        FALSE  ($78.53 < $1,000)
  required_segments_all_positive:               TRUE
  max_drawdown_at_most_20pct:                   TRUE   (9.04%)
  stress_cost_pooled_positive:                  TRUE
  no_symbol_over_50pct_of_positive_pnl:         TRUE   (22.2%)
CONTRIBUTOR checks:
  average_monthly_dollars_at_least_250:         FALSE  ($78.53 < $250)
  pooled_net_positive / mean_monthly_positive:   TRUE / TRUE
  at_least_4_of_5_segments_nonnegative:         TRUE   (4/5)
  at_least_3_of_5_segments_positive:            TRUE   (4/5)
  max_drawdown_at_most_15pct:                   TRUE   (9.04%)
  stress_cost_pooled_positive:                  TRUE
  profitable_months_at_least_55pct:             TRUE   (58.9%)
  orb_correlation_at_most_060:                  TRUE   (0.146)
  no_symbol_over_50pct_of_positive_pnl:         TRUE   (22.2%)
```
**VERDICT: POSITIVE_BUT_INSUFFICIENT.** Every CONTRIBUTOR gate passes except the $250/month dollar threshold ($78.53 actual) -- the closest of the three arms to CONTRIBUTOR status, failing on exactly one gate, consistent with the Round 2 finding even though the underlying numbers moved again under Round 3's warm-up-execution-gating and exact-endpoint-date fixes.

### ARM_B `SECTOR_MOMENTUM_12_1`

```
DIRECT_TARGET_FEASIBLE checks:
  average_monthly_dollars_at_least_1000:        FALSE  ($196.72 < $1,000)
  required_segments_all_positive:               TRUE
  max_drawdown_at_most_20pct:                   FALSE  (23.56%)
  stress_cost_pooled_positive:                  TRUE
  no_symbol_over_50pct_of_positive_pnl:         TRUE   (47.3%)
CONTRIBUTOR checks:
  average_monthly_dollars_at_least_250:         FALSE  ($196.72 < $250)
  max_drawdown_at_most_15pct:                   FALSE  (23.56%)
  no_symbol_over_50pct_of_positive_pnl:         TRUE   (47.3%)
  (all other contributor checks pass)
```
**VERDICT: POSITIVE_BUT_INSUFFICIENT.** Fails on the dollar threshold and drawdown; concentration PASSES under the corrected trim/top-up rebalancing.

### ARM_C `DIVERSIFIED_TREND_12M`

```
DIRECT_TARGET_FEASIBLE checks:
  average_monthly_dollars_at_least_1000:        FALSE  ($177.26 < $1,000)
  required_segments_all_positive:               TRUE
  max_drawdown_at_most_20pct:                   FALSE  (26.79%)
  stress_cost_pooled_positive:                  TRUE
  no_symbol_over_50pct_of_positive_pnl:         TRUE   (39.0%)
CONTRIBUTOR checks:
  average_monthly_dollars_at_least_250:         FALSE  ($177.26 < $250)
  at_least_4_of_5_segments_nonnegative:         TRUE   (5/5)
  max_drawdown_at_most_15pct:                   FALSE  (26.79%)
  no_symbol_over_50pct_of_positive_pnl:         TRUE   (39.0%)
  (all other contributor checks pass)
```
**VERDICT: POSITIVE_BUT_INSUFFICIENT.** Fails on the dollar threshold and drawdown. ARM_C's mean monthly dollar figure and max drawdown are unchanged from Round 2 to the displayed precision ($177.26, 26.79%) -- Finding 10's warm-up-execution gating did not measurably change ARM_C's own trade set in-window. Finding 11's exact-endpoint-date fix DID move ARM_C's endpoint-liquidation cost slightly ($19.81 -> $19.76, since it holds an open position at the true 2026-08-13 endpoint), a small effect visible in the pooled table above but not large enough to change the headline dollar/drawdown figures at two-decimal precision.

## Decision-Tree Route

No arm reached `DIRECT_TARGET_FEASIBLE`. No arm reached `PROMISING_PORTFOLIO_CONTRIBUTOR`. **All three arms are `POSITIVE_BUT_INSUFFICIENT`** -- positive pooled net return after both cost cases, but each fails one or more contributor gates. This is unchanged from Round 2, though the underlying numbers moved under Round 3's corrections. Per the frozen decision tree: *"Only POSITIVE_BUT_INSUFFICIENT arms → record the economics and decide whether combining two pre-existing positive arms with ORB is mathematically plausible; no parameter tuning."*

Recorded economics for that judgment: ARM_A's own numbers remain closest to a genuine portfolio-contributor case (lowest drawdown at 9.0%, most broad-based concentration at 22.2%, lowest ORB correlation at 0.146, only the dollar-magnitude gate fails) but its P&L is small in absolute terms ($78.53/month) and its very high turnover (198.3x) makes it by far the most cost-sensitive of the three (stress-case net P&L falls to $1,039, versus base $4,445). ARM_B and ARM_C both cleared meaningfully larger absolute dollar figures ($177-197/month) but carry drawdowns (23.6%, 26.8%) well above the 15% contributor ceiling -- a real combined portfolio would need to size either down materially to control that risk, diluting exactly the dollar edge that makes them interesting. Cross-arm correlations (0.46-0.80, with ARM_B/ARM_C at a strong 0.795) mean the three M48 arms are not a strong diversification bloc among themselves either -- ARM_B and ARM_C in particular would contribute largely redundant portfolio risk if combined with each other. No parameter tuning, universe change, or threshold adjustment was made in response to any of this, at any point across three rounds of correction -- these are the frozen protocol's own numbers, reported as computed.

### Finding 15: Combination-Plausibility Upper Bound

Since every arm is `POSITIVE_BUT_INSUFFICIENT`, the frozen decision tree requires computing a deliberately generous mathematical upper bound for combining ORB with the two highest standalone arms, BEFORE any M49 portfolio-combination simulation is considered:

```
ORB (1% risk, established reference figure)        $108.90 /month
+ SECTOR_MOMENTUM_12_1 (highest standalone arm)     $196.72 /month
+ DIVERSIFIED_TREND_12M (2nd-highest standalone arm) $177.26 /month
------------------------------------------------------------------
= Generous combination upper bound                  $482.87 /month
  Target                                           $1,000.00 /month

VERDICT: COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE
```

This bound is deliberately generous, not a claim of real achievability: it sums ORB's and each chosen arm's OWN monthly-dollar figure as though each could independently draw on its own full $30,000 (or, for ORB, its own $300-per-trade risk capital), ignoring that a single real $30,000 account would have to be SPLIT between whichever of ARM_B and ARM_C were actually run together, diluting each one's dollar contribution below its solo-$30k figure reported above. Because this upper bound can only OVERSTATE true combined achievability, never understate it, and it still falls to less than half the $1,000/month target ($482.87 vs. $1,000.00), no less-generous (capital-split) combination of ORB with any two M48 arms could plausibly reach the target either.

**No M49 portfolio-combination simulation is warranted.** The generous upper bound does not support it. This is the milestone's final, honest negative answer to the question M48 set out to ask: none of the three ETF strategy families, alone or combined with ORB under a deliberately favorable assumption, can plausibly close the gap to a $1,000/month target from $30,000 of capital under the frozen safety constraints. ARM_A remains the most interesting candidate for a future, separately-scoped portfolio-contributor role (lowest drawdown, lowest ORB correlation, broadest concentration) if a future milestone wants to pursue that on its own frozen terms -- but that is future scope, not something this milestone's frozen protocol authorizes proceeding to now.

*Recomputed, not assumed, after Round 4's Finding 16 anchor fix: SECTOR_MOMENTUM_12_1 and DIVERSIFIED_TREND_12M remain the two highest standalone arms with numerically unchanged fixed-$30K mean monthly dollars ($196.72 / $177.26 -- see "Corrections From Independent Review -- Round 4" above for why the anchor fix did not move this dataset's pooled dollar figures), so the upper bound and verdict above are identical to what a naive assumption would have predicted -- but this was verified by an actual rerun of the corrected implementation, not carried forward without checking.*

## Safety

`strategy.enabled` remains `false` throughout. No broker-write code was added; every network call in this milestone is a market-data-only Alpaca request (`AlpacaCredentials`, never a brokerage credential) -- and every rerun since Round 2 (including this Round 4 result set) required zero such calls, since the complete study reran entirely from the existing gitignored SIP cache (cache-only mode fails closed on any cache miss rather than silently fetching). No production `SignalEngine`/`ExitEngine`/`Simulator`/config/universe/broker code was touched; no pinned-closure file was modified. `src/orb_capital_target_strategy_family_research.py` and `scripts/research_capital_target_strategy_family.py` are research-only. Raw vendor data was never committed -- only the committed SHA-256 hashes document (`docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json`) and this report's aggregate tables; the gitignored provenance manifest and raw daily-bar caches (both IEX and SIP) stay local-only. Trusted implementation digest confirmed unchanged before and after this milestone: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`.

**Provenance chain for this result set (Round 4, Finding 13's sequencing, continued):** implementation commit `f04b68bd46f6b2dc8a6773ebafcddd28b43dfee9` (Findings 16-19) was committed and pushed on top of the Round 3 chain (`999d43c` → `95d7372` → `36f3a19`, see the prior report revision for that history), with NO results/report/hashes-document change included. The complete study was then run exactly once more, from a clean checkout of `f04b68b` (working tree verified clean, scoped to the two source files being hashed), against the existing SIP cache -- and that exact commit SHA is what `generation_git_head_sha` in the committed hashes document now records, alongside SHA-256 hashes of both source files and a `generation_working_tree_clean: true` flag. This report's results/report/hashes-document update is committed separately, afterward, per the same required sequencing Finding 13 established.

## Structured Artifacts

Generated under `artifacts/research-capital-target-strategy-family/` (gitignored, regenerable from cache via `python scripts/research_capital_target_strategy_family.py`): `daily-bar-cache/` (original IEX responses, preserved as historical evidence) and `daily-bar-cache/sip/` (frozen empirical SIP responses), `results/provenance_manifest.json` (per-symbol provenance/hashes/coverage), `results/study_results.json` (full structured results for every arm/cost-case/segment, including the transaction and position/equity ledgers, the Finding 15 `combination_plausibility` block, and the Finding 19 `stress_segments`/`segment_end_open_positions`-derived per-segment carry attribution). The COMMITTED (non-gitignored) counterpart is `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json` -- SHA-256 hashes only, no raw vendor data -- carrying, per arm/cost-case, three separate ledger hashes (`strategy_transaction_ledger_sha256`, `endpoint_mark_ledger_sha256`, `combined_accounting_ledger_sha256`) plus an expanded `position_equity_ledger_sha256` covering full daily position snapshots (Finding 12), and `generation_git_head_sha`/`generation_working_tree_clean`/`generation_source_file_sha256` (Finding 13).
