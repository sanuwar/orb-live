from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.config import load_config
from src.journal import Journal
from src.live_scanner import (
    ET,
    LiveScanner,
    batches,
    first_real_bar_for_date,
    latest_completed_bar,
    median_historical_opening_volume,
    previous_session_close,
)
from src.market_data import MinuteBar


def make_bar(
    *,
    session_date: date,
    hour: int = 9,
    minute: int = 30,
    close: str = "100",
    volume: int = 1000,
    symbol: str = "AAPL",
    interpolated: bool = False,
) -> MinuteBar:
    timestamp_et = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        hour,
        minute,
        tzinfo=ET,
    )

    price = Decimal(close)

    return MinuteBar(
        symbol=symbol,
        begins_at=timestamp_et.astimezone(timezone.utc),
        open_price=price,
        high_price=price + Decimal("1"),
        low_price=price - Decimal("1"),
        close_price=price,
        volume=volume,
        session="reg",
        interpolated=interpolated,
    )


def test_batches_splits_symbols():
    symbols = ["AAPL", "MSFT", "NVDA", "AMZN", "META"]

    result = list(batches(symbols, 2))

    assert result == [
        ["AAPL", "MSFT"],
        ["NVDA", "AMZN"],
        ["META"],
    ]


def test_latest_completed_bar_ignores_current_incomplete_bar():
    session_date = date(2026, 7, 20)

    bars = [
        make_bar(
            session_date=session_date,
            hour=10,
            minute=0,
            close="100",
        ),
        make_bar(
            session_date=session_date,
            hour=10,
            minute=1,
            close="101",
        ),
    ]

    now_et = datetime(
        2026,
        7,
        20,
        10,
        1,
        30,
        tzinfo=ET,
    )

    result = latest_completed_bar(
        bars,
        now_et=now_et,
    )

    assert result.close_price == Decimal("100")


def test_latest_completed_bar_ignores_interpolated_bar():
    session_date = date(2026, 7, 20)

    bars = [
        make_bar(
            session_date=session_date,
            hour=10,
            minute=0,
            close="100",
        ),
        make_bar(
            session_date=session_date,
            hour=10,
            minute=1,
            close="200",
            interpolated=True,
        ),
    ]

    now_et = datetime(
        2026,
        7,
        20,
        10,
        3,
        tzinfo=ET,
    )

    result = latest_completed_bar(
        bars,
        now_et=now_et,
    )

    assert result.close_price == Decimal("100")


def test_first_real_bar_for_date():
    session_date = date(2026, 7, 20)

    bars = [
        make_bar(
            session_date=session_date,
            hour=9,
            minute=31,
            close="101",
        ),
        make_bar(
            session_date=session_date,
            hour=9,
            minute=30,
            close="100",
        ),
    ]

    result = first_real_bar_for_date(
        bars,
        session_date=session_date,
    )

    assert result.timestamp_et.time().hour == 9
    assert result.timestamp_et.time().minute == 30
    assert result.close_price == Decimal("100")


def test_previous_session_close_uses_latest_prior_bar():
    current_date = date(2026, 7, 20)
    previous_date = date(2026, 7, 17)

    bars = [
        make_bar(
            session_date=previous_date,
            hour=15,
            minute=58,
            close="99",
        ),
        make_bar(
            session_date=previous_date,
            hour=15,
            minute=59,
            close="100",
        ),
        make_bar(
            session_date=current_date,
            hour=9,
            minute=30,
            close="105",
        ),
    ]

    result = previous_session_close(
        bars,
        before_date=current_date,
    )

    assert result == Decimal("100")


def test_median_historical_opening_volume():
    before_date = date(2026, 7, 21)
    bars = []

    volumes = [1000, 2000, 3000, 4000, 5000]

    for index, volume in enumerate(volumes):
        session_date = before_date - timedelta(
            days=index + 1
        )

        bars.append(
            make_bar(
                session_date=session_date,
                hour=9,
                minute=30,
                volume=volume,
            )
        )

    result = median_historical_opening_volume(
        bars,
        before_date=before_date,
        lookback_days=5,
    )

    assert result == Decimal("3000")


@pytest.mark.anyio
async def test_scanner_rejects_weekend_before_network_call(
    tmp_path,
):
    config = deepcopy(load_config())

    scanner = LiveScanner(
        config,
        journal=Journal(
            tmp_path / "scanner.sqlite"
        ),
    )

    weekend = datetime(
        2026,
        7,
        19,
        10,
        0,
        tzinfo=ET,
    )

    with pytest.raises(
        RuntimeError,
        match="weekends",
    ):
        await scanner.scan_once(now=weekend)