from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.broker_identity import CommissionedAccount
from src.broker_snapshot import (
    BrokerSnapshot,
    DEFAULT_MAXIMUM_SNAPSHOT_AGE,
    snapshot_digest,
    validate_snapshot,
)
from src.order_observer import mask_identifier
from src.trade_state import ACTIVE_STATUSES, TradeRecord, TradeStatus


DEFAULT_VISIBILITY_GRACE = timedelta(seconds=90)

_CONFIRMED_STOP_STATES = frozenset(
    {"confirmed", "queued", "unconfirmed", "partially_filled"}
)

_TERMINAL_ORDER_STATES = frozenset(
    {"filled", "cancelled", "canceled", "rejected", "failed"}
)


class LiveReconciliationError(RuntimeError):
    """Raised when live reconciliation cannot proceed safely."""


class DivergenceCategory(str, Enum):
    CONFIRMED = "confirmed"
    BROKER_AHEAD = "broker_ahead"
    INTERNAL_AHEAD = "internal_ahead"
    PARTIAL_FILL = "partial_fill"
    PROTECTION_MISSING = "protection_missing"
    PROTECTION_UNKNOWN = "protection_unknown"
    DELAYED_VISIBILITY = "delayed_visibility"
    CONFLICTING = "conflicting"
    MISSING_EVIDENCE = "missing_evidence"


ESCALATING_CATEGORIES = frozenset(
    {
        DivergenceCategory.BROKER_AHEAD,
        DivergenceCategory.INTERNAL_AHEAD,
        DivergenceCategory.PARTIAL_FILL,
        DivergenceCategory.PROTECTION_MISSING,
        DivergenceCategory.PROTECTION_UNKNOWN,
        DivergenceCategory.CONFLICTING,
        DivergenceCategory.MISSING_EVIDENCE,
    }
)

HALTING_CATEGORIES = frozenset(
    {
        DivergenceCategory.PROTECTION_MISSING,
        DivergenceCategory.PROTECTION_UNKNOWN,
        DivergenceCategory.CONFLICTING,
        DivergenceCategory.MISSING_EVIDENCE,
    }
)

INSUFFICIENT_EVIDENCE_CATEGORIES = frozenset(
    {DivergenceCategory.DELAYED_VISIBILITY}
)


@dataclass(frozen=True)
class SnapshotRejection:
    result_code: str
    reason: str


@dataclass(frozen=True)
class ReconciliationFinding:
    """
    One classified divergence between durable state and the broker.

    Carries fingerprints and masked identifiers only. A full account
    number never reaches this record.
    """

    trade_id: str
    strategy: str
    symbol: str
    category: DivergenceCategory
    reason: str
    reconciliation_id: str
    snapshot_digest: str
    account_fingerprint: str
    observed_at: datetime
    masked_order_id: str | None = None
    internal_quantity: Decimal | None = None
    observed_quantity: Decimal | None = None

    @property
    def escalates(self) -> bool:
        return self.category in ESCALATING_CATEGORIES

    @property
    def halts(self) -> bool:
        return self.category in HALTING_CATEGORIES

    @property
    def is_insufficient_evidence(self) -> bool:
        return (
            self.category
            in INSUFFICIENT_EVIDENCE_CATEGORIES
        )


@dataclass(frozen=True)
class ReconciliationOutcome:
    accepted: bool
    reconciliation_id: str
    snapshot_digest: str
    result_code: str
    rejection: SnapshotRejection | None
    findings: tuple[ReconciliationFinding, ...]

    def by_category(
        self,
        category: DivergenceCategory,
    ) -> tuple[ReconciliationFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.category == category
        )


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _is_protective_stop(order: Any) -> bool:
    order_type = str(
        getattr(order, "order_type", "") or ""
    ).lower()
    trigger = str(
        getattr(order, "trigger", "") or ""
    ).lower()
    side = str(getattr(order, "side", "") or "").lower()

    return (
        side == "sell"
        and (
            "stop" in order_type
            or trigger == "stop"
        )
    )


def _is_open_state(order: Any) -> bool:
    state = str(
        getattr(order, "state", "") or ""
    ).lower()
    return state not in _TERMINAL_ORDER_STATES


def _expected_position(trade: TradeRecord) -> Decimal:
    """
    The position durable state expects the broker to hold.

    Uses the same precedence as TradeRecord.unprotected_quantity, so a
    partially exited trade is not mistaken for a divergence.
    """

    if trade.exit_remaining_position_quantity is not None:
        return Decimal(
            trade.exit_remaining_position_quantity
        )

    if trade.remaining_broker_position_quantity is not None:
        return Decimal(
            trade.remaining_broker_position_quantity
        )

    return Decimal(trade.filled_quantity)


def _within_grace(
    trade: TradeRecord,
    *,
    evaluated_at: datetime,
    grace: timedelta,
) -> bool:
    reference = (
        trade.last_broker_observation_at
        or trade.updated_at
    )

    if reference is None:
        return False

    if reference.tzinfo is None:
        return False

    return (evaluated_at - reference) <= grace


class LiveReconciler:
    """
    Classifies durable trade state against a read-only broker snapshot.

    Pure and deterministic given its inputs. This class never decides a
    resolution, never closes a trade, never writes P&L, and never
    unblocks a saga dependency. It emits evidence for the existing
    operator-resolution workflow to act on.
    """

    def __init__(
        self,
        commissioned_account: CommissionedAccount,
        *,
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
        visibility_grace: timedelta = (
            DEFAULT_VISIBILITY_GRACE
        ),
    ):
        if not isinstance(
            commissioned_account, CommissionedAccount
        ):
            raise LiveReconciliationError(
                "A CommissionedAccount is required."
            )

        if maximum_snapshot_age <= timedelta(0):
            raise LiveReconciliationError(
                "The maximum snapshot age must be positive."
            )

        if visibility_grace < timedelta(0):
            raise LiveReconciliationError(
                "The visibility grace window must not be negative."
            )

        self.commissioned_account = commissioned_account
        self.maximum_snapshot_age = maximum_snapshot_age
        self.visibility_grace = visibility_grace

    def reconcile(
        self,
        *,
        snapshot: BrokerSnapshot,
        trades: Iterable[TradeRecord],
        evaluated_at: datetime,
    ) -> ReconciliationOutcome:
        digest = snapshot_digest(snapshot)
        metadata = snapshot.metadata

        validation = validate_snapshot(
            snapshot,
            evaluated_at=evaluated_at,
            maximum_age=self.maximum_snapshot_age,
        )

        if not validation.valid:
            return ReconciliationOutcome(
                accepted=False,
                reconciliation_id=metadata.reconciliation_id,
                snapshot_digest=digest,
                result_code=validation.result_code,
                rejection=SnapshotRejection(
                    result_code=validation.result_code,
                    reason=(
                        validation.failure_reason
                        or "The broker snapshot is invalid."
                    ),
                ),
                findings=(),
            )

        if not self.commissioned_account.matches(
            metadata.account_fingerprint
        ):
            return ReconciliationOutcome(
                accepted=False,
                reconciliation_id=metadata.reconciliation_id,
                snapshot_digest=digest,
                result_code="commissioned_account_mismatch",
                rejection=SnapshotRejection(
                    result_code="commissioned_account_mismatch",
                    reason=(
                        "The observed broker account does not match "
                        "the commissioned account. Failing closed "
                        "without adopting the observed account."
                    ),
                ),
                findings=(),
            )

        findings: list[ReconciliationFinding] = []

        for trade in sorted(
            trades,
            key=lambda item: item.trade_id,
        ):
            findings.extend(
                self._classify_trade(
                    trade,
                    snapshot=snapshot,
                    digest=digest,
                    evaluated_at=evaluated_at,
                )
            )

        return ReconciliationOutcome(
            accepted=True,
            reconciliation_id=metadata.reconciliation_id,
            snapshot_digest=digest,
            result_code="reconciled",
            rejection=None,
            findings=tuple(findings),
        )

    def _finding(
        self,
        trade: TradeRecord,
        *,
        category: DivergenceCategory,
        reason: str,
        snapshot: BrokerSnapshot,
        digest: str,
        evaluated_at: datetime,
        order_id: str | None = None,
        internal_quantity: Decimal | None = None,
        observed_quantity: Decimal | None = None,
    ) -> ReconciliationFinding:
        return ReconciliationFinding(
            trade_id=trade.trade_id,
            strategy=trade.strategy,
            symbol=trade.symbol,
            category=category,
            reason=reason,
            reconciliation_id=(
                snapshot.metadata.reconciliation_id
            ),
            snapshot_digest=digest,
            account_fingerprint=(
                snapshot.metadata.account_fingerprint
            ),
            observed_at=evaluated_at,
            masked_order_id=(
                mask_identifier(order_id)
                if order_id
                else None
            ),
            internal_quantity=internal_quantity,
            observed_quantity=observed_quantity,
        )

    def _classify_trade(
        self,
        trade: TradeRecord,
        *,
        snapshot: BrokerSnapshot,
        digest: str,
        evaluated_at: datetime,
    ) -> list[ReconciliationFinding]:
        state = snapshot.account_state
        symbol = trade.symbol

        positions = [
            position
            for position in state.positions
            if position.symbol == symbol
        ]
        observed_position = sum(
            (
                _decimal(position.quantity)
                for position in positions
            ),
            Decimal("0"),
        )

        symbol_orders = [
            order
            for order in state.open_orders
            if order.symbol == symbol
        ]
        open_orders = [
            order
            for order in symbol_orders
            if _is_open_state(order)
        ]
        protective_stops = [
            order
            for order in open_orders
            if _is_protective_stop(order)
        ]

        internal_position = _expected_position(trade)
        in_grace = _within_grace(
            trade,
            evaluated_at=evaluated_at,
            grace=self.visibility_grace,
        )

        findings: list[ReconciliationFinding] = []

        def add(
            category: DivergenceCategory,
            reason: str,
            **extra: Any,
        ) -> None:
            findings.append(
                self._finding(
                    trade,
                    category=category,
                    reason=reason,
                    snapshot=snapshot,
                    digest=digest,
                    evaluated_at=evaluated_at,
                    **extra,
                )
            )

        if trade.status not in ACTIVE_STATUSES:
            if observed_position > 0:
                add(
                    DivergenceCategory.CONFLICTING,
                    "The broker reports an open position for a "
                    "trade that is no longer active.",
                    internal_quantity=internal_position,
                    observed_quantity=observed_position,
                )
            return findings

        if len(protective_stops) > 1:
            add(
                DivergenceCategory.CONFLICTING,
                "The broker reports more than one protective stop "
                "for this symbol.",
                observed_quantity=Decimal(
                    len(protective_stops)
                ),
            )
            return findings

        if (
            observed_position == 0
            and protective_stops
            and _decimal(
                protective_stops[0].quantity
            )
            > 0
        ):
            add(
                DivergenceCategory.CONFLICTING,
                "The broker reports a protective stop with no "
                "underlying position.",
                order_id=protective_stops[0].order_id,
                observed_quantity=_decimal(
                    protective_stops[0].quantity
                ),
            )
            return findings

        entry_order = next(
            (
                order
                for order in symbol_orders
                if trade.entry_order_id
                and order.order_id == trade.entry_order_id
            ),
            None,
        )

        if trade.entry_order_id and entry_order is None:
            if in_grace:
                add(
                    DivergenceCategory.DELAYED_VISIBILITY,
                    "The entry order is not yet visible at the "
                    "broker and remains within the visibility "
                    "grace window. Evidence is insufficient; "
                    "retry later.",
                    order_id=trade.entry_order_id,
                )
                return findings

            add(
                DivergenceCategory.INTERNAL_AHEAD,
                "Durable state expects an entry order the broker "
                "does not report after the visibility grace "
                "window.",
                order_id=trade.entry_order_id,
                internal_quantity=Decimal(trade.quantity),
                observed_quantity=Decimal("0"),
            )
            return findings

        if entry_order is not None:
            observed_fill = _decimal(
                entry_order.cumulative_quantity
            )
            requested = _decimal(entry_order.quantity)

            if (
                Decimal("0")
                < observed_fill
                < requested
            ):
                add(
                    DivergenceCategory.PARTIAL_FILL,
                    "The broker reports a partially filled entry "
                    "order.",
                    order_id=entry_order.order_id,
                    internal_quantity=internal_position,
                    observed_quantity=observed_fill,
                )

        if observed_position > internal_position:
            add(
                DivergenceCategory.BROKER_AHEAD,
                "The broker reports a larger position than "
                "durable state records.",
                internal_quantity=internal_position,
                observed_quantity=observed_position,
            )
        elif (
            internal_position > 0
            and observed_position < internal_position
            and not in_grace
        ):
            add(
                DivergenceCategory.INTERNAL_AHEAD,
                "Durable state records a larger position than the "
                "broker reports.",
                internal_quantity=internal_position,
                observed_quantity=observed_position,
            )

        findings.extend(
            self._classify_protection(
                trade,
                protective_stops=protective_stops,
                observed_position=observed_position,
                in_grace=in_grace,
                snapshot=snapshot,
                digest=digest,
                evaluated_at=evaluated_at,
            )
        )

        if not findings:
            add(
                DivergenceCategory.CONFIRMED,
                "Durable state matches the broker observation.",
                internal_quantity=internal_position,
                observed_quantity=observed_position,
            )

        return findings

    def _classify_protection(
        self,
        trade: TradeRecord,
        *,
        protective_stops: Sequence[Any],
        observed_position: Decimal,
        in_grace: bool,
        snapshot: BrokerSnapshot,
        digest: str,
        evaluated_at: datetime,
    ) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []

        def add(
            category: DivergenceCategory,
            reason: str,
            **extra: Any,
        ) -> None:
            findings.append(
                self._finding(
                    trade,
                    category=category,
                    reason=reason,
                    snapshot=snapshot,
                    digest=digest,
                    evaluated_at=evaluated_at,
                    **extra,
                )
            )

        protection_expected = (
            _expected_position(trade) > 0
            and observed_position > 0
            and trade.status
            not in {
                TradeStatus.CLOSED,
                TradeStatus.CANCELLED,
            }
        )

        if not protection_expected:
            return findings

        if not trade.protective_stop_order_id:
            if in_grace:
                add(
                    DivergenceCategory.DELAYED_VISIBILITY,
                    "A protective stop is expected but not yet "
                    "recorded, within the visibility grace "
                    "window. Evidence is insufficient; retry "
                    "later.",
                )
                return findings

            add(
                DivergenceCategory.MISSING_EVIDENCE,
                "A protective stop is expected but durable state "
                "holds no protective stop order identifier, so "
                "protection cannot be verified.",
                internal_quantity=Decimal(
                    trade.filled_quantity
                ),
            )
            return findings

        matching = next(
            (
                order
                for order in protective_stops
                if order.order_id
                == trade.protective_stop_order_id
            ),
            None,
        )

        if matching is None:
            if in_grace:
                add(
                    DivergenceCategory.DELAYED_VISIBILITY,
                    "The protective stop is not yet visible at "
                    "the broker and remains within the "
                    "visibility grace window. Evidence is "
                    "insufficient; retry later.",
                    order_id=trade.protective_stop_order_id,
                )
                return findings

            add(
                DivergenceCategory.PROTECTION_MISSING,
                "The expected protective stop is absent at the "
                "broker after the visibility grace window.",
                order_id=trade.protective_stop_order_id,
                internal_quantity=observed_position,
                observed_quantity=Decimal("0"),
            )
            return findings

        state = str(
            getattr(matching, "state", "") or ""
        ).lower()

        if state not in _CONFIRMED_STOP_STATES:
            add(
                DivergenceCategory.PROTECTION_UNKNOWN,
                "The protective stop is in an indeterminate "
                f"state ({state or 'unknown'}); protection "
                "cannot be confirmed.",
                order_id=matching.order_id,
            )
            return findings

        stop_quantity = _decimal(matching.quantity)

        if stop_quantity < observed_position:
            add(
                DivergenceCategory.PROTECTION_UNKNOWN,
                "The protective stop covers less than the "
                "observed position.",
                order_id=matching.order_id,
                internal_quantity=observed_position,
                observed_quantity=stop_quantity,
            )

        return findings


class LiveReconciliationStore:
    """
    Durable, idempotent store for live reconciliation evidence.

    Requires an explicitly supplied database path. Writes only to its
    own evidence table: it never mutates trade state, saga actions,
    routing, or P&L.
    """

    def __init__(self, db_path: Path | str):
        if db_path is None:
            raise LiveReconciliationError(
                "An explicit database path is required."
            )

        self.db_path = Path(db_path)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")

        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS
                live_reconciliation_evidence (
                    reconciliation_id TEXT NOT NULL,
                    trade_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    account_fingerprint TEXT NOT NULL,
                    masked_order_id TEXT,
                    internal_quantity TEXT,
                    observed_quantity TEXT,
                    escalated INTEGER NOT NULL,
                    halting INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (
                        reconciliation_id,
                        trade_id,
                        category
                    )
                )
                """
            )

    def record(
        self,
        findings: Iterable[ReconciliationFinding],
    ) -> int:
        """
        Persists findings idempotently.

        Re-recording the same reconciliation produces no duplicate
        rows. Returns the number of newly inserted rows.
        """

        inserted = 0

        with self._connect() as connection:
            for finding in findings:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO
                    live_reconciliation_evidence (
                        reconciliation_id,
                        trade_id,
                        category,
                        strategy,
                        symbol,
                        reason,
                        snapshot_digest,
                        account_fingerprint,
                        masked_order_id,
                        internal_quantity,
                        observed_quantity,
                        escalated,
                        halting,
                        observed_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        finding.reconciliation_id,
                        finding.trade_id,
                        finding.category.value,
                        finding.strategy,
                        finding.symbol,
                        finding.reason,
                        finding.snapshot_digest,
                        finding.account_fingerprint,
                        finding.masked_order_id,
                        (
                            format(
                                finding.internal_quantity,
                                "f",
                            )
                            if finding.internal_quantity
                            is not None
                            else None
                        ),
                        (
                            format(
                                finding.observed_quantity,
                                "f",
                            )
                            if finding.observed_quantity
                            is not None
                            else None
                        ),
                        1 if finding.escalates else 0,
                        1 if finding.halts else 0,
                        finding.observed_at.isoformat(),
                    ),
                )
                inserted += cursor.rowcount

        return inserted

    def count_evidence(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM "
                "live_reconciliation_evidence"
            ).fetchone()

        return int(row["total"])

    def list_evidence(
        self,
        *,
        trade_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT * FROM live_reconciliation_evidence"
        )
        parameters: tuple[Any, ...] = ()

        if trade_id is not None:
            query += " WHERE trade_id = ?"
            parameters = (trade_id,)

        query += (
            " ORDER BY reconciliation_id, trade_id, category"
        )

        with self._connect() as connection:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()

        return [dict(row) for row in rows]
