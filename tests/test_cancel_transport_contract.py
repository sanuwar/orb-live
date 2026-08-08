"""Broker-free coverage for the cancel argument contract and cancel diagnostics.

Every session here is a local fake. Nothing in this module reaches a network,
an MCP server, or a broker.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.one_share_commissioning import (
    ALLOWED_CANCEL_DIAGNOSTICS,
    AtomicEvidenceStore,
    CommissioningError,
    State,
    coarse_duration_bucket,
    validate_sanitized_evidence,
)
from src.one_share_commissioning_orchestrator import (
    OneShareCommissioningOrchestrator,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    BrokerRejectedError,
    RobinhoodCommissioningAdapter,
)
from tests.execution_safety_test_support import allow_decision
from tests.test_ambiguous_stop_reconciliation import (
    Clock,
    NOW,
    POLICY,
    authorization,
)
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
)

ADVERTISED_CANCEL_INPUT_FIELDS = frozenset({"account_number", "order_id"})
STOP_ID = "stop-fixture"


class RecordingSession:
    """Capture the exact tool arguments the adapter transmits."""

    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def adapter(session):
    return RobinhoodCommissioningAdapter(session, "transient")


def cancel_envelope(accepted=True):
    return SimpleNamespace(
        structuredContent={
            "data": {"accepted": accepted},
            "guide": "call get_equity_orders for the final state",
        }
    )


def cancel(session):
    cancel_args = {"order_id": STOP_ID}
    return asyncio.run(
        adapter(session).cancel_equity_order(
            cancel_args,
            safety_decision=allow_decision(role="cancel", arguments=cancel_args),
        )
    )


# --- argument contract -------------------------------------------------


def test_cancel_transmits_every_advertised_required_input_field():
    session = RecordingSession([cancel_envelope(True)])
    cancel(session)

    name, arguments = session.calls[0]
    assert name == "cancel_equity_order"
    assert set(arguments) == ADVERTISED_CANCEL_INPUT_FIELDS
    assert arguments["order_id"] == STOP_ID
    assert arguments["account_number"] == "transient"


def test_cancel_account_comes_from_the_resolved_identity_not_the_caller():
    session = RecordingSession([cancel_envelope(True)])
    instance = RobinhoodCommissioningAdapter(session, "resolved-identity")
    resolved_cancel_args = {"order_id": STOP_ID}
    asyncio.run(instance.cancel_equity_order(
        resolved_cancel_args,
        safety_decision=allow_decision(role="cancel", arguments=resolved_cancel_args),
    ))

    assert session.calls[0][1]["account_number"] == "resolved-identity"
    for supplied in ({"order_id": STOP_ID, "account_number": "attacker"},
                     {"account_number": "attacker"}, {}):
        with pytest.raises(CommissioningError):
            asyncio.run(
                RobinhoodCommissioningAdapter(
                    RecordingSession([cancel_envelope(True)]), "resolved-identity"
                ).cancel_equity_order(supplied)
            )


@pytest.mark.parametrize("order_id", ["", "   ", None, 7, b"x"])
def test_cancel_requires_one_non_empty_string_order_identifier(order_id):
    session = RecordingSession([cancel_envelope(True)])
    with pytest.raises(CommissioningError):
        asyncio.run(adapter(session).cancel_equity_order({"order_id": order_id}))
    assert session.calls == []


# --- response handling -------------------------------------------------


def test_documented_success_envelope_is_accepted():
    parsed = cancel(RecordingSession([cancel_envelope(True)]))
    assert parsed.request_was_accepted is True


def test_accepted_false_is_preserved_and_never_becomes_a_cancellation():
    parsed = cancel(RecordingSession([cancel_envelope(False)]))
    assert parsed.request_was_accepted is False


@pytest.mark.parametrize(
    "structured",
    [
        {},
        {"data": {}, "guide": "g"},
        {"data": {"accepted": "true"}, "guide": "g"},
        {"data": {"accepted": 1}, "guide": "g"},
        {"data": {"accepted": None}, "guide": "g"},
        {"data": {"accepted": True, "extra": 1}, "guide": "g"},
        {"data": {"accepted": True}, "guide": "g", "surprise": 1},
    ],
)
def test_empty_and_malformed_envelopes_are_ambiguous_not_cancelled(structured):
    session = RecordingSession([SimpleNamespace(structuredContent=structured)])
    with pytest.raises(AmbiguousWriteError) as caught:
        cancel(session)
    assert caught.value.category in {
        "STOP_CANCEL_RESPONSE_MALFORMED", "STOP_CANCEL_TRANSPORT_AMBIGUOUS",
    }
    assert caught.value.call_returned is True


def test_explicit_broker_rejection_is_distinct_from_ambiguity():
    session = RecordingSession([SimpleNamespace(isError=True)])
    with pytest.raises(BrokerRejectedError):
        cancel(session)


@pytest.mark.parametrize(
    "error,classification",
    [
        (RuntimeError("private transport text"), "RuntimeError"),
        (TimeoutError(), "TimeoutError"),
        (OSError("private socket text"), "OSError"),
        (ValueError("private validation text"), "ValueError"),
    ],
)
def test_transport_failures_are_classified_without_retaining_text(
    error, classification
):
    session = RecordingSession(error=error)
    with pytest.raises(AmbiguousWriteError) as caught:
        cancel(session)

    assert caught.value.category == "STOP_CANCEL_TRANSPORT_AMBIGUOUS"
    assert caught.value.transport_classification == classification
    assert caught.value.call_returned is False
    assert caught.value.response_shape == "not_reached"
    assert "private" not in str(caught.value)


def test_task_cancellation_propagates_and_is_never_read_as_a_cancellation():
    """asyncio.CancelledError is a BaseException; swallowing it would both
    break structured cancellation and manufacture a write conclusion."""
    session = RecordingSession(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        cancel(session)
    assert len(session.calls) == 1


def test_a_missing_guide_never_manufactures_an_accepted_value():
    """The adapter supplies its own guide, so prose absence is tolerated, but
    the accepted boolean itself is still required from the broker."""
    for structured in ({"data": {"accepted": False}}, {"accepted": False}):
        parsed = cancel(
            RecordingSession([SimpleNamespace(structuredContent=structured)])
        )
        assert parsed.request_was_accepted is False


def test_transport_failure_still_consumes_the_single_write_reservation():
    session = RecordingSession(error=RuntimeError("boom"))
    instance = adapter(session)
    reservation_args = {"order_id": STOP_ID}
    decision = allow_decision(role="cancel", arguments=reservation_args)
    with pytest.raises(AmbiguousWriteError):
        asyncio.run(instance.cancel_equity_order(
            reservation_args, safety_decision=decision
        ))
    with pytest.raises(CommissioningError, match="only once"):
        asyncio.run(instance.cancel_equity_order(
            reservation_args, safety_decision=decision
        ))
    assert len(session.calls) == 1


# --- diagnostics -------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,bucket",
    [
        (0.0, "under_1_second"), (0.999, "under_1_second"),
        (1.0, "1_to_5_seconds"), (4.99, "1_to_5_seconds"),
        (5.0, "5_to_15_seconds"), (15.0, "5_to_15_seconds"),
        (15.01, "over_15_seconds"), (None, "unknown"), (-1.0, "unknown"),
    ],
)
def test_elapsed_duration_is_recorded_only_as_a_coarse_bucket(seconds, bucket):
    assert coarse_duration_bucket(seconds) == bucket


def test_diagnostic_keys_and_values_are_strictly_allowlisted(tmp_path):
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="diagnostic-allowlist")
    machine = instance.machine
    machine.evidence_store.start()
    for key in ("order_id", "account_number", "raw_response", "unknown_key"):
        with pytest.raises(CommissioningError, match="key is invalid"):
            machine.record_cancel_diagnostic(key, "true")
    for value in ("stop-fixture", "maybe", "", None, 1):
        with pytest.raises(CommissioningError, match="value is invalid"):
            machine.record_cancel_diagnostic("cancel_call_returned", value)
    for key, allowed in ALLOWED_CANCEL_DIAGNOSTICS.items():
        for value in allowed:
            machine.record_cancel_diagnostic(key, value)


def evidence_of(result):
    return json.loads(result.evidence_path.read_text(encoding="utf-8"))


class SubmittedButAmbiguousCancelAdapter(FakeAdapter):
    """The broker received the cancel; only the local response was lost.

    This is the case the live incidents did NOT exhibit: here the order does
    progress to cancelled, so bounded reads reach a terminal conclusion.
    """

    async def cancel_equity_order(self, arguments, *, safety_decision=None):
        self._record("cancel_equity_order")
        self.write_roles.append("cancel")
        self.phase = "cancel"
        raise AmbiguousWriteError(
            "STOP_CANCEL_TRANSPORT_AMBIGUOUS",
            transport_classification="RuntimeError",
            response_shape="not_reached",
            call_returned=False,
        )


class NeverSubmittedCancelAdapter(FakeAdapter):
    """The live incident shape: the request never reached the broker, so the
    protective stop stays working through every bounded read."""

    async def cancel_equity_order(self, arguments, *, safety_decision=None):
        self._record("cancel_equity_order")
        self.write_roles.append("cancel")
        raise AmbiguousWriteError(
            "STOP_CANCEL_TRANSPORT_AMBIGUOUS",
            transport_classification="RuntimeError",
            response_shape="not_reached",
            call_returned=False,
        )


def test_conclusive_cancel_records_returned_diagnostics_and_verifies_flat(tmp_path):
    instance, adapter_used, _, handoffs = make_orchestrator(
        tmp_path, adapter=FakeAdapter(cancel_mode="pending_then_cancelled"),
        run_id="cancel-conclusive",
    )
    result = asyncio.run(instance.run())
    diagnostics = evidence_of(result)["cancel_diagnostics"]

    assert result.state is State.COMPLETE
    assert diagnostics["cancel_call_started"] == "true"
    assert diagnostics["cancel_call_returned"] == "true"
    assert diagnostics["cancel_transport_classification"] == "NONE"
    assert diagnostics["cancel_response_shape_classification"] == "documented_envelope"
    assert diagnostics["cancel_response_contained_accepted"] == "true"
    assert diagnostics["cancel_accepted_value"] == "true"
    assert "cancel_task_cancelled" not in diagnostics
    assert diagnostics["cancel_timeout_layer"] == "none"
    assert diagnostics["session_healthy_after_cancel"] == "true"
    assert adapter_used.write_roles == ["entry", "stop", "cancel", "exit"]
    assert handoffs == []


def test_ambiguous_cancel_then_delayed_cancellation_still_exits_once(tmp_path):
    instance, adapter_used, _, handoffs = make_orchestrator(
        tmp_path,
        adapter=SubmittedButAmbiguousCancelAdapter(
            cancel_mode="pending_then_cancelled"
        ),
        run_id="cancel-ambiguous-then-cancelled",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)
    diagnostics = evidence["cancel_diagnostics"]

    assert result.state is State.COMPLETE
    assert diagnostics["cancel_call_returned"] == "false"
    assert diagnostics["cancel_transport_classification"] != "NONE"
    assert diagnostics["cancel_response_shape_classification"] == "not_reached"
    assert diagnostics["session_healthy_after_cancel"] == "unknown"
    assert evidence["write_attempt_counters"]["stop_cancel"] == 1
    assert evidence["write_attempt_counters"]["exit_place"] == 1
    assert evidence["record_counts"]["stop_cancel_retries"] == 0
    assert adapter_used.write_roles.count("cancel") == 1
    assert adapter_used.write_roles.count("exit") == 1
    assert handoffs == []


def test_repeated_live_failure_shape_reaches_operator_with_no_exit(tmp_path):
    instance, adapter_used, _, handoffs = make_orchestrator(
        tmp_path, adapter=NeverSubmittedCancelAdapter(cancel_mode="still_active"),
        run_id="cancel-still-active",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)

    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["failure_category"] == "STOP_CANCEL_TIMEOUT"
    assert evidence["write_attempt_counters"] == {
        "entry_place": 1, "stop_place": 1, "stop_cancel": 1, "exit_place": 0,
    }
    assert evidence["record_counts"]["stop_cancel_retries"] == 0
    assert evidence["cancel_diagnostics"]["cancel_timeout_layer"] == (
        "reconciliation_deadline"
    )
    assert "exit" not in adapter_used.write_roles
    assert adapter_used.write_roles.count("cancel") == 1
    assert len(handoffs) == 1


def test_stop_fills_during_reconciliation_produces_zero_exit_attempts(tmp_path):
    instance, adapter_used, _, handoffs = make_orchestrator(
        tmp_path, adapter=FakeAdapter(cancel_mode="stop_fills"),
        run_id="cancel-stop-fills",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)

    assert result.state is State.COMPLETE
    assert evidence["boolean_validations"]["verified_flat"] is True
    assert evidence["write_attempt_counters"]["exit_place"] == 0
    assert "exit" not in adapter_used.write_roles
    assert handoffs == []


def test_session_closes_exactly_once_after_an_ambiguous_cancel(tmp_path):
    from tests.test_checkpoint_7c_incident_regression import (
        actual_binding, read_sanitized,
    )

    binding, factory, _, store = actual_binding(
        tmp_path,
        secret_reader=lambda prompt: "y",
        run_id="cancel-session-closure",
        adapter=NeverSubmittedCancelAdapter(cancel_mode="still_active"),
    )
    result = asyncio.run(binding.run())
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")

    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["failure_category"] == "STOP_CANCEL_TIMEOUT"
    assert evidence["write_attempt_counters"]["exit_place"] == 0
    assert factory.created == 1
    assert factory.entered == 1
    assert factory.closed == 1


def test_session_stays_usable_for_bounded_reads_after_cancel_ambiguity(tmp_path):
    adapter_used = NeverSubmittedCancelAdapter(cancel_mode="still_active")
    instance, _, _, _ = make_orchestrator(
        tmp_path, adapter=adapter_used, run_id="cancel-reads-continue",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)

    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["record_counts"]["cancel_reconciliation_orders_reads"] > 1
    assert evidence["record_counts"]["cancel_reconciliation_positions_reads"] > 1
    assert adapter_used.calls.count("cancel_equity_order") == 1


def test_cancel_diagnostics_never_contain_raw_or_private_values(tmp_path):
    instance, _, _, _ = make_orchestrator(
        tmp_path, adapter=NeverSubmittedCancelAdapter(cancel_mode="still_active"),
        run_id="cancel-diagnostic-sanitized",
    )
    result = asyncio.run(instance.run())
    rendered = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(rendered)

    validate_sanitized_evidence(evidence)
    for prohibited in (STOP_ID, "transient", "private", "raw", "boom",
                       "entry-fixture"):
        assert prohibited not in rendered
    for key, value in evidence["cancel_diagnostics"].items():
        assert value in ALLOWED_CANCEL_DIAGNOSTICS[key]
