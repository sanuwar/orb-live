from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any

try:
    from .broker_action_plan import (
        BrokerActionKind,
        BrokerActionPlan,
        BrokerActionPlanner,
        BrokerPlanPurpose,
    )
    from .broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        validate_snapshot,
    )
    from .trade_state import (
        BrokerActionAuthorizationRecord,
        TradeStateError,
        TradeStateStore,
    )
except ImportError:
    from broker_action_plan import (
        BrokerActionKind,
        BrokerActionPlan,
        BrokerActionPlanner,
        BrokerPlanPurpose,
    )
    from broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        validate_snapshot,
    )
    from trade_state import (
        BrokerActionAuthorizationRecord,
        TradeStateError,
        TradeStateStore,
    )


DEFAULT_AUTHORIZATION_TTL = timedelta(seconds=15)


class BrokerActionAuthorizationError(TradeStateError):
    """Raised when a broker action cannot be authorized safely."""


@dataclass(frozen=True)
class BrokerActionAuthorizationResult:
    authorization: BrokerActionAuthorizationRecord
    created: bool
    plan_digest: str


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise BrokerActionAuthorizationError(
        "Broker action plan contains a non-canonical payload value."
    )


def canonical_plan_payload(plan: BrokerActionPlan) -> dict[str, Any]:
    return {
        "purpose": plan.purpose.value,
        "allowed": plan.allowed,
        "actions": [
            {
                "sequence": action.sequence,
                "kind": action.kind.value,
                "payload": _canonical_value(action.payload),
                "requires_broker_confirmation": (
                    action.requires_broker_confirmation
                ),
                "depends_on_sequence": action.depends_on_sequence,
                "requires_review_ref_id": (
                    action.requires_review_ref_id
                ),
            }
            for action in plan.actions
        ],
        "blockers": [
            {
                "code": blocker.code,
                "message": blocker.message,
            }
            for blocker in plan.blockers
        ],
    }


def broker_action_plan_digest(plan: BrokerActionPlan) -> str:
    canonical = json.dumps(
        canonical_plan_payload(plan),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _action_payload(
    plan: BrokerActionPlan,
    kind: BrokerActionKind,
) -> dict[str, Any] | None:
    matching = [
        action.payload
        for action in plan.actions
        if action.kind == kind
    ]
    if len(matching) != 1 or not isinstance(matching[0], dict):
        return None
    return dict(matching[0])


def _validate_action_structure(plan: BrokerActionPlan) -> None:
    if not plan.allowed or plan.blockers or not plan.actions:
        raise BrokerActionAuthorizationError(
            "Only an allowed, blocker-free, non-empty plan may be authorized."
        )

    sequences = tuple(action.sequence for action in plan.actions)
    expected = tuple(range(1, len(plan.actions) + 1))
    if sequences != expected:
        raise BrokerActionAuthorizationError(
            "Broker action sequences must be contiguous and ordered."
        )

    for action in plan.actions:
        if (
            action.depends_on_sequence is not None
            and (
                action.depends_on_sequence not in sequences
                or action.depends_on_sequence >= action.sequence
            )
        ):
            raise BrokerActionAuthorizationError(
                "Broker action dependencies must reference an earlier step."
            )
        if not action.requires_broker_confirmation:
            raise BrokerActionAuthorizationError(
                "Every authorized broker action must require confirmation."
            )


def _validate_payload_accounts(
    plan: BrokerActionPlan,
    expected_account_fingerprint: str,
) -> None:
    try:
        from .broker_snapshot import account_fingerprint
    except ImportError:
        from broker_snapshot import account_fingerprint

    for action in plan.actions:
        payload = action.payload or {}
        if "account_number" not in payload:
            continue
        raw_account = str(payload["account_number"])
        if account_fingerprint(raw_account) != expected_account_fingerprint:
            raise BrokerActionAuthorizationError(
                "Broker action payload account does not match the expected account fingerprint."
            )


def _rebuild_plan(
    plan: BrokerActionPlan,
    *,
    trade: Any,
    open_orders: Any,
) -> BrokerActionPlan:
    if plan.purpose == BrokerPlanPurpose.PROTECT_FILLED_ENTRY:
        payload = _action_payload(
            plan,
            BrokerActionKind.REVIEW_PROTECTIVE_STOP,
        )
        return BrokerActionPlanner.plan_initial_protection(
            trade=trade,
            stop_payload=payload,
            open_orders=open_orders,
        )

    if plan.purpose == BrokerPlanPurpose.REPLACE_PROTECTIVE_STOP:
        payload = _action_payload(
            plan,
            BrokerActionKind.REVIEW_PROTECTIVE_STOP,
        )
        return BrokerActionPlanner.plan_stop_replacement(
            trade=trade,
            replacement_stop_payload=payload,
            open_orders=open_orders,
        )

    if plan.purpose == BrokerPlanPurpose.EXIT_POSITION:
        payload = _action_payload(
            plan,
            BrokerActionKind.REVIEW_EXIT,
        )
        return BrokerActionPlanner.plan_exit(
            trade=trade,
            exit_payload=payload,
            open_orders=open_orders,
        )

    raise BrokerActionAuthorizationError(
        "Entry authorization requires a durable reservation binding and is not enabled by this milestone."
    )


class BrokerActionAuthorizer:
    """
    Binds a pure action plan to fresh durable evidence.

    This class has no broker client and cannot review, place, cancel,
    replace, or otherwise submit an order.
    """

    def __init__(self, trade_store: TradeStateStore):
        self.trade_store = trade_store

    def authorize(
        self,
        *,
        plan: BrokerActionPlan,
        strategy: str,
        trade_id: str,
        snapshot: BrokerSnapshot,
        expected_account_fingerprint: str,
        occurred_at: datetime | None = None,
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
        authorization_ttl: timedelta = (
            DEFAULT_AUTHORIZATION_TTL
        ),
    ) -> BrokerActionAuthorizationResult:
        timestamp = occurred_at or datetime.now(timezone.utc)
        _validate_action_structure(plan)

        if authorization_ttl <= timedelta(0):
            raise BrokerActionAuthorizationError(
                "Broker action authorization TTL must be positive."
            )

        validation = validate_snapshot(
            snapshot,
            evaluated_at=timestamp,
            maximum_age=maximum_snapshot_age,
        )
        if not validation.valid:
            raise BrokerActionAuthorizationError(
                validation.failure_reason
                or "Broker snapshot is invalid."
            )

        metadata = snapshot.metadata
        if metadata.account_fingerprint != expected_account_fingerprint:
            raise BrokerActionAuthorizationError(
                "Broker snapshot does not match the expected account fingerprint."
            )

        _validate_payload_accounts(
            plan,
            expected_account_fingerprint,
        )

        trade = self.trade_store.get_trade(trade_id)
        if trade.strategy != strategy:
            raise BrokerActionAuthorizationError(
                "Trade strategy does not match the requested authorization strategy."
            )

        rebuilt = _rebuild_plan(
            plan,
            trade=trade,
            open_orders=snapshot.account_state.open_orders,
        )
        supplied_digest = broker_action_plan_digest(plan)
        rebuilt_digest = broker_action_plan_digest(rebuilt)
        if not rebuilt.allowed or supplied_digest != rebuilt_digest:
            raise BrokerActionAuthorizationError(
                "Broker action plan does not match a plan rebuilt from the current trade and fresh snapshot."
            )

        snapshot_expiry = (
            metadata.completed_at + maximum_snapshot_age
        )
        requested_expiry = timestamp + authorization_ttl
        expires_at = min(snapshot_expiry, requested_expiry)
        if expires_at <= timestamp:
            raise BrokerActionAuthorizationError(
                "Broker action evidence expires before authorization can be created."
            )

        authorization, created = (
            self.trade_store.authorize_broker_action_plan(
                strategy=strategy,
                trade_id=trade_id,
                purpose=plan.purpose.value,
                plan_digest=supplied_digest,
                source_trade_version=trade.version,
                reconciliation_id=metadata.reconciliation_id,
                snapshot_digest=validation.snapshot_digest,
                account_fingerprint=metadata.account_fingerprint,
                authorized_at=timestamp,
                expires_at=expires_at,
            )
        )
        return BrokerActionAuthorizationResult(
            authorization=authorization,
            created=created,
            plan_digest=supplied_digest,
        )


def main() -> None:
    print("\nBROKER ACTION AUTHORIZATION\n")
    print(
        "Plans are bound to a current trade version, fresh complete "
        "broker snapshot, account fingerprint, canonical digest, and "
        "short expiry before they can be claimed once."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()