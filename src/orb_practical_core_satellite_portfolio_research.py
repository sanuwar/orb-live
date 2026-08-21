"""M49: $30K practical-return core-satellite portfolio feasibility
(Issue #350).

One frozen portfolio, `PRACTICAL_CORE_SATELLITE_30K`: the already-supported
large-cap ORB baseline (`GAP_050_RVOL_175` + `WAITED_FIRST_VALID_0950` +
`BE_AFTER_050R`) and M48's two nonredundant positive ETF sleeves
(`ETF_PULLBACK_RSI2` = ARM_A, `SECTOR_MOMENTUM_12_1` = ARM_B) compete
honestly for ONE shared $30,000 cash account, replacing each lane's own
M43/M48 dedicated-$30,000 execution with a single shared allocator. No
entry signal, universe, exit rule, lookback, holding period, or cost
assumption differs from the frozen M43/M48 baselines -- only the
capital/cash/position-count/risk-cap allocation across lanes is new here.

This module holds every deterministic, independently-testable piece:
- Reconstructing the frozen ORB baseline's real per-trade intraday
  entry/exit timestamps (never stored in the committed M43 checkpoints)
  by re-invoking `src.orb_profit_protection_exit_research.simulate_exit_arm`
  -- completely unmodified -- against the SAME cached minute bars that
  originally produced those checkpoints, and verifying the recomputed
  economics match the frozen checkpoint bit-for-bit (this IS the required
  ORB standalone-parity gate, not a separate assertion).
- ARM_A/ARM_B signal-intent generation, reusing
  `src.orb_capital_target_strategy_family_research`'s indicator/ranking/
  selector functions completely unmodified -- but emitting INTENTS (what
  each lane would signal, before any cash/position/risk contention is
  applied) rather than self-executing against a dedicated capital pool.
- The shared allocator: one unified, chronologically-ordered event loop
  (`simulate_practical_core_satellite_portfolio`) that processes ORB +
  ARM_A + ARM_B intents against ONE shared cash/tactical-risk/tactical-
  position-count state, per Issue #350's exact 7-step ordering, recording
  a complete audit trail of every scheduled/filled/partial/blocked intent
  with an explicit reason.

`scripts/research_orb_practical_core_satellite_portfolio.py` owns all I/O
(minute/daily-bar fetch, caching, provenance, report generation) and
orchestrates this module; nothing here performs a network call or reads a
clock.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.market_data_canonical import MarketBar
from src.orb_candidate_selection_timing_research import CONFIG_ENTRY, CONFIG_LABEL, early_pool, enrich_with_decision_time
from src.orb_gap_rvol_sensitivity_research import ComboEvaluation, InputsTable, evaluate_combo, precompute_inputs_table, sensitivity_config
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_profit_protection_exit_research import (
    ARM_BE_AFTER_050R,
    ExitArmOutcome,
    ProfitProtectionExitError,
    _outcome_dict,
    _waited_shadow_decision,
    simulate_exit_arm,
)

from src.orb_capital_target_strategy_family_research import (
    ARM_A,
    ARM_B,
    ATR_PERIOD_A,
    ATR_STOP_MULTIPLE_A,
    BASE_COST_BPS,
    EXIT_SMA_PERIOD_A,
    MAX_AGGREGATE_RISK,
    MAX_CONCURRENT_POSITIONS_A,
    MAX_HOLDING_SESSIONS_A,
    MAX_RISK_PER_POSITION,
    MOMENTUM_LOOKBACK_SESSIONS,
    MOMENTUM_SKIP_SESSIONS,
    RSI_ENTRY_THRESHOLD_A,
    RSI_PERIOD_A,
    STARTING_CAPITAL,
    STRESS_COST_BPS,
    TOP_N_B,
    TREND_SMA_PERIOD,
    UNIVERSE_A,
    UNIVERSE_B,
    DailySeries,
    PullbackCandidate,
    arm_b_selector,
    average_true_range,
    equal_capital_whole_shares,
    momentum_12_1,
    month_end_indices,
    rank_pullback_candidates,
    require_full_alignment,
    simple_moving_average,
    stop_fill_price,
    transaction_cost,
    whole_share_quantity,
    wilder_rsi,
    CALENDAR_SEGMENTS,
    annualized_downside_deviation_pct,
    annualized_volatility_pct,
    cagr,
    fixed_notional_monthly_dollar_pnl,
    max_drawdown,
    monthly_dollar_pnl,
    monthly_return_pct,
    positive_contribution_concentration,
    profitable_month_stats,
    segment_label,
    sharpe_ratio,
    slice_to_window,
    sortino_ratio,
    turnover_and_costs,
    worst_and_percentile_month,
)

__all__ = [
    "PracticalCoreSatelliteError",
    "ARM_ORB", "ARM_A", "ARM_B",
    "CORE_ALLOCATION_CAP_PCT", "TACTICAL_MAX_POSITIONS",
    "STARTING_CAPITAL", "MAX_RISK_PER_POSITION", "MAX_AGGREGATE_RISK", "MAX_CONCURRENT_POSITIONS_A",
    "BASE_COST_BPS", "STRESS_COST_BPS",
    "UNIVERSE_A", "UNIVERSE_B",
    "OrbTradeIntent", "replay_orb_baseline_intents", "verify_orb_baseline_parity",
    "TacticalPosition", "CoreHolding", "ClosedPortfolioTrade", "PortfolioTransaction",
    "BlockedIntent", "PortfolioPositionSnapshot", "CoreRebalanceEvent", "PortfolioSimulationResult",
    "simulate_practical_core_satellite_portfolio",
    "portfolio_end_mark_transactions", "compute_portfolio_metrics",
    "CLASSIFICATION_PRACTICAL_TARGET_FEASIBLE", "CLASSIFICATION_SAFE_BUT_BELOW_TARGET",
    "CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT", "CLASSIFICATION_NOT_PRACTICAL",
    "PRACTICAL_TARGET_MONTHLY_DOLLARS", "PRACTICAL_STRETCH_MONTHLY_DOLLARS", "SAFE_MIN_MONTHLY_DOLLARS",
    "PRACTICAL_MAX_DRAWDOWN_PCT", "NOT_PRACTICAL_MAX_DRAWDOWN_PCT", "PRACTICAL_MIN_PROFITABLE_MONTH_PCT",
    "PRACTICAL_WORST_MONTH_FLOOR_PCT", "MAX_SINGLE_CONTRIBUTOR_POSITIVE_SHARE", "ALL_FIVE_CALENDAR_SEGMENTS",
    "PRACTICAL_MIN_NONNEGATIVE_SEGMENTS", "PRACTICAL_MIN_POSITIVE_SEGMENTS",
    "classify_practical_core_satellite_portfolio",
]


class PracticalCoreSatelliteError(RuntimeError):
    pass


ARM_ORB = "ORB_GAP_050_RVOL_175"

#: ARM_B's desired core allocation is capped at 60% of current marked
#: equity (Issue #350) -- unlike M48's ARM_B, which was always 100%
#: equal-capital invested across its target basket.
CORE_ALLOCATION_CAP_PCT = Decimal("0.60")

#: Maximum three concurrent TACTICAL positions total (ORB + ARM_A
#: combined) -- unchanged numeric value from M48's ARM_A-only cap, but
#: now shared across two lanes instead of exclusive to one.
TACTICAL_MAX_POSITIONS = MAX_CONCURRENT_POSITIONS_A


# ---------------------------------------------------------------------------
# ORB baseline replay: reconstruct real entry/exit timestamps for the
# frozen GAP_050_RVOL_175 / WAITED_FIRST_VALID_0950 / BE_AFTER_050R
# baseline by re-invoking `simulate_exit_arm` (completely unmodified)
# against the same cached minute bars that produced the already-committed
# M43 checkpoints (`artifacts/research-profit-protection-exit/
# period-checkpoint/*.pkl`, gitignored, read-only, never regenerated by
# this module). `TradeDiagnosticRow`'s own frozen `_outcome_dict()`
# serialization drops `ExitArmOutcome.entry_timestamp`/`exit_timestamp`
# (an M43-era format choice) -- this module calls the SAME upstream
# candidate pipeline (`precompute_inputs_table`, `evaluate_combo`,
# `early_pool`, `select_first_candidate_per_day`, `enrich_with_decision_time`,
# `_waited_shadow_decision`, `simulate_exit_arm`), all imported unmodified,
# and simply keeps the full `ExitArmOutcome` object (with its timestamps)
# instead of discarding them. `verify_orb_baseline_parity` reconverts each
# replayed outcome back through the SAME `_outcome_dict()` used to build
# the frozen checkpoint and asserts an exact match -- this recomputation
# IS the required "ORB standalone replay matches the frozen baseline
# metrics bit-for-bit" integrity gate, not a separate claim.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrbTradeIntent:
    """One ORB baseline trade, with real intraday timestamps, exactly as
    replayed from the frozen `WAITED_FIRST_VALID_0950`/`BE_AFTER_050R`
    pipeline. `entry_timestamp`/`exit_timestamp` are `None` for a
    candidate that was scanned but never entered (or never exited within
    its own session's cached bars) -- `entered`/`closed` disambiguate."""

    period: str
    symbol: str
    session_date: date
    entered: bool
    closed: bool
    entry_timestamp: datetime | None
    entry_price: Decimal | None
    initial_stop_price: Decimal | None
    target_price: Decimal | None
    exit_timestamp: datetime | None
    exit_price: Decimal | None
    exit_reason: str
    r_multiple: float | None


def _waited_enriched_candidates(
    *, universe: Sequence[str], session_dates: Sequence[date],
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]], base_config: Mapping[str, Any],
    session_open_resolver: Any = None,
) -> tuple[tuple[Any, ...], ComboEvaluation, InputsTable]:
    """The exact `WAITED_FIRST_VALID_0950` candidate-identity pipeline
    `build_all_entry_and_exit_rows` itself uses (`early_pool` ->
    `select_first_candidate_per_day` -> `enrich_with_decision_time`),
    called directly and unmodified so the intermediate `ComboEvaluation`/
    `InputsTable` stay available to the caller for a `simulate_exit_arm`
    replay that preserves full `ExitArmOutcome` timestamps."""
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    resolver_kwargs = {} if session_open_resolver is None else {"session_open_resolver": session_open_resolver}
    inputs_table = precompute_inputs_table(
        universe=universe, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=base_config, **resolver_kwargs,
    )
    evaluation = evaluate_combo(combo=CONFIG_ENTRY.combo, universe=universe, session_dates=session_dates, inputs_table=inputs_table, base_config=base_config)
    early_signals = early_pool(evaluation.candidate_signals)
    early_winners = select_first_candidate_per_day(early_signals)
    waited_enriched = tuple(enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_winners)
    return waited_enriched, evaluation, inputs_table


def replay_orb_baseline_intents(
    *, period: str, universe: Sequence[str], session_dates: Sequence[date],
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]], base_config: Mapping[str, Any],
    created_at: datetime, session_bounds_provider: Any = None, session_open_resolver: Any = None,
) -> tuple[OrbTradeIntent, ...]:
    """Every `WAITED_FIRST_VALID_0950`/`BE_AFTER_050R` candidate for one
    frozen ORB period, as an `OrbTradeIntent` carrying real entry/exit
    timestamps. Reuses the exact frozen candidate pipeline and
    `simulate_exit_arm` -- no new signal, threshold, or exit logic."""
    waited_enriched, _evaluation, _inputs_table = _waited_enriched_candidates(
        universe=universe, session_dates=session_dates, market_bars_by_symbol=market_bars_by_symbol,
        base_config=base_config, session_open_resolver=session_open_resolver,
    )
    combo_config = sensitivity_config(base_config, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"
    shadow_run_id = f"m49-{period}-WAITED_FIRST_VALID_0950-{CONFIG_LABEL}"
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}

    intents: list[OrbTradeIntent] = []
    for candidate in waited_enriched:
        shadow_decision, subsequent_bars, _wait_approved = _waited_shadow_decision(
            candidate, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest="m49-orb-replay", shadow_run_id=shadow_run_id, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol,
        )
        if shadow_decision is None:
            intents.append(OrbTradeIntent(
                period=period, symbol=candidate.symbol, session_date=candidate.session_date,
                entered=False, closed=False, entry_timestamp=None, entry_price=None, initial_stop_price=None,
                target_price=None, exit_timestamp=None, exit_price=None, exit_reason="", r_multiple=None,
            ))
            continue
        outcome: ExitArmOutcome = simulate_exit_arm(shadow_decision, subsequent_bars, arm=ARM_BE_AFTER_050R, **kwargs)
        entered = outcome.entry_price is not None
        closed = outcome.realized_pnl is not None
        risk = (outcome.entry_price - outcome.initial_stop_price) if (entered and outcome.initial_stop_price is not None) else None
        r_multiple = float(outcome.realized_pnl / risk) if (closed and risk and risk > 0) else None
        intents.append(OrbTradeIntent(
            period=period, symbol=shadow_decision.symbol, session_date=candidate.session_date,
            entered=entered, closed=closed, entry_timestamp=outcome.entry_timestamp, entry_price=outcome.entry_price,
            initial_stop_price=outcome.initial_stop_price, target_price=outcome.target_price,
            exit_timestamp=outcome.exit_timestamp, exit_price=outcome.exit_price, exit_reason=outcome.exit_reason,
            r_multiple=r_multiple,
        ))
    return tuple(intents)


def verify_orb_baseline_parity(
    *, period: str, replayed_intents: Sequence[OrbTradeIntent], frozen_rows: Sequence[Any],
) -> dict[str, Any]:
    """Bit-for-bit comparison of `replayed_intents` (this module's own
    timestamp-preserving replay) against `frozen_rows` (the already-
    committed, read-only `research-profit-protection-exit` checkpoint's
    `WAITED_FIRST_VALID_0950`/`BE_AFTER_050R` rows) -- every economic
    field except the timestamps themselves (which the frozen checkpoint
    never stored) must match exactly, in the same order. This recomputation
    IS the required ORB standalone-parity integrity gate."""
    if len(replayed_intents) != len(frozen_rows):
        return {
            "period": period, "ok": False, "matched_count": 0, "mismatch_count": len(frozen_rows),
            "mismatches": [{"index": None, "reason": f"row-count mismatch: replayed={len(replayed_intents)} frozen={len(frozen_rows)}"}],
        }
    mismatches: list[dict[str, Any]] = []
    for i, (intent, frozen_row) in enumerate(zip(replayed_intents, frozen_rows)):
        frozen_outcome = dict(frozen_row.outcome)
        if not intent.entered:
            if frozen_outcome.get("state") not in ("NO_DECISION_TIME_DATA",) and frozen_outcome.get("entered") is not False:
                mismatches.append({"index": i, "symbol": intent.symbol, "reason": f"replay never entered but frozen state={frozen_outcome.get('state')!r} entered={frozen_outcome.get('entered')!r}"})
            continue
        fields = {
            "entry_price": str(intent.entry_price) if intent.entry_price is not None else None,
            "initial_stop_price": str(intent.initial_stop_price) if intent.initial_stop_price is not None else None,
            "target_price": str(intent.target_price) if intent.target_price is not None else None,
            "exit_price": str(intent.exit_price) if intent.exit_price is not None else None,
            "exit_reason": intent.exit_reason,
            "r_multiple": intent.r_multiple,
            "entered": intent.entered,
            "closed": intent.closed,
        }
        for key, value in fields.items():
            frozen_value = frozen_outcome.get(key)
            if value != frozen_value:
                mismatches.append({"index": i, "symbol": intent.symbol, "field": key, "replayed": value, "frozen": frozen_value})
    return {
        "period": period, "ok": not mismatches,
        "matched_count": len(replayed_intents) - len({m["index"] for m in mismatches}),
        "mismatch_count": len({m["index"] for m in mismatches}),
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# The shared allocator: ORB + ARM_A + ARM_B replayed through ONE unified,
# chronologically-ordered daily event loop against a single shared cash/
# tactical-risk/tactical-position-count state (Issue #350's exact 7-step
# ordering). ARM_A's and ARM_B's own signal-generation math (indicators,
# ranking, month-end selection) is reused completely unmodified via the
# imported M48 functions; only the EXECUTION/allocation layer is new here.
# `include_orb`/`include_arm_a`/`include_arm_b` (each defaulting True) let
# the SAME function run any single lane alone, at `core_allocation_cap_pct
# =Decimal("1")` and the other two lanes disabled, to prove standalone
# parity against the frozen M43/M48 baselines before any combined economics
# are read.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TacticalPosition:
    """One open ORB or ARM_A tactical position in the shared allocator's
    own book. `stop_price` is `None` for an ORB position -- ORB's exit is
    resolved entirely by its own pre-replayed `OrbTradeIntent` (a real,
    data-driven intraday event), never a daily (open, low) stop check."""

    lane: str
    symbol: str
    entry_date: date
    entry_index: int
    entry_price: Decimal
    quantity: int
    stop_price: Decimal | None
    planned_risk: Decimal
    entry_cost: Decimal


@dataclass(frozen=True)
class CoreHolding:
    """One open ARM_B core holding (mirrors M48's `RebalanceHolding`)."""

    symbol: str
    entry_date: date
    entry_index: int
    entry_price: Decimal
    quantity: int
    entry_cost: Decimal


@dataclass(frozen=True)
class ClosedPortfolioTrade:
    lane: str
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    holding_sessions: int
    gross_pnl: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    net_pnl: Decimal
    reason: str


@dataclass(frozen=True)
class PortfolioTransaction:
    date: date
    lane: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    notional: Decimal
    cost: Decimal
    reason: str


@dataclass(frozen=True)
class BlockedIntent:
    """An otherwise-valid entry/buy that could not be (fully) filled, with
    an explicit reason -- never silently dropped. `filled_quantity` may be
    0 (fully blocked) or between 0 and `desired_quantity` (partially
    filled elsewhere; this row records only the SHORTFALL)."""

    date: date
    lane: str
    symbol: str
    side: str
    desired_quantity: int
    filled_quantity: int
    reason: str


@dataclass(frozen=True)
class PortfolioPositionSnapshot:
    date: date
    lane: str
    symbol: str
    quantity: int
    mark_price: Decimal
    market_value: Decimal


@dataclass(frozen=True)
class CoreRebalanceEvent:
    signal_date: date
    signal_index: int
    execution_date: date | None
    execution_index: int | None
    target_symbols: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioSimulationResult:
    daily_equity: tuple[tuple[date, Decimal], ...]
    starting_capital: Decimal
    ending_cash: Decimal
    total_cost_paid: Decimal
    closed_trades: tuple[ClosedPortfolioTrade, ...]
    transactions: tuple[PortfolioTransaction, ...]
    blocked_intents: tuple[BlockedIntent, ...]
    daily_position_snapshots: tuple[PortfolioPositionSnapshot, ...]
    daily_tactical_position_count: tuple[tuple[date, int], ...]
    daily_core_allocation_pct: tuple[tuple[date, Decimal], ...]
    open_tactical_positions_at_end: tuple[TacticalPosition, ...]
    open_core_holdings_at_end: tuple[CoreHolding, ...]
    core_rebalance_events: tuple[CoreRebalanceEvent, ...]


def _arm_a_indicators(series_by_symbol: Mapping[str, DailySeries]) -> dict[str, dict[str, list[Decimal | None]]]:
    indicators: dict[str, dict[str, list[Decimal | None]]] = {}
    for symbol, s in series_by_symbol.items():
        indicators[symbol] = {
            "sma200": simple_moving_average(s.closes, TREND_SMA_PERIOD),
            "rsi2": wilder_rsi(s.closes, RSI_PERIOD_A),
            "sma5": simple_moving_average(s.closes, EXIT_SMA_PERIOD_A),
            "atr14": average_true_range(s.highs, s.lows, s.closes, ATR_PERIOD_A),
        }
    return indicators


def simulate_practical_core_satellite_portfolio(
    *, series_by_symbol_a: Mapping[str, DailySeries], series_by_symbol_b: Mapping[str, DailySeries],
    master_dates: Sequence[date], orb_intents: Sequence[OrbTradeIntent],
    cost_bps: Decimal = BASE_COST_BPS, starting_capital: Decimal = STARTING_CAPITAL,
    evaluation_start: date | None = None, core_allocation_cap_pct: Decimal = CORE_ALLOCATION_CAP_PCT,
    include_orb: bool = True, include_arm_a: bool = True, include_arm_b: bool = True,
    max_tactical_positions: int = TACTICAL_MAX_POSITIONS, max_risk_per_position: Decimal = MAX_RISK_PER_POSITION,
    max_aggregate_tactical_risk: Decimal = MAX_AGGREGATE_RISK,
) -> PortfolioSimulationResult:
    """One unified, chronologically-ordered daily event loop over ONE
    shared `cash` pool, implementing Issue #350's exact 7-step ordering:
    (1) nothing to do here explicitly -- pending state is simply carried
    in `pending_entries_a`/`scheduled_exit_reason_a`/`pending_target_b`
    across iterations, each fully recomputed every relevant session, so a
    blocked prior-day intent is never replayed; (2) at the open, ARM_A's
    scheduled exits, then ARM_B's removals/trims; (3) ARM_B target buys/
    top-ups, capped at `core_allocation_cap_pct` of TOTAL shared marked
    equity and by available cash; (4) ARM_A next-open entries, in RSI-
    ranked order, subject to the SHARED tactical position-count/aggregate-
    risk caps and available cash; (5) ORB's own entry at its real intraday
    timestamp (this function does not model true intraday interleaving of
    ARM_A/ARM_B, which only ever trade at the regular open -- ORB's entry
    is applied after them the same session, exactly matching the required
    ordering, using whatever cash/risk/position slots remain at that
    point); (6) protective exits -- ARM_A's own (open, low) stop check,
    then ORB's own already-timestamped exit; (7) mark every open position
    at the session close and append one complete snapshot. ARM_B never
    receives a synthetic stop. Tactical positions are never liquidated to
    make room for another entry, in any lane."""
    if include_arm_a:
        require_full_alignment(series_by_symbol_a, master_dates=master_dates)
    if include_arm_b:
        require_full_alignment(series_by_symbol_b, master_dates=master_dates)
    n = len(master_dates)

    indicators_a = _arm_a_indicators(series_by_symbol_a) if include_arm_a else {}
    symbols_a = sorted(series_by_symbol_a) if include_arm_a else []
    sma200_b = {s: simple_moving_average(series_by_symbol_b[s].closes, TREND_SMA_PERIOD) for s in series_by_symbol_b} if include_arm_b else {}
    selector_b = arm_b_selector(series_by_symbol_b, sma200_b) if include_arm_b else None
    month_ends_b = set(month_end_indices(master_dates)) if include_arm_b else set()

    orb_intents_by_date: dict[date, OrbTradeIntent] = {}
    if include_orb:
        for intent in orb_intents:
            if not intent.entered:
                continue
            if intent.session_date in orb_intents_by_date:
                raise PracticalCoreSatelliteError(f"more than one entered ORB intent on {intent.session_date} -- select_first_candidate_per_day should guarantee at most one")
            orb_intents_by_date[intent.session_date] = intent

    cash = starting_capital
    total_cost_paid = Decimal(0)
    open_positions_a: dict[str, TacticalPosition] = {}
    open_positions_orb: dict[str, TacticalPosition] = {}
    open_positions_b: dict[str, CoreHolding] = {}
    scheduled_exit_reason_a: dict[str, str] = {}
    pending_entries_a: list[str] = []
    pending_target_b: tuple[str, ...] | None = None
    pending_execution_index_b: int | None = None

    closed_trades: list[ClosedPortfolioTrade] = []
    transactions: list[PortfolioTransaction] = []
    blocked_intents: list[BlockedIntent] = []
    daily_equity: list[tuple[date, Decimal]] = []
    daily_position_snapshots: list[PortfolioPositionSnapshot] = []
    daily_tactical_position_count: list[tuple[date, int]] = []
    daily_core_allocation_pct: list[tuple[date, Decimal]] = []
    core_rebalance_events: list[CoreRebalanceEvent] = []

    def _tactical_count() -> int:
        return len(open_positions_a) + len(open_positions_orb)

    def _tactical_aggregate_risk() -> Decimal:
        return sum((p.planned_risk for p in open_positions_a.values()), Decimal(0)) + sum((p.planned_risk for p in open_positions_orb.values()), Decimal(0))

    for i in range(n):
        day = master_dates[i]
        execution_allowed = evaluation_start is None or day >= evaluation_start

        # --- STEP 2a: ARM_A scheduled exits at today's open ---
        if execution_allowed and include_arm_a:
            for symbol in list(open_positions_a):
                pos = open_positions_a[symbol]
                reason = scheduled_exit_reason_a.get(symbol)
                if reason is None:
                    continue
                s = series_by_symbol_a[symbol]
                exit_price = s.opens[i]
                exit_notional = exit_price * pos.quantity
                exit_cost = transaction_cost(exit_notional, cost_bps)
                gross_pnl = (exit_price - pos.entry_price) * pos.quantity
                net_pnl = gross_pnl - pos.entry_cost - exit_cost
                cash += exit_notional - exit_cost
                total_cost_paid += exit_cost
                closed_trades.append(ClosedPortfolioTrade(
                    lane=ARM_A, symbol=symbol, entry_date=pos.entry_date, exit_date=day,
                    entry_price=pos.entry_price, exit_price=exit_price, quantity=pos.quantity,
                    holding_sessions=i - pos.entry_index + 1, gross_pnl=gross_pnl,
                    entry_cost=pos.entry_cost, exit_cost=exit_cost, net_pnl=net_pnl, reason=reason,
                ))
                transactions.append(PortfolioTransaction(
                    date=day, lane=ARM_A, symbol=symbol, side="SELL", quantity=pos.quantity,
                    price=exit_price, notional=exit_notional, cost=exit_cost, reason=reason,
                ))
                del open_positions_a[symbol]
                scheduled_exit_reason_a.pop(symbol, None)

        # --- STEP 2b: ARM_B removals (full exit for symbols leaving the
        # target basket) and trims (partial sell for continuing holdings
        # above their new capped target quantity), at today's open ---
        is_core_execution_day = execution_allowed and include_arm_b and pending_target_b is not None and pending_execution_index_b == i
        if is_core_execution_day:
            target = set(pending_target_b)
            for symbol in sorted(open_positions_b):
                if symbol in target:
                    continue
                pos = open_positions_b[symbol]
                s = series_by_symbol_b[symbol]
                exit_price = s.opens[i]
                exit_notional = exit_price * pos.quantity
                exit_cost = transaction_cost(exit_notional, cost_bps)
                gross_pnl = (exit_price - pos.entry_price) * pos.quantity
                net_pnl = gross_pnl - pos.entry_cost - exit_cost
                cash += exit_notional - exit_cost
                total_cost_paid += exit_cost
                closed_trades.append(ClosedPortfolioTrade(
                    lane=ARM_B, symbol=symbol, entry_date=pos.entry_date, exit_date=day,
                    entry_price=pos.entry_price, exit_price=exit_price, quantity=pos.quantity,
                    holding_sessions=i - pos.entry_index + 1, gross_pnl=gross_pnl,
                    entry_cost=pos.entry_cost, exit_cost=exit_cost, net_pnl=net_pnl, reason="CORE_REMOVED",
                ))
                transactions.append(PortfolioTransaction(
                    date=day, lane=ARM_B, symbol=symbol, side="SELL", quantity=pos.quantity,
                    price=exit_price, notional=exit_notional, cost=exit_cost, reason="CORE_REMOVED",
                ))
                del open_positions_b[symbol]

            # Total SHARED marked equity, at today's open, after ARM_A's
            # own scheduled exits and ARM_B's own removals above (but
            # before any new capital deployment this session) -- the
            # basis the 60% core cap is measured against.
            equity_now = cash
            equity_now += sum((open_positions_a[s].quantity * series_by_symbol_a[s].opens[i] for s in open_positions_a), Decimal(0))
            equity_now += sum((open_positions_orb[s].quantity * series_by_symbol_a[s].opens[i] for s in open_positions_orb if s in series_by_symbol_a), Decimal(0))
            equity_now += sum((open_positions_b[s].quantity * series_by_symbol_b[s].opens[i] for s in open_positions_b), Decimal(0))
            core_capital_cap = core_allocation_cap_pct * equity_now
            target_capital_per_symbol = core_capital_cap / Decimal(len(target)) if target else Decimal(0)
            target_qty: dict[str, int] = {}
            for symbol in target:
                price = series_by_symbol_b[symbol].opens[i]
                target_qty[symbol] = equal_capital_whole_shares(capital=target_capital_per_symbol, price=price)

            for symbol in sorted(s for s in open_positions_b if s in target):
                pos = open_positions_b[symbol]
                desired = target_qty.get(symbol, 0)
                if pos.quantity <= desired:
                    continue
                trim_qty = pos.quantity - desired
                price = series_by_symbol_b[symbol].opens[i]
                proceeds = price * trim_qty
                exit_cost = transaction_cost(proceeds, cost_bps)
                frac = Decimal(trim_qty) / Decimal(pos.quantity)
                allocated_entry_cost = pos.entry_cost * frac
                gross_pnl = (price - pos.entry_price) * trim_qty
                net_pnl = gross_pnl - allocated_entry_cost - exit_cost
                cash += proceeds - exit_cost
                total_cost_paid += exit_cost
                closed_trades.append(ClosedPortfolioTrade(
                    lane=ARM_B, symbol=symbol, entry_date=pos.entry_date, exit_date=day,
                    entry_price=pos.entry_price, exit_price=price, quantity=trim_qty,
                    holding_sessions=i - pos.entry_index + 1, gross_pnl=gross_pnl,
                    entry_cost=allocated_entry_cost, exit_cost=exit_cost, net_pnl=net_pnl, reason="CORE_TRIM",
                ))
                transactions.append(PortfolioTransaction(
                    date=day, lane=ARM_B, symbol=symbol, side="SELL", quantity=trim_qty,
                    price=price, notional=proceeds, cost=exit_cost, reason="CORE_TRIM",
                ))
                if desired <= 0:
                    del open_positions_b[symbol]
                else:
                    open_positions_b[symbol] = CoreHolding(
                        symbol=symbol, entry_date=pos.entry_date, entry_index=pos.entry_index,
                        entry_price=pos.entry_price, quantity=desired, entry_cost=pos.entry_cost - allocated_entry_cost,
                    )

        # --- STEP 3: ARM_B target buys/top-ups, up to the 60% core cap
        # and available SHARED cash. Cash is shared chronologically -- if
        # prior tactical exits didn't free enough, ARM_B records a
        # partial/blocked target rather than creating negative cash. ---
        if is_core_execution_day:
            target = set(pending_target_b)
            for symbol in sorted(target):
                desired_total = target_qty.get(symbol, 0)
                current = open_positions_b.get(symbol)
                current_qty = current.quantity if current is not None else 0
                needed = desired_total - current_qty
                if needed <= 0:
                    continue
                price = series_by_symbol_b[symbol].opens[i]
                if price <= 0:
                    continue
                affordable = int(cash // (price * (Decimal(1) + cost_bps / Decimal(10000))))
                buy_qty = min(needed, affordable)
                while buy_qty > 0 and price * buy_qty + transaction_cost(price * buy_qty, cost_bps) > cash:
                    buy_qty -= 1
                if buy_qty < needed:
                    blocked_intents.append(BlockedIntent(
                        date=day, lane=ARM_B, symbol=symbol, side="BUY",
                        desired_quantity=needed, filled_quantity=max(buy_qty, 0),
                        reason="insufficient_shared_cash_after_tactical_allocation",
                    ))
                if buy_qty <= 0:
                    continue
                notional = price * buy_qty
                entry_cost = transaction_cost(notional, cost_bps)
                cash -= notional + entry_cost
                total_cost_paid += entry_cost
                reason = "CORE_TOPUP" if current is not None else "CORE_BUY"
                if current is None:
                    open_positions_b[symbol] = CoreHolding(
                        symbol=symbol, entry_date=day, entry_index=i, entry_price=price,
                        quantity=buy_qty, entry_cost=entry_cost,
                    )
                else:
                    combined_qty = current.quantity + buy_qty
                    weighted_entry_price = (current.quantity * current.entry_price + buy_qty * price) / Decimal(combined_qty)
                    open_positions_b[symbol] = CoreHolding(
                        symbol=symbol, entry_date=current.entry_date, entry_index=current.entry_index,
                        entry_price=weighted_entry_price, quantity=combined_qty,
                        entry_cost=current.entry_cost + entry_cost,
                    )
                transactions.append(PortfolioTransaction(
                    date=day, lane=ARM_B, symbol=symbol, side="BUY", quantity=buy_qty,
                    price=price, notional=notional, cost=entry_cost, reason=reason,
                ))
            pending_target_b = None
            pending_execution_index_b = None

        # --- STEP 4: ARM_A next-open entries, RSI-ranked order, subject
        # to the SHARED tactical position-count/aggregate-risk caps and
        # available SHARED cash. ---
        if execution_allowed and include_arm_a:
            for symbol in pending_entries_a:
                if _tactical_count() >= max_tactical_positions:
                    blocked_intents.append(BlockedIntent(date=day, lane=ARM_A, symbol=symbol, side="BUY", desired_quantity=0, filled_quantity=0, reason="tactical_position_count_cap"))
                    break
                if symbol in open_positions_a:
                    continue
                s = series_by_symbol_a[symbol]
                atr = indicators_a[symbol]["atr14"][i - 1] if i > 0 else None
                if atr is None:
                    continue
                entry_open = s.opens[i]
                stop_price = entry_open - ATR_STOP_MULTIPLE_A * atr
                remaining_aggregate = max_aggregate_tactical_risk - _tactical_aggregate_risk()
                if remaining_aggregate <= 0:
                    blocked_intents.append(BlockedIntent(date=day, lane=ARM_A, symbol=symbol, side="BUY", desired_quantity=0, filled_quantity=0, reason="tactical_aggregate_risk_cap"))
                    continue
                risk_cap = min(max_risk_per_position, remaining_aggregate)
                quantity = whole_share_quantity(entry_price=entry_open, stop_price=stop_price, max_risk_dollars=risk_cap, available_cash=cash)
                if quantity <= 0:
                    blocked_intents.append(BlockedIntent(date=day, lane=ARM_A, symbol=symbol, side="BUY", desired_quantity=0, filled_quantity=0, reason="insufficient_shared_cash_or_risk_budget"))
                    continue
                notional = entry_open * quantity
                entry_cost = transaction_cost(notional, cost_bps)
                if notional + entry_cost > cash:
                    blocked_intents.append(BlockedIntent(date=day, lane=ARM_A, symbol=symbol, side="BUY", desired_quantity=quantity, filled_quantity=0, reason="insufficient_shared_cash"))
                    continue
                cash -= notional + entry_cost
                total_cost_paid += entry_cost
                planned_risk = (entry_open - stop_price) * quantity
                open_positions_a[symbol] = TacticalPosition(
                    lane=ARM_A, symbol=symbol, entry_date=day, entry_index=i, entry_price=entry_open,
                    quantity=quantity, stop_price=stop_price, planned_risk=planned_risk, entry_cost=entry_cost,
                )
                transactions.append(PortfolioTransaction(
                    date=day, lane=ARM_A, symbol=symbol, side="BUY", quantity=quantity,
                    price=entry_open, notional=notional, cost=entry_cost, reason="ENTRY",
                ))

        # --- STEP 5: ORB's own entry at its real intraday timestamp,
        # using whatever SHARED cash/risk/position slots remain after
        # steps 2-4. ---
        if execution_allowed and include_orb:
            intent = orb_intents_by_date.get(day)
            if intent is not None and intent.entered and intent.entry_price is not None and intent.initial_stop_price is not None:
                symbol = intent.symbol
                if _tactical_count() >= max_tactical_positions:
                    blocked_intents.append(BlockedIntent(date=day, lane=ARM_ORB, symbol=symbol, side="BUY", desired_quantity=0, filled_quantity=0, reason="tactical_position_count_cap"))
                elif symbol in open_positions_a or symbol in open_positions_orb:
                    blocked_intents.append(BlockedIntent(date=day, lane=ARM_ORB, symbol=symbol, side="BUY", desired_quantity=0, filled_quantity=0, reason="symbol_already_open"))
                else:
                    remaining_aggregate = max_aggregate_tactical_risk - _tactical_aggregate_risk()
                    if remaining_aggregate <= 0:
                        blocked_intents.append(BlockedIntent(date=day, lane=ARM_ORB, symbol=symbol, side="BUY", desired_quantity=0, filled_quantity=0, reason="tactical_aggregate_risk_cap"))
                    else:
                        risk_cap = min(max_risk_per_position, remaining_aggregate)
                        quantity = whole_share_quantity(
                            entry_price=intent.entry_price, stop_price=intent.initial_stop_price,
                            max_risk_dollars=risk_cap, available_cash=cash,
                        )
                        if quantity <= 0:
                            blocked_intents.append(BlockedIntent(date=day, lane=ARM_ORB, symbol=symbol, side="BUY", desired_quantity=0, filled_quantity=0, reason="insufficient_shared_cash_or_risk_budget"))
                        else:
                            notional = intent.entry_price * quantity
                            entry_cost = transaction_cost(notional, cost_bps)
                            if notional + entry_cost > cash:
                                blocked_intents.append(BlockedIntent(date=day, lane=ARM_ORB, symbol=symbol, side="BUY", desired_quantity=quantity, filled_quantity=0, reason="insufficient_shared_cash"))
                            else:
                                cash -= notional + entry_cost
                                total_cost_paid += entry_cost
                                planned_risk = (intent.entry_price - intent.initial_stop_price) * quantity
                                open_positions_orb[symbol] = TacticalPosition(
                                    lane=ARM_ORB, symbol=symbol, entry_date=day, entry_index=i, entry_price=intent.entry_price,
                                    quantity=quantity, stop_price=None, planned_risk=planned_risk, entry_cost=entry_cost,
                                )
                                transactions.append(PortfolioTransaction(
                                    date=day, lane=ARM_ORB, symbol=symbol, side="BUY", quantity=quantity,
                                    price=intent.entry_price, notional=notional, cost=entry_cost, reason="ENTRY",
                                ))

        # --- STEP 6a: ARM_A's own (open, low) protective-stop check, for
        # every position still open after steps 2-4 (including one just
        # entered in step 4). ---
        if execution_allowed and include_arm_a:
            for symbol in list(open_positions_a):
                pos = open_positions_a[symbol]
                s = series_by_symbol_a[symbol]
                open_i, low_i = s.opens[i], s.lows[i]
                if pos.stop_price is None or low_i > pos.stop_price:
                    continue
                exit_price = stop_fill_price(session_open=open_i, stop_price=pos.stop_price)
                exit_notional = exit_price * pos.quantity
                exit_cost = transaction_cost(exit_notional, cost_bps)
                gross_pnl = (exit_price - pos.entry_price) * pos.quantity
                net_pnl = gross_pnl - pos.entry_cost - exit_cost
                cash += exit_notional - exit_cost
                total_cost_paid += exit_cost
                closed_trades.append(ClosedPortfolioTrade(
                    lane=ARM_A, symbol=symbol, entry_date=pos.entry_date, exit_date=day,
                    entry_price=pos.entry_price, exit_price=exit_price, quantity=pos.quantity,
                    holding_sessions=i - pos.entry_index + 1, gross_pnl=gross_pnl,
                    entry_cost=pos.entry_cost, exit_cost=exit_cost, net_pnl=net_pnl, reason="STOP",
                ))
                transactions.append(PortfolioTransaction(
                    date=day, lane=ARM_A, symbol=symbol, side="SELL", quantity=pos.quantity,
                    price=exit_price, notional=exit_notional, cost=exit_cost, reason="STOP",
                ))
                del open_positions_a[symbol]
                scheduled_exit_reason_a.pop(symbol, None)

        # --- STEP 6b: ORB's own already-timestamped exit, if it entered
        # this session. A candidate that entered but recorded no exit
        # within its own session's cached bars (frozen data ends before
        # any stop/target/time-exit condition triggers) is NOT
        # fabricated an exit -- it is closed out at the session's own
        # close as a fail-closed integrity condition (see
        # `run_full_study`'s own guard for this in the CLI), since the
        # frozen baseline's own data never lets an ORB trade carry past
        # its entry session in the first place. ---
        if execution_allowed and include_orb:
            intent = orb_intents_by_date.get(day)
            if intent is not None:
                pos = open_positions_orb.get(intent.symbol)
                # `pos` is None here only if step 5's entry attempt was
                # itself blocked (recorded as a BlockedIntent there) --
                # nothing to exit in that case.
                if pos is not None and pos.entry_date == day:
                    if intent.closed and intent.exit_price is not None:
                        exit_price = intent.exit_price
                        exit_notional = exit_price * pos.quantity
                        exit_cost = transaction_cost(exit_notional, cost_bps)
                        gross_pnl = (exit_price - pos.entry_price) * pos.quantity
                        net_pnl = gross_pnl - pos.entry_cost - exit_cost
                        cash += exit_notional - exit_cost
                        total_cost_paid += exit_cost
                        closed_trades.append(ClosedPortfolioTrade(
                            lane=ARM_ORB, symbol=intent.symbol, entry_date=pos.entry_date, exit_date=day,
                            entry_price=pos.entry_price, exit_price=exit_price, quantity=pos.quantity,
                            holding_sessions=1, gross_pnl=gross_pnl, entry_cost=pos.entry_cost,
                            exit_cost=exit_cost, net_pnl=net_pnl, reason=intent.exit_reason or "orb_exit",
                        ))
                        transactions.append(PortfolioTransaction(
                            date=day, lane=ARM_ORB, symbol=intent.symbol, side="SELL", quantity=pos.quantity,
                            price=exit_price, notional=exit_notional, cost=exit_cost, reason=intent.exit_reason or "orb_exit",
                        ))
                        del open_positions_orb[intent.symbol]
                    else:
                        raise PracticalCoreSatelliteError(
                            f"ORB position {intent.symbol} entered {day.isoformat()} recorded no exit within its own "
                            "session's cached bars -- the frozen baseline never carries a position past its entry "
                            "session; this is an unmodeled edge case and must be investigated, not silently carried "
                            "forward or fabricated an exit."
                        )

        # --- ARM_A signal scan / scheduled-exit flagging at today's
        # close (unconditional every session, including warm-up). ---
        if include_arm_a:
            candidates: list[PullbackCandidate] = []
            for symbol in symbols_a:
                if symbol in open_positions_a:
                    continue
                sma200_i = indicators_a[symbol]["sma200"][i]
                rsi2_i = indicators_a[symbol]["rsi2"][i]
                if sma200_i is None or rsi2_i is None:
                    continue
                close_i = series_by_symbol_a[symbol].closes[i]
                if close_i > sma200_i and rsi2_i < RSI_ENTRY_THRESHOLD_A:
                    candidates.append(PullbackCandidate(symbol=symbol, rsi2=rsi2_i))
            pending_entries_a = [c.symbol for c in rank_pullback_candidates(candidates)]

            scheduled_exit_reason_a = {}
            for symbol, pos in open_positions_a.items():
                s = series_by_symbol_a[symbol]
                close_i = s.closes[i]
                sma5_i = indicators_a[symbol]["sma5"][i]
                sessions_held = i - pos.entry_index + 1
                if sma5_i is not None and close_i > sma5_i:
                    scheduled_exit_reason_a[symbol] = "SMA5"
                elif sessions_held >= MAX_HOLDING_SESSIONS_A:
                    scheduled_exit_reason_a[symbol] = "MAX_HOLD"

        # --- ARM_B month-end signal (unconditional every session,
        # including warm-up) -- schedules execution at the NEXT session's
        # open, exactly like M48. ---
        if include_arm_b and i in month_ends_b:
            selected = tuple(selector_b(i))
            if i + 1 < n:
                core_rebalance_events.append(CoreRebalanceEvent(
                    signal_date=day, signal_index=i, execution_date=master_dates[i + 1],
                    execution_index=i + 1, target_symbols=selected,
                ))
                pending_target_b = selected
                pending_execution_index_b = i + 1
            else:
                core_rebalance_events.append(CoreRebalanceEvent(
                    signal_date=day, signal_index=i, execution_date=None, execution_index=None, target_symbols=selected,
                ))

        # --- STEP 7: mark every open position at the session close;
        # emit one complete snapshot. ---
        equity = cash
        for symbol, pos in open_positions_a.items():
            mark_price = series_by_symbol_a[symbol].closes[i]
            equity += mark_price * pos.quantity
            daily_position_snapshots.append(PortfolioPositionSnapshot(date=day, lane=ARM_A, symbol=symbol, quantity=pos.quantity, mark_price=mark_price, market_value=mark_price * pos.quantity))
        for symbol, pos in open_positions_orb.items():
            mark_price = series_by_symbol_a[symbol].closes[i] if symbol in series_by_symbol_a else pos.entry_price
            equity += mark_price * pos.quantity
            daily_position_snapshots.append(PortfolioPositionSnapshot(date=day, lane=ARM_ORB, symbol=symbol, quantity=pos.quantity, mark_price=mark_price, market_value=mark_price * pos.quantity))
        for symbol, pos in open_positions_b.items():
            mark_price = series_by_symbol_b[symbol].closes[i]
            equity += mark_price * pos.quantity
            daily_position_snapshots.append(PortfolioPositionSnapshot(date=day, lane=ARM_B, symbol=symbol, quantity=pos.quantity, mark_price=mark_price, market_value=mark_price * pos.quantity))
        daily_equity.append((day, equity))
        daily_tactical_position_count.append((day, _tactical_count()))
        core_value = sum((series_by_symbol_b[s].closes[i] * open_positions_b[s].quantity for s in open_positions_b), Decimal(0))
        daily_core_allocation_pct.append((day, (core_value / equity) if equity > 0 else Decimal(0)))

    return PortfolioSimulationResult(
        daily_equity=tuple(daily_equity), starting_capital=starting_capital, ending_cash=cash,
        total_cost_paid=total_cost_paid, closed_trades=tuple(closed_trades), transactions=tuple(transactions),
        blocked_intents=tuple(blocked_intents), daily_position_snapshots=tuple(daily_position_snapshots),
        daily_tactical_position_count=tuple(daily_tactical_position_count),
        daily_core_allocation_pct=tuple(daily_core_allocation_pct),
        open_tactical_positions_at_end=tuple(list(open_positions_a.values()) + list(open_positions_orb.values())),
        open_core_holdings_at_end=tuple(open_positions_b.values()), core_rebalance_events=tuple(core_rebalance_events),
    )


# ---------------------------------------------------------------------------
# Portfolio-level metrics and the frozen four-label classification gate.
# Reuses M48's own monthly-return/dollar/drawdown/Sharpe/Sortino/
# volatility/concentration/turnover primitives completely unmodified --
# including the Finding 16 opening-equity anchor fix and the Finding 17
# 0%-MAR downside-deviation formula, both already baked into those
# imported functions themselves. Only the orchestration (portfolio-wide
# instead of per-arm, plus sleeve/lane concentration and blocked-intent
# attribution) is new here.
# ---------------------------------------------------------------------------

def portfolio_end_mark_transactions(
    *, tactical_positions_at_end: Sequence[TacticalPosition], core_holdings_at_end: Sequence[CoreHolding],
    end_date: date, end_price_by_symbol: Mapping[str, Decimal], cost_bps: Decimal,
) -> tuple[PortfolioTransaction, ...]:
    """A hypothetical full liquidation of every still-open position (both
    tactical and core) at `end_date`'s close, with a hypothetical
    liquidation cost -- for net-P&L/concentration/turnover accounting
    ONLY, never fed back into the simulation. `end_date` must be the
    study's true fixed evaluation endpoint, never an interim segment
    boundary."""
    out: list[PortfolioTransaction] = []
    for pos in tactical_positions_at_end:
        price = end_price_by_symbol.get(pos.symbol)
        if price is None:
            continue
        notional = price * pos.quantity
        cost = transaction_cost(notional, cost_bps)
        out.append(PortfolioTransaction(date=end_date, lane=pos.lane, symbol=pos.symbol, side="SELL", quantity=pos.quantity, price=price, notional=notional, cost=cost, reason="EVALUATION_END_MARK"))
    for pos in core_holdings_at_end:
        price = end_price_by_symbol.get(pos.symbol)
        if price is None:
            continue
        notional = price * pos.quantity
        cost = transaction_cost(notional, cost_bps)
        out.append(PortfolioTransaction(date=end_date, lane=ARM_B, symbol=pos.symbol, side="SELL", quantity=pos.quantity, price=price, notional=notional, cost=cost, reason="EVALUATION_END_MARK"))
    return tuple(out)


def compute_portfolio_metrics(
    *, result: PortfolioSimulationResult, evaluation_start: date, evaluation_end: date,
    end_mark_transactions: Sequence[PortfolioTransaction] = (), fixed_notional: Decimal = STARTING_CAPITAL,
) -> dict[str, Any]:
    """The full required-output set for the COMBINED portfolio (base or
    stress cost case), computed over `[evaluation_start, evaluation_end]`
    only. Mirrors `orb_capital_target_strategy_family_research.
    compute_arm_metrics`'s own structure, generalized from one arm's
    ledgers to the whole shared-capital portfolio's ledgers, with an
    ADDITIONAL sleeve/lane concentration view and a blocked-intent
    summary that `compute_arm_metrics` has no equivalent of."""
    daily_equity = result.daily_equity
    window_equity = slice_to_window(daily_equity, start=evaluation_start, end=evaluation_end)
    window_trades = [t for t in result.closed_trades if evaluation_start <= t.exit_date <= evaluation_end]
    window_transactions = [t for t in result.transactions if evaluation_start <= t.date <= evaluation_end]
    if not window_equity:
        raise PracticalCoreSatelliteError("no equity marks in the evaluation window")

    baseline_equity = result.starting_capital
    prior_before_window = [e for d, e in daily_equity if d < evaluation_start]
    if prior_before_window:
        baseline_equity = prior_before_window[-1]
    anchored_equity = [(evaluation_start, baseline_equity), *window_equity]

    monthly_pct_pairs = monthly_return_pct(anchored_equity)
    monthly_pct = [v for _, v in monthly_pct_pairs]
    fixed_pnl_pairs = fixed_notional_monthly_dollar_pnl(monthly_pct_pairs, notional=fixed_notional)
    compounded_pnl_pairs = monthly_dollar_pnl(anchored_equity)
    fixed_pnl = [v for _, v in fixed_pnl_pairs]
    compounded_pnl = [v for _, v in compounded_pnl_pairs]

    gross_pnl = sum((t.gross_pnl for t in window_trades), Decimal(0))
    net_pnl_realized = sum((t.net_pnl for t in window_trades), Decimal(0))
    net_pnl_equity = window_equity[-1][1] - baseline_equity
    end_mark_cost = sum((t.cost for t in end_mark_transactions), Decimal(0))
    net_pnl_marked = net_pnl_equity - end_mark_cost

    dd = max_drawdown(anchored_equity)
    prof = profitable_month_stats(fixed_pnl)
    worst_pct = worst_and_percentile_month(monthly_pct)

    pnl_by_symbol: dict[str, Decimal] = {}
    pnl_by_lane: dict[str, Decimal] = {}
    for t in window_trades:
        pnl_by_symbol[t.symbol] = pnl_by_symbol.get(t.symbol, Decimal(0)) + t.net_pnl
        pnl_by_lane[t.lane] = pnl_by_lane.get(t.lane, Decimal(0)) + t.net_pnl

    end_mark_by_symbol = {t.symbol: t for t in end_mark_transactions}
    open_positions_at_end: list[Any] = list(result.open_tactical_positions_at_end) + list(result.open_core_holdings_at_end)
    for pos in open_positions_at_end:
        mark = end_mark_by_symbol.get(pos.symbol)
        if mark is None:
            continue
        unrealized_gross = (mark.price - pos.entry_price) * pos.quantity
        unrealized_net = unrealized_gross - pos.entry_cost - mark.cost
        lane = getattr(pos, "lane", ARM_B)
        pnl_by_symbol[pos.symbol] = pnl_by_symbol.get(pos.symbol, Decimal(0)) + unrealized_net
        pnl_by_lane[lane] = pnl_by_lane.get(lane, Decimal(0)) + unrealized_net

    concentration = positive_contribution_concentration(pnl_by_symbol)
    lane_concentration = positive_contribution_concentration(pnl_by_lane)

    trade_notionals = [t.notional for t in window_transactions] + [t.notional for t in end_mark_transactions]
    window_cost_paid = sum((t.cost for t in window_transactions), Decimal(0)) + end_mark_cost
    average_equity = (sum((e for _, e in window_equity), Decimal(0)) / Decimal(len(window_equity))) if window_equity else result.starting_capital
    turnover = turnover_and_costs(trade_notionals, window_cost_paid, average_equity)

    turnover_by_lane: dict[str, dict[str, Decimal]] = {}
    for lane in (ARM_ORB, ARM_A, ARM_B):
        lane_notionals = [t.notional for t in window_transactions if t.lane == lane] + [t.notional for t in end_mark_transactions if t.lane == lane]
        lane_cost = sum((t.cost for t in window_transactions if t.lane == lane), Decimal(0)) + sum((t.cost for t in end_mark_transactions if t.lane == lane), Decimal(0))
        turnover_by_lane[lane] = {
            "total_traded_notional": sum(lane_notionals, Decimal(0)), "total_cost_paid": lane_cost,
        }

    segment_net: dict[str, Decimal] = {}
    for seg in CALENDAR_SEGMENTS:
        seg_marks = [(d, e) for d, e in anchored_equity if segment_label(d) == seg]
        if not seg_marks:
            continue
        seg_start_candidates = [e for d, e in anchored_equity if d < seg_marks[0][0]]
        seg_baseline = seg_start_candidates[-1] if seg_start_candidates else baseline_equity
        segment_net[seg] = seg_marks[-1][1] - seg_baseline

    window_blocked = [b for b in result.blocked_intents if evaluation_start <= b.date <= evaluation_end]
    blocked_summary: dict[str, int] = {}
    for b in window_blocked:
        key = f"{b.lane}__{b.reason}"
        blocked_summary[key] = blocked_summary.get(key, 0) + 1

    return {
        "gross_pnl": gross_pnl,
        "net_pnl_realized": net_pnl_realized,
        "net_pnl": net_pnl_equity,
        "net_pnl_marked": net_pnl_marked,
        "end_mark_liquidation_cost": end_mark_cost,
        "mean_monthly_dollar_pnl_fixed_30k": (statistics.fmean([float(v) for v in fixed_pnl]) if fixed_pnl else None),
        "median_monthly_dollar_pnl_fixed_30k": (statistics.median([float(v) for v in fixed_pnl]) if fixed_pnl else None),
        "mean_monthly_dollar_pnl_compounded": (statistics.fmean([float(v) for v in compounded_pnl]) if compounded_pnl else None),
        "median_monthly_dollar_pnl_compounded": (statistics.median([float(v) for v in compounded_pnl]) if compounded_pnl else None),
        "mean_monthly_return_pct": (Decimal(str(statistics.fmean([float(v) for v in monthly_pct]))) if monthly_pct else Decimal(0)),
        "median_monthly_return_pct": (statistics.median([float(v) for v in monthly_pct]) if monthly_pct else None),
        "cagr": cagr(anchored_equity),
        "sharpe": sharpe_ratio(monthly_pct),
        "sortino": sortino_ratio(monthly_pct),
        "annualized_volatility_pct": annualized_volatility_pct(monthly_pct),
        "annualized_downside_deviation_pct": annualized_downside_deviation_pct(monthly_pct),
        "max_drawdown_dollars": dd["max_drawdown_dollars"],
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "recovery_days": dd["recovery_days"],
        "profitable_month_pct": prof["profitable_month_pct"],
        "longest_losing_streak": prof["longest_losing_streak"],
        "worst_month_dollars_fixed_30k": worst_and_percentile_month(fixed_pnl)["worst_month"],
        "fifth_percentile_month_pct": worst_pct["percentile_month"],
        "concentration": concentration,
        "sleeve_concentration": lane_concentration,
        "turnover": turnover,
        "turnover_by_lane": turnover_by_lane,
        "monthly_dollar_pnl_fixed_30k_series": fixed_pnl_pairs,
        "monthly_dollar_pnl_compounded_series": compounded_pnl_pairs,
        "monthly_return_pct_series": monthly_pct_pairs,
        "segment_net_pnl": segment_net,
        "starting_capital": baseline_equity,
        "ending_equity": window_equity[-1][1],
        "total_cost_paid": window_cost_paid,
        "blocked_intent_summary": blocked_summary,
        "blocked_intent_count": len(window_blocked),
        "reconciliation": {
            "symbol_contribution_sum": sum(pnl_by_symbol.values(), Decimal(0)),
            "net_pnl_marked": net_pnl_marked,
            "residual": net_pnl_marked - sum(pnl_by_symbol.values(), Decimal(0)),
        },
    }


# ---------------------------------------------------------------------------
# Predeclared classification (frozen in Issue #350 before any empirical
# result was read).
# ---------------------------------------------------------------------------

CLASSIFICATION_PRACTICAL_TARGET_FEASIBLE = "PRACTICAL_TARGET_FEASIBLE"
CLASSIFICATION_SAFE_BUT_BELOW_TARGET = "SAFE_BUT_BELOW_TARGET"
CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT = "POSITIVE_BUT_INSUFFICIENT"
CLASSIFICATION_NOT_PRACTICAL = "NOT_PRACTICAL"

PRACTICAL_TARGET_MONTHLY_DOLLARS = Decimal("250")
PRACTICAL_STRETCH_MONTHLY_DOLLARS = Decimal("400")  # diagnostic only -- never gates a label
SAFE_MIN_MONTHLY_DOLLARS = Decimal("150")
PRACTICAL_MAX_DRAWDOWN_PCT = Decimal("0.15")
NOT_PRACTICAL_MAX_DRAWDOWN_PCT = Decimal("0.20")
PRACTICAL_MIN_PROFITABLE_MONTH_PCT = Decimal("0.55")
PRACTICAL_WORST_MONTH_FLOOR_PCT = Decimal("-0.10")
MAX_SINGLE_CONTRIBUTOR_POSITIVE_SHARE = Decimal("0.50")
ALL_FIVE_CALENDAR_SEGMENTS = ("2022", "2023", "2024", "2025", "2026_YTD")
PRACTICAL_MIN_NONNEGATIVE_SEGMENTS = 4
PRACTICAL_MIN_POSITIVE_SEGMENTS = 3


def classify_practical_core_satellite_portfolio(
    *, mean_monthly_dollars_base: Decimal, segment_net_returns: Mapping[str, Decimal],
    max_drawdown_pct: Decimal, pooled_net_base: Decimal, pooled_net_stress: Decimal,
    profitable_month_pct: Decimal | None, worst_month_pct: Decimal | None,
    best_single_contributor_positive_share: Decimal | None, integrity_ok: bool,
) -> dict[str, Any]:
    """Applies the frozen four-label M49 gate exactly (Issue #350). No
    label may be promoted for being close to a threshold -- every
    boundary below is applied literally, as specified."""
    if not integrity_ok:
        return {"verdict": CLASSIFICATION_NOT_PRACTICAL, "reason": "data-integrity failure", "checks": {}}
    if max_drawdown_pct > NOT_PRACTICAL_MAX_DRAWDOWN_PCT:
        return {"verdict": CLASSIFICATION_NOT_PRACTICAL, "reason": f"max_drawdown_pct={max_drawdown_pct} exceeds {NOT_PRACTICAL_MAX_DRAWDOWN_PCT}", "checks": {}}
    if pooled_net_base <= 0 or pooled_net_stress <= 0:
        return {
            "verdict": CLASSIFICATION_NOT_PRACTICAL,
            "reason": f"pooled_net_base={pooled_net_base} pooled_net_stress={pooled_net_stress} -- one or both non-positive",
            "checks": {},
        }

    concentration_ok = best_single_contributor_positive_share is None or best_single_contributor_positive_share <= MAX_SINGLE_CONTRIBUTOR_POSITIVE_SHARE
    nonneg_segments = sum(1 for seg in ALL_FIVE_CALENDAR_SEGMENTS if segment_net_returns.get(seg, Decimal("-1")) >= 0)
    pos_segments = sum(1 for seg in ALL_FIVE_CALENDAR_SEGMENTS if segment_net_returns.get(seg, Decimal("-1")) > 0)

    feasible_checks = {
        "mean_monthly_dollars_at_least_250": mean_monthly_dollars_base >= PRACTICAL_TARGET_MONTHLY_DOLLARS,
        "max_drawdown_at_most_15pct": max_drawdown_pct <= PRACTICAL_MAX_DRAWDOWN_PCT,
        "pooled_net_base_and_stress_positive": pooled_net_base > 0 and pooled_net_stress > 0,
        "at_least_4_of_5_segments_nonnegative": nonneg_segments >= PRACTICAL_MIN_NONNEGATIVE_SEGMENTS,
        "at_least_3_of_5_segments_positive": pos_segments >= PRACTICAL_MIN_POSITIVE_SEGMENTS,
        "profitable_months_at_least_55pct": profitable_month_pct is not None and profitable_month_pct >= PRACTICAL_MIN_PROFITABLE_MONTH_PCT,
        "worst_month_at_least_negative_10pct": worst_month_pct is not None and worst_month_pct >= PRACTICAL_WORST_MONTH_FLOOR_PCT,
        "no_sleeve_or_symbol_over_50pct_of_positive_pnl": concentration_ok,
    }
    stretch_reached = mean_monthly_dollars_base >= PRACTICAL_STRETCH_MONTHLY_DOLLARS
    if all(feasible_checks.values()):
        return {"verdict": CLASSIFICATION_PRACTICAL_TARGET_FEASIBLE, "checks": feasible_checks, "stretch_400_reached": stretch_reached}

    safe_checks = {
        "mean_monthly_dollars_at_least_150_below_250": SAFE_MIN_MONTHLY_DOLLARS <= mean_monthly_dollars_base < PRACTICAL_TARGET_MONTHLY_DOLLARS,
        "max_drawdown_at_most_15pct": max_drawdown_pct <= PRACTICAL_MAX_DRAWDOWN_PCT,
        "pooled_net_base_and_stress_positive": pooled_net_base > 0 and pooled_net_stress > 0,
        "at_least_4_of_5_segments_nonnegative": nonneg_segments >= PRACTICAL_MIN_NONNEGATIVE_SEGMENTS,
    }
    if all(safe_checks.values()):
        return {"verdict": CLASSIFICATION_SAFE_BUT_BELOW_TARGET, "checks": safe_checks, "feasible_checks": feasible_checks, "stretch_400_reached": stretch_reached}

    # Positive pooled net return after BOTH cost cases (already
    # established above by the early NOT_PRACTICAL return) but one or
    # more FEASIBLE/SAFE gate failed.
    return {
        "verdict": CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT,
        "checks": safe_checks, "feasible_checks": feasible_checks, "stretch_400_reached": stretch_reached,
    }
