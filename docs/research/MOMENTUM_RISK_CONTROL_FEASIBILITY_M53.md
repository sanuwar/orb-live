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

## Results

Generated from commit `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865` (clean working tree, all three source files' SHA-256 hashes recorded in `artifacts/research-momentum-risk-control/results/m53_result.json` / `docs/research/MOMENTUM_RISK_CONTROL_ARTIFACT_HASHES.json`). All three runs (baseline, Arm A, Arm B) used the existing immutable M52 caches only -- no SEC/OpenFIGI/Alpaca fetch was repeated.

### Baseline parity: PASS

58 scalar metric fields and 12 calendar-segment entries (base + stress, 6 segments each) compared field-by-field as exact `Decimal` values against the committed M52 result (commit `3d345ebca19c7159678aaad801cdb66299f9ba6b`) -- zero mismatches. Bit-for-bit parity confirmed before either new arm was examined, as required.

### Stage-0 integrity: PASS for baseline and both arms

0 duplicate transactions and 0 unclassified blocked intents in all three runs (baseline, Arm A, Arm B). No implementation or data-integrity defect surfaced during this run -- unlike M52's own generation (which required several pipeline/harness fixes), M53's empirical run completed cleanly on the first attempt, reusing M52's already-hardened caches and harness unchanged.

### Arm A -- `BREADTH_30` (top_n=30): `POSITIVE_BUT_INSUFFICIENT`

| Metric (base 5bps) | Value | vs. M52 baseline |
|---|---|---|
| Mean monthly $ | $328.24 | -$128.63 |
| Median monthly $ | $45.06 | -- |
| Endpoint-marked net P&L | $23,611.02 | -$11,704.84 |
| CAGR | 11.22% | -3.94pp |
| Sharpe / Sortino | 0.58 / 0.95 | -- |
| Annualized volatility / downside deviation | 22.69% / 13.86% | -- |
| **Max drawdown** | **27.25%** | **-6.89pp (improved)** |
| Worst month | -20.79% | +4.29pp (improved) |
| Profitable months | 50.0% | -1.47pp |
| Best-symbol contribution share | 6.70% | -2.39pp |
| Turnover | $1,559,078.65 | +$49,366 |
| Avg invested positions | 24.13 / 30 max | -- |

Stress (10bps): mean $320.47/month, endpoint net P&L $22,659.86, max drawdown 27.55% -- still net-positive.

Segments (base): 2021 +, **2022 -**, **2023 -**, 2024 +, 2025 +, 2026 YTD + -- only **4 of 6** positive (M52 baseline had 5 of 6; 2023 flips negative under breadth-30).

**Gate failures (`TARGET_FEASIBLE`):** base mean $ ($328.24 < $500), base max drawdown (27.25% > 25% -- close, but still fails), segments positive (4 < 5), profitable months (50.0% < 55%), worst month (-20.79% < -10%).

**Interpretation:** breadth reduction genuinely helped drawdown and worst-month severity -- both improved meaningfully versus the concentrated M52 baseline -- but diluting the top-15 momentum signal to top-30 also diluted the return, both in absolute dollars and in calendar-segment consistency (2023 flips from marginally positive to negative). The risk reduction did not come for free.

### Arm B -- `MARKET_VOL_15` (top_n=15, 63-session/15%-target exposure cap): `POSITIVE_BUT_INSUFFICIENT`

| Metric (base 5bps) | Value | vs. M52 baseline |
|---|---|---|
| Mean monthly $ | $456.72 | -$0.15 (essentially unchanged) |
| Median monthly $ | $92.73 | -- |
| Endpoint-marked net P&L | $36,148.94 | +$833.09 |
| CAGR | 15.38% | +0.23pp |
| Sharpe / Sortino | 0.64 / 1.13 | -- |
| Annualized volatility / downside deviation | 28.68% / 16.20% | -- |
| Max drawdown | 34.29% | +0.15pp (essentially unchanged) |
| Worst month | -25.13% | -0.05pp (essentially unchanged) |
| Profitable months | 51.47% | unchanged |
| Best-symbol contribution share | 9.45% | +0.36pp |
| Turnover | $1,490,555.16 | -$19,157 |
| Avg invested positions | 12.07 / 15 max | -- |

Stress (10bps): mean $451.42/month, endpoint net P&L $35,336.00, max drawdown 34.14% -- both net-positive and both marginally better than the M52 baseline's stress case.

Segments (base): identical pattern to the M52 baseline -- 5 of 6 positive (only 2022 negative).

**Gate failures (`TARGET_FEASIBLE`):** base mean $ ($456.72 < $500), base max drawdown (34.29% > 25%), profitable months (51.47% < 55%), worst month (-25.13% < -10%) -- essentially the same failure profile as the unmodified M52 baseline.

**Why the cap barely moved the headline numbers -- diagnosed, not a defect:** a direct check of the exposure multiplier across all 69 real signal dates confirms the cap genuinely engaged during real market stress -- it fell as low as 0.53 in mid-2022 and 0.45 in April-June 2025, both real, sustained volatility episodes. But the strategy's single worst drawdown and worst month (M52's own $76,898.81-to-$50,645.61 decline, 2026-06-22 to 2026-07-29) developed quickly, and the trailing 63-session realized-volatility measure had not yet risen enough by the signal dates leading into it to meaningfully cut exposure. This is a known, structural limitation of lagged volatility targeting -- it reduces exposure only after realized volatility has already risen, so it cannot preempt a fast-onset drawdown -- not an implementation defect. Stage-0 integrity (0 duplicates, 0 unclassified blocked intents) and the diagnostic multiplier trace both confirm the mechanism itself works exactly as specified.

### M53 decision: `PARKED_NO_CREDIBLE_RISK_CONTROL`

Neither arm reached `TARGET_FEASIBLE`, so per the frozen selection rule no arm is promoted -- "best of two" is not sufficient. This bounded risk-control branch is parked. No hand-tuning was applied to either arm, and no automatic M54 follows.

Reported honestly: Arm A demonstrates that reducing concentration measurably improves risk (drawdown, worst month) at a real cost to return and segment consistency; Arm B demonstrates that a lagged market-volatility throttle preserves M52's return profile almost exactly but does not meaningfully reduce its worst-case risk, because the realized drawdown was fast-onset rather than a sustained high-volatility regime the lookback could detect in time. Both are genuine, useful negative/mixed findings about this specific momentum strategy's risk structure, not implementation failures.
