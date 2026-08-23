# M52: Concentrated Point-in-Time Cross-Sectional Equity Momentum Feasibility

**Issue:** [#356](https://github.com/sanuwar/orb-live/issues/356)
**Branch:** `agent/m52-cross-sectional-equity-momentum-feasibility`
**Depends on:** M51 (`SELECTED_FOR_M52` -- concentrated long-only cross-sectional equity momentum with a market/trend filter). M43-M49's ORB/ETF family is not reopened here.

> **This section is committed BEFORE any empirical result is read**, exactly as Issue #356 requires. It records the frozen protocol only: universe sourcing, liquidity/momentum/trend-filter rules, portfolio mechanics, Stage-0 gate, and the classification function (`src/cross_sectional_momentum_research.py`, `scripts/research_cross_sectional_momentum.py`). The Stage-0 outcome, results, and classification are added in a later, separate results commit, after the single frozen experiment is actually run from a clean implementation checkout.

## Purpose

M51 evaluated four candidate strategy families and selected exactly one for a separately frozen M52 experiment: **concentrated long-only cross-sectional equity momentum with a market/trend filter**. M52 tests that one strategy, and only that strategy, on a fixed $30,000 account.

> Determine whether this frozen momentum strategy can produce a practically useful, risk-controlled result on a fixed $30,000 account after realistic costs.

- Primary target: **$500/month** average fixed-notional net profit. Stretch target: **$1,000/month**. Neither is assumed or guaranteed.
- Research only. No activation, no broker writes.
- ORB, PEAD, options, ETF trend following, and every M43-M51 threshold remain out of scope and untouched.

## Frozen configuration (`src/cross_sectional_momentum_research.py`)

### Universe: iShares Russell 1000 ETF (IWB) point-in-time holdings

- Official identity independently verified against SEC's own series/class index: CIK `0001100663` (iShares Trust), series `S000004347`, class `C000012077` -- "iShares Russell 1000 ETF," distinct from the adjacent "iShares Russell 1000 Growth ETF" (`S000004346`) and "iShares Russell 1000 Value ETF" (`S000004345`).
- Point-in-time holdings from SEC EDGAR Form N-PORT-P filings, discovered via EDGAR's full-text search API scoped to the exact series ID (a robust, programmatic, verifiable query -- not a hand-curated accession list). Confirmed real filing history: quarterly, ~60-day public-disclosure lag (only the third month of each fiscal quarter's holdings become public), 27 filings spanning period-ending 2019-09-30 through 2026-03-31.
- At every monthly rebalance signal date, only the most recently PUBLISHED filing (`file_date <= signal_date`) is used -- `select_filing_for_signal_date`, never the filing with the latest `period_ending` (that would be lookahead). A signal date's eligible universe is therefore normally 2-5 months stale relative to that date, by design, matching what was genuinely knowable at the time.
- Holdings filtered structurally via the filing's own categorization (`assetCat=="EC"`, `issuerCat=="CORP"`, `invCountry=="US"`) to US common-equity corporate issuers only -- excludes cash, derivatives, other ETF shares, preferred stock, and non-US issuers by construction, not by name-matching heuristics.
- Identifier resolution: N-PORT holdings carry CUSIP/ISIN but no ticker. Resolved via OpenFIGI's free public search API, filtered to `securityType2=="Common Stock"` on NYSE (`UN`) and Nasdaq Global Select (`UW`) -- the primary listing venue for the overwhelming majority of genuine Russell 1000 (large/mid-cap US operating company) constituents. A holding resolves only if exactly one distinct ticker survives that filter; zero or multiple distinct tickers are preserved as `UNRESOLVED`/`AMBIGUOUS` respectively -- never guessed, never silently substituted with a current successor.
  - **Documented scope trade-off:** OpenFIGI's own exchange-code reference lists four additional US common-stock venues (Nasdaq Global Market, Nasdaq Capital Market, NYSE American, NYSE Arca) that this pipeline does NOT search. A six-exchange version was run live first; OpenFIGI's unauthenticated rate limit proved bursty/unpredictable under sustained load (a meaningful fraction of individual requests returned HTTP 429 regardless of inter-request spacing), making the full ~1,000-holding, 27-filing universe run take upward of a day of wall-clock time. Narrowing to the two dominant venues was a deliberate choice to keep the milestone bounded; it narrows resolution COVERAGE, not the resolution LOGIC's correctness (a holding whose primary listing happens to be on one of the four omitted venues is preserved as UNRESOLVED, never guessed). The aggregate unresolved count/percentage is reported honestly in Stage-0 evidence, not hidden.
  - **Ticker-format validation and normalization:** candidates are additionally filtered to a plausible ticker pattern (1-5 letters, optional one-or-two-letter share-class suffix separated by `.` or `/`) before the uniqueness check. This was added after a live run found OpenFIGI returns an internal, non-tradeable placeholder string (e.g. `0673085D`) as the "ticker" for issuers with no current listing (e.g. Pluralsight LLC, acquired/delisted in 2021) -- such candidates are excluded, leaving the holding correctly `UNRESOLVED` rather than passed downstream to the market-data fetch. Separately, OpenFIGI's own share-class notation uses `/` (e.g. `BRK/B`) where Alpaca's API requires `.` (confirmed live: `BRK.B` returns real bars, `BRK-B` 400s, `BRKB` 200s with zero bars); a genuine `/`-notation candidate is therefore normalized to `.` before the resolved ticker is stored, so it is Alpaca-compatible by construction rather than crashing the later data-fetch phase.

### Liquidity eligibility (checked at every signal date, using only data available by that date)

- Trailing 60-session median close >= $10.
- Trailing 60-session median dollar volume >= $50,000,000.
- Trailing 60-session median share volume >= 500,000.
- >= 95% session coverage over that trailing window.

### Data

- Alpaca SIP daily bars, `timeframe=1Day`, `adjustment=all` -- same feed/adjustment convention as M48/M49.
- Separate, immutable M52-only cache paths (`artifacts/research-cross-sectional-momentum/`), gitignored; every prior M43-M51 cache preserved untouched.
- Warmup fetch begins 2020-01-02. Evaluation window: **2021-01-04 through 2026-08-13**.
- Endpoint marking uses the exact 2026-08-13 close (date-lookup, never a positional index).

### Signal: 12-1 momentum, top 15, monthly rebalance

- On the final trading session of each calendar month: `adjusted_close[t-21] / adjusted_close[t-252] - 1` for every liquidity-eligible, point-in-time-resolved stock.
- Rank descending; ties broken by ticker ascending (deterministic).
- Select exactly the top 15; if fewer qualify, use the qualifying number and report the shortfall -- never padded.

### Market/trend filter: SPY vs. its own 200-session SMA

- Signal-date SPY adjusted close strictly greater than the trailing 200-session SMA (computed from the 200 sessions immediately preceding the signal date, excluding the signal-date bar itself) -> long permitted; otherwise liquidate and hold cash until a later month's signal passes. No alternate lengths, tolerance bands, or delayed confirmation tested.

### Portfolio mechanics

- $30,000 starting capital, cash account, long-only, whole shares, no leverage/margin/options/shorting/fractional shares.
- Equal-capital weighting recomputed at every rebalance from total account equity (cash + mark-to-market positions) divided by the selected count.
- Execution at the next session's open. Event order: sell removals in full -> trim overweight continuing holdings -> top up underweight continuing holdings -> buy new additions. Deterministic ticker-ascending ordering within each step.
- Weighted-average cost basis (including buy-side costs) tracked per position so realized P&L is computed correctly across partial buys/sells, not merely approximated.
- Cash never allowed negative (fail-closed if it would go negative). Every signal, intended selection, fill, and blocked/under-filled intent recorded explicitly -- nothing silently dropped.

### Costs

- Base: 5 bps/side. Stress: 10 bps/side. Reuses M48/M49's transaction-cost and fixed-$30,000 (non-compounding) primary reporting convention directly -- `monthly_return_pct`, `fixed_notional_monthly_dollar_pnl`, `max_drawdown`, `cagr`, `sharpe_ratio`, `sortino_ratio`, and `annualized_downside_deviation_pct` are imported and called UNCHANGED from `src/orb_capital_target_strategy_family_research.py`, not reimplemented -- so parity with the frozen M48/M49 metric definitions holds by construction.

## Stage 0 -- mandatory provenance/data feasibility gate

Per Issue #356, completed and reported before any portfolio economic result is read:

1. **Universe coverage:** a qualifying N-PORT-P filing is confirmed available (via `select_filing_for_signal_date`) for every monthly signal date in the evaluation window.
2. **No future-snapshot leakage:** structurally guaranteed by `select_filing_for_signal_date`'s own filter (`file_date <= signal_date`), covered by dedicated unit tests.
3. **Deterministic identifier resolution:** every N-PORT holding resolved via OpenFIGI is classified `RESOLVED`/`UNRESOLVED`/`AMBIGUOUS`; the aggregate unresolved/ambiguous count and percentage across all snapshots is reported, never hidden.
4. **Daily-bar coverage/integrity:** SPY and the full resolved-ticker union fetched from Alpaca; coverage reported per symbol.
   - **Data-gap handling in daily equity marking:** a live run's first computed max drawdown (97%) turned out to be a single-day artifact, not a real portfolio loss -- 2025-01-09 was a full-day NYSE/Nasdaq closure (National Day of Mourning for former President Jimmy Carter) that this pipeline's session calendar had no data for on that specific date, and the original equity-marking logic treated a missing bar as a $0 mark for every held position that day, reversing the moment data resumed the next real session. Fixed at the source of the defect, not the calendar: when a held symbol has no bar for a given session, its last known close is carried forward instead of substituted with $0 -- correct treatment regardless of whether the missing bar is a genuine vendor gap or a market closure the calendar table doesn't yet know about. Covered by a dedicated regression test (`test_simulate_portfolio_marks_missing_bar_using_last_known_close_not_zero`) confirmed to fail against the original `or Decimal(0)` fallback.
5. **Corporate actions:** `adjustment=all` on every Alpaca request handles splits/dividends at the source, same convention as M48/M49.
6. **Calendar:** `research_regular_session_bounds` (extended in M50 to 2018-01-01, delegating to the production `exchange_calendar` for 2026 onward) used throughout -- no separate calendar logic in this module.
7. **Parity:** the reused M48 metric primitives are called unchanged, not reimplemented -- parity holds by construction, not by a separate re-verification pass.
8. **Synthetic no-lookahead/no-pre-window-execution/whole-share/nonnegative-cash/deterministic-ranking proofs:** covered by dedicated unit tests in `tests/test_cross_sectional_momentum_research.py` (`test_simulate_portfolio_no_pre_evaluation_start_transactions`, `test_simulate_portfolio_cash_never_negative_and_endpoint_marks_at_evaluation_end`, `test_simulate_portfolio_whole_shares_only`, `test_rank_and_select_top_n_orders_descending_ties_ascending_ticker`, `test_trend_filter_excludes_signal_date_bar_from_its_own_sma`).
9. **No duplicate transactions or unclassified blocked intents:** verified programmatically against the actual run's transaction/blocked-intent records.
10. **Clean implementation-generating commit and source hashes:** the established M48-M51 `generation_git_head_sha`/`generation_source_file_sha256`/`generation_working_tree_clean` provenance convention.

If a material universe, data, parity, or integrity failure remains, M52 stops at `INSUFFICIENT_EVIDENCE` -- no vendor, index, date, or configuration substitution.

## Frozen classification (`classify_cross_sectional_momentum`)

Applied once, without tuning, from the base (5bps) and stress (10bps) metrics:

- **`TARGET_FEASIBLE`:** all of -- base mean monthly $ >= $500; stress mean monthly $ > $0; pooled base and stress endpoint-marked net P&L > $0; base max drawdown <= 25%; >= 5 of 6 calendar segments (2021-2025 + 2026 YTD) positive; profitable months >= 55%; worst month >= -10%; best-symbol positive-contribution share <= 35%; no integrity failure.
- **`POSITIVE_BUT_INSUFFICIENT`:** pooled base and stress net P&L both positive; base max drawdown <= 35%; >= 1 segment positive; one or more `TARGET_FEASIBLE` gates fail; no integrity failure. Checked only AFTER `NOT_PROMISING`'s own triggers are confirmed false (see note below).
- **`NOT_PROMISING`:** ANY of -- base or stress endpoint-marked net P&L <= $0; base max drawdown > 35%; fewer than 3 of 6 segments positive. This is checked as an explicit override BEFORE `POSITIVE_BUT_INSUFFICIENT`: the two labels' literal conditions overlap for the case of exactly 1-2 positive segments (satisfies `POSITIVE_BUT_INSUFFICIENT`'s ">= 1 segment" but also trips `NOT_PROMISING`'s "fewer than 3"), and `NOT_PROMISING`'s own explicit trigger must not be silently skipped in that case -- covered by a dedicated unit test.
- **`INSUFFICIENT_EVIDENCE`:** any Stage-0 provenance, data, parity, or integrity failure.

No threshold, universe source, lookback, skip period, portfolio size, liquidity floor, trend-filter length, rebalance timing, cost, date, or classification threshold is swept. One bounded run, one deterministic classification.

## Comparison points (reference only, never combined)

- **M49** (frozen, 2022-2026): $184.75/month base, $122.02/month stress, 14.60% base max drawdown -- `SAFE_BUT_BELOW_TARGET`.
- **SPY buy-and-hold** over the same evaluation window: a diagnostic comparison only, computed alongside the strategy's own results.

---

## Stage-0 result and classification

Generated `2026-08-23T03:34:17Z` from commit `3d345ebca19c7159678aaad801cdb66299f9ba6b` (clean working tree, both source files' SHA-256 hashes recorded in `artifacts/research-cross-sectional-momentum/results/m52_result.json` / `docs/research/CROSS_SECTIONAL_MOMENTUM_ARTIFACT_HASHES.json`).

### Stage-0 gate: PASS (`_all_ok = True`)

1. **Universe coverage:** a qualifying N-PORT-P filing was available for every one of the 69 monthly signal dates.
2. **No future-snapshot leakage:** structurally guaranteed, unit-tested.
3. **Identifier resolution:** 27 filings, 25,054 total holdings across all snapshots, 5,223 (20.85%) unresolved/ambiguous -- reported honestly, per the documented UN/UW-only exchange-search trade-off above. Every resolved holding is a deterministic, never-guessed ticker.
4. **Daily-bar coverage:** SPY available; all 942 requested resolved tickers returned at least some Alpaca data (0 symbols with zero coverage).
5. **Corporate actions:** `adjustment=all` used throughout.
6. **Calendar:** `research_regular_session_bounds` used throughout for session membership.
7. **Parity:** M48 metric primitives reused unchanged.
8. **Synthetic proofs:** all pass, including the new `test_simulate_portfolio_marks_missing_bar_using_last_known_close_not_zero` regression added during this run (see below).
9. **No duplicates / unclassified blocked intents:** 0 duplicate transactions (a Stage-0 harness bug that pooled base+stress transactions before deduplicating was found and fixed -- see "Defects found and fixed" below), 0 unclassified blocked intents.
10. **Provenance:** clean generating commit, source hashes recorded above.

### Defects found and fixed during this run (all in pipeline/harness code, none in the frozen economic rules)

- **OpenFIGI placeholder tickers / share-class notation:** a live run crashed fetching Alpaca data for `0673085D`, an OpenFIGI-returned internal placeholder (not a real ticker) for Pluralsight LLC, delisted after its 2021 acquisition. `resolve_holding` now requires a plausible ticker format before accepting a candidate, and normalizes OpenFIGI's `/` share-class notation (e.g. `BRK/B`) to Alpaca's required `.` notation (e.g. `BRK.B`), confirmed live.
- **Alpaca rate limiting:** the ~940-symbol sequential daily-bar fetch triggered Alpaca's SIP 429 rate limit partway through; `fetch_daily_bars_for_symbols` now retries with exponential backoff, scoped to this M52-owned CLI wrapper only.
- **Stage-0 duplicate-transaction false positive:** the item9 check pooled base and stress transactions before deduplicating, flagging 669 harmless base/stress overlaps (two independent simulations of the same signals routinely produce identical transaction tuples) as duplicates. Fixed to dedup each cost scenario separately.
- **Single-day equity-marking artifact (the one that changed the classification):** the first Stage-0-clean run classified `NOT_PROMISING` on a 97% max drawdown that turned out to be fabricated -- 2025-01-09 was a full-day NYSE/Nasdaq closure (National Day of Mourning for former President Jimmy Carter) not yet in this repo's research-only calendar table, so the equity-marking loop's `_close_on(...) or Decimal(0)` fallback marked every held position at $0 for that single day, reversing the moment data resumed. Fixed by carrying the last known close forward on a missing bar instead of the calendar itself (adding the missing holiday was rejected -- it shifts 252-session lookback boundaries enough to create a 1-day overlap between two unrelated, already-completed out-of-sample validation periods, Issue #321's fourth-period baseline study, which is out of M52's scope to reopen). Confirmed the corrected max drawdown reflects a real, gradual multi-week decline (equity peak $76,898.81 on 2026-06-22 to trough $50,645.61 on 2026-07-29), not another artifact.

### Base (5bps) metrics

| Metric | Value |
|---|---|
| Mean monthly $ | **$456.87** |
| Endpoint-marked net P&L | $35,315.86 |
| Gross P&L | $36,070.72 |
| CAGR | 15.15% |
| Sharpe | 0.62 |
| Sortino | 1.08 |
| Max drawdown | 34.14% ($26,253.20) |
| Worst month | -25.08% (2026-07, -$7,525.37) |
| 5th-percentile month | -10.52% |
| Profitable months | 51.47% |
| Longest losing streak | 3 months |
| Total costs | $754.86 |
| Avg / max invested positions | 12.07 / 15 |
| Avg cash utilization | 76.35% |
| Best-symbol contribution share | 9.09% |
| Signals / intended orders / fills / blocked | 69 / 840 / 1016 / 0 |

### Stress (10bps) metrics

Mean monthly $ **$439.23**; endpoint-marked net P&L $32,857.36; max drawdown 34.23%; worst month -25.19%; profitable months 51.47%.

### Calendar-segment net P&L (base)

| Segment | Net P&L | Positive? |
|---|---|---|
| 2021 | $3,892.36 | Yes |
| 2022 | -$6,724.93 | No |
| 2023 | $1,063.53 | Yes |
| 2024 | $16,490.35 | Yes |
| 2025 | $4,284.92 | Yes |
| 2026 YTD | $12,060.89 | Yes |

5 of 6 segments positive.

### SPY buy-and-hold comparison (reference only)

Net P&L $37,870.23, final equity $67,870.23, max drawdown 18.69% -- a smoother path than the strategy's, as expected for a single diversified index versus a concentrated 15-stock momentum sleeve.

### Classification: `POSITIVE_BUT_INSUFFICIENT`

`TARGET_FEASIBLE` gates that passed: pooled base/stress net P&L both positive, >=5 of 6 segments positive, best-symbol contribution share <=35%, stress mean monthly $ > $0.
`TARGET_FEASIBLE` gates that failed: base mean monthly $ ($456.87 < $500 target), base max drawdown (34.14% > 25% limit), profitable months (51.47% < 55%), worst month (-25.08% < -10% floor).
`NOT_PROMISING` triggers, all false: base/stress endpoint P&L both positive, base max drawdown 34.14% <= 35%, 5/6 segments positive (>= 3 required).
`POSITIVE_BUT_INSUFFICIENT` conditions, all true: pooled base/stress net P&L positive, base max drawdown <= 35%, >= 1 segment positive.

**Interpretation:** the frozen strategy is directionally profitable on this $30,000 account after realistic costs -- positive CAGR, positive net P&L in both cost scenarios, 5 of 6 calendar years/YTD periods profitable -- but falls short of the $500/month practical target primarily on drawdown depth (34% vs a 25% limit) and single-month tail risk (-25% worst month vs a -10% floor), both driven by the concentrated 15-name, no-hedge construction. Not a rejection of the underlying momentum signal; a statement that this specific sizing/risk configuration is not yet practically deployable without further risk controls (position-level stops, volatility targeting, or a wider/less concentrated book) that were explicitly out of scope for this one frozen run. No follow-on tuning was performed -- per Issue #356, one bounded run, one deterministic classification.
