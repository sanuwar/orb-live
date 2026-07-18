from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from src.config import load_config
from src.watchdog import (
    ET,
    SystemSnapshot,
    Watchdog,
    WatchdogAction,
)


def enabled_config():
    config = deepcopy(load_config())
    config["strategy"]["enabled"] = True
    return config


def healthy_snapshot() -> SystemSnapshot:
    now = datetime(2026, 7, 20, 10, 0, tzinfo=ET)

    return SystemSnapshot(
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
        trading_enabled=True,
        manual_kill_switch=False,
    )


def test_healthy_snapshot_passes():
    watchdog = Watchdog(enabled_config())

    decision = watchdog.evaluate(
        healthy_snapshot()
    )

    assert decision.healthy is True
    assert decision.action == WatchdogAction.HEALTHY
    assert decision.block_new_entries is False


def test_disabled_trading_blocks_entries():
    watchdog = Watchdog(load_config())

    snapshot = healthy_snapshot()

    disabled_snapshot = SystemSnapshot(
        **{
            **snapshot.__dict__,
            "trading_enabled": False,
        }
    )

    decision = watchdog.evaluate(
        disabled_snapshot
    )

    assert decision.healthy is False
    assert decision.action == WatchdogAction.BLOCK_NEW_ENTRIES
    assert decision.block_new_entries is True


def test_manual_kill_switch_halts_everything():
    watchdog = Watchdog(enabled_config())

    snapshot = healthy_snapshot()

    killed_snapshot = SystemSnapshot(
        **{
            **snapshot.__dict__,
            "manual_kill_switch": True,
            "open_positions": 1,
            "expected_open_positions": 1,
            "protective_stop_orders": 1,
        }
    )

    decision = watchdog.evaluate(
        killed_snapshot
    )

    assert decision.action == WatchdogAction.EMERGENCY_HALT
    assert decision.cancel_entry_orders is True
    assert decision.close_positions is True


def test_stale_heartbeat_triggers_halt():
    watchdog = Watchdog(enabled_config())

    snapshot = healthy_snapshot()

    stale_snapshot = SystemSnapshot(
        **{
            **snapshot.__dict__,
            "engine_heartbeat_at": (
                snapshot.timestamp
                - timedelta(seconds=31)
            ),
        }
    )

    decision = watchdog.evaluate(
        stale_snapshot
    )

    assert decision.action == WatchdogAction.EMERGENCY_HALT
    assert "heartbeat is stale" in decision.reason.lower()


def test_stale_market_data_blocks_entries():
    watchdog = Watchdog(enabled_config())

    snapshot = healthy_snapshot()

    stale_snapshot = SystemSnapshot(
        **{
            **snapshot.__dict__,
            "last_market_data_at": (
                snapshot.timestamp
                - timedelta(seconds=16)
            ),
        }
    )

    decision = watchdog.evaluate(
        stale_snapshot
    )

    assert decision.action == WatchdogAction.BLOCK_NEW_ENTRIES
    assert "market data is stale" in decision.reason.lower()


def test_unprotected_position_must_be_closed():
    watchdog = Watchdog(enabled_config())

    snapshot = healthy_snapshot()

    unsafe_snapshot = SystemSnapshot(
        **{
            **snapshot.__dict__,
            "open_positions": 1,
            "expected_open_positions": 1,
            "protective_stop_orders": 0,
        }
    )

    decision = watchdog.evaluate(
        unsafe_snapshot
    )

    assert decision.action == WatchdogAction.CLOSE_POSITION
    assert decision.close_positions is True
    assert "stop protection" in decision.reason.lower()


def test_daily_loss_limit_triggers_halt():
    watchdog = Watchdog(enabled_config())

    snapshot = healthy_snapshot()

    loss_snapshot = SystemSnapshot(
        **{
            **snapshot.__dict__,
            "daily_pnl": Decimal("-45"),
        }
    )

    decision = watchdog.evaluate(
        loss_snapshot
    )

    assert decision.action == WatchdogAction.EMERGENCY_HALT
    assert "daily loss limit" in decision.reason.lower()


def test_open_position_at_time_exit_must_close():
    watchdog = Watchdog(enabled_config())

    snapshot = healthy_snapshot()

    late_snapshot = SystemSnapshot(
        **{
            **snapshot.__dict__,
            "timestamp": datetime(
                2026,
                7,
                20,
                15,
                45,
                tzinfo=ET,
            ),
            "last_market_data_at": datetime(
                2026,
                7,
                20,
                15,
                45,
                tzinfo=ET,
            ),
            "engine_heartbeat_at": datetime(
                2026,
                7,
                20,
                15,
                45,
                tzinfo=ET,
            ),
            "open_positions": 1,
            "expected_open_positions": 1,
            "protective_stop_orders": 1,
        }
    )

    decision = watchdog.evaluate(
        late_snapshot
    )

    assert decision.action == WatchdogAction.CLOSE_POSITION
    assert decision.close_positions is True
    assert "time-exit" in decision.reason.lower()

def test_durable_entry_halt_blocks_entries():
    watchdog = Watchdog(enabled_config())
    snapshot = replace(
        healthy_snapshot(),
        durable_entry_halt=True,
    )

    decision = watchdog.evaluate(snapshot)

    assert decision.healthy is False
    assert decision.action == (
        WatchdogAction.BLOCK_NEW_ENTRIES
    )
    assert decision.block_new_entries is True
    assert decision.cancel_entry_orders is True
    assert decision.close_positions is False
    assert "durable" in decision.reason.lower()
