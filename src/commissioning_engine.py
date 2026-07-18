from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

try:
    from .account_state import (
        AccountState,
        AccountStateReader,
    )
    from .config import get_strategy_name, load_config
    from .dry_run_engine import (
        DryRunEngine,
        DryRunPlan,
    )
    from .journal import Journal
    from .robinhood_client import RobinhoodClient
    from .trade_state import (
        ReconciliationIssue,
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
    from dry_run_engine import (
        DryRunEngine,
        DryRunPlan,
    )
    from journal import Journal
    from robinhood_client import RobinhoodClient
    from trade_state import (
        ReconciliationIssue,
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
class CommissioningReport:
    timestamp: datetime
    account_state: AccountState
    watchdog_decision: WatchdogDecision
    dry_run_plan: DryRunPlan
    hypothetical_cash: Decimal
    daily_pnl: Decimal
    weekly_pnl: Decimal
    strategy_drawdown: Decimal
    active_local_trades: int
    expected_open_positions: int
    reconciliation_issues: tuple[
        ReconciliationIssue,
        ...,
    ]
    warnings: tuple[str, ...]
    live_order_capable: bool = False

    @property
    def has_critical_reconciliation_issue(
        self,
    ) -> bool:
        return any(
            issue.severity == "critical"
            for issue in self.reconciliation_issues
        )

    @property
    def safe_for_shadow_monitoring(self) -> bool:
        return (
            self.account_state.account.agentic_allowed
            and self.account_state.account.state
            == "active"
            and not self.live_order_capable
            and not (
                self.has_critical_reconciliation_issue
            )
        )


class CommissioningEngine:
    """
    Runs a read-only commissioning cycle.

    The engine can:

    - read account state,
    - read persistent local trade state,
    - reconcile broker and local state,
    - calculate realized risk metrics,
    - scan the market,
    - evaluate risk,
    - build hypothetical order payloads,
    - evaluate the watchdog.

    It cannot review, place, replace, or cancel orders.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: RobinhoodClient | None = None,
        journal: Journal | None = None,
        trade_store: TradeStateStore | None = None,
    ):
        self.config = config
        self.strategy_name = get_strategy_name(
            config
        )
        self.client = client or RobinhoodClient()
        self.journal = journal
        self.trade_store = (
            trade_store or TradeStateStore()
        )

        self.account_reader = AccountStateReader(
            client=self.client
        )
        self.watchdog = Watchdog(config)

    @staticmethod
    def hypothetical_cash(
        account_state: AccountState,
    ) -> Decimal:
        """
        Use the lower of broker-reported cash and
        unleveraged buying power for hypothetical sizing.

        This is not proof that sale proceeds are settled.
        """

        return max(
            Decimal("0"),
            min(
                account_state.portfolio.cash,
                account_state.portfolio
                .unleveraged_buying_power,
            ),
        )

    @staticmethod
    def expected_open_position_count(
        active_trades,
    ) -> int:
        return sum(
            1
            for trade in active_trades
            if trade.status
            in {
                TradeStatus.OPEN_UNPROTECTED,
                TradeStatus.OPEN_PROTECTED,
                TradeStatus.EXIT_PENDING,
            }
        )

    async def run(
        self,
        *,
        account_number: str | None = None,
    ) -> CommissioningReport:
        account_state = await self.account_reader.read(
            account_number=account_number
        )

        started_at = datetime.now(EASTERN)
        session_date = started_at.date()

        active_trades = (
            self.trade_store.list_active_trades()
        )

        expected_positions = (
            self.expected_open_position_count(
                active_trades
            )
        )

        daily_pnl = self.trade_store.daily_pnl(
            session_date,
            strategy=self.strategy_name,
        )

        weekly_pnl = self.trade_store.weekly_pnl(
            session_date,
            strategy=self.strategy_name,
        )

        strategy_drawdown = (
            self.trade_store.current_drawdown(
                strategy=self.strategy_name
            )
        )

        reconciliation_issues = (
            self.trade_store.reconcile(
                account_state
            )
        )

        available_cash = self.hypothetical_cash(
            account_state
        )

        warnings: list[str] = [
            (
                "Broker-reported cash and buying power "
                "are being used only for hypothetical "
                "sizing; settlement is not independently "
                "verified."
            ),
            (
                "The commissioning engine contains no "
                "order review, placement, replacement, "
                "or cancellation methods."
            ),
        ]

        for issue in reconciliation_issues:
            warnings.append(
                f"Reconciliation "
                f"{issue.severity.upper()} "
                f"[{issue.code}]: "
                f"{issue.message}"
            )

        if account_state.open_position_count:
            warnings.append(
                "The broker currently reports one or "
                "more open positions."
            )

        if account_state.open_entry_order_count:
            warnings.append(
                "The broker currently reports one or "
                "more open buy orders."
            )

        if (
            account_state
            .protective_stop_order_count
        ):
            warnings.append(
                "The broker currently reports one or "
                "more protective stop orders."
            )

        scan_completed_at: datetime | None = None

        critical_reconciliation = any(
            issue.severity == "critical"
            for issue in reconciliation_issues
        )

        if critical_reconciliation:
            dry_run_plan = DryRunPlan(
                symbol="",
                approved=False,
                reason=(
                    "Market scanning was blocked because "
                    "broker and local trade state have a "
                    "critical reconciliation mismatch."
                ),
            )

            warnings.append(
                "Dry-run scanning was skipped until "
                "critical reconciliation issues are "
                "resolved."
            )

        elif available_cash > 0:
            dry_run_engine = DryRunEngine(
                self.config,
                settled_cash=available_cash,
                client=self.client,
                journal=self.journal,
            )

            dry_run_plan = (
                await dry_run_engine.create_plan()
            )

            scan_completed_at = datetime.now(
                EASTERN
            )

        else:
            dry_run_plan = DryRunPlan(
                symbol="",
                approved=False,
                reason=(
                    "No positive broker-reported cash "
                    "was available for hypothetical "
                    "position sizing."
                ),
            )

            warnings.append(
                "Market scanning was skipped because "
                "hypothetical available cash was zero."
            )

        timestamp = datetime.now(EASTERN)

        snapshot = SystemSnapshot(
            timestamp=timestamp,
            last_market_data_at=scan_completed_at,
            engine_heartbeat_at=timestamp,
            open_positions=(
                account_state.open_position_count
            ),
            expected_open_positions=(
                expected_positions
            ),
            open_entry_orders=(
                account_state.open_entry_order_count
            ),
            protective_stop_orders=(
                account_state
                .protective_stop_order_count
            ),
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            strategy_drawdown=strategy_drawdown,
            trading_enabled=False,
            manual_kill_switch=False,
        )

        watchdog_decision = self.watchdog.evaluate(
            snapshot
        )

        return CommissioningReport(
            timestamp=timestamp,
            account_state=account_state,
            watchdog_decision=watchdog_decision,
            dry_run_plan=dry_run_plan,
            hypothetical_cash=available_cash,
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            strategy_drawdown=strategy_drawdown,
            active_local_trades=len(
                active_trades
            ),
            expected_open_positions=(
                expected_positions
            ),
            reconciliation_issues=(
                reconciliation_issues
            ),
            warnings=tuple(warnings),
            live_order_capable=False,
        )


def action_text(
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


def print_report(
    report: CommissioningReport,
) -> None:
    state = report.account_state
    decision = report.watchdog_decision
    plan = report.dry_run_plan

    print(
        "\nORB COMMISSIONING REPORT â€” READ ONLY\n"
    )

    print(
        f"Timestamp: "
        f"{report.timestamp.isoformat()}"
    )
    print(
        f"Account: "
        f"{state.account.masked_account_number}"
    )
    print(
        f"Account state: "
        f"{state.account.state}"
    )
    print(
        f"Trading type: "
        f"{state.account.trading_type}"
    )

    print(
        f"\nPortfolio value: "
        f"${state.portfolio.total_value}"
    )
    print(
        f"Broker-reported cash: "
        f"${state.portfolio.cash}"
    )
    print(
        f"Hypothetical sizing cash: "
        f"${report.hypothetical_cash}"
    )

    print(
        f"\nBroker open positions: "
        f"{state.open_position_count}"
    )
    print(
        f"Expected local positions: "
        f"{report.expected_open_positions}"
    )
    print(
        f"Active local trades: "
        f"{report.active_local_trades}"
    )
    print(
        f"Open entry orders: "
        f"{state.open_entry_order_count}"
    )
    print(
        f"Protective stop orders: "
        f"{state.protective_stop_order_count}"
    )

    print("\nREALIZED RISK METRICS")
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

    print("\nDRY-RUN PLAN")
    print(
        f"Approved: {plan.approved}"
    )
    print(
        f"Symbol: "
        f"{plan.symbol or 'NONE'}"
    )
    print(
        f"Reason: {plan.reason}"
    )

    if plan.approved:
        print(
            f"Shares: {plan.shares}"
        )
        print(
            f"Planned risk: "
            f"${plan.planned_risk}"
        )
        print(
            f"Position value: "
            f"${plan.position_value}"
        )

    print("\nWATCHDOG")
    print(
        f"Healthy: {decision.healthy}"
    )
    print(
        f"Action: {action_text(decision)}"
    )
    print(
        f"Reason: {decision.reason}"
    )
    print(
        f"Block new entries: "
        f"{decision.block_new_entries}"
    )
    print(
        f"Cancel entry orders: "
        f"{decision.cancel_entry_orders}"
    )
    print(
        f"Close positions: "
        f"{decision.close_positions}"
    )

    print("\nSAFETY")
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
    engine = CommissioningEngine(config)
    report = await engine.run()
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())