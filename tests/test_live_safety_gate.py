from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.commissioning_engine import (
    CommissioningReport,
)
from src.config import load_config
from src.dry_run_engine import DryRunPlan
from src.live_safety_gate import (
    LiveSafetyContext,
    LiveSafetyGate,
    REQUIRED_ARM_TOKEN,
)
from src.trade_state import (
    ReconciliationIssue,
)
from src.watchdog import WatchdogDecision


EASTERN = ZoneInfo("America/New_York")
SESSION_DATE = date(2026, 7, 17)
REPORT_TIME = datetime(
    2026,
    7,
    17,
    10,
    0,
    tzinfo=EASTERN,
)


def enabled_config() -> dict:
    config = deepcopy(load_config())
    config["strategy"]["enabled"] = True
    return config


def healthy_watchdog() -> WatchdogDecision:
    return WatchdogDecision(
        healthy=True,
        action=None,
        reason="All checks passed.",
        block_new_entries=False,
        cancel_entry_orders=False,
        close_positions=False,
    )


def make_position() -> EquityPosition:
    return EquityPosition(
        symbol="AAPL",
        quantity=Decimal("1"),
        intraday_quantity=Decimal("1"),
        average_buy_price=Decimal("100"),
        shares_available_for_sells=Decimal("1"),
        shares_held_for_sells=Decimal("0"),
        position_type="long",
    )


def make_entry_order() -> EquityOrder:
    return EquityOrder(
        order_id="entry-1",
        symbol="AAPL",
        side="buy",
        order_type="limit",
        state="confirmed",
        quantity=Decimal("1"),
        cumulative_quantity=Decimal("0"),
        limit_price=Decimal("100.10"),
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    )


def make_account_state(
    *,
    positions: tuple[EquityPosition, ...] = (),
    orders: tuple[EquityOrder, ...] = (),
) -> AccountState:
    return AccountState(
        account=AccountInfo(
            account_number="TEST12345678",
            masked_account_number="••••5678",
            brokerage_account_type="individual",
            trading_type="cash",
            nickname="Test",
            agentic_allowed=True,
            state="active",
        ),
        portfolio=PortfolioState(
            total_value=Decimal("3000"),
            equity_value=Decimal("3000"),
            cash=Decimal("3000"),
            pending_deposits=Decimal("0"),
            buying_power=Decimal("3000"),
            unleveraged_buying_power=Decimal(
                "3000"
            ),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=positions,
        open_orders=orders,
    )


def approved_plan() -> DryRunPlan:
    return DryRunPlan(
        symbol="AAPL",
        approved=True,
        reason="Qualified ORB signal.",
        shares=10,
        planned_risk=Decimal("11"),
        position_value=Decimal("1001"),
        entry_review_payload={
            "account_number": "TEST12345678",
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "quantity": "10",
            "limit_price": "100.10",
        },
        entry_placement_payload={
            "account_number": "TEST12345678",
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "quantity": "10",
            "limit_price": "100.10",
        },
        protective_stop_payload={
            "account_number": "TEST12345678",
            "symbol": "AAPL",
            "side": "sell",
            "type": "stop_market",
            "quantity": "10",
            "stop_price": "99.00",
        },
    )


def make_report(
    *,
    account_state: AccountState | None = None,
    plan: DryRunPlan | None = None,
    watchdog: WatchdogDecision | None = None,
    daily_pnl: str = "0",
    weekly_pnl: str = "0",
    drawdown: str = "0",
    expected_open_positions: int = 0,
    issues: tuple[
        ReconciliationIssue, ...
    ] = (),
) -> CommissioningReport:
    return CommissioningReport(
        timestamp=REPORT_TIME,
        account_state=(
            account_state or make_account_state()
        ),
        watchdog_decision=(
            watchdog or healthy_watchdog()
        ),
        dry_run_plan=(
            plan or approved_plan()
        ),
        hypothetical_cash=Decimal("3000"),
        daily_pnl=Decimal(daily_pnl),
        weekly_pnl=Decimal(weekly_pnl),
        strategy_drawdown=Decimal(drawdown),
        active_local_trades=(
            expected_open_positions
        ),
        expected_open_positions=(
            expected_open_positions
        ),
        reconciliation_issues=issues,
        warnings=(),
        live_order_capable=False,
    )


def valid_context() -> LiveSafetyContext:
    return LiveSafetyContext(
        arm_token=REQUIRED_ARM_TOKEN,
        expected_account_last4="5678",
        session_date=SESSION_DATE,
        entry_count_today=0,
        duplicate_entry_exists=False,
        manual_kill_switch=False,
    )


def test_valid_context_passes_all_checks():
    decision = LiveSafetyGate(
        enabled_config()
    ).evaluate(
        make_report(),
        valid_context(),
    )

    assert decision.allowed is True
    assert decision.failures == ()
    assert (
        decision.reason
        == "All live-entry safety checks passed."
    )


def test_strategy_disabled_fails_closed():
    config = load_config()

    decision = LiveSafetyGate(
        config
    ).evaluate(
        make_report(),
        valid_context(),
    )

    assert decision.allowed is False
    assert decision.has_failure(
        "strategy_disabled"
    )


def test_exact_arm_token_and_account_confirmation_required():
    context = replace(
        valid_context(),
        arm_token="arm orb live",
        expected_account_last4="9999",
    )

    decision = LiveSafetyGate(
        enabled_config()
    ).evaluate(
        make_report(),
        context,
    )

    assert decision.has_failure(
        "not_explicitly_armed"
    )
    assert decision.has_failure(
        "account_not_confirmed"
    )


def test_manual_kill_switch_blocks_entry():
    context = replace(
        valid_context(),
        manual_kill_switch=True,
    )

    decision = LiveSafetyGate(
        enabled_config()
    ).evaluate(
        make_report(),
        context,
    )

    assert decision.allowed is False
    assert decision.has_failure(
        "manual_kill_switch"
    )


def test_watchdog_and_reconciliation_must_be_clean():
    issue = ReconciliationIssue(
        severity="critical",
        code="untracked_position",
        message="Untracked broker position.",
        symbol="AAPL",
    )

    blocked_watchdog = WatchdogDecision(
        healthy=False,
        action=None,
        reason="Blocked.",
        block_new_entries=True,
        cancel_entry_orders=True,
        close_positions=False,
    )

    report = make_report(
        watchdog=blocked_watchdog,
        issues=(issue,),
    )

    decision = LiveSafetyGate(
        enabled_config()
    ).evaluate(
        report,
        valid_context(),
    )

    assert decision.has_failure(
        "watchdog_blocked"
    )
    assert decision.has_failure(
        "reconciliation_not_clean"
    )


def test_open_position_and_entry_order_are_blocked():
    account_state = make_account_state(
        positions=(make_position(),),
        orders=(make_entry_order(),),
    )

    report = make_report(
        account_state=account_state,
        expected_open_positions=0,
    )

    decision = LiveSafetyGate(
        enabled_config()
    ).evaluate(
        report,
        valid_context(),
    )

    assert decision.has_failure(
        "open_position_limit"
    )
    assert decision.has_failure(
        "position_count_mismatch"
    )
    assert decision.has_failure(
        "existing_entry_order"
    )


def test_daily_limits_and_duplicate_entry_are_blocked():
    context = replace(
        valid_context(),
        entry_count_today=2,
        duplicate_entry_exists=True,
    )

    report = make_report(
        daily_pnl="-45",
        weekly_pnl="-120",
        drawdown="300",
    )

    decision = LiveSafetyGate(
        enabled_config()
    ).evaluate(
        report,
        context,
    )

    assert decision.has_failure(
        "daily_entry_limit"
    )
    assert decision.has_failure(
        "duplicate_entry"
    )
    assert decision.has_failure(
        "daily_loss_limit"
    )
    assert decision.has_failure(
        "weekly_loss_limit"
    )
    assert decision.has_failure(
        "drawdown_halt"
    )


def test_payload_quantity_and_stop_price_must_match():
    plan = approved_plan()

    bad_plan = replace(
        plan,
        entry_placement_payload={
            **plan.entry_placement_payload,
            "quantity": "9",
        },
        protective_stop_payload={
            **plan.protective_stop_payload,
            "quantity": "8",
            "stop_price": "101.00",
        },
    )

    decision = LiveSafetyGate(
        enabled_config()
    ).evaluate(
        make_report(plan=bad_plan),
        valid_context(),
    )

    assert decision.has_failure(
        "entry_quantity_mismatch"
    )
    assert decision.has_failure(
        "stop_quantity_mismatch"
    )
    assert decision.has_failure(
        "invalid_stop_price"
    )


def test_missing_risk_configuration_fails_closed():
    config = enabled_config()
    config["risk"] = {}

    decision = LiveSafetyGate(
        config
    ).evaluate(
        make_report(),
        valid_context(),
    )

    assert decision.allowed is False
    assert decision.has_failure(
        "missing_config_risk_per_trade"
    )
    assert decision.has_failure(
        "missing_config_max_position"
    )
    assert decision.has_failure(
        "missing_config_daily_loss"
    )
    assert decision.has_failure(
        "missing_config_weekly_loss"
    )
    assert decision.has_failure(
        "missing_config_drawdown_halt"
    )
    assert decision.has_failure(
        "missing_config_max_open_positions"
    )
    assert decision.has_failure(
        "missing_config_max_entries_per_day"
    )

def test_durable_entry_halt_fails_live_gate():
    report = replace(
        make_report(),
        durable_entry_halt=True,
    )

    decision = LiveSafetyGate(
        enabled_config()
    ).evaluate(
        report,
        valid_context(),
    )

    assert decision.allowed is False
    assert decision.has_failure(
        "durable_entry_halt"
    )
