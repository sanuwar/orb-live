"""Diagnostic fidelity for the cancel path: a diagnostic must not claim an
observation this code cannot make.

Three narrow properties are pinned here:

  * task cancellation is propagated, never classified, and never recorded;
  * a cancel call that raised did not necessarily time out;
  * an explicit broker rejection is classified as itself, not as unknown.

Every session and adapter in this module is a local fake. Nothing reaches a
network, an MCP server, or a broker.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.one_share_commissioning import (
    ALLOWED_CANCEL_DIAGNOSTICS,
    RECOGNIZED_TIMEOUT_CLASSIFICATIONS,
    SAFE_EXCEPTION_TYPES,
    TERMINAL_REJECTION_CLASSIFICATIONS,
    CommissioningError,
    State,
    cancel_timeout_layer,
    sanitized_exception_type,
    validate_sanitized_evidence,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    BrokerRejectedError,
    RobinhoodCommissioningAdapter,
)
from tests.test_cancel_transport_contract import (
    RecordingSession,
    STOP_ID,
    cancel,
)
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
)


def evidence_of(result):
    return json.loads(result.evidence_path.read_text(encoding="utf-8"))


class RaisingCancelAdapter(FakeAdapter):
    """Fail the cancel write with a chosen sanitized classification.

    ``advance_phase=False`` reproduces the live-incident shape: the request
    never reached the broker, so the protective stop keeps reading as working
    and bounded reconciliation runs to its deadline.
    """

    def __init__(self, *, classification, advance_phase=True, **kwargs):
        super().__init__(**kwargs)
        self.classification = classification
        self.advance_phase = advance_phase

    async def cancel_equity_order(self, arguments):
        self._record("cancel_equity_order")
        self.write_roles.append("cancel")
        if self.advance_phase:
            self.phase = "cancel"
        raise AmbiguousWriteError(
            "STOP_CANCEL_TRANSPORT_AMBIGUOUS",
            transport_classification=self.classification,
            response_shape="not_reached",
            call_returned=False,
        )


class RejectedCancelAdapter(FakeAdapter):
    """The broker explicitly refused the cancel request."""

    async def cancel_equity_order(self, arguments):
        self._record("cancel_equity_order")
        self.write_roles.append("cancel")
        self.phase = "cancel"
        raise BrokerRejectedError("broker operation was rejected")


# --- task cancellation is propagated, never recorded -------------------


def test_cancelled_error_still_propagates_unchanged():
    """asyncio.CancelledError derives from BaseException. Converting it into a
    write ambiguity would break structured cancellation and manufacture a
    conclusion about a write whose outcome is unknown."""
    session = RecordingSession(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        cancel(session)
    assert len(session.calls) == 1


def test_cancelled_error_is_not_a_classifiable_exception_type():
    assert "CancelledError" not in SAFE_EXCEPTION_TYPES
    assert sanitized_exception_type(asyncio.CancelledError()) == (
        "UNKNOWN_EXCEPTION"
    )


def test_cancel_task_cancelled_can_never_be_recorded(tmp_path):
    """The key is gone from the allowlist, so the impossible observation cannot
    be written even by a future caller that tries."""
    assert "cancel_task_cancelled" not in ALLOWED_CANCEL_DIAGNOSTICS
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="fidelity-no-key")
    instance.machine.evidence_store.start()
    for value in ("true", "false", "unknown"):
        with pytest.raises(CommissioningError, match="key is invalid"):
            instance.machine.record_cancel_diagnostic(
                "cancel_task_cancelled", value
            )


def test_no_run_records_a_task_cancelled_diagnostic(tmp_path):
    instance, _, _, _ = make_orchestrator(
        tmp_path,
        adapter=RaisingCancelAdapter(
            classification="RuntimeError", cancel_mode="pending_then_cancelled"
        ),
        run_id="fidelity-absent-key",
    )
    diagnostics = evidence_of(asyncio.run(instance.run()))["cancel_diagnostics"]
    assert "cancel_task_cancelled" not in diagnostics


# --- a raised call is not automatically a timeout ----------------------


@pytest.mark.parametrize(
    "classification",
    ["RuntimeError", "OSError", "ValueError", "TypeError", "UNKNOWN_EXCEPTION"],
)
def test_non_timeout_failures_do_not_claim_a_timeout_layer(classification):
    assert cancel_timeout_layer(
        returned=False, classification=classification
    ) == "not_applicable"


def test_recognized_timeout_names_the_transport_layer():
    assert RECOGNIZED_TIMEOUT_CLASSIFICATIONS == frozenset({"TimeoutError"})
    assert cancel_timeout_layer(
        returned=False, classification="TimeoutError"
    ) == "transport"


def test_a_returned_call_reports_no_timeout_at_any_layer():
    for classification in ("NONE", "TimeoutError", "RuntimeError"):
        assert cancel_timeout_layer(
            returned=True, classification=classification
        ) == "none"


def test_contract_rejection_shape_records_not_applicable_end_to_end(tmp_path):
    """The argument-contract mismatch that caused both live incidents raised
    without timing out. It must not be recorded as a timeout."""
    instance, adapter_used, _, _ = make_orchestrator(
        tmp_path,
        adapter=RaisingCancelAdapter(
            classification="RuntimeError", cancel_mode="pending_then_cancelled"
        ),
        run_id="fidelity-not-applicable",
    )
    evidence = evidence_of(asyncio.run(instance.run()))
    diagnostics = evidence["cancel_diagnostics"]

    assert diagnostics["cancel_timeout_layer"] == "not_applicable"
    assert diagnostics["cancel_call_returned"] == "false"
    assert diagnostics["cancel_transport_classification"] == "RuntimeError"
    assert evidence["write_attempt_counters"]["stop_cancel"] == 1
    assert evidence["record_counts"]["stop_cancel_retries"] == 0
    assert adapter_used.write_roles.count("cancel") == 1


def test_transport_timeout_records_the_transport_layer_end_to_end(tmp_path):
    instance, adapter_used, _, _ = make_orchestrator(
        tmp_path,
        adapter=RaisingCancelAdapter(
            classification="TimeoutError", cancel_mode="pending_then_cancelled"
        ),
        run_id="fidelity-transport-timeout",
    )
    evidence = evidence_of(asyncio.run(instance.run()))
    diagnostics = evidence["cancel_diagnostics"]

    assert diagnostics["cancel_timeout_layer"] == "transport"
    assert diagnostics["cancel_transport_classification"] == "TimeoutError"
    assert evidence["write_attempt_counters"]["stop_cancel"] == 1
    assert evidence["record_counts"]["stop_cancel_retries"] == 0
    assert adapter_used.write_roles.count("cancel") == 1


def test_reconciliation_deadline_still_overrides_the_transport_verdict(tmp_path):
    """A cancel that never reached the broker records not_applicable first, then
    the reconciliation deadline names the layer that actually expired."""
    instance, adapter_used, _, handoffs = make_orchestrator(
        tmp_path,
        adapter=RaisingCancelAdapter(
            classification="RuntimeError", cancel_mode="still_active",
            advance_phase=False,
        ),
        run_id="fidelity-reconciliation-deadline",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)

    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["failure_category"] == "STOP_CANCEL_TIMEOUT"
    assert evidence["cancel_diagnostics"]["cancel_timeout_layer"] == (
        "reconciliation_deadline"
    )
    assert evidence["write_attempt_counters"]["exit_place"] == 0
    assert evidence["record_counts"]["stop_cancel_retries"] == 0
    assert adapter_used.write_roles.count("cancel") == 1
    assert "exit" not in adapter_used.write_roles
    assert len(handoffs) == 1


# --- explicit rejection is classified as itself ------------------------


def test_broker_rejection_has_an_explicit_safe_classification():
    assert "BrokerRejectedError" in SAFE_EXCEPTION_TYPES
    assert sanitized_exception_type(
        BrokerRejectedError("broker operation was rejected")
    ) == "BrokerRejectedError"
    assert "BrokerRejectedError" in ALLOWED_CANCEL_DIAGNOSTICS[
        "cancel_transport_classification"
    ]


def test_rejected_cancel_records_broker_rejected_not_unknown(tmp_path):
    instance, adapter_used, _, handoffs = make_orchestrator(
        tmp_path,
        adapter=RejectedCancelAdapter(cancel_mode="still_active"),
        run_id="fidelity-broker-rejected",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)
    diagnostics = evidence["cancel_diagnostics"]

    assert result.state is State.OPERATOR_REQUIRED
    assert diagnostics["cancel_transport_classification"] == "BrokerRejectedError"
    assert diagnostics["cancel_transport_classification"] != "UNKNOWN_EXCEPTION"
    assert handoffs[-1].reason_code == "STOP_CANCEL_REJECTED"
    assert evidence["write_attempt_counters"]["stop_cancel"] == 1
    # A rejected cancel never enters reconciliation, so the retry counter is
    # never initialized. Absent and zero both mean no retry occurred.
    assert evidence["record_counts"].get("stop_cancel_retries", 0) == 0
    assert adapter_used.write_roles.count("cancel") == 1
    assert "exit" not in adapter_used.write_roles


def test_rejected_cancel_has_its_own_response_shape(tmp_path):
    """An explicit rejection is not a documented envelope.

    The adapter detects the rejection from the error flag on the result, before
    any payload field is read, so no cancel envelope is ever parsed. Recording
    ``documented_envelope`` would claim a well-formed cancel response arrived
    when none did; ``malformed_envelope`` would claim one arrived and failed
    parsing; ``not_reached`` would deny that the transport returned at all.
    """
    instance, _, _, _ = make_orchestrator(
        tmp_path,
        adapter=RejectedCancelAdapter(cancel_mode="still_active"),
        run_id="fidelity-rejected-shape",
    )
    diagnostics = evidence_of(asyncio.run(instance.run()))["cancel_diagnostics"]

    assert diagnostics["cancel_response_shape_classification"] == "broker_rejected"
    assert diagnostics["cancel_response_shape_classification"] not in {
        "documented_envelope", "malformed_envelope", "not_reached", "unknown",
        "empty_result",
    }
    # The transport did return; only the content was a refusal.
    assert diagnostics["cancel_call_started"] == "true"
    assert diagnostics["cancel_call_returned"] == "true"
    assert diagnostics["session_healthy_after_cancel"] == "true"


def test_rejected_cancel_is_not_a_timeout_at_any_layer(tmp_path):
    instance, _, _, _ = make_orchestrator(
        tmp_path,
        adapter=RejectedCancelAdapter(cancel_mode="still_active"),
        run_id="fidelity-rejected-timeout-layer",
    )
    diagnostics = evidence_of(asyncio.run(instance.run()))["cancel_diagnostics"]

    assert diagnostics["cancel_timeout_layer"] == "not_applicable"
    assert diagnostics["cancel_timeout_layer"] not in {
        "transport", "reconciliation_deadline",
    }


def test_terminal_rejection_never_names_a_timeout_layer():
    """A rejection is terminal whichever way the call is described: no deadline
    governed it and no reconciliation follows, so "none" - which on the success
    path means "no timeout yet" - would be the wrong frame."""
    assert TERMINAL_REJECTION_CLASSIFICATIONS == frozenset({"BrokerRejectedError"})
    for returned in (True, False):
        assert cancel_timeout_layer(
            returned=returned, classification="BrokerRejectedError"
        ) == "not_applicable"


def test_rejected_cancel_claims_no_accepted_field_it_did_not_observe(tmp_path):
    """The rejection short-circuits before the payload is inspected, so whether
    an ``accepted`` field was present is genuinely unobserved. "false" would
    assert the response was read and lacked the field; only "unknown" is
    truthful, and no accepted value may be reported either way."""
    instance, _, _, _ = make_orchestrator(
        tmp_path,
        adapter=RejectedCancelAdapter(cancel_mode="still_active"),
        run_id="fidelity-rejected-accepted",
    )
    diagnostics = evidence_of(asyncio.run(instance.run()))["cancel_diagnostics"]

    assert diagnostics["cancel_response_contained_accepted"] == "unknown"
    assert diagnostics["cancel_accepted_value"] == "unknown"
    assert diagnostics["cancel_accepted_value"] != "true"


def test_rejected_cancel_starts_no_reconciliation_and_no_exit(tmp_path):
    """An explicit rejection is terminal, so bounded cancel reconciliation is
    never entered, the stop is left executable, and no sell follows."""
    instance, adapter_used, _, handoffs = make_orchestrator(
        tmp_path,
        adapter=RejectedCancelAdapter(cancel_mode="still_active"),
        run_id="fidelity-rejected-no-reconciliation",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)
    counts = evidence["record_counts"]

    assert result.state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == "STOP_CANCEL_REJECTED"
    assert handoffs[-1].stop_may_remain_executable is True
    assert counts.get("cancel_reconciliation_orders_reads", 0) == 0
    assert counts.get("cancel_reconciliation_positions_reads", 0) == 0
    assert counts.get("stop_cancel_retries", 0) == 0
    assert evidence["write_attempt_counters"]["stop_cancel"] == 1
    assert evidence["write_attempt_counters"]["exit_place"] == 0
    assert adapter_used.calls.count("cancel_equity_order") == 1
    assert adapter_used.write_roles == ["entry", "stop", "cancel"]


def test_rejected_cancel_closes_the_session_exactly_once(tmp_path):
    from tests.test_checkpoint_7c_incident_regression import (
        actual_binding, read_sanitized,
    )

    binding, factory, _, store = actual_binding(
        tmp_path,
        secret_reader=lambda prompt: "y",
        run_id="fidelity-rejected-session-close",
        adapter=RejectedCancelAdapter(cancel_mode="still_active"),
    )
    result = asyncio.run(binding.run())
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")

    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["cancel_diagnostics"][
        "cancel_response_shape_classification"
    ] == "broker_rejected"
    assert evidence["write_attempt_counters"]["exit_place"] == 0
    assert factory.created == 1
    assert factory.entered == 1
    assert factory.closed == 1


def test_rejection_classification_retains_no_broker_text():
    error = BrokerRejectedError("private broker refusal text")
    assert sanitized_exception_type(error) == "BrokerRejectedError"
    assert "private" not in sanitized_exception_type(error)


# --- nothing raw reaches evidence --------------------------------------


@pytest.mark.parametrize(
    "adapter_factory,run_id",
    [
        (
            lambda: RaisingCancelAdapter(
                classification="RuntimeError", cancel_mode="still_active",
                advance_phase=False,
            ),
            "fidelity-sanitized-raised",
        ),
        (
            lambda: RaisingCancelAdapter(
                classification="TimeoutError", cancel_mode="still_active",
                advance_phase=False,
            ),
            "fidelity-sanitized-timeout",
        ),
        (
            lambda: RejectedCancelAdapter(cancel_mode="still_active"),
            "fidelity-sanitized-rejected",
        ),
    ],
)
def test_no_raw_values_reach_evidence(tmp_path, adapter_factory, run_id):
    instance, _, _, _ = make_orchestrator(
        tmp_path, adapter=adapter_factory(), run_id=run_id
    )
    result = asyncio.run(instance.run())
    rendered = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(rendered)

    validate_sanitized_evidence(evidence)
    for prohibited in (
        STOP_ID, "stop-1", "entry-1", "transient", "private", "raw",
        "refusal", "broker operation was rejected",
    ):
        assert prohibited not in rendered
    for key, value in evidence["cancel_diagnostics"].items():
        assert value in ALLOWED_CANCEL_DIAGNOSTICS[key]


def test_every_diagnostic_value_stays_inside_its_allowlist(tmp_path):
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="fidelity-allowlist")
    instance.machine.evidence_store.start()
    machine = instance.machine
    for key, allowed in ALLOWED_CANCEL_DIAGNOSTICS.items():
        for value in allowed:
            machine.record_cancel_diagnostic(key, value)
        for rejected in ("stop-1", "transient", "maybe", ""):
            if rejected in allowed:
                continue
            with pytest.raises(CommissioningError, match="value is invalid"):
                machine.record_cancel_diagnostic(key, rejected)
