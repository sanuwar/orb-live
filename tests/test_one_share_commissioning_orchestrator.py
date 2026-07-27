import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.one_share_commissioning import (
    AtomicEvidenceStore, EASTERN, ENTRY_AUTHORIZATION_PHRASE,
    FLATTEN_AUTHORIZATION_PHRASE, RuntimeAuthorization, State,
)
from src.one_share_commissioning_orchestrator import OneShareCommissioningOrchestrator
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError, CommissionedAccountRead,
    CommissioningOrderRecord, CommissioningPositionRecord,
)


NOW = datetime(2026, 7, 27, 11, 5, tzinfo=EASTERN)


class Clock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def authorization(run_id, stage):
    phrase = (ENTRY_AUTHORIZATION_PHRASE if stage == "entry"
              else FLATTEN_AUTHORIZATION_PHRASE)
    return RuntimeAuthorization(
        run_id=run_id, eastern_date=date(2026, 7, 27),
        window_start=NOW - timedelta(minutes=5), window_end=NOW + timedelta(minutes=5),
        phrase=phrase, symbol="AAPL", quantity=Decimal("1"),
        planned_risk_usd=Decimal("15"), acknowledges_loss_may_exceed_plan=True,
        operator_present=True, robinhood_ui_available=True, no_second_trade=True,
    )


def order(order_id, *, side, order_type="market", state="filled",
          cumulative=Decimal("1"), remaining=Decimal("0"),
          stop_price=None, average=Decimal("200")):
    return CommissioningOrderRecord(
        server_order_id=order_id, symbol="AAPL", side=side,
        order_type=order_type, state=state, requested_quantity=Decimal("1"),
        cumulative_quantity=cumulative, remaining_quantity=remaining,
        stop_price=stop_price, average_fill_price=average,
        time_in_force="gtc" if order_type == "stop_market" else "gfd",
        market_hours="regular_hours",
    )


class FakeAdapter:
    commissioned_account_number = "transient"

    def __init__(self, *, entry_mode="filled", cancel_mode="pending_then_cancelled",
                 identifier_mode=None, interrupt_after=None, ambiguous_role=None):
        self.entry_mode = entry_mode
        self.cancel_mode = cancel_mode
        self.identifier_mode = identifier_mode
        self.interrupt_after = interrupt_after
        self.ambiguous_role = ambiguous_role
        self.phase = "preflight"
        self.calls = []
        self.write_roles = []
        self.cancel_reads = 0
        self.interrupted = False

    def _record(self, name):
        self.calls.append(name)

    def _interrupt(self):
        if self.interrupt_after == self.phase and not self.interrupted:
            self.interrupted = True
            raise RuntimeError("simulated interruption")

    async def get_accounts(self):
        self._record("get_accounts")
        return CommissionedAccountRead(True, 1, 1)

    async def get_equity_positions(self):
        self._record("get_equity_positions")
        self._interrupt()
        quantity = Decimal("0")
        if self.phase in {"entry", "stop", "cancel"}:
            quantity = Decimal("1")
        if self.phase == "cancel" and self.cancel_mode in {"stop_fills", "flat_cancelled"}:
            quantity = Decimal("0")
        if self.phase == "exit":
            quantity = Decimal("0")
        return (() if quantity == 0 else
                (CommissioningPositionRecord("AAPL", quantity),))

    async def get_equity_orders(self):
        self._record("get_equity_orders")
        self._interrupt()
        if self.phase == "preflight":
            return ()
        entry = order("entry-1", side="buy")
        if self.entry_mode == "pending":
            entry = order("entry-1", side="buy", state="confirmed",
                          cumulative=Decimal("0"), remaining=Decimal("1"), average=None)
        if self.identifier_mode == "entry_missing" and self.phase == "entry":
            return ()
        if self.identifier_mode == "entry_duplicate" and self.phase == "entry":
            return (entry, entry)
        if self.phase == "entry":
            return (entry,)
        stop = order("stop-1", side="sell", order_type="stop_market",
                     state="confirmed", cumulative=Decimal("0"),
                     remaining=Decimal("1"), stop_price=Decimal("185"), average=None)
        if self.identifier_mode == "stop_missing" and self.phase == "stop":
            return (entry,)
        if self.phase == "stop":
            return (entry, stop)
        if self.phase == "cancel":
            self.cancel_reads += 1
            if self.cancel_mode == "pending_then_cancelled" and self.cancel_reads == 1:
                stop = order("stop-1", side="sell", order_type="stop_market",
                    state="pending_cancelled", cumulative=Decimal("0"),
                    remaining=Decimal("1"), stop_price=Decimal("185"), average=None)
            elif self.cancel_mode == "stop_fills":
                stop = order("stop-1", side="sell", order_type="stop_market",
                    state="filled", cumulative=Decimal("1"), remaining=Decimal("0"),
                    stop_price=Decimal("185"), average=Decimal("185"))
            else:
                stop = order("stop-1", side="sell", order_type="stop_market",
                    state="cancelled", cumulative=Decimal("0"), remaining=Decimal("1"),
                    stop_price=Decimal("185"), average=None)
            return (entry, stop)
        stop = order("stop-1", side="sell", order_type="stop_market",
            state="cancelled", cumulative=Decimal("0"), remaining=Decimal("1"),
            stop_price=Decimal("185"), average=None)
        exit_order = order("exit-1", side="sell", average=Decimal("190"))
        if self.identifier_mode == "exit_missing" and self.phase == "exit":
            return (entry, stop)
        return (entry, stop, exit_order)

    async def review_equity_order(self, arguments, *, role):
        self._record("review_equity_order")
        return {"symbol": "AAPL", "quantity": "1", "side": arguments["side"],
                "type": arguments["type"]}

    async def place_equity_order(self, arguments, *, role):
        self._record("place_equity_order")
        self.write_roles.append(role)
        if self.ambiguous_role == role:
            raise AmbiguousWriteError("ambiguous")
        self.phase = role
        return SimpleNamespace(server_order_id=f"{role}-1")

    async def cancel_equity_order(self, arguments):
        self._record("cancel_equity_order")
        self.write_roles.append("cancel")
        if self.ambiguous_role == "cancel":
            raise AmbiguousWriteError("ambiguous")
        self.phase = "cancel"
        return SimpleNamespace(request_was_accepted=True)


def make_orchestrator(tmp_path, adapter=None, run_id="run-1"):
    clock = Clock()
    handoffs = []
    adapter = adapter or FakeAdapter()
    instance = OneShareCommissioningOrchestrator(
        run_id=run_id, adapter=adapter, monotonic=clock.monotonic,
        sleep=clock.sleep, now_eastern=lambda: NOW,
        authorization_provider=lambda stage: authorization(run_id, stage),
        evidence_store=AtomicEvidenceStore(tmp_path, run_id),
        emergency_handoff=handoffs.append, strategy_enabled=False,
        regular_market_hours=True,
    )
    return instance, adapter, clock, handoffs


def run(instance):
    return asyncio.run(instance.run())


def test_clean_complete_flow_and_exact_tool_order(tmp_path):
    instance, adapter, clock, handoffs = make_orchestrator(tmp_path)
    result = run(instance)
    assert result.state is State.COMPLETE
    assert handoffs == []
    assert adapter.calls == [
        "get_accounts", "get_equity_positions", "get_equity_orders",
        "review_equity_order", "place_equity_order",
        "get_equity_orders", "get_equity_positions",
        "review_equity_order", "place_equity_order",
        "get_equity_orders", "get_equity_positions",
        "cancel_equity_order",
        "get_equity_orders", "get_equity_positions",
        "get_equity_orders", "get_equity_positions",
        "get_equity_orders", "get_equity_positions",
        "review_equity_order", "place_equity_order",
        "get_equity_orders", "get_equity_positions",
        "get_equity_orders", "get_equity_positions",
    ]
    assert adapter.calls.count("place_equity_order") == 3
    assert adapter.calls.count("cancel_equity_order") == 1
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert clock.sleeps == [1.0]


def test_entry_timeout_has_no_duplicate_write_and_uses_injected_sleep(tmp_path):
    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path, FakeAdapter(entry_mode="pending")
    )
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.calls.count("place_equity_order") == 1
    assert len(clock.sleeps) == 30
    assert handoffs[-1].reason_code == "ENTRY_FILL_DEADLINE_EXCEEDED"


@pytest.mark.parametrize("role", ["entry", "stop", "cancel", "exit"])
def test_ambiguous_write_handoffs_without_retry(tmp_path, role):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, FakeAdapter(ambiguous_role=role)
    )
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    tool = "cancel_equity_order" if role == "cancel" else "place_equity_order"
    expected = 1 if role != "stop" and role != "exit" else {"stop": 2, "exit": 3}[role]
    assert adapter.calls.count(tool) == expected
    assert handoffs[-1].reason_code.startswith("AMBIGUOUS_")


@pytest.mark.parametrize("mode,reason", [
    ("entry_missing", "ENTRY_ORDER_RECONCILIATION_CONFLICT"),
    ("entry_duplicate", "ENTRY_ORDER_RECONCILIATION_CONFLICT"),
    ("stop_missing", "STOP_ORDER_RECONCILIATION_CONFLICT"),
    ("exit_missing", "EXIT_ORDER_RECONCILIATION_CONFLICT"),
])
def test_exact_identifier_mismatch_fails_closed(tmp_path, mode, reason):
    instance, _, _, handoffs = make_orchestrator(
        tmp_path, FakeAdapter(identifier_mode=mode)
    )
    assert run(instance).state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == reason


def test_cancel_pending_then_cancelled_then_one_exit(tmp_path):
    instance, adapter, clock, _ = make_orchestrator(tmp_path)
    assert run(instance).state is State.COMPLETE
    assert adapter.cancel_reads >= 2
    assert adapter.calls.count("cancel_equity_order") == 1
    assert adapter.calls.count("place_equity_order") == 3
    assert clock.sleeps == [1.0]


def test_stop_fill_during_cancel_prevents_exit(tmp_path):
    instance, adapter, _, _ = make_orchestrator(
        tmp_path, FakeAdapter(cancel_mode="stop_fills")
    )
    assert run(instance).state is State.COMPLETE
    assert adapter.calls.count("place_equity_order") == 2


def test_flat_position_after_cancel_prevents_exit(tmp_path):
    instance, adapter, _, _ = make_orchestrator(
        tmp_path, FakeAdapter(cancel_mode="flat_cancelled")
    )
    assert run(instance).state is State.COMPLETE
    assert adapter.calls.count("place_equity_order") == 2


@pytest.mark.parametrize("boundary", ["entry", "stop", "cancel", "exit"])
def test_interruption_after_each_write_boundary_never_retries(tmp_path, boundary):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, FakeAdapter(interrupt_after=boundary)
    )
    assert run(instance).state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == "INTERRUPTION_AFTER_WRITE_BOUNDARY"
    if boundary == "entry":
        assert adapter.calls.count("place_equity_order") == 1
    elif boundary == "stop":
        assert adapter.calls.count("place_equity_order") == 2
    elif boundary == "cancel":
        assert adapter.calls.count("cancel_equity_order") == 1
    else:
        assert adapter.calls.count("place_equity_order") == 3


def test_interrupted_run_id_recovers_only_to_operator_required(tmp_path):
    first, first_adapter, _, _ = make_orchestrator(
        tmp_path, FakeAdapter(interrupt_after="entry")
    )
    assert run(first).state is State.OPERATOR_REQUIRED
    second, second_adapter, _, handoffs = make_orchestrator(
        tmp_path, FakeAdapter(), run_id="run-1"
    )
    assert run(second).state is State.OPERATOR_REQUIRED
    assert second_adapter.calls == []
    assert handoffs[-1].reason_code == "INTERRUPTED_RUN_REQUIRES_OPERATOR"
    assert "last safe state" in handoffs[-1].expected_operator_action
    assert first_adapter.calls.count("place_equity_order") == 1


def test_completed_run_id_is_rejected(tmp_path):
    first, _, _, _ = make_orchestrator(tmp_path)
    assert run(first).state is State.COMPLETE
    second, _, _, _ = make_orchestrator(tmp_path)
    with pytest.raises(Exception, match="run ID already used"):
        run(second)


def test_emergency_handoff_payload_is_sanitized(tmp_path):
    instance, _, _, handoffs = make_orchestrator(
        tmp_path, FakeAdapter(ambiguous_role="entry")
    )
    assert run(instance).state is State.OPERATOR_REQUIRED
    payload = handoffs[-1].__dict__
    assert set(payload) == {
        "current_state", "reason_code", "position_may_exist",
        "stop_may_remain_executable", "fixed_one_share",
        "expected_operator_action",
    }
    rendered = repr(payload).lower()
    for prohibited in ("account_number", "order_id", "price", "token", "credential"):
        assert prohibited not in rendered
