from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from src.config import load_config
from src.signal_engine import (
    ET,
    LiveData,
    OpeningData,
    SignalEngine,
)


def enabled_config():
    config = deepcopy(load_config())
    config["strategy"]["enabled"] = True
    return config


def valid_opening():
    return OpeningData(
        symbol="AAPL",
        previous_close=Decimal("200"),
        today_open=Decimal("203"),
        opening_range_high=Decimal("204"),
        opening_range_low=Decimal("202"),
        opening_range_volume=3_000_000,
        median_opening_volume=Decimal("1_000_000"),
    )


def valid_live():
    return LiveData(
        timestamp=datetime(2026, 7, 20, 10, 0, tzinfo=ET),
        completed_bar_close=Decimal("204.10"),
        spy_price=Decimal("690"),
        spy_vwap=Decimal("689"),
    )


def test_disabled_strategy_rejects_signal():
    engine = SignalEngine(load_config())

    decision = engine.evaluate(valid_opening(), valid_live())

    assert decision.approved is False
    assert "disabled" in decision.reason.lower()


def test_valid_orb_signal_is_approved():
    engine = SignalEngine(enabled_config())

    decision = engine.evaluate(valid_opening(), valid_live())

    assert decision.approved is True
    assert decision.entry_price == Decimal("204.10")
    assert decision.stop_price == Decimal("202")
    assert decision.relative_volume == Decimal("3")
    assert decision.gap_percent == Decimal("1.5")


def test_low_gap_is_rejected():
    engine = SignalEngine(enabled_config())

    opening = OpeningData(
        symbol="AAPL",
        previous_close=Decimal("200"),
        today_open=Decimal("201"),
        opening_range_high=Decimal("202"),
        opening_range_low=Decimal("200.50"),
        opening_range_volume=3_000_000,
        median_opening_volume=Decimal("1_000_000"),
    )

    decision = engine.evaluate(opening, valid_live())

    assert decision.approved is False
    assert "gap" in decision.reason.lower()


def test_low_relative_volume_is_rejected():
    engine = SignalEngine(enabled_config())

    opening = OpeningData(
        symbol="AAPL",
        previous_close=Decimal("200"),
        today_open=Decimal("203"),
        opening_range_high=Decimal("204"),
        opening_range_low=Decimal("202"),
        opening_range_volume=2_000_000,
        median_opening_volume=Decimal("1_000_000"),
    )

    decision = engine.evaluate(opening, valid_live())

    assert decision.approved is False
    assert "relative volume" in decision.reason.lower()


def test_spy_below_vwap_is_rejected():
    engine = SignalEngine(enabled_config())

    live = LiveData(
        timestamp=datetime(2026, 7, 20, 10, 0, tzinfo=ET),
        completed_bar_close=Decimal("204.10"),
        spy_price=Decimal("688"),
        spy_vwap=Decimal("689"),
    )

    decision = engine.evaluate(valid_opening(), live)

    assert decision.approved is False
    assert "spy" in decision.reason.lower()


def test_price_must_break_opening_range_high():
    engine = SignalEngine(enabled_config())

    live = LiveData(
        timestamp=datetime(2026, 7, 20, 10, 0, tzinfo=ET),
        completed_bar_close=Decimal("204"),
        spy_price=Decimal("690"),
        spy_vwap=Decimal("689"),
    )

    decision = engine.evaluate(valid_opening(), live)

    assert decision.approved is False
    assert "not broken" in decision.reason.lower()