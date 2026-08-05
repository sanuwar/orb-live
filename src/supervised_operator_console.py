"""One-shot, read-only projection of privacy-safe ORB-LIVE evidence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from src.host_process_lifecycle import HostProcessLifecycleContract, validate_lifecycle_contract
from src.offline_path_safety import is_repository_data_path
from src.offline_recovery_package import validate_recovery_package
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
from src.read_only_deployment_profile import ReadOnlyDeploymentProfile, validate_profile


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONSOLE_SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 1
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_PATH = Path("evidence") / "operator-console-evidence.json"
PROFILE_PATH = Path("config") / "deployment-profile.json"
LIFECYCLE_PATH = Path("config") / "lifecycle-contract.json"


class ConsoleError(ValueError):
    """The console input is invalid or cannot be safely projected."""


class ConsoleIdentityError(ConsoleError):
    """Evidence does not bind to the checked-out release."""


class ConsoleSafetyError(ConsoleError):
    """Evidence attempts to widen a closed safety boundary."""


class OperatorAction(str, Enum):
    REVIEW_ACTIVE_SUSPENSION = "REVIEW_ACTIVE_SUSPENSION"
    CONFIRM_ALERT_DELIVERY = "CONFIRM_ALERT_DELIVERY"
    REVIEW_STALE_HEARTBEAT = "REVIEW_STALE_HEARTBEAT"
    REVIEW_AMBIGUOUS_RECOVERY = "REVIEW_AMBIGUOUS_RECOVERY"
    COMPLETE_OPERATOR_REVIEW = "COMPLETE_OPERATOR_REVIEW"
    VERIFY_DEPLOYMENT_CONFIGURATION = "VERIFY_DEPLOYMENT_CONFIGURATION"
    NO_ACTION_OFFLINE_STATE_HEALTHY = "NO_ACTION_OFFLINE_STATE_HEALTHY"
    FAIL_CLOSED_INCOMPLETE_EVIDENCE = "FAIL_CLOSED_INCOMPLETE_EVIDENCE"


RESULT_KEYS = frozenset({
    "schema_version", "release_commit_sha", "implementation_digest", "generated_as_of",
    "operating_mode", "scheduler_status", "runner_status", "suspension_status",
    "heartbeat_status", "alert_status", "acknowledgment_status", "recovery_status",
    "operational_status", "supervised_readiness_status", "blocker_reasons",
    "recommended_operator_action", "strategy_enabled", "activation_approvals_present",
    "approved_protection_state_count", "protection_grounded", "activation_approved",
    "permits_automated_action", "live_ready", "unattended_ready", "evidence_completeness",
    "evidence_freshness", "source_component_statuses",
})
EVIDENCE_KEYS = frozenset({
    "schema_version", "evidence_as_of", "release_commit_sha", "implementation_digest", "operating_mode", "scheduler_status", "runner_status",
    "suspension_status", "heartbeat_status", "alert_status", "acknowledgment_status",
    "recovery_status", "operational_status", "supervised_readiness_status", "blocker_reasons",
    "strategy_enabled", "activation_approvals_present", "approved_protection_state_count",
    "protection_grounded", "activation_approved", "permits_automated_action", "live_ready",
    "unattended_ready", "evidence_completeness", "evidence_freshness", "source_component_statuses",
})
_COMPONENTS = ("scheduler", "runner", "suspension", "heartbeat", "alerts", "acknowledgment", "operational_status", "readiness")
_PRIVATE_TERMS = ("account", "balance", "credential", "exception", "order", "password", "price", "quantity", "secret", "symbol", "token", "username", "url", "ip", "path")
_BAD_VALUES = {"LIVE_READY", "UNATTENDED_READY", "DEPLOYMENT_APPROVED", "BROKER_APPROVED", "WRITE_ENABLED"}


def _reject_private(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if any(term in str(key).casefold() for term in _PRIVATE_TERMS):
                raise ConsoleError("privacy-prohibited field")
            _reject_private(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_private(nested)
    elif isinstance(value, str):
        lowered = value.casefold()
        if "://" in value or any(term in lowered for term in ("c:\\", "/users/", "\\users\\", "onedrive", "exception:")):
            raise ConsoleError("privacy-prohibited value")


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ConsoleError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ConsoleError(f"{field} is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConsoleError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConsoleError("evidence is unavailable or malformed") from exc
    if not isinstance(raw, dict) or set(raw) != EVIDENCE_KEYS:
        raise ConsoleError("evidence schema is not exact")
    _reject_private(raw)
    return raw


def _safe_root(runtime_root: Path) -> Path:
    try:
        root = runtime_root.resolve(strict=True)
    except OSError as exc:
        raise ConsoleError("runtime root is unavailable") from exc
    if not root.is_dir() or is_repository_data_path(root) or root == REPOSITORY_ROOT or REPOSITORY_ROOT in root.parents:
        raise ConsoleError("runtime root is unsafe")
    if any(part.casefold() in {"src", "tests", "test", "examples"} for part in root.parts):
        raise ConsoleError("runtime root is unsafe")
    return root


def _validate_identity(raw: Mapping[str, Any], repository_root: Path) -> tuple[str, str]:
    commit = raw.get("release_commit_sha")
    digest = raw.get("implementation_digest")
    if not isinstance(commit, str) or not _SHA.fullmatch(commit) or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ConsoleIdentityError("release identity is malformed")
    expected_commit = resolve_head_sha(repository_root)
    expected_digest = checkpoint_implementation_digest(repository_root)
    if commit != expected_commit or digest != expected_digest:
        raise ConsoleIdentityError("release identity does not match repository")
    return commit, digest


def _validate_contracts(root: Path, repository_root: Path) -> str:
    profile_path = root / PROFILE_PATH
    lifecycle_path = root / LIFECYCLE_PATH
    try:
        profile = ReadOnlyDeploymentProfile.from_dict(json.loads(profile_path.read_text(encoding="utf-8")))
        validate_profile(profile, repository_root=repository_root, filesystem_root=root)
        lifecycle = HostProcessLifecycleContract.from_dict(json.loads(lifecycle_path.read_text(encoding="utf-8")))
        validate_lifecycle_contract(lifecycle, repository_root=repository_root)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ConsoleError("deployment configuration is invalid or incomplete") from exc
    if lifecycle.deployment_profile_id != profile.deployment_profile_id:
        raise ConsoleIdentityError("deployment configuration identities conflict")
    recovery_path = root / "recovery-package.json"
    if recovery_path.exists():
        try:
            validate_recovery_package(package_path=recovery_path, restore_root=root, repository_root=repository_root)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ConsoleError("recovery evidence is invalid") from exc
    return "VALIDATED"


def _action(raw: Mapping[str, Any]) -> OperatorAction:
    if raw["evidence_completeness"] != "COMPLETE" or raw["evidence_freshness"] != "CURRENT":
        return OperatorAction.FAIL_CLOSED_INCOMPLETE_EVIDENCE
    if raw["suspension_status"] in {"ACTIVE", "SUSPENDED"}:
        return OperatorAction.REVIEW_ACTIVE_SUSPENSION
    if raw["recovery_status"] in {"AMBIGUOUS", "REVIEW_REQUIRED", "FAILED_CLOSED"}:
        return OperatorAction.REVIEW_AMBIGUOUS_RECOVERY
    if raw["heartbeat_status"] in {"STALE", "MISSED", "UNKNOWN"}:
        return OperatorAction.REVIEW_STALE_HEARTBEAT
    if raw["alert_status"] in {"UNCONFIRMED", "DELIVERY_UNCONFIRMED"}:
        return OperatorAction.CONFIRM_ALERT_DELIVERY
    if raw["acknowledgment_status"] in {"PENDING", "REVIEW_REQUIRED"}:
        return OperatorAction.COMPLETE_OPERATOR_REVIEW
    if raw["operational_status"] in {"CONFIGURATION_INVALID", "CONFIGURATION_REVIEW_REQUIRED"}:
        return OperatorAction.VERIFY_DEPLOYMENT_CONFIGURATION
    return OperatorAction.NO_ACTION_OFFLINE_STATE_HEALTHY


@dataclass(frozen=True)
class SupervisedOperatorConsoleResult:
    schema_version: int
    release_commit_sha: str
    implementation_digest: str
    generated_as_of: str
    operating_mode: str
    scheduler_status: str
    runner_status: str
    suspension_status: str
    heartbeat_status: str
    alert_status: str
    acknowledgment_status: str
    recovery_status: str
    operational_status: str
    supervised_readiness_status: str
    blocker_reasons: tuple[str, ...]
    recommended_operator_action: OperatorAction
    strategy_enabled: bool
    activation_approvals_present: bool
    approved_protection_state_count: int
    protection_grounded: bool
    activation_approved: bool
    permits_automated_action: bool
    live_ready: bool
    unattended_ready: bool
    evidence_completeness: str
    evidence_freshness: str
    source_component_statuses: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_commit_sha": self.release_commit_sha,
            "implementation_digest": self.implementation_digest,
            "generated_as_of": self.generated_as_of,
            "operating_mode": self.operating_mode,
            "scheduler_status": self.scheduler_status,
            "runner_status": self.runner_status,
            "suspension_status": self.suspension_status,
            "heartbeat_status": self.heartbeat_status,
            "alert_status": self.alert_status,
            "acknowledgment_status": self.acknowledgment_status,
            "recovery_status": self.recovery_status,
            "operational_status": self.operational_status,
            "supervised_readiness_status": self.supervised_readiness_status,
            "blocker_reasons": list(self.blocker_reasons),
            "recommended_operator_action": self.recommended_operator_action.value,
            "strategy_enabled": self.strategy_enabled,
            "activation_approvals_present": self.activation_approvals_present,
            "approved_protection_state_count": self.approved_protection_state_count,
            "protection_grounded": self.protection_grounded,
            "activation_approved": self.activation_approved,
            "permits_automated_action": self.permits_automated_action,
            "live_ready": self.live_ready,
            "unattended_ready": self.unattended_ready,
            "evidence_completeness": self.evidence_completeness,
            "evidence_freshness": self.evidence_freshness,
            "source_component_statuses": dict(self.source_component_statuses),
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"


def load_console_snapshot(*, runtime_root: Path, as_of: datetime, repository_root: Path = REPOSITORY_ROOT) -> SupervisedOperatorConsoleResult:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ConsoleError("as_of must be timezone-aware")
    root = _safe_root(Path(runtime_root))
    evidence = _json(root / EVIDENCE_PATH)
    if type(evidence["schema_version"]) is not int or evidence["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise ConsoleError("evidence schema version is unsupported")
    evidence_time = _timestamp(evidence["evidence_as_of"], "evidence_as_of")
    as_of_utc = as_of.astimezone(timezone.utc)
    if evidence_time > as_of_utc:
        raise ConsoleError("evidence is future-dated")
    commit, digest = _validate_identity(evidence, repository_root)
    _validate_contracts(root, repository_root)
    for key in ("blocker_reasons", "source_component_statuses"):
        if not isinstance(evidence[key], (list, dict)):
            raise ConsoleError("evidence component is malformed")
    if set(evidence["source_component_statuses"]) != set(_COMPONENTS):
        raise ConsoleError("evidence components are incomplete")
    if any(not isinstance(value, str) or value in _BAD_VALUES for value in evidence["source_component_statuses"].values()):
        raise ConsoleError("evidence component status is invalid")
    for field in ("strategy_enabled", "activation_approvals_present", "protection_grounded", "activation_approved", "permits_automated_action", "live_ready", "unattended_ready"):
        if type(evidence[field]) is not bool:
            raise ConsoleError("safety evidence is malformed")
    if evidence["strategy_enabled"] or evidence["activation_approvals_present"] or evidence["approved_protection_state_count"] != 0 or evidence["protection_grounded"] or evidence["activation_approved"] or evidence["permits_automated_action"] or evidence["live_ready"] or evidence["unattended_ready"]:
        raise ConsoleSafetyError("safety boundary is not closed")
    action = _action(evidence)
    blockers = tuple(sorted(set(evidence["blocker_reasons"])))
    statuses = tuple((key, evidence["source_component_statuses"][key]) for key in sorted(_COMPONENTS))
    return SupervisedOperatorConsoleResult(
        schema_version=CONSOLE_SCHEMA_VERSION, release_commit_sha=commit, implementation_digest=digest,
        generated_as_of=as_of_utc.isoformat().replace("+00:00", "Z"),
        operating_mode=evidence["operating_mode"], scheduler_status=evidence["scheduler_status"],
        runner_status=evidence["runner_status"], suspension_status=evidence["suspension_status"],
        heartbeat_status=evidence["heartbeat_status"], alert_status=evidence["alert_status"],
        acknowledgment_status=evidence["acknowledgment_status"], recovery_status=evidence["recovery_status"],
        operational_status=evidence["operational_status"], supervised_readiness_status=evidence["supervised_readiness_status"],
        blocker_reasons=blockers, recommended_operator_action=action,
        strategy_enabled=evidence["strategy_enabled"], activation_approvals_present=evidence["activation_approvals_present"],
        approved_protection_state_count=evidence["approved_protection_state_count"], protection_grounded=evidence["protection_grounded"],
        activation_approved=evidence["activation_approved"], permits_automated_action=evidence["permits_automated_action"],
        live_ready=evidence["live_ready"], unattended_ready=evidence["unattended_ready"],
        evidence_completeness=evidence["evidence_completeness"], evidence_freshness=evidence["evidence_freshness"],
        source_component_statuses=statuses,
    )


def render_text(result: SupervisedOperatorConsoleResult) -> str:
    data = result.to_dict()
    blockers = "\n".join(f"  - {item}" for item in result.blocker_reasons) or "  - none"
    return "\n".join([
        "ORB-LIVE SUPERVISED OPERATOR CONSOLE", "", "Release:",
        f"  Commit: {data['release_commit_sha'][:12]}", f"  Digest: {data['implementation_digest'][:12]}",
        f"  Mode: {data['operating_mode']}", "", "Runtime:",
        f"  Scheduler: {data['scheduler_status']}", f"  Runner: {data['runner_status']}",
        f"  Suspension: {data['suspension_status']}", f"  Heartbeat: {data['heartbeat_status']}",
        f"  Alerts: {data['alert_status']}", f"  Acknowledgment: {data['acknowledgment_status']}",
        "", "Readiness:", f"  Operational status: {data['operational_status']}",
        f"  Supervised readiness: {data['supervised_readiness_status']}",
        f"  Live ready: {'YES' if data['live_ready'] else 'NO'}",
        f"  Unattended ready: {'YES' if data['unattended_ready'] else 'NO'}",
        f"  Automated action permitted: {'YES' if data['permits_automated_action'] else 'NO'}",
        "", "Blockers:", blockers, "", "Operator action:", f"  {data['recommended_operator_action']}", "",
    ])


__all__ = ["ConsoleError", "ConsoleIdentityError", "ConsoleSafetyError", "EVIDENCE_KEYS", "OperatorAction", "RESULT_KEYS", "SupervisedOperatorConsoleResult", "load_console_snapshot", "render_text"]
