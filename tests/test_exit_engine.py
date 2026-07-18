from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from src.config import load_config
from src.exit_engine import (
    ET,
    ExitAction,
    ExitEngine,
    PositionState,
)


def enabled_config():
    config = deepcopy(load_config())
    config["strategy"]["enabled"] = True
    return config


def test_move_stop_to_break_even_at_one_r():
    engine = ExitEngine(enabled_config())

    position = PositionState(
        symbol="AAPL",
        entry_price=Decimal("102"),
        initial_stop_price=Decimal("101"),
        current_stop_price=Decimal("101"),
        current_price=Decimal("103.10"),
        highest_price_seen=Decimal("103.10"),
        timestamp=datetime(2026, 7, 20, 11, 0, tzinfo=ET),
    )

    decision = engine.evaluate(position)

    assert decision.action == ExitAction.MOVE_STOP_TO_BREAKEVEN
    assert decision.new_stop_price == Decimal("102")
    assert decision.target_price == Decimal("104.0")


def test_exit_at_two_r_profit_target():
    engine = ExitEngine(enabled_config())

    position = PositionState(
        symbol="AAPL",
        entry_price=Decimal("102"),
        initial_stop_price=Decimal("101"),
        current_stop_price=Decimal("102"),
        current_price=Decimal("104"),
        highest_price_seen=Decimal("104"),
        timestamp=datetime(2026, 7, 20, 12, 0, tzinfo=ET),
    )

    decision = engine.evaluate(position)

    assert decision.action == ExitAction.EXIT_PROFIT_TARGET
    assert decision.target_price == Decimal("104.0")


def test_exit_when_stop_is_reached():
    engine = ExitEngine(enabled_config())

    position = PositionState(
        symbol="AAPL",
        entry_price=Decimal("102"),
        initial_stop_price=Decimal("101"),
        current_stop_price=Decimal("101"),
        current_price=Decimal("101"),
        highest_price_seen=Decimal("102.50"),
        timestamp=datetime(2026, 7, 20, 10, 30, tzinfo=ET),
    )

    decision = engine.evaluate(position)

    assert decision.action == ExitAction.EXIT_STOP


def test_exit_at_end_of_day():
    engine = ExitEngine(enabled_config())

    position = PositionState(
        symbol="AAPL",
        entry_price=Decimal("102"),
        initial_stop_price=Decimal("101"),
        current_stop_price=Decimal("102"),
        current_price=Decimal("103"),
        highest_price_seen=Decimal("103.50"),
        timestamp=datetime(2026, 7, 20, 15, 45, tzinfo=ET),
    )

    decision = engine.evaluate(position)

    assert decision.action == ExitAction.EXIT_TIME


def test_hold_when_no_exit_condition_is_met():
    engine = ExitEngine(enabled_config())

    position = PositionState(
        symbol="AAPL",
        entry_price=Decimal("102"),
        initial_stop_price=Decimal("101"),
        current_stop_price=Decimal("101"),
        current_price=Decimal("102.50"),
        highest_price_seen=Decimal("102.50"),
        timestamp=datetime(2026, 7, 20, 10, 30, tzinfo=ET),
    )

    decision = engine.evaluate(position)

    assert decision.action == ExitAction.HOLD
    assert decision.new_stop_price == Decimal("101")
    assert decision.target_price == Decimal("104.0")