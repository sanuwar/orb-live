"""Broker-free coverage for bounded read-only reconciliation of one ambiguous exit.

Every adapter here is a local fake. Nothing reaches a broker, an MCP server, or
the network. The invariant under test in every scenario is that exactly one exit
write is ever attempted, whatever the outcome.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from src.one_share_commissioning import (
    ALLOWED_EXIT_DIAGNOSTICS,
    State,
    validate_sanitized_evidence,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    CommissioningPositionRecord,
)
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
    order,
)


def evidence_of(result):
    return json.loads(result.evidence_path.read_text(encoding="utf-8"))


def exit_order(state, *, order_id="exit-1", cumulative=Decimal("1"),
               remaining=Decimal("0"), average=Decimal("190")):
    return order(order_id, side="sell", state=state, cumulative=cumulative,
                 remaining=remaining, average=average)


class AmbiguousExitAdapter(FakeAdapter):
    """Place the exit, lose the response, then reveal a chosen book state.

    ``exit_scenario`` decides what the bounded reads observe afterwards.
    """

    def __init__(self, exit_scenario, **kwargs):
        super().__init__(**kwargs)
        self.exit_scenario = exit_scenario
        self.exit_reads = 0

    async def place_equity_order(self, arguments, *, role, safety_decision=None):
        if role != "exit":
            return await super().place_equity_order(
                arguments, role=role, safety_decision=safety_decision
            )
        self._record("place_equity_order")
        self.write_roles.append("exit")
        # The sell reached the broker; only the local response was lost.
        self.phase = "exit"
        raise AmbiguousWriteError(
            "EXIT_PLACE_RESPONSE_MALFORMED",
            transport_classification="ValueError",
            response_shape="malformed_envelope",
            call_returned=True,
        )

    async def get_equity_positions(self):
        if self.phase != "exit":
            return await super().get_equity_positions()
        self._record("get_equity_positions")
        held = self.exit_scenario in {
            "pending_forever", "cancelled_share_remains", "conflicting_sell",
            "not_found_share_remains",
        }
        return (
            (CommissioningPositionRecord("AAPL", Decimal("1")),) if held else ()
        )

    async def get_equity_orders(self):
        if self.phase != "exit":
            return await super().get_equity_orders()
        self._record("get_equity_orders")
        self.exit_reads += 1
        entry = order("entry-1", side="buy")
        stop = order("stop-1", side="sell", order_type="stop_market",
                     state="cancelled", cumulative=Decimal("0"),
                     remaining=Decimal("1"), stop_price=Decimal("185"),
                     average=None)
        scenario = self.exit_scenario
        if scenario == "filled":
            return entry, stop, exit_order("filled")
        if scenario == "pending_then_filled":
            if self.exit_reads == 1:
                return entry, stop, exit_order(
                    "confirmed", cumulative=Decimal("0"),
                    remaining=Decimal("1"), average=None,
                )
            return entry, stop, exit_order("filled")
        if scenario == "pending_forever":
            return entry, stop, exit_order(
                "confirmed", cumulative=Decimal("0"), remaining=Decimal("1"),
                average=None,
            )
        if scenario == "cancelled_share_remains":
            return entry, stop, exit_order(
                "cancelled", cumulative=Decimal("0"), remaining=Decimal("1"),
                average=None,
            )
        if scenario == "flat_no_executable_sell":
            # Flat, and the only new order is untraceable to this run.
            return entry, stop
        if scenario == "not_found_share_remains":
            return entry, stop
        if scenario == "duplicate_sells":
            return (entry, stop, exit_order("filled"),
                    exit_order("filled", order_id="exit-2"))
        if scenario == "conflicting_sell":
            return (entry, stop, exit_order("filled"),
                    order("mystery-1", side="buy", state="confirmed",
                          cumulative=Decimal("0"), remaining=Decimal("1"),
                          average=None))
        raise AssertionError(f"unknown exit scenario {scenario}")


def run_scenario(tmp_path, scenario, run_id):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=AmbiguousExitAdapter(scenario), run_id=run_id,
    )
    result = asyncio.run(instance.run())
    return result, adapter, handoffs, evidence_of(result)


def assert_exactly_one_exit(adapter, evidence):
    assert adapter.write_roles.count("exit") == 1
    assert adapter.calls.count("place_equity_order") == 3
    assert evidence["write_attempt_counters"]["exit_place"] == 1
    assert evidence["write_attempt_counters"]["entry_place"] == 1
    assert evidence["write_attempt_counters"]["stop_place"] == 1
    assert evidence["write_attempt_counters"]["stop_cancel"] == 1
    assert evidence["record_counts"]["stop_cancel_retries"] == 0


# --- resolves safely --------------------------------------------------


def test_ambiguous_exit_then_filled_and_flat_verifies_flat(tmp_path):
    result, adapter, handoffs, evidence = run_scenario(
        tmp_path, "filled", "exit-ambiguous-filled",
    )
    diagnostics = evidence["exit_diagnostics"]

    assert result.state is State.COMPLETE
    assert evidence["boolean_validations"]["verified_flat"] is True
    assert "EXIT_RECONCILED_BY_ORDER" in evidence["conservative_classifications"]
    assert diagnostics["exit_call_started"] == "true"
    assert diagnostics["exit_call_returned"] == "true"
    assert diagnostics["exit_response_shape_classification"] == "malformed_envelope"
    assert diagnostics["exit_uniquely_attributed"] == "true"
    assert diagnostics["exit_state_classification"] == "filled"
    assert diagnostics["position_flat_observed"] == "true"
    assert evidence["record_counts"]["exit_reconciliation_orders_reads"] >= 1
    assert evidence["record_counts"]["exit_reconciliation_positions_reads"] >= 1
    assert_exactly_one_exit(adapter, evidence)
    assert handoffs == []


def test_ambiguous_exit_then_flat_with_no_executable_sell_verifies_flat(tmp_path):
    result, adapter, handoffs, evidence = run_scenario(
        tmp_path, "flat_no_executable_sell", "exit-ambiguous-flat",
    )
    diagnostics = evidence["exit_diagnostics"]

    assert result.state is State.COMPLETE
    assert evidence["boolean_validations"]["verified_flat"] is True
    assert (
        "EXIT_RECONCILED_BY_FLAT_POSITION"
        in evidence["conservative_classifications"]
    )
    # Flatness alone did not decide it: attribution is explicitly unproven and
    # the order book had to show nothing that could still sell.
    assert diagnostics["exit_uniquely_attributed"] == "false"
    assert diagnostics["exit_state_classification"] == "not_observed"
    assert diagnostics["position_flat_observed"] == "true"
    assert_exactly_one_exit(adapter, evidence)
    assert handoffs == []


def test_ambiguous_exit_pending_then_filled_verifies_flat(tmp_path):
    result, adapter, handoffs, evidence = run_scenario(
        tmp_path, "pending_then_filled", "exit-ambiguous-pending-filled",
    )

    assert result.state is State.COMPLETE
    assert "EXIT_STILL_WORKING" in evidence["conservative_classifications"]
    assert "EXIT_RECONCILED_BY_ORDER" in evidence["conservative_classifications"]
    assert evidence["record_counts"]["exit_reconciliation_orders_reads"] >= 2
    assert_exactly_one_exit(adapter, evidence)
    assert handoffs == []


# --- fails closed -----------------------------------------------------


@pytest.mark.parametrize(
    "scenario,run_id,category",
    [
        ("pending_forever", "exit-pending-deadline", "EXIT_RECONCILIATION_TIMEOUT"),
        ("cancelled_share_remains", "exit-cancelled", "EXIT_RECONCILIATION_INCONSISTENT"),
        ("not_found_share_remains", "exit-not-found", "EXIT_RECONCILIATION_TIMEOUT"),
        ("duplicate_sells", "exit-duplicates", "EXIT_RECONCILIATION_MULTIPLE_MATCHES"),
        ("conflicting_sell", "exit-conflicting", "EXIT_RECONCILIATION_CONFLICTING_ORDER"),
    ],
)
def test_unresolved_ambiguous_exit_requires_operator_without_a_second_sell(
    tmp_path, scenario, run_id, category
):
    result, adapter, handoffs, evidence = run_scenario(tmp_path, scenario, run_id)

    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["failure_category"] == category
    assert_exactly_one_exit(adapter, evidence)
    assert len(handoffs) == 1
    assert evidence["boolean_validations"].get("verified_flat") is not True


def test_attribution_failure_is_recorded_as_not_unique(tmp_path):
    _, _, _, evidence = run_scenario(
        tmp_path, "duplicate_sells", "exit-duplicates-diagnostic",
    )
    assert evidence["exit_diagnostics"]["exit_uniquely_attributed"] == "false"
    assert evidence["record_counts"]["exit_reconciliation_candidate_count"] == 2


# --- unchanged paths --------------------------------------------------


def test_normal_exit_path_is_unchanged(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=FakeAdapter(), run_id="exit-normal-path",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)

    assert result.state is State.COMPLETE
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert evidence["write_attempt_counters"]["exit_place"] == 1
    assert evidence["exit_diagnostics"]["exit_call_returned"] == "true"
    assert (
        evidence["exit_diagnostics"]["exit_response_shape_classification"]
        == "documented_envelope"
    )
    assert evidence["exit_diagnostics"]["exit_timeout_layer"] == "none"
    assert handoffs == []


def test_stop_filled_and_flat_still_places_zero_exit_orders(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=FakeAdapter(cancel_mode="stop_fills"),
        run_id="exit-stop-filled",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)

    assert result.state is State.COMPLETE
    assert evidence["boolean_validations"]["verified_flat"] is True
    assert evidence["write_attempt_counters"]["exit_place"] == 0
    assert "exit" not in adapter.write_roles
    assert handoffs == []


def test_exit_diagnostics_are_allowlisted_and_carry_no_private_values(tmp_path):
    instance, _, _, _ = make_orchestrator(
        tmp_path, adapter=AmbiguousExitAdapter("filled"),
        run_id="exit-diagnostic-sanitized",
    )
    result = asyncio.run(instance.run())
    rendered = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(rendered)

    validate_sanitized_evidence(evidence)
    for prohibited in ("exit-1", "entry-1", "stop-1", "transient", "190", "185"):
        assert prohibited not in rendered
    for key, value in evidence["exit_diagnostics"].items():
        assert value in ALLOWED_EXIT_DIAGNOSTICS[key]


def test_exit_diagnostic_keys_and_values_are_strictly_allowlisted(tmp_path):
    from src.one_share_commissioning import CommissioningError

    instance, _, _, _ = make_orchestrator(tmp_path, run_id="exit-diagnostic-allowlist")
    machine = instance.machine
    machine.evidence_store.start()
    for key in ("order_id", "account_number", "raw_response", "nope"):
        with pytest.raises(CommissioningError, match="key is invalid"):
            machine.record_exit_diagnostic(key, "true")
    for value in ("exit-1", "maybe", "", None, 1):
        with pytest.raises(CommissioningError, match="value is invalid"):
            machine.record_exit_diagnostic("exit_call_returned", value)
    for key, allowed in ALLOWED_EXIT_DIAGNOSTICS.items():
        for value in allowed:
            machine.record_exit_diagnostic(key, value)
