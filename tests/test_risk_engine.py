from copy import deepcopy
from decimal import Decimal

from src.config import load_config
from src.risk_engine import (
    AccountSnapshot,
    RiskEngine,
    TradeRequest,
)


def enabled_config():
    config = deepcopy(load_config())
    config["strategy"]["enabled"] = True
    return config


def normal_account():
    return AccountSnapshot(
        settled_cash=Decimal("3000"),
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        strategy_drawdown=Decimal("0"),
        open_positions=0,
        entries_today=0,
    )


def test_disabled_strategy_rejects_trade():
    engine = RiskEngine(load_config())

    decision = engine.evaluate(
        TradeRequest(
            symbol="AAPL",
            entry_price=Decimal("200"),
            stop_price=Decimal("199"),
        ),
        normal_account(),
    )

    assert decision.approved is False
    assert "disabled" in decision.reason.lower()


def test_valid_trade_is_approved():
    engine = RiskEngine(enabled_config())

    decision = engine.evaluate(
        TradeRequest(
            symbol="AAPL",
            entry_price=Decimal("200"),
            stop_price=Decimal("199"),
        ),
        normal_account(),
    )

    assert decision.approved is True
    assert decision.shares == 15
    assert decision.planned_risk == Decimal("15")
    assert decision.position_value == Decimal("3000")


def test_stop_above_entry_is_rejected():
    engine = RiskEngine(enabled_config())

    decision = engine.evaluate(
        TradeRequest(
            symbol="AAPL",
            entry_price=Decimal("200"),
            stop_price=Decimal("201"),
        ),
        normal_account(),
    )

    assert decision.approved is False
    assert "stop must be below" in decision.reason.lower()


def test_daily_loss_limit_blocks_trade():
    engine = RiskEngine(enabled_config())

    account = AccountSnapshot(
        settled_cash=Decimal("3000"),
        daily_pnl=Decimal("-45"),
        weekly_pnl=Decimal("-45"),
        strategy_drawdown=Decimal("45"),
        open_positions=0,
        entries_today=0,
    )

    decision = engine.evaluate(
        TradeRequest(
            symbol="AAPL",
            entry_price=Decimal("200"),
            stop_price=Decimal("199"),
        ),
        account,
    )

    assert decision.approved is False
    assert "daily loss limit" in decision.reason.lower()


def test_cash_limit_reduces_share_count():
    engine = RiskEngine(enabled_config())

    account = AccountSnapshot(
        settled_cash=Decimal("800"),
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        strategy_drawdown=Decimal("0"),
        open_positions=0,
        entries_today=0,
    )

    decision = engine.evaluate(
        TradeRequest(
            symbol="AAPL",
            entry_price=Decimal("200"),
            stop_price=Decimal("199"),
        ),
        account,
    )

    assert decision.approved is True
    assert decision.shares == 4
    assert decision.position_value == Decimal("800")