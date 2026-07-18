from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from src.account_state import (
    AccountStateReader,
    OPEN_ORDER_STATES,
    RobinhoodClientError,
    mask_account_number,
    parse_order,
    select_agentic_account,
)
from src.broker_snapshot import account_fingerprint


class FakeResult:
    def __init__(self, data: dict):
        self.isError = False
        self.structuredContent = {"data": data}
        self.content = []


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
    ):
        self.calls.append((tool_name, arguments))

        if tool_name == "get_accounts":
            return FakeResult(
                {
                    "accounts": [
                        {
                            "account_number": "TEST12345678",
                            "rhs_account_number": "12345678",
                            "type": "cash",
                            "brokerage_account_type": "individual",
                            "nickname": "Agentic Test",
                            "is_default": False,
                            "agentic_allowed": True,
                            "option_level": "option_level_0",
                            "state": "active",
                            "deactivated": False,
                            "permanently_deactivated": False,
                        }
                    ]
                }
            )

        if tool_name == "get_portfolio":
            return FakeResult(
                {
                    "total_value": "2680.03",
                    "equity_value": "2680.03",
                    "cash": "2680.03",
                    "pending_deposits": "0",
                    "currency": "USD",
                    "buying_power": {
                        "buying_power": "2680.03",
                        "unleveraged_buying_power": "2680.03",
                        "intraday_buying_power": None,
                        "display_currency": "USD",
                    },
                }
            )

        if tool_name == "get_equity_positions":
            return FakeResult(
                {
                    "positions": [
                        {
                            "symbol": "AAPL",
                            "quantity": "2",
                            "intraday_quantity": "2",
                            "average_buy_price": "200",
                            "shares_available_for_sells": "2",
                            "shares_held_for_sells": "0",
                            "type": "long",
                        }
                    ],
                    "next": None,
                }
            )

        if tool_name == "get_equity_orders":
            return FakeResult(
                {
                    "orders": [
                        {
                            "id": "stop-order-1",
                            "symbol": "AAPL",
                            "side": "sell",
                            "type": "market",
                            "state": "confirmed",
                            "quantity": "2",
                            "cumulative_quantity": "0",
                            "price": None,
                            "stop_price": "195",
                            "trigger": "stop",
                            "placed_agent": "agentic",
                        },
                        {
                            "id": "old-order-1",
                            "symbol": "MSFT",
                            "side": "buy",
                            "type": "limit",
                            "state": "filled",
                            "quantity": "1",
                            "cumulative_quantity": "1",
                            "price": "500",
                            "stop_price": None,
                            "trigger": "immediate",
                            "placed_agent": "agentic",
                        },
                    ],
                    "next": None,
                }
            )

        raise AssertionError(
            f"Unexpected tool call: {tool_name}"
        )


class FakeClient:
    def __init__(self):
        self.fake_session = FakeSession()

    @asynccontextmanager
    async def session(self):
        yield self.fake_session


def eligible_account(
    account_number: str,
) -> dict:
    return {
        "account_number": account_number,
        "agentic_allowed": True,
        "state": "active",
        "deactivated": False,
        "permanently_deactivated": False,
    }


def test_mask_account_number():
    assert (
        mask_account_number("TEST12345678")
        == "••••5678"
    )


def test_select_single_agentic_account():
    account = eligible_account(
        "TEST12345678"
    )

    selected = select_agentic_account(
        [account]
    )

    assert selected == account


def test_requested_account_must_be_eligible():
    with pytest.raises(
        RobinhoodClientError,
        match="not an active",
    ):
        select_agentic_account(
            [
                eligible_account(
                    "TEST12345678"
                )
            ],
            requested_account_number=(
                "OTHER87654321"
            ),
        )


def test_multiple_accounts_require_explicit_selection():
    with pytest.raises(
        RobinhoodClientError,
        match="More than one",
    ):
        select_agentic_account(
            [
                eligible_account(
                    "TEST12345678"
                ),
                eligible_account(
                    "TEST87654321"
                ),
            ]
        )


def test_parse_stop_market_order():
    order = parse_order(
        {
            "id": "order-123",
            "symbol": "aapl",
            "side": "sell",
            "type": "market",
            "state": "confirmed",
            "quantity": "2",
            "cumulative_quantity": "0",
            "price": None,
            "stop_price": "195",
            "trigger": "stop",
            "placed_agent": "agentic",
        }
    )

    assert order.symbol == "AAPL"
    assert order.order_type == "stop_market"
    assert order.stop_price == Decimal("195")
    assert order.state in OPEN_ORDER_STATES


@pytest.mark.anyio
async def test_read_account_state():
    client = FakeClient()
    reader = AccountStateReader(
        client=client
    )

    state = await reader.read()

    assert (
        state.account.masked_account_number
        == "••••5678"
    )
    assert state.account.trading_type == "cash"

    assert (
        state.portfolio.buying_power
        == Decimal("2680.03")
    )
    assert (
        state.portfolio.unleveraged_buying_power
        == Decimal("2680.03")
    )

    assert state.open_position_count == 1
    assert state.positions[0].symbol == "AAPL"
    assert (
        state.positions[0].quantity
        == Decimal("2")
    )

    # The filled MSFT order is excluded.
    assert len(state.open_orders) == 1
    assert (
        state.protective_stop_order_count
        == 1
    )
    assert state.open_entry_order_count == 0

    assert client.fake_session.calls == [
        ("get_accounts", {}),
        (
            "get_portfolio",
            {
                "account_number":
                    "TEST12345678"
            },
        ),
        (
            "get_equity_positions",
            {
                "account_number":
                    "TEST12345678"
            },
        ),
        (
            "get_equity_orders",
            {
                "account_number":
                    "TEST12345678"
            },
        ),
    ]

@pytest.mark.anyio
async def test_read_snapshot_records_one_complete_read_cycle():
    client = FakeClient()
    snapshot = await AccountStateReader(
        client=client
    ).read_snapshot()
    metadata = snapshot.metadata

    assert metadata.reconciliation_id
    assert (
        metadata.positions_cycle_id
        == metadata.reconciliation_id
    )
    assert (
        metadata.orders_cycle_id
        == metadata.reconciliation_id
    )
    assert metadata.positions_complete is True
    assert metadata.orders_complete is True
    assert (
        metadata.started_at
        <= metadata.positions_read_at
        <= metadata.orders_read_at
        <= metadata.completed_at
    )
    assert metadata.account_fingerprint == (
        account_fingerprint("TEST12345678")
    )
    assert metadata.account_fingerprint != (
        snapshot.account_state.account.account_number
    )
    assert {
        tool_name
        for tool_name, _ in client.fake_session.calls
    } == {
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
    }
