"""Research tooling for M43: profit-protection exit experiment (Issue #336).

M42 independently validated `WAITED_FIRST_VALID_0950` (Issue #332) as
MIXED/PROVISIONAL: R-positive but not beating `CURRENT_FIRST_VALID` on the
one fully independent period tested. Earlier R-normalized MFE analysis
found a substantial fraction of eventual losers first reach favorable
excursion, including roughly the +0.50R region. This module asks a single,
narrow question: can a small, predeclared stop-management policy protect
some of that favorable excursion without materially destroying the
strategy's R-normalized expectancy?

This is EXIT research only. It reuses the M41/M42 entry-policy
implementation completely unmodified (`early_pool`, `enrich_with_decision_time`,
`select_first_candidate_per_day`, and the SignalEngine re-evaluation
mechanics inside what M41 calls `build_wait_row`) -- no gap/RVOL threshold,
09:50 cutoff, candidate ranking/universe, second trade, sector logic, or
position-sizing rule is touched anywhere in this module.

Why a research-only replay, not a production change
-----------------------------------------------------
`src/exit_engine.py`'s `ExitEngine.evaluate` raises if a position's current
stop is above its entry price ("Current stop cannot be above entry in V0").
Two of the four frozen arms here (`PROFIT_LADDER`, `STRUCTURE_TRAIL_AFTER_1R`)
deliberately move the stop above entry once favorable excursion is large
enough -- production's pinned V0 exit engine cannot represent that state at
all, by design. Rather than weaken that invariant (forbidden -- pinned exit
behavior may not change during this milestone), this module never
constructs a `PositionState`/calls `ExitEngine` for the three protection
arms. Instead:

- Entry-fill mechanics (limit-touch conservative fill, same-bar entry
  ambiguity, market-data validation, candidate-identity/digest checks) are
  entirely reused by calling the unmodified, pinned
  `src.shadow_execution_simulator.simulate_shadow_execution` once per
  candidate to get a REFERENCE result. Every one of those mechanics is
  policy-independent (they only ever look at the fixed *initial* stop),
  so the reference call is authoritative for whether/when/at-what-price a
  trade enters, for every arm.
- The profit target is also policy-independent: `ExitEngine`'s
  `full_profit_exit_at_r` formula is `entry_price + (entry_price -
  initial_stop_price) * profit_target_r` -- a function of the *initial*
  stop only, never the evolving current stop. `reference.target_price` is
  therefore correct and reused unchanged for every arm.
- `BASELINE` returns the reference result outright: zero research
  modification, byte-identical to production by construction (verified by
  `test_baseline_arm_is_byte_identical_to_production_simulator`).
- `BE_AFTER_050R`/`PROFIT_LADDER`/`STRUCTURE_TRAIL_AFTER_1R` re-walk only
  the bars strictly after the reference entry bar, replicating the
  identical conservative same-bar priority order the pinned simulator uses
  (ambiguous-if-both-stop-and-target-touched > stop > target > time-exit >
  else advance the stop for the NEXT bar only) with a pluggable
  stop-management rule in place of the pinned +1R-breakeven-only rule.

No lookahead: each bar's own stop/target/time-exit outcome is always
decided using the stop that was already in effect *entering* that bar; a
policy's stop update is computed only after that bar's own outcome is
settled, and takes effect starting the following bar.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence

from src.exchange_calendar import EASTERN, regular_session_bounds
from src.config import load_config
from src.market_data_canonical import MarketBar
from src.orb_candidate_selection_timing_research import (
    CONFIG_ENTRY,
    CONFIG_LABEL,
    DecisionTimeCandidate,
    early_pool,
    enrich_with_decision_time,
)
from src.orb_gap_rvol_sensitivity_research import (
    CandidateSignal,
    ComboEvaluation,
    InputsTable,
    build_candidate_shadow_decision,
    sensitivity_config,
)
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_regime_diagnostic_research import TradeDiagnosticRow
from src.shadow_execution_simulator import ShadowExecutionResult, ShadowExecutionState, simulate_shadow_execution
from src.shadow_trading_decision import ShadowTradingDecision
from src.signal_engine import SignalEngine

__all__ = [
    "ProfitProtectionExitError",
    "CONFIG_LABEL",
    "ENTRY_CURRENT_FIRST_VALID",
    "ENTRY_WAITED_FIRST_VALID_0950",
    "ENTRY_POLICIES",
    "ARM_BASELINE",
    "ARM_BE_AFTER_050R",
    "ARM_PROFIT_LADDER",
    "ARM_STRUCTURE_TRAIL_AFTER_1R",
    "EXIT_ARMS",
    "PROTECTION_ARMS",
    "ExitArmOutcome",
    "simulate_exit_arm",
    "build_all_entry_and_exit_rows",
    "max_drawdown_r_and_streak_from_rows",
    "full_stop_out_rate",
    "protection_diagnostics",
    "realized_r_distribution",
]


class ProfitProtectionExitError(RuntimeError):
    """Refused: M43 profit-protection exit research tooling could not
    safely proceed."""


# ---------------------------------------------------------------------------
# Frozen entry policies -- both reuse M41/M42's own implementation exactly.

ENTRY_CURRENT_FIRST_VALID = "CURRENT_FIRST_VALID"
ENTRY_WAITED_FIRST_VALID_0950 = "WAITED_FIRST_VALID_0950"
ENTRY_POLICIES: tuple[str, str] = (ENTRY_CURRENT_FIRST_VALID, ENTRY_WAITED_FIRST_VALID_0950)


# ---------------------------------------------------------------------------
# Frozen exit arms -- exactly four, predeclared before any empirical result.

ARM_BASELINE = "BASELINE"
ARM_BE_AFTER_050R = "BE_AFTER_050R"
ARM_PROFIT_LADDER = "PROFIT_LADDER"
ARM_STRUCTURE_TRAIL_AFTER_1R = "STRUCTURE_TRAIL_AFTER_1R"
EXIT_ARMS: tuple[str, ...] = (ARM_BASELINE, ARM_BE_AFTER_050R, ARM_PROFIT_LADDER, ARM_STRUCTURE_TRAIL_AFTER_1R)
PROTECTION_ARMS: tuple[str, ...] = (ARM_BE_AFTER_050R, ARM_PROFIT_LADDER, ARM_STRUCTURE_TRAIL_AFTER_1R)

_R_025 = Decimal("0.25")
_R_050 = Decimal("0.50")
_R_075 = Decimal("0.75")
_R_100 = Decimal("1.00")

_TIME_EXIT_ET = time.fromisoformat(load_config()["exit"]["time_exit"])

# Reporting-only epsilons (float R units) for classifying a converted full
# loss as breakeven/small-win/reduced-loss in `protection_diagnostics` --
# never used inside `simulate_exit_arm`'s own stop-management decisions,
# which are exact Decimal comparisons against the frozen thresholds above.
_DELTA_EPSILON = 1e-9
_BREAKEVEN_EPSILON = 0.05


def _policy_be_after_050r(*, entry_price: Decimal, risk: Decimal, current_stop: Decimal, mfe: Decimal, completed_bar_low: Decimal) -> Decimal:
    if mfe >= risk * _R_050:
        return max(current_stop, entry_price)
    return current_stop


def _policy_profit_ladder(*, entry_price: Decimal, risk: Decimal, current_stop: Decimal, mfe: Decimal, completed_bar_low: Decimal) -> Decimal:
    if mfe >= risk * _R_100:
        return max(current_stop, entry_price + risk * _R_050)
    if mfe >= risk * _R_075:
        return max(current_stop, entry_price + risk * _R_025)
    if mfe >= risk * _R_050:
        return max(current_stop, entry_price)
    return current_stop


def _policy_structure_trail_after_1r(*, entry_price: Decimal, risk: Decimal, current_stop: Decimal, mfe: Decimal, completed_bar_low: Decimal) -> Decimal:
    if mfe >= risk * _R_100:
        return max(current_stop, entry_price, completed_bar_low)
    return current_stop


_POLICIES: Mapping[str, Callable[..., Decimal]] = {
    ARM_BE_AFTER_050R: _policy_be_after_050r,
    ARM_PROFIT_LADDER: _policy_profit_ladder,
    ARM_STRUCTURE_TRAIL_AFTER_1R: _policy_structure_trail_after_1r,
}


# ---------------------------------------------------------------------------
# Exit-arm replay engine.

@dataclass(frozen=True)
class ExitArmOutcome:
    arm: str
    state: ShadowExecutionState
    entry_price: Decimal | None
    initial_stop_price: Decimal | None
    final_stop_price: Decimal | None
    target_price: Decimal | None
    exit_price: Decimal | None
    exit_reason: str
    realized_pnl: Decimal | None
    maximum_favorable_excursion: Decimal | None
    maximum_adverse_excursion: Decimal | None
    bars_evaluated_after_entry: int
    protection_activated: bool


def _outcome_from_reference(arm: str, reference: ShadowExecutionResult) -> ExitArmOutcome:
    protection_activated = (
        reference.final_stop_price is not None and reference.initial_stop_price is not None
        and reference.final_stop_price != reference.initial_stop_price
    )
    return ExitArmOutcome(
        arm=arm, state=reference.state, entry_price=reference.entry_price,
        initial_stop_price=reference.initial_stop_price, final_stop_price=reference.final_stop_price,
        target_price=reference.target_price, exit_price=reference.exit_price, exit_reason=reference.exit_reason,
        realized_pnl=reference.realized_pnl, maximum_favorable_excursion=reference.maximum_favorable_excursion,
        maximum_adverse_excursion=reference.maximum_adverse_excursion,
        bars_evaluated_after_entry=reference.bars_evaluated_after_entry, protection_activated=protection_activated,
    )


def _replay_post_entry(
    decision: ShadowTradingDecision, subsequent_bars: Sequence[MarketBar], reference: ShadowExecutionResult, *, arm: str,
) -> ExitArmOutcome:
    entry_price = reference.entry_price
    initial_stop = reference.initial_stop_price
    target = reference.target_price
    if entry_price is None or initial_stop is None:
        raise ProfitProtectionExitError("reference result missing entry/stop for post-entry replay")
    risk = entry_price - initial_stop
    if risk <= 0:
        raise ProfitProtectionExitError("reference result has non-positive initial risk")

    eligible = [bar for bar in subsequent_bars if bar.event_timestamp >= decision.signal_timestamp]
    entry_index = next((i for i, b in enumerate(eligible) if b.event_timestamp == reference.entry_timestamp), None)
    if entry_index is None:
        raise ProfitProtectionExitError("could not locate the reference entry bar while replaying an exit arm")
    if eligible[entry_index].low > entry_price:
        raise ProfitProtectionExitError("entry bar identity mismatch while replaying an exit arm")

    policy = _POLICIES[arm]
    current_stop = initial_stop
    mfe = Decimal("0")
    mae = Decimal("0")
    tail = eligible[entry_index + 1:]

    def _result(*, state: ShadowExecutionState, exit_price: Decimal | None, exit_reason: str, realized_pnl: Decimal | None, bars_after: int) -> ExitArmOutcome:
        return ExitArmOutcome(
            arm=arm, state=state, entry_price=entry_price, initial_stop_price=initial_stop,
            final_stop_price=current_stop, target_price=target, exit_price=exit_price, exit_reason=exit_reason,
            realized_pnl=realized_pnl, maximum_favorable_excursion=mfe, maximum_adverse_excursion=mae,
            bars_evaluated_after_entry=bars_after, protection_activated=current_stop != initial_stop,
        )

    for bars_after, later in enumerate(tail, start=1):
        mfe = max(mfe, later.high - entry_price)
        mae = min(mae, later.low - entry_price)
        if later.low <= current_stop and target is not None and later.high >= target:
            return _result(state=ShadowExecutionState.AMBIGUOUS, exit_price=None, exit_reason="", realized_pnl=None, bars_after=bars_after)
        if later.low <= current_stop:
            pnl = current_stop - entry_price
            return _result(state=ShadowExecutionState.STOPPED, exit_price=current_stop, exit_reason="protective_stop", realized_pnl=pnl, bars_after=bars_after)
        if target is not None and later.high >= target:
            pnl = target - entry_price
            return _result(state=ShadowExecutionState.TARGET_HIT, exit_price=target, exit_reason="profit_target", realized_pnl=pnl, bars_after=bars_after)
        if later.event_timestamp.astimezone(EASTERN).time() >= _TIME_EXIT_ET:
            pnl = later.close - entry_price
            return _result(state=ShadowExecutionState.TIME_EXIT, exit_price=later.close, exit_reason="time_exit", realized_pnl=pnl, bars_after=bars_after)
        current_stop = policy(entry_price=entry_price, risk=risk, current_stop=current_stop, mfe=mfe, completed_bar_low=later.low)

    return _result(state=ShadowExecutionState.FILLED_OPEN, exit_price=None, exit_reason="", realized_pnl=None, bars_after=len(tail))


def simulate_exit_arm(
    decision: ShadowTradingDecision, subsequent_bars: Sequence[MarketBar], *, arm: str,
    session_bounds_provider: Callable[[date], tuple[time, time] | None] = regular_session_bounds,
) -> ExitArmOutcome:
    """The one entry point every exit arm goes through. `BASELINE` and any
    trade that never enters (or whose market data is invalid) reuse the
    pinned, unmodified `simulate_shadow_execution` result outright -- see
    module docstring for why this is exact, not an approximation."""
    if arm not in EXIT_ARMS:
        raise ProfitProtectionExitError(f"unknown exit arm {arm!r}")
    reference = simulate_shadow_execution(decision, subsequent_bars, session_bounds_provider=session_bounds_provider)
    if arm == ARM_BASELINE or reference.entry_timestamp is None:
        return _outcome_from_reference(arm, reference)
    return _replay_post_entry(decision, subsequent_bars, reference, arm=arm)


# ---------------------------------------------------------------------------
# Outcome/decision dict construction -- same shape/units convention as
# `orb_candidate_selection_timing_research._outcome_fields`, extended with
# `final_stop_price`/`protection_activated`/`is_full_stop_loss` (needed for
# this study's own protection diagnostics).

def _outcome_dict(outcome: ExitArmOutcome) -> dict[str, Any]:
    entered = outcome.entry_price is not None
    closed = outcome.realized_pnl is not None
    risk = (outcome.entry_price - outcome.initial_stop_price) if (entered and outcome.initial_stop_price is not None) else None
    r_multiple = float(outcome.realized_pnl / risk) if (closed and risk and risk > 0) else None
    mfe_r = float(outcome.maximum_favorable_excursion / risk) if (
        entered and risk and risk > 0 and outcome.maximum_favorable_excursion is not None
    ) else None
    mae_r = float(outcome.maximum_adverse_excursion / risk) if (
        entered and risk and risk > 0 and outcome.maximum_adverse_excursion is not None
    ) else None
    is_stopped = outcome.state.value == "STOPPED"
    is_full_stop_loss = bool(is_stopped and outcome.final_stop_price == outcome.initial_stop_price)
    return {
        "state": outcome.state.value, "entered": entered, "closed": closed,
        "entry_price": str(outcome.entry_price) if outcome.entry_price is not None else None,
        "initial_stop_price": str(outcome.initial_stop_price) if outcome.initial_stop_price is not None else None,
        "final_stop_price": str(outcome.final_stop_price) if outcome.final_stop_price is not None else None,
        "target_price": str(outcome.target_price) if outcome.target_price is not None else None,
        "exit_price": str(outcome.exit_price) if outcome.exit_price is not None else None,
        "exit_reason": outcome.exit_reason,
        "realized_pnl": str(outcome.realized_pnl) if outcome.realized_pnl is not None else None,
        "r_multiple": r_multiple,
        "is_winner": (outcome.realized_pnl > 0) if closed else None,
        "is_stopped": is_stopped,
        "is_full_stop_loss": is_full_stop_loss,
        "initial_risk_r": str(risk) if risk is not None else None,
        "maximum_favorable_excursion": (
            str(outcome.maximum_favorable_excursion) if outcome.maximum_favorable_excursion is not None else None
        ),
        "maximum_adverse_excursion": (
            str(outcome.maximum_adverse_excursion) if outcome.maximum_adverse_excursion is not None else None
        ),
        "mfe_r": mfe_r, "mae_r": mae_r,
        "protection_activated": outcome.protection_activated,
        "bars_evaluated_after_entry": outcome.bars_evaluated_after_entry,
    }


def _no_decision_time_data_outcome() -> dict[str, Any]:
    return {
        "state": "NO_DECISION_TIME_DATA", "entered": False, "closed": False,
        "entry_price": None, "initial_stop_price": None, "final_stop_price": None, "target_price": None,
        "exit_price": None, "exit_reason": None, "realized_pnl": None, "r_multiple": None,
        "is_winner": None, "is_stopped": False, "is_full_stop_loss": False, "initial_risk_r": None,
        "maximum_favorable_excursion": None, "maximum_adverse_excursion": None, "mfe_r": None, "mae_r": None,
        "protection_activated": False, "bars_evaluated_after_entry": 0,
    }


def _session_bars(bars: Sequence[MarketBar], *, session_date: date) -> list[MarketBar]:
    return sorted((bar for bar in bars if bar.trading_date == session_date), key=lambda bar: bar.event_timestamp)


def _decision_features(*, period: str, entry_policy: str, symbol: str, session_date: date, original_timestamp: datetime) -> dict[str, Any]:
    return {
        "period": period, "entry_policy": entry_policy, "config": CONFIG_LABEL, "symbol": symbol,
        "session_date": session_date.isoformat(), "month": session_date.strftime("%Y-%m"),
        "quarter": f"{session_date.year}-Q{(session_date.month - 1) // 3 + 1}",
        "original_qualifying_timestamp": original_timestamp.isoformat(),
    }


# ---------------------------------------------------------------------------
# Shadow-decision construction per entry policy -- duplicates only the
# decision/bar-preparation half of `orb_baseline_stability_research.
# build_stability_rows` and `orb_candidate_selection_timing_research.
# build_wait_row` (never their final `simulate_shadow_execution` call,
# which this module replaces with one call per exit arm).

def _current_shadow_decision(
    candidate: CandidateSignal, *, combo_config: Mapping[str, Any], strategy_version: str,
    implementation_digest: str, shadow_run_id: str, created_at: datetime,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]],
) -> tuple[ShadowTradingDecision, list[MarketBar]]:
    symbol, session_date, opening_data, live_data, decision = candidate
    candidate_bars = market_bars_by_symbol[symbol]
    spy_bars = market_bars_by_symbol["SPY"]
    shadow_decision = build_candidate_shadow_decision(
        symbol=symbol, session_date=session_date, opening_data=opening_data, live_data=live_data,
        decision=decision, combo_config=combo_config, strategy_version=strategy_version,
        implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
        candidate_market_bars=candidate_bars, spy_market_bars=spy_bars,
    )
    session_bars = _session_bars(candidate_bars, session_date=session_date)
    subsequent_bars = [b for b in session_bars if b.event_timestamp >= shadow_decision.signal_timestamp]
    return shadow_decision, subsequent_bars


def _waited_shadow_decision(
    candidate: DecisionTimeCandidate, *, combo_config: Mapping[str, Any], strategy_version: str,
    implementation_digest: str, shadow_run_id: str, created_at: datetime,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]],
) -> tuple[ShadowTradingDecision | None, list[MarketBar], bool | None]:
    if candidate.decision_live_data is None:
        return None, [], None

    evaluation_config = copy.deepcopy(dict(combo_config))
    evaluation_config["strategy"]["enabled"] = True  # in-memory only; never persisted
    engine = SignalEngine(evaluation_config)
    new_decision = engine.evaluate(candidate.opening_data, candidate.decision_live_data)

    candidate_bars = market_bars_by_symbol[candidate.symbol]
    spy_bars = market_bars_by_symbol["SPY"]
    shadow_decision = build_candidate_shadow_decision(
        symbol=candidate.symbol, session_date=candidate.session_date, opening_data=candidate.opening_data,
        live_data=candidate.decision_live_data, decision=new_decision, combo_config=combo_config,
        strategy_version=strategy_version, implementation_digest=implementation_digest,
        shadow_run_id=shadow_run_id, created_at=created_at,
        candidate_market_bars=candidate_bars, spy_market_bars=spy_bars,
    )
    session_bars = _session_bars(candidate_bars, session_date=candidate.session_date)
    subsequent_bars = [b for b in session_bars if b.event_timestamp >= shadow_decision.signal_timestamp]
    return shadow_decision, subsequent_bars, new_decision.approved


def _build_rows_for_current(
    candidates: Sequence[CandidateSignal], *, period: str, market_bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    combo_config: Mapping[str, Any], strategy_version: str, implementation_digest: str, created_at: datetime,
    session_bounds_provider=None, arms: Sequence[str] = EXIT_ARMS,
) -> dict[str, tuple[TradeDiagnosticRow, ...]]:
    rows_by_arm: dict[str, list[TradeDiagnosticRow]] = {arm: [] for arm in arms}
    shadow_run_id = f"m43-{period}-{ENTRY_CURRENT_FIRST_VALID}-{CONFIG_LABEL}"
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    for candidate in candidates:
        symbol, session_date, _opening, live_data, _decision = candidate
        shadow_decision, subsequent_bars = _current_shadow_decision(
            candidate, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol,
        )
        decision_features = _decision_features(
            period=period, entry_policy=ENTRY_CURRENT_FIRST_VALID, symbol=symbol,
            session_date=session_date, original_timestamp=live_data.timestamp,
        )
        for arm in arms:
            outcome = simulate_exit_arm(shadow_decision, subsequent_bars, arm=arm, **kwargs)
            rows_by_arm[arm].append(TradeDiagnosticRow(decision={**decision_features, "exit_arm": arm}, outcome=_outcome_dict(outcome)))
    return {arm: tuple(rows) for arm, rows in rows_by_arm.items()}


def _build_rows_for_waited(
    waited_enriched: Sequence[DecisionTimeCandidate], *, period: str, market_bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    combo_config: Mapping[str, Any], strategy_version: str, implementation_digest: str, created_at: datetime,
    session_bounds_provider=None, arms: Sequence[str] = EXIT_ARMS,
) -> dict[str, tuple[TradeDiagnosticRow, ...]]:
    rows_by_arm: dict[str, list[TradeDiagnosticRow]] = {arm: [] for arm in arms}
    shadow_run_id = f"m43-{period}-{ENTRY_WAITED_FIRST_VALID_0950}-{CONFIG_LABEL}"
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    for candidate in waited_enriched:
        shadow_decision, subsequent_bars, wait_approved = _waited_shadow_decision(
            candidate, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol,
        )
        decision_features = _decision_features(
            period=period, entry_policy=ENTRY_WAITED_FIRST_VALID_0950, symbol=candidate.symbol,
            session_date=candidate.session_date, original_timestamp=candidate.original_live_data.timestamp,
        )
        decision_features["wait_reevaluation_approved"] = wait_approved
        if shadow_decision is None:
            for arm in arms:
                rows_by_arm[arm].append(TradeDiagnosticRow(decision={**decision_features, "exit_arm": arm}, outcome=_no_decision_time_data_outcome()))
            continue
        for arm in arms:
            outcome = simulate_exit_arm(shadow_decision, subsequent_bars, arm=arm, **kwargs)
            rows_by_arm[arm].append(TradeDiagnosticRow(decision={**decision_features, "exit_arm": arm}, outcome=_outcome_dict(outcome)))
    return {arm: tuple(rows) for arm, rows in rows_by_arm.items()}


def build_all_entry_and_exit_rows(
    *, period: str, evaluation: ComboEvaluation, inputs_table: InputsTable,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]], base_config: Mapping[str, Any],
    implementation_digest: str, created_at: datetime, session_bounds_provider=None, arms: Sequence[str] = EXIT_ARMS,
) -> dict[str, dict[str, tuple[TradeDiagnosticRow, ...]]]:
    """`{entry_policy: {exit_arm: rows}}` for both frozen entry policies and
    every requested exit arm, from one already-computed full-window
    `ComboEvaluation` + `InputsTable`. No new candidate generation beyond
    what `evaluate_combo` (unmodified) already produced; the WAITED policy
    reuses M41's own `early_pool`/`enrich_with_decision_time` unmodified."""
    if evaluation.combo != CONFIG_ENTRY.combo:
        raise ProfitProtectionExitError("build_all_entry_and_exit_rows requires the frozen GAP_050_RVOL_175 combo")

    combo_config = sensitivity_config(base_config, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"

    full_signals = evaluation.candidate_signals
    early_signals = early_pool(full_signals)
    current_winners = select_first_candidate_per_day(full_signals)
    early_winners = select_first_candidate_per_day(early_signals)
    waited_enriched = tuple(enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_winners)

    current_rows = _build_rows_for_current(
        current_winners, period=period, market_bars_by_symbol=market_bars_by_symbol, combo_config=combo_config,
        strategy_version=strategy_version, implementation_digest=implementation_digest, created_at=created_at,
        session_bounds_provider=session_bounds_provider, arms=arms,
    )
    waited_rows = _build_rows_for_waited(
        waited_enriched, period=period, market_bars_by_symbol=market_bars_by_symbol, combo_config=combo_config,
        strategy_version=strategy_version, implementation_digest=implementation_digest, created_at=created_at,
        session_bounds_provider=session_bounds_provider, arms=arms,
    )
    return {ENTRY_CURRENT_FIRST_VALID: current_rows, ENTRY_WAITED_FIRST_VALID_0950: waited_rows}


# ---------------------------------------------------------------------------
# R-normalized reporting helpers. `orb_baseline_fourth_period_research.
# max_drawdown_and_streak_from_rows` accumulates raw `realized_pnl`
# (dollars per share) -- not comparable across trades with different stop
# widths (see `orb_regime_diagnostic_research.trade_quality_from_rows`'s own
# docstring). This study's own predeclared classification is R-based
# throughout, so drawdown here is computed from each trade's own
# `r_multiple` instead.

def max_drawdown_r_and_streak_from_rows(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    closed = [r for r in rows if r.outcome["closed"] and r.outcome["r_multiple"] is not None]
    ordered = sorted(closed, key=lambda r: (r.decision["session_date"], r.decision["symbol"]))
    running = 0.0
    peak = 0.0
    max_drawdown_r = 0.0
    longest_losing_streak = 0
    current_streak = 0
    for row in ordered:
        r = row.outcome["r_multiple"]
        running += r
        peak = max(peak, running)
        max_drawdown_r = max(max_drawdown_r, peak - running)
        if r <= 0:
            current_streak += 1
            longest_losing_streak = max(longest_losing_streak, current_streak)
        else:
            current_streak = 0
    return {"maximum_drawdown_r": max_drawdown_r, "longest_losing_streak": longest_losing_streak}


def full_stop_out_rate(rows: Sequence[TradeDiagnosticRow]) -> float | None:
    closed = [r for r in rows if r.outcome["closed"]]
    if not closed:
        return None
    full_stops = sum(1 for r in closed if r.outcome["is_full_stop_loss"])
    return full_stops / len(closed)


def realized_r_distribution(rows: Sequence[TradeDiagnosticRow]) -> list[float]:
    return sorted(r.outcome["r_multiple"] for r in rows if r.outcome["closed"] and r.outcome["r_multiple"] is not None)


def protection_diagnostics(
    *, baseline_rows: Sequence[TradeDiagnosticRow], arm_rows: Sequence[TradeDiagnosticRow],
) -> dict[str, Any]:
    """Paired, trade-by-trade comparison of one protection arm against
    `BASELINE` on the SAME entry policy. `baseline_rows`/`arm_rows` are
    built from the identical candidate list in the identical order (both
    are one of `build_all_entry_and_exit_rows`'s own per-entry-policy exit-
    arm row tuples), so index-aligned pairing is exact -- verified
    defensively per-pair below rather than re-matched by day/symbol."""
    if len(baseline_rows) != len(arm_rows):
        raise ProfitProtectionExitError("baseline_rows/arm_rows must be the same length to pair")

    paired_deltas: list[float] = []
    activated = 0
    winners_clipped = 0
    r_lost_from_clipped = 0.0
    improved = worsened = unchanged = 0
    converted_to_breakeven = 0
    converted_to_small_win = 0
    converted_to_reduced_loss = 0
    baseline_full_losses = 0

    for b_row, a_row in zip(baseline_rows, arm_rows):
        if b_row.decision["symbol"] != a_row.decision["symbol"] or b_row.decision["session_date"] != a_row.decision["session_date"]:
            raise ProfitProtectionExitError("baseline_rows/arm_rows are not aligned to the same candidate occurrences")
        if not (b_row.outcome["closed"] and a_row.outcome["closed"]):
            continue

        b_r = b_row.outcome["r_multiple"]
        a_r = a_row.outcome["r_multiple"]
        delta = a_r - b_r
        paired_deltas.append(delta)

        if a_row.outcome["protection_activated"]:
            activated += 1

        if b_row.outcome["is_full_stop_loss"]:
            baseline_full_losses += 1
            if not a_row.outcome["is_full_stop_loss"]:
                if abs(a_r) <= _BREAKEVEN_EPSILON:
                    converted_to_breakeven += 1
                elif a_r > _BREAKEVEN_EPSILON:
                    converted_to_small_win += 1
                elif a_r > -1.0:
                    converted_to_reduced_loss += 1

        if b_r > 0 and a_r < b_r and a_row.outcome["is_stopped"]:
            winners_clipped += 1
            r_lost_from_clipped += (b_r - a_r)

        if delta > _DELTA_EPSILON:
            improved += 1
        elif delta < -_DELTA_EPSILON:
            worsened += 1
        else:
            unchanged += 1

    n = len(paired_deltas)
    return {
        "paired_trades": n,
        "activation_count": activated,
        "activation_rate": (activated / n) if n else None,
        "baseline_full_losses": baseline_full_losses,
        "full_losses_converted_to_breakeven": converted_to_breakeven,
        "full_losses_converted_to_small_win": converted_to_small_win,
        "full_losses_converted_to_reduced_loss": converted_to_reduced_loss,
        "full_losses_converted_total": converted_to_breakeven + converted_to_small_win + converted_to_reduced_loss,
        "winners_clipped": winners_clipped,
        "r_lost_from_clipped_winners": r_lost_from_clipped,
        "trades_improved": improved,
        "trades_worsened": worsened,
        "trades_unchanged": unchanged,
        "paired_mean_delta_r": (sum(paired_deltas) / n) if n else None,
    }
