"""Typed, broker-independent configuration profile for supervised read-only review."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from src.offline_path_safety import is_repository_data_path
from src.read_only_authorization_manifest import (
    PINNED_IMPLEMENTATION_CLOSURE,
    checkpoint_implementation_digest,
    resolve_head_sha,
)


SCHEMA_VERSION = 1
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{7,95}$")


class ProfileError(ValueError):
    """The profile is malformed or has an unsupported schema value."""


class ProfileIdentityError(ProfileError):
    """The profile does not bind to the checked-out release."""


class ProfileSafetyError(ProfileError):
    """The profile attempts to widen a closed safety boundary."""


class ProfilePathError(ProfileError):
    """The profile contains an unsafe or conflicting path declaration."""


class OperatingMode(str, Enum):
    SUPERVISED_READ_ONLY = "SUPERVISED_READ_ONLY"


class EnvironmentClass(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    STAGING = "STAGING"
    CONTROLLED_HOST = "CONTROLLED_HOST"


class HostRole(str, Enum):
    OFFLINE_COMMISSIONING = "OFFLINE_COMMISSIONING"
    SUPERVISED_READ_ONLY_CANDIDATE = "SUPERVISED_READ_ONLY_CANDIDATE"


class BrokerAccessMode(str, Enum):
    NONE = "NONE"
    READ_ONLY_NOT_CONFIGURED = "READ_ONLY_NOT_CONFIGURED"


class BrokerWriteCapability(str, Enum):
    ABSENT = "ABSENT"


class NetworkAccessMode(str, Enum):
    DISABLED = "DISABLED"
    FUTURE_ALLOWLIST_REQUIRED = "FUTURE_ALLOWLIST_REQUIRED"


class StartupMode(str, Enum):
    MANUAL_OPERATOR_START = "MANUAL_OPERATOR_START"


class ShutdownMode(str, Enum):
    CONTROLLED_OPERATOR_SHUTDOWN = "CONTROLLED_OPERATOR_SHUTDOWN"


class RestartPolicy(str, Enum):
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    FAIL_CLOSED_ON_AMBIGUITY = "FAIL_CLOSED_ON_AMBIGUITY"


class RetentionClass(str, Enum):
    EXTERNAL_POLICY_REQUIRED = "EXTERNAL_POLICY_REQUIRED"


PROFILE_KEYS = frozenset({
    "schema_version", "deployment_profile_id", "environment_name",
    "release_commit_sha", "implementation_digest", "pinned_file_count",
    "host_role", "operating_mode", "timezone_name", "environment_class",
    "repository_root_policy", "runtime_data_directory", "database_directory",
    "log_directory", "evidence_directory", "backup_directory",
    "temporary_directory", "strategy_enabled", "activation_approvals_present",
    "approved_protection_state_count", "protection_grounded",
    "activation_approved", "permits_automated_action", "broker_access_mode",
    "broker_write_capability", "network_access_mode", "startup_mode",
    "shutdown_mode", "restart_policy", "heartbeat_required",
    "alert_confirmation_required", "operator_supervision_required",
    "backup_required", "restore_test_required", "log_retention_class",
    "evidence_retention_class", "recovery_point_objective_class",
    "recovery_time_objective_class",
})
_PATH_FIELDS = (
    "runtime_data_directory", "database_directory", "log_directory",
    "evidence_directory", "backup_directory", "temporary_directory",
)
_PRIVATE_TERMS = (
    "account", "balance", "credential", "endpoint", "exception", "ip",
    "manifest", "order", "password", "price", "quantity", "secret",
    "symbol", "token", "username", "url", "user-home", "onedrive",
)


def _enum(enum_type: type[Enum], value: object, field: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise ProfileError(f"{field} is unsupported") from None


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ProfileError(f"{field} is invalid")
    if "://" in value or any(term in value.casefold() for term in _PRIVATE_TERMS):
        raise ProfileError(f"{field} contains prohibited text")
    return value


def _relative_path(value: object, field: str) -> str:
    value = _text(value, field)
    if "\\" in value or ":" in value or value.startswith("/") or value.startswith("~"):
        raise ProfilePathError(f"{field} is not a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ProfilePathError(f"{field} escapes its approved root")
    if any(part.casefold() in {"src", "tests", "test", "examples", "config", "data"} for part in path.parts):
        raise ProfilePathError(f"{field} targets source or test content")
    if value == "." or any(part in {".", ""} for part in path.parts):
        raise ProfilePathError(f"{field} is not canonical")
    return path.as_posix()


@dataclass(frozen=True)
class ReadOnlyDeploymentProfile:
    schema_version: int
    deployment_profile_id: str
    environment_name: str
    release_commit_sha: str
    implementation_digest: str
    pinned_file_count: int
    host_role: HostRole
    operating_mode: OperatingMode
    timezone_name: str
    environment_class: EnvironmentClass
    repository_root_policy: str
    runtime_data_directory: str
    database_directory: str
    log_directory: str
    evidence_directory: str
    backup_directory: str
    temporary_directory: str
    strategy_enabled: bool
    activation_approvals_present: bool
    approved_protection_state_count: int
    protection_grounded: bool
    activation_approved: bool
    permits_automated_action: bool
    broker_access_mode: BrokerAccessMode
    broker_write_capability: BrokerWriteCapability
    network_access_mode: NetworkAccessMode
    startup_mode: StartupMode
    shutdown_mode: ShutdownMode
    restart_policy: RestartPolicy
    heartbeat_required: bool
    alert_confirmation_required: bool
    operator_supervision_required: bool
    backup_required: bool
    restore_test_required: bool
    log_retention_class: RetentionClass
    evidence_retention_class: RetentionClass
    recovery_point_objective_class: RetentionClass
    recovery_time_objective_class: RetentionClass

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReadOnlyDeploymentProfile":
        if not isinstance(raw, Mapping) or set(raw) != PROFILE_KEYS:
            raise ProfileError("profile schema is not exact")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
            raise ProfileError("schema version is unsupported")
        profile = cls(
            schema_version=raw["schema_version"],
            deployment_profile_id=_text(raw["deployment_profile_id"], "deployment_profile_id"),
            environment_name=_text(raw["environment_name"], "environment_name"),
            release_commit_sha=_text(raw["release_commit_sha"], "release_commit_sha"),
            implementation_digest=_text(raw["implementation_digest"], "implementation_digest"),
            pinned_file_count=raw["pinned_file_count"],
            host_role=_enum(HostRole, raw["host_role"], "host_role"),
            operating_mode=_enum(OperatingMode, raw["operating_mode"], "operating_mode"),
            timezone_name=_text(raw["timezone_name"], "timezone_name"),
            environment_class=_enum(EnvironmentClass, raw["environment_class"], "environment_class"),
            repository_root_policy=_text(raw["repository_root_policy"], "repository_root_policy"),
            runtime_data_directory=_relative_path(raw["runtime_data_directory"], "runtime_data_directory"),
            database_directory=_relative_path(raw["database_directory"], "database_directory"),
            log_directory=_relative_path(raw["log_directory"], "log_directory"),
            evidence_directory=_relative_path(raw["evidence_directory"], "evidence_directory"),
            backup_directory=_relative_path(raw["backup_directory"], "backup_directory"),
            temporary_directory=_relative_path(raw["temporary_directory"], "temporary_directory"),
            strategy_enabled=raw["strategy_enabled"],
            activation_approvals_present=raw["activation_approvals_present"],
            approved_protection_state_count=raw["approved_protection_state_count"],
            protection_grounded=raw["protection_grounded"],
            activation_approved=raw["activation_approved"],
            permits_automated_action=raw["permits_automated_action"],
            broker_access_mode=_enum(BrokerAccessMode, raw["broker_access_mode"], "broker_access_mode"),
            broker_write_capability=_enum(BrokerWriteCapability, raw["broker_write_capability"], "broker_write_capability"),
            network_access_mode=_enum(NetworkAccessMode, raw["network_access_mode"], "network_access_mode"),
            startup_mode=_enum(StartupMode, raw["startup_mode"], "startup_mode"),
            shutdown_mode=_enum(ShutdownMode, raw["shutdown_mode"], "shutdown_mode"),
            restart_policy=_enum(RestartPolicy, raw["restart_policy"], "restart_policy"),
            heartbeat_required=raw["heartbeat_required"],
            alert_confirmation_required=raw["alert_confirmation_required"],
            operator_supervision_required=raw["operator_supervision_required"],
            backup_required=raw["backup_required"],
            restore_test_required=raw["restore_test_required"],
            log_retention_class=_enum(RetentionClass, raw["log_retention_class"], "log_retention_class"),
            evidence_retention_class=_enum(RetentionClass, raw["evidence_retention_class"], "evidence_retention_class"),
            recovery_point_objective_class=_enum(RetentionClass, raw["recovery_point_objective_class"], "recovery_point_objective_class"),
            recovery_time_objective_class=_enum(RetentionClass, raw["recovery_time_objective_class"], "recovery_time_objective_class"),
        )
        profile._validate_types()
        return profile

    def _validate_types(self) -> None:
        if not _IDENTIFIER.fullmatch(self.deployment_profile_id) or not self.deployment_profile_id.startswith("deployment-profile-"):
            raise ProfileError("deployment_profile_id is invalid")
        if not _SHA.fullmatch(self.release_commit_sha) or not _DIGEST.fullmatch(self.implementation_digest):
            raise ProfileIdentityError("release identity is malformed")
        if type(self.pinned_file_count) is not int or self.pinned_file_count < 1:
            raise ProfileIdentityError("pinned_file_count is invalid")
        enum_values = (
            (self.host_role, HostRole), (self.operating_mode, OperatingMode),
            (self.environment_class, EnvironmentClass),
            (self.broker_access_mode, BrokerAccessMode),
            (self.broker_write_capability, BrokerWriteCapability),
            (self.network_access_mode, NetworkAccessMode),
            (self.startup_mode, StartupMode), (self.shutdown_mode, ShutdownMode),
            (self.restart_policy, RestartPolicy),
            (self.log_retention_class, RetentionClass),
            (self.evidence_retention_class, RetentionClass),
            (self.recovery_point_objective_class, RetentionClass),
            (self.recovery_time_objective_class, RetentionClass),
        )
        if any(not isinstance(value, enum_type) for value, enum_type in enum_values):
            raise ProfileError("profile enum types are invalid")
        bools = (self.strategy_enabled, self.activation_approvals_present, self.protection_grounded, self.activation_approved, self.permits_automated_action, self.heartbeat_required, self.alert_confirmation_required, self.operator_supervision_required, self.backup_required, self.restore_test_required)
        if any(type(value) is not bool for value in bools) or type(self.approved_protection_state_count) is not int:
            raise ProfileError("profile scalar types are invalid")

    def validate(self, *, repository_root: Path, filesystem_root: Path | None = None) -> None:
        self._validate_types()
        if self.operating_mode is not OperatingMode.SUPERVISED_READ_ONLY or self.strategy_enabled or self.activation_approvals_present or self.approved_protection_state_count != 0 or self.protection_grounded or self.activation_approved or self.permits_automated_action or self.broker_write_capability is not BrokerWriteCapability.ABSENT or self.broker_access_mode not in {BrokerAccessMode.NONE, BrokerAccessMode.READ_ONLY_NOT_CONFIGURED} or self.operator_supervision_required is not True or self.heartbeat_required is not True or self.alert_confirmation_required is not True or self.startup_mode is not StartupMode.MANUAL_OPERATOR_START or self.restart_policy not in {RestartPolicy.MANUAL_REVIEW_REQUIRED, RestartPolicy.FAIL_CLOSED_ON_AMBIGUITY}:
            raise ProfileSafetyError("profile safety boundary is invalid")
        if self.repository_root_policy != "SOURCE_READ_ONLY":
            raise ProfilePathError("repository_root_policy is invalid")
        paths = tuple(getattr(self, field) for field in _PATH_FIELDS)
        if len(set(paths)) != len(paths):
            raise ProfilePathError("deployment directories are not distinct")
        if self.database_directory == ":memory:":
            raise ProfilePathError("in-memory database is not a deployment path")
        if filesystem_root is not None:
            base = Path(filesystem_root).resolve()
            for value in paths:
                candidate = (base / value).resolve(strict=False)
                if base not in candidate.parents and candidate != base:
                    raise ProfilePathError("deployment path escapes filesystem root")
                lexical = base / value
                if lexical.is_symlink() or is_repository_data_path(lexical):
                    raise ProfilePathError("deployment path uses a symlink or repository data")
        expected_sha = resolve_head_sha(repository_root)
        expected_digest = checkpoint_implementation_digest(repository_root)
        if self.release_commit_sha != expected_sha or self.implementation_digest != expected_digest or self.pinned_file_count != len(PINNED_IMPLEMENTATION_CLOSURE):
            raise ProfileIdentityError("profile release identity does not match repository")

    def to_dict(self) -> dict[str, object]:
        values = dict(self.__dict__)
        for key, value in values.items():
            if isinstance(value, Enum):
                values[key] = value.value
        return values

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"


def validate_profile(profile: ReadOnlyDeploymentProfile, *, repository_root: Path, filesystem_root: Path | None = None) -> ReadOnlyDeploymentProfile:
    if not isinstance(profile, ReadOnlyDeploymentProfile):
        raise ProfileError("a typed deployment profile is required")
    profile.validate(repository_root=repository_root, filesystem_root=filesystem_root)
    return profile


__all__ = [
    "BrokerAccessMode", "BrokerWriteCapability", "EnvironmentClass", "HostRole",
    "NetworkAccessMode", "OperatingMode", "ProfileError", "ProfileIdentityError",
    "ProfilePathError", "ProfileSafetyError", "ReadOnlyDeploymentProfile",
    "RestartPolicy", "RetentionClass", "SCHEMA_VERSION", "ShutdownMode",
    "StartupMode", "validate_profile",
]
