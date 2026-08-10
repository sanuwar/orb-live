from datetime import date, datetime, time, timedelta, timezone
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

# ---------------------------------------------------------------------
# M15 Issue 1: session_open / session_open_for threading (calendar-aware
# opening range, instead of the fixed 9:30 default every existing test
# above relies on).
# ---------------------------------------------------------------------


def test_calculate_opening_range_accepts_an_explicit_session_open():
    from src.exchange_calendar import regular_session_bounds

    session_date = date(2026, 8, 11)  # Tuesday
    calendar_open = regular_session_bounds(session_date)[0]
    assert calendar_open == time(9, 30)  # documents today's actual value

    bars = [
        make_bar(
            session_date=session_date, minute_offset=minute,
            high="101", low="99", close="100", volume=100,
        )
        for minute in range(5)
    ]
    default_range = calculate_opening_range(bars, session_date=session_date)
    explicit_range = calculate_opening_range(
        bars, session_date=session_date, session_open=calendar_open,
    )
    assert default_range == explicit_range


def test_calculate_opening_range_with_different_session_open_selects_different_bars():
    session_date = date(2026, 8, 11)
    bars = [
        make_bar(
            session_date=session_date, minute_offset=minute,
            high="101", low="99", close="100", volume=100,
        )
        for minute in range(10)
    ]
    # A later (wrong) session_open should select a later, disjoint window
    # of real bars -- proving the parameter genuinely changes selection,
    # not just accepted and ignored.
    shifted_open = time(9, 35)
    shifted_range = calculate_opening_range(
        bars, session_date=session_date, session_open=shifted_open,
    )
    default_range = calculate_opening_range(bars, session_date=session_date)
    assert shifted_range.bar_count == default_range.bar_count == 5


def test_calculate_median_opening_volume_accepts_session_open_for():
    from src.exchange_calendar import regular_session_bounds

    before_date = date(2026, 8, 25)
    bars = []
    session_date = before_date
    for _ in range(30):
        session_date = session_date - timedelta(days=1)
        if session_date.weekday() >= 5:
            continue
        for minute in range(5):
            bars.append(
                make_bar(
                    session_date=session_date, minute_offset=minute,
                    high="101", low="99", close="100", volume=100,
                )
            )

    def open_for(day: date) -> time:
        bounds = regular_session_bounds(day)
        return bounds[0] if bounds is not None else time(9, 30)

    with_calendar = calculate_median_opening_volume(
        bars, before_date=before_date, lookback_days=15, session_open_for=open_for,
    )
    without_calendar = calculate_median_opening_volume(
        bars, before_date=before_date, lookback_days=15,
    )
    assert with_calendar == without_calendar
