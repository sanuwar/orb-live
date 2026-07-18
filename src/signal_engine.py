from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

try:
    from .config import load_config
except ImportError:
    from config import load_config


ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class OpeningData:
    symbol: str
    previous_close: Decimal
    today_open: Decimal
    opening_range_high: Decimal
    opening_range_low: Decimal
    opening_range_volume: int
    median_opening_volume: Decimal


@dataclass(frozen=True)
class LiveData:
    timestamp: datetime
    completed_bar_close: Decimal
    spy_price: Decimal
    spy_vwap: Decimal
    already_entered_today: bool = False


@dataclass(frozen=True)
class SignalDecision:
    approved: bool
    reason: str
    gap_percent: Decimal = Decimal("0")
    relative_volume: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    stop_price: Decimal = Decimal("0")


class SignalEngine:
    """Evaluates the frozen ORB V0 entry rules. It never places orders."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def evaluate(
        self,
        opening: OpeningData,
        live: LiveData,
    ) -> SignalDecision:
        strategy = self.config["strategy"]
        signal = self.config["signal"]
        universe = self.config["universe"]

        if strategy.get("enabled") is not True:
            return SignalDecision(False, "Strategy is disabled.")

        symbol = opening.symbol.upper().strip()

        if symbol not in universe["symbols"]:
            return SignalDecision(False, "Symbol is not in the approved universe.")

        if live.already_entered_today:
            return SignalDecision(False, "This symbol was already traded today.")

        if live.timestamp.tzinfo is None:
            return SignalDecision(False, "Timestamp must include a timezone.")

        timestamp_et = live.timestamp.astimezone(ET)

        if timestamp_et.weekday() >= 5:
            return SignalDecision(False, "Market is closed on weekends.")

        entry_start = time.fromisoformat(signal["entry_start"])
        entry_end = time.fromisoformat(signal["entry_end"])

        if not entry_start <= timestamp_et.time() <= entry_end:
            return SignalDecision(False, "Outside the permitted entry window.")

        price_values = [
            opening.previous_close,
            opening.today_open,
            opening.opening_range_high,
            opening.opening_range_low,
            opening.median_opening_volume,
            live.completed_bar_close,
            live.spy_price,
            live.spy_vwap,
        ]

        if any(value <= 0 for value in price_values):
            return SignalDecision(False, "Prices and reference values must be positive.")

        if opening.opening_range_volume <= 0:
            return SignalDecision(False, "Opening-range volume must be positive.")

        if opening.opening_range_low >= opening.opening_range_high:
            return SignalDecision(False, "Opening-range low must be below its high.")

        gap_percent = (
            (opening.today_open - opening.previous_close)
            / opening.previous_close
            * Decimal("100")
        )

        minimum_gap = Decimal(str(signal["minimum_gap_percent"]))

        if gap_percent < minimum_gap:
            return SignalDecision(
                False,
                "Opening gap is below the required threshold.",
                gap_percent=gap_percent,
            )

        relative_volume = (
            Decimal(opening.opening_range_volume)
            / opening.median_opening_volume
        )

        minimum_rvol = Decimal(str(signal["minimum_relative_volume"]))

        if relative_volume < minimum_rvol:
            return SignalDecision(
                False,
                "Opening relative volume is below the required threshold.",
                gap_percent=gap_percent,
                relative_volume=relative_volume,
            )

        if signal.get("require_spy_above_vwap") is True:
            if live.spy_price <= live.spy_vwap:
                return SignalDecision(
                    False,
                    "SPY is not above its session VWAP.",
                    gap_percent=gap_percent,
                    relative_volume=relative_volume,
                )

        if live.completed_bar_close <= opening.opening_range_high:
            return SignalDecision(
                False,
                "Completed bar has not broken above the opening-range high.",
                gap_percent=gap_percent,
                relative_volume=relative_volume,
            )

        return SignalDecision(
            approved=True,
            reason="ORB V0 long-entry signal approved.",
            gap_percent=gap_percent,
            relative_volume=relative_volume,
            entry_price=live.completed_bar_close,
            stop_price=opening.opening_range_low,
        )


if __name__ == "__main__":
    config = load_config()
    engine = SignalEngine(config)

    sample_opening = OpeningData(
        symbol="AAPL",
        previous_close=Decimal("200"),
        today_open=Decimal("203"),
        opening_range_high=Decimal("204"),
        opening_range_low=Decimal("202"),
        opening_range_volume=3000000,
        median_opening_volume=Decimal("1000000"),
    )

    sample_live = LiveData(
        timestamp=datetime.now(ET),
        completed_bar_close=Decimal("204.10"),
        spy_price=Decimal("690"),
        spy_vwap=Decimal("689"),
    )

    decision = engine.evaluate(sample_opening, sample_live)

    print(f"Approved: {decision.approved}")
    print(f"Reason: {decision.reason}")
    print(f"Gap: {decision.gap_percent:.2f}%")
    print(f"Relative volume: {decision.relative_volume:.2f}x")
    print(f"Entry: ${decision.entry_price}")
    print(f"Stop: ${decision.stop_price}")