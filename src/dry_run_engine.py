from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

try:
    from .broker_adapter import (
        Quote,
        build_marketable_entry_limit,
        build_protective_stop_market,
        review_arguments,
    )
    from .config import load_config
    from .journal import Journal
    from .live_scanner import LiveScanner, ScanResult
    from .risk_engine import (
        AccountSnapshot,
        RiskDecision,
        RiskEngine,
        TradeRequest,
    )
    from .robinhood_client import RobinhoodClient
except ImportError:
    from broker_adapter import (
        Quote,
        build_marketable_entry_limit,
        build_protective_stop_market,
        review_arguments,
    )
    from config import load_config
    from journal import Journal
    from live_scanner import LiveScanner, ScanResult
    from risk_engine import (
        AccountSnapshot,
        RiskDecision,
        RiskEngine,
        TradeRequest,
    )
    from robinhood_client import RobinhoodClient


DRY_RUN_ACCOUNT = "DRY_RUN_ACCOUNT"


@dataclass(frozen=True)
class DryRunPlan:
    symbol: str
    approved: bool
    reason: str
    shares: int = 0
    planned_risk: Decimal = Decimal("0")
    position_value: Decimal = Decimal("0")
    entry_review_payload: dict[str, Any] | None = None
    entry_placement_payload: dict[str, Any] | None = None
    protective_stop_payload: dict[str, Any] | None = None
    generated_at: datetime | None = None


class DryRunEngine:
    """
    Combines the live scanner, risk engine, quote reader, and order builder.

    It contains no Robinhood order-placement, cancellation, or review calls.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        settled_cash: Decimal,
        client: RobinhoodClient | None = None,
        journal: Journal | None = None,
    ):
        if settled_cash <= 0:
            raise ValueError(
                "Dry-run settled cash must be greater than zero."
            )

        self.config = config
        self.settled_cash = settled_cash
        self.client = client or RobinhoodClient()
        self.journal = journal or Journal()

        self.scanner = LiveScanner(
            config,
            client=self.client,
            journal=self.journal,
        )

        risk_config = deepcopy(config)

        # The real YAML remains disabled. Only this in-memory copy is enabled
        # so the risk calculations can be tested.
        risk_config["strategy"]["enabled"] = True

        self.risk_engine = RiskEngine(risk_config)
        self.config_hash = Journal.config_hash(config)

    @staticmethod
    def select_candidate(
        scan_results: list[ScanResult],
    ) -> ScanResult | None:
        """
        Select one qualified candidate deterministically.

        Highest relative volume wins. Ticker is the tie-breaker.
        """

        approved = [
            result
            for result in scan_results
            if result.approved
        ]

        if not approved:
            return None

        return sorted(
            approved,
            key=lambda result: (
                -result.relative_volume,
                result.symbol,
            ),
        )[0]

    async def create_plan(self) -> DryRunPlan:
        scan_results = await self.scanner.scan_once()

        candidate = self.select_candidate(
            scan_results
        )

        if candidate is None:
            plan = DryRunPlan(
                symbol="",
                approved=False,
                reason="No qualified ORB signal was found.",
            )

            self._journal_plan(plan)
            return plan

        equity_quote = await self.client.get_equity_quote(
            candidate.symbol
        )

        quote = Quote(
            symbol=candidate.symbol,
            bid=equity_quote.bid_price,
            ask=equity_quote.ask_price,
        )

        maximum_slippage_bps = Decimal(
            str(
                self.config["execution"][
                    "maximum_entry_slippage_bps"
                ]
            )
        )

        estimated_entry_price = (
            quote.ask
            * (
                Decimal("1")
                + maximum_slippage_bps
                / Decimal("10000")
            )
        ).quantize(Decimal("0.01"))

        risk_decision: RiskDecision = (
            self.risk_engine.evaluate(
                TradeRequest(
                    symbol=candidate.symbol,
                    entry_price=estimated_entry_price,
                    stop_price=candidate.opening_range_low,
                ),
                AccountSnapshot(
                    settled_cash=self.settled_cash,
                    daily_pnl=Decimal("0"),
                    weekly_pnl=Decimal("0"),
                    strategy_drawdown=Decimal("0"),
                    open_positions=0,
                    entries_today=0,
                ),
            )
        )

        if not risk_decision.approved:
            plan = DryRunPlan(
                symbol=candidate.symbol,
                approved=False,
                reason=(
                    "Signal qualified, but risk engine rejected it: "
                    f"{risk_decision.reason}"
                ),
            )

            self._journal_plan(plan)
            return plan

        entry_order = build_marketable_entry_limit(
            account_number=DRY_RUN_ACCOUNT,
            symbol=candidate.symbol,
            quantity=risk_decision.shares,
            quote=quote,
            maximum_slippage_bps=maximum_slippage_bps,
        )

        stop_order = build_protective_stop_market(
            account_number=DRY_RUN_ACCOUNT,
            symbol=candidate.symbol,
            quantity=risk_decision.shares,
            stop_price=candidate.opening_range_low,
        )

        plan = DryRunPlan(
            symbol=candidate.symbol,
            approved=True,
            reason="Dry-run trade plan approved.",
            shares=risk_decision.shares,
            planned_risk=risk_decision.planned_risk,
            position_value=risk_decision.position_value,
            entry_review_payload=review_arguments(
                entry_order
            ),
            entry_placement_payload=entry_order.tool_arguments,
            protective_stop_payload=stop_order.tool_arguments,
            generated_at=datetime.now(timezone.utc),
        )

        self._journal_plan(plan)
        return plan

    def _journal_plan(
        self,
        plan: DryRunPlan,
    ) -> None:
        self.journal.append_event(
            strategy_id=self.config["strategy"]["name"],
            event_type="dry_run_plan",
            symbol=plan.symbol or None,
            config_hash=self.config_hash,
            payload={
                "monitoring_only": True,
                "real_order_submitted": False,
                **asdict(plan),
            },
        )


def print_plan(plan: DryRunPlan) -> None:
    print("\nORB V0 DRY-RUN ENGINE")
    print("No order tool is available to this program.\n")

    if not plan.approved:
        print(f"NO TRADE: {plan.reason}")
        return

    print(f"Symbol: {plan.symbol}")
    print(f"Shares: {plan.shares}")
    print(f"Planned risk: ${plan.planned_risk}")
    print(f"Position value: ${plan.position_value}")

    print("\nHypothetical entry review payload:")
    print(plan.entry_review_payload)

    print("\nHypothetical entry placement payload:")
    print(plan.entry_placement_payload)

    print("\nHypothetical protective stop payload:")
    print(plan.protective_stop_payload)

    print("\nNothing was sent to Robinhood for execution.")


async def main() -> None:
    config = load_config()

    engine = DryRunEngine(
        config,
        settled_cash=Decimal("3000"),
    )

    plan = await engine.create_plan()

    print_plan(plan)


if __name__ == "__main__":
    asyncio.run(main())