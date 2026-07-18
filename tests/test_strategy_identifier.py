from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tests.snapshot_helpers import (
    apply_broker_snapshot,
    make_broker_snapshot,
)

from src.account_state import (
    AccountInfo,
    AccountState,
    PortfolioState,
)
from src.commissioning_engine import CommissioningEngine
from src.config import ConfigError, load_config
from src.shadow_runtime import ShadowRuntime
from src.trade_state import TradeStateStore


CONFIGURED_STRATEGY = "custom_orb_strategy"
OTHER_STRATEGY = "unrelated_strategy"


class ForbiddenBrokerClient:
    def __getattr__(self, name):
        raise AssertionError(
            f"Broker access is forbidden in this test: {name}"
        )


class FakeAccountReader:
    def __init__(self, state: AccountState):
        self.state = state
        self.calls = 0

    async def read(
        self,
        *,
        account_number: str | None = None,
    ) -> AccountState:
        self.calls += 1
        return self.state

    async def read_snapshot(
        self,
        *,
        account_number: str | None = None,
    ):
        return make_broker_snapshot(
            await self.read(
                account_number=account_number
            )
        )


class ForbiddenOrderMonitor:
    async def monitor_trade(self, **kwargs):
        raise AssertionError(
            "No order may be observed in this test."
        )


class TrackingTradeStateStore(TradeStateStore):
    def __init__(self, db_path):
        self.metric_calls: list[
            tuple[str, str | None]
        ] = []
        super().__init__(db_path)

    def daily_pnl(
        self,
        session_date,
        *,
        strategy=None,
    ):
        self.metric_calls.append(
            ("daily", strategy)
        )
        return super().daily_pnl(
            session_date,
            strategy=strategy,
        )

    def weekly_pnl(
        self,
        session_date,
        *,
        strategy=None,
    ):
        self.metric_calls.append(
            ("weekly", strategy)
        )
        return super().weekly_pnl(
            session_date,
            strategy=strategy,
        )

    def current_drawdown(
        self,
        *,
        strategy=None,
    ):
        self.metric_calls.append(
            ("drawdown", strategy)
        )
        return super().current_drawdown(
            strategy=strategy,
        )


def make_account_state() -> AccountState:
    return AccountState(
        account=AccountInfo(
            account_number="FAKE_ACCOUNT",
            masked_account_number="FAKE",
            brokerage_account_type="individual",
            trading_type="cash",
            nickname="Test",
            agentic_allowed=True,
            state="active",
        ),
        portfolio=PortfolioState(
            total_value=Decimal("0"),
            equity_value=Decimal("0"),
            cash=Decimal("0"),
            pending_deposits=Decimal("0"),
            buying_power=Decimal("0"),
            unleveraged_buying_power=Decimal("0"),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=(),
        open_orders=(),
    )


def close_trade(
    store: TradeStateStore,
    *,
    strategy: str,
    symbol: str,
    session_date,
    pnl: str,
    occurred_at: datetime,
) -> None:
    trade = store.reserve_entry(
        strategy=strategy,
        symbol=symbol,
        session_date=session_date,
        quantity=1,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
        occurred_at=occurred_at,
    )
    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id=f"fake-entry-{symbol}",
        occurred_at=occurred_at + timedelta(
            seconds=1
        ),
    )
    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=1,
        average_entry_price=Decimal("100"),
        occurred_at=occurred_at + timedelta(
            seconds=2
        ),
    )
    store.close_trade(
        trade.trade_id,
        average_exit_price=(
            Decimal("100") + Decimal(pnl)
        ),
        reason="regression_test",
        occurred_at=occurred_at + timedelta(
            seconds=3
        ),
    )
    apply_broker_snapshot(
        store,
        strategy=strategy,
        account_state=make_account_state(),
    )


def seed_strategy_metrics(
    store: TradeStateStore,
) -> None:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    start = datetime.combine(
        today,
        time(14, 0),
        tzinfo=timezone.utc,
    )

    close_trade(
        store,
        strategy=CONFIGURED_STRATEGY,
        symbol="AAPL",
        session_date=today,
        pnl="10",
        occurred_at=start,
    )
    close_trade(
        store,
        strategy=CONFIGURED_STRATEGY,
        symbol="MSFT",
        session_date=today,
        pnl="-4",
        occurred_at=start + timedelta(minutes=1),
    )
    close_trade(
        store,
        strategy=CONFIGURED_STRATEGY,
        symbol="NVDA",
        session_date=today,
        pnl="-8",
        occurred_at=start + timedelta(minutes=2),
    )
    close_trade(
        store,
        strategy=OTHER_STRATEGY,
        symbol="AMZN",
        session_date=today,
        pnl="-90",
        occurred_at=start + timedelta(minutes=3),
    )


@pytest.mark.anyio
async def test_runtimes_use_configured_strategy_for_risk_metrics(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "strategy_metrics.sqlite"
    store = TrackingTradeStateStore(
        database_path
    )
    seed_strategy_metrics(store)

    def forbid_default_store(*args, **kwargs):
        raise AssertionError(
            "A default or production database path was opened."
        )

    monkeypatch.setattr(
        "src.commissioning_engine.TradeStateStore",
        forbid_default_store,
    )
    monkeypatch.setattr(
        "src.shadow_runtime.TradeStateStore",
        forbid_default_store,
    )

    config = deepcopy(load_config())
    config["strategy"]["name"] = (
        CONFIGURED_STRATEGY
    )

    assert config["strategy"]["enabled"] is False
    assert store.db_path == database_path

    account_state = make_account_state()
    broker_client = ForbiddenBrokerClient()

    commissioning = CommissioningEngine(
        config,
        client=broker_client,
        trade_store=store,
    )
    commissioning_reader = FakeAccountReader(
        account_state
    )
    commissioning.account_reader = (
        commissioning_reader
    )

    commissioning_report = (
        await commissioning.run()
    )

    shadow = ShadowRuntime(
        config,
        client=broker_client,
        trade_store=store,
        order_monitor=ForbiddenOrderMonitor(),
    )
    shadow_reader = FakeAccountReader(
        account_state
    )
    shadow.account_reader = shadow_reader

    shadow_report = await shadow.run()

    assert commissioning_report.daily_pnl == Decimal(
        "-2"
    )
    assert commissioning_report.weekly_pnl == Decimal(
        "-2"
    )
    assert (
        commissioning_report.strategy_drawdown
        == Decimal("12")
    )

    assert shadow_report.daily_pnl == Decimal("-2")
    assert shadow_report.weekly_pnl == Decimal("-2")
    assert shadow_report.strategy_drawdown == Decimal(
        "12"
    )

    assert store.metric_calls == [
        ("daily", CONFIGURED_STRATEGY),
        ("weekly", CONFIGURED_STRATEGY),
        ("drawdown", CONFIGURED_STRATEGY),
        ("daily", CONFIGURED_STRATEGY),
        ("weekly", CONFIGURED_STRATEGY),
        ("drawdown", CONFIGURED_STRATEGY),
    ]
    assert commissioning_reader.calls == 1
    assert shadow_reader.calls == 2


@pytest.mark.parametrize(
    "invalid_name",
    [
        None,
        "",
        "   ",
        123,
        "invalid strategy",
        " padded_name ",
    ],
)
def test_runtime_components_reject_invalid_strategy_names(
    tmp_path,
    invalid_name,
):
    config = deepcopy(load_config())

    if invalid_name is None:
        del config["strategy"]["name"]
    else:
        config["strategy"]["name"] = invalid_name

    store = TradeStateStore(
        tmp_path / "invalid_strategy.sqlite"
    )
    broker_client = ForbiddenBrokerClient()

    with pytest.raises(
        ConfigError,
        match="strategy.name",
    ):
        CommissioningEngine(
            config,
            client=broker_client,
            trade_store=store,
        )

    with pytest.raises(
        ConfigError,
        match="strategy.name",
    ):
        ShadowRuntime(
            config,
            client=broker_client,
            trade_store=store,
            order_monitor=ForbiddenOrderMonitor(),
        )
