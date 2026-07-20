from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

try:
    from .broker_snapshot import (
        BrokerSnapshot,
        BrokerSnapshotMetadata,
        account_fingerprint,
    )
    from .read_only_broker_gateway import (
        ReadOnlyBrokerGateway,
    )
    from .robinhood_client import (
        RobinhoodClient,
        RobinhoodClientError,
        _unwrap_tool_result,
    )
except ImportError:
    from read_only_broker_gateway import (
        ReadOnlyBrokerGateway,
    )
    from broker_snapshot import (
        BrokerSnapshot,
        BrokerSnapshotMetadata,
        account_fingerprint,
    )
    from robinhood_client import (
        RobinhoodClient,
        RobinhoodClientError,
        _unwrap_tool_result,
    )


OPEN_ORDER_STATES = {
    "new",
    "queued",
    "confirmed",
    "unconfirmed",
    "partially_filled",
    "pending_cancelled",
    "locating",
}


def mask_account_number(account_number: str) -> str:
    cleaned = account_number.strip()

    if len(cleaned) <= 4:
        return "••••"

    return f"••••{cleaned[-4:]}"


def decimal_value(
    value: Any,
    *,
    default: str = "0",
) -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    return Decimal(str(value))


@dataclass(frozen=True)
class AccountInfo:
    account_number: str
    masked_account_number: str
    brokerage_account_type: str
    trading_type: str
    nickname: str | None
    agentic_allowed: bool
    state: str


@dataclass(frozen=True)
class PortfolioState:
    total_value: Decimal
    equity_value: Decimal
    cash: Decimal
    pending_deposits: Decimal
    buying_power: Decimal
    unleveraged_buying_power: Decimal
    intraday_buying_power: Decimal | None
    currency: str


@dataclass(frozen=True)
class EquityPosition:
    symbol: str
    quantity: Decimal
    intraday_quantity: Decimal
    average_buy_price: Decimal | None
    shares_available_for_sells: Decimal
    shares_held_for_sells: Decimal
    position_type: str


@dataclass(frozen=True)
class EquityOrder:
    order_id: str
    symbol: str
    side: str
    order_type: str
    state: str
    quantity: Decimal | None
    cumulative_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    trigger: str
    placed_agent: str


@dataclass(frozen=True)
class AccountState:
    account: AccountInfo
    portfolio: PortfolioState
    positions: tuple[EquityPosition, ...]
    open_orders: tuple[EquityOrder, ...]

    @property
    def open_position_count(self) -> int:
        return sum(
            1
            for position in self.positions
            if position.quantity != 0
        )

    @property
    def open_entry_order_count(self) -> int:
        return sum(
            1
            for order in self.open_orders
            if order.side == "buy"
            and order.trigger != "stop"
        )

    @property
    def protective_stop_order_count(self) -> int:
        return sum(
            1
            for order in self.open_orders
            if order.side == "sell"
            and order.trigger == "stop"
        )


def select_agentic_account(
    accounts: list[dict[str, Any]],
    *,
    requested_account_number: str | None = None,
) -> dict[str, Any]:
    eligible = [
        account
        for account in accounts
        if (
            account
            and account.get("agentic_allowed") is True
            and account.get("state") == "active"
            and account.get("deactivated") is not True
            and account.get("permanently_deactivated") is not True
        )
    ]

    if requested_account_number:
        matching = next(
            (
                account
                for account in eligible
                if account.get("account_number")
                == requested_account_number
            ),
            None,
        )

        if matching is None:
            raise RobinhoodClientError(
                "The requested account is not an active "
                "agentic-accessible account."
            )

        return matching

    if not eligible:
        raise RobinhoodClientError(
            "No active agentic-accessible account was found."
        )

    if len(eligible) > 1:
        masked = ", ".join(
            mask_account_number(
                account["account_number"]
            )
            for account in eligible
        )

        raise RobinhoodClientError(
            "More than one agentic-accessible account was found: "
            f"{masked}. An account must be selected explicitly."
        )

    return eligible[0]


def parse_position(
    raw: dict[str, Any],
) -> EquityPosition:
    average_raw = raw.get("average_buy_price")

    return EquityPosition(
        symbol=str(raw.get("symbol", "")).upper(),
        quantity=decimal_value(
            raw.get("quantity")
        ),
        intraday_quantity=decimal_value(
            raw.get("intraday_quantity")
        ),
        average_buy_price=(
            decimal_value(average_raw)
            if average_raw not in {None, ""}
            else None
        ),
        shares_available_for_sells=decimal_value(
            raw.get("shares_available_for_sells")
        ),
        shares_held_for_sells=decimal_value(
            raw.get("shares_held_for_sells")
        ),
        position_type=str(
            raw.get("type", "")
        ),
    )


def parse_order(
    raw: dict[str, Any],
) -> EquityOrder:
    quantity_raw = raw.get("quantity")
    limit_raw = raw.get("price")
    stop_raw = raw.get("stop_price")

    trigger = str(
        raw.get("trigger", "immediate")
    )

    base_type = str(
        raw.get("type", "")
    )

    displayed_type = (
        f"stop_{base_type}"
        if trigger == "stop"
        else base_type
    )

    return EquityOrder(
        order_id=str(raw.get("id", "")),
        symbol=str(
            raw.get("symbol", "")
        ).upper(),
        side=str(raw.get("side", "")),
        order_type=displayed_type,
        state=str(raw.get("state", "")),
        quantity=(
            decimal_value(quantity_raw)
            if quantity_raw not in {None, ""}
            else None
        ),
        cumulative_quantity=decimal_value(
            raw.get("cumulative_quantity")
        ),
        limit_price=(
            decimal_value(limit_raw)
            if limit_raw not in {None, ""}
            else None
        ),
        stop_price=(
            decimal_value(stop_raw)
            if stop_raw not in {None, ""}
            else None
        ),
        trigger=trigger,
        placed_agent=str(
            raw.get("placed_agent", "")
        ),
    )


def reject_unread_pages(
    data: dict[str, Any],
    *,
    tool_name: str,
) -> None:
    if data.get("next"):
        raise RobinhoodClientError(
            f"{tool_name} returned another page. "
            "Failing closed rather than using incomplete account state."
        )


class AccountStateReader:
    """
    Retrieves a consistent, read-only account snapshot.

    No write-capable Robinhood tools are called.
    """

    def __init__(
        self,
        client: RobinhoodClient | None = None,
        *,
        gateway: ReadOnlyBrokerGateway | None = None,
    ):
        """
        Reads account state through the read-only gateway.

        Every broker read passes the allowlist, so a non-allowlisted
        tool is rejected before the underlying session is opened. A
        gateway may be injected for testing; by default one is built
        around the supplied or default client.
        """

        self.client = client or RobinhoodClient()
        self.gateway = gateway or ReadOnlyBrokerGateway(
            self.client
        )

    async def read(
        self,
        *,
        account_number: str | None = None,
    ) -> AccountState:
        snapshot = await self.read_snapshot(
            account_number=account_number
        )
        return snapshot.account_state

    async def read_snapshot(
        self,
        *,
        account_number: str | None = None,
    ) -> BrokerSnapshot:
        reconciliation_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        async with self.gateway.session() as session:
            accounts_result = await session.call_tool(
                "get_accounts",
                {},
            )

            accounts_data = _unwrap_tool_result(
                accounts_result,
                "get_accounts",
            )

            account = select_agentic_account(
                accounts_data.get("accounts") or [],
                requested_account_number=account_number,
            )

            selected_account_number = account[
                "account_number"
            ]

            portfolio_result = await session.call_tool(
                "get_portfolio",
                {
                    "account_number":
                        selected_account_number,
                },
            )

            positions_result = await session.call_tool(
                "get_equity_positions",
                {
                    "account_number":
                        selected_account_number,
                },
            )
            positions_read_at = datetime.now(
                timezone.utc
            )

            orders_result = await session.call_tool(
                "get_equity_orders",
                {
                    "account_number":
                        selected_account_number,
                },
            )
            orders_read_at = datetime.now(timezone.utc)

        portfolio_data = _unwrap_tool_result(
            portfolio_result,
            "get_portfolio",
        )

        positions_data = _unwrap_tool_result(
            positions_result,
            "get_equity_positions",
        )

        orders_data = _unwrap_tool_result(
            orders_result,
            "get_equity_orders",
        )

        reject_unread_pages(
            positions_data,
            tool_name="get_equity_positions",
        )

        reject_unread_pages(
            orders_data,
            tool_name="get_equity_orders",
        )

        buying_power_data = (
            portfolio_data.get("buying_power")
            or {}
        )

        intraday_raw = buying_power_data.get(
            "intraday_buying_power"
        )

        parsed_positions = tuple(
            parse_position(raw)
            for raw in (
                positions_data.get("positions")
                or []
            )
            if raw
            and decimal_value(
                raw.get("quantity")
            ) != 0
        )

        parsed_orders = tuple(
            parsed_order
            for parsed_order in (
                parse_order(raw)
                for raw in (
                    orders_data.get("orders")
                    or []
                )
                if raw
            )
            if parsed_order.state
            in OPEN_ORDER_STATES
        )

        account_state = AccountState(
            account=AccountInfo(
                account_number=
                    selected_account_number,
                masked_account_number=
                    mask_account_number(
                        selected_account_number
                    ),
                brokerage_account_type=str(
                    account.get(
                        "brokerage_account_type",
                        "",
                    )
                ),
                trading_type=str(
                    account.get("type", "")
                ),
                nickname=account.get("nickname"),
                agentic_allowed=bool(
                    account.get("agentic_allowed")
                ),
                state=str(
                    account.get("state", "")
                ),
            ),
            portfolio=PortfolioState(
                total_value=decimal_value(
                    portfolio_data.get("total_value")
                ),
                equity_value=decimal_value(
                    portfolio_data.get("equity_value")
                ),
                cash=decimal_value(
                    portfolio_data.get("cash")
                ),
                pending_deposits=decimal_value(
                    portfolio_data.get(
                        "pending_deposits"
                    )
                ),
                buying_power=decimal_value(
                    buying_power_data.get(
                        "buying_power"
                    )
                ),
                unleveraged_buying_power=
                    decimal_value(
                        buying_power_data.get(
                            "unleveraged_buying_power"
                        )
                    ),
                intraday_buying_power=(
                    decimal_value(intraday_raw)
                    if intraday_raw
                    not in {None, ""}
                    else None
                ),
                currency=str(
                    buying_power_data.get(
                        "display_currency",
                        portfolio_data.get(
                            "currency",
                            "USD",
                        ),
                    )
                ),
            ),
            positions=parsed_positions,
            open_orders=parsed_orders,
        )

        completed_at = datetime.now(timezone.utc)

        return BrokerSnapshot(
            account_state=account_state,
            metadata=BrokerSnapshotMetadata(
                reconciliation_id=reconciliation_id,
                account_fingerprint=account_fingerprint(
                    selected_account_number
                ),
                started_at=started_at,
                positions_read_at=positions_read_at,
                orders_read_at=orders_read_at,
                completed_at=completed_at,
                positions_cycle_id=reconciliation_id,
                orders_cycle_id=reconciliation_id,
                positions_complete=True,
                orders_complete=True,
            ),
        )


def print_account_state(
    state: AccountState,
) -> None:
    print("\nROBINHOOD ACCOUNT STATE â€” READ ONLY\n")

    print(
        f"Account: "
        f"{state.account.masked_account_number}"
    )
    print(
        f"Account type: "
        f"{state.account.brokerage_account_type}"
    )
    print(
        f"Trading type: "
        f"{state.account.trading_type}"
    )
    print(
        f"State: {state.account.state}"
    )

    print(
        f"\nPortfolio value: "
        f"${state.portfolio.total_value}"
    )
    print(
        f"Cash reported: "
        f"${state.portfolio.cash}"
    )
    print(
        f"Pending deposits: "
        f"${state.portfolio.pending_deposits}"
    )
    print(
        f"Broker buying power: "
        f"${state.portfolio.buying_power}"
    )
    print(
        f"Unleveraged buying power: "
        f"${state.portfolio.unleveraged_buying_power}"
    )

    print(
        f"\nOpen positions: "
        f"{state.open_position_count}"
    )

    for position in state.positions:
        print(
            f"  {position.symbol}: "
            f"{position.quantity} shares, "
            f"available to sell="
            f"{position.shares_available_for_sells}"
        )

    print(
        f"\nOpen equity orders: "
        f"{len(state.open_orders)}"
    )
    print(
        f"Open entry orders: "
        f"{state.open_entry_order_count}"
    )
    print(
        f"Protective stop orders: "
        f"{state.protective_stop_order_count}"
    )

    print(
        "\nImportant: buying power is not proof that "
        "cash-sale proceeds are fully settled."
    )
    print("No write-capable tool was called.")


async def main() -> None:
    reader = AccountStateReader()
    state = await reader.read()
    print_account_state(state)


if __name__ == "__main__":
    asyncio.run(main())
