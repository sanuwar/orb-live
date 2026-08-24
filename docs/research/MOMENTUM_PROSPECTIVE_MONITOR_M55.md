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

_Pending -- added in the results/checkpoint commit, after the monitor is run once from this clean commit. See that commit for the exact protocol-freeze timestamp (UTC and America/New_York), the M52 baseline-parity result, the initialization-gate outcome, the first eligible prospective signal and execution dates, and confirmation of whether any prospective signal or transaction exists yet (expected: none)._
