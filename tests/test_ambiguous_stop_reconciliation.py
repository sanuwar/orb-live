"""Offline regression coverage for ambiguous protective-stop submission."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from checkpoint_7c_one_share_commissioning_launcher import RETIRED_LIVE_RUN_IDS
from src.one_share_commissioning import (
    AtomicEvidenceStore,
    EASTERN,
    ENTRY_AUTHORIZATION_PHRASE,
    FLATTEN_AUTHORIZATION_PHRASE,
    RuntimeAuthorization,
    State,
    validate_sanitized_evidence,
)
from src.one_share_commissioning_orchestrator import (
    CommissioningTimePolicy,
    OneShareCommissioningOrchestrator,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    CommissionedAccountRead,
    CommissioningOrderRecord,
    CommissioningPositionRecord,
    RobinhoodCommissioningAdapter,
)
from tests.test_live_order_contract import make_place_response


NOW = datetime(2026, 7, 29, 10, 40, tzinfo=EASTERN)
POLICY = CommissioningTimePolicy(
    execution_date=date(2026, 7, 29),
    entry_start=datetime(2026, 7, 29, 10, 35, tzinfo=EASTERN),
    entry_cutoff=datetime(2026, 7, 29, 11, 0, tzinfo=EASTERN),
    mandatory_risk_off=datetime(2026, 7, 29, 11, 10, tzinfo=EASTERN),
    verified_flat_deadline=datetime(2026, 7, 29, 11, 15, tzinfo=EASTERN),
    attended_end=datetime(2026, 7, 29, 11, 20, tzinfo=EASTERN),
)
EXPECTED_STOP = Decimal("85.00")


class Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def authorization(run_id: str, stage: str) -> RuntimeAuthorization:
    return RuntimeAuthorization(
        run_id=run_id,
        eastern_date=date(2026, 7, 29),
        window_start=NOW - timedelta(minutes=5),
        window_end=NOW + timedelta(minutes=10),
        phrase=(
            ENTRY_AUTHORIZATION_PHRASE
            if stage == "entry"
            else FLATTEN_AUTHORIZATION_PHRASE
        ),
        symbol="AAPL",
        quantity=Decimal("1"),
        planned_risk_usd=Decimal("15"),
        acknowledges_loss_may_exceed_plan=True,
        operator_present=True,
        robinhood_ui_available=True,
        no_second_trade=True,
    )


def order(
    identifier: str,
    *,
    symbol: str = "AAPL",
    side: str,
    order_type: str = "market",
    state: str = "filled",
    requested: Decimal | None = Decimal("1"),
    cumulative: Decimal = Decimal("1"),
    remaining: Decimal | None = Decimal("0"),
    stop_value: Decimal | None = None,
    average: Decimal | None = Decimal("100"),
) -> CommissioningOrderRecord:
    return CommissioningOrderRecord(
        server_order_id=identifier,
        symbol=symbol,
        side=side,
        order_type=order_type,
        state=state,
        requested_quantity=requested,
        cumulative_quantity=cumulative,
        remaining_quantity=remaining,
        stop_price=stop_value,
        average_fill_price=average,
        time_in_force="gtc" if order_type in {"stop", "stop_market"} else "gfd",
        market_hours="regular_hours",
    )


class AmbiguousStopAdapter:
    commissioned_account_number = "transient"

    def __init__(self, scenario: str = "pending") -> None:
        self.scenario = scenario
        self.phase = "preflight"
        self.calls: list[str] = []
        self.write_roles: list[str] = []
        self.entry_reads = 0
        self.stop_reads = 0
        self.cancel_reads = 0

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def _entry(self) -> CommissioningOrderRecord:
        return order("entry-fixture", side="buy")

    def _stop(self) -> CommissioningOrderRecord:
        values: dict[str, object] = {
            "identifier": "stop-fixture",
            "side": "sell",
            "order_type": "stop_market",
            "state": "queued",
            "requested": Decimal("1"),
            "cumulative": Decimal("0"),
            "remaining": Decimal("1"),
            "stop_value": EXPECTED_STOP,
            "average": None,
        }
        if self.scenario == "filled_flat":
            values.update(
                state="filled",
                cumulative=Decimal("1"),
                remaining=Decimal("0"),
                average=EXPECTED_STOP,
            )
        elif self.scenario == "filled_long_conflict":
            values.update(
                state="filled",
                cumulative=Decimal("1"),
                remaining=Decimal("0"),
                average=EXPECTED_STOP,
            )
        elif self.scenario == "wrong_quantity":
            values.update(requested=Decimal("2"), remaining=Decimal("2"))
        elif self.scenario == "wrong_side":
            values.update(side="buy")
        elif self.scenario == "wrong_symbol":
            values.update(symbol="MSFT")
        elif self.scenario == "conflicting_order":
            values.update(order_type="market", stop_value=None)
        elif self.scenario in {"partial", "partial_cancelled"}:
            values.update(
                state=(
                    "partially_filled"
                    if self.scenario == "partial"
                    else "partially_filled_rest_cancelled"
                ),
                cumulative=Decimal("0.5"),
                remaining=Decimal("0.5"),
            )
        elif self.scenario == "pending_cancel":
            values.update(state="pending_cancelled")
        elif self.scenario == "cancelled":
            values.update(state="cancelled")
        return order(**values)

    async def get_accounts(self) -> CommissionedAccountRead:
        self._record("get_accounts")
        return CommissionedAccountRead(True, 1, 1)

    async def get_equity_positions(
        self,
    ) -> tuple[CommissioningPositionRecord, ...]:
        self._record("get_equity_positions")
        flat = (
            self.phase == "exit"
            or (
                self.phase == "ambiguous_stop"
                and self.scenario in {"filled_flat", "position_flat_no_order"}
            )
        )
        if self.phase == "cancel" and self.cancel_reads > 1:
            flat = False
        if self.phase == "preflight":
            flat = True
        return (
            ()
            if flat
            else (CommissioningPositionRecord("AAPL", Decimal("1")),)
        )

    async def get_equity_orders(
        self,
    ) -> tuple[CommissioningOrderRecord, ...]:
        self._record("get_equity_orders")
        if self.phase == "preflight":
            return ()
        entry = self._entry()
        if self.phase == "entry":
            self.entry_reads += 1
            if self.scenario == "existing_sell" and self.entry_reads >= 2:
                competing = order(
                    "competing-fixture",
                    side="sell",
                    state="queued",
                    cumulative=Decimal("0"),
                    remaining=Decimal("1"),
                    average=None,
                )
                return entry, competing
            return (entry,)
        stop = self._stop()
        if self.phase == "ambiguous_stop":
            self.stop_reads += 1
            if self.scenario in {
                "missing",
                "position_flat_no_order",
            }:
                return (entry,)
            if self.scenario == "delayed" and self.stop_reads == 1:
                return (entry,)
            if self.scenario == "multiple":
                second = order(
                    "second-stop-fixture",
                    side="sell",
                    order_type="stop_market",
                    state="queued",
                    cumulative=Decimal("0"),
                    remaining=Decimal("1"),
                    stop_value=EXPECTED_STOP,
                    average=None,
                )
                return entry, stop, second
            return entry, stop
        if self.phase == "cancel":
            self.cancel_reads += 1
            stop = order(
                "stop-fixture",
                side="sell",
                order_type="stop_market",
                state=(
                    "pending_cancelled"
                    if self.cancel_reads == 1
                    else "cancelled"
                ),
                cumulative=Decimal("0"),
                remaining=Decimal("1"),
                stop_value=EXPECTED_STOP,
                average=None,
            )
            return entry, stop
        cancelled = order(
            "stop-fixture",
            side="sell",
            order_type="stop_market",
            state="cancelled",
            cumulative=Decimal("0"),
            remaining=Decimal("1"),
            stop_value=EXPECTED_STOP,
            average=None,
        )
        completed_exit = order("exit-fixture", side="sell")
        return entry, cancelled, completed_exit

    async def review_equity_order(self, arguments, *, role):
        self._record("review_equity_order")
        return {
            "symbol": "AAPL",
            "quantity": "1",
            "side": arguments["side"],
            "type": arguments["type"],
        }

    async def place_equity_order(self, arguments, *, role):
        self._record("place_equity_order")
        self.write_roles.append(role)
        if role == "entry":
            self.phase = "entry"
            return SimpleNamespace(server_order_id="entry-fixture", state="filled")
        if role == "stop":
            self.phase = "ambiguous_stop"
            raise AmbiguousWriteError("STOP_PLACE_RESPONSE_MALFORMED")
        self.phase = "exit"
        return SimpleNamespace(server_order_id="exit-fixture", state="filled")

    async def cancel_equity_order(self, arguments):
        self._record("cancel_equity_order")
        self.write_roles.append("cancel")
        self.phase = "cancel"
        return SimpleNamespace(request_was_accepted=True)


def make_orchestrator(tmp_path, scenario: str = "pending", *, run_id="stop-run"):
    adapter = AmbiguousStopAdapter(scenario)
    clock = Clock()
    handoffs = []
    instance = OneShareCommissioningOrchestrator(
        run_id=run_id,
        adapter=adapter,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now_eastern=lambda: NOW,
        authorization_provider=lambda stage: authorization(run_id, stage),
        evidence_store=AtomicEvidenceStore(tmp_path, run_id),
        emergency_handoff=handoffs.append,
        strategy_enabled=False,
        regular_market_hours=True,
        time_policy=POLICY,
    )
    return instance, adapter, clock, handoffs


def run(instance):
    return asyncio.run(instance.run())


class Session:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def call_tool(self, name, arguments):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


def stop_payload() -> dict[str, str]:
    return {
        "account_number": "transient",
        "symbol": "AAPL",
        "side": "sell",
        "type": "stop_market",
        "quantity": "1",
        "stop_price": format(EXPECTED_STOP, "f"),
        "time_in_force": "gtc",
        "market_hours": "regular_hours",
    }


@pytest.mark.parametrize(
    ("state", "category"),
    [
        ("new", "STOP_PLACE_ACCEPTED_PENDING"),
        ("unconfirmed", "STOP_PLACE_ACCEPTED_PENDING"),
        ("queued", "STOP_PLACE_ACCEPTED_OPEN"),
        ("confirmed", "STOP_PLACE_ACCEPTED_OPEN"),
        ("placed", "STOP_PLACE_ACCEPTED_OPEN"),
        ("open", "STOP_PLACE_ACCEPTED_OPEN"),
        ("accepted", "STOP_PLACE_ACCEPTED_OPEN"),
        ("triggered", "STOP_PLACE_ACCEPTED_TRIGGERED"),
        ("filled", "STOP_PLACE_ACCEPTED_FILLED"),
    ],
)
def test_nested_stop_place_response_preserves_state(state, category):
    response = make_place_response(
        symbol="AAPL",
        side="sell",
        type="market",
        trigger="stop",
        state=state,
        quantity="1",
        cumulative_quantity="0" if state != "filled" else "1",
        price=None,
        stop_price=format(EXPECTED_STOP, "f"),
        average_price=None if state != "filled" else format(EXPECTED_STOP, "f"),
        time_in_force="gtc",
        market_hours="regular_hours",
    )
    adapter = RobinhoodCommissioningAdapter(Session(response), "transient")
    parsed = asyncio.run(
        adapter.place_equity_order(stop_payload(), role="stop")
    )
    assert parsed.state == state
    assert (
        OneShareCommissioningOrchestrator._stop_place_category(parsed.state)
        == category
    )


@pytest.mark.parametrize(
    ("response", "error", "category"),
    [
        ({"accepted": True}, None, "STOP_PLACE_RESPONSE_MALFORMED"),
        (None, TimeoutError(), "STOP_PLACE_TRANSPORT_AMBIGUOUS"),
    ],
)
def test_stop_ambiguity_subtypes_are_sanitized(response, error, category):
    session = Session(response, error)
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    with pytest.raises(AmbiguousWriteError) as captured:
        asyncio.run(adapter.place_equity_order(stop_payload(), role="stop"))
    assert captured.value.category == category
    assert session.calls == 1
    with pytest.raises(Exception):
        asyncio.run(adapter.place_equity_order(stop_payload(), role="stop"))
    assert session.calls == 1


@pytest.mark.parametrize("scenario", ["pending", "delayed"])
def test_ambiguous_stop_is_uniquely_reconciled_without_retry(tmp_path, scenario):
    instance, adapter, _, handoffs = make_orchestrator(tmp_path, scenario)
    result = run(instance)
    assert result.state is State.COMPLETE
    assert handoffs == []
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert adapter.write_roles.count("stop") == 1
    assert instance.machine.evidence.record_counts["stop_place_retries"] == 0
    assert instance.machine.evidence.boolean_validations[
        "stop_attribution_unique"
    ] is True
    assert "STOP_RECONCILED_BY_ORDER" in (
        instance.machine.evidence.conservative_classifications
    )


def test_normal_filled_stop_response_reconciles_flat_without_more_writes(tmp_path):
    class NormalFilledStopAdapter(AmbiguousStopAdapter):
        async def place_equity_order(self, arguments, *, role):
            if role != "stop":
                return await super().place_equity_order(arguments, role=role)
            self._record("place_equity_order")
            self.write_roles.append("stop")
            self.phase = "ambiguous_stop"
            return SimpleNamespace(
                server_order_id="stop-fixture", state="filled"
            )

    adapter = NormalFilledStopAdapter("filled_flat")
    clock = Clock()
    handoffs = []
    instance = OneShareCommissioningOrchestrator(
        run_id="normal-filled-stop-run",
        adapter=adapter,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now_eastern=lambda: NOW,
        authorization_provider=lambda stage: authorization(
            "normal-filled-stop-run", stage
        ),
        evidence_store=AtomicEvidenceStore(
            tmp_path, "normal-filled-stop-run"
        ),
        emergency_handoff=handoffs.append,
        strategy_enabled=False,
        regular_market_hours=True,
        time_policy=POLICY,
    )
    assert run(instance).state is State.COMPLETE
    assert handoffs == []
    assert adapter.write_roles == ["entry", "stop"]


def test_ambiguous_stop_already_filled_finishes_flat_without_more_writes(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(tmp_path, "filled_flat")
    result = run(instance)
    assert result.state is State.COMPLETE
    assert handoffs == []
    assert adapter.write_roles == ["entry", "stop"]
    assert "STOP_ALREADY_FILLED_POSITION_FLAT" in (
        instance.machine.evidence.conservative_classifications
    )
    assert instance.machine.state is State.COMPLETE


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("multiple", "STOP_RECONCILIATION_MULTIPLE_MATCHES"),
        ("wrong_quantity", "STOP_RECONCILIATION_WRONG_QUANTITY"),
        ("wrong_side", "STOP_RECONCILIATION_WRONG_SIDE"),
        ("wrong_symbol", "STOP_RECONCILIATION_WRONG_SYMBOL"),
        ("conflicting_order", "STOP_RECONCILIATION_CONFLICTING_ORDER"),
        ("partial", "STOP_PARTIALLY_FILLED"),
        ("partial_cancelled", "STOP_PARTIALLY_FILLED"),
        ("pending_cancel", "STOP_CANCEL_PENDING"),
        ("cancelled", "STOP_CANCEL_CONFIRMED"),
    ],
)
def test_unsafe_stop_attribution_requires_operator_without_another_sell(
    tmp_path, scenario, reason
):
    instance, adapter, _, handoffs = make_orchestrator(tmp_path, scenario)
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == reason
    assert adapter.write_roles == ["entry", "stop"]
    assert adapter.write_roles.count("stop") == 1
    assert "cancel" not in adapter.write_roles
    assert "exit" not in adapter.write_roles


@pytest.mark.parametrize(
    "scenario", ["missing", "position_flat_no_order", "filled_long_conflict"]
)
def test_missing_or_inconsistent_stop_times_out_fail_closed(tmp_path, scenario):
    instance, adapter, clock, handoffs = make_orchestrator(tmp_path, scenario)
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == "STOP_RECONCILIATION_TIMEOUT"
    assert adapter.write_roles == ["entry", "stop"]
    assert len(clock.sleeps) == 30
    assert instance.machine.evidence.record_counts[
        "stop_reconciliation_orders_reads"
    ] == 31
    assert instance.machine.evidence.record_counts[
        "stop_reconciliation_positions_reads"
    ] == 31


def test_existing_nonterminal_sell_blocks_stop_write(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(tmp_path, "existing_sell")
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == "STOP_RECONCILIATION_CONFLICTING_ORDER"
    assert adapter.write_roles == ["entry"]


def test_repaired_stop_evidence_is_sanitized(tmp_path):
    run_id = "sanitized-stop-run"
    instance, _, _, _ = make_orchestrator(
        tmp_path, "pending", run_id=run_id
    )
    result = run(instance)
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    validate_sanitized_evidence(payload)
    rendered = json.dumps(payload, sort_keys=True)
    for prohibited in (
        "entry-fixture",
        "stop-fixture",
        "exit-fixture",
        ENTRY_AUTHORIZATION_PHRASE,
        FLATTEN_AUTHORIZATION_PHRASE,
        format(EXPECTED_STOP, "f"),
    ):
        assert prohibited not in rendered


def test_failed_live_run_id_is_permanently_retired():
    assert (
        "cp7c-live-aapl-one-share-20260729-03"
        in RETIRED_LIVE_RUN_IDS
    )
