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
- Identifier resolution: N-PORT holdings carry CUSIP/ISIN but no ticker. Resolved via OpenFIGI's free public search API, filtered to all six major US common-stock listing venues per OpenFIGI's own published exchange-code reference (NYSE, Nasdaq Global Select, Nasdaq Global Market, Nasdaq Capital Market, NYSE American, NYSE Arca) and `securityType2=="Common Stock"`. A holding resolves only if exactly one distinct ticker survives that filter; zero or multiple distinct tickers are preserved as `UNRESOLVED`/`AMBIGUOUS` respectively -- never guessed, never silently substituted with a current successor. Exchanges are queried NYSE/Nasdaq-Global-Select first (the primary listing venue for the overwhelming majority of Russell 1000 constituents); once the candidates accumulated so far already resolve uniquely, remaining exchanges for that name are skipped, an accepted speed/thoroughness trade-off for real large/mid-cap common-stock names given the otherwise-impractical total runtime across ~1,000+ holdings per snapshot -- a holding that has NOT yet resolved uniquely always continues through every remaining exchange.

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

## Stage-0 result and classification (added in the results commit)

_Pending -- see the results commit for the full evidence (filing list, universe-snapshot resolution counts, daily-bar coverage), the Stage-0 gate outcome, base/stress metrics by calendar segment, the final classification, and the exact implementation/results-generating commit SHAs._
