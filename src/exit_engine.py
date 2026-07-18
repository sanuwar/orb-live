from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

try:
    from .config import load_config
except ImportError:
    from config import load_config


ET = ZoneInfo("America/New_York")


class ExitAction(str, Enum):
    HOLD = "hold"
    MOVE_STOP_TO_BREAKEVEN = "move_stop_to_breakeven"
    EXIT_STOP = "exit_stop"
    EXIT_PROFIT_TARGET = "exit_profit_target"
    EXIT_TIME = "exit_time"


@dataclass(frozen=True)
class PositionState:
    symbol: str
    entry_price: Decimal
    initial_stop_price: Decimal
    current_stop_price: Decimal
    current_price: Decimal
    highest_price_seen: Decimal
    timestamp: datetime
    position_open: bool = True


@dataclass(frozen=True)
class ExitDecision:
    action: ExitAction
    reason: str
    new_stop_price: Decimal | None = None
    target_price: Decimal | None = None


class ExitEngine:
    """
    Applies the frozen ORB V0 exit rules.

    Rules:
    1. Initial stop remains at the opening-range low.
    2. After price reaches +1R, move the stop to entry price.
    3. At +2R, exit the full position.
    4. Exit any remaining position at the configured time.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def evaluate(self, position: PositionState) -> ExitDecision:
        if not position.position_open:
            return ExitDecision(
                ExitAction.HOLD,
                "No open position.",
            )

        if position.timestamp.tzinfo is None:
            raise ValueError("Position timestamp must include a timezone.")

        if position.initial_stop_price >= position.entry_price:
            raise ValueError("Initial stop must be below entry for a long trade.")

        if position.current_stop_price > position.entry_price:
            raise ValueError("Current stop cannot be above entry in V0.")

        if position.current_price <= 0 or position.highest_price_seen <= 0:
            raise ValueError("Prices must be positive.")

        risk_per_share = (
            position.entry_price - position.initial_stop_price
        )

        break_even_trigger_r = Decimal(
            str(self.config["exit"]["move_stop_to_break_even_at_r"])
        )

        profit_target_r = Decimal(
            str(self.config["exit"]["full_profit_exit_at_r"])
        )

        break_even_trigger = (
            position.entry_price
            + risk_per_share * break_even_trigger_r
        )

        profit_target = (
            position.entry_price
            + risk_per_share * profit_target_r
        )

        timestamp_et = position.timestamp.astimezone(ET)
        time_exit = time.fromisoformat(
            self.config["exit"]["time_exit"]
        )

        # A triggered protective stop has first priority.
        if position.current_price <= position.current_stop_price:
            return ExitDecision(
                ExitAction.EXIT_STOP,
                "Current price reached the protective stop.",
                new_stop_price=position.current_stop_price,
                target_price=profit_target,
            )

        # Take the complete profit at +2R.
        if position.current_price >= profit_target:
            return ExitDecision(
                ExitAction.EXIT_PROFIT_TARGET,
                "Current price reached the +2R profit target.",
                new_stop_price=position.current_stop_price,
                target_price=profit_target,
            )

        # Exit before the market closes.
        if timestamp_et.time() >= time_exit:
            return ExitDecision(
                ExitAction.EXIT_TIME,
                "Configured end-of-day exit time reached.",
                new_stop_price=position.current_stop_price,
                target_price=profit_target,
            )

        # Once +1R has been observed, move protection to entry price.
        if (
            position.highest_price_seen >= break_even_trigger
            and position.current_stop_price < position.entry_price
        ):
            return ExitDecision(
                ExitAction.MOVE_STOP_TO_BREAKEVEN,
                "Price reached +1R; move stop to entry price.",
                new_stop_price=position.entry_price,
                target_price=profit_target,
            )

        return ExitDecision(
            ExitAction.HOLD,
            "No exit condition has been reached.",
            new_stop_price=position.current_stop_price,
            target_price=profit_target,
        )


if __name__ == "__main__":
    config = load_config()
    engine = ExitEngine(config)

    sample_position = PositionState(
        symbol="AAPL",
        entry_price=Decimal("102"),
        initial_stop_price=Decimal("101"),
        current_stop_price=Decimal("101"),
        current_price=Decimal("103.10"),
        highest_price_seen=Decimal("103.10"),
        timestamp=datetime.now(ET),
    )

    decision = engine.evaluate(sample_position)

    print(f"Action: {decision.action.value}")
    print(f"Reason: {decision.reason}")
    print(f"New stop: {decision.new_stop_price}")
    print(f"Profit target: {decision.target_price}")