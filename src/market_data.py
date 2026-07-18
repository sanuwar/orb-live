from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Iterable
from zoneinfo import ZoneInfo

try:
    from .robinhood_client import (
        RobinhoodClient,
        RobinhoodClientError,
        _unwrap_tool_result,
    )
except ImportError:
    from robinhood_client import (
        RobinhoodClient,
        RobinhoodClientError,
        _unwrap_tool_result,
    )


ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MinuteBar:
    symbol: str
    begins_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int
    session: str
    interpolated: bool

    @property
    def timestamp_et(self) -> datetime:
        return self.begins_at.astimezone(ET)


@dataclass(frozen=True)
class OpeningRange:
    symbol: str
    session_date: date
    high_price: Decimal
    low_price: Decimal
    volume: int
    bar_count: int


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


class MarketData:
    """Read-only minute-bar access and ORB calculations."""

    def __init__(
        self,
        client: RobinhoodClient | None = None,
    ):
        self.client = client or RobinhoodClient()

    async def get_minute_bars(
        self,
        *,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[MinuteBar]:
        normalized_symbol = symbol.upper().strip()

        if not normalized_symbol:
            raise ValueError("A stock symbol is required.")

        if start_time.tzinfo is None or end_time.tzinfo is None:
            raise ValueError(
                "Start and end times must include timezones."
            )

        if start_time >= end_time:
            raise ValueError(
                "Start time must be earlier than end time."
            )

        arguments = {
            "symbols": [normalized_symbol],
            "start_time": start_time.astimezone(
                timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "end_time": end_time.astimezone(
                timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "interval": "minute",
            "bounds": "regular",
            "adjustment_type": "split",
        }

        async with self.client.session() as session:
            result = await session.call_tool(
                "get_equity_historicals",
                arguments,
            )

        data = _unwrap_tool_result(
            result,
            "get_equity_historicals",
        )

        results = data.get("results") or []

        matching_result = next(
            (
                item
                for item in results
                if item
                and item.get("symbol")
                == normalized_symbol
            ),
            None,
        )

        if not matching_result:
            raise RobinhoodClientError(
                f"No historical bars were returned for "
                f"{normalized_symbol}."
            )

        raw_bars = matching_result.get("bars") or []
        parsed_bars: list[MinuteBar] = []

        for raw_bar in raw_bars:
            if not raw_bar:
                continue

            parsed_bars.append(
                MinuteBar(
                    symbol=normalized_symbol,
                    begins_at=parse_timestamp(
                        raw_bar["begins_at"]
                    ),
                    open_price=Decimal(
                        raw_bar["open_price"]
                    ),
                    high_price=Decimal(
                        raw_bar["high_price"]
                    ),
                    low_price=Decimal(
                        raw_bar["low_price"]
                    ),
                    close_price=Decimal(
                        raw_bar["close_price"]
                    ),
                    volume=int(raw_bar["volume"]),
                    session=raw_bar.get("session", ""),
                    interpolated=bool(
                        raw_bar.get("interpolated", False)
                    ),
                )
            )

        return sorted(
            parsed_bars,
            key=lambda bar: bar.begins_at,
        )


def calculate_opening_range(
    bars: Iterable[MinuteBar],
    *,
    session_date: date,
    opening_range_minutes: int = 5,
) -> OpeningRange:
    if opening_range_minutes <= 0:
        raise ValueError(
            "Opening-range minutes must be positive."
        )

    opening_start = time(9, 30)
    opening_end_datetime = (
        datetime.combine(
            session_date,
            opening_start,
            tzinfo=ET,
        )
        + timedelta(minutes=opening_range_minutes)
    )

    selected = [
        bar
        for bar in bars
        if (
            bar.timestamp_et.date() == session_date
            and bar.timestamp_et.time() >= opening_start
            and bar.timestamp_et < opening_end_datetime
            and not bar.interpolated
        )
    ]

    if len(selected) < opening_range_minutes:
        raise ValueError(
            f"Expected at least {opening_range_minutes} "
            f"real opening bars, found {len(selected)}."
        )

    symbols = {bar.symbol for bar in selected}

    if len(symbols) != 1:
        raise ValueError(
            "Opening-range bars must belong to one symbol."
        )

    return OpeningRange(
        symbol=selected[0].symbol,
        session_date=session_date,
        high_price=max(
            bar.high_price for bar in selected
        ),
        low_price=min(
            bar.low_price for bar in selected
        ),
        volume=sum(
            bar.volume for bar in selected
        ),
        bar_count=len(selected),
    )


def calculate_estimated_vwap(
    bars: Iterable[MinuteBar],
) -> Decimal:
    """
    Estimate session VWAP from minute OHLCV bars.

    Typical price = (high + low + close) / 3.
    """

    valid_bars = [
        bar
        for bar in bars
        if not bar.interpolated
        and bar.volume > 0
    ]

    if not valid_bars:
        raise ValueError(
            "No real volume bars are available for VWAP."
        )

    total_price_volume = Decimal("0")
    total_volume = 0

    for bar in valid_bars:
        typical_price = (
            bar.high_price
            + bar.low_price
            + bar.close_price
        ) / Decimal("3")

        total_price_volume += (
            typical_price * bar.volume
        )
        total_volume += bar.volume

    return total_price_volume / Decimal(
        total_volume
    )


def calculate_median_opening_volume(
    bars: Iterable[MinuteBar],
    *,
    before_date: date,
    lookback_days: int = 20,
    opening_range_minutes: int = 5,
) -> Decimal:
    if lookback_days <= 0:
        raise ValueError(
            "Lookback days must be positive."
        )

    bars_list = list(bars)

    available_dates = sorted(
        {
            bar.timestamp_et.date()
            for bar in bars_list
            if (
                bar.timestamp_et.date() < before_date
                and not bar.interpolated
            )
        },
        reverse=True,
    )

    opening_volumes: list[int] = []

    for session_date in available_dates:
        try:
            opening_range = calculate_opening_range(
                bars_list,
                session_date=session_date,
                opening_range_minutes=opening_range_minutes,
            )
        except ValueError:
            continue

        opening_volumes.append(
            opening_range.volume
        )

        if len(opening_volumes) >= lookback_days:
            break

    if len(opening_volumes) < lookback_days:
        raise ValueError(
            f"Only {len(opening_volumes)} complete sessions "
            f"were available; {lookback_days} are required."
        )

    return Decimal(
        str(median(opening_volumes))
    )


async def main() -> None:
    market_data = MarketData()

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=5)

    bars = await market_data.get_minute_bars(
        symbol="AAPL",
        start_time=start_time,
        end_time=end_time,
    )

    print("\nRead-only minute-bar request succeeded.")
    print(f"Bars returned: {len(bars)}")

    if bars:
        latest_bar = bars[-1]
        latest_date = latest_bar.timestamp_et.date()

        print(
            f"Latest bar: "
            f"{latest_bar.timestamp_et.isoformat()}"
        )
        print(
            f"Latest close: ${latest_bar.close_price}"
        )

        try:
            opening_range = calculate_opening_range(
                bars,
                session_date=latest_date,
            )

            print(
                f"Opening-range high: "
                f"${opening_range.high_price}"
            )
            print(
                f"Opening-range low: "
                f"${opening_range.low_price}"
            )
            print(
                f"Opening-range volume: "
                f"{opening_range.volume}"
            )
        except ValueError as error:
            print(
                f"Opening range unavailable: {error}"
            )

    print("\nNo order tool was called.")


if __name__ == "__main__":
    asyncio.run(main())