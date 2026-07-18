from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping
from zoneinfo import ZoneInfo

try:
    from .account_state import (
        AccountState,
        AccountStateReader,
    )
    from .config import get_strategy_name, load_config
    from .order_monitor import (
        OrderMonitorResult,
        ReadOnlyOrderMonitor,
    )
    from .order_observer import mask_identifier
    from .robinhood_client import RobinhoodClient
    from .trade_state import (
        DEFAULT_DB_PATH,
        ReconciliationIssue,
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )
    from .watchdog import (
        SystemSnapshot,
        Watchdog,
        WatchdogDecision,
    )
except ImportError:
    from account_state import (
        AccountState,
        AccountStateReader,
    )
    from config import get_strategy_name, load_config
    from order_monitor import (
        OrderMonitorResult,
        ReadOnlyOrderMonitor,
    )
    from order_observer import mask_identifier
    from robinhood_client import RobinhoodClient
    from trade_state import (
        DEFAULT_DB_PATH,
        ReconciliationIssue,
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )
    from watchdog import (
        SystemSnapshot,
        Watchdog,
        WatchdogDecision,
    )


EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ShadowRuntimeIssue:
    severity: str
    code: str
    message: str
    trade_id: str = ""
    symbol: str = ""


@dataclass(frozen=True)
class ShadowRuntimeReport:
    timestamp: datetime
    account_state: AccountState
    monitor_results: tuple[
        OrderMonitorResult,
        ...,
    ]
    runtime_issues: tuple[
        ShadowRuntimeIssue,
        ...,
    ]
    reconciliation_issues: tuple[
        ReconciliationIssue,
        ...,
    ]
    watchdog_decision: WatchdogDecision
    daily_pnl: Decimal
    weekly_pnl: Decimal
    strategy_drawdown: Decimal
    active_local_trades: tuple[
        TradeRecord,
        ...,
    ]
    expected_open_positions: int
    open_unprotected_trades: int
    warnings: tuple[str, ...]
    live_order_capable: bool = False
    durable_entry_halt: bool = False

    @property
    def has_critical_issue(self) -> bool:
        runtime_critical = any(
            issue.severity == "critical"
            for issue in self.runtime_issues
        )

        reconciliation_critical = any(
            issue.severity == "critical"
            for issue
            in self.reconciliation_issues
        )

        monitor_critical = any(
            result.fail_closed
            or result.requires_reconciliation
            for result in self.monitor_results
        )

        return (
            runtime_critical
            or reconciliation_critical
            or monitor_critical
        )

    @property
    def requires_immediate_protection(
        self,
    ) -> bool:
        return (
            self.open_unprotected_trades > 0
            or any(
                result
                .requires_immediate_protection
                for result
                in self.monitor_results
            )
        )

    @property
    def safe_for_shadow_monitoring(
        self,
    ) -> bool:
        account = self.account_state.account

        return (
            account.agentic_allowed
            and account.state == "active"
            and not self.live_order_capable
            and not self.durable_entry_halt
            and not self.has_critical_issue
            and not (
                self.requires_immediate_protection
            )
        )


def expected_position_count(
    trades: tuple[TradeRecord, ...],
) -> int:
    return sum(
        1
        for trade in trades
        if (
            trade.filled_quantity > 0
            and trade.status
            in {
                TradeStatus
                .ENTRY_PARTIALLY_FILLED,
                TradeStatus.PROTECTION_PENDING,
                TradeStatus.RECOVERY_REQUIRED,
                TradeStatus.OPEN_UNPROTECTED,
                TradeStatus.OPEN_PROTECTED,
                TradeStatus.EXIT_PENDING,
            }
        )
    )


def unprotected_trade_count(
    trades: tuple[TradeRecord, ...],
) -> int:
    return sum(
        1
        for trade in trades
        if trade.unprotected_quantity > 0
        and trade.status
        in {
            TradeStatus.ENTRY_PARTIALLY_FILLED,
            TradeStatus.PROTECTION_PENDING,
            TradeStatus.RECOVERY_REQUIRED,
            TradeStatus.OPEN_UNPROTECTED,
        }
    )


def watchdog_action_text(
    decision: WatchdogDecision,
) -> str:
    value = getattr(
        decision.action,
        "value",
        None,
    )

    return str(
        value
        if value is not None
        else decision.action
    )


class ShadowRuntime:
    """
    Runs one read-only broker-monitoring cycle.

    The runtime:

    - reads current broker account state,
    - monitors each active local trade sequentially,
    - retrieves linked orders using get_equity_orders,
    - applies confirmed observations to local SQLite,
    - refreshes broker account state,
    - reruns broker-versus-local reconciliation,
    - calculates realized risk metrics,
    - evaluates the watchdog,
    - produces one consolidated safety report.

    It cannot review, place, replace, or cancel a
    Robinhood order.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        trade_store: TradeStateStore,
        client: RobinhoodClient | None = None,
        order_monitor: (
            ReadOnlyOrderMonitor | None
        ) = None,
    ):
        self.config = config
        self.strategy_name = get_strategy_name(
            config
        )
        self.client = client or RobinhoodClient()

        self.trade_store = trade_store

        self.account_reader = AccountStateReader(
            client=self.client
        )

        self.order_monitor = (
            order_monitor
            or ReadOnlyOrderMonitor(
                self.trade_store
            )
        )

        self.watchdog = Watchdog(config)

    async def run(
        self,
        *,
        account_number: str | None = None,
        exit_close_reasons: (
            Mapping[str, str] | None
        ) = None,
    ) -> ShadowRuntimeReport:
        """
        Run one complete shadow-monitoring cycle.

        exit_close_reasons maps local trade IDs to the
        intended reason for an already-submitted exit,
        such as "profit_target" or "time_exit".

        An exit fill without a supplied close reason
        remains fail-closed instead of guessing.
        """

        exit_close_reasons = (
            exit_close_reasons or {}
        )

        self.trade_store.begin_startup_reconciliation(
            self.strategy_name
        )

        initial_account_state = (
            await self.account_reader.read(
                account_number=account_number
            )
        )

        selected_account_number = (
            initial_account_state
            .account
            .account_number
        )

        trades_before = (
            self.trade_store
            .list_active_trades()
        )

        monitor_results: list[
            OrderMonitorResult
        ] = []

        runtime_issues: list[
            ShadowRuntimeIssue
        ] = []

        for trade in trades_before:
            close_reason = (
                exit_close_reasons.get(
                    trade.trade_id
                )
            )

            try:
                result = (
                    await self.order_monitor
                    .monitor_trade(
                        account_number=(
                            selected_account_number
                        ),
                        trade_id=trade.trade_id,
                        close_reason=close_reason,
                    )
                )

                monitor_results.append(result)

            except Exception as error:
                runtime_issues.append(
                    ShadowRuntimeIssue(
                        severity="critical",
                        code=(
                            "trade_monitor_exception"
                        ),
                        message=(
                            "The trade monitor raised "
                            "an unexpected exception and "
                            "the cycle failed closed: "
                            f"{type(error).__name__}: "
                            f"{error}"
                        ),
                        trade_id=trade.trade_id,
                        symbol=trade.symbol,
                    )
                )

        refreshed_account_state = (
            await self.account_reader.read(
                account_number=(
                    selected_account_number
                )
            )
        )

        timestamp = datetime.now(EASTERN)

        reconciliation_issues = (
            self.trade_store
            .apply_startup_reconciliation(
                strategy=self.strategy_name,
                account_state=(
                    refreshed_account_state
                ),
                occurred_at=timestamp,
            )
        )

        durable_entry_halt = (
            self.trade_store.is_entry_halted(
                self.strategy_name
            )
        )

        active_trades = (
            self.trade_store
            .list_active_trades()
        )

        session_date = timestamp.date()

        daily_pnl = (
            self.trade_store.daily_pnl(
                session_date,
                strategy=self.strategy_name,
            )
        )

        weekly_pnl = (
            self.trade_store.weekly_pnl(
                session_date,
                strategy=self.strategy_name,
            )
        )

        strategy_drawdown = (
            self.trade_store
            .current_drawdown(
                strategy=self.strategy_name
            )
        )

        expected_positions = (
            expected_position_count(
                active_trades
            )
        )

        unprotected_count = (
            unprotected_trade_count(
                active_trades
            )
        )

        snapshot = SystemSnapshot(
            timestamp=timestamp,
            last_market_data_at=None,
            engine_heartbeat_at=timestamp,
            open_positions=(
                refreshed_account_state
                .open_position_count
            ),
            expected_open_positions=(
                expected_positions
            ),
            open_entry_orders=(
                refreshed_account_state
                .open_entry_order_count
            ),
            protective_stop_orders=(
                refreshed_account_state
                .protective_stop_order_count
            ),
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            strategy_drawdown=(
                strategy_drawdown
            ),
            trading_enabled=False,
            manual_kill_switch=False,
            durable_entry_halt=(
                durable_entry_halt
            ),
        )

        watchdog_decision = (
            self.watchdog.evaluate(snapshot)
        )

        warnings: list[str] = [
            (
                "This runtime uses only read-only "
                "Robinhood tools. Confirmed broker "
                "observations may update the local "
                "SQLite trade ledger."
            ),
            (
                "Trading remains disabled. This cycle "
                "cannot review, place, replace, or "
                "cancel an order."
            ),
        ]

        for result in monitor_results:
            if (
                result.requires_reconciliation
                or result.fail_closed
            ):
                warnings.append(
                    "Order monitor "
                    f"[{result.role.value}/"
                    f"{result.action.value}]: "
                    f"{result.reason}"
                )

            elif (
                result
                .requires_immediate_protection
            ):
                warnings.append(
                    "Immediate protection required "
                    f"for {result.trade_after.symbol}: "
                    f"{result.reason}"
                )

        for issue in runtime_issues:
            warnings.append(
                "Runtime "
                f"{issue.severity.upper()} "
                f"[{issue.code}]: "
                f"{issue.message}"
            )

        for issue in reconciliation_issues:
            warnings.append(
                "Reconciliation "
                f"{issue.severity.upper()} "
                f"[{issue.code}]: "
                f"{issue.message}"
            )

        if unprotected_count:
            warnings.append(
                f"{unprotected_count} active local "
                "trade(s) are open without a confirmed "
                "protective stop."
            )

        return ShadowRuntimeReport(
            timestamp=timestamp,
            account_state=(
                refreshed_account_state
            ),
            monitor_results=tuple(
                monitor_results
            ),
            runtime_issues=tuple(
                runtime_issues
            ),
            reconciliation_issues=(
                reconciliation_issues
            ),
            watchdog_decision=(
                watchdog_decision
            ),
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            strategy_drawdown=(
                strategy_drawdown
            ),
            active_local_trades=(
                active_trades
            ),
            expected_open_positions=(
                expected_positions
            ),
            open_unprotected_trades=(
                unprotected_count
            ),
            warnings=tuple(warnings),
            live_order_capable=False,
            durable_entry_halt=(
                durable_entry_halt
            ),
        )


def print_report(
    report: ShadowRuntimeReport,
) -> None:
    account_state = report.account_state
    watchdog = report.watchdog_decision

    print(
        "\nORB SHADOW RUNTIME REPORT â€” "
        "READ ONLY\n"
    )

    print(
        f"Timestamp: "
        f"{report.timestamp.isoformat()}"
    )
    print(
        f"Account: "
        f"{account_state.account.masked_account_number}"
    )
    print(
        f"Account state: "
        f"{account_state.account.state}"
    )
    print(
        f"Trading type: "
        f"{account_state.account.trading_type}"
    )

    print("\nBROKER STATE")
    print(
        f"Open positions: "
        f"{account_state.open_position_count}"
    )
    print(
        f"Open entry orders: "
        f"{account_state.open_entry_order_count}"
    )
    print(
        f"Protective stop orders: "
        f"{account_state.protective_stop_order_count}"
    )

    print("\nLOCAL STATE")
    print(
        f"Active local trades: "
        f"{len(report.active_local_trades)}"
    )
    print(
        f"Expected open positions: "
        f"{report.expected_open_positions}"
    )
    print(
        f"Open unprotected trades: "
        f"{report.open_unprotected_trades}"
    )

    print("\nORDER MONITOR RESULTS")
    print(
        f"Orders monitored: "
        f"{len(report.monitor_results)}"
    )

    for result in report.monitor_results:
        order_label = (
            mask_identifier(
                result.broker_order_id
            )
            if result.broker_order_id
            else "NONE"
        )

        print(
            f"  {result.trade_after.symbol} | "
            f"{result.role.value} | "
            f"{result.action.value} | "
            f"order={order_label}"
        )
        print(
            f"    Ledger changed: "
            f"{result.ledger_changed}"
        )
        print(
            f"    Reconciliation required: "
            f"{result.requires_reconciliation}"
        )
        print(
            f"    Immediate protection: "
            f"{result.requires_immediate_protection}"
        )
        print(
            f"    Reason: {result.reason}"
        )

    print("\nRISK METRICS")
    print(
        f"Daily P&L: ${report.daily_pnl}"
    )
    print(
        f"Weekly P&L: ${report.weekly_pnl}"
    )
    print(
        f"Current drawdown: "
        f"${report.strategy_drawdown}"
    )

    print("\nRECONCILIATION")
    print(
        f"Issues: "
        f"{len(report.reconciliation_issues)}"
    )

    for issue in report.reconciliation_issues:
        label = issue.symbol or "ACCOUNT"

        print(
            f"  {issue.severity.upper()} "
            f"[{issue.code}] "
            f"{label}: "
            f"{issue.message}"
        )

    print("\nRUNTIME ISSUES")
    print(
        f"Issues: "
        f"{len(report.runtime_issues)}"
    )

    for issue in report.runtime_issues:
        label = issue.symbol or "SYSTEM"

        print(
            f"  {issue.severity.upper()} "
            f"[{issue.code}] "
            f"{label}: "
            f"{issue.message}"
        )

    print("\nWATCHDOG")
    print(f"Healthy: {watchdog.healthy}")
    print(
        f"Action: "
        f"{watchdog_action_text(watchdog)}"
    )
    print(f"Reason: {watchdog.reason}")
    print(
        f"Block new entries: "
        f"{watchdog.block_new_entries}"
    )

    print("\nSAFETY")
    print(
        f"Critical issue: "
        f"{report.has_critical_issue}"
    )
    print(
        f"Immediate protection required: "
        f"{report.requires_immediate_protection}"
    )
    print(
        f"Live order capable: "
        f"{report.live_order_capable}"
    )
    print(
        f"Safe for shadow monitoring: "
        f"{report.safe_for_shadow_monitoring}"
    )

    print("\nWarnings:")

    for warning in report.warnings:
        print(f"  - {warning}")

    print(
        "\nNo write-capable Robinhood tool "
        "was called."
    )


async def main() -> None:
    config = load_config()
    trade_store = TradeStateStore(DEFAULT_DB_PATH)
    runtime = ShadowRuntime(
        config,
        trade_store=trade_store,
    )
    report = await runtime.run()
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())