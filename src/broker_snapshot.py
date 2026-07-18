from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any


DEFAULT_MAXIMUM_SNAPSHOT_AGE = timedelta(seconds=30)


@dataclass(frozen=True)
class BrokerSnapshotMetadata:
    reconciliation_id: str
    account_fingerprint: str
    started_at: datetime
    positions_read_at: datetime
    orders_read_at: datetime
    completed_at: datetime
    positions_cycle_id: str
    orders_cycle_id: str
    positions_complete: bool
    orders_complete: bool


@dataclass(frozen=True)
class BrokerSnapshot:
    account_state: Any
    metadata: BrokerSnapshotMetadata


@dataclass(frozen=True)
class SnapshotValidation:
    valid: bool
    result_code: str
    failure_reason: str | None
    snapshot_age_seconds: Decimal | None
    maximum_age_seconds: Decimal
    snapshot_digest: str


def account_fingerprint(account_number: str) -> str:
    normalized = account_number.strip()

    if not normalized:
        raise ValueError(
            "Account number is required to create a fingerprint."
        )

    return hashlib.sha256(
        (
            "orb-live:broker-account:v1:"
            f"{normalized}"
        ).encode("utf-8")
    ).hexdigest()


def _decimal_text(value: Any) -> str | None:
    if value is None:
        return None

    return format(Decimal(str(value)), "f")


def snapshot_digest(snapshot: BrokerSnapshot) -> str:
    state = snapshot.account_state
    metadata = snapshot.metadata
    payload = {
        "account_fingerprint": metadata.account_fingerprint,
        "completed_at": metadata.completed_at.isoformat(),
        "orders": [
            {
                "cumulative_quantity": _decimal_text(
                    order.cumulative_quantity
                ),
                "order_id": order.order_id,
                "order_type": order.order_type,
                "placed_agent": order.placed_agent,
                "quantity": _decimal_text(order.quantity),
                "side": order.side,
                "state": order.state,
                "symbol": order.symbol,
                "trigger": order.trigger,
            }
            for order in sorted(
                state.open_orders,
                key=lambda item: item.order_id,
            )
        ],
        "orders_complete": metadata.orders_complete,
        "orders_cycle_id": metadata.orders_cycle_id,
        "orders_read_at": metadata.orders_read_at.isoformat(),
        "positions": [
            {
                "quantity": _decimal_text(position.quantity),
                "symbol": position.symbol,
            }
            for position in sorted(
                state.positions,
                key=lambda item: item.symbol,
            )
        ],
        "positions_complete": metadata.positions_complete,
        "positions_cycle_id": metadata.positions_cycle_id,
        "positions_read_at": metadata.positions_read_at.isoformat(),
        "reconciliation_id": metadata.reconciliation_id,
        "started_at": metadata.started_at.isoformat(),
    }

    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validate_snapshot(
    snapshot: BrokerSnapshot,
    *,
    evaluated_at: datetime,
    maximum_age: timedelta = DEFAULT_MAXIMUM_SNAPSHOT_AGE,
) -> SnapshotValidation:
    metadata = snapshot.metadata
    maximum_age_seconds = Decimal(
        str(maximum_age.total_seconds())
    )
    digest = snapshot_digest(snapshot)

    def invalid(
        code: str,
        reason: str,
        *,
        age: Decimal | None = None,
    ) -> SnapshotValidation:
        return SnapshotValidation(
            valid=False,
            result_code=code,
            failure_reason=reason,
            snapshot_age_seconds=age,
            maximum_age_seconds=maximum_age_seconds,
            snapshot_digest=digest,
        )

    if maximum_age <= timedelta(0):
        return invalid(
            "invalid_maximum_age",
            "Maximum broker snapshot age must be positive.",
        )

    if not metadata.reconciliation_id.strip():
        return invalid(
            "missing_reconciliation_id",
            "Broker snapshot reconciliation ID is required.",
        )

    if not metadata.account_fingerprint.strip():
        return invalid(
            "missing_account_fingerprint",
            "Broker snapshot account fingerprint is required.",
        )

    timestamps = (
        metadata.started_at,
        metadata.positions_read_at,
        metadata.orders_read_at,
        metadata.completed_at,
        evaluated_at,
    )

    if any(value.tzinfo is None for value in timestamps):
        return invalid(
            "naive_snapshot_timestamp",
            "Every broker snapshot timestamp must include a timezone.",
        )

    if not (
        metadata.started_at
        <= metadata.positions_read_at
        <= metadata.orders_read_at
        <= metadata.completed_at
        <= evaluated_at
    ):
        return invalid(
            "inconsistent_snapshot_timestamps",
            "Broker snapshot timestamps are not ordered within one read cycle.",
        )

    if (
        metadata.positions_cycle_id
        != metadata.reconciliation_id
        or metadata.orders_cycle_id
        != metadata.reconciliation_id
    ):
        return invalid(
            "mixed_snapshot_cycles",
            "Positions and orders were not read in the same reconciliation cycle.",
        )

    if not metadata.positions_complete:
        return invalid(
            "positions_incomplete",
            "Broker position pages are incomplete.",
        )

    if not metadata.orders_complete:
        return invalid(
            "orders_incomplete",
            "Broker order pages are incomplete.",
        )

    try:
        expected_fingerprint = account_fingerprint(
            snapshot.account_state.account.account_number
        )
    except (AttributeError, ValueError):
        return invalid(
            "invalid_snapshot_account",
            "Broker snapshot account identity is unavailable.",
        )

    if metadata.account_fingerprint != expected_fingerprint:
        return invalid(
            "account_fingerprint_mismatch",
            "Broker snapshot account identity does not match its evidence.",
        )

    age = Decimal(
        str(
            (
                evaluated_at - metadata.completed_at
            ).total_seconds()
        )
    )

    if age > maximum_age_seconds:
        return invalid(
            "stale_snapshot",
            "Broker snapshot is older than the permitted maximum age.",
            age=age,
        )

    return SnapshotValidation(
        valid=True,
        result_code="valid_snapshot",
        failure_reason=None,
        snapshot_age_seconds=age,
        maximum_age_seconds=maximum_age_seconds,
        snapshot_digest=digest,
    )
