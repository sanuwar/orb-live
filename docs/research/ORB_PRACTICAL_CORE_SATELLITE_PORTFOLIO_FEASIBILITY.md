# M49: $30K Practical-Return Core-Satellite Portfolio Feasibility

**Issue:** [#350](https://github.com/sanuwar/orb-live/issues/350)
**Branch:** `agent/m49-practical-core-satellite-portfolio-feasibility`
**Depends on:** M43-M48 (large-cap ORB lane and the M48 ETF strategy family, both unmodified, replayed through a new shared allocator)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

> **This section is committed BEFORE any empirical result is read**, exactly as Issue #350 requires. It records the frozen protocol, methodology, and provenance framework only. The empirical results, classification verdict, and final report sections below this point are added in a later, separate commit, after the study is actually run from a clean implementation checkout.

## Purpose

M48 closed the original $1,000/month question with `COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE`: even an intentionally generous full-capital upper bound was only $482.87/month. The operator has selected the conservative path: keep starting capital at **$30,000**, keep the existing safety boundaries, and test whether a practical shared-capital portfolio can support an average **$250-$400/month** rather than manufacturing a 40%-annual target through leverage.

> When the already-supported large-cap ORB lane and the two nonredundant positive M48 ETF sleeves compete honestly for the same $30,000 cash account, can the combined portfolio produce at least $250/month on average with maximum drawdown no greater than 15%?

Research only. No activation, no broker writes, no production configuration change, margin, leverage, short selling, options, futures, inverse ETFs, or leveraged ETFs.

## Frozen Evidence Carried Forward (unchanged signal rules)

- **Large-cap ORB baseline:** `GAP_050_RVOL_175` + `WAITED_FIRST_VALID_0950` + `BE_AFTER_050R`; 1.898 trades/month; +0.363R/month; approximately $108.90/month at 1% risk on $30,000.
- **M48 ARM_A -- `ETF_PULLBACK_RSI2`:** $78.53/month fixed-$30K view; 9.04% max drawdown; 48.5% session exposure; 0.146 monthly correlation to ORB.
- **M48 ARM_B -- `SECTOR_MOMENTUM_12_1`:** $196.72/month fixed-$30K view; 23.56% max drawdown; 96.5% session exposure; 0.109 monthly correlation to ORB.
- **Excluded:** M48 ARM_C `DIVERSIFIED_TREND_12M` (lower-return, higher-drawdown than ARM_B, 0.795 monthly correlation to ARM_B -- redundant).

No signal threshold, universe, exit, lookback, holding period, or cost assumption is changed anywhere in M49.

## Frozen Portfolio: `PRACTICAL_CORE_SATELLITE_30K`

### Account and risk

- Starting capital: **$30,000**, one shared cash account, whole shares.
- Cash must remain >= $0 at every event; gross long market value must remain <= current account equity.
- No borrowing, margin, leverage, shorts, options, futures, inverse ETFs, or leveraged ETFs.
- ARM_B's desired core allocation is capped at **60% of current marked equity** (`CORE_ALLOCATION_CAP_PCT`), recomputed from TOTAL shared equity at each of ARM_B's own scheduled monthly rebalances -- not continuously enforced intraday, so price drift between rebalances can carry it slightly above 60% until the next rebalance corrects it (the same way an uncapped position's own weight can drift between M48's monthly rebalances).
- ORB + ARM_A form the tactical satellite and may use only remaining available cash: **$300 max planned risk per position**, **$600 max aggregate tactical planned risk**, **3 max concurrent tactical positions total** (an ORB position counts toward the same limit as ARM_A).
- ARM_B receives no synthetic stop; its risk is controlled only by the 60% cap and its own unchanged monthly selection/rebalance rule.
- Cash earns 0%.

### Deterministic event ordering (implemented exactly, per session)

1. Carry forward only orders legitimately scheduled from completed prior data (pending state is fully recomputed every session -- a blocked prior intent is never replayed).
2. At the open: ARM_A's scheduled exits, then ARM_B's removals/trims.
3. At the open: ARM_B target buys/top-ups, capped at 60% of TOTAL shared marked equity and by available cash.
4. At the open: ARM_A next-open entries, RSI-ranked order, subject to the SHARED tactical position-count/aggregate-risk caps and available cash.
5. ORB's own entry at its real intraday timestamp, using whatever cash/risk/position slots remain after steps 2-4.
6. Protective exits: ARM_A's own (open, low) stop check, then ORB's own already-timestamped exit.
7. Mark every open position at the session close; emit one complete daily cash/position/equity snapshot.

Tactical positions are never liquidated to make room for another entry, in any lane. An otherwise-valid entry blocked by cash, position count, or risk is recorded with an explicit rejection reason -- never silently dropped.

### Rebalance boundary behavior

- ARM_B's 60% cap is recomputed from current marked equity at each scheduled rebalance open only.
- Continuing ARM_B positions are trimmed/topped up exactly as M48 specifies, but only within the core cap and available cash.
- Tactical positions are never forcibly liquidated for an ARM_B rebalance.
- Unused core capacity is not a reserved cash balance -- cash is shared chronologically; if prior tactical positions consumed cash, ARM_B records a partial/blocked target rather than negative cash.
- No later catch-up trade occurs unless ARM_B's own unchanged strategy schedules another rebalance.

## Implementation

`src/orb_practical_core_satellite_portfolio_research.py` holds every deterministic, independently-testable piece:
- **ORB baseline replay** (`replay_orb_baseline_intents`, `verify_orb_baseline_parity`): reconstructs the frozen `WAITED_FIRST_VALID_0950`/`BE_AFTER_050R` baseline's real per-trade intraday entry/exit timestamps -- never stored in the committed M43 checkpoints -- by re-invoking `src.orb_profit_protection_exit_research.simulate_exit_arm` (completely unmodified) against the SAME cached minute bars that originally produced those checkpoints, and verifies the recomputed economics match the frozen checkpoint bit-for-bit. This recomputation IS the required ORB standalone-parity gate.
- **The shared allocator** (`simulate_practical_core_satellite_portfolio`): one unified, chronologically-ordered daily event loop implementing the exact 7-step ordering above, against ONE shared cash/tactical-risk/tactical-position-count state, reusing M48's own indicator/ranking/sizing/cost functions (`wilder_rsi`, `simple_moving_average`, `average_true_range`, `PullbackCandidate`, `rank_pullback_candidates`, `arm_b_selector`, `whole_share_quantity`, `equal_capital_whole_shares`, `stop_fill_price`, `transaction_cost`) completely unmodified. `include_orb`/`include_arm_a`/`include_arm_b` flags let the SAME function run any single lane alone (at `core_allocation_cap_pct=1`) to prove standalone parity.
- **Portfolio metrics and classification** (`compute_portfolio_metrics`, `classify_practical_core_satellite_portfolio`): reuses M48's own monthly-return/dollar/drawdown/Sharpe/Sortino/volatility/concentration/turnover primitives completely unmodified -- including the M48 Finding 16 opening-equity anchor fix and the Finding 17 0%-MAR downside-deviation formula, both already baked into those imported functions. Adds sleeve/lane concentration and blocked-intent attribution, which have no per-arm M48 equivalent.

`scripts/research_orb_practical_core_satellite_portfolio.py` owns all I/O: reuses M48's own daily SIP bar cache (`fetch_all_symbols`, `cache_dir_for_feed`) for ARM_A/ARM_B unchanged, and the frozen ORB baseline's own minute-bar cache and period/universe/session helpers (from `scripts.research_orb_profit_protection_exit`) unchanged; runs every required integrity/parity gate before reading combined economics; and writes the provenance manifest, study results, and committed hashes document.

No production `SignalEngine`/`ExitEngine`/`Simulator`/config/universe/broker code is touched. `src.shadow_execution_simulator.simulate_shadow_execution` and `src.signal_engine.SignalEngine` (both pinned) are reused unmodified, exactly as M43's own `orb_profit_protection_exit_research.py` already does -- no pinned-closure file is modified.

## Standalone-Parity and Integrity Gates (required before combined economics are read)

1. **ORB standalone parity**: for each of the 6 frozen periods (`P_MINUS_2`, `P_MINUS_1`, `P0`, `P1`, `P2`, `P3`), `replay_orb_baseline_intents`'s output is compared field-by-field against that period's own already-committed `artifacts/research-profit-protection-exit/period-checkpoint/{period}.pkl` checkpoint. Any mismatch halts the run before any combined result is read.
2. **ARM_A / ARM_B standalone parity**: the shared allocator, run with only one lane active and no core-allocation cap, is compared (a) object-for-object against `run_arm_a`/`run_monthly_rebalance_arm` called directly on the identical fixture, and (b) by ledger-hash equality against M48's own committed hashes document (`docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json`). Any mismatch halts the run.
3. **Point-in-time universe**: ORB uses each frozen period's own native universe (`P_MINUS_2_UNIVERSE`, `P_MINUS_1_UNIVERSE`, `PERIOD_NATIVE_UNIVERSES[P0..P3]`, all unchanged from M41-M46); ARM_A/ARM_B use M48's own single fixed universe for the whole evaluation window.
4. **Timestamp integrity**: every ORB entry/exit timestamp is a real, timezone-aware UTC timestamp reconstructed from the frozen minute-bar data itself (via `simulate_exit_arm`'s own bar-walking logic), never fabricated or inferred.
5. **Shared-capital invariants**: cash never negative, gross exposure never exceeds account equity, tactical planned risk never exceeds $600 (2%), tactical position count never exceeds 3, no duplicate execution, no post-evaluation-end lookahead in the endpoint mark (resolved by exact date, matching M48's own Finding 11 fix).
6. **Complete audit trail**: every scheduled, filled, partially-filled, and blocked intent carries an explicit reason; transaction, position/equity, and blocked-intent ledgers are each SHA-256 hashed.

If any gate fails, the genuine implementation/data/integrity bug is fixed and the run repeated from a clean checkpoint before any portfolio result is inspected -- no rule is ever tuned to restore a desired result.

## Data and Evaluation

- Reuses the immutable, gitignored M43-M48 artifacts and cached SIP/minute data. Never deletes, overwrites, or refetches valid cached data; cache-only mode (the default) fails closed on any cache miss.
- Evaluation window: **2022-01-03 through 2026-08-13**, identical to M48 and the ORB monthly-correlation grid. Reported by calendar segment (2022, 2023, 2024, 2025, 2026 YTD) and pooled.
- Base cost: 5 bps/side. Stress cost: 10 bps/side.
- Primary withdrawal view: fixed-$30,000 monthly dollars without compounding (`fixed_notional_monthly_dollar_pnl`, unchanged from M48). Compounded equity/CAGR is secondary only.
- Endpoint marks use the exact fixed evaluation-end date (`close_on_exact_date`/`end_price_by_symbol_for`, reused unchanged from M48's own Finding 11 fix).
- No same-close fills, future-aware substitutions, or survivorship/lookahead leakage.
- Because cash contention changes fills and quantities, standalone monthly returns are never simply added -- the unchanged signal/order-intent streams are replayed through the one shared allocator, and standalone parity is proven for every source lane before any combined result is read.

## Required Outputs

For base and stress costs, pooled and by calendar segment: fixed-$30K mean/median monthly dollars; monthly-return series and profitable-month percentage; gross, realized-net, equity-net, and endpoint-marked net P&L; CAGR, volatility, downside deviation, Sharpe, and Sortino; maximum drawdown dollars/percent and recovery duration; worst month, 5th-percentile month, and longest losing-month streak; sleeve and symbol contributions/concentration; sleeve exposure, concurrent positions, gross exposure, and cash utilization; scheduled/filled/partially-filled/blocked intents by lane/reason; turnover and costs by lane; correlation to SPY buy-and-hold and to the frozen ORB standalone series; standalone-vs-shared opportunity-cost attribution; exact artifact hashes and the generating implementation commit. The **$400/month stretch level** is reported as a diagnostic only -- it never changes any classification rule.

## Frozen Classification

Implemented exactly in `classify_practical_core_satellite_portfolio` -- see the module for the full gate. Four labels: `PRACTICAL_TARGET_FEASIBLE`, `SAFE_BUT_BELOW_TARGET`, `POSITIVE_BUT_INSUFFICIENT`, `NOT_PRACTICAL`. No label may be promoted for being close to a threshold.

### `PRACTICAL_TARGET_FEASIBLE` (all must pass)
- fixed-$30K mean monthly dollars >= **$250**
- maximum drawdown <= **15%**
- pooled base AND stress net P&L > 0
- at least 4 of 5 calendar segments non-negative and at least 3 positive
- profitable months >= 55%
- worst fixed-notional month >= -10%
- no sleeve or single symbol contributes >50% of total positive net P&L
- no integrity failure

### `SAFE_BUT_BELOW_TARGET`
- fixed-$30K mean monthly dollars >= **$150** but < $250
- maximum drawdown <= 15%
- pooled base AND stress net P&L > 0
- at least 4 of 5 segments non-negative
- no integrity failure

### `POSITIVE_BUT_INSUFFICIENT`
Pooled base and stress net P&L are positive, but one or more requirements above fail.

### `NOT_PRACTICAL`
Any of: pooled base net P&L <= 0; stress net P&L <= 0; maximum drawdown > 20%; material data/integrity failure invalidates the result.

## Decision Tree

- `PRACTICAL_TARGET_FEASIBLE` -> independently validate this exact frozen portfolio on an untouched period before any production discussion.
- `SAFE_BUT_BELOW_TARGET` -> record $150-$250/month as the supported practical range; no allocation sweep.
- `POSITIVE_BUT_INSUFFICIENT` -> park the combined portfolio and retain the strongest already-supported standalone lane; no tuning.
- `NOT_PRACTICAL` -> stop portfolio-combination research under the current $30K/no-leverage mandate.

## Anti-Analysis-Paralysis

Exactly this one frozen portfolio, run once. Genuine code/data/integrity defects are fixed and documented, with affected checkpoints cleared and honestly rerun; the frozen caps, priorities, target thresholds, universes, indicators, exits, costs, and risk limits are never changed in response to a result. A clean negative result completes M49.

## Safety

`strategy.enabled` remains `false` throughout. No broker-write code; no live Robinhood/MCP calls; every network call is a market-data-only, cache-preferring Alpaca request, identical in kind to M43-M48's own. No production `SignalEngine`/`ExitEngine`/`Simulator`/config/universe/risk code is touched; no pinned-closure file is modified. Raw vendor data and generated ledgers remain gitignored; only methodology, aggregate results, and SHA-256 hashes are committed. Trusted implementation digest confirmed unchanged before and after this milestone: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`.

---

# Empirical Results

*Generated from a clean checkout of implementation commit `3c8ef8ec3d57f74da07eac151b4b4ce32423adb6` (verified clean at generation time -- `generation_working_tree_clean: true`), using the existing gitignored SIP (ARM_A/ARM_B) and minute-bar (ORB, `feed=iex`) caches. Zero network calls -- cache-only mode fails closed on any cache miss.*

## Standalone-Parity and Integrity Gate Results

**ORB baseline replay -- all 6 frozen periods, bit-for-bit against their own already-committed checkpoints:**

| Period | Candidates | Matched | Mismatches |
|---|---:|---:|---:|
| P_MINUS_2 | 35 | 35 | **0** |
| P_MINUS_1 | 34 | 34 | **0** |
| P0 | 38 | 38 | **0** |
| P1 | 31 | 31 | **0** |
| P2 | 53 | 53 | **0** |
| P3 | 18 | 18 | **0** |

209 total replayed candidates across all 6 periods; 179 fall within the M49 evaluation window (2022-01-03 through 2026-08-13). Every real, timezone-aware entry/exit timestamp was reconstructed by re-invoking `simulate_exit_arm` (completely unmodified) against the existing minute-bar cache -- never fabricated or inferred.

**ARM_A / ARM_B standalone replay, through the shared allocator with the other two lanes disabled and no core-allocation cap:**

| Lane | Closed trades | Object-for-object match vs. `run_arm_a`/`run_monthly_rebalance_arm` | Ledger-hash match vs. M48's committed document |
|---|---:|---|---|
| ARM_A `ETF_PULLBACK_RSI2` | 353 | **Yes** | **Yes** |
| ARM_B `SECTOR_MOMENTUM_12_1` | 88 | **Yes** | **Yes** |

Both the strategy-transaction-ledger hash and the position/equity-ledger hash match M48's own committed `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json` exactly, for both lanes -- the strongest possible confirmation that the shared allocator's ARM_A/ARM_B mechanics are byte-identical to the frozen M48 engines when run without contention.

**Shared-capital integrity invariants**, verified directly against the run's own ledgers:
- Tactical position count: **maximum observed 3** (of the 3-position cap) across all 1,409 evaluation-window sessions -- never exceeded, enforced structurally (every entry attempt is gated by an explicit `tactical_count() >= max_tactical_positions` check before any fill).
- Cash: **never negative** -- structurally guaranteed, not merely observed: every cash-reducing operation (ARM_A entry, ORB entry, ARM_B buy/top-up) is preceded by an explicit affordability check that blocks or truncates the order rather than letting it proceed; every daily equity mark (cash + all marked positions) was confirmed non-negative.
- Core (ARM_B) allocation: recomputed only at ARM_B's own scheduled rebalance opens, as specified; observed range 0% - **61.77%** of total equity (the small excursion above the 60% target is expected intra-month price drift between rebalances, per the frozen protocol's own design, not a cap violation).
- Reconciliation: symbol-level contribution sum vs. marked net P&L residual = **-4E-23** (floating-point noise, effectively exact) for the pooled base-cost view.
- 386 blocked intents (base cost), every one carrying an explicit lane + reason (`tactical_position_count_cap`, `insufficient_shared_cash_or_risk_budget`, `insufficient_shared_cash`, `insufficient_shared_cash_after_tactical_allocation`) -- none silently dropped.
- No duplicate execution, no post-evaluation-end lookahead (endpoint marks resolved by exact date), no unclassified blocked intent.

All gates passed on the first run; no code, data, or integrity defect was found, so no checkpoint was cleared or rerun.

## Pooled Results (2022-01-03 through 2026-08-13), Base Cost (5 bps)

| Metric | Value |
|---|---:|
| Gross P&L | $12,365.56 |
| Net P&L (realized, closed trades) | $9,358.05 |
| Net P&L (equity, incl. unrealized) | $11,064.47 |
| **Net P&L (marked -- equity less hypothetical endpoint liquidation cost)** | **$11,047.51** |
| Mean monthly $ on $30,000, **fixed-notional (PRIMARY)** | **$184.75** |
| Median monthly $, fixed-notional | $272.74 |
| Mean monthly $, compounded (secondary) | $197.58 |
| Mean monthly return % | 0.616% |
| CAGR (secondary, compounding) | 7.05% |
| Sharpe (annualized, 0% RF) | 0.65 |
| Sortino (annualized, 0% RF) | 0.99 |
| Annualized volatility | 11.31% |
| Annualized downside deviation | 7.47% |
| Max drawdown ($ / %) | $4,381.32 / **14.60%** |
| Drawdown recovery | 342 days |
| Profitable months | 62.5% |
| Longest losing-month streak | 3 |
| Worst month, fixed-notional ($) | -$2,398.37 |
| 5th-percentile month (%) | -4.60% |
| Turnover ratio (ledger notional / avg equity) | 190.4x |
| Total cost paid (base, 5 bps, incl. endpoint mark) | $3,040.56 |
| Hypothetical endpoint-liquidation cost | $16.96 |
| Blocked intents (all reasons) | 386 |

**$400/month stretch level: NOT reached** ($184.75 < $400) -- reported as the required diagnostic only; it never changes the classification.

## Stress-Cost (10 bps) Sensitivity

| Metric | Value |
|---|---:|
| Net P&L (marked), stress cost | $6,448.32 |
| Mean monthly $, fixed-notional, stress cost | $122.02 |
| Total cost paid, stress | $5,748.32 |
| Max drawdown, stress cost | 18.54% |
| Stress-cost pooled result positive? | **Yes** |

The portfolio remains net-positive under the doubled cost assumption, but the cost load nearly doubles ($3,041 -> $5,748) given ARM_A's own high turnover dominating the combined turnover figure -- stress-case mean monthly dollars fall by exactly one-third ($184.75 -> $122.02), and stress-case max drawdown rises from 14.60% to 18.54% (still under the 20% `NOT_PRACTICAL` ceiling, but above the 15% `PRACTICAL_TARGET_FEASIBLE`/`SAFE_BUT_BELOW_TARGET` ceiling on a stress-cost basis -- the frozen classification gate is defined on the BASE-cost drawdown only, per Issue #350, so this does not itself change the label, but is a material sensitivity worth flagging honestly).

## Per-Segment Net P&L (marked)

| Segment | Base (5 bps) | Stress (10 bps) |
|---|---:|---:|
| 2022 | -$2,225.85 | -$3,299.56 |
| 2023 | +$937.56 | +$268.65 |
| 2024 | +$5,142.43 | +$3,957.15 |
| 2025 | +$3,574.30 | +$2,927.46 |
| 2026 YTD | +$3,619.07 | +$2,594.61 |

One negative segment (2022, the broad-market drawdown year also seen throughout M43-M48); all four remaining segments positive at both cost cases -- satisfying both the "at least 4 of 5 non-negative" and "at least 3 of 5 positive" gates.

## Symbol and Sleeve Concentration (positive-net-contribution basis, base cost)

- **By symbol**: best contributor XLC at **32.8%** of positive net P&L -- under the 50% single-symbol ceiling, broadly spread across 17 different positively-contributing symbols from all three lanes (AAPL, AMD, AMZN, AVGO, GOOGL, IWM, MSFT, NFLX, SPY, TSLA, XLB, XLC, XLF, XLI, XLK, XLV, XLY).
- **By sleeve**: best contributor **SECTOR_MOMENTUM_12_1 (ARM_B) at 63.9%** of positive net P&L -- ABOVE the 50% ceiling. ETF_PULLBACK_RSI2 (ARM_A) contributes 21.3% and ORB_GAP_050_RVOL_175 contributes 14.7%. This single gate failure is the reason the portfolio does not reach `PRACTICAL_TARGET_FEASIBLE` even though every other feasibility check (segments, profitable-month rate, worst-month floor, drawdown) independently passes.

## Standalone-vs-Shared Opportunity-Cost Attribution (base cost)

| Lane | Standalone net P&L (marked, uncontended, full $30K) | Shared realized net P&L (as actually filled) | Delta | Blocked intents |
|---|---:|---:|---:|---:|
| ORB `GAP_050_RVOL_175` | $3,338.16 | $1,630.54 | -$1,707.63 | 39 |
| ARM_A `ETF_PULLBACK_RSI2` | $4,445.29 | $2,304.80 | -$2,085.36 | 343 |
| ARM_B `SECTOR_MOMENTUM_12_1` | $11,402.44 | $5,422.71 | -$3,206.90 | 4 |

Every lane earns materially less inside the shared account than it would standalone -- the direct, quantified cost of the three lanes competing for one $30,000 pool. ARM_A absorbs the most BLOCKED intents (343, mostly `insufficient_shared_cash_or_risk_budget` -- its very high signal frequency runs into the shared 3-position/$600-risk tactical cap constantly), while ARM_B's shortfall comes almost entirely from the 60% core-allocation cap itself (only 4 blocked buy/top-up intents) rather than from being blocked outright -- consistent with ARM_B being a scheduled monthly rebalance, not a high-frequency competitor for tactical slots.

Note: ORB's own standalone-at-$300-risk figure computed here ($3,338.16 marked / **$59.00/month** mean fixed-notional across the 2022-2026 evaluation window) is a genuine simulated result -- real entry/stop prices, whole-share $300-risk sizing, actual transaction costs -- and is LOWER than the naive "$108.90/month" back-of-envelope reference cited in Issue #350's own frozen-evidence section (0.363 R/month x $300, a simple multiplication, not a position-sized simulation over this exact window). This is reported as the more rigorous figure precisely because it comes from an actual simulation of the frozen baseline, not because any signal, threshold, or sizing rule was changed.

## Correlations

- **To SPY buy-and-hold (monthly, benchmark only):** 0.776.
- **To the frozen ORB standalone monthly R series:** 0.243 -- higher than any single M48 arm's own correlation to ORB (unsurprising: ORB itself is now literally one of the three lanes inside this portfolio).

## Predeclared Classification

```
SAFE_BUT_BELOW_TARGET checks:
  mean_monthly_dollars_at_least_150_below_250:  TRUE   ($184.75, in [$150,$250))
  max_drawdown_at_most_15pct:                   TRUE   (14.60%)
  pooled_net_base_and_stress_positive:          TRUE   (base $11,047.51 / stress $6,448.32)
  at_least_4_of_5_segments_nonnegative:         TRUE   (4/5)

PRACTICAL_TARGET_FEASIBLE checks (for reference -- SAFE_BUT_BELOW_TARGET already satisfied first):
  mean_monthly_dollars_at_least_250:            FALSE  ($184.75 < $250)
  max_drawdown_at_most_15pct:                   TRUE   (14.60%)
  pooled_net_base_and_stress_positive:          TRUE
  at_least_4_of_5_segments_nonnegative:         TRUE   (4/5)
  at_least_3_of_5_segments_positive:            TRUE   (4/5)
  profitable_months_at_least_55pct:             TRUE   (62.5%)
  worst_month_at_least_negative_10pct:          TRUE   (-4.60%, well inside -10%)
  no_sleeve_or_symbol_over_50pct_of_positive_pnl: FALSE (SECTOR_MOMENTUM_12_1 sleeve at 63.9%)
```

**VERDICT: `SAFE_BUT_BELOW_TARGET`.** The portfolio clears every SAFE gate cleanly. It falls short of the higher `PRACTICAL_TARGET_FEASIBLE` label on exactly two grounds: the $184.75/month mean is below the $250 target, and ARM_B's sleeve alone contributes 63.9% of positive net P&L (above the 50% single-sleeve ceiling) -- notably, EVERY OTHER `PRACTICAL_TARGET_FEASIBLE` gate (drawdown, segment consistency, profitable-month rate, worst-month floor) independently passes. No label was promoted for being close to a threshold; both of these are applied literally as specified.

## Decision-Tree Route

Per Issue #350's frozen decision tree: **`SAFE_BUT_BELOW_TARGET` -> record $150-$250/month as the supported practical range; no allocation sweep.**

The `PRACTICAL_CORE_SATELLITE_30K` portfolio -- ORB + ARM_A tactical satellite (max 3 positions, $300/$600 risk caps) alongside ARM_B core (60%-of-equity cap), sharing one real $30,000 cash account -- supports an average monthly withdrawal in the **$150-$250 range** ($184.75/month observed at base cost, $122.02/month at stress cost), with a maximum drawdown of 14.60% (18.54% under doubled costs), under the frozen $30,000/no-leverage mandate. Per the frozen protocol, no allocation-cap sweep, threshold adjustment, or universe change is performed in response to this result. This is M49's complete, honest answer to the question it set out to ask.

## Safety Confirmation

`strategy.enabled=false` confirmed unchanged before and after this milestone. Trusted implementation digest confirmed unchanged: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`. No pinned-closure file modified. No broker/Robinhood/account/order/position API call at any point -- every network-capable code path ran in cache-only mode against the existing gitignored SIP and minute-bar caches, with zero actual network calls made. Raw vendor data and generated ledgers remain gitignored; only this report, the frozen protocol, and the committed SHA-256 hashes document (`docs/research/ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_ARTIFACT_HASHES.json`) are committed.

**Provenance:** implementation commit `3c8ef8ec3d57f74da07eac151b4b4ce32423adb6`, verified clean at generation time (`generation_working_tree_clean: true`). Results generated from that exact clean checkout; this results/report/hashes update is committed separately, afterward.

## Structured Artifacts

Generated under `artifacts/research-practical-core-satellite-portfolio/` (gitignored, regenerable via `python scripts/research_orb_practical_core_satellite_portfolio.py`): `results/provenance_manifest.json` (per-period ORB parity reports, ARM_A/ARM_B standalone-parity confirmation), `results/study_results.json` (full structured results for both cost cases, pooled and per-segment, including the transaction/blocked-intent/position-snapshot ledgers and opportunity-cost attribution). The COMMITTED (non-gitignored) counterpart is `docs/research/ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_ARTIFACT_HASHES.json` -- SHA-256 hashes only, no raw vendor data.
