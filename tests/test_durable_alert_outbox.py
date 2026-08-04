from __future__ import annotations

import ast
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.durable_alert_outbox as outbox_module
from src.durable_alert_outbox import (
    AcknowledgmentStatus,
    AlertCategory,
    AlertIntent,
    AlertReason,
    AlertSeverity,
    AlertState,
    AttemptBucket,
    DeliveryEvidence,
    DeliveryAttestationVerifier,
    DeliveryProvenance,
    DurableAlertOutbox,
    OutboxError,
    OutboxPolicy,
    intent_from_liveness,
    intent_from_status,
    intent_from_suspension,
)
from src.durable_runner_liveness import LivenessReason, LivenessState
from src.durable_scheduler import SchedulerAction
from src.durable_suspension_controller import (
    ControllerState,
    SuspensionCause,
    SuspensionDecision,
)
from src.execution_gates import ACTIVATION_APPROVALS
from src.read_only_broker_state import approved_active_protection_states
from tests.test_operational_status_report import context, inputs, liveness as make_liveness, project_status


NOW = datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)
MODULE_PATH = Path(outbox_module.__file__)


def policy(**changes) -> OutboxPolicy:
    values = dict(
        claim_timeout=timedelta(minutes=5),
        maximum_evidence_age=timedelta(minutes=2),
        maximum_attempts=3,
    )
    values.update(changes)
    return OutboxPolicy(**values)


class TestVerifier:
    verifier_id = "verifier-test"

    def verify(self, selected: DeliveryEvidence) -> bool:
        return selected.evidence_id.startswith("delivery-evidence-")


def open_outbox(tmp_path: Path, selected: OutboxPolicy | None = None, verifier=None) -> DurableAlertOutbox:
    return DurableAlertOutbox(
        (tmp_path / "outbox.sqlite").resolve(), selected or policy(), verifier or TestVerifier(),
    )


def intent(alert_id: str = "alert-one", category: AlertCategory = AlertCategory.STATUS_DEGRADED) -> AlertIntent:
    return AlertIntent(
        alert_id, category, AlertSeverity.WARNING, AlertReason.STATUS_PROJECTION,
        NOW, "correlation-one",
    )


def created(outbox: DurableAlertOutbox, selected: AlertIntent | None = None):
    return outbox.create(selected or intent(), transition_id="alert-transition-create")


def claimed(outbox: DurableAlertOutbox):
    created(outbox)
    outbox.ready("alert-one", transition_id="alert-transition-ready", now=NOW + timedelta(seconds=1))
    return outbox.claim(
        "alert-one", claim_id="claim-one", transition_id="alert-transition-claim",
        now=NOW + timedelta(seconds=2),
    )


def evidence(*, delivered: bool = True, provenance: DeliveryProvenance = DeliveryProvenance.VERIFIED_ATTESTATION,
             observed_at: datetime | None = None, evidence_id: str = "delivery-evidence-one",
             alert_id: str = "alert-one", claim_id: str = "claim-one") -> DeliveryEvidence:
    return DeliveryEvidence(
        evidence_id, alert_id, claim_id, observed_at or NOW + timedelta(seconds=3),
        delivered, provenance,
    )


def test_all_normal_states_and_immutable_history(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    assert created(outbox).state is AlertState.CREATED
    assert outbox.ready("alert-one", transition_id="alert-transition-ready", now=NOW + timedelta(seconds=1)).state is AlertState.READY
    record = outbox.claim("alert-one", claim_id="claim-one", transition_id="alert-transition-claim", now=NOW + timedelta(seconds=2))
    assert record.state is AlertState.CLAIMED
    assert record.attempt_bucket is AttemptBucket.ONE
    delivered = outbox.submit_delivery(evidence(), transition_id="alert-transition-delivered", now=NOW + timedelta(seconds=4))
    assert delivered.state is AlertState.DELIVERED_CONFIRMED
    assert delivered.acknowledgment_status is AcknowledgmentStatus.CONFIRMED
    history = outbox.transition_history("alert-one")
    assert [item.to_state for item in history] == [
        AlertState.CREATED, AlertState.READY, AlertState.CLAIMED, AlertState.DELIVERED_CONFIRMED,
    ]
    assert [item.sequence for item in history] == [1, 2, 3, 4]
    outbox.close()


def test_missing_confirmation_is_delivery_unconfirmed_not_success(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    record = outbox.submit_delivery(
        evidence(delivered=False), transition_id="alert-transition-unconfirmed",
        now=NOW + timedelta(seconds=4),
    )
    assert record.state is AlertState.DELIVERY_UNCONFIRMED
    assert record.acknowledgment_status is AcknowledgmentStatus.UNCONFIRMED
    outbox.close()


@pytest.mark.parametrize("provenance", [DeliveryProvenance.UNVERIFIED, DeliveryProvenance.SELF_DECLARED])
def test_delivered_without_verified_attestation_fails_closed(tmp_path: Path, provenance: DeliveryProvenance) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    record = outbox.submit_delivery(
        evidence(provenance=provenance), transition_id="alert-transition-invalid-evidence",
        now=NOW + timedelta(seconds=4),
    )
    assert record.state is AlertState.FAILED_CLOSED
    assert record.reason is AlertReason.DELIVERY_EVIDENCE_INVALID
    outbox.close()


def test_future_and_stale_delivery_evidence_fail_closed(tmp_path: Path) -> None:
    future_box = open_outbox(tmp_path / "future")
    claimed(future_box)
    future = future_box.submit_delivery(
        evidence(observed_at=NOW + timedelta(minutes=1)),
        transition_id="alert-transition-future", now=NOW + timedelta(seconds=4),
    )
    assert future.state is AlertState.FAILED_CLOSED
    future_box.close()
    stale_box = open_outbox(tmp_path / "stale")
    claimed(stale_box)
    stale = stale_box.submit_delivery(
        evidence(observed_at=NOW + timedelta(seconds=3)),
        transition_id="alert-transition-stale", now=NOW + timedelta(minutes=3),
    )
    assert stale.state is AlertState.FAILED_CLOSED
    assert stale.reason is AlertReason.DELIVERY_EVIDENCE_STALE
    stale_box.close()


def test_preclaim_delivery_evidence_fails_closed(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    result = outbox.submit_delivery(
        evidence(observed_at=NOW + timedelta(seconds=1)),
        transition_id="alert-transition-preclaim", now=NOW + timedelta(seconds=4),
    )
    assert result.state is AlertState.FAILED_CLOSED
    assert result.reason is AlertReason.DELIVERY_EVIDENCE_INVALID
    outbox.close()


def test_verified_label_alone_does_not_bypass_trusted_verifier(tmp_path: Path) -> None:
    class DenyingVerifier:
        verifier_id = "verifier-denying"

        def verify(self, selected: DeliveryEvidence) -> bool:
            return False

    outbox = open_outbox(tmp_path, verifier=DenyingVerifier())
    claimed(outbox)
    result = outbox.submit_delivery(
        evidence(), transition_id="alert-transition-denied", now=NOW + timedelta(seconds=4),
    )
    assert result.state is AlertState.FAILED_CLOSED
    assert result.reason is AlertReason.DELIVERY_EVIDENCE_INVALID
    outbox.close()


def test_stale_claim_fails_closed_and_restart_preserves_it(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.close()
    restarted = open_outbox(tmp_path)
    record = restarted.claim(
        "alert-one", claim_id="claim-two", transition_id="alert-transition-stale-claim",
        now=NOW + timedelta(minutes=6),
    )
    assert record.state is AlertState.FAILED_CLOSED
    assert record.reason is AlertReason.CLAIM_STALE
    restarted.close()


def test_restart_preserves_claim_and_delivery_state(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.close()
    restarted = open_outbox(tmp_path)
    assert restarted.load("alert-one").claim_id == "claim-one"
    restarted.submit_delivery(evidence(), transition_id="alert-transition-delivered", now=NOW + timedelta(seconds=4))
    restarted.close()
    final = open_outbox(tmp_path)
    assert final.load("alert-one").state is AlertState.DELIVERED_CONFIRMED
    final.close()


def test_changed_policy_cannot_reinterpret_restart(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    created(outbox)
    outbox.close()
    with pytest.raises(OutboxError, match="initialization"):
        open_outbox(tmp_path, policy(claim_timeout=timedelta(hours=1)))


def test_changed_verifier_identity_cannot_reinterpret_restart(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    created(outbox)
    outbox.close()

    class OtherVerifier(TestVerifier):
        verifier_id = "verifier-other"

    with pytest.raises(OutboxError, match="initialization"):
        open_outbox(tmp_path, verifier=OtherVerifier())


def test_maximum_attempts_fails_closed_without_hidden_retry(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path, policy(maximum_attempts=1))
    claimed(outbox)
    outbox.submit_delivery(
        evidence(delivered=False), transition_id="alert-transition-unconfirmed",
        now=NOW + timedelta(seconds=4),
    )
    outbox.ready("alert-one", transition_id="alert-transition-retry-ready", now=NOW + timedelta(seconds=5))
    result = outbox.claim(
        "alert-one", claim_id="claim-two", transition_id="alert-transition-max-attempts",
        now=NOW + timedelta(seconds=6),
    )
    assert result.state is AlertState.FAILED_CLOSED
    assert result.attempt_count == 1
    outbox.close()


def test_duplicate_alert_claim_and_transition_identities_are_rejected(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    created(outbox)
    with pytest.raises(OutboxError):
        created(outbox)
    outbox.ready("alert-one", transition_id="alert-transition-ready", now=NOW + timedelta(seconds=1))
    outbox.claim("alert-one", claim_id="claim-one", transition_id="alert-transition-claim", now=NOW + timedelta(seconds=2))
    with pytest.raises(OutboxError):
        outbox.claim("alert-one", claim_id="claim-one", transition_id="alert-transition-other", now=NOW + timedelta(seconds=3))
    outbox.close()


def test_claim_identity_cannot_bind_to_second_alert(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.create(intent("alert-two"), transition_id="alert-transition-create-two")
    outbox.ready("alert-two", transition_id="alert-transition-ready-two", now=NOW + timedelta(seconds=1))
    with pytest.raises(OutboxError, match="spent"):
        outbox.claim("alert-two", claim_id="claim-one", transition_id="alert-transition-claim-two", now=NOW + timedelta(seconds=2))
    outbox.close()


def test_wrong_alert_or_claim_binding_is_rejected_without_state_change(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    with pytest.raises(OutboxError):
        outbox.submit_delivery(
            evidence(claim_id="claim-other"), transition_id="alert-transition-wrong",
            now=NOW + timedelta(seconds=4),
        )
    assert outbox.load("alert-one").state is AlertState.CLAIMED
    outbox.close()


def test_conflicting_confirmation_downgrades_delivered_to_failed_closed(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.submit_delivery(evidence(), transition_id="alert-transition-delivered", now=NOW + timedelta(seconds=4))
    conflict = outbox.submit_delivery(
        evidence(delivered=False), transition_id="alert-transition-conflict",
        now=NOW + timedelta(seconds=5),
    )
    assert conflict.state is AlertState.FAILED_CLOSED
    assert conflict.reason is AlertReason.CONFLICTING_CONFIRMATION
    outbox.close()


def test_fresh_identity_conflicting_confirmation_fails_closed(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.submit_delivery(evidence(), transition_id="alert-transition-delivered", now=NOW + timedelta(seconds=4))
    conflict = outbox.submit_delivery(
        evidence(delivered=False, evidence_id="delivery-evidence-conflict"),
        transition_id="alert-transition-conflict-fresh", now=NOW + timedelta(seconds=5),
    )
    assert conflict.state is AlertState.FAILED_CLOSED
    assert conflict.reason is AlertReason.CONFLICTING_CONFIRMATION
    outbox.close()


def test_explicit_retry_after_unconfirmed_preserves_attempt_history(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.submit_delivery(
        evidence(delivered=False), transition_id="alert-transition-unconfirmed",
        now=NOW + timedelta(seconds=4),
    )
    ready = outbox.ready("alert-one", transition_id="alert-transition-retry-ready", now=NOW + timedelta(seconds=5))
    assert ready.claim_id is None
    retried = outbox.claim(
        "alert-one", claim_id="claim-two", transition_id="alert-transition-retry-claim",
        now=NOW + timedelta(seconds=6),
    )
    assert retried.attempt_count == 2
    assert retried.attempt_bucket is AttemptBucket.MANY
    outbox.close()


def test_identical_evidence_replay_is_rejected_without_new_transition(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.submit_delivery(evidence(), transition_id="alert-transition-delivered", now=NOW + timedelta(seconds=4))
    with pytest.raises(OutboxError, match="duplicated"):
        outbox.submit_delivery(evidence(), transition_id="alert-transition-replay", now=NOW + timedelta(seconds=5))
    assert outbox.load("alert-one").transition_sequence == 4
    outbox.close()


def test_expired_alert_cannot_become_delivered(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    assert outbox.expire("alert-one", transition_id="alert-transition-expire", now=NOW + timedelta(seconds=4)).state is AlertState.EXPIRED
    with pytest.raises(OutboxError):
        outbox.submit_delivery(evidence(), transition_id="alert-transition-late", now=NOW + timedelta(seconds=5))
    assert outbox.load("alert-one").state is AlertState.EXPIRED
    outbox.close()


def test_non_suspension_can_be_cancelled_but_suspension_cannot_be_discarded(tmp_path: Path) -> None:
    ordinary = open_outbox(tmp_path / "ordinary")
    created(ordinary)
    assert ordinary.cancel_by_safety("alert-one", transition_id="alert-transition-cancel", now=NOW + timedelta(seconds=1)).state is AlertState.CANCELLED_BY_SAFETY
    ordinary.close()
    suspension = open_outbox(tmp_path / "suspension")
    created(suspension, intent(category=AlertCategory.SUSPENSION))
    with pytest.raises(OutboxError, match="cannot be discarded"):
        suspension.cancel_by_safety("alert-one", transition_id="alert-transition-cancel", now=NOW + timedelta(seconds=1))
    with pytest.raises(OutboxError, match="cannot be discarded"):
        suspension.expire("alert-one", transition_id="alert-transition-expire", now=NOW + timedelta(seconds=1))
    suspension.close()


def test_terminal_failed_state_cannot_be_overwritten(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.submit_delivery(
        evidence(provenance=DeliveryProvenance.SELF_DECLARED),
        transition_id="alert-transition-fail", now=NOW + timedelta(seconds=4),
    )
    with pytest.raises(OutboxError):
        outbox.ready("alert-one", transition_id="alert-transition-overwrite", now=NOW + timedelta(seconds=5))
    assert outbox.load("alert-one").state is AlertState.FAILED_CLOSED
    outbox.close()


def test_mutable_summary_tamper_is_detected_by_history(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    created(outbox)
    with sqlite3.connect(outbox.database_path) as connection:
        connection.execute("UPDATE alerts SET state='DELIVERED_CONFIRMED' WHERE alert_id='alert-one'")
    with pytest.raises(OutboxError, match="history"):
        outbox.load("alert-one")
    outbox.close()


def test_deleted_delivery_evidence_invalidates_confirmed_state(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    outbox.submit_delivery(evidence(), transition_id="alert-transition-delivered", now=NOW + timedelta(seconds=4))
    with sqlite3.connect(outbox.database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DELETE FROM delivery_evidence WHERE evidence_id='delivery-evidence-one'")
    with pytest.raises(OutboxError, match="evidence history"):
        outbox.load("alert-one")
    outbox.close()


def test_transition_reason_and_spent_claim_tamper_are_detected(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    claimed(outbox)
    with sqlite3.connect(outbox.database_path) as connection:
        connection.execute(
            "UPDATE alert_transitions SET resulting_reason='CLAIM_STALE' WHERE sequence=2"
        )
    with pytest.raises(OutboxError, match="history"):
        outbox.load("alert-one")
    outbox.close()


def test_transaction_failure_rolls_back_transition_and_spent_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outbox = open_outbox(tmp_path)
    created(outbox)
    original = outbox._insert_transition
    monkeypatch.setattr(outbox, "_insert_transition", lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.Error("PRIVATE token")))
    with pytest.raises(OutboxError):
        outbox.ready("alert-one", transition_id="alert-transition-ready", now=NOW + timedelta(seconds=1))
    monkeypatch.setattr(outbox, "_insert_transition", original)
    assert outbox.load("alert-one").state is AlertState.CREATED
    assert outbox.ready("alert-one", transition_id="alert-transition-ready", now=NOW + timedelta(seconds=1)).state is AlertState.READY
    outbox.close()


def test_persistence_errors_are_sanitized_at_begin_and_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    begin_box = open_outbox(tmp_path / "begin")
    created(begin_box)
    begin_box.close()
    with pytest.raises(OutboxError) as begin_error:
        begin_box.ready("alert-one", transition_id="alert-transition-ready", now=NOW + timedelta(seconds=1))
    assert "PRIVATE" not in str(begin_error.value)

    commit_box = open_outbox(tmp_path / "commit")
    created(commit_box)
    monkeypatch.setattr(commit_box, "_commit", lambda: (_ for _ in ()).throw(sqlite3.Error("PRIVATE account")))
    with pytest.raises(OutboxError) as commit_error:
        commit_box.ready("alert-one", transition_id="alert-transition-ready", now=NOW + timedelta(seconds=1))
    assert "PRIVATE" not in str(commit_error.value)
    assert commit_box.load("alert-one").state is AlertState.CREATED
    commit_box.close()


@pytest.mark.parametrize(
    "fragment",
    ["account", "credential", "email", "order", "phone", "private", "secret", "symbol", "token", "username", "wallet", "webhook"],
)
def test_private_database_path_is_rejected(tmp_path: Path, fragment: str) -> None:
    with pytest.raises(OutboxError):
        DurableAlertOutbox(
            (tmp_path.parent / f"wo2-{fragment}" / "outbox.sqlite").resolve(), policy(), TestVerifier(),
        )


def test_database_symlink_cannot_escape_into_repository_data(tmp_path: Path) -> None:
    alias = tmp_path / "safe-alias"
    try:
        alias.symlink_to(Path("data").resolve(), target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(OutboxError):
        DurableAlertOutbox(alias / "outbox.sqlite", policy(), TestVerifier())


def test_persistence_schema_contains_only_allowlisted_fixed_fields(tmp_path: Path) -> None:
    outbox = open_outbox(tmp_path)
    created(outbox)
    columns = {
        row[1]
        for table in ("alerts", "alert_transitions", "delivery_evidence", "spent_identities")
        for row in outbox._connection.execute(f"PRAGMA table_info({table})")
    }
    prohibited = {"text", "symbol", "account", "order_id", "quantity", "price", "balance", "exception", "email", "phone", "webhook", "token", "credential", "username", "path"}
    assert columns.isdisjoint(prohibited)
    assert outbox.load("alert-one").permits_automated_action is False
    outbox.close()


@pytest.mark.parametrize("value", ["alert-account-123", "alert-user_name", "alert-a/b", "bad"])
def test_private_or_malformed_alert_identity_is_rejected(value: str) -> None:
    with pytest.raises(OutboxError):
        replace(intent(), alert_id=value)


def test_public_typed_status_liveness_and_suspension_adapters() -> None:
    status = project_status(context(), inputs(suspension=SuspensionDecision(
        ControllerState.SUSPENDED, SuspensionCause.OPERATOR_HALT, True, SchedulerAction.SUSPEND
    )))
    assert intent_from_status(status, alert_id="alert-status", correlation_id="correlation-status", created_at=NOW).category is AlertCategory.SUSPENSION
    live = make_liveness(LivenessState.MISSED_CYCLE, LivenessReason.EXPECTED_TICK_MISSED)
    assert intent_from_liveness(live, alert_id="alert-live", correlation_id="correlation-live", created_at=NOW).category is AlertCategory.MISSED_CYCLE
    decision = SuspensionDecision(ControllerState.SUSPENDED, SuspensionCause.OPERATOR_HALT, True, SchedulerAction.SUSPEND)
    assert intent_from_suspension(decision, alert_id="alert-suspension", correlation_id="correlation-suspension", created_at=NOW).category is AlertCategory.SUSPENSION


def test_arbitrary_dictionaries_are_not_public_contracts() -> None:
    with pytest.raises(OutboxError):
        intent_from_status({}, alert_id="alert-status", correlation_id="correlation-status", created_at=NOW)  # type: ignore[arg-type]
    with pytest.raises(OutboxError):
        intent_from_liveness({}, alert_id="alert-live", correlation_id="correlation-live", created_at=NOW)  # type: ignore[arg-type]


def test_no_network_retry_clock_manifest_broker_or_clearance_surface() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint({"requests", "httpx", "socket", "smtplib", "subprocess", "slack_sdk", "twilio", "robin_stocks"})
    assert not any(isinstance(node, ast.While) for node in ast.walk(tree))
    lowered = source.lower()
    for forbidden in ("datetime.now", "datetime.utcnow", "time.time", "time.monotonic", ".sleep(", "authorization_manifest", "clear_suspension", "activation_approvals", "webhook_url"):
        assert forbidden not in lowered


def test_true_automated_permission_is_impossible() -> None:
    with pytest.raises(OutboxError):
        replace(intent(), permits_automated_action=True)
    with pytest.raises(OutboxError):
        replace(evidence(), permits_automated_action=True)


def test_global_safety_invariants_remain_closed() -> None:
    assert ACTIVATION_APPROVALS == frozenset()
    assert approved_active_protection_states() == frozenset()
    assert "enabled: false" in Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
