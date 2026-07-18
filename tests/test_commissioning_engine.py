from __future__ import annotations

from decimal import Decimal

import pytest

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.commissioning_engine import (
    CommissioningEngine,
)
from src.config import load_config
from src.dry_run_engine import DryRunPlan
from src.trade_state import TradeStateStore


class FakeAccountReader:
    def __init__(self, state: AccountState):
        self.state = state
        self.requested_accounts: list[
            str | None
        ] = []

    async def read(
        self,
        *,
        account_number: str | None = None,
    ) -> AccountState:
        self.requested_accounts.append(
            account_number
        )
        return self.state


def make_account_state(
    *,
    cash: str = "1000",
    unleveraged_buying_power: str = "800",
    agentic_allowed: bool = True,
    account_status: str = "active",
    positions: tuple[
        EquityPosition, ...
    ] = (),
    orders: tuple[
        EquityOrder, ...
    ] = (),
) -> AccountState:
    return AccountState(
        account=AccountInfo(
            account_number="TEST12345678",
            masked_account_number="••••5678",
            brokerage_account_type="individual",
            trading_type="cash",
            nickname="Test",
            agentic_allowed=agentic_allowed,
            state=account_status,
        ),
        portfolio=PortfolioState(
            total_value=Decimal("1000"),
            equity_value=Decimal("1000"),
            cash=Decimal(cash),
            pending_deposits=Decimal("0"),
            buying_power=Decimal(
                unleveraged_buying_power
            ),
            unleveraged_buying_power=Decimal(
                unleveraged_buying_power
            ),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=positions,
        open_orders=orders,
    )


def install_fake_dry_run(
    monkeypatch,
    plan: DryRunPlan,
):
    calls: list[dict] = []

    class FakeDryRunEngine:
        def __init__(
            self,
            config,
            *,
            settled_cash,
            client=None,
            journal=None,
        ):
            calls.append(
                {
                    "config": config,
                    "settled_cash":
                        settled_cash,
                    "client": client,
                    "journal": journal,
                }
            )

        async def create_plan(self):
            return plan

    monkeypatch.setattr(
        "src.commissioning_engine.DryRunEngine",
        FakeDryRunEngine,
    )

    return calls


def test_hypothetical_cash_uses_lower_value():
    state = make_account_state(
        cash="1000",
        unleveraged_buying_power="800",
    )

    result = (
        CommissioningEngine.hypothetical_cash(
            state
        )
    )

    assert result == Decimal("800")


def test_hypothetical_cash_never_negative():
    state = make_account_state(
        cash="-50",
        unleveraged_buying_power="800",
    )

    result = (
        CommissioningEngine.hypothetical_cash(
            state
        )
    )

    assert result == Decimal("0")


@pytest.mark.anyio
async def test_run_uses_account_cash_for_dry_run(
    monkeypatch,
    tmp_path,
):
    state = make_account_state(
        cash="1000",
        unleveraged_buying_power="800",
    )

    expected_plan = DryRunPlan(
        symbol="",
        approved=False,
        reason="No qualified ORB signal.",
    )

    dry_run_calls = install_fake_dry_run(
        monkeypatch,
        expected_plan,
    )

    client = object()

    engine = CommissioningEngine(
        load_config(),
        trade_store=TradeStateStore(
            tmp_path / "cash.sqlite"
        ),
        client=client,
    )

    fake_reader = FakeAccountReader(state)
    engine.account_reader = fake_reader

    report = await engine.run(
        account_number="TEST12345678"
    )

    assert report.account_state == state
    assert report.hypothetical_cash == Decimal(
        "800"
    )
    assert report.dry_run_plan == expected_plan

    assert fake_reader.requested_accounts == [
        "TEST12345678"
    ]

    assert len(dry_run_calls) == 1
    assert (
        dry_run_calls[0]["settled_cash"]
        == Decimal("800")
    )
    assert dry_run_calls[0]["client"] is client

    assert report.live_order_capable is False
    assert (
        report.safe_for_shadow_monitoring
        is True
    )

    assert report.watchdog_decision.healthy is False
    assert (
        report.watchdog_decision
        .block_new_entries
        is True
    )


@pytest.mark.anyio
async def test_zero_cash_skips_market_scan(
    monkeypatch,
    tmp_path,
):
    state = make_account_state(
        cash="0",
        unleveraged_buying_power="0",
    )

    class ForbiddenDryRunEngine:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "Dry-run engine should not be "
                "created when cash is zero."
            )

    monkeypatch.setattr(
        "src.commissioning_engine.DryRunEngine",
        ForbiddenDryRunEngine,
    )

    engine = CommissioningEngine(
        load_config(),
        trade_store=TradeStateStore(
            tmp_path / "zero_cash.sqlite"
        ),
        client=object(),
    )

    engine.account_reader = FakeAccountReader(
        state
    )

    report = await engine.run()

    assert report.hypothetical_cash == Decimal(
        "0"
    )
    assert report.dry_run_plan.approved is False
    assert (
        "No positive broker-reported cash"
        in report.dry_run_plan.reason
    )
    assert any(
        "scanning was skipped"
        in warning
        for warning in report.warnings
    )


@pytest.mark.anyio
async def test_existing_account_activity_adds_warnings(
    monkeypatch,
    tmp_path,
):
    position = EquityPosition(
        symbol="AAPL",
        quantity=Decimal("2"),
        intraday_quantity=Decimal("2"),
        average_buy_price=Decimal("100"),
        shares_available_for_sells=Decimal(
            "2"
        ),
        shares_held_for_sells=Decimal("0"),
        position_type="long",
    )

    entry_order = EquityOrder(
        order_id="entry-1",
        symbol="MSFT",
        side="buy",
        order_type="limit",
        state="confirmed",
        quantity=Decimal("1"),
        cumulative_quantity=Decimal("0"),
        limit_price=Decimal("400"),
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    )

    stop_order = EquityOrder(
        order_id="stop-1",
        symbol="AAPL",
        side="sell",
        order_type="stop_market",
        state="confirmed",
        quantity=Decimal("2"),
        cumulative_quantity=Decimal("0"),
        limit_price=None,
        stop_price=Decimal("95"),
        trigger="stop",
        placed_agent="agentic",
    )

    state = make_account_state(
        positions=(position,),
        orders=(
            entry_order,
            stop_order,
        ),
    )

    install_fake_dry_run(
        monkeypatch,
        DryRunPlan(
            symbol="",
            approved=False,
            reason="No signal.",
        ),
    )

    engine = CommissioningEngine(
        load_config(),
        trade_store=TradeStateStore(
            tmp_path / "zero_cash.sqlite"
        ),
        client=object(),
    )

    engine.account_reader = FakeAccountReader(
        state
    )

    report = await engine.run()

    warning_text = " ".join(
        report.warnings
    ).lower()

    assert "open position" in warning_text
    assert "open buy orders" in warning_text
    assert (
        "protective stop orders"
        in warning_text
    )


@pytest.mark.anyio
async def test_inactive_account_is_not_shadow_safe(
    monkeypatch,
    tmp_path,
):
    state = make_account_state(
        agentic_allowed=True,
        account_status="inactive",
    )

    install_fake_dry_run(
        monkeypatch,
        DryRunPlan(
            symbol="",
            approved=False,
            reason="No signal.",
        ),
    )

    engine = CommissioningEngine(
        load_config(),
        trade_store=TradeStateStore(
            tmp_path / "zero_cash.sqlite"
        ),
        client=object(),
    )

    engine.account_reader = FakeAccountReader(
        state
    )

    report = await engine.run()

    assert (
        report.safe_for_shadow_monitoring
        is False
    )
    assert report.live_order_capable is False