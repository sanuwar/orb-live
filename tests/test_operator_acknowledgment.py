from __future__ import annotations

import ast
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.operator_acknowledgment as module
from src.durable_alert_outbox import (
    AcknowledgmentStatus as AlertAcknowledgmentStatus,
    AlertCategory,
    AlertReason,
    AlertRecord,
    AlertSeverity,
    AlertState,
    AttemptBucket,
)
from src.durable_scheduler import RunState, ScheduledRun, SchedulerAction, SchedulerDecision, SchedulerReason, SchedulerTransitionReceipt
from src.durable_suspension_controller import ControllerRecord, ControllerState, EvidenceClass, SuspensionCause, SuspensionDecision
from src.execution_gates import ACTIVATION_APPROVALS
from src.operator_acknowledgment import (
    AcknowledgmentCategory,
    AcknowledgmentDecision,
    AcknowledgmentDisposition,
    AcknowledgmentError,
    AcknowledgmentReason,
    AcknowledgmentRequest,
    AcknowledgmentState,
    AcknowledgmentSourceKind,
    AttestedProvenance,
    OperatorAcknowledgmentArtifact,
    OperatorAcknowledgmentStore,
    request_from_alert,
    request_from_scheduler,
    request_from_status,
    request_from_suspension,
)
from src.read_only_broker_state import approved_active_protection_states
from tests.test_operational_status_report import context, inputs, project_status


NOW = datetime(2026, 8, 4, 16, 0, tzinfo=timezone.utc)
MODULE_PATH = Path(module.__file__)


class TestVerifier:
    verifier_id = "ack-verifier-test"

    def verify(self, artifact: OperatorAcknowledgmentArtifact) -> bool:
        return artifact.artifact_id.startswith("ack-artifact-")


def store(tmp_path: Path, verifier=None) -> OperatorAcknowledgmentStore:
    return OperatorAcknowledgmentStore((tmp_path / "ack.sqlite").resolve(), verifier or TestVerifier())


def request(
    acknowledgment_id: str = "ack-one",
    review_item_id: str = "review-item-one",
    category: AcknowledgmentCategory = AcknowledgmentCategory.STATUS_DEGRADED,
    sequence: int = 3,
) -> AcknowledgmentRequest:
    return AcknowledgmentRequest(
        acknowledgment_id, review_item_id, sequence, category, NOW,
        NOW + timedelta(hours=1), "correlation-one",
        AcknowledgmentSourceKind.REVIEW_ITEM, f"source-{review_item_id}", sequence,
    )


def artifact(**changes) -> OperatorAcknowledgmentArtifact:
    values = dict(
        artifact_id="ack-artifact-one", acknowledgment_id="ack-one",
        review_item_id="review-item-one", review_sequence=3,
        category=AcknowledgmentCategory.STATUS_DEGRADED,
        operator_reviewed=True, provenance=AttestedProvenance.VERIFIED_ATTESTATION,
        signature_present=True, issued_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=30),
    )
    values.update(changes)
    return OperatorAcknowledgmentArtifact(**values)


def created(selected: OperatorAcknowledgmentStore, item: AcknowledgmentRequest | None = None):
    return selected.create(item or request(), transition_id="ack-transition-create")


def test_verified_acknowledgment_records_awareness_only_and_survives_restart(tmp_path: Path) -> None:
    selected = store(tmp_path)
    assert created(selected).state is AcknowledgmentState.PENDING
    result = selected.acknowledge(
        artifact(), transition_id="ack-transition-aware", now=NOW + timedelta(minutes=2),
    )
    assert result.record.state is AcknowledgmentState.ACKNOWLEDGED
    assert result.record.reason is AcknowledgmentReason.OPERATOR_AWARE
    assert result.acknowledgment_obligation_closed is True
    assert result.route_to_further_review is True
    assert result.clears_suspension is False
    assert result.activation_approved is False
    assert result.protection_grounded is False
    assert result.opens_execution_gate is False
    assert result.permits_automated_action is False
    selected.close()
    restarted = store(tmp_path)
    assert restarted.load("ack-one") == result.record
    with pytest.raises(AcknowledgmentError, match="terminal"):
        restarted.acknowledge(artifact(), transition_id="ack-transition-replay", now=NOW + timedelta(minutes=3))
    restarted.close()


@pytest.mark.parametrize("category", list(AcknowledgmentCategory))
def test_every_category_starts_pending(tmp_path: Path, category: AcknowledgmentCategory) -> None:
    selected = store(tmp_path / category.value.lower())
    assert created(selected, request(category=category)).category is category
    selected.close()


def test_expired_state_is_durable(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    decision = selected.expire("ack-one", transition_id="ack-transition-expire", now=NOW + timedelta(hours=2))
    assert decision.record.state is AcknowledgmentState.EXPIRED
    assert decision.acknowledgment_obligation_closed is False
    assert decision.route_to_further_review is True
    selected.close()
    restarted = store(tmp_path)
    assert restarted.load("ack-one").state is AcknowledgmentState.EXPIRED
    restarted.close()


@pytest.mark.parametrize(
    ("disposition", "method", "state"),
    [
        (AcknowledgmentDisposition.REJECT, "reject", AcknowledgmentState.REJECTED),
        (AcknowledgmentDisposition.SUPERSEDE, "supersede", AcknowledgmentState.SUPERSEDED),
    ],
)
def test_rejection_and_supersession_require_verified_durable_artifacts(tmp_path: Path, disposition: AcknowledgmentDisposition, method: str, state: AcknowledgmentState) -> None:
    selected = store(tmp_path)
    created(selected)
    changes = {"disposition": disposition}
    if disposition is AcknowledgmentDisposition.SUPERSEDE:
        changes.update(successor_review_item_id="review-item-successor", successor_review_sequence=4)
    decision = getattr(selected, method)(
        artifact(**changes), transition_id=f"ack-transition-{method}", now=NOW + timedelta(minutes=2),
    )
    assert decision.record.state is state
    assert decision.record.requires_further_review is True
    if state is AcknowledgmentState.SUPERSEDED:
        assert decision.record.successor_review_item_id == "review-item-successor"
        assert decision.record.successor_review_sequence == 4
    selected.close()
    restarted = store(tmp_path)
    assert restarted.load("ack-one").state is state
    restarted.close()


def test_unverified_supersession_fails_closed_without_replacing_review_item(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    result = selected.supersede(
        artifact(
            disposition=AcknowledgmentDisposition.SUPERSEDE,
            successor_review_item_id="review-item-successor", successor_review_sequence=4,
            provenance=AttestedProvenance.SELF_DECLARED,
        ),
        transition_id="ack-transition-invalid-supersede", now=NOW + timedelta(minutes=2),
    )
    assert result.record.state is AcknowledgmentState.FAILED_CLOSED
    assert result.record.successor_review_item_id is None
    assert result.record.requires_further_review is True
    selected.close()


def test_expiry_before_deadline_is_rejected_without_state_change(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    with pytest.raises(AcknowledgmentError, match="not expired"):
        selected.expire("ack-one", transition_id="ack-transition-expire", now=NOW + timedelta(minutes=30))
    assert selected.load("ack-one").state is AcknowledgmentState.PENDING
    selected.close()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"review_item_id": "review-item-other"}, AcknowledgmentReason.IDENTITY_MISMATCH),
        ({"category": AcknowledgmentCategory.FAILED_CLOSED}, AcknowledgmentReason.IDENTITY_MISMATCH),
        ({"review_sequence": 4}, AcknowledgmentReason.SEQUENCE_MISMATCH),
        ({"issued_at": NOW + timedelta(minutes=3)}, AcknowledgmentReason.ARTIFACT_FUTURE_DATED),
        ({"issued_at": NOW - timedelta(minutes=1)}, AcknowledgmentReason.ARTIFACT_STALE),
        ({"expires_at": NOW + timedelta(seconds=90)}, AcknowledgmentReason.ARTIFACT_STALE),
        ({"expires_at": NOW + timedelta(minutes=2)}, AcknowledgmentReason.ARTIFACT_STALE),
    ],
)
def test_wrong_binding_stale_and_future_artifacts_fail_closed(tmp_path: Path, changes: dict, reason: AcknowledgmentReason) -> None:
    selected = store(tmp_path)
    created(selected)
    result = selected.acknowledge(
        artifact(**changes), transition_id="ack-transition-invalid", now=NOW + timedelta(minutes=2),
    )
    assert result.record.state is AcknowledgmentState.FAILED_CLOSED
    assert result.record.reason is reason
    assert result.acknowledgment_obligation_closed is False
    selected.close()
    restarted = store(tmp_path)
    assert restarted.load("ack-one").reason is reason
    restarted.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"provenance": AttestedProvenance.SELF_DECLARED},
        {"provenance": AttestedProvenance.UNVERIFIED},
        {"signature_present": False},
        {"operator_reviewed": False},
    ],
)
def test_self_declared_unsigned_unverified_or_unreviewed_artifact_fails_closed(tmp_path: Path, changes: dict) -> None:
    selected = store(tmp_path)
    created(selected)
    result = selected.acknowledge(
        artifact(**changes), transition_id="ack-transition-invalid", now=NOW + timedelta(minutes=2),
    )
    assert result.record.state is AcknowledgmentState.FAILED_CLOSED
    assert result.record.reason is AcknowledgmentReason.ARTIFACT_INVALID
    selected.close()


def test_verified_label_cannot_bypass_attestation_verifier(tmp_path: Path) -> None:
    class DenyingVerifier(TestVerifier):
        verifier_id = "ack-verifier-denying"

        def verify(self, artifact: OperatorAcknowledgmentArtifact) -> bool:
            return False

    selected = store(tmp_path, DenyingVerifier())
    created(selected)
    result = selected.acknowledge(artifact(), transition_id="ack-transition-denied", now=NOW + timedelta(minutes=2))
    assert result.record.state is AcknowledgmentState.FAILED_CLOSED
    selected.close()


def test_artifact_identity_cannot_be_reused_for_another_review_item(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    selected.acknowledge(artifact(), transition_id="ack-transition-aware", now=NOW + timedelta(minutes=2))
    selected.create(request("ack-two", "review-item-two"), transition_id="ack-transition-create-two")
    with pytest.raises(AcknowledgmentError, match="spent"):
        selected.acknowledge(
            artifact(acknowledgment_id="ack-two", review_item_id="review-item-two"),
            transition_id="ack-transition-aware-two", now=NOW + timedelta(minutes=2),
        )
    assert selected.load("ack-two").state is AcknowledgmentState.PENDING
    selected.close()


def test_duplicate_acknowledgment_or_review_item_identity_is_rejected(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    with pytest.raises(AcknowledgmentError):
        selected.create(request(), transition_id="ack-transition-duplicate")
    with pytest.raises(AcknowledgmentError):
        selected.create(request("ack-two"), transition_id="ack-transition-duplicate-two")
    selected.close()


def test_transition_identity_cannot_be_reused(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    with pytest.raises(AcknowledgmentError, match="spent"):
        selected.reject(
            artifact(disposition=AcknowledgmentDisposition.REJECT),
            transition_id="ack-transition-create", now=NOW + timedelta(minutes=1),
        )
    assert selected.load("ack-one").state is AcknowledgmentState.PENDING
    selected.close()


def test_superseded_item_cannot_be_acknowledged(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    selected.supersede(
        artifact(
            disposition=AcknowledgmentDisposition.SUPERSEDE,
            successor_review_item_id="review-item-successor", successor_review_sequence=4,
        ),
        transition_id="ack-transition-supersede", now=NOW + timedelta(minutes=2),
    )
    with pytest.raises(AcknowledgmentError, match="terminal"):
        selected.acknowledge(artifact(), transition_id="ack-transition-aware", now=NOW + timedelta(minutes=2))
    selected.close()


def test_deleted_artifact_and_mutated_summary_are_detected(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    selected.acknowledge(artifact(), transition_id="ack-transition-aware", now=NOW + timedelta(minutes=2))
    with sqlite3.connect(selected.database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DELETE FROM acknowledgment_artifacts")
    with pytest.raises(AcknowledgmentError, match="artifact history"):
        selected.load("ack-one")
    selected.close()

    other = store(tmp_path / "summary")
    created(other)
    with sqlite3.connect(other.database_path) as connection:
        connection.execute("UPDATE acknowledgments SET state='ACKNOWLEDGED' WHERE acknowledgment_id='ack-one'")
    with pytest.raises(AcknowledgmentError):
        other.load("ack-one")
    other.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("issued_at", (NOW + timedelta(minutes=8)).isoformat()),
        ("expires_at", (NOW + timedelta(minutes=2)).isoformat()),
    ],
)
def test_acknowledged_artifact_temporal_tamper_is_detected(tmp_path: Path, column: str, value: str) -> None:
    selected = store(tmp_path)
    created(selected)
    selected.acknowledge(artifact(), transition_id="ack-transition-aware", now=NOW + timedelta(minutes=2))
    with sqlite3.connect(selected.database_path) as connection:
        connection.execute(f"UPDATE acknowledgment_artifacts SET {column}=?", (value,))
    with pytest.raises(AcknowledgmentError, match="artifact history"):
        selected.load("ack-one")
    selected.close()


def test_partial_transaction_rolls_back_and_sanitizes_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = store(tmp_path)
    created(selected)
    monkeypatch.setattr(selected, "_commit", lambda: (_ for _ in ()).throw(sqlite3.Error("PRIVATE token")))
    with pytest.raises(AcknowledgmentError) as captured:
        selected.reject(
            artifact(disposition=AcknowledgmentDisposition.REJECT),
            transition_id="ack-transition-reject", now=NOW + timedelta(minutes=2),
        )
    assert "PRIVATE" not in str(captured.value)
    assert selected.load("ack-one").state is AcknowledgmentState.PENDING
    selected.close()


def test_verifier_identity_and_sequence_survive_restart(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    selected.close()
    restarted = store(tmp_path)
    assert restarted.load("ack-one").transition_sequence == 1
    restarted.close()

    class OtherVerifier(TestVerifier):
        verifier_id = "ack-verifier-other"

    with pytest.raises(AcknowledgmentError, match="initialization"):
        store(tmp_path, OtherVerifier())


def test_public_typed_integration_adapters() -> None:
    fields = dict(
        acknowledgment_id="ack-adapter", issued_at=NOW, expires_at=NOW + timedelta(hours=1),
        correlation_id="correlation-adapter",
    )
    from tests.test_operational_status_report import scheduled
    status = project_status(context(), inputs(scheduler=scheduled(RunState.READY_FOR_SUPERVISED_REVIEW)))
    assert request_from_status(status, **fields).category is AcknowledgmentCategory.STATUS_DEGRADED
    alert = AlertRecord(
        "alert-adapter", AlertCategory.SUSPENSION, AlertSeverity.CRITICAL,
        AlertReason.SUSPENSION_PROJECTION, AlertState.READY, NOW, NOW,
        "correlation-alert", None, None, 0, AttemptBucket.ZERO,
        AlertAcknowledgmentStatus.PENDING, 1, "alert-transition-adapter",
    )
    assert request_from_alert(alert, **fields).category is AcknowledgmentCategory.SUSPENSION_NOTICE
    suspension = SuspensionDecision(ControllerState.SUSPENDED, SuspensionCause.OPERATOR_HALT, True, SchedulerAction.SUSPEND)
    controller = ControllerRecord("controller-safe", ControllerState.SUSPENDED, SuspensionCause.OPERATOR_HALT, EvidenceClass.NONE, 4, False, NOW, NOW)
    assert request_from_suspension(suspension, controller, **fields).source_sequence == 4
    scheduler = SchedulerDecision(SchedulerAction.REQUIRE_OPERATOR_REVIEW, SchedulerReason.MONITORING_DELIVERY_UNCONFIRMED)
    run = ScheduledRun("run-safe", date(2026, 8, 4), RunState.READY_FOR_SUPERVISED_REVIEW, NOW, NOW)
    receipt = SchedulerTransitionReceipt("transition-safe", "run-safe", RunState.PRECONDITION_CHECK, RunState.READY_FOR_SUPERVISED_REVIEW, 5)
    scheduler_request = request_from_scheduler(scheduler, run, receipt, **fields)
    assert scheduler_request.category is AcknowledgmentCategory.ALERT_UNCONFIRMED
    assert scheduler_request.source_sequence == 5


def test_adapter_source_identity_and_sequence_cannot_be_overridden() -> None:
    fields = dict(
        acknowledgment_id="ack-adapter", issued_at=NOW, expires_at=NOW + timedelta(hours=1),
        correlation_id="correlation-adapter",
    )
    alert = AlertRecord(
        "alert-adapter", AlertCategory.SUSPENSION, AlertSeverity.CRITICAL,
        AlertReason.SUSPENSION_PROJECTION, AlertState.READY, NOW, NOW,
        "correlation-alert", None, None, 0, AttemptBucket.ZERO,
        AlertAcknowledgmentStatus.PENDING, 7, "alert-transition-adapter",
    )
    derived = request_from_alert(alert, **fields)
    assert derived.expected_review_sequence == 7
    assert derived.source_sequence == 7
    assert derived.review_item_id.endswith("alert-adapter")
    with pytest.raises(AcknowledgmentError, match="fields"):
        request_from_alert(alert, **fields, review_item_id="review-item-forged")

    suspension = SuspensionDecision(ControllerState.SUSPENDED, SuspensionCause.OPERATOR_HALT, True, SchedulerAction.SUSPEND)
    wrong_record = ControllerRecord("controller-safe", ControllerState.CLEARANCE_PENDING_REVIEW, SuspensionCause.OPERATOR_HALT, EvidenceClass.NONE, 4, False, NOW, NOW)
    with pytest.raises(AcknowledgmentError, match="conflicts"):
        request_from_suspension(suspension, wrong_record, **fields)


def test_direct_request_cannot_forge_unrelated_source_binding(tmp_path: Path) -> None:
    with pytest.raises(AcknowledgmentError, match="identity conflict"):
        replace(
            request(), source_kind=AcknowledgmentSourceKind.ALERT_OUTBOX,
            source_id="source-status-report-unrelated",
        )
    forged_values = dict(
        acknowledgment_id="ack-forged", review_item_id="review-item-unrelated",
        expected_review_sequence=7, category=AcknowledgmentCategory.STATUS_DEGRADED,
        issued_at=NOW, expires_at=NOW + timedelta(hours=1), correlation_id="correlation-forged",
        source_kind=AcknowledgmentSourceKind.ALERT_OUTBOX,
        source_id="source-alert-outbox-alert-other", source_sequence=7,
    )
    with pytest.raises(AcknowledgmentError, match="identity conflict"):
        AcknowledgmentRequest(**forged_values)


def test_arbitrary_dictionaries_are_not_integration_contracts() -> None:
    with pytest.raises(AcknowledgmentError):
        request_from_status({}, acknowledgment_id="ack-a")  # type: ignore[arg-type]
    with pytest.raises(AcknowledgmentError):
        request_from_alert({}, acknowledgment_id="ack-a")  # type: ignore[arg-type]
    with pytest.raises(AcknowledgmentError):
        request_from_suspension({}, {}, acknowledgment_id="ack-a")  # type: ignore[arg-type]
    with pytest.raises(AcknowledgmentError):
        request_from_scheduler({}, {}, {}, acknowledgment_id="ack-a")  # type: ignore[arg-type]


def test_schema_and_source_are_privacy_safe_and_capability_inert(tmp_path: Path) -> None:
    selected = store(tmp_path)
    created(selected)
    columns = {
        row[1]
        for table in ("acknowledgments", "acknowledgment_origins", "acknowledgment_transitions", "acknowledgment_artifacts", "acknowledgment_spent")
        for row in selected._connection.execute(f"PRAGMA table_info({table})")
    }
    prohibited = {"text", "account", "symbol", "quantity", "price", "balance", "order_id", "exception", "username", "credential", "manifest", "path"}
    assert columns.isdisjoint(prohibited)
    selected.close()
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint({"requests", "httpx", "socket", "smtplib", "subprocess", "robin_stocks"})
    lowered = source.lower()
    for forbidden in ("datetime.now", "datetime.utcnow", "time.time", "clear_suspension", "clearance_artifact", "activation_approvals", "running_offline_evaluation", "live_ready", "unattended_ready"):
        assert forbidden not in lowered


def test_true_permission_or_authorization_flags_are_impossible() -> None:
    with pytest.raises(AcknowledgmentError):
        replace(request(), permits_automated_action=True)
    with pytest.raises(AcknowledgmentError):
        replace(artifact(), permits_automated_action=True)
    selected_record = None
    with pytest.raises(AcknowledgmentError):
        AcknowledgmentDecision(selected_record, False, True, clears_suspension=True)  # type: ignore[arg-type]


def test_database_path_is_absolute_nonprivate_and_outside_repository(tmp_path: Path) -> None:
    with pytest.raises(AcknowledgmentError):
        OperatorAcknowledgmentStore(Path(":memory:"), TestVerifier())
    with pytest.raises(AcknowledgmentError):
        OperatorAcknowledgmentStore((Path("data") / "ack.sqlite").resolve(), TestVerifier())
    with pytest.raises(AcknowledgmentError):
        OperatorAcknowledgmentStore((tmp_path.parent / "account-private" / "ack.sqlite").resolve(), TestVerifier())


def test_repository_symlink_escape_is_rejected(tmp_path: Path) -> None:
    alias = tmp_path / "safe-alias"
    try:
        alias.symlink_to(Path("data").resolve(), target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(AcknowledgmentError):
        OperatorAcknowledgmentStore(alias / "ack.sqlite", TestVerifier())


def test_malformed_naive_or_nonpositive_artifacts_are_rejected() -> None:
    with pytest.raises(AcknowledgmentError):
        replace(request(), expected_review_sequence=0)
    with pytest.raises(AcknowledgmentError):
        replace(request(), issued_at=NOW.replace(tzinfo=None))
    with pytest.raises(AcknowledgmentError):
        replace(artifact(), review_sequence=0)
    with pytest.raises(AcknowledgmentError):
        replace(artifact(), provenance="VERIFIED_ATTESTATION")  # type: ignore[arg-type]


@pytest.mark.parametrize("fragment", ["secret", "wallet"])
def test_private_identifier_vocabulary_is_consistent(fragment: str) -> None:
    with pytest.raises(AcknowledgmentError, match="private"):
        replace(request(), acknowledgment_id=f"ack-{fragment}-value")


def test_global_safety_invariants_remain_closed() -> None:
    assert ACTIVATION_APPROVALS == frozenset()
    assert approved_active_protection_states() == frozenset()
    assert "enabled: false" in Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
