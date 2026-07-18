from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

try:
    from .commissioning_engine import (
        CommissioningReport,
    )
    from .config import load_config
except ImportError:
    from commissioning_engine import (
        CommissioningReport,
    )
    from config import load_config


REQUIRED_ARM_TOKEN = "ARM ORB LIVE"


@dataclass(frozen=True)
class GateFailure:
    code: str
    message: str


@dataclass(frozen=True)
class LiveSafetyContext:
    """
    Values that must be supplied immediately before
    considering a live entry.

    The arm token is deliberately explicit to reduce
    accidental activation.
    """

    arm_token: str
    expected_account_last4: str
    session_date: date
    entry_count_today: int
    duplicate_entry_exists: bool
    manual_kill_switch: bool = False


@dataclass(frozen=True)
class SafetyGateDecision:
    allowed: bool
    failures: tuple[GateFailure, ...]

    @property
    def reason(self) -> str:
        if self.allowed:
            return (
                "All live-entry safety checks passed."
            )

        return "; ".join(
            failure.message
            for failure in self.failures
        )

    def has_failure(
        self,
        code: str,
    ) -> bool:
        return any(
            failure.code == code
            for failure in self.failures
        )


def first_present(
    mapping: dict[str, Any],
    names: Iterable[str],
) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]

    return None


def decimal_setting(
    mapping: dict[str, Any],
    names: Iterable[str],
) -> Decimal | None:
    value = first_present(mapping, names)

    if value in {None, ""}:
        return None

    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def integer_setting(
    mapping: dict[str, Any],
    names: Iterable[str],
) -> int | None:
    value = first_present(mapping, names)

    if value in {None, ""}:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def payload_decimal(
    payload: dict[str, Any],
    key: str,
) -> Decimal | None:
    value = payload.get(key)

    if value in {None, ""}:
        return None

    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


class LiveSafetyGate:
    """
    Pure fail-closed safety evaluation.

    This class contains no Robinhood client, session,
    review, placement, replacement, or cancellation
    methods. It cannot submit an order.
    """

    def __init__(
        self,
        config: dict[str, Any],
    ):
        self.config = config

        self.strategy = (
            config.get("strategy") or {}
        )

        self.risk = (
            config.get("risk")
            or config.get("risk_limits")
            or {}
        )

        self.execution = (
            config.get("execution") or {}
        )

    def _configuration_failures(
        self,
    ) -> list[GateFailure]:
        failures: list[GateFailure] = []

        required_decimal_settings = {
            "risk_per_trade": (
                "risk_per_trade_usd",
                "risk_per_trade_dollars",
                "risk_per_trade",
                "per_trade_risk",
                "max_risk_per_trade",
            ),
            "max_position": (
                "maximum_position_usd",
                "max_position_dollars",
                "max_position_value",
                "max_position_size",
            ),
            "daily_loss": (
                "daily_loss_limit_usd",
                "daily_loss_limit_dollars",
                "daily_loss_limit",
                "max_daily_loss",
            ),
            "weekly_loss": (
                "weekly_loss_limit_usd",
                "weekly_loss_limit_dollars",
                "weekly_loss_limit",
                "max_weekly_loss",
            ),
            "drawdown_halt": (
                "total_drawdown_halt_usd",
                "total_drawdown_halt_dollars",
                "total_drawdown_halt",
                "max_drawdown",
                "drawdown_halt",
            ),
        }

        for label, names in (
            required_decimal_settings.items()
        ):
            value = decimal_setting(
                self.risk,
                names,
            )

            if value is None or value <= 0:
                failures.append(
                    GateFailure(
                        code=(
                            f"missing_config_{label}"
                        ),
                        message=(
                            "A valid positive risk "
                            f"configuration value is "
                            f"missing for {label}."
                        ),
                    )
                )

        required_integer_settings = {
            "max_open_positions": (
                "max_open_positions",
                "maximum_open_positions",
            ),
            "max_entries_per_day": (
                "max_entries_per_day",
                "maximum_entries_per_day",
            ),
        }

        for label, names in (
            required_integer_settings.items()
        ):
            value = integer_setting(
                self.risk,
                names,
            )

            if value is None or value <= 0:
                failures.append(
                    GateFailure(
                        code=(
                            f"missing_config_{label}"
                        ),
                        message=(
                            "A valid positive risk "
                            f"configuration value is "
                            f"missing for {label}."
                        ),
                    )
                )

        return failures

    def _risk_per_trade(
        self,
    ) -> Decimal | None:
        return decimal_setting(
            self.risk,
            (
                "risk_per_trade_usd",
                "risk_per_trade_dollars",
                "risk_per_trade",
                "per_trade_risk",
                "max_risk_per_trade",
            ),
        )

    def _max_position_value(
        self,
    ) -> Decimal | None:
        return decimal_setting(
            self.risk,
            (
                "maximum_position_usd",
                "max_position_dollars",
                "max_position_value",
                "max_position_size",
            ),
        )

    def _daily_loss_limit(
        self,
    ) -> Decimal | None:
        return decimal_setting(
            self.risk,
            (
                "daily_loss_limit_usd",
                "daily_loss_limit_dollars",
                "daily_loss_limit",
                "max_daily_loss",
            ),
        )

    def _weekly_loss_limit(
        self,
    ) -> Decimal | None:
        return decimal_setting(
            self.risk,
            (
                "weekly_loss_limit_usd",
                "weekly_loss_limit_dollars",
                "weekly_loss_limit",
                "max_weekly_loss",
            ),
        )

    def _drawdown_halt(
        self,
    ) -> Decimal | None:
        return decimal_setting(
            self.risk,
            (
                "total_drawdown_halt_usd",
                "total_drawdown_halt_dollars",
                "total_drawdown_halt",
                "max_drawdown",
                "drawdown_halt",
            ),
        )

    def _max_open_positions(
        self,
    ) -> int | None:
        return integer_setting(
            self.risk,
            (
                "max_open_positions",
                "maximum_open_positions",
            ),
        )

    def _max_entries_per_day(
        self,
    ) -> int | None:
        return integer_setting(
            self.risk,
            (
                "max_entries_per_day",
                "maximum_entries_per_day",
            ),
        )

    def evaluate(
        self,
        report: CommissioningReport,
        context: LiveSafetyContext,
    ) -> SafetyGateDecision:
        failures = (
            self._configuration_failures()
        )

        account = report.account_state.account
        broker_state = report.account_state
        plan = report.dry_run_plan

        if (
            context.arm_token.strip()
            != REQUIRED_ARM_TOKEN
        ):
            failures.append(
                GateFailure(
                    code="not_explicitly_armed",
                    message=(
                        "The exact live-trading arm "
                        "token was not supplied."
                    ),
                )
            )

        if context.manual_kill_switch:
            failures.append(
                GateFailure(
                    code="manual_kill_switch",
                    message=(
                        "The manual kill switch is active."
                    ),
                )
            )

        if report.durable_entry_halt:
            failures.append(
                GateFailure(
                    code="durable_entry_halt",
                    message=(
                        "A durable entry recovery or "
                        "startup halt is active."
                    ),
                )
            )
        if (
            self.strategy.get("enabled")
            is not True
        ):
            failures.append(
                GateFailure(
                    code="strategy_disabled",
                    message=(
                        "The strategy is disabled in "
                        "configuration."
                    ),
                )
            )

        if (
            self.execution.get("fail_closed")
            is not True
        ):
            failures.append(
                GateFailure(
                    code="fail_closed_disabled",
                    message=(
                        "Execution fail-closed mode is "
                        "not enabled."
                    ),
                )
            )

        if (
            self.execution.get(
                "require_broker_stop_confirmation"
            )
            is not True
        ):
            failures.append(
                GateFailure(
                    code=(
                        "stop_confirmation_not_required"
                    ),
                    message=(
                        "Broker protective-stop "
                        "confirmation is not required "
                        "by configuration."
                    ),
                )
            )

        if (
            self.execution.get(
                "regular_hours_only"
            )
            is not True
        ):
            failures.append(
                GateFailure(
                    code="regular_hours_not_required",
                    message=(
                        "Regular-hours-only execution "
                        "is not enabled."
                    ),
                )
            )

        if first_present(
            self.risk,
            (
                "allow_margin",
                "margin_allowed",
            ),
        ) is True:
            failures.append(
                GateFailure(
                    code="margin_allowed",
                    message=(
                        "Margin is allowed by risk "
                        "configuration."
                    ),
                )
            )

        if first_present(
            self.risk,
            (
                "allow_shorting",
                "short_selling_allowed",
            ),
        ) is True:
            failures.append(
                GateFailure(
                    code="shorting_allowed",
                    message=(
                        "Shorting is allowed by risk "
                        "configuration."
                    ),
                )
            )

        if first_present(
            self.risk,
            (
                "allow_averaging_down",
                "averaging_down_allowed",
            ),
        ) is True:
            failures.append(
                GateFailure(
                    code="averaging_down_allowed",
                    message=(
                        "Averaging down is allowed by "
                        "risk configuration."
                    ),
                )
            )

        expected_last4 = (
            context.expected_account_last4
            .strip()
        )

        actual_last4 = (
            account.account_number[-4:]
            if account.account_number
            else ""
        )

        if (
            len(expected_last4) != 4
            or expected_last4 != actual_last4
        ):
            failures.append(
                GateFailure(
                    code="account_not_confirmed",
                    message=(
                        "The intended account was not "
                        "confirmed using its last four "
                        "characters."
                    ),
                )
            )

        if not account.agentic_allowed:
            failures.append(
                GateFailure(
                    code="agentic_access_disabled",
                    message=(
                        "The selected account does not "
                        "permit agentic access."
                    ),
                )
            )

        if account.state != "active":
            failures.append(
                GateFailure(
                    code="account_inactive",
                    message=(
                        "The selected account is not "
                        "active."
                    ),
                )
            )

        if account.trading_type != "cash":
            failures.append(
                GateFailure(
                    code="account_not_cash",
                    message=(
                        "The selected account is not a "
                        "cash trading account."
                    ),
                )
            )

        if (
            context.session_date
            != report.timestamp.date()
        ):
            failures.append(
                GateFailure(
                    code="session_date_mismatch",
                    message=(
                        "The requested trading date does "
                        "not match the commissioning "
                        "report date."
                    ),
                )
            )

        if report.reconciliation_issues:
            failures.append(
                GateFailure(
                    code="reconciliation_not_clean",
                    message=(
                        "Broker and local trade state "
                        "have unresolved reconciliation "
                        "issues."
                    ),
                )
            )

        if (
            not report.watchdog_decision.healthy
            or report.watchdog_decision
            .block_new_entries
        ):
            failures.append(
                GateFailure(
                    code="watchdog_blocked",
                    message=(
                        "The watchdog is not healthy or "
                        "is blocking new entries."
                    ),
                )
            )

        max_positions = (
            self._max_open_positions()
        )

        if (
            max_positions is not None
            and broker_state.open_position_count
            >= max_positions
        ):
            failures.append(
                GateFailure(
                    code="open_position_limit",
                    message=(
                        "The maximum number of open "
                        "positions has been reached."
                    ),
                )
            )

        if (
            broker_state.open_position_count
            != report.expected_open_positions
        ):
            failures.append(
                GateFailure(
                    code="position_count_mismatch",
                    message=(
                        "Broker and local expected "
                        "position counts do not match."
                    ),
                )
            )

        if (
            broker_state.open_entry_order_count
            != 0
        ):
            failures.append(
                GateFailure(
                    code="existing_entry_order",
                    message=(
                        "The broker already has an open "
                        "entry order."
                    ),
                )
            )

        if (
            broker_state
            .protective_stop_order_count
            > broker_state.open_position_count
        ):
            failures.append(
                GateFailure(
                    code="orphan_stop_order",
                    message=(
                        "The broker reports more "
                        "protective stops than open "
                        "positions."
                    ),
                )
            )

        max_entries = (
            self._max_entries_per_day()
        )

        if context.entry_count_today < 0:
            failures.append(
                GateFailure(
                    code="invalid_entry_count",
                    message=(
                        "The daily entry count cannot "
                        "be negative."
                    ),
                )
            )

        elif (
            max_entries is not None
            and context.entry_count_today
            >= max_entries
        ):
            failures.append(
                GateFailure(
                    code="daily_entry_limit",
                    message=(
                        "The maximum daily entry count "
                        "has been reached."
                    ),
                )
            )

        if context.duplicate_entry_exists:
            failures.append(
                GateFailure(
                    code="duplicate_entry",
                    message=(
                        "This strategy already has an "
                        "entry record for the symbol and "
                        "session date."
                    ),
                )
            )

        daily_limit = (
            self._daily_loss_limit()
        )

        if (
            daily_limit is not None
            and report.daily_pnl
            <= -daily_limit
        ):
            failures.append(
                GateFailure(
                    code="daily_loss_limit",
                    message=(
                        "The daily realized-loss limit "
                        "has been reached."
                    ),
                )
            )

        weekly_limit = (
            self._weekly_loss_limit()
        )

        if (
            weekly_limit is not None
            and report.weekly_pnl
            <= -weekly_limit
        ):
            failures.append(
                GateFailure(
                    code="weekly_loss_limit",
                    message=(
                        "The weekly realized-loss limit "
                        "has been reached."
                    ),
                )
            )

        drawdown_limit = (
            self._drawdown_halt()
        )

        if (
            drawdown_limit is not None
            and report.strategy_drawdown
            >= drawdown_limit
        ):
            failures.append(
                GateFailure(
                    code="drawdown_halt",
                    message=(
                        "The strategy drawdown halt "
                        "threshold has been reached."
                    ),
                )
            )

        if not plan.approved:
            failures.append(
                GateFailure(
                    code="plan_not_approved",
                    message=(
                        "The dry-run trade plan is not "
                        "approved."
                    ),
                )
            )

        if not plan.symbol:
            failures.append(
                GateFailure(
                    code="missing_symbol",
                    message=(
                        "The trade plan has no symbol."
                    ),
                )
            )

        if plan.shares <= 0:
            failures.append(
                GateFailure(
                    code="invalid_quantity",
                    message=(
                        "The planned share quantity is "
                        "not positive."
                    ),
                )
            )

        risk_per_trade = (
            self._risk_per_trade()
        )

        if (
            plan.planned_risk <= 0
            or (
                risk_per_trade is not None
                and plan.planned_risk
                > risk_per_trade
            )
        ):
            failures.append(
                GateFailure(
                    code="planned_risk_invalid",
                    message=(
                        "The planned trade risk is zero, "
                        "negative, or above the configured "
                        "per-trade limit."
                    ),
                )
            )

        max_position = (
            self._max_position_value()
        )

        if (
            plan.position_value <= 0
            or (
                max_position is not None
                and plan.position_value
                > max_position
            )
        ):
            failures.append(
                GateFailure(
                    code="position_value_invalid",
                    message=(
                        "The planned position value is "
                        "zero, negative, or above the "
                        "configured position limit."
                    ),
                )
            )

        entry_payload = (
            plan.entry_placement_payload
        )
        stop_payload = (
            plan.protective_stop_payload
        )

        if not isinstance(
            entry_payload,
            dict,
        ):
            failures.append(
                GateFailure(
                    code="missing_entry_payload",
                    message=(
                        "The trade plan has no entry "
                        "placement payload."
                    ),
                )
            )

        if not isinstance(
            stop_payload,
            dict,
        ):
            failures.append(
                GateFailure(
                    code="missing_stop_payload",
                    message=(
                        "The trade plan has no protective "
                        "stop payload."
                    ),
                )
            )

        if isinstance(entry_payload, dict):
            if (
                entry_payload.get("side")
                != "buy"
                or entry_payload.get("type")
                != "limit"
            ):
                failures.append(
                    GateFailure(
                        code="invalid_entry_payload",
                        message=(
                            "The entry payload is not a "
                            "long limit-buy order."
                        ),
                    )
                )

            entry_quantity = payload_decimal(
                entry_payload,
                "quantity",
            )

            if (
                entry_quantity
                != Decimal(plan.shares)
            ):
                failures.append(
                    GateFailure(
                        code=(
                            "entry_quantity_mismatch"
                        ),
                        message=(
                            "The entry payload quantity "
                            "does not match the plan."
                        ),
                    )
                )

        if isinstance(stop_payload, dict):
            if (
                stop_payload.get("side")
                != "sell"
                or stop_payload.get("type")
                != "stop_market"
            ):
                failures.append(
                    GateFailure(
                        code="invalid_stop_payload",
                        message=(
                            "The protective payload is "
                            "not a sell stop-market order."
                        ),
                    )
                )

            stop_quantity = payload_decimal(
                stop_payload,
                "quantity",
            )

            if (
                stop_quantity
                != Decimal(plan.shares)
            ):
                failures.append(
                    GateFailure(
                        code=(
                            "stop_quantity_mismatch"
                        ),
                        message=(
                            "The protective-stop quantity "
                            "does not match the plan."
                        ),
                    )
                )

            if isinstance(entry_payload, dict):
                entry_price = payload_decimal(
                    entry_payload,
                    "limit_price",
                )

                stop_price = payload_decimal(
                    stop_payload,
                    "stop_price",
                )

                if (
                    entry_price is None
                    or stop_price is None
                    or stop_price >= entry_price
                ):
                    failures.append(
                        GateFailure(
                            code=(
                                "invalid_stop_price"
                            ),
                            message=(
                                "The protective-stop "
                                "price is missing or is not "
                                "below the planned entry."
                            ),
                        )
                    )

        return SafetyGateDecision(
            allowed=not failures,
            failures=tuple(failures),
        )


def main() -> None:
    config = load_config()
    gate = LiveSafetyGate(config)

    print("\nLIVE SAFETY GATE\n")
    print(
        f"Required arm token: "
        f"{REQUIRED_ARM_TOKEN}"
    )
    print(
        f"Strategy enabled: "
        f"{config.get('strategy', {}).get('enabled')}"
    )
    print(
        "Broker write capability: False"
    )
    print(
        "Gate status: fail-closed until a complete "
        "commissioning report and safety context "
        "are evaluated."
    )


if __name__ == "__main__":
    main()