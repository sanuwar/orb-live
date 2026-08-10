from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.shadow_execution_simulator import (
    FILL_POLICY_VERSION,
    SLIPPAGE_ASSUMPTION_BPS,
    SLIPPAGE_POLICY_VERSION,
    ShadowExecutionState,
    simulate_shadow_execution,
)
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
    compute_shadow_decision_digest,
)

DAY = date(2026, 8, 11)
SIGNAL = datetime(2026, 8, 11, 14, 40, tzinfo=timezone.utc)


def candidate(*, decision_type=ShadowDecisionType.LONG_CANDIDATE, entry=Decimal("100"), stop=Decimal("99"), target=None):
    proposed_entry = entry if decision_type is ShadowDecisionType.LONG_CANDIDATE else None
    proposed_stop = stop if decision_type is ShadowDecisionType.LONG_CANDIDATE else None
    digest = compute_shadow_decision_digest(
        shadow_run_id="fixture-run", symbol="AAPL", trading_date=DAY,
        strategy_version="orb_long_v0-0.1.0", implementation_digest="a" * 64,
        market_data_digest="b" * 64, opening_range_identity="c" * 64,
        signal_timestamp=SIGNAL, decision_type=decision_type,
        proposed_entry=proposed_entry, proposed_stop=proposed_stop,
        proposed_target=target, reason="fixture", market_data_quality=MarketDataQuality.VALID,
        exchange_session="REGULAR",
    )
    return ShadowTradingDecision(
        shadow_run_id="fixture-run", decision_id=digest, symbol="AAPL", trading_date=DAY,
        strategy_version="orb_long_v0-0.1.0", implementation_digest="a" * 64,
        market_data_digest="b" * 64, opening_range_identity="c" * 64,
        signal_timestamp=SIGNAL, decision_type=decision_type,
        proposed_entry=proposed_entry, proposed_stop=proposed_stop, proposed_target=target,
        reason="fixture", market_data_quality=MarketDataQuality.VALID,
        exchange_session="REGULAR", created_at=SIGNAL,
    )


def bar(minute, *, open="100", high="100.5", low="99.5", close="100"):
    timestamp = SIGNAL + timedelta(minutes=minute)
    return __import__("src.market_data_canonical", fromlist=["MarketBar"]).MarketBar(
        symbol="AAPL", exchange_session="REGULAR", trading_date=DAY,
        event_timestamp=timestamp, end_timestamp=timestamp + timedelta(minutes=1),
        receive_timestamp=timestamp + timedelta(seconds=1), open=Decimal(open),
        high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=100,
        source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None,
        quality=MarketDataQuality.VALID,
    )


def test_non_candidate_decisions_never_become_positions():
    for kind in (ShadowDecisionType.REJECTED, ShadowDecisionType.NO_SIGNAL, ShadowDecisionType.DATA_INVALID):
        result = simulate_shadow_execution(candidate(decision_type=kind), [bar(1)])
        assert result.state is ShadowExecutionState.NOT_ELIGIBLE
        assert result.entry_price is None


def test_limit_entry_never_touched_is_pending_and_pre_signal_does_not_fill():
    before = bar(-1, open="100", low="99.5", high="100.5", close="100")
    result = simulate_shadow_execution(candidate(), [before, bar(1, open="100.2", low="100.1", high="101", close="100.2")])
    assert result.state is ShadowExecutionState.PENDING_NOT_FILLED
    assert result.reason_code == "ENTRY_LIMIT_NOT_TOUCHED"


def test_entry_stop_target_and_time_exit_lifecycle():
    stopped = simulate_shadow_execution(candidate(), [bar(1), bar(2, low="98.9", high="100.2")])
    assert stopped.state is ShadowExecutionState.STOPPED
    assert stopped.exit_price == Decimal("99") and stopped.realized_pnl == Decimal("-1")
    targeted = simulate_shadow_execution(candidate(), [bar(1), bar(2, open="100.2", low="100.1", high="102.1", close="100.2")])
    assert targeted.state is ShadowExecutionState.TARGET_HIT
    assert targeted.exit_price == Decimal("102") and targeted.realized_pnl == Decimal("2")
    explicit_target = simulate_shadow_execution(candidate(target=Decimal("101.5")), [bar(1), bar(2, open="100.2", low="100.1", high="101.6", close="101.4")])
    assert explicit_target.state is ShadowExecutionState.TARGET_HIT
    assert explicit_target.target_price == Decimal("101.5")
    eod = datetime.combine(DAY, __import__("datetime").time(15, 45), tzinfo=EASTERN).astimezone(timezone.utc)
    time_bar = replace(bar(2, open="100.2", low="100.1", high="101", close="100.5"), event_timestamp=eod, end_timestamp=eod + timedelta(minutes=1))
    timed = simulate_shadow_execution(candidate(), [bar(1), time_bar])
    assert timed.state is ShadowExecutionState.TIME_EXIT
    assert timed.exit_price == Decimal("100.5")


def test_ambiguous_bars_never_choose_a_favorable_ordering():
    entry_stop = simulate_shadow_execution(candidate(), [bar(1, low="98.9", high="100.5")])
    assert entry_stop.state is ShadowExecutionState.AMBIGUOUS
    assert entry_stop.reason_code == "AMBIGUOUS_ENTRY_BAR"
    stop_target = simulate_shadow_execution(candidate(), [bar(1), bar(2, low="98.9", high="102.1")])
    assert stop_target.state is ShadowExecutionState.AMBIGUOUS
    assert stop_target.reason_code == "AMBIGUOUS_STOP_TARGET_BAR"


@pytest.mark.parametrize("mutator", [
    lambda item: replace(item, symbol="MSFT"),
    lambda item: replace(item, trading_date=date(2026, 8, 10)),
    lambda item: replace(item, event_timestamp=item.event_timestamp.replace(hour=12)),
    lambda item: replace(item, high=Decimal("98")),
    lambda item: replace(item, quality=MarketDataQuality.STALE),
    lambda item: replace(item, exchange_session="PREMARKET"),
])
def test_invalid_market_data_fails_closed(mutator):
    result = simulate_shadow_execution(candidate(), [mutator(bar(1))])
    assert result.state is ShadowExecutionState.DATA_INVALID


def test_weekend_holiday_and_conflicting_duplicate_fail_closed():
    weekend = replace(bar(1), trading_date=date(2026, 8, 8), event_timestamp=datetime(2026, 8, 8, 14, 41, tzinfo=timezone.utc))
    assert simulate_shadow_execution(candidate(), [weekend]).state is ShadowExecutionState.DATA_INVALID
    holiday = replace(bar(1), trading_date=date(2026, 7, 3), event_timestamp=datetime(2026, 7, 3, 14, 41, tzinfo=timezone.utc))
    assert simulate_shadow_execution(candidate(), [holiday]).state is ShadowExecutionState.DATA_INVALID
    first = bar(1)
    conflicting = replace(first, close=Decimal("100.2"))
    assert simulate_shadow_execution(candidate(), [first, conflicting]).state is ShadowExecutionState.DATA_INVALID


def test_result_digest_pnl_excursions_and_slippage_are_deterministic():
    bars = [bar(1), bar(2, open="100.3", low="100.2", high="101.5", close="101")]
    first = simulate_shadow_execution(candidate(), bars)
    second = simulate_shadow_execution(candidate(), bars)
    assert first == second and first.simulation_id == first.simulation_digest
    assert first.state is ShadowExecutionState.FILLED_OPEN
    assert first.maximum_favorable_excursion == Decimal("1.5")
    assert first.maximum_adverse_excursion == Decimal("0")
    assert first.fill_policy_version == FILL_POLICY_VERSION
    assert first.slippage_policy_version == SLIPPAGE_POLICY_VERSION
    assert first.slippage_assumption_bps == SLIPPAGE_ASSUMPTION_BPS
    assert first.broker_write_count == 0


def test_tampered_candidate_identity_is_not_eligible():
    result = simulate_shadow_execution(replace(candidate(), reason="altered"), [bar(1)])
    assert result.state is ShadowExecutionState.NOT_ELIGIBLE
    assert result.reason_code == "DECISION_ID_MISMATCH"


def test_simulator_has_no_broker_or_authority_reachability():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "src/shadow_execution_simulator.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    forbidden = {"RobinhoodClient", "RobinhoodCommissioningAdapter", "RobinhoodCommissioningSessionBinding", "OneShareCommissioningOrchestrator", "ExecutionSafetyDecision", "call_tool", "place_equity_order", "cancel_equity_order"}
    assert not (names & forbidden)
    config = load_config()
    assert config["strategy"]["enabled"] is False