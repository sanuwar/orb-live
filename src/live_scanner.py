from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

try:
    from .config import load_config
    from .journal import Journal
    from .market_data import (
        MinuteBar,
        calculate_estimated_vwap,
        calculate_opening_range,
        parse_timestamp,
    )
    from .robinhood_client import (
        RobinhoodClient,
        RobinhoodClientError,
        _unwrap_tool_result,
    )
    from .signal_engine import (
        LiveData,
        OpeningData,
        SignalDecision,
        SignalEngine,
    )
except ImportError:
    from config import load_config
    from journal import Journal
    from market_data import (
        MinuteBar,
        calculate_estimated_vwap,
        calculate_opening_range,
        parse_timestamp,
    )
    from robinhood_client import (
        RobinhoodClient,
        RobinhoodClientError,
        _unwrap_tool_result,
    )
    from signal_engine import (
        LiveData,
        OpeningData,
        SignalDecision,
        SignalEngine,
    )


ET = ZoneInfo("America/New_York")
SPY_SYMBOL = "SPY"
HISTORY_CALENDAR_DAYS = 45
SYMBOL_BATCH_SIZE = 5


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    approved: bool
    reason: str
    gap_percent: Decimal = Decimal("0")
    relative_volume: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    opening_range_high: Decimal = Decimal("0")
    opening_range_low: Decimal = Decimal("0")


def batches(
    values: list[str],
    batch_size: int,
) -> Iterable[list[str]]:
    if batch_size < 1:
        raise ValueError("Batch size must be positive.")

    for index in range(0, len(values), batch_size):
        yield values[index : index + batch_size]


def latest_completed_bar(
    bars: list[MinuteBar],
    *,
    now_et: datetime,
) -> MinuteBar:
    completed = [
        bar
        for bar in bars
        if (
            not bar.interpolated
            and bar.timestamp_et.date() == now_et.date()
            and bar.timestamp_et + timedelta(minutes=1) <= now_et
        )
    ]

    if not completed:
        raise ValueError(
            "No completed one-minute bar is available."
        )

    return max(
        completed,
        key=lambda bar: bar.begins_at,
    )


def first_real_bar_for_date(
    bars: list[MinuteBar],
    *,
    session_date: date,
) -> MinuteBar:
    selected = [
        bar
        for bar in bars
        if (
            bar.timestamp_et.date() == session_date
            and not bar.interpolated
        )
    ]

    if not selected:
        raise ValueError(
            f"No real bars found for {session_date}."
        )

    return min(
        selected,
        key=lambda bar: bar.begins_at,
    )


def previous_session_close(
    bars: list[MinuteBar],
    *,
    before_date: date,
) -> Decimal:
    previous_bars = [
        bar
        for bar in bars
        if (
            bar.timestamp_et.date() < before_date
            and not bar.interpolated
        )
    ]

    if not previous_bars:
        raise ValueError(
            "No previous-session bar is available."
        )

    latest_previous_bar = max(
        previous_bars,
        key=lambda bar: bar.begins_at,
    )

    return latest_previous_bar.close_price


def median_historical_opening_volume(
    five_minute_bars: list[MinuteBar],
    *,
    before_date: date,
    lookback_days: int,
) -> Decimal:
    """
    Use the 9:30–9:35 five-minute bar from each historical session.
    """

    if lookback_days < 1:
        raise ValueError(
            "Opening-volume lookback must be positive."
        )

    opening_volume_by_date: dict[date, int] = {}

    for bar in five_minute_bars:
        timestamp_et = bar.timestamp_et

        if (
            timestamp_et.date() >= before_date
            or timestamp_et.time() != time(9, 30)
            or bar.interpolated
            or bar.volume <= 0
        ):
            continue

        opening_volume_by_date[
            timestamp_et.date()
        ] = bar.volume

    recent_dates = sorted(
        opening_volume_by_date,
        reverse=True,
    )[:lookback_days]

    if len(recent_dates) < lookback_days:
        raise ValueError(
            f"Only {len(recent_dates)} historical opening "
            f"sessions are available; {lookback_days} are required."
        )

    volumes = [
        opening_volume_by_date[session_date]
        for session_date in recent_dates
    ]

    return Decimal(str(median(volumes)))


class LiveScanner:
    """
    Read-only ORB scanner.

    It evaluates candidate signals but has no order-placement capability.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: RobinhoodClient | None = None,
        journal: Journal | None = None,
    ):
        self.original_config = config
        self.client = client or RobinhoodClient()
        self.journal = journal or Journal()

        monitoring_config = deepcopy(config)

        # Monitoring may evaluate signals while live trading remains disabled.
        monitoring_config["strategy"]["enabled"] = True

        self.signal_engine = SignalEngine(
            monitoring_config
        )

        self.config_hash = Journal.config_hash(
            config
        )

    async def _fetch_bars(
        self,
        session,
        *,
        symbols: list[str],
        start_time: datetime,
        end_time: datetime,
        interval: str,
    ) -> dict[str, list[MinuteBar]]:
        arguments = {
            "symbols": symbols,
            "start_time": start_time.astimezone(
                timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "end_time": end_time.astimezone(
                timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "interval": interval,
            "bounds": "regular",
            "adjustment_type": "split",
        }

        result = await session.call_tool(
            "get_equity_historicals",
            arguments,
        )

        data = _unwrap_tool_result(
            result,
            "get_equity_historicals",
        )

        output: dict[str, list[MinuteBar]] = {
            symbol: []
            for symbol in symbols
        }

        for item in data.get("results") or []:
            if not item:
                continue

            symbol = item.get("symbol")

            if symbol not in output:
                continue

            for raw_bar in item.get("bars") or []:
                if not raw_bar:
                    continue

                output[symbol].append(
                    MinuteBar(
                        symbol=symbol,
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
                        volume=int(
                            raw_bar["volume"]
                        ),
                        session=raw_bar.get(
                            "session",
                            "",
                        ),
                        interpolated=bool(
                            raw_bar.get(
                                "interpolated",
                                False,
                            )
                        ),
                    )
                )

        for symbol in output:
            output[symbol].sort(
                key=lambda bar: bar.begins_at
            )

        return output

    async def scan_once(
        self,
        *,
        now: datetime | None = None,
    ) -> list[ScanResult]:
        now_et = (
            now.astimezone(ET)
            if now is not None
            else datetime.now(ET)
        )

        if now_et.weekday() >= 5:
            raise RuntimeError(
                "The live scanner does not run on weekends."
            )

        market_open = datetime.combine(
            now_et.date(),
            time(9, 30),
            tzinfo=ET,
        )

        if now_et <= market_open + timedelta(minutes=5):
            raise RuntimeError(
                "The opening range is not complete yet."
            )

        history_start = datetime.combine(
            now_et.date() - timedelta(
                days=HISTORY_CALENDAR_DAYS
            ),
            time(9, 30),
            tzinfo=ET,
        )

        configured_symbols = [
            symbol.upper().strip()
            for symbol in self.original_config[
                "universe"
            ]["symbols"]
        ]

        all_symbols = list(
            dict.fromkeys(
                configured_symbols + [SPY_SYMBOL]
            )
        )

        historical_bars: dict[
            str,
            list[MinuteBar],
        ] = {
            symbol: []
            for symbol in all_symbols
        }

        current_bars: dict[
            str,
            list[MinuteBar],
        ] = {
            symbol: []
            for symbol in all_symbols
        }

        async with self.client.session() as session:
            for symbol_batch in batches(
                all_symbols,
                SYMBOL_BATCH_SIZE,
            ):
                history_result = await self._fetch_bars(
                    session,
                    symbols=symbol_batch,
                    start_time=history_start,
                    end_time=now_et,
                    interval="5minute",
                )

                current_result = await self._fetch_bars(
                    session,
                    symbols=symbol_batch,
                    start_time=market_open,
                    end_time=now_et,
                    interval="minute",
                )

                for symbol in symbol_batch:
                    historical_bars[symbol].extend(
                        history_result.get(
                            symbol,
                            [],
                        )
                    )

                    current_bars[symbol].extend(
                        current_result.get(
                            symbol,
                            [],
                        )
                    )

        spy_bars = current_bars.get(
            SPY_SYMBOL,
            [],
        )

        spy_latest = latest_completed_bar(
            spy_bars,
            now_et=now_et,
        )

        completed_spy_bars = [
            bar
            for bar in spy_bars
            if (
                not bar.interpolated
                and bar.timestamp_et.date()
                == now_et.date()
                and bar.timestamp_et
                + timedelta(minutes=1)
                <= now_et
            )
        ]

        spy_vwap = calculate_estimated_vwap(
            completed_spy_bars
        )

        results: list[ScanResult] = []

        lookback_days = int(
            self.original_config["signal"][
                "relative_volume_lookback_days"
            ]
        )

        opening_minutes = int(
            self.original_config["signal"][
                "opening_range_minutes"
            ]
        )

        for symbol in configured_symbols:
            try:
                symbol_current_bars = current_bars[
                    symbol
                ]

                current_bar = latest_completed_bar(
                    symbol_current_bars,
                    now_et=now_et,
                )

                opening_range = calculate_opening_range(
                    symbol_current_bars,
                    session_date=now_et.date(),
                    opening_range_minutes=opening_minutes,
                )

                today_first_bar = first_real_bar_for_date(
                    symbol_current_bars,
                    session_date=now_et.date(),
                )

                previous_close = previous_session_close(
                    historical_bars[symbol],
                    before_date=now_et.date(),
                )

                median_opening_volume = (
                    median_historical_opening_volume(
                        historical_bars[symbol],
                        before_date=now_et.date(),
                        lookback_days=lookback_days,
                    )
                )

                opening_data = OpeningData(
                    symbol=symbol,
                    previous_close=previous_close,
                    today_open=today_first_bar.open_price,
                    opening_range_high=opening_range.high_price,
                    opening_range_low=opening_range.low_price,
                    opening_range_volume=opening_range.volume,
                    median_opening_volume=median_opening_volume,
                )

                live_data = LiveData(
                    timestamp=now_et,
                    completed_bar_close=current_bar.close_price,
                    spy_price=spy_latest.close_price,
                    spy_vwap=spy_vwap,
                    already_entered_today=False,
                )

                decision: SignalDecision = (
                    self.signal_engine.evaluate(
                        opening_data,
                        live_data,
                    )
                )

                results.append(
                    ScanResult(
                        symbol=symbol,
                        approved=decision.approved,
                        reason=decision.reason,
                        gap_percent=decision.gap_percent,
                        relative_volume=decision.relative_volume,
                        current_price=current_bar.close_price,
                        opening_range_high=opening_range.high_price,
                        opening_range_low=opening_range.low_price,
                    )
                )

            except (
                ValueError,
                KeyError,
                RobinhoodClientError,
            ) as error:
                results.append(
                    ScanResult(
                        symbol=symbol,
                        approved=False,
                        reason=f"Data unavailable: {error}",
                    )
                )

        approved = [
            result
            for result in results
            if result.approved
        ]

        self.journal.append_event(
            strategy_id=self.original_config[
                "strategy"
            ]["name"],
            event_type="scanner_cycle",
            symbol=None,
            config_hash=self.config_hash,
            payload={
                "monitoring_only": True,
                "scan_time_et": now_et.isoformat(),
                "symbols_scanned": len(results),
                "approved_count": len(approved),
                "approved_symbols": [
                    result.symbol
                    for result in approved
                ],
                "results": [
                    {
                        "symbol": result.symbol,
                        "approved": result.approved,
                        "reason": result.reason,
                        "gap_percent": str(
                            result.gap_percent
                        ),
                        "relative_volume": str(
                            result.relative_volume
                        ),
                        "current_price": str(
                            result.current_price
                        ),
                    }
                    for result in results
                ],
            },
        )

        return results


def print_results(
    results: list[ScanResult],
) -> None:
    print("\nORB V0 LIVE SCANNER — MONITORING ONLY")
    print("No order tool is available to this scanner.\n")

    approved = [
        result
        for result in results
        if result.approved
    ]

    if approved:
        print("QUALIFIED SIGNALS:")

        for result in approved:
            print(
                f"  {result.symbol}: "
                f"price=${result.current_price}, "
                f"gap={result.gap_percent:.2f}%, "
                f"RVOL={result.relative_volume:.2f}x, "
                f"OR high=${result.opening_range_high}, "
                f"OR low=${result.opening_range_low}"
            )
    else:
        print("No stock currently qualifies.")

    print("\nSCAN DETAILS:")

    for result in results:
        status = (
            "PASS"
            if result.approved
            else "NO"
        )

        print(
            f"  {result.symbol:<6} "
            f"{status:<4} "
            f"{result.reason}"
        )


async def main() -> None:
    config = load_config()

    scanner = LiveScanner(config)

    results = await scanner.scan_once()

    print_results(results)

    print(
        "\nResults were written to the append-only journal."
    )
    print("No order tool was called.")


if __name__ == "__main__":
    asyncio.run(main())