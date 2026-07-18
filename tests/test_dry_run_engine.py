from __future__ import annotations

from decimal import Decimal

import pytest

from src.config import load_config
from src.dry_run_engine import (
    DryRunEngine,
    ScanResult,
)
from src.journal import Journal
from src.robinhood_client import EquityQuote


class FakeScanner:
    def __init__(self, results: list[ScanResult]):
        self.results = results
        self.calls = 0

    async def scan_once(self):
        self.calls += 1
        return self.results


class FakeClient:
    def __init__(
        self,
        quote: EquityQuote | None = None,
    ):
        self.quote = quote
        self.requested_symbols: list[str] = []

    async def get_equity_quote(
        self,
        symbol: str,
    ) -> EquityQuote:
        self.requested_symbols.append(symbol)

        if self.quote is None:
            raise AssertionError(
                "Quote should not have been requested."
            )

        return self.quote


def approved_result(
    symbol: str = "AAPL",
    relative_volume: str = "4.0",
) -> ScanResult:
    return ScanResult(
        symbol=symbol,
        approved=True,
        reason="Qualified",
        gap_percent=Decimal("1.5"),
        relative_volume=Decimal(relative_volume),
        current_price=Decimal("100"),
        opening_range_high=Decimal("99.90"),
        opening_range_low=Decimal("99"),
    )


def rejected_result(
    symbol: str = "MSFT",
) -> ScanResult:
    return ScanResult(
        symbol=symbol,
        approved=False,
        reason="Gap too small",
    )


def test_select_candidate_uses_highest_relative_volume():
    results = [
        approved_result(
            symbol="AAPL",
            relative_volume="3.5",
        ),
        approved_result(
            symbol="NVDA",
            relative_volume="5.0",
        ),
        rejected_result(),
    ]

    selected = DryRunEngine.select_candidate(
        results
    )

    assert selected is not None
    assert selected.symbol == "NVDA"


def test_select_candidate_uses_symbol_as_tiebreaker():
    results = [
        approved_result(
            symbol="NVDA",
            relative_volume="4.0",
        ),
        approved_result(
            symbol="AAPL",
            relative_volume="4.0",
        ),
    ]

    selected = DryRunEngine.select_candidate(
        results
    )

    assert selected is not None
    assert selected.symbol == "AAPL"


def test_select_candidate_returns_none_without_signal():
    selected = DryRunEngine.select_candidate(
        [
            rejected_result("AAPL"),
            rejected_result("MSFT"),
        ]
    )

    assert selected is None


@pytest.mark.anyio
async def test_create_plan_returns_no_trade_without_signal(
    tmp_path,
):
    journal = Journal(
        tmp_path / "dry_run.sqlite"
    )

    client = FakeClient()

    engine = DryRunEngine(
        load_config(),
        settled_cash=Decimal("3000"),
        client=client,
        journal=journal,
    )

    engine.scanner = FakeScanner(
        [
            rejected_result("AAPL"),
            rejected_result("MSFT"),
        ]
    )

    plan = await engine.create_plan()

    assert plan.approved is False
    assert plan.symbol == ""
    assert "No qualified" in plan.reason
    assert client.requested_symbols == []
    assert journal.count_events() == 1


@pytest.mark.anyio
async def test_create_plan_builds_hypothetical_orders(
    tmp_path,
):
    journal = Journal(
        tmp_path / "dry_run.sqlite"
    )

    client = FakeClient(
        EquityQuote(
            symbol="AAPL",
            bid_price=Decimal("99.98"),
            ask_price=Decimal("100.00"),
            last_trade_price=Decimal("99.99"),
        )
    )

    engine = DryRunEngine(
        load_config(),
        settled_cash=Decimal("3000"),
        client=client,
        journal=journal,
    )

    engine.scanner = FakeScanner(
        [approved_result()]
    )

    plan = await engine.create_plan()

    assert plan.approved is True
    assert plan.symbol == "AAPL"
    assert plan.shares == 13
    assert plan.planned_risk == Decimal("14.30")
    assert plan.position_value == Decimal("1301.30")

    assert (
        plan.entry_placement_payload["type"]
        == "limit"
    )
    assert (
        plan.entry_placement_payload["side"]
        == "buy"
    )
    assert (
        plan.entry_placement_payload["quantity"]
        == "13"
    )
    assert (
        plan.entry_placement_payload["limit_price"]
        == "100.10"
    )

    assert (
        plan.protective_stop_payload["type"]
        == "stop_market"
    )
    assert (
        plan.protective_stop_payload["side"]
        == "sell"
    )
    assert (
        plan.protective_stop_payload["stop_price"]
        == "99.00"
    )

    assert client.requested_symbols == ["AAPL"]
    assert journal.count_events() == 1


@pytest.mark.anyio
async def test_risk_engine_can_reject_qualified_signal(
    tmp_path,
):
    journal = Journal(
        tmp_path / "dry_run.sqlite"
    )

    client = FakeClient(
        EquityQuote(
            symbol="AAPL",
            bid_price=Decimal("99.98"),
            ask_price=Decimal("100.00"),
            last_trade_price=Decimal("99.99"),
        )
    )

    engine = DryRunEngine(
        load_config(),
        settled_cash=Decimal("50"),
        client=client,
        journal=journal,
    )

    engine.scanner = FakeScanner(
        [approved_result()]
    )

    plan = await engine.create_plan()

    assert plan.approved is False
    assert plan.symbol == "AAPL"
    assert "risk engine rejected" in plan.reason.lower()
    assert plan.entry_placement_payload is None
    assert plan.protective_stop_payload is None
    assert journal.count_events() == 1