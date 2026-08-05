from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts.validate_host_process_lifecycle import main as validate_cli
from src.execution_gates import ACTIVATION_APPROVALS
from src.host_process_lifecycle import (
    LifecycleCompatibilityError,
    LifecycleError,
    LifecycleIdentityError,
    LifecycleSafetyError,
    HostProcessLifecycleContract,
    ModeledLockOutcome,
    evaluate_modeled_lock,
    validate_lifecycle_contract,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "read-only-deployment" / "host-process-lifecycle.json"


def valid_raw() -> dict[str, object]:
    value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    value["release_commit_sha"] = resolve_head_sha(ROOT)
    value["implementation_digest"] = checkpoint_implementation_digest(ROOT)
    return value


def valid_contract() -> HostProcessLifecycleContract:
    contract = HostProcessLifecycleContract.from_dict(valid_raw())
    return validate_lifecycle_contract(contract, repository_root=ROOT)


def rejected(mutator, error=LifecycleError) -> None:
    value = valid_raw()
    mutator(value)
    with pytest.raises(error):
        validate_lifecycle_contract(HostProcessLifecycleContract.from_dict(value), repository_root=ROOT)


def test_valid_contract_is_frozen_and_exact() -> None:
    contract = valid_contract()
    assert contract.process_lock_required is True
    assert contract.restart_after_ambiguous_state.value == "PROHIBITED_UNTIL_REVIEW"
    assert contract.broker_write_capability.value == "ABSENT"
    assert contract.__dataclass_params__.frozen is True


def test_exact_schema_and_unknown_enum_are_rejected() -> None:
    rejected(lambda value: value.update(extra=True))
    rejected(lambda value: value.pop("startup_mode"))
    rejected(lambda value: value.update(startup_mode="AUTOMATIC_START"))


@pytest.mark.parametrize("field", [
    "operator_presence_required", "preflight_validation_required", "single_instance_required",
    "process_lock_required", "heartbeat_required", "liveness_monitor_required",
    "suspension_controller_required", "alert_confirmation_required",
    "operator_acknowledgment_required", "stop_accepting_new_cycles_first",
    "durable_state_flush_required", "final_heartbeat_required", "shutdown_receipt_required",
    "operator_review_required_after_ambiguity", "replay_prevention_required",
    "stale_process_detection_required", "operator_role_required", "alert_owner_required",
    "incident_owner_required", "rollback_owner_required",
])
def test_required_lifecycle_controls_cannot_be_disabled(field: str) -> None:
    rejected(lambda value: value.update({field: False}), LifecycleSafetyError)


@pytest.mark.parametrize("field,value", [
    ("startup_mode", "AUTOMATIC_START"),
    ("stale_lock_policy", "IGNORE_STALE_LOCK"),
    ("missed_heartbeat_policy", "CONTINUE"),
    ("shutdown_mode", "UNCONTROLLED"),
    ("drain_inflight_cycle_policy", "ABANDON"),
    ("restart_mode", "AUTOMATIC_RESTART"),
    ("restart_after_clean_shutdown", "AUTOMATIC"),
    ("restart_after_crash", "AUTOMATIC"),
    ("restart_after_ambiguous_state", "CONTINUE_ON_AMBIGUITY"),
])
def test_unsafe_lifecycle_values_are_rejected(field: str, value: str) -> None:
    rejected(lambda raw: raw.update({field: value}))


@pytest.mark.parametrize("field,value", [
    ("strategy_enabled", True), ("activation_approvals_present", True),
    ("approved_protection_state_count", 1), ("protection_grounded", True),
    ("activation_approved", True), ("permits_automated_action", True),
    ("broker_write_capability", "PRESENT"), ("live_ready", True),
    ("unattended_ready", True),
])
def test_safety_boundary_is_closed(field: str, value: object) -> None:
    rejected(lambda raw: raw.update({field: value}), LifecycleSafetyError if field != "broker_write_capability" else LifecycleError)


def test_identity_and_profile_compatibility_are_bound() -> None:
    rejected(lambda value: value.update(release_commit_sha="0" * 40), LifecycleIdentityError)
    rejected(lambda value: value.update(implementation_digest="0" * 64), LifecycleIdentityError)
    rejected(lambda value: value.update(release_commit_sha="not-a-sha"), LifecycleIdentityError)
    rejected(lambda value: value.update(deployment_profile_id="deployment-profile-other-v1"), LifecycleCompatibilityError)


def test_serialization_is_deterministic_and_tampering_is_rejected() -> None:
    contract = valid_contract()
    assert contract.serialize() == contract.serialize()
    altered = json.loads(contract.serialize())
    altered["implementation_digest"] = "0" * 64
    with pytest.raises(LifecycleIdentityError):
        validate_lifecycle_contract(HostProcessLifecycleContract.from_dict(altered), repository_root=ROOT)


def test_modeled_lock_fixture_is_fail_closed() -> None:
    contract = valid_contract()
    identity = {"lock_contract_id": contract.lifecycle_contract_id, "lock_release_sha": contract.release_commit_sha}
    assert evaluate_modeled_lock(contract, **identity, owner_id="operator-primary") is ModeledLockOutcome.OWNER_ACTIVE
    assert evaluate_modeled_lock(contract, **identity, owner_id="operator-primary", existing_owner_id="operator-other") is ModeledLockOutcome.DUPLICATE_OWNER_REFUSED
    assert evaluate_modeled_lock(contract, **identity, owner_id="operator-primary", stale=True) is ModeledLockOutcome.STALE_LOCK_REVIEW_REQUIRED
    assert evaluate_modeled_lock(contract, **identity, owner_id="operator-primary", crashed=True) is ModeledLockOutcome.CRASH_RECOVERY_REVIEW_REQUIRED
    assert evaluate_modeled_lock(contract, **identity, owner_id="operator-primary", clean_shutdown=True) is ModeledLockOutcome.CLEAN_SHUTDOWN_RELEASED


def test_modeled_lock_identity_substitution_is_rejected() -> None:
    contract = valid_contract()
    with pytest.raises(LifecycleIdentityError):
        evaluate_modeled_lock(contract, lock_contract_id="lifecycle-contract-other-v1", lock_release_sha=contract.release_commit_sha, owner_id="operator-primary")


def test_modeled_owner_identity_is_validated() -> None:
    contract = valid_contract()
    identity = {"lock_contract_id": contract.lifecycle_contract_id, "lock_release_sha": contract.release_commit_sha}
    with pytest.raises(LifecycleError):
        evaluate_modeled_lock(contract, **identity, owner_id="machine-process")
    with pytest.raises(LifecycleError):
        evaluate_modeled_lock(contract, **identity, owner_id="operator-primary", existing_owner_id="other")


@pytest.mark.parametrize("field,value", [
    ("lifecycle_contract_id", "account-contract"),
    ("deployment_profile_id", "https://profile"),
    ("lifecycle_contract_id", "raw-exception"),
    ("deployment_profile_id", "credential-value"),
])
def test_private_or_network_values_are_rejected(field: str, value: str) -> None:
    rejected(lambda raw: raw.update({field: value}))


def test_cli_is_offline_and_cannot_claim_authorization(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "lifecycle.json"
    path.write_text(valid_contract().serialize(), encoding="utf-8")
    assert validate_cli(["--contract", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "VALID_FOR_OFFLINE_LIFECYCLE_REVIEW"

    tree = ast.parse((ROOT / "scripts" / "validate_host_process_lifecycle.py").read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(any(term in name for term in ("socket", "urllib", "requests", "broker", "robinhood", "subprocess", "psutil")) for name in imports)
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"sleep", "now", "utcnow", "monotonic", "terminate", "kill"} for node in ast.walk(tree))


def test_runbooks_are_procedures_not_approval_or_process_control() -> None:
    runbook = (ROOT / "HOST_PROCESS_LIFECYCLE_RUNBOOK.md").read_text(encoding="utf-8").casefold()
    restart = (ROOT / "CONTROLLED_SHUTDOWN_AND_RESTART.md").read_text(encoding="utf-8").casefold()
    assert "does not install a service" in runbook
    assert "create an approval mechanism" in runbook
    assert "acknowledgment closes" in restart
    assert "never clears suspension" in restart


def test_validation_does_not_change_runtime_approval_state() -> None:
    before = tuple(ACTIVATION_APPROVALS)
    valid_contract()
    assert tuple(ACTIVATION_APPROVALS) == before == ()
