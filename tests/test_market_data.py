from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.market_data import (
    ET,
    MinuteBar,
    calculate_estimated_vwap,
    calculate_median_opening_volume,
    calculate_opening_range,
    parse_timestamp,
)


def make_bar(
    *,
    session_date: date,
    minute_offset: int,
    high: str,
    low: str,
    close: str,
    volume: int,
    symbol: str = "AAPL",
    interpolated: bool = False,
) -> MinuteBar:
    timestamp_et = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        9,
        30,
        tzinfo=ET,
    ) + timedelta(minutes=minute_offset)

    return MinuteBar(
        symbol=symbol,
        begins_at=timestamp_et.astimezone(timezone.utc),
        open_price=Decimal(close),
        high_price=Decimal(high),
        low_price=Decimal(low),
        close_price=Decimal(close),
        volume=volume,
        session="reg",
        interpolated=interpolated,
    )


def test_parse_timestamp_converts_to_utc():
    parsed = parse_timestamp("2026-07-17T13:30:00Z")

    assert parsed.tzinfo == timezone.utc
    assert parsed.hour == 13
    assert parsed.minute == 30


def test_calculate_opening_range():
    session_date = date(2026, 7, 17)

    bars = [
        make_bar(
            session_date=session_date,
            minute_offset=0,
            high="101",
            low="99",
            close="100",
            volume=100,
        ),
        make_bar(
            session_date=session_date,
            minute_offset=1,
            high="102",
            low="100",
            close="101",
            volume=200,
        ),
        make_bar(
            session_date=session_date,
            minute_offset=2,
            high="103",
            low="98",
            close="102",
            volume=300,
        ),
        make_bar(
            session_date=session_date,
            minute_offset=3,
            high="102",
            low="99",
            close="101",
            volume=400,
        ),
        make_bar(
            session_date=session_date,
            minute_offset=4,
            high="104",
            low="100",
            close="103",
            volume=500,
        ),
    ]

    result = calculate_opening_range(
        bars,
        session_date=session_date,
    )

    assert result.symbol == "AAPL"
    assert result.high_price == Decimal("104")
    assert result.low_price == Decimal("98")
    assert result.volume == 1500
    assert result.bar_count == 5


def test_interpolated_bar_is_rejected_from_opening_range():
    session_date = date(2026, 7, 17)

    bars = [
        make_bar(
            session_date=session_date,
            minute_offset=minute,
            high="101",
            low="99",
            close="100",
            volume=100,
            interpolated=(minute == 2),
        )
        for minute in range(5)
    ]

    with pytest.raises(
        ValueError,
        match="Expected at least 5 real opening bars",
    ):
        calculate_opening_range(
            bars,
            session_date=session_date,
        )


def test_calculate_estimated_vwap():
    session_date = date(2026, 7, 17)

    bars = [
        make_bar(
            session_date=session_date,
            minute_offset=0,
            high="102",
            low="98",
            close="100",
            volume=100,
        ),
        make_bar(
            session_date=session_date,
            minute_offset=1,
            high="105",
            low="99",
            close="102",
            volume=300,
        ),
    ]

    result = calculate_estimated_vwap(bars)

    expected = (
        Decimal("100") * Decimal("100")
        + Decimal("102") * Decimal("300")
    ) / Decimal("400")

    assert result == expected


def test_vwap_ignores_interpolated_and_zero_volume_bars():
    session_date = date(2026, 7, 17)

    bars = [
        make_bar(
            session_date=session_date,
            minute_offset=0,
            high="102",
            low="98",
            close="100",
            volume=100,
        ),
        make_bar(
            session_date=session_date,
            minute_offset=1,
            high="500",
            low="500",
            close="500",
            volume=1000,
            interpolated=True,
        ),
        make_bar(
            session_date=session_date,
            minute_offset=2,
            high="200",
            low="200",
            close="200",
            volume=0,
        ),
    ]

    assert calculate_estimated_vwap(bars) == Decimal("100")


def test_calculate_median_opening_volume():
    before_date = date(2026, 7, 21)
    bars = []

    for day_number in range(20):
        session_date = before_date - timedelta(
            days=day_number + 1
        )

        opening_volume = 1000 + day_number * 100

        for minute in range(5):
            bars.append(
                make_bar(
                    session_date=session_date,
                    minute_offset=minute,
                    high="101",
                    low="99",
                    close="100",
                    volume=opening_volume // 5,
                )
            )

    result = calculate_median_opening_volume(
        bars,
        before_date=before_date,
        lookback_days=20,
    )

    assert result == Decimal("1950")


def test_median_volume_requires_complete_lookback():
    before_date = date(2026, 7, 21)
    bars = []

    for day_number in range(5):
        session_date = before_date - timedelta(
            days=day_number + 1
        )

        for minute in range(5):
            bars.append(
                make_bar(
                    session_date=session_date,
                    minute_offset=minute,
                    high="101",
                    low="99",
                    close="100",
                    volume=100,
                )
            )

    with pytest.raises(
        ValueError,
        match="5 complete sessions",
    ):
        calculate_median_opening_volume(
            bars,
            before_date=before_date,
            lookback_days=20,
        )