# M55: Prospective Simulated Monitoring of the Frozen M52 Momentum Candidate

**Issue:** [#362](https://github.com/sanuwar/orb-live/issues/362)
**Branch:** `agent/m55-m52-prospective-paper-monitor`
**Depends on:** M52 (Issue #356, PR #357, merged `f205848…`, `POSITIVE_BUT_INSUFFICIENT`) and M54 (Issue #360, PR #361, merged `bdca030…`, which named this exact monitoring activity as the forward path -- `PAPER_MONITOR_RESEARCH_ONLY_CANDIDATE`). M53's two risk-control arms are explicitly NOT monitored here.

> **This section is committed BEFORE any prospective market result is read**, exactly as Issue #362 requires. It records the frozen protocol, the prospective boundary rule, the execution model, and the initialization-gate checklist only. The initialization evidence (the exact freeze timestamp, the baseline-parity result, and the first eligible signal/execution dates) is added in a separate, later results/checkpoint commit, after the monitor is actually run once from this clean commit.

## Purpose

Collect genuinely forward evidence on the frozen M52 momentum strategy without real-money trading, result-driven tuning, or reuse of future data. This is not a new backtest, not an M52/M53 optimization, not live strategy activation, not a continuously running process, and not authorization for broker/account access.

## Frozen candidate

The exact M52 strategy -- not either M53 arm:

- Point-in-time IWB universe from official SEC N-PORT-P filings; at each signal date, only a filing publicly filed on or before that signal date is used (`select_filing_for_signal_date`, reused unmodified).
- Deterministic OpenFIGI identifier resolution; unresolved/ambiguous holdings are never guessed.
- Alpaca SIP adjusted daily bars (`feed=sip`, `timeframe=1Day`, `adjustment=all`).
- Same liquidity floors, same 12-1 momentum calculation, top 15 equal-capital weight, same SPY 200-session trend filter.
- Monthly signal on the final completed regular trading session; execution at the next regular session's open.
- $30,000 fixed starting paper capital, long-only, whole shares, no leverage, no margin, no options, no shorting.
- Base 5bps and stress 10bps parallel paper ledgers.
- Same transaction ordering, accounting, endpoint marking, and metric primitives as M52 (`simulate_cross_sectional_momentum_portfolio`, `compute_portfolio_metrics`, called with M52's own defaults -- `top_n=15`, no volatility-exposure cap).
- No M53 `BREADTH_30` or `MARKET_VOL_15` overlay. No strategy parameter may change during monitoring -- enforced at runtime by `assert_frozen_candidate_config` (`src/momentum_prospective_monitor.py`), not merely documented.

## Prospective boundary

No paper P&L may begin before the first monthly signal whose signal date occurs **strictly after** the frozen protocol commit's timestamp. Compared as an America/New_York calendar date -- the conservative reading: a signal occurring on the exact same calendar date as the freeze is never treated as prospective, since that day's own close could not yet be known at the moment of freezing. Warm-up history (the existing M52 caches, extending back to 2020-01-02) may be loaded solely to compute the frozen indicators (252-session momentum lookback, 200-session SPY SMA) -- it is never itself treated as prospective performance evidence. The prospective start is never backdated.

If the protocol is committed before the current month's final regular session, that session's signal may become the first prospective signal; otherwise monitoring begins with the next month-end.

## Monitoring horizon

Minimum 12 completed monthly signal cycles. Interim checkpoints are descriptive only -- no promotion, activation, retuning, or reclassification follows from an interim checkpoint. At completion, the accumulated evidence is reported against the original M52 `TARGET_FEASIBLE`/`POSITIVE_BUT_INSUFFICIENT`/`NOT_PROMISING` gates, and any activation consideration requires a separately authorized decision milestone.

## Execution model

The monitor (`scripts/monitor_momentum_prospective.py`) runs once and exits:

- Never remains open waiting for future sessions; never polls continuously.
- If no new completed signal/execution/checkpoint is due, returns `NOT_DUE` with the last processed session, the next expected signal date, and the earliest next useful run date -- and writes nothing.
- If a checkpoint is genuinely due, processes only the new completed sessions and exits.
- Reruns against the same completed data are idempotent: no duplicate signal, transaction, or checkpoint is ever created (`checkpoint_run_decision` only proceeds past `NOT_DUE` when genuinely new data is available; the checkpoint ledger's own hash chain, `validate_hash_chain`, is checked before any append).
- No scheduled GitHub Actions workflow or repository secret is added in this milestone.

## Data rules

Market-data-only Alpaca access -- no account, position, order, balance, or portfolio endpoint is ever called (statically verified: `_no_broker_call_path_check` greps the monitor's own source for forbidden patterns before any checkpoint is accepted). Only completed regular sessions are used, determined in America/New_York time via `src.exchange_calendar.regular_session_bounds` (the digest-pinned, production-authoritative calendar, correctly early-close-aware). Feed `sip`, timeframe `1Day`, adjustment `all`. Raw vendor data stays gitignored; this milestone reuses M52's existing SEC/OpenFIGI/Alpaca caches (`artifacts/research-cross-sectional-momentum/`) directly rather than creating a parallel cache tree. Only genuinely new required filings, identifier resolutions, or completed daily bars are retrieved -- the full historical ~25,000-holding resolution process is never rerun. A newly-discovered N-PORT filing's accession, filing date, reporting date, series/class identity, source URL, and (once resolved) SHA-256 are recorded; a filing is only resolved into holdings when a genuinely due signal actually requires it, never speculatively. Current issuer holdings, an aggregator universe, or a future filing are never substituted for a missing point-in-time snapshot.

## State and audit design

An append-only, hash-chained checkpoint ledger, committed directly to git (not gitignored, since it is the derived audit record itself, not raw vendor data): `docs/research/MOMENTUM_PROSPECTIVE_MONITOR_M55_CHECKPOINTS.json`, a JSON array where each checkpoint records the protocol identifier/commit SHA, the previous checkpoint's own hash, its own content hash, the processing cutoff, the most recently known public filing, signal/execution dates, the eligible universe count, selected symbols and ranks, the SPY trend-filter state, intended/filled transactions, blocked/underfilled intents with reasons, base/stress cash/positions/equity/costs/realized-and-unrealized P&L, drawdown/exposure summaries, and an integrity/reconciliation status block. `validate_hash_chain` (`src/momentum_prospective_monitor.py`) walks the ledger and confirms every link and every checkpoint's own content hash -- a hand-edited or reordered checkpoint fails this check.

A previously committed checkpoint is never silently rewritten. If a genuine implementation or data defect is discovered later, the original checkpoint is preserved, a documented correction and a regression test are added, and a superseding checkpoint is appended with an explicit link to the corrected predecessor -- the same discipline M46/M47/M48/M50 already established for backtest corrections, applied here to a live audit trail instead.

## Initialization gate

Checked once, before the prospective starting state is accepted (`run_initialization_gate`, `scripts/monitor_momentum_prospective.py`):

1. Exact M52 historical baseline parity against the final committed M52 artifact -- reuses `scripts.research_momentum_risk_control.verify_baseline_parity` (M53's own parity verifier) unmodified.
2. M53 changes do not leak into the candidate -- `assert_frozen_candidate_config(top_n=15, vol_target_annualized=None)`.
3. Point-in-time universe selection / no-future-filing behavior -- structurally guaranteed by `select_filing_for_signal_date`, covered by M52's existing tests.
4. No pre-freeze paper transaction or P&L.
5. Base/stress ledger separation.
6. Whole shares and nonnegative cash.
7. No duplicate transactions/checkpoints.
8. Checkpoint hash-chain and idempotent rerun behavior.
9. Exact-date market-session handling -- covered by `tests/test_momentum_prospective_monitor.py`.
10. `strategy.enabled=false` and no broker-capable call path.

## Initial run

After the protocol commit, the monitor runs once through the latest completed regular session. `INITIALIZED_WAITING_FOR_FIRST_SIGNAL` or `NOT_DUE` is an acceptable and likely-correct result -- no transaction or performance figure is manufactured merely to populate this report. The exact first eligible future signal and execution dates, if determinable from the current calendar, are reported in the results commit below.

## Future checkpoint workflow (documented, not started)

1. Run the monitor after the next eligible execution/session data are complete.
2. Create a genuine checkpoint issue/branch/PR only when there is new evidence to record.
3. Commit exactly one append-only checkpoint.
4. Verify hashes, tests, integrity, and CI.
5. Merge only with explicit operator authorization.
6. Never create empty monthly activity solely for GitHub statistics.

## Safety

`strategy.enabled=false` throughout. Simulation only. No Robinhood or broker calls. No account, balance, buying-power, order, position, credential, token, or production-database access. No production strategy/config/universe/risk change. No pinned implementation-closure change. No live or paper broker order placement anywhere in this codebase. No notification claiming a trade should be executed. Every M43-M54 cache/artifact and unrelated untracked file is preserved. No `git clean`, destructive reset, or history rewriting.

## Publication audit

This report preserves: the human-defined protocol and decision boundary (the operator's Issue #362 work order, verbatim in scope); the LLM-assisted implementation/review role (the assistant implemented the monitor, wrote and ran its tests, and drafted this report under that specification); the freeze timestamp and commit, recorded before any prospective evidence is read; every defect and correction encountered; every null, negative, missing-data, or blocked checkpoint on equal footing with a positive one; and a clear, explicit distinction between M43-M54's historical backtest evidence and this milestone's genuinely prospective simulated evidence. No claim of formal preregistration is made -- this is, per the established M51-M54 convention, a time-stamped, version-controlled frozen protocol, not a preregistration filed outside Git.

---

## Initialization results

Generated from commit `aab1cd3be7e1e21d3b1003cd3dcc1578605361d1` (the frozen-protocol commit itself -- this is the initialization run's own freeze point). Checkpoint ledger: `docs/research/MOMENTUM_PROSPECTIVE_MONITOR_M55_CHECKPOINTS.json`, checkpoint index 0.

### Protocol freeze

- **Commit:** `aab1cd3be7e1e21d3b1003cd3dcc1578605361d1`
- **Timestamp (UTC):** `2026-08-24T00:02:30+00:00`
- **Timestamp (America/New_York calendar date):** `2026-08-23`

### Two genuine defects found and fixed during the initialization run

The first initialization attempt failed with `initialization gate all_ok=False`, on two independent items -- both fixed with regression tests before the run that actually produced this checkpoint:

1. **Master_dates-truncation bug (item 1, baseline parity):** the CLI's forward-looking `master_dates` array deliberately extends into 2027 so the run-decision logic can see future sessions -- but that same array was mistakenly reused for the parity recomputation. `month_end_signal_dates` picks the last session of each calendar month *present in the array it's given*, not the true calendar month-end; extending past M52's original fetch cutoff (`ALPACA_FETCH_END_EXCLUSIVE`, 2026-08-14) silently shifted "August 2026's month-end" from 2026-08-13 (M52's actual, fetch-window-truncated last signal) to 2026-08-31 (a date outside M52's own `EVALUATION_END`), which then dropped that signal from the recomputation entirely -- 68 signals and 825 intended orders recomputed against M52's committed 69 and 840. Fixed by building a second, separately-truncated `parity_master_dates` array (`WARMUP_TARGET_START` through `ALPACA_FETCH_END_EXCLUSIVE`, bit-for-bit identical to M52's own) used only for the parity recomputation, while the extended array remains reserved for the forward-looking decision logic. Regression test: `test_month_end_signal_dates_sensitive_to_master_dates_truncation`.
2. **Self-referential broker-call-pattern grep (item 10, safety):** `_no_broker_call_path_check` greps the monitor's own source for forbidden patterns (`robin_stocks`, `place_order`, etc.) -- but the denylist tuple itself, defined in that same source file, trivially contains every one of its own pattern strings, so the check always "found" all eight patterns inside its own definition line and always failed. Fixed by excluding any line that defines `_FORBIDDEN_SOURCE_PATTERNS` from the scan. Regression test: `test_no_broker_call_path_check_passes_on_actual_current_source` (must pass on the real file) and `test_no_broker_call_path_check_still_catches_a_genuine_hit` (must still catch a real violation in a synthetic file).

Neither defect involved the frozen candidate's economics, universe, or thresholds -- both were harness-level bugs in this milestone's own new code, caught by the initialization gate doing exactly what it was built to do.

### M52 baseline parity: PASS

58 scalar metric fields and 12 calendar-segment entries (base + stress) compared as exact `Decimal` values against the committed M52 result (commit `3d345ebca19c7159678aaad801cdb66299f9ba6b`) -- zero mismatches, using the corrected, identically-truncated `master_dates`.

### Initialization gate: all 10 items PASS

Item 1 (baseline parity) and item 10 (safety) detailed above; items 2-9 all passed on the first corrected run -- no M53 leakage (`top_n=15`, `vol_target_annualized=None`), point-in-time universe selection reused unmodified, no pre-freeze transactions (empty ledgers), base/stress ledger separation, whole shares/nonnegative cash by construction, no duplicate checkpoints (ledger was empty before this run), a valid (trivially empty) hash chain before the append, and session handling covered by the pure-module test suite.

### Initial run outcome

- **Status:** `INITIALIZED_WAITING_FOR_FIRST_SIGNAL`
- **Latest completed regular session as of the freeze:** `2026-08-21`
- **First eligible prospective signal date:** `2026-08-31` (August 2026's month-end session -- strictly after the 2026-08-23 freeze date, so it qualifies as the first prospective signal per the boundary rule)
- **First eligible execution date:** `2026-09-01` (the next regular session after the signal date)
- **Most recently known public N-PORT-P filing:** accession `0001004726-26-003726`, period ending 2026-03-31, filed 2026-05-22 (unchanged from M52's own last-known filing -- no newer filing has been publicly filed yet)
- **Prospective signal or transaction:** **none exists.** No transaction was manufactured to populate this report -- base and stress paper ledgers both hold their full, untouched $30,000 starting cash with zero positions and zero transactions, exactly as the boundary rule requires before the first eligible signal has occurred.

Idempotency was directly verified: rerunning the monitor immediately afterward reproduced the identical run decision and exited without writing a second checkpoint (`checkpoint count after rerun: 1`).

The next useful run of this monitor is not before `2026-09-01`. No further checkpoint is scheduled or started by this milestone -- per the future checkpoint workflow above, a genuine checkpoint issue/branch/PR is only opened when there is new evidence to record.
