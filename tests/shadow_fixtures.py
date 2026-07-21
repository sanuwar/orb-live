"""
Synthetic, deterministic fixtures for Milestone 6 shadow-mode tests.

Everything here is fabricated in-process: no live broker call, no live
market data, no production database. Bars, snapshots, and sessions are
built by hand so every strategy and risk path can be exercised
deterministically.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.broker_snapshot import (
    BrokerSnapshot,
    BrokerSnapshotMetadata,
    account_fingerprint,
)
from src.market_data import MinuteBar


ET = ZoneInfo("America/New_York")

# A normal full regular session used across the fixtures. 2026-07-20 is a
# Monday.
TRADING_DATE = date(2026, 7, 20)
ACCOUNT_NUMBER = "ACCT-SHADOW-0001"
ACCOUNT_FINGERPRINT = account_fingerprint(ACCOUNT_NUMBER)


def et_dt(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=ET)


def bar(
    symbol: str,
    when: datetime,
    open_price: str,
    high_price: str,
    low_price: str,
    close_price: str,
    *,
    volume: int = 1_000,
    interpolated: bool = False,
    session: str = "reg",
) -> MinuteBar:
    return MinuteBar(
        symbol=symbol,
        begins_at=when,
        open_price=Decimal(open_price),
        high_price=Decimal(high_price),
        low_price=Decimal(low_price),
        close_price=Decimal(close_price),
        volume=volume,
        session=session,
        interpolated=interpolated,
    )


def prior_weekdays(before: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = before - timedelta(days=1)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def historical_five_minute_bars(
    symbol: str,
    *,
    trading_date: date = TRADING_DATE,
    sessions: int = 22,
    opening_volume: int = 1_000_000,
    previous_close: str = "100.00",
) -> list[MinuteBar]:
    """Twenty-plus prior sessions, each with a 9:30 opening bar and a
    session-close bar, so median opening volume and the previous close are
    both available."""

    bars: list[MinuteBar] = []
    for day in prior_weekdays(trading_date, sessions):
        bars.append(
            bar(
                symbol,
                et_dt(day, 9, 30),
                "99.00",
                "100.50",
                "98.50",
                "100.00",
                volume=opening_volume,
            )
        )
        bars.append(
            bar(
                symbol,
                et_dt(day, 15, 55),
                "100.00",
                "100.20",
                "99.80",
                previous_close,
                volume=500_000,
            )
        )
    bars.sort(key=lambda item: item.begins_at)
    return bars


def opening_range_bars(
    symbol: str,
    *,
    trading_date: date = TRADING_DATE,
    open_price: str = "102.00",
    high: str = "103.00",
    low: str = "101.50",
    per_bar_volume: int = 700_000,
) -> list[MinuteBar]:
    """Five one-minute opening-range bars (9:30-9:34)."""

    bars: list[MinuteBar] = []
    for minute in range(5):
        first = minute == 0
        bars.append(
            bar(
                symbol,
                et_dt(trading_date, 9, 30 + minute),
                open_price if first else "102.20",
                high,
                low,
                "102.50",
                volume=per_bar_volume,
            )
        )
    return bars


def spy_current_bars(
    *,
    trading_date: date = TRADING_DATE,
    upto_minute: int = 44,
) -> list[MinuteBar]:
    """SPY bars whose latest completed close sits above the session VWAP."""

    bars = [
        bar("SPY", et_dt(trading_date, 9, 30), "498", "499", "497", "498"),
        bar(
            "SPY",
            et_dt(trading_date, 9, upto_minute),
            "499",
            "501",
            "499",
            "500",
        ),
    ]
    return bars


def qualifying_symbol_current_bars(
    symbol: str,
    *,
    trading_date: date = TRADING_DATE,
    breakout_minute: int = 44,
    breakout_close: str = "104.00",
) -> list[MinuteBar]:
    """Opening range plus a completed breakout bar above the range high."""

    bars = opening_range_bars(symbol, trading_date=trading_date)
    bars.append(
        bar(
            symbol,
            et_dt(trading_date, 9, breakout_minute),
            "103.10",
            breakout_close,
            "103.00",
            breakout_close,
            volume=500_000,
        )
    )
    bars.sort(key=lambda item: item.begins_at)
    return bars


def make_snapshot(
    *,
    completed_at: datetime,
    account_number: str = ACCOUNT_NUMBER,
    cash: str = "5000.00",
    positions: tuple[EquityPosition, ...] = (),
    open_orders: tuple[EquityOrder, ...] = (),
    fingerprint: str | None = None,
) -> BrokerSnapshot:
    started = completed_at - timedelta(seconds=3)
    positions_read = completed_at - timedelta(seconds=2)
    orders_read = completed_at - timedelta(seconds=1)
    reconciliation_id = "recon-shadow-0001"

    account_state = AccountState(
        account=AccountInfo(
            account_number=account_number,
            masked_account_number="••••0001",
            brokerage_account_type="individual",
            trading_type="margin",
            nickname=None,
            agentic_allowed=True,
            state="active",
        ),
        portfolio=PortfolioState(
            total_value=Decimal(cash),
            equity_value=Decimal("0"),
            cash=Decimal(cash),
            pending_deposits=Decimal("0"),
            buying_power=Decimal(cash),
            unleveraged_buying_power=Decimal(cash),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=positions,
        open_orders=open_orders,
    )

    return BrokerSnapshot(
        account_state=account_state,
        metadata=BrokerSnapshotMetadata(
            reconciliation_id=reconciliation_id,
            account_fingerprint=(
                fingerprint
                if fingerprint is not None
                else account_fingerprint(account_number)
            ),
            started_at=started,
            positions_read_at=positions_read,
            orders_read_at=orders_read,
            completed_at=completed_at,
            positions_cycle_id=reconciliation_id,
            orders_cycle_id=reconciliation_id,
            positions_complete=True,
            orders_complete=True,
        ),
    )


def sample_position(symbol: str = "AAPL", quantity: str = "10") -> EquityPosition:
    return EquityPosition(
        symbol=symbol,
        quantity=Decimal(quantity),
        intraday_quantity=Decimal("0"),
        average_buy_price=Decimal("100"),
        shares_available_for_sells=Decimal(quantity),
        shares_held_for_sells=Decimal("0"),
        position_type="long",
    )


def sample_open_order(symbol: str = "AAPL") -> EquityOrder:
    return EquityOrder(
        order_id="order-0001",
        symbol=symbol,
        side="buy",
        order_type="limit",
        state="confirmed",
        quantity=Decimal("10"),
        cumulative_quantity=Decimal("0"),
        limit_price=Decimal("100"),
        stop_price=None,
        trigger="immediate",
        placed_agent="user",
    )
