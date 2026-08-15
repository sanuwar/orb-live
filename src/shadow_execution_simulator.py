"""Deterministic, offline-only execution outcomes for shadow ORB candidates.

This module consumes immutable shadow decisions and canonical market bars.  It
has no transport, broker, account, order, or authority dependencies.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Sequence

from src.config import load_config
from src.exchange_calendar import regular_session_bounds
from src.exit_engine import ExitAction, ExitEngine, PositionState
from src.market_data_canonical import (
    MarketBar,
    MarketDataFreshnessPolicy,
    MarketDataQuality,
    compute_market_data_batch_digest,
    compute_market_data_digest,
    validate_market_bar,
    validate_market_data_batch,
)
from src.protection_observation_model import canonical_bytes
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
    compute_shadow_decision_digest,
)

FILL_POLICY_VERSION = "limit-touch-conservative-v1"
SLIPPAGE_POLICY_VERSION = "zero-bps-baseline-v1"
SLIPPAGE_ASSUMPTION_BPS = Decimal("0")
EXIT_POLICY_VERSION = "orb-v0-exit-engine-v1"
BROKER_WRITE_COUNT = 0
NORMALIZATION_VERSION = "alpaca-market-data-v1"
HISTORICAL_POLICY = MarketDataFreshnessPolicy("shadow-execution-history-v1", 45 * 86400.0)
_DIGEST_DOMAIN = b"orb-live/shadow-execution-simulator/v1"


class ShadowExecutionState(str, Enum):
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    PENDING_NOT_FILLED = "PENDING_NOT_FILLED"
    FILLED_OPEN = "FILLED_OPEN"
    STOPPED = "STOPPED"
    TARGET_HIT = "TARGET_HIT"
    TIME_EXIT = "TIME_EXIT"
    AMBIGUOUS = "AMBIGUOUS"
    DATA_INVALID = "DATA_INVALID"


@dataclass(frozen=True)
class ShadowExecutionResult:
    simulation_id: str
    simulation_digest: str
    source_shadow_decision_id: str
    symbol: str
    trading_date: date
    strategy_version: str
    implementation_digest: str
    source_market_data_digest: str
    opening_range_identity: str
    signal_timestamp: datetime
    fill_policy_version: str
    slippage_policy_version: str
    slippage_assumption_bps: Decimal
    exit_policy_version: str
    input_market_data_digest: str
    state: ShadowExecutionState
    entry_timestamp: datetime | None
    entry_price: Decimal | None
    initial_stop_price: Decimal | None
    final_stop_price: Decimal | None
    target_price: Decimal | None
    exit_timestamp: datetime | None
    exit_price: Decimal | None
    exit_reason: str
    realized_pnl: Decimal | None
    realized_pnl_percent: Decimal | None
    maximum_favorable_excursion: Decimal | None
    maximum_adverse_excursion: Decimal | None
    bars_evaluated_after_entry: int
    reason_code: str
    broker_write_count: int
    def __post_init__(self) -> None:
        if self.broker_write_count != BROKER_WRITE_COUNT:
            raise ValueError("shadow execution may not record broker writes")
        if self.simulation_id != self.simulation_digest:
            raise ValueError("simulation identity must equal its digest")
        if not self.source_shadow_decision_id or not self.symbol or not self.strategy_version:
            raise ValueError("source candidate identity is required")
        if self.symbol != self.symbol.upper().strip():
            raise ValueError("symbol must be normalized")
        if not isinstance(self.trading_date, date) or isinstance(self.trading_date, datetime):
            raise ValueError("trading date must be a plain date")
        if self.signal_timestamp.tzinfo is None or self.signal_timestamp.utcoffset() is None:
            raise ValueError("signal timestamp must be timezone-aware")
        if self.entry_timestamp is not None and self.entry_timestamp.tzinfo is None:
            raise ValueError("entry timestamp must be timezone-aware")
        if self.exit_timestamp is not None and self.exit_timestamp.tzinfo is None:
            raise ValueError("exit timestamp must be timezone-aware")


def _decimal(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _timestamp(value: datetime | None) -> str:
    return "" if value is None else value.isoformat()


def _result_digest(fields: dict[str, Any]) -> str:
    return hashlib.sha256(_DIGEST_DOMAIN + b"\x00result\x00" + canonical_bytes(fields)).hexdigest()


def compute_shadow_execution_result_digest(result: ShadowExecutionResult) -> str:
    """Independently reconstruct an immutable simulation result identity."""
    if not isinstance(result, ShadowExecutionResult):
        raise TypeError("result must be a ShadowExecutionResult")
    fields = {
        "source_shadow_decision_id": result.source_shadow_decision_id,
        "symbol": result.symbol,
        "trading_date": result.trading_date.isoformat(),
        "strategy_version": result.strategy_version,
        "implementation_digest": result.implementation_digest,
        "source_market_data_digest": result.source_market_data_digest,
        "opening_range_identity": result.opening_range_identity,
        "signal_timestamp": result.signal_timestamp.isoformat(),
        "fill_policy_version": result.fill_policy_version,
        "slippage_policy_version": result.slippage_policy_version,
        "slippage_assumption_bps": _decimal(result.slippage_assumption_bps),
        "exit_policy_version": result.exit_policy_version,
        "input_market_data_digest": result.input_market_data_digest,
        "state": result.state.value,
        "entry_timestamp": _timestamp(result.entry_timestamp),
        "entry_price": _decimal(result.entry_price),
        "initial_stop_price": _decimal(result.initial_stop_price),
        "final_stop_price": _decimal(result.final_stop_price),
        "target_price": _decimal(result.target_price),
        "exit_timestamp": _timestamp(result.exit_timestamp),
        "exit_price": _decimal(result.exit_price),
        "exit_reason": result.exit_reason,
        "realized_pnl": _decimal(result.realized_pnl),
        "realized_pnl_percent": _decimal(result.realized_pnl_percent),
        "maximum_favorable_excursion": _decimal(result.maximum_favorable_excursion),
        "maximum_adverse_excursion": _decimal(result.maximum_adverse_excursion),
        "bars_evaluated_after_entry": result.bars_evaluated_after_entry,
        "reason_code": result.reason_code,
        "broker_write_count": result.broker_write_count,
    }
    return _result_digest(fields)

def _authentic_candidate(decision: ShadowTradingDecision) -> tuple[bool, str]:
    if not decision.is_candidate or decision.decision_type is not ShadowDecisionType.LONG_CANDIDATE:
        return False, "DECISION_NOT_LONG_CANDIDATE"
    expected = compute_shadow_decision_digest(
        shadow_run_id=decision.shadow_run_id,
        symbol=decision.symbol,
        trading_date=decision.trading_date,
        strategy_version=decision.strategy_version,
        implementation_digest=decision.implementation_digest,
        market_data_digest=decision.market_data_digest,
        opening_range_identity=decision.opening_range_identity,
        signal_timestamp=decision.signal_timestamp,
        decision_type=decision.decision_type,
        proposed_entry=decision.proposed_entry,
        proposed_stop=decision.proposed_stop,
        proposed_target=decision.proposed_target,
        reason=decision.reason,
        market_data_quality=decision.market_data_quality,
        exchange_session=decision.exchange_session,
    )
    if expected != decision.decision_id:
        return False, "DECISION_ID_MISMATCH"
    if decision.market_data_quality is not MarketDataQuality.VALID or decision.exchange_session != "REGULAR":
        return False, "DECISION_MARKET_DATA_NOT_VALID"
    if decision.proposed_entry is None or decision.proposed_stop is None or decision.proposed_stop >= decision.proposed_entry:
        return False, "DECISION_ENTRY_STOP_INVALID"
    return True, "ELIGIBLE"


def _build_result(
    decision: ShadowTradingDecision,
    *,
    input_market_data_digest: str,
    state: ShadowExecutionState,
    entry_timestamp: datetime | None = None,
    entry_price: Decimal | None = None,
    initial_stop_price: Decimal | None = None,
    final_stop_price: Decimal | None = None,
    target_price: Decimal | None = None,
    exit_timestamp: datetime | None = None,
    exit_price: Decimal | None = None,
    exit_reason: str = "",
    realized_pnl: Decimal | None = None,
    realized_pnl_percent: Decimal | None = None,
    mfe: Decimal | None = None,
    mae: Decimal | None = None,
    bars_evaluated_after_entry: int = 0,
    reason_code: str,
) -> ShadowExecutionResult:
    fields = {
        "source_shadow_decision_id": decision.decision_id,
        "symbol": decision.symbol,
        "trading_date": decision.trading_date.isoformat(),
        "strategy_version": decision.strategy_version,
        "implementation_digest": decision.implementation_digest,
        "source_market_data_digest": decision.market_data_digest,
        "opening_range_identity": decision.opening_range_identity,
        "signal_timestamp": decision.signal_timestamp.isoformat(),
        "fill_policy_version": FILL_POLICY_VERSION,
        "slippage_policy_version": SLIPPAGE_POLICY_VERSION,
        "slippage_assumption_bps": _decimal(SLIPPAGE_ASSUMPTION_BPS),
        "exit_policy_version": EXIT_POLICY_VERSION,
        "input_market_data_digest": input_market_data_digest,
        "state": state.value,
        "entry_timestamp": _timestamp(entry_timestamp),
        "entry_price": _decimal(entry_price),
        "initial_stop_price": _decimal(initial_stop_price),
        "final_stop_price": _decimal(final_stop_price),
        "target_price": _decimal(target_price),
        "exit_timestamp": _timestamp(exit_timestamp),
        "exit_price": _decimal(exit_price),
        "exit_reason": exit_reason,
        "realized_pnl": _decimal(realized_pnl),
        "realized_pnl_percent": _decimal(realized_pnl_percent),
        "maximum_favorable_excursion": _decimal(mfe),
        "maximum_adverse_excursion": _decimal(mae),
        "bars_evaluated_after_entry": bars_evaluated_after_entry,
        "reason_code": reason_code,
        "broker_write_count": BROKER_WRITE_COUNT,
    }
    digest = _result_digest(fields)
    result_fields = dict(fields)
    result_fields.update(
        trading_date=decision.trading_date,
        signal_timestamp=decision.signal_timestamp,
        slippage_assumption_bps=SLIPPAGE_ASSUMPTION_BPS,
        entry_timestamp=entry_timestamp,
        entry_price=entry_price,
        initial_stop_price=initial_stop_price,
        final_stop_price=final_stop_price,
        target_price=target_price,
        exit_timestamp=exit_timestamp,
        exit_price=exit_price,
        realized_pnl=realized_pnl,
        realized_pnl_percent=realized_pnl_percent,
        maximum_favorable_excursion=mfe,
        maximum_adverse_excursion=mae,
    )
    result_fields["state"] = state
    return ShadowExecutionResult(simulation_id=digest, simulation_digest=digest, **result_fields)


def _validated_bars(
    decision: ShadowTradingDecision, bars: Sequence[MarketBar], *,
    session_bounds_provider: Callable[[date], tuple[time, time] | None] = regular_session_bounds,
) -> tuple[list[MarketBar], str, str | None]:
    ordered = list(bars)
    if not ordered:
        return [], "", None
    now = max(bar.event_timestamp for bar in ordered)
    overrides = validate_market_data_batch(ordered)
    for index, bar in enumerate(ordered):
        if bar.quality is not MarketDataQuality.VALID:
            return [], "", bar.quality.value
        if bar.exchange_session != "REGULAR":
            return [], "", MarketDataQuality.OUTSIDE_SESSION.value
        quality = overrides.get(index)
        if quality is None:
            quality = validate_market_bar(
                bar,
                expected_symbol=decision.symbol,
                expected_trading_date=decision.trading_date,
                expected_interval_seconds=60,
                freshness_policy=HISTORICAL_POLICY,
                now=now,
                session_bounds_provider=session_bounds_provider,
            )
        if quality is not MarketDataQuality.VALID:
            return [], "", quality.value
    eligible = [bar for bar in ordered if bar.event_timestamp >= decision.signal_timestamp]
    if any(later.event_timestamp < earlier.event_timestamp for earlier, later in zip(eligible, eligible[1:])):
        return [], "", MarketDataQuality.OUT_OF_ORDER.value
    digests = [compute_market_data_digest(bar, normalization_version=NORMALIZATION_VERSION) for bar in eligible]
    return eligible, compute_market_data_batch_digest(digests) if digests else "", None


def simulate_shadow_execution(
    decision: ShadowTradingDecision, subsequent_bars: Sequence[MarketBar], *,
    session_bounds_provider: Callable[[date], tuple[time, time] | None] = regular_session_bounds,
) -> ShadowExecutionResult:
    """Evaluate one immutable long candidate with conservative OHLC rules.

    A limit entry fills only on a later bar whose low reaches the candidate
    price.  Same-bar entry/stop or entry/target is ambiguous, as is a post-
    entry bar touching both stop and target; no favorable ordering is guessed.

    `session_bounds_provider` defaults to the production `regular_session_
    bounds` -- every existing caller omits it and is therefore unaffected.
    It exists only so research code replaying candidates from historical
    periods outside `src.exchange_calendar`'s own coverage can supply an
    alternate, explicitly research-only provider, forwarded unchanged to
    `_validated_bars`' own identically-defaulted parameter."""
    eligible, reason = _authentic_candidate(decision)
    if not eligible:
        return _build_result(decision, input_market_data_digest="", state=ShadowExecutionState.NOT_ELIGIBLE, reason_code=reason)
    bars, input_digest, quality_reason = _validated_bars(decision, subsequent_bars, session_bounds_provider=session_bounds_provider)
    if quality_reason is not None:
        return _build_result(decision, input_market_data_digest="", state=ShadowExecutionState.DATA_INVALID, reason_code=quality_reason)
    if not bars:
        return _build_result(decision, input_market_data_digest="", state=ShadowExecutionState.PENDING_NOT_FILLED, reason_code="NO_POST_SIGNAL_BARS")

    entry = decision.proposed_entry
    stop = decision.proposed_stop
    assert entry is not None and stop is not None
    engine = ExitEngine(load_config())
    target: Decimal | None = None
    for index, bar in enumerate(bars):
        if bar.low > entry:
            continue
        provisional = engine.evaluate(PositionState(
            symbol=decision.symbol, entry_price=entry, initial_stop_price=stop,
            current_stop_price=stop, current_price=bar.close,
            highest_price_seen=max(entry, bar.high), timestamp=bar.event_timestamp,
        ))
        target = decision.proposed_target or provisional.target_price
        if bar.low <= stop or (target is not None and bar.high >= target):
            return _build_result(decision, input_market_data_digest=input_digest, state=ShadowExecutionState.AMBIGUOUS, initial_stop_price=stop, final_stop_price=stop, target_price=target, reason_code="AMBIGUOUS_ENTRY_BAR")
        entry_price = entry  # limit price; zero-bps policy records no better assumed fill.
        highest = entry_price
        mfe = Decimal("0")
        mae = Decimal("0")
        current_stop = stop
        for bars_after, later in enumerate(bars[index + 1:], start=1):
            highest = max(highest, later.high)
            mfe = max(mfe, later.high - entry_price)
            mae = min(mae, later.low - entry_price)
            provisional = engine.evaluate(PositionState(
                symbol=decision.symbol, entry_price=entry_price, initial_stop_price=stop,
                current_stop_price=current_stop, current_price=later.close,
                highest_price_seen=highest, timestamp=later.event_timestamp,
            ))
            target = decision.proposed_target or provisional.target_price
            if later.low <= current_stop and target is not None and later.high >= target:
                return _build_result(decision, input_market_data_digest=input_digest, state=ShadowExecutionState.AMBIGUOUS, entry_timestamp=bar.event_timestamp, entry_price=entry_price, initial_stop_price=stop, final_stop_price=current_stop, target_price=target, mfe=mfe, mae=mae, bars_evaluated_after_entry=bars_after, reason_code="AMBIGUOUS_STOP_TARGET_BAR")
            if later.low <= current_stop:
                pnl = current_stop - entry_price
                return _build_result(decision, input_market_data_digest=input_digest, state=ShadowExecutionState.STOPPED, entry_timestamp=bar.event_timestamp, entry_price=entry_price, initial_stop_price=stop, final_stop_price=current_stop, target_price=target, exit_timestamp=later.event_timestamp, exit_price=current_stop, exit_reason="protective_stop", realized_pnl=pnl, realized_pnl_percent=pnl / entry_price * Decimal("100"), mfe=mfe, mae=mae, bars_evaluated_after_entry=bars_after, reason_code="STOP_TOUCHED")
            if target is not None and later.high >= target:
                pnl = target - entry_price
                return _build_result(decision, input_market_data_digest=input_digest, state=ShadowExecutionState.TARGET_HIT, entry_timestamp=bar.event_timestamp, entry_price=entry_price, initial_stop_price=stop, final_stop_price=current_stop, target_price=target, exit_timestamp=later.event_timestamp, exit_price=target, exit_reason="profit_target", realized_pnl=pnl, realized_pnl_percent=pnl / entry_price * Decimal("100"), mfe=mfe, mae=mae, bars_evaluated_after_entry=bars_after, reason_code="TARGET_TOUCHED")
            if provisional.action is ExitAction.EXIT_TIME:
                pnl = later.close - entry_price
                return _build_result(decision, input_market_data_digest=input_digest, state=ShadowExecutionState.TIME_EXIT, entry_timestamp=bar.event_timestamp, entry_price=entry_price, initial_stop_price=stop, final_stop_price=current_stop, target_price=target, exit_timestamp=later.event_timestamp, exit_price=later.close, exit_reason="time_exit", realized_pnl=pnl, realized_pnl_percent=pnl / entry_price * Decimal("100"), mfe=mfe, mae=mae, bars_evaluated_after_entry=bars_after, reason_code="CONFIGURED_TIME_EXIT")
            if provisional.action is ExitAction.MOVE_STOP_TO_BREAKEVEN:
                current_stop = provisional.new_stop_price or current_stop
        return _build_result(decision, input_market_data_digest=input_digest, state=ShadowExecutionState.FILLED_OPEN, entry_timestamp=bar.event_timestamp, entry_price=entry_price, initial_stop_price=stop, final_stop_price=current_stop, target_price=target, mfe=mfe, mae=mae, bars_evaluated_after_entry=len(bars) - index - 1, reason_code="DATA_ENDED_WITH_POSITION_OPEN")
    return _build_result(decision, input_market_data_digest=input_digest, state=ShadowExecutionState.PENDING_NOT_FILLED, initial_stop_price=stop, final_stop_price=stop, reason_code="ENTRY_LIMIT_NOT_TOUCHED")


__all__ = [
    "BROKER_WRITE_COUNT", "EXIT_POLICY_VERSION", "FILL_POLICY_VERSION", "SLIPPAGE_ASSUMPTION_BPS",
    "SLIPPAGE_POLICY_VERSION", "ShadowExecutionResult", "ShadowExecutionState",
    "compute_shadow_execution_result_digest",
    "simulate_shadow_execution",
]