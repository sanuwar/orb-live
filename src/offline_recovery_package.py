"""Deterministic, offline recovery-package contract.

The package is an integrity-bound inventory, not a backup service.  Creation
and validation operate only under explicit temporary roots.  Existing durable
stores remain the authorities for their schemas, suspension, liveness,
acknowledgment, and replay behavior; this module only inventories and hashes
their bounded artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from src.host_process_lifecycle import DEPLOYMENT_PROFILE_ID
from src.offline_path_safety import is_repository_data_path
from src.read_only_authorization_manifest import (
    PINNED_IMPLEMENTATION_CLOSURE,
    checkpoint_implementation_digest,
    resolve_head_sha,
)


SCHEMA_VERSION = 1
LIFECYCLE_CONTRACT_ID = "lifecycle-contract-supervised-read-only-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{7,95}$")
_PRIVATE_TERMS = (
    "account", "balance", "credential", "endpoint", "exception", "hostname",
    "manifest", "order", "password", "price", "quantity", "secret",
    "symbol", "token", "username", "url", "user-home", "onedrive",
)


class RecoveryPackageError(ValueError):
    """The recovery package is malformed or incomplete."""


class RecoveryIdentityError(RecoveryPackageError):
    """The package is bound to another release or contract."""


class RecoveryIntegrityError(RecoveryPackageError):
    """An artifact or package digest does not match."""


class RecoveryPathError(RecoveryPackageError):
    """A package path is unsafe or outside an approved temporary root."""


class RecoveryRestoreError(RecoveryPackageError):
    """The isolated restore validation cannot complete safely."""


class RecoveryPointClass(str, Enum):
    LAST_VERIFIED_DURABLE_COMMIT = "LAST_VERIFIED_DURABLE_COMMIT"
    LAST_COMPLETE_CYCLE_BOUNDARY = "LAST_COMPLETE_CYCLE_BOUNDARY"
    LAST_VERIFIED_EVIDENCE_BOUNDARY = "LAST_VERIFIED_EVIDENCE_BOUNDARY"


class RecoveryTimeClass(str, Enum):
    MANUAL_OPERATOR_RECOVERY = "MANUAL_OPERATOR_RECOVERY"
    REVIEW_REQUIRED_BEFORE_RESTART = "REVIEW_REQUIRED_BEFORE_RESTART"
    FAIL_CLOSED_UNTIL_VALIDATED = "FAIL_CLOSED_UNTIL_VALIDATED"


class PackageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    VERIFIED = "VERIFIED"
    VALIDATED_OFFLINE = "VALIDATED_OFFLINE"
    PRIVACY_SAFE = "PRIVACY_SAFE"
    MATCHED = "MATCHED"


class ArtifactPrivacy(str, Enum):
    PRIVACY_SAFE_OFFLINE_EVIDENCE = "PRIVACY_SAFE_OFFLINE_EVIDENCE"


class ArtifactMutability(str, Enum):
    DURABLE_MUTABLE_STATE = "DURABLE_MUTABLE_STATE"
    IMMUTABLE_EVIDENCE = "IMMUTABLE_EVIDENCE"
    IMMUTABLE_CONFIGURATION_IDENTITY = "IMMUTABLE_CONFIGURATION_IDENTITY"


@dataclass(frozen=True)
class _ArtifactDefinition:
    artifact_type: str
    relative_path: str
    schema_version: str
    restore_order: int
    mutable: ArtifactMutability
    sqlite: bool


_ARTIFACT_DEFINITIONS = (
    _ArtifactDefinition("configuration_identity", "config/deployment-profile.json", "1", 1, ArtifactMutability.IMMUTABLE_CONFIGURATION_IDENTITY, False),
    _ArtifactDefinition("lifecycle_contract", "config/lifecycle-contract.json", "1", 2, ArtifactMutability.IMMUTABLE_CONFIGURATION_IDENTITY, False),
    _ArtifactDefinition("scheduler_state", "durable/scheduler.sqlite", "1", 3, ArtifactMutability.DURABLE_MUTABLE_STATE, True),
    _ArtifactDefinition("suspension_controller_state", "durable/suspension.sqlite", "1", 4, ArtifactMutability.DURABLE_MUTABLE_STATE, True),
    _ArtifactDefinition("runner_receipts", "durable/runner.sqlite", "1", 5, ArtifactMutability.DURABLE_MUTABLE_STATE, True),
    _ArtifactDefinition("liveness_state", "durable/liveness.sqlite", "1", 6, ArtifactMutability.DURABLE_MUTABLE_STATE, True),
    _ArtifactDefinition("alert_outbox", "durable/alerts.sqlite", "1", 7, ArtifactMutability.DURABLE_MUTABLE_STATE, True),
    _ArtifactDefinition("operator_acknowledgment", "durable/acknowledgment.sqlite", "1", 8, ArtifactMutability.DURABLE_MUTABLE_STATE, True),
    _ArtifactDefinition("operational_status_evidence", "evidence/operational-status.json", "1", 9, ArtifactMutability.IMMUTABLE_EVIDENCE, False),
    _ArtifactDefinition("commissioning_campaign_evidence", "evidence/commissioning-campaign.json", "1", 10, ArtifactMutability.IMMUTABLE_EVIDENCE, False),
)
ARTIFACT_TYPES = frozenset(item.artifact_type for item in _ARTIFACT_DEFINITIONS)
_DEFINITIONS_BY_TYPE = {item.artifact_type: item for item in _ARTIFACT_DEFINITIONS}
_DEFINITIONS_BY_PATH = {item.relative_path: item for item in _ARTIFACT_DEFINITIONS}


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RecoveryPackageError(f"{field} is invalid")
    folded = value.casefold()
    if "://" in value or folded.startswith(("ip-", "ip:")) or any(term in folded for term in _PRIVATE_TERMS):
        raise RecoveryPackageError(f"{field} contains prohibited text")
    return value


def _enum(enum_type: type[Enum], value: object, field: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise RecoveryPackageError(f"{field} is unsupported") from None


def _safe_relative(value: object, field: str) -> str:
    value = _text(value, field)
    if value.startswith(("/", "~")) or "\\" in value or ":" in value:
        raise RecoveryPathError(f"{field} is not relative")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or not path.parts:
        raise RecoveryPathError(f"{field} escapes its root")
    if any(part.casefold() in {"src", "tests", "test", "data", "logs", "private"} for part in path.parts):
        raise RecoveryPathError(f"{field} targets a prohibited directory")
    return path.as_posix()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _temporary_root(path: Path, field: str) -> Path:
    if not isinstance(path, Path):
        raise RecoveryPathError(f"{field} is invalid")
    resolved = path.resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    if temp not in resolved.parents and resolved != temp:
        raise RecoveryPathError(f"{field} must be under the temporary root")
    return resolved


def _candidate(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve(strict=False)
    if root not in candidate.parents or (root / relative_path).is_symlink():
        raise RecoveryPathError("artifact path escapes its root")
    if is_repository_data_path(candidate):
        raise RecoveryPathError("repository data path is not a recovery root")
    return candidate


@dataclass(frozen=True)
class RecoveryArtifact:
    artifact_type: str
    relative_path: str
    schema_version: str
    canonical_content_hash: str
    required_for_recovery: bool
    restore_order: int
    privacy_classification: ArtifactPrivacy
    mutability_classification: ArtifactMutability
    originating_release_commit_sha: str
    implementation_digest_binding: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RecoveryArtifact":
        expected = {
            "artifact_type", "relative_path", "schema_version", "canonical_content_hash",
            "required_for_recovery", "restore_order", "privacy_classification",
            "mutability_classification", "originating_release_commit_sha",
            "implementation_digest_binding",
        }
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise RecoveryPackageError("artifact schema is not exact")
        value = cls(
            artifact_type=_text(raw["artifact_type"], "artifact_type"),
            relative_path=_safe_relative(raw["relative_path"], "relative_path"),
            schema_version=_text(raw["schema_version"], "schema_version"),
            canonical_content_hash=_text(raw["canonical_content_hash"], "canonical_content_hash"),
            required_for_recovery=raw["required_for_recovery"],
            restore_order=raw["restore_order"],
            privacy_classification=_enum(ArtifactPrivacy, raw["privacy_classification"], "privacy_classification"),
            mutability_classification=_enum(ArtifactMutability, raw["mutability_classification"], "mutability_classification"),
            originating_release_commit_sha=_text(raw["originating_release_commit_sha"], "originating_release_commit_sha"),
            implementation_digest_binding=_text(raw["implementation_digest_binding"], "implementation_digest_binding"),
        )
        if (
            not _SHA.fullmatch(value.canonical_content_hash)
            or not _COMMIT.fullmatch(value.originating_release_commit_sha)
            or not _SHA.fullmatch(value.implementation_digest_binding)
            or type(value.required_for_recovery) is not bool
            or type(value.restore_order) is not int
            or value.restore_order < 1
        ):
            raise RecoveryPackageError("artifact scalar values are invalid")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_type": self.artifact_type,
            "relative_path": self.relative_path,
            "schema_version": self.schema_version,
            "canonical_content_hash": self.canonical_content_hash,
            "required_for_recovery": self.required_for_recovery,
            "restore_order": self.restore_order,
            "privacy_classification": self.privacy_classification.value,
            "mutability_classification": self.mutability_classification.value,
            "originating_release_commit_sha": self.originating_release_commit_sha,
            "implementation_digest_binding": self.implementation_digest_binding,
        }


PACKAGE_KEYS = frozenset({
    "schema_version", "recovery_package_id", "source_release_commit_sha",
    "source_implementation_digest", "source_pinned_file_count", "deployment_profile_id",
    "lifecycle_contract_id", "created_at_injected", "artifact_inventory", "artifact_count",
    "restore_order", "recovery_point_class", "recovery_time_class",
    "backup_completeness_status", "integrity_status", "strategy_enabled",
    "activation_approvals_present", "approved_protection_state_count", "protection_grounded",
    "activation_approved", "permits_automated_action", "live_ready", "unattended_ready",
    "active_suspension_preserved", "ambiguous_state_preserved", "replay_prevention_preserved",
    "package_digest", "canonical_manifest_hash", "privacy_validation_status",
    "release_identity_status", "restore_validation_status",
})


@dataclass(frozen=True)
class OfflineRecoveryPackage:
    schema_version: int
    recovery_package_id: str
    source_release_commit_sha: str
    source_implementation_digest: str
    source_pinned_file_count: int
    deployment_profile_id: str
    lifecycle_contract_id: str
    created_at_injected: str
    artifact_inventory: tuple[RecoveryArtifact, ...]
    artifact_count: int
    restore_order: tuple[str, ...]
    recovery_point_class: RecoveryPointClass
    recovery_time_class: RecoveryTimeClass
    backup_completeness_status: PackageStatus
    integrity_status: PackageStatus
    strategy_enabled: bool
    activation_approvals_present: bool
    approved_protection_state_count: int
    protection_grounded: bool
    activation_approved: bool
    permits_automated_action: bool
    live_ready: bool
    unattended_ready: bool
    active_suspension_preserved: bool
    ambiguous_state_preserved: bool
    replay_prevention_preserved: bool
    package_digest: str
    canonical_manifest_hash: str
    privacy_validation_status: PackageStatus
    release_identity_status: PackageStatus
    restore_validation_status: PackageStatus

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OfflineRecoveryPackage":
        if not isinstance(raw, Mapping) or set(raw) != PACKAGE_KEYS:
            raise RecoveryPackageError("package schema is not exact")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
            raise RecoveryPackageError("schema version is unsupported")
        if not isinstance(raw["artifact_inventory"], list):
            raise RecoveryPackageError("artifact inventory is invalid")
        inventory = tuple(RecoveryArtifact.from_dict(item) for item in raw["artifact_inventory"])
        value = cls(
            schema_version=raw["schema_version"], recovery_package_id=_text(raw["recovery_package_id"], "recovery_package_id"),
            source_release_commit_sha=_text(raw["source_release_commit_sha"], "source_release_commit_sha"),
            source_implementation_digest=_text(raw["source_implementation_digest"], "source_implementation_digest"),
            source_pinned_file_count=raw["source_pinned_file_count"], deployment_profile_id=_text(raw["deployment_profile_id"], "deployment_profile_id"),
            lifecycle_contract_id=_text(raw["lifecycle_contract_id"], "lifecycle_contract_id"), created_at_injected=_text(raw["created_at_injected"], "created_at_injected"),
            artifact_inventory=inventory, artifact_count=raw["artifact_count"],
            restore_order=tuple(raw["restore_order"]), recovery_point_class=_enum(RecoveryPointClass, raw["recovery_point_class"], "recovery_point_class"),
            recovery_time_class=_enum(RecoveryTimeClass, raw["recovery_time_class"], "recovery_time_class"),
            backup_completeness_status=_enum(PackageStatus, raw["backup_completeness_status"], "backup_completeness_status"),
            integrity_status=_enum(PackageStatus, raw["integrity_status"], "integrity_status"),
            strategy_enabled=raw["strategy_enabled"], activation_approvals_present=raw["activation_approvals_present"],
            approved_protection_state_count=raw["approved_protection_state_count"], protection_grounded=raw["protection_grounded"],
            activation_approved=raw["activation_approved"], permits_automated_action=raw["permits_automated_action"],
            live_ready=raw["live_ready"], unattended_ready=raw["unattended_ready"],
            active_suspension_preserved=raw["active_suspension_preserved"], ambiguous_state_preserved=raw["ambiguous_state_preserved"],
            replay_prevention_preserved=raw["replay_prevention_preserved"], package_digest=_text(raw["package_digest"], "package_digest"),
            canonical_manifest_hash=_text(raw["canonical_manifest_hash"], "canonical_manifest_hash"),
            privacy_validation_status=_enum(PackageStatus, raw["privacy_validation_status"], "privacy_validation_status"),
            release_identity_status=_enum(PackageStatus, raw["release_identity_status"], "release_identity_status"),
            restore_validation_status=_enum(PackageStatus, raw["restore_validation_status"], "restore_validation_status"),
        )
        value._validate_shape()
        return value

    def _validate_shape(self) -> None:
        if not _IDENTIFIER.fullmatch(self.recovery_package_id) or not self.recovery_package_id.startswith("recovery-package-"):
            raise RecoveryPackageError("recovery package identity is invalid")
        if not _COMMIT.fullmatch(self.source_release_commit_sha) or not _SHA.fullmatch(self.source_implementation_digest) or not _SHA.fullmatch(self.package_digest) or not _SHA.fullmatch(self.canonical_manifest_hash):
            raise RecoveryIdentityError("package identity is malformed")
        if type(self.source_pinned_file_count) is not int or type(self.artifact_count) is not int or self.source_pinned_file_count < 1 or self.artifact_count < 1:
            raise RecoveryPackageError("package counts are invalid")
        if len(self.artifact_inventory) != self.artifact_count or tuple(self.restore_order) != tuple(item.artifact_type for item in sorted(self.artifact_inventory, key=lambda item: item.restore_order)):
            raise RecoveryPackageError("restore order is invalid")
        if not isinstance(self.restore_order, tuple) or any(not isinstance(item, str) for item in self.restore_order):
            raise RecoveryPackageError("restore order is invalid")
        bool_fields = (self.strategy_enabled, self.activation_approvals_present, self.protection_grounded, self.activation_approved, self.permits_automated_action, self.live_ready, self.unattended_ready, self.active_suspension_preserved, self.ambiguous_state_preserved, self.replay_prevention_preserved)
        if any(type(item) is not bool for item in bool_fields) or type(self.approved_protection_state_count) is not int:
            raise RecoveryPackageError("safety fields are invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "recovery_package_id": self.recovery_package_id,
            "source_release_commit_sha": self.source_release_commit_sha, "source_implementation_digest": self.source_implementation_digest,
            "source_pinned_file_count": self.source_pinned_file_count, "deployment_profile_id": self.deployment_profile_id,
            "lifecycle_contract_id": self.lifecycle_contract_id, "created_at_injected": self.created_at_injected,
            "artifact_inventory": [item.to_dict() for item in self.artifact_inventory], "artifact_count": self.artifact_count,
            "restore_order": list(self.restore_order), "recovery_point_class": self.recovery_point_class.value,
            "recovery_time_class": self.recovery_time_class.value, "backup_completeness_status": self.backup_completeness_status.value,
            "integrity_status": self.integrity_status.value, "strategy_enabled": self.strategy_enabled,
            "activation_approvals_present": self.activation_approvals_present, "approved_protection_state_count": self.approved_protection_state_count,
            "protection_grounded": self.protection_grounded, "activation_approved": self.activation_approved,
            "permits_automated_action": self.permits_automated_action, "live_ready": self.live_ready, "unattended_ready": self.unattended_ready,
            "active_suspension_preserved": self.active_suspension_preserved, "ambiguous_state_preserved": self.ambiguous_state_preserved,
            "replay_prevention_preserved": self.replay_prevention_preserved, "package_digest": self.package_digest,
            "canonical_manifest_hash": self.canonical_manifest_hash, "privacy_validation_status": self.privacy_validation_status.value,
            "release_identity_status": self.release_identity_status.value, "restore_validation_status": self.restore_validation_status.value,
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"


def _manifest_payload(package: OfflineRecoveryPackage) -> dict[str, object]:
    value = package.to_dict()
    return {key: value[key] for key in value if key not in {"package_digest", "canonical_manifest_hash"}}


def _canonical_manifest_hash(package: OfflineRecoveryPackage) -> str:
    return _sha256_bytes(_canonical({"artifact_inventory": [item.to_dict() for item in package.artifact_inventory], "restore_order": list(package.restore_order)}))


def _package_digest(package: OfflineRecoveryPackage) -> str:
    return _sha256_bytes(_canonical(_manifest_payload(package)))


def _validate_artifact_file(candidate: Path, definition: _ArtifactDefinition) -> None:
    try:
        content = candidate.read_bytes()
    except OSError:
        raise RecoveryRestoreError("required artifact cannot be read") from None
    if definition.sqlite:
        if not content.startswith(b"SQLite format 3\x00"):
            raise RecoveryRestoreError("durable artifact is not a SQLite store")
        try:
            connection = sqlite3.connect(candidate.as_uri() + "?mode=ro", uri=True)
            result = connection.execute("PRAGMA integrity_check").fetchone()
            connection.close()
        except sqlite3.Error:
            raise RecoveryRestoreError("durable artifact integrity check failed") from None
        if result != ("ok",):
            raise RecoveryRestoreError("durable artifact integrity check failed")


def _inventory_from_root(root: Path, release_sha: str, digest: str) -> tuple[RecoveryArtifact, ...]:
    records: list[RecoveryArtifact] = []
    for definition in _ARTIFACT_DEFINITIONS:
        candidate = _candidate(root, definition.relative_path)
        if not candidate.is_file():
            raise RecoveryRestoreError("required recovery artifact is missing")
        _validate_artifact_file(candidate, definition)
        records.append(RecoveryArtifact(
            artifact_type=definition.artifact_type, relative_path=definition.relative_path,
            schema_version=definition.schema_version, canonical_content_hash=_sha256_bytes(candidate.read_bytes()),
            required_for_recovery=True, restore_order=definition.restore_order,
            privacy_classification=ArtifactPrivacy.PRIVACY_SAFE_OFFLINE_EVIDENCE,
            mutability_classification=definition.mutable, originating_release_commit_sha=release_sha,
            implementation_digest_binding=digest,
        ))
    return tuple(records)


def _build_package(package_id: str, release_sha: str, digest: str, created_at: str, inventory: tuple[RecoveryArtifact, ...], lifecycle_contract_id: str) -> OfflineRecoveryPackage:
    package = OfflineRecoveryPackage(
        schema_version=SCHEMA_VERSION, recovery_package_id=package_id, source_release_commit_sha=release_sha,
        source_implementation_digest=digest, source_pinned_file_count=len(PINNED_IMPLEMENTATION_CLOSURE),
        deployment_profile_id=DEPLOYMENT_PROFILE_ID, lifecycle_contract_id=lifecycle_contract_id,
        created_at_injected=created_at, artifact_inventory=inventory, artifact_count=len(inventory),
        restore_order=tuple(item.artifact_type for item in inventory), recovery_point_class=RecoveryPointClass.LAST_VERIFIED_DURABLE_COMMIT,
        recovery_time_class=RecoveryTimeClass.FAIL_CLOSED_UNTIL_VALIDATED, backup_completeness_status=PackageStatus.COMPLETE,
        integrity_status=PackageStatus.VERIFIED, strategy_enabled=False, activation_approvals_present=False,
        approved_protection_state_count=0, protection_grounded=False, activation_approved=False,
        permits_automated_action=False, live_ready=False, unattended_ready=False, active_suspension_preserved=True,
        ambiguous_state_preserved=True, replay_prevention_preserved=True, package_digest="0" * 64,
        canonical_manifest_hash="0" * 64, privacy_validation_status=PackageStatus.PRIVACY_SAFE,
        release_identity_status=PackageStatus.MATCHED, restore_validation_status=PackageStatus.VALIDATED_OFFLINE,
    )
    manifest_hash = _canonical_manifest_hash(package)
    package = OfflineRecoveryPackage(**{**package.__dict__, "canonical_manifest_hash": manifest_hash})
    digest_value = _package_digest(package)
    return OfflineRecoveryPackage(**{**package.__dict__, "package_digest": digest_value})


def create_recovery_package(*, source_root: Path, package_path: Path, repository_root: Path, recovery_package_id: str, lifecycle_contract_id: str, created_at_injected: str) -> OfflineRecoveryPackage:
    source = _temporary_root(source_root, "source_root")
    target = _temporary_root(package_path.parent, "package_path") / package_path.name
    if target.exists():
        raise RecoveryPackageError("completed recovery package already exists")
    if lifecycle_contract_id != LIFECYCLE_CONTRACT_ID:
        raise RecoveryIdentityError("lifecycle contract identity is invalid")
    release_sha = resolve_head_sha(repository_root)
    digest = checkpoint_implementation_digest(repository_root)
    inventory = _inventory_from_root(source, release_sha, digest)
    package = _build_package(recovery_package_id, release_sha, digest, created_at_injected, inventory, lifecycle_contract_id)
    temporary = target.with_name(target.name + ".partial")
    if temporary.exists():
        raise RecoveryPackageError("partial recovery package already exists")
    try:
        temporary.write_text(package.serialize(), encoding="utf-8", newline="\n")
        os.replace(temporary, target)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RecoveryPackageError("recovery package publication failed") from None
    return package


def validate_recovery_package(*, package_path: Path, restore_root: Path, repository_root: Path) -> OfflineRecoveryPackage:
    restore = _temporary_root(restore_root, "restore_root")
    try:
        raw = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RecoveryPackageError("recovery package cannot be read") from None
    package = OfflineRecoveryPackage.from_dict(raw)
    if _package_digest(package) != package.package_digest or _canonical_manifest_hash(package) != package.canonical_manifest_hash:
        raise RecoveryIntegrityError("recovery package digest mismatch")
    if package.source_release_commit_sha != resolve_head_sha(repository_root) or package.source_implementation_digest != checkpoint_implementation_digest(repository_root) or package.source_pinned_file_count != len(PINNED_IMPLEMENTATION_CLOSURE):
        raise RecoveryIdentityError("recovery package release identity does not match repository")
    if package.deployment_profile_id != DEPLOYMENT_PROFILE_ID or package.lifecycle_contract_id != LIFECYCLE_CONTRACT_ID:
        raise RecoveryIdentityError("recovery package contract identity is invalid")
    if package.strategy_enabled or package.activation_approvals_present or package.approved_protection_state_count != 0 or package.protection_grounded or package.activation_approved or package.permits_automated_action or package.live_ready or package.unattended_ready or not package.active_suspension_preserved or not package.ambiguous_state_preserved or not package.replay_prevention_preserved:
        raise RecoveryPackageError("recovery safety state is invalid")
    if package.backup_completeness_status is not PackageStatus.COMPLETE or package.integrity_status is not PackageStatus.VERIFIED:
        raise RecoveryRestoreError("recovery package is incomplete")
    seen_types: set[str] = set()
    seen_paths: set[str] = set()
    for artifact in package.artifact_inventory:
        definition = _DEFINITIONS_BY_TYPE.get(artifact.artifact_type)
        if definition is None or artifact.artifact_type in seen_types or artifact.relative_path in seen_paths or definition.relative_path != artifact.relative_path or definition.schema_version != artifact.schema_version or artifact.restore_order != definition.restore_order or artifact.privacy_classification is not ArtifactPrivacy.PRIVACY_SAFE_OFFLINE_EVIDENCE or artifact.mutability_classification is not definition.mutable or artifact.originating_release_commit_sha != package.source_release_commit_sha or artifact.implementation_digest_binding != package.source_implementation_digest:
            raise RecoveryIntegrityError("artifact inventory is not canonical")
        seen_types.add(artifact.artifact_type)
        seen_paths.add(artifact.relative_path)
        candidate = _candidate(restore, artifact.relative_path)
        if not candidate.is_file() or _sha256_bytes(candidate.read_bytes()) != artifact.canonical_content_hash:
            raise RecoveryIntegrityError("artifact content hash mismatch")
        _validate_artifact_file(candidate, definition)
    if seen_types != ARTIFACT_TYPES:
        raise RecoveryRestoreError("required artifact inventory is incomplete")
    return package


__all__ = [
    "ARTIFACT_TYPES", "ArtifactMutability", "ArtifactPrivacy", "OfflineRecoveryPackage",
    "PackageStatus", "RecoveryArtifact", "RecoveryIdentityError", "RecoveryIntegrityError",
    "RecoveryPackageError", "RecoveryPointClass", "RecoveryPathError", "RecoveryRestoreError",
    "RecoveryTimeClass", "SCHEMA_VERSION", "create_recovery_package", "validate_recovery_package",
    "LIFECYCLE_CONTRACT_ID",
]
