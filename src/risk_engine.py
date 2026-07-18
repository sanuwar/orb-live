from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

try:
    from .config import load_config
except ImportError:
    from config import load_config


@dataclass(frozen=True)
class TradeRequest:
    symbol: str
    entry_price: Decimal
    stop_price: Decimal


@dataclass(frozen=True)
class AccountSnapshot:
    settled_cash: Decimal
    daily_pnl: Decimal
    weekly_pnl: Decimal
    strategy_drawdown: Decimal
    open_positions: int
    entries_today: int


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    shares: int = 0
    planned_risk: Decimal = Decimal("0")
    position_value: Decimal = Decimal("0")


class RiskEngine:
    """Approves or rejects trade requests. It never places orders."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def evaluate(
        self,
        request: TradeRequest,
        account: AccountSnapshot,
    ) -> RiskDecision:
        strategy = self.config["strategy"]
        risk = self.config["risk"]

        if strategy.get("enabled") is not True:
            return RiskDecision(False, "Strategy is disabled in configuration.")

        if account.open_positions >= int(risk["maximum_open_positions"]):
            return RiskDecision(False, "Maximum open positions reached.")

        if account.entries_today >= int(risk["maximum_entries_per_day"]):
            return RiskDecision(False, "Maximum daily entries reached.")

        daily_limit = Decimal(str(risk["daily_loss_limit_usd"]))
        weekly_limit = Decimal(str(risk["weekly_loss_limit_usd"]))
        drawdown_limit = Decimal(str(risk["total_drawdown_halt_usd"]))

        if account.daily_pnl <= -daily_limit:
            return RiskDecision(False, "Daily loss limit reached.")

        if account.weekly_pnl <= -weekly_limit:
            return RiskDecision(False, "Weekly loss limit reached.")

        if account.strategy_drawdown >= drawdown_limit:
            return RiskDecision(False, "Strategy drawdown halt reached.")

        if request.entry_price <= 0 or request.stop_price <= 0:
            return RiskDecision(False, "Entry and stop prices must be positive.")

        if request.stop_price >= request.entry_price:
            return RiskDecision(
                False,
                "For a long trade, the stop must be below the entry price.",
            )

        risk_per_share = request.entry_price - request.stop_price
        allowed_trade_risk = Decimal(str(risk["risk_per_trade_usd"]))
        maximum_position = Decimal(str(risk["maximum_position_usd"]))

        shares_by_risk = int(
            (allowed_trade_risk / risk_per_share).to_integral_value(
                rounding=ROUND_DOWN
            )
        )

        shares_by_position_cap = int(
            (maximum_position / request.entry_price).to_integral_value(
                rounding=ROUND_DOWN
            )
        )

        shares_by_cash = int(
            (account.settled_cash / request.entry_price).to_integral_value(
                rounding=ROUND_DOWN
            )
        )

        shares = min(
            shares_by_risk,
            shares_by_position_cap,
            shares_by_cash,
        )

        if shares < 1:
            return RiskDecision(
                False,
                "No whole-share position fits the risk, cash, and position limits.",
            )

        planned_risk = risk_per_share * shares
        position_value = request.entry_price * shares

        return RiskDecision(
            approved=True,
            reason="Trade approved by risk engine.",
            shares=shares,
            planned_risk=planned_risk,
            position_value=position_value,
        )


if __name__ == "__main__":
    config = load_config()
    engine = RiskEngine(config)

    sample_request = TradeRequest(
        symbol="AAPL",
        entry_price=Decimal("200.00"),
        stop_price=Decimal("199.00"),
    )

    sample_account = AccountSnapshot(
        settled_cash=Decimal("3000.00"),
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        strategy_drawdown=Decimal("0"),
        open_positions=0,
        entries_today=0,
    )

    decision = engine.evaluate(sample_request, sample_account)

    print(f"Approved: {decision.approved}")
    print(f"Reason: {decision.reason}")
    print(f"Shares: {decision.shares}")
    print(f"Planned risk: ${decision.planned_risk}")
    print(f"Position value: ${decision.position_value}")