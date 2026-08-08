import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.one_share_commissioning import ENTRY_AUTHORIZATION_STAGE, State
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    RobinhoodCommissioningAdapter,
)
from tests.execution_safety_test_support import allow_decision
from tests.test_live_order_contract import make_order
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
    order,
    run,
)


class OneResponseSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return self.response


def entry_payload():
    return {
        "account_number": "transient",
        "symbol": "AAPL",
        "side": "buy",
        "type": "market",
        "quantity": "1",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }


def place_envelope(*, state, cumulative):
    return SimpleNamespace(structuredContent={
        "data": {"order": make_order(
            state=state,
            symbol="AAPL",
            type="market",
            quantity="1",
            cumulative_quantity=cumulative,
        )},
        "guide": "sanitized fixture",
    })


@pytest.mark.parametrize(
    "state,cumulative",
    [("queued", "0"), ("filled", "1")],
)
def test_data_order_place_response_parses_pending_and_filled(state, cumulative):
    session = OneResponseSession(place_envelope(
        state=state, cumulative=cumulative
    ))
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    result = asyncio.run(adapter.place_equity_order(
        entry_payload(), role="entry",
        safety_decision=allow_decision(role="entry", arguments=entry_payload()),
    ))
    assert result.state == state
    assert result.server_order_id
    assert session.calls == ["place_equity_order"]


def test_transport_and_malformed_responses_have_distinct_safe_categories():
    transport = RobinhoodCommissioningAdapter(
        OneResponseSession(error=TimeoutError()), "transient"
    )
    with pytest.raises(AmbiguousWriteError) as transport_error:
        asyncio.run(transport.place_equity_order(
            entry_payload(), role="entry",
            safety_decision=allow_decision(role="entry", arguments=entry_payload()),
        ))
    assert transport_error.value.category == (
        "ENTRY_PLACE_TRANSPORT_AMBIGUOUS"
    )

    malformed = RobinhoodCommissioningAdapter(
        OneResponseSession({"unexpected": True}), "transient"
    )
    with pytest.raises(AmbiguousWriteError) as malformed_error:
        asyncio.run(malformed.place_equity_order(
            entry_payload(), role="entry",
            safety_decision=allow_decision(role="entry", arguments=entry_payload()),
        ))
    assert malformed_error.value.category == "ENTRY_PLACE_RESPONSE_MALFORMED"


class AmbiguousEntryAdapter(FakeAdapter):
    def __init__(
        self,
        scenario="filled",
        category="ENTRY_PLACE_RESPONSE_MALFORMED",
    ):
        super().__init__()
        self.scenario = scenario
        self.category = category
        self.ambiguous_reads = 0

    async def place_equity_order(self, arguments, *, role, safety_decision=None):
        if role != "entry":
            return await super().place_equity_order(
                arguments, role=role, safety_decision=safety_decision
            )
        self._record("place_equity_order")
        self.write_roles.append("entry")
        self.phase = "ambiguous_entry"
        raise AmbiguousWriteError(self.category)

    async def get_equity_orders(self):
        if self.phase != "ambiguous_entry":
            return await super().get_equity_orders()
        self._record("get_equity_orders")
        self.ambiguous_reads += 1
        filled = order("entry-1", side="buy")
        if self.scenario == "delayed" and self.ambiguous_reads == 1:
            return ()
        if self.scenario in {"not_found", "position_only"}:
            return ()
        if self.scenario == "multiple":
            return (
                filled,
                order("entry-2", side="buy"),
            )
        if self.scenario == "conflicting_position":
            return (filled,)
        if self.scenario == "pending":
            return (
                order(
                    "entry-1",
                    side="buy",
                    state="queued",
                    cumulative=Decimal("0"),
                    remaining=Decimal("1"),
                    average=None,
                ),
            )
        return (filled,)

    async def get_equity_positions(self):
        if self.phase != "ambiguous_entry":
            return await super().get_equity_positions()
        self._record("get_equity_positions")
        if self.scenario in {"not_found", "pending", "conflicting_position"}:
            return ()
        return super_position()


def super_position():
    from src.robinhood_commissioning_adapter import CommissioningPositionRecord

    return (CommissioningPositionRecord("AAPL", Decimal("1")),)


def evidence(instance):
    return instance.machine.evidence


def test_exact_incident_reproduction_reconciles_fill_and_completes(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, AmbiguousEntryAdapter("filled"),
        run_id="incident-reproduction",
    )
    result = run(instance)
    assert result.state is State.COMPLETE
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert adapter.write_roles.count("entry") == 1
    assert handoffs == []
    assert "ENTRY_PLACE_RESPONSE_MALFORMED" in (
        evidence(instance).conservative_classifications
    )
    assert "ENTRY_RECONCILED_BY_ORDER" in (
        evidence(instance).conservative_classifications
    )


def test_transport_ambiguity_uses_the_same_read_only_reconciliation(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path,
        AmbiguousEntryAdapter(
            "filled", category="ENTRY_PLACE_TRANSPORT_AMBIGUOUS"
        ),
    )
    assert run(instance).state is State.COMPLETE
    assert adapter.write_roles.count("entry") == 1
    assert "ENTRY_PLACE_TRANSPORT_AMBIGUOUS" in (
        evidence(instance).conservative_classifications
    )
    assert handoffs == []


@pytest.mark.parametrize(
    "entry_mode,expected",
    [
        ("filled", "ENTRY_PLACE_ACCEPTED_FILLED"),
        ("pending", "ENTRY_PLACE_ACCEPTED_PENDING"),
    ],
)
def test_normal_place_result_records_accepted_state(
    tmp_path, entry_mode, expected
):
    instance, adapter, _, _ = make_orchestrator(
        tmp_path, FakeAdapter(entry_mode=entry_mode)
    )
    run(instance)
    assert adapter.write_roles.count("entry") == 1
    assert expected in evidence(instance).conservative_classifications


class AcceptedThenDelayedFillAdapter(FakeAdapter):
    def __init__(self):
        super().__init__(entry_mode="pending")
        self.entry_reads = 0

    async def get_equity_orders(self):
        if self.phase != "entry":
            return await super().get_equity_orders()
        self._record("get_equity_orders")
        self.entry_reads += 1
        if self.entry_reads == 1:
            return (
                order(
                    "entry-1",
                    side="buy",
                    state="queued",
                    cumulative=Decimal("0"),
                    remaining=Decimal("1"),
                    average=None,
                ),
            )
        self.entry_mode = "filled"
        return (order("entry-1", side="buy"),)

    async def get_equity_positions(self):
        if self.phase != "entry":
            return await super().get_equity_positions()
        self._record("get_equity_positions")
        return () if self.entry_reads == 1 else super_position()


def test_accepted_pending_entry_can_fill_on_later_read_only_poll(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, AcceptedThenDelayedFillAdapter()
    )
    assert run(instance).state is State.COMPLETE
    assert adapter.write_roles.count("entry") == 1
    assert adapter.entry_reads >= 2
    assert "ENTRY_PLACE_ACCEPTED_PENDING" in (
        evidence(instance).conservative_classifications
    )
    assert handoffs == []


def test_position_seen_before_unique_order_is_not_ignored(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, AmbiguousEntryAdapter("delayed")
    )
    assert run(instance).state is State.COMPLETE
    assert adapter.write_roles.count("entry") == 1
    assert "ENTRY_RECONCILED_BY_POSITION" in (
        evidence(instance).conservative_classifications
    )
    assert evidence(instance).boolean_validations[
        "entry_attribution_unique"
    ] is True
    assert handoffs == []


def test_unique_active_order_is_adopted_but_fill_timeout_never_retries(
    tmp_path,
):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, AmbiguousEntryAdapter("pending")
    )
    assert run(instance).state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == ["entry"]
    assert "ENTRY_RECONCILED_BY_ORDER" in (
        evidence(instance).conservative_classifications
    )
    assert handoffs[-1].reason_code == "ENTRY_FILL_DEADLINE_EXCEEDED"


def test_multiple_matching_orders_fail_closed_without_another_write(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, AmbiguousEntryAdapter("multiple")
    )
    assert run(instance).state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == ["entry"]
    assert handoffs[-1].reason_code == (
        "ENTRY_RECONCILIATION_MULTIPLE_MATCHES"
    )
    assert evidence(instance).boolean_validations[
        "entry_attribution_unique"
    ] is False


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("not_found", "ENTRY_RECONCILIATION_NOT_FOUND"),
        ("position_only", "ENTRY_RECONCILIATION_INCONSISTENT"),
        ("conflicting_position", "ENTRY_RECONCILIATION_INCONSISTENT"),
    ],
)
def test_unattributable_entry_times_out_without_retry(
    tmp_path, scenario, expected
):
    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path, AmbiguousEntryAdapter(scenario)
    )
    assert run(instance).state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == ["entry"]
    assert len(clock.sleeps) == 30
    assert expected in evidence(instance).conservative_classifications
    assert "ENTRY_RECONCILIATION_TIMEOUT" in (
        evidence(instance).conservative_classifications
    )
    assert handoffs[-1].reason_code == "ENTRY_RECONCILIATION_TIMEOUT"


def test_repaired_evidence_is_sanitized(tmp_path):
    instance, _, _, _ = make_orchestrator(
        tmp_path, AmbiguousEntryAdapter("filled"),
        run_id="redaction-check",
    )
    result = run(instance)
    rendered = result.evidence_path.read_text(encoding="utf-8").lower()
    for prohibited in (
        "account_number",
        "order_id",
        "fingerprint",
        "raw_response",
    ):
        assert prohibited not in rendered
    parsed = json.loads(rendered)
    assert parsed["write_attempt_counters"] == {
        "entry_place": 1,
        "stop_place": 1,
        "stop_cancel": 1,
        "exit_place": 1,
    }
