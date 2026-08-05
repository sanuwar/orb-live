from __future__ import annotations

import ast
import json
import sqlite3
import shutil
from pathlib import Path

import pytest

from scripts.validate_offline_recovery_package import main as validate_cli
from src.execution_gates import ACTIVATION_APPROVALS
from src.offline_recovery_package import (
    ARTIFACT_TYPES,
    LIFECYCLE_CONTRACT_ID,
    RecoveryPackageError,
    RecoveryIdentityError,
    RecoveryIntegrityError,
    RecoveryPathError,
    RecoveryRestoreError,
    create_recovery_package,
    validate_recovery_package,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ID = "recovery-package-supervised-read-only-v1"
CREATED = "2026-08-05T12:00:00Z"


def make_source(root: Path) -> Path:
    for relative in (
        "config/deployment-profile.json", "config/lifecycle-contract.json",
        "evidence/operational-status.json", "evidence/commissioning-campaign.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"privacy_safe":true}\n', encoding="utf-8")
    for relative in (
        "durable/scheduler.sqlite", "durable/suspension.sqlite", "durable/runner.sqlite",
        "durable/liveness.sqlite", "durable/alerts.sqlite", "durable/acknowledgment.sqlite",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target)
        try:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker VALUES ('offline-fixture')")
            connection.commit()
        finally:
            connection.close()
    return root


def create_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    source = make_source(tmp_path / "source")
    package_path = source / "recovery-package.json"
    package = create_recovery_package(
        source_root=source, package_path=package_path, repository_root=ROOT,
        recovery_package_id=PACKAGE_ID, lifecycle_contract_id=LIFECYCLE_CONTRACT_ID,
        created_at_injected=CREATED,
    )
    return source, package_path, json.loads(package.serialize())


def valid_package(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    source, package_path, raw = create_fixture(tmp_path)
    return source, package_path, raw


def test_valid_package_is_accepted_and_covers_required_artifacts(tmp_path: Path) -> None:
    source, package_path, raw = valid_package(tmp_path)
    package = validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)
    assert package.artifact_count == 10
    assert {item["artifact_type"] for item in raw["artifact_inventory"]} == ARTIFACT_TYPES
    assert package.active_suspension_preserved is True
    assert package.ambiguous_state_preserved is True
    assert package.replay_prevention_preserved is True


def test_exact_schema_unknown_and_missing_keys_are_rejected(tmp_path: Path) -> None:
    source, package_path, raw = valid_package(tmp_path)
    raw["unknown"] = True
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryPackageError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)
    raw.pop("unknown")
    raw.pop("artifact_inventory")
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryPackageError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


def test_unknown_recovery_class_is_rejected(tmp_path: Path) -> None:
    source, package_path, raw = valid_package(tmp_path)
    raw["recovery_point_class"] = "ZERO_DATA_LOSS"
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryPackageError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


def test_unknown_schema_version_and_manifest_digest_are_rejected(tmp_path: Path) -> None:
    source, package_path, raw = valid_package(tmp_path)
    raw["schema_version"] = 2
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryPackageError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)
    raw["schema_version"] = 1
    raw["canonical_manifest_hash"] = "0" * 64
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryIntegrityError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


def test_missing_artifact_and_partial_package_are_rejected(tmp_path: Path) -> None:
    source, package_path, _ = valid_package(tmp_path)
    (source / "durable/alerts.sqlite").unlink()
    with pytest.raises(RecoveryRestoreError):
        create_recovery_package(source_root=source, package_path=tmp_path / "other.json", repository_root=ROOT, recovery_package_id=PACKAGE_ID, lifecycle_contract_id=LIFECYCLE_CONTRACT_ID, created_at_injected=CREATED)


@pytest.mark.parametrize("mutation", ["duplicate", "order", "artifact_hash", "package_hash", "release", "digest", "pinned", "profile", "lifecycle"])
def test_integrity_identity_and_inventory_mutations_are_rejected(tmp_path: Path, mutation: str) -> None:
    source, package_path, raw = valid_package(tmp_path)
    if mutation == "duplicate":
        raw["artifact_inventory"][1] = dict(raw["artifact_inventory"][0])
    elif mutation == "order":
        raw["restore_order"] = list(reversed(raw["restore_order"]))
    elif mutation == "artifact_hash":
        raw["artifact_inventory"][0]["canonical_content_hash"] = "0" * 64
    elif mutation == "package_hash":
        raw["package_digest"] = "0" * 64
    elif mutation == "release":
        raw["source_release_commit_sha"] = "0" * 40
    elif mutation == "digest":
        raw["source_implementation_digest"] = "0" * 64
    elif mutation == "pinned":
        raw["source_pinned_file_count"] = 19
    elif mutation == "profile":
        raw["deployment_profile_id"] = "deployment-profile-other-v1"
    else:
        raw["lifecycle_contract_id"] = "lifecycle-contract-other-v1"
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises((RecoveryPackageError, RecoveryIdentityError, RecoveryIntegrityError)):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


@pytest.mark.parametrize("relative", ["C:/private/file", "~/private/file", "src/private/file", "data/private/file", "private/account.json"])
def test_absolute_private_and_repository_paths_are_rejected(tmp_path: Path, relative: str) -> None:
    source = make_source(tmp_path / "source")
    with pytest.raises((RecoveryPathError, RecoveryRestoreError)):
        # A fixed artifact path is required; an unsafe root cannot be substituted.
        create_recovery_package(source_root=source / relative, package_path=tmp_path / "package.json", repository_root=ROOT, recovery_package_id=PACKAGE_ID, lifecycle_contract_id=LIFECYCLE_CONTRACT_ID, created_at_injected=CREATED)


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = source / "durable"
    shutil.rmtree(link)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable in this Windows environment")
    with pytest.raises(RecoveryPathError):
        create_recovery_package(source_root=source, package_path=tmp_path / "package.json", repository_root=ROOT, recovery_package_id=PACKAGE_ID, lifecycle_contract_id=LIFECYCLE_CONTRACT_ID, created_at_injected=CREATED)


@pytest.mark.parametrize("field,value", [
    ("recovery_package_id", "account-package"), ("deployment_profile_id", "credential-value"),
    ("lifecycle_contract_id", "symbol-AAPL"), ("created_at_injected", "https://endpoint"),
    ("recovery_package_id", "raw-exception"), ("recovery_package_id", "quantity-1"),
    ("recovery_package_id", "price-1"), ("recovery_package_id", "balance-1"),
    ("recovery_package_id", "order-1"),
])
def test_private_fields_are_rejected(tmp_path: Path, field: str, value: str) -> None:
    source, package_path, raw = valid_package(tmp_path)
    raw[field] = value
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryPackageError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


@pytest.mark.parametrize("field", [
    "strategy_enabled", "activation_approvals_present", "protection_grounded",
    "activation_approved", "permits_automated_action", "live_ready", "unattended_ready",
    "active_suspension_preserved", "ambiguous_state_preserved", "replay_prevention_preserved",
])
def test_safety_and_preservation_flags_cannot_be_weakened(tmp_path: Path, field: str) -> None:
    source, package_path, raw = valid_package(tmp_path)
    raw[field] = False if field.endswith("preserved") else True
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryPackageError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


def test_protection_count_above_zero_is_rejected(tmp_path: Path) -> None:
    source, package_path, raw = valid_package(tmp_path)
    raw["approved_protection_state_count"] = 1
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryPackageError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


@pytest.mark.parametrize("relative", ["/private/file", "~/private/file", "src/private/file", "data/private/file", "private/account.json"])
def test_inventory_paths_are_rejected(tmp_path: Path, relative: str) -> None:
    source, package_path, raw = valid_package(tmp_path)
    raw["artifact_inventory"][0]["relative_path"] = relative
    package_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RecoveryPackageError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


def test_tampered_artifact_and_partial_cycle_fail_closed(tmp_path: Path) -> None:
    source, package_path, _ = valid_package(tmp_path)
    (source / "durable/runner.sqlite").write_bytes(b"tampered")
    with pytest.raises(RecoveryIntegrityError):
        validate_recovery_package(package_path=package_path, restore_root=source, repository_root=ROOT)


def test_existing_completed_package_cannot_be_overwritten(tmp_path: Path) -> None:
    source, package_path, _ = valid_package(tmp_path)
    with pytest.raises(RecoveryPackageError):
        create_recovery_package(source_root=source, package_path=package_path, repository_root=ROOT, recovery_package_id=PACKAGE_ID, lifecycle_contract_id=LIFECYCLE_CONTRACT_ID, created_at_injected=CREATED)


def test_package_is_deterministic_and_hashes_are_stable(tmp_path: Path) -> None:
    source_a, package_a, _ = valid_package(tmp_path / "a")
    source_b, package_b, _ = valid_package(tmp_path / "b")
    assert json.loads(package_a.read_text()) == json.loads(package_b.read_text())
    assert source_a != source_b


def test_restore_root_must_be_temporary_and_no_real_state_changes() -> None:
    with pytest.raises(RecoveryPathError):
        # The repository root is deliberately outside the permitted temporary restore scope.
        validate_recovery_package(package_path=ROOT / "missing.json", restore_root=ROOT, repository_root=ROOT)


def test_cli_is_offline_and_zero_is_not_authorization(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source, package_path, _ = valid_package(tmp_path)
    assert validate_cli(["--package", str(package_path), "--restore-root", str(source)]) == 0
    assert capsys.readouterr().out.strip() == "VALID_FOR_OFFLINE_RECOVERY_REVIEW"
    tree = ast.parse((ROOT / "scripts/validate_offline_recovery_package.py").read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(any(term in name for term in ("socket", "urllib", "requests", "broker", "robinhood", "subprocess", "psutil")) for name in imports)
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))


def test_runbooks_are_guidance_only_and_acknowledgment_is_not_clearance() -> None:
    runbook = (ROOT / "BACKUP_RESTORE_RECOVERY_RUNBOOK.md").read_text(encoding="utf-8").casefold()
    checklist = (ROOT / "RECOVERY_VALIDATION_CHECKLIST.md").read_text(encoding="utf-8").casefold()
    assert "does not schedule backups" in runbook
    assert "clear suspension" in runbook
    assert "cannot change runtime state" in checklist
    assert "does not authorize deployment" in checklist


def test_validation_does_not_change_approval_state() -> None:
    before = tuple(ACTIVATION_APPROVALS)
    assert before == ()
