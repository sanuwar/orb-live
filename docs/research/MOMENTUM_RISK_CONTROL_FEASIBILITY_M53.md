# M53: Bounded Cross-Sectional Momentum Risk-Control Feasibility

**Issue:** [#358](https://github.com/sanuwar/orb-live/issues/358)
**Branch:** `agent/m53-momentum-risk-control-feasibility`
**Depends on:** M52 (Issue #356, PR #357, merged `f205848340a5f0f3f6b2bb7fa2183998e754f723`) -- `POSITIVE_BUT_INSUFFICIENT`.

> **This section is committed BEFORE any empirical arm result is read**, exactly as Issue #358 requires. It records the frozen protocol only: baseline-parity requirement, the two arms' exact configuration, the deterministic selection rule, and the required outputs. This is a time-stamped, version-controlled frozen protocol, not a formal preregistration in the academic sense. The Stage-0/parity outcome, arm results, and final decision are added in a later, separate results commit, after the single frozen experiment is actually run from a clean implementation checkout.

## Purpose

M52 tested the one strategy M51 selected -- concentrated long-only cross-sectional equity momentum with a market/trend filter -- and classified it `POSITIVE_BUT_INSUFFICIENT`: directionally profitable in both cost scenarios (base 5bps $456.87/month, stress 10bps $439.23/month, both net-positive; 15.15% CAGR; 5 of 6 calendar segments positive) but short of the $500/month practical target primarily on drawdown depth (34.14% vs. a 25% ceiling) and single-month tail risk (worst month ~-25% vs. a -10% floor).

> Determine, in one bounded experiment, whether either of two predeclared, economically interpretable risk controls can preserve M52's positive momentum economics while bringing drawdown and monthly tail risk inside M52's own `TARGET_FEASIBLE` limits.

This is not a threshold sweep and must not become analysis paralysis. Research only. No activation, no broker writes.

## Frozen baseline: reproduce M52 exactly first

Bit-for-bit parity against the exact M52 baseline result is required before either new arm is examined (`verify_baseline_parity`, `scripts/research_momentum_risk_control.py`). The baseline reuses, unmodified:

- The same point-in-time IWB N-PORT-P filings and publication-date rule (`select_filing_for_signal_date`).
- The same resolved ticker snapshots and unresolved/ambiguous treatment -- never guessed, never substituted with a current successor.
- The same Alpaca SIP adjusted daily bars and evaluation window (2021-01-04 through 2026-08-13).
- The same liquidity rules, 12-1 momentum, monthly execution, costs (5bps base / 10bps stress), whole-share mechanics, SPY 200-session trend filter, and accounting.
- The same top-15 M52 baseline (`top_n=15`, the default).
- The same M52 metric definitions (`compute_portfolio_metrics`) and classification gates (`classify_cross_sectional_momentum`) -- reused unchanged, not redefined.
- The existing immutable M52 caches (`artifacts/research-cross-sectional-momentum/`: 27/27 N-PORT filings, OpenFIGI resolutions, Alpaca daily bars). The multi-hour SEC/OpenFIGI fetch is not repeated unless a material cache file is demonstrably missing or corrupt (`--fetch-missing` remains available for that case only).

Mechanically, the baseline is produced by calling `simulate_cross_sectional_momentum_portfolio` (`src/cross_sectional_momentum_research.py`) with no new arguments -- the exact same code path M52 itself used, not a separately maintained copy. `top_n` and `vol_target_annualized` were added to that function as optional parameters, both defaulting to exactly reproduce M52's original behavior when unset; this is the mechanism that makes "bit-for-bit parity" achievable by construction rather than by hand-matching two independent implementations.

## Frozen arm configuration (`src/momentum_risk_control_research.py`)

Run exactly two new arms -- no others.

### Arm A -- `BREADTH_30`

Portfolio breadth top 15 -> top 30 (`top_n=30`), equal-capital weighting across the selected names, every other M52 economic and mechanical rule identical. Tests whether reducing single-name concentration improves drawdown/tail behavior without destroying the momentum return.

### Arm B -- `MARKET_VOL_15`

M52's top-15 selection unchanged (`top_n=15`). Adds one lagged, monthly market-volatility exposure cap:

- On each monthly signal date, compute SPY's trailing 63-session standard deviation of daily adjusted-close returns, using observations ending on that signal date only (`compute_market_vol_exposure_multiplier`, population-stdev convention matching `annualized_volatility_pct` elsewhere in this codebase).
- Annualize with `sqrt(252)`.
- Exposure multiplier = `min(1.0, 0.15 / annualized_volatility)`.
- Applied at the next-session-open rebalance by scaling the equal-capital target (`target_dollars_per_position = portfolio_value * exposure_multiplier / target_count`); the remaining capital sits in cash.
- Never levers above 1.0; no volatility floor, alternate lookback, target sweep, or discretionary override.
- The existing SPY 200-session trend filter remains unchanged and takes precedence: when it is off, `selected` is already empty, so the multiplier has no effect and exposure is zero either way (verified by a dedicated synthetic test).

Tests an explicit, lagged market-risk throttle without changing stock selection.

No combined `BREADTH_30` + `MARKET_VOL_15` arm. No top 20/25, other volatility targets, stops, hedges, sector caps, or alternate lookbacks.

## Classification

M52's existing classification (`classify_cross_sectional_momentum`) is applied independently to Arm A and Arm B, unweakened and unredefined: `TARGET_FEASIBLE` / `POSITIVE_BUT_INSUFFICIENT` / `NOT_PROMISING` / `INSUFFICIENT_EVIDENCE`.

## Frozen selection rule (`select_m53_design`)

- Exactly one arm `TARGET_FEASIBLE` -> select it as the supported M53 design.
- Both `TARGET_FEASIBLE` -> select deterministically by higher stress-cost mean monthly dollars; tie -> lower base max drawdown; still tied -> lower turnover; a full tie at every level raises rather than guesses.
- Neither `TARGET_FEASIBLE` -> report both honestly, park this bounded risk-control branch. No hand-tuning, no automatic M54. "Best of two" alone is not sufficient for promotion.

Covered by dedicated synthetic tests (`tests/test_momentum_risk_control_research.py`) committed in this same pre-results commit, before any empirical arm result is read.

## Required outputs

For baseline and both arms, under 5bps and 10bps: mean/median fixed-$30K monthly profit; endpoint-marked net P&L; CAGR; max drawdown; worst month; profitable-month %; six calendar-segment results; Sharpe/Sortino/annualized volatility/downside deviation; best-symbol positive-contribution concentration; turnover, transaction count, costs, avg invested exposure, cash %; opportunity cost relative to the exact M52 baseline (`compute_opportunity_cost`); explicit pass/fail table for every M52 `TARGET_FEASIBLE` gate (from `classify_cross_sectional_momentum`'s own `gate_checks`); baseline-parity evidence and artifact/source hashes; point-in-time/no-lookahead/integrity audits (M52's own Stage-0 harness, `run_stage0_checks`, reused unmodified for baseline and both arms).

## Anti-analysis-paralysis

Two arms only, one empirical run each. No threshold, universe, target-volatility, lookback, portfolio-size, date, cost, or classifier sweep. No milestone extension because a result is close. Genuine implementation/data-integrity defects are fixed with regression tests and an honest rerun; frozen economic rules are never changed to chase a result. The full test suite runs only at meaningful stop gates and once on the final result state.

## Safety

`strategy.enabled=false` throughout. Research only -- no broker/Robinhood/account/order/position/credential/token/production-database access, no strategy activation, no production configuration/universe/risk-parameter/pinned-implementation-closure change, no deletion of existing research data or caches. No PR merge into `main` without explicit operator authorization.

## Publication audit

- **Human-specified protocol decisions:** the two arms (breadth-30, a 63-session/15%-target lagged market-vol exposure cap), the deterministic selection rule and its exact tiebreak order, the required-output list, and every safety/scope boundary above were specified by the operator's M53 work order, not chosen by the implementation.
- **LLM-assisted implementation/review work:** `simulate_cross_sectional_momentum_portfolio`'s `top_n`/`vol_target_annualized` extension, `compute_market_vol_exposure_multiplier`, `src/momentum_risk_control_research.py`'s selection/opportunity-cost logic, `scripts/research_momentum_risk_control.py`'s orchestration and baseline-parity verifier, and all synthetic tests in this commit were implemented and self-reviewed by the assistant against the frozen spec above.
- **Defects detected and corrections made:** recorded in the results commit, if any surface during the actual arm runs (per the anti-analysis-paralysis rule: genuine defects get regression tests and an honest rerun, never a change to frozen economic rules).
- **Confirmation:** no result-driven economic tuning occurred -- this section, the two arms' exact configuration, and the selection rule were committed and pushed before any empirical arm result was read.

---

## Results (added in the results commit)

_Pending -- see the results commit for baseline-parity evidence, Stage-0 outcomes, full base/stress metrics for baseline/Arm A/Arm B, the gate-by-gate `TARGET_FEASIBLE` pass/fail table, opportunity cost versus the exact M52 baseline, the final M53 decision, and the exact implementation/results-generating commit SHAs._
