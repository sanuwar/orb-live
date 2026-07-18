from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

try:
    from .config import load_config
except ImportError:
    from config import load_config


ET = ZoneInfo("America/New_York")


class WatchdogAction(str, Enum):
    HEALTHY = "healthy"
    BLOCK_NEW_ENTRIES = "block_new_entries"
    CANCEL_ENTRY_ORDERS = "cancel_entry_orders"
    CLOSE_POSITION = "close_position"
    EMERGENCY_HALT = "emergency_halt"


@dataclass(frozen=True)
class SystemSnapshot:
    timestamp: datetime
    last_market_data_at: datetime | None
    engine_heartbeat_at: datetime | None

    open_positions: int
    expected_open_positions: int

    open_entry_orders: int
    protective_stop_orders: int

    daily_pnl: Decimal
    weekly_pnl: Decimal
    strategy_drawdown: Decimal

    trading_enabled: bool
    manual_kill_switch: bool = False
    durable_entry_halt: bool = False


@dataclass(frozen=True)
class WatchdogDecision:
    healthy: bool
    action: WatchdogAction
    reason: str
    block_new_entries: bool
    cancel_entry_orders: bool
    close_positions: bool


class Watchdog:
    """
    Independent fail-closed safety controller.

    This class never places or cancels orders. It only decides whether the
    trading system is safe enough to continue.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def evaluate(
        self,
        snapshot: SystemSnapshot,
    ) -> WatchdogDecision:
        if snapshot.timestamp.tzinfo is None:
            raise ValueError(
                "Snapshot timestamp must include a timezone."
            )

        if (
            snapshot.last_market_data_at is not None
            and snapshot.last_market_data_at.tzinfo is None
        ):
            raise ValueError(
                "Market-data timestamp must include a timezone."
            )

        if (
            snapshot.engine_heartbeat_at is not None
            and snapshot.engine_heartbeat_at.tzinfo is None
        ):
            raise ValueError(
                "Heartbeat timestamp must include a timezone."
            )

        now_et = snapshot.timestamp.astimezone(ET)

        if snapshot.manual_kill_switch:
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.EMERGENCY_HALT,
                reason="Manual kill switch is active.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=True,
            )

        if snapshot.durable_entry_halt:
            return WatchdogDecision(
                healthy=False,
                action=(
                    WatchdogAction.BLOCK_NEW_ENTRIES
                ),
                reason=(
                    "A durable entry recovery or "
                    "startup halt is active."
                ),
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=False,
            )
        if not snapshot.trading_enabled:
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.BLOCK_NEW_ENTRIES,
                reason="Trading is disabled.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=False,
            )

        if snapshot.engine_heartbeat_at is None:
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.EMERGENCY_HALT,
                reason="Engine heartbeat is missing.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=snapshot.open_positions > 0,
            )

        heartbeat_age = (
            snapshot.timestamp
            - snapshot.engine_heartbeat_at
        )

        if heartbeat_age > timedelta(seconds=30):
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.EMERGENCY_HALT,
                reason="Engine heartbeat is stale.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=snapshot.open_positions > 0,
            )

        if snapshot.last_market_data_at is None:
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.BLOCK_NEW_ENTRIES,
                reason="Market data is unavailable.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=False,
            )

        market_data_age = (
            snapshot.timestamp
            - snapshot.last_market_data_at
        )

        if market_data_age > timedelta(seconds=15):
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.BLOCK_NEW_ENTRIES,
                reason="Market data is stale.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=False,
            )

        if snapshot.open_positions != snapshot.expected_open_positions:
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.EMERGENCY_HALT,
                reason=(
                    "Actual open positions do not match "
                    "the strategy's expected positions."
                ),
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=snapshot.open_positions > 0,
            )

        if snapshot.open_positions > 0:
            if snapshot.protective_stop_orders < snapshot.open_positions:
                return WatchdogDecision(
                    healthy=False,
                    action=WatchdogAction.CLOSE_POSITION,
                    reason=(
                        "An open position does not have confirmed "
                        "broker-held stop protection."
                    ),
                    block_new_entries=True,
                    cancel_entry_orders=True,
                    close_positions=True,
                )

        risk = self.config["risk"]

        daily_limit = Decimal(
            str(risk["daily_loss_limit_usd"])
        )
        weekly_limit = Decimal(
            str(risk["weekly_loss_limit_usd"])
        )
        drawdown_limit = Decimal(
            str(risk["total_drawdown_halt_usd"])
        )

        if snapshot.daily_pnl <= -daily_limit:
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.EMERGENCY_HALT,
                reason="Daily loss limit has been reached.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=snapshot.open_positions > 0,
            )

        if snapshot.weekly_pnl <= -weekly_limit:
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.EMERGENCY_HALT,
                reason="Weekly loss limit has been reached.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=snapshot.open_positions > 0,
            )

        if snapshot.strategy_drawdown >= drawdown_limit:
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.EMERGENCY_HALT,
                reason="Strategy drawdown halt has been reached.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=snapshot.open_positions > 0,
            )

        exit_time = time.fromisoformat(
            self.config["exit"]["time_exit"]
        )

        if (
            now_et.time() >= exit_time
            and snapshot.open_positions > 0
        ):
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.CLOSE_POSITION,
                reason="A position remains open at the time-exit deadline.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=True,
            )

        if snapshot.open_entry_orders > 0 and now_et.time() > time(11, 0):
            return WatchdogDecision(
                healthy=False,
                action=WatchdogAction.CANCEL_ENTRY_ORDERS,
                reason="Entry orders remain open after the entry window.",
                block_new_entries=True,
                cancel_entry_orders=True,
                close_positions=False,
            )

        return WatchdogDecision(
            healthy=True,
            action=WatchdogAction.HEALTHY,
            reason="All watchdog safety checks passed.",
            block_new_entries=False,
            cancel_entry_orders=False,
            close_positions=False,
        )


if __name__ == "__main__":
    config = load_config()
    watchdog = Watchdog(config)

    now = datetime.now(ET)

    sample_snapshot = SystemSnapshot(
        timestamp=now,
        last_market_data_at=now,
        engine_heartbeat_at=now,
        open_positions=0,
        expected_open_positions=0,
        open_entry_orders=0,
        protective_stop_orders=0,
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        strategy_drawdown=Decimal("0"),
        trading_enabled=False,
        manual_kill_switch=False,
    )

    decision = watchdog.evaluate(sample_snapshot)

    print(f"Healthy: {decision.healthy}")
    print(f"Action: {decision.action.value}")
    print(f"Reason: {decision.reason}")
    print(f"Block entries: {decision.block_new_entries}")
    print(f"Cancel entries: {decision.cancel_entry_orders}")
    print(f"Close positions: {decision.close_positions}")