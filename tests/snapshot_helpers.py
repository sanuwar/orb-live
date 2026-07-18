from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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


def make_broker_snapshot(
    account_state,
    *,
    reconciliation_id: str | None = None,
    started_at: datetime | None = None,
    positions_read_at: datetime | None = None,
    orders_read_at: datetime | None = None,
    completed_at: datetime | None = None,
    positions_cycle_id: str | None = None,
    orders_cycle_id: str | None = None,
    positions_complete: bool = True,
    orders_complete: bool = True,
    account_fingerprint_value: str | None = None,
) -> BrokerSnapshot:
    cycle_id = reconciliation_id or str(uuid.uuid4())
    start = started_at or (
        datetime.now(timezone.utc)
        - timedelta(milliseconds=100)
    )
    positions_at = positions_read_at or (
        start + timedelta(milliseconds=1)
    )
    orders_at = orders_read_at or (
        positions_at + timedelta(milliseconds=1)
    )
    complete = completed_at or (
        orders_at + timedelta(milliseconds=1)
    )

    return BrokerSnapshot(
        account_state=account_state,
        metadata=BrokerSnapshotMetadata(
            reconciliation_id=cycle_id,
            account_fingerprint=(
                account_fingerprint_value
                or account_fingerprint(
                    account_state.account.account_number
                )
            ),
            started_at=start,
            positions_read_at=positions_at,
            orders_read_at=orders_at,
            completed_at=complete,
            positions_cycle_id=(
                positions_cycle_id or cycle_id
            ),
            orders_cycle_id=(
                orders_cycle_id or cycle_id
            ),
            positions_complete=positions_complete,
            orders_complete=orders_complete,
        ),
    )

def apply_broker_snapshot(
    store,
    *,
    strategy: str,
    account_state,
    reconciliation_id: str | None = None,
    **metadata_changes,
):
    snapshot = make_broker_snapshot(
        account_state,
        reconciliation_id=reconciliation_id,
        **metadata_changes,
    )
    evaluated_at = (
        snapshot.metadata.completed_at
        + timedelta(milliseconds=1)
    )
    issues = store.apply_startup_reconciliation(
        strategy=strategy,
        snapshot=snapshot,
        occurred_at=evaluated_at,
    )
    return snapshot, issues

def protected_account_state(
    trade,
    *,
    account_number: str = "TEST12345678",
) -> AccountState:
    quantity = Decimal(trade.filled_quantity)

    return AccountState(
        account=AccountInfo(
            account_number=account_number,
            masked_account_number="test-account",
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
        positions=(
            EquityPosition(
                symbol=trade.symbol,
                quantity=quantity,
                intraday_quantity=quantity,
                average_buy_price=(
                    trade.average_entry_price
                ),
                shares_available_for_sells=quantity,
                shares_held_for_sells=Decimal("0"),
                position_type="long",
            ),
        ),
        open_orders=(
            EquityOrder(
                order_id=(
                    trade.protective_stop_order_id
                    or ""
                ),
                symbol=trade.symbol,
                side="sell",
                order_type="stop_market",
                state="confirmed",
                quantity=quantity,
                cumulative_quantity=Decimal("0"),
                limit_price=None,
                stop_price=trade.initial_stop,
                trigger="stop",
                placed_agent="agentic",
            ),
        ),
    )


def confirm_linked_stop(
    store,
    trade,
    *,
    account_number: str = "TEST12345678",
):
    apply_broker_snapshot(
        store,
        strategy=trade.strategy,
        account_state=protected_account_state(
            trade,
            account_number=account_number,
        ),
    )
    return store.get_trade(trade.trade_id)
