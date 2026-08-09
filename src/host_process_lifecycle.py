"""Offline contract for a manually operated supervised read-only host.

This module validates lifecycle declarations and pure lock observations.  It
does not own a process, acquire an OS lock, read a clock, launch a process, or
persist lifecycle state.  Existing runner, liveness, suspension, and scheduler
modules remain the runtime authorities.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from src.read_only_authorization_manifest import (
    PINNED_IMPLEMENTATION_CLOSURE,
    checkpoint_implementation_digest,
    resolve_head_sha,
)
from src.read_only_deployment_profile import BrokerWriteCapability


SCHEMA_VERSION = 1
DEPLOYMENT_PROFILE_ID = "deployment-profile-supervised-read-only-v1"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{7,95}$")
_PRIVATE_TERMS = (
    "account", "balance", "credential", "endpoint", "exception", "hostname",
    "ip", "manifest", "order", "password", "price", "quantity", "secret",
    "symbol", "token", "username", "url", "user-home", "onedrive",
)


class LifecycleError(ValueError):
    """The contract or modeled observation is malformed."""


class LifecycleIdentityError(LifecycleError):
    """The contract does not bind to the checked-out release."""


class LifecycleSafetyError(LifecycleError):
    """The contract weakens the supervised read-only boundary."""


class LifecycleCompatibilityError(LifecycleError):
    """The contract is incompatible with the existing deployment profile."""


class StartupMode(str, Enum):
    MANUAL_OPERATOR_START = "MANUAL_OPERATOR_START"


class StaleLockPolicy(str, Enum):
    FAIL_CLOSED_REQUIRE_REVIEW = "FAIL_CLOSED_REQUIRE_REVIEW"


class StartupTimeoutClass(str, Enum):
    BOUNDED_OPERATOR_PREFLIGHT = "BOUNDED_OPERATOR_PREFLIGHT"


class HeartbeatIntervalClass(str, Enum):
    BOUNDED_BY_EXISTING_LIVENESS_POLICY = "BOUNDED_BY_EXISTING_LIVENESS_POLICY"


class MissedHeartbeatPolicy(str, Enum):
    SUSPEND_AND_ALERT = "SUSPEND_AND_ALERT"


class ShutdownMode(str, Enum):
    CONTROLLED_OPERATOR_SHUTDOWN = "CONTROLLED_OPERATOR_SHUTDOWN"


class DrainPolicy(str, Enum):
    COMPLETE_OR_FAIL_CLOSED = "COMPLETE_OR_FAIL_CLOSED"


class ShutdownTimeoutClass(str, Enum):
    BOUNDED_OPERATOR_SHUTDOWN = "BOUNDED_OPERATOR_SHUTDOWN"


class RestartMode(str, Enum):
    MANUAL_REVIEWED_RESTART = "MANUAL_REVIEWED_RESTART"


class CleanRestartPolicy(str, Enum):
    ALLOWED_AFTER_PREFLIGHT = "ALLOWED_AFTER_PREFLIGHT"


class CrashRestartPolicy(str, Enum):
    REQUIRE_RECOVERY_REVIEW = "REQUIRE_RECOVERY_REVIEW"


class AmbiguousRestartPolicy(str, Enum):
    PROHIBITED_UNTIL_REVIEW = "PROHIBITED_UNTIL_REVIEW"


class ModeledLockOutcome(str, Enum):
    OWNER_ACTIVE = "OWNER_ACTIVE"
    DUPLICATE_OWNER_REFUSED = "DUPLICATE_OWNER_REFUSED"
    STALE_LOCK_REVIEW_REQUIRED = "STALE_LOCK_REVIEW_REQUIRED"
    CLEAN_SHUTDOWN_RELEASED = "CLEAN_SHUTDOWN_RELEASED"
    CRASH_RECOVERY_REVIEW_REQUIRED = "CRASH_RECOVERY_REVIEW_REQUIRED"


LIFECYCLE_KEYS = frozenset({
    "schema_version", "lifecycle_contract_id", "deployment_profile_id",
    "release_commit_sha", "implementation_digest", "startup_mode",
    "operator_presence_required", "preflight_validation_required",
    "single_instance_required", "process_lock_required", "stale_lock_policy",
    "startup_timeout_class", "heartbeat_required", "heartbeat_interval_class",
    "missed_heartbeat_policy", "liveness_monitor_required",
    "suspension_controller_required", "alert_confirmation_required",
    "operator_acknowledgment_required", "shutdown_mode",
    "stop_accepting_new_cycles_first", "drain_inflight_cycle_policy",
    "durable_state_flush_required", "final_heartbeat_required",
    "shutdown_receipt_required", "shutdown_timeout_class", "restart_mode",
    "restart_after_clean_shutdown", "restart_after_crash",
    "restart_after_ambiguous_state", "operator_review_required_after_ambiguity",
    "replay_prevention_required", "stale_process_detection_required",
    "operator_role_required", "alert_owner_required", "incident_owner_required",
    "rollback_owner_required", "strategy_enabled",
    "activation_approvals_present", "approved_protection_state_count",
    "protection_grounded", "activation_approved", "permits_automated_action",
    "broker_write_capability", "live_ready", "unattended_ready",
})


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LifecycleError(f"{field} is invalid")
    if "://" in value or any(term in value.casefold() for term in _PRIVATE_TERMS):
        raise LifecycleError(f"{field} contains prohibited text")
    return value


def _enum(enum_type: type[Enum], value: object, field: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise LifecycleError(f"{field} is unsupported") from None


@dataclass(frozen=True)
class HostProcessLifecycleContract:
    schema_version: int
    lifecycle_contract_id: str
    deployment_profile_id: str
    release_commit_sha: str
    implementation_digest: str
    startup_mode: StartupMode
    operator_presence_required: bool
    preflight_validation_required: bool
    single_instance_required: bool
    process_lock_required: bool
    stale_lock_policy: StaleLockPolicy
    startup_timeout_class: StartupTimeoutClass
    heartbeat_required: bool
    heartbeat_interval_class: HeartbeatIntervalClass
    missed_heartbeat_policy: MissedHeartbeatPolicy
    liveness_monitor_required: bool
    suspension_controller_required: bool
    alert_confirmation_required: bool
    operator_acknowledgment_required: bool
    shutdown_mode: ShutdownMode
    stop_accepting_new_cycles_first: bool
    drain_inflight_cycle_policy: DrainPolicy
    durable_state_flush_required: bool
    final_heartbeat_required: bool
    shutdown_receipt_required: bool
    shutdown_timeout_class: ShutdownTimeoutClass
    restart_mode: RestartMode
    restart_after_clean_shutdown: CleanRestartPolicy
    restart_after_crash: CrashRestartPolicy
    restart_after_ambiguous_state: AmbiguousRestartPolicy
    operator_review_required_after_ambiguity: bool
    replay_prevention_required: bool
    stale_process_detection_required: bool
    operator_role_required: bool
    alert_owner_required: bool
    incident_owner_required: bool
    rollback_owner_required: bool
    strategy_enabled: bool
    activation_approvals_present: bool
    approved_protection_state_count: int
    protection_grounded: bool
    activation_approved: bool
    permits_automated_action: bool
    broker_write_capability: BrokerWriteCapability
    live_ready: bool
    unattended_ready: bool

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "HostProcessLifecycleContract":
        if not isinstance(raw, Mapping) or set(raw) != LIFECYCLE_KEYS:
            raise LifecycleError("lifecycle schema is not exact")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
            raise LifecycleError("schema version is unsupported")
        enum_fields = {
            "startup_mode": (StartupMode, raw["startup_mode"]),
            "stale_lock_policy": (StaleLockPolicy, raw["stale_lock_policy"]),
            "startup_timeout_class": (StartupTimeoutClass, raw["startup_timeout_class"]),
            "heartbeat_interval_class": (HeartbeatIntervalClass, raw["heartbeat_interval_class"]),
            "missed_heartbeat_policy": (MissedHeartbeatPolicy, raw["missed_heartbeat_policy"]),
            "shutdown_mode": (ShutdownMode, raw["shutdown_mode"]),
            "drain_inflight_cycle_policy": (DrainPolicy, raw["drain_inflight_cycle_policy"]),
            "shutdown_timeout_class": (ShutdownTimeoutClass, raw["shutdown_timeout_class"]),
            "restart_mode": (RestartMode, raw["restart_mode"]),
            "restart_after_clean_shutdown": (CleanRestartPolicy, raw["restart_after_clean_shutdown"]),
            "restart_after_crash": (CrashRestartPolicy, raw["restart_after_crash"]),
            "restart_after_ambiguous_state": (AmbiguousRestartPolicy, raw["restart_after_ambiguous_state"]),
            "broker_write_capability": (BrokerWriteCapability, raw["broker_write_capability"]),
        }
        values: dict[str, object] = dict(raw)
        values["lifecycle_contract_id"] = _text(raw["lifecycle_contract_id"], "lifecycle_contract_id")
        values["deployment_profile_id"] = _text(raw["deployment_profile_id"], "deployment_profile_id")
        values["release_commit_sha"] = _text(raw["release_commit_sha"], "release_commit_sha")
        values["implementation_digest"] = _text(raw["implementation_digest"], "implementation_digest")
        for field, (enum_type, raw_value) in enum_fields.items():
            values[field] = _enum(enum_type, raw_value, field)
        profile = cls(**values)
        profile._validate_types()
        return profile

    def _validate_types(self) -> None:
        if not _IDENTIFIER.fullmatch(self.lifecycle_contract_id) or not self.lifecycle_contract_id.startswith("lifecycle-contract-"):
            raise LifecycleError("lifecycle_contract_id is invalid")
        if not _SHA.fullmatch(self.release_commit_sha) or not _DIGEST.fullmatch(self.implementation_digest):
            raise LifecycleIdentityError("release identity is malformed")
        if type(self.approved_protection_state_count) is not int:
            raise LifecycleError("protection count is invalid")
        bool_fields = [name for name in LIFECYCLE_KEYS if name not in {
            "schema_version", "lifecycle_contract_id", "deployment_profile_id",
            "release_commit_sha", "implementation_digest", "startup_mode",
            "approved_protection_state_count",
            "stale_lock_policy", "startup_timeout_class", "heartbeat_interval_class",
            "missed_heartbeat_policy", "shutdown_mode", "drain_inflight_cycle_policy",
            "shutdown_timeout_class", "restart_mode", "restart_after_clean_shutdown",
            "restart_after_crash", "restart_after_ambiguous_state", "broker_write_capability",
        }]
        if any(type(getattr(self, field)) is not bool for field in bool_fields):
            raise LifecycleError("lifecycle scalar types are invalid")

    def validate(self, *, repository_root: Path) -> None:
        self._validate_types()
        if self.deployment_profile_id != DEPLOYMENT_PROFILE_ID:
            raise LifecycleCompatibilityError("deployment profile is incompatible")
        required_true = (
            "operator_presence_required", "preflight_validation_required", "single_instance_required",
            "process_lock_required", "heartbeat_required", "liveness_monitor_required",
            "suspension_controller_required", "alert_confirmation_required",
            "operator_acknowledgment_required", "stop_accepting_new_cycles_first",
            "durable_state_flush_required", "final_heartbeat_required", "shutdown_receipt_required",
            "operator_review_required_after_ambiguity", "replay_prevention_required",
            "stale_process_detection_required", "operator_role_required", "alert_owner_required",
            "incident_owner_required", "rollback_owner_required",
        )
        if any(getattr(self, field) is not True for field in required_true):
            raise LifecycleSafetyError("required lifecycle control is disabled")
        if (
            self.startup_mode is not StartupMode.MANUAL_OPERATOR_START
            or self.stale_lock_policy is not StaleLockPolicy.FAIL_CLOSED_REQUIRE_REVIEW
            or self.heartbeat_interval_class is not HeartbeatIntervalClass.BOUNDED_BY_EXISTING_LIVENESS_POLICY
            or self.missed_heartbeat_policy is not MissedHeartbeatPolicy.SUSPEND_AND_ALERT
            or self.shutdown_mode is not ShutdownMode.CONTROLLED_OPERATOR_SHUTDOWN
            or self.drain_inflight_cycle_policy is not DrainPolicy.COMPLETE_OR_FAIL_CLOSED
            or self.restart_mode is not RestartMode.MANUAL_REVIEWED_RESTART
            or self.restart_after_clean_shutdown is not CleanRestartPolicy.ALLOWED_AFTER_PREFLIGHT
            or self.restart_after_crash is not CrashRestartPolicy.REQUIRE_RECOVERY_REVIEW
            or self.restart_after_ambiguous_state is not AmbiguousRestartPolicy.PROHIBITED_UNTIL_REVIEW
        ):
            raise LifecycleSafetyError("lifecycle policy is not fail closed")
        if (
            self.strategy_enabled or self.activation_approvals_present
            or self.approved_protection_state_count != 0 or self.protection_grounded
            or self.activation_approved or self.permits_automated_action
            or self.broker_write_capability is not BrokerWriteCapability.ABSENT
            or self.live_ready or self.unattended_ready
        ):
            raise LifecycleSafetyError("safety boundary is invalid")
        if self.release_commit_sha != resolve_head_sha(repository_root):
            raise LifecycleIdentityError("release commit does not match repository")
        if self.implementation_digest != checkpoint_implementation_digest(repository_root):
            raise LifecycleIdentityError("implementation digest does not match repository")
        # 20 through M13 Issue 4; +6 in M13 Issue 5 for the protection-
        # grounding integration reached from the checkpoint launcher; +1 in
        # M13 Issue 7 for the assessment store reached through the
        # launcher's optional --protection-assessment-store persistence
        # call; +1 in M14 Issue 1 for the execution-safety-governance
        # boundary reached from robinhood_commissioning_adapter.py and
        # one_share_commissioning_orchestrator.py. This is a deliberate
        # manual re-pin gate, not a live comparison: a closure-size change
        # must be consciously reviewed and this literal bumped, exactly as
        # it was here.
        if len(PINNED_IMPLEMENTATION_CLOSURE) != 28:
            raise LifecycleIdentityError("pinned implementation closure is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            field.name: (
                value.value if isinstance(value := getattr(self, field.name), Enum) else value
            )
            for field in self.__dataclass_fields__.values()
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"


def validate_lifecycle_contract(contract: HostProcessLifecycleContract, *, repository_root: Path) -> HostProcessLifecycleContract:
    if not isinstance(contract, HostProcessLifecycleContract):
        raise LifecycleError("a typed lifecycle contract is required")
    contract.validate(repository_root=repository_root)
    return contract


def evaluate_modeled_lock(
    contract: HostProcessLifecycleContract,
    *,
    lock_contract_id: str,
    lock_release_sha: str,
    owner_id: str,
    existing_owner_id: str | None = None,
    stale: bool = False,
    clean_shutdown: bool = False,
    crashed: bool = False,
) -> ModeledLockOutcome:
    """Evaluate a temporary fixture; never acquires or persists a real lock."""
    if not isinstance(contract, HostProcessLifecycleContract):
        raise LifecycleError("a typed lifecycle contract is required")
    if lock_contract_id != contract.lifecycle_contract_id or lock_release_sha != contract.release_commit_sha:
        raise LifecycleIdentityError("modeled lock identity does not match contract")
    if not _IDENTIFIER.fullmatch(owner_id) or not owner_id.startswith("operator-"):
        raise LifecycleError("modeled owner identity is invalid")
    if existing_owner_id is not None and (not _IDENTIFIER.fullmatch(existing_owner_id) or not existing_owner_id.startswith("operator-")):
        raise LifecycleError("existing modeled owner identity is invalid")
    if stale:
        return ModeledLockOutcome.STALE_LOCK_REVIEW_REQUIRED
    if crashed:
        return ModeledLockOutcome.CRASH_RECOVERY_REVIEW_REQUIRED
    if clean_shutdown:
        return ModeledLockOutcome.CLEAN_SHUTDOWN_RELEASED
    if existing_owner_id is not None:
        return ModeledLockOutcome.DUPLICATE_OWNER_REFUSED
    return ModeledLockOutcome.OWNER_ACTIVE


__all__ = [
    "AmbiguousRestartPolicy", "CleanRestartPolicy", "DEPLOYMENT_PROFILE_ID",
    "DrainPolicy", "HeartbeatIntervalClass", "HostProcessLifecycleContract",
    "LifecycleCompatibilityError", "LifecycleError", "LifecycleIdentityError",
    "LifecycleSafetyError", "LIFECYCLE_KEYS", "MissedHeartbeatPolicy",
    "ModeledLockOutcome", "RestartMode", "SCHEMA_VERSION", "ShutdownMode",
    "ShutdownTimeoutClass", "StartupMode", "StartupTimeoutClass",
    "StaleLockPolicy", "evaluate_modeled_lock", "validate_lifecycle_contract",
]
