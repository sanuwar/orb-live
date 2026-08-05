from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.host_process_lifecycle import LIFECYCLE_KEYS
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
from src.read_only_deployment_profile import PROFILE_KEYS
from src.supervised_operator_console import (
    EVIDENCE_KEYS,
    ConsoleError,
    ConsoleIdentityError,
    ConsoleSafetyError,
    OperatorAction,
    RESULT_KEYS,
    load_console_snapshot,
    render_text,
)

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc)


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "evidence").mkdir(exist_ok=True)
    profile = json.loads((ROOT / "examples/read-only-deployment/valid-supervised-read-only-profile.json").read_text())
    profile["release_commit_sha"] = resolve_head_sha(ROOT)
    profile["implementation_digest"] = checkpoint_implementation_digest(ROOT)
    (root / "config/deployment-profile.json").write_text(json.dumps(profile), encoding="utf-8")
    lifecycle = json.loads((ROOT / "examples/read-only-deployment/host-process-lifecycle.json").read_text())
    lifecycle["release_commit_sha"] = resolve_head_sha(ROOT)
    lifecycle["implementation_digest"] = checkpoint_implementation_digest(ROOT)
    (root / "config/lifecycle-contract.json").write_text(json.dumps(lifecycle), encoding="utf-8")
    evidence = {
        "schema_version": 1, "evidence_as_of": "2026-08-05T15:00:00Z", "operating_mode": "SUPERVISED_READ_ONLY",
        "scheduler_status": "IDLE", "runner_status": "BOUNDED_IDLE", "suspension_status": "CLEAR",
        "heartbeat_status": "CURRENT", "alert_status": "CONFIRMED", "acknowledgment_status": "COMPLETE",
        "recovery_status": "NOT_AVAILABLE", "operational_status": "OFFLINE_HEALTHY",
        "supervised_readiness_status": "REVIEW_ONLY", "blocker_reasons": [],
        "strategy_enabled": False, "activation_approvals_present": False,
        "approved_protection_state_count": 0, "protection_grounded": False,
        "activation_approved": False, "permits_automated_action": False,
        "live_ready": False, "unattended_ready": False, "evidence_completeness": "COMPLETE",
        "evidence_freshness": "CURRENT",
        "source_component_statuses": {key: "AVAILABLE" for key in ("scheduler", "runner", "suspension", "heartbeat", "alerts", "acknowledgment", "operational_status", "readiness")},
        "release_commit_sha": resolve_head_sha(ROOT), "implementation_digest": checkpoint_implementation_digest(ROOT),
    }
    # Identity is part of the evidence contract even though it is not a display field.
    evidence["release_commit_sha"] = resolve_head_sha(ROOT)
    evidence["implementation_digest"] = checkpoint_implementation_digest(ROOT)
    (root / "evidence/operator-console-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    return root


def _load(tmp_path: Path, **changes):
    root = _fixture(tmp_path)
    path = root / "evidence/operator-console-evidence.json"
    raw = json.loads(path.read_text())
    raw.update(changes)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_console_snapshot(runtime_root=root, as_of=AS_OF)


def test_healthy_snapshot_and_exact_json_schema(tmp_path):
    result = _load(tmp_path)
    assert set(result.to_dict()) == RESULT_KEYS
    assert result.recommended_operator_action is OperatorAction.NO_ACTION_OFFLINE_STATE_HEALTHY
    assert result.to_dict()["source_component_statuses"] == dict(sorted(result.to_dict()["source_component_statuses"].items()))


def test_text_has_required_sections_and_no_private_values(tmp_path):
    text = render_text(_load(tmp_path))
    assert "ORB-LIVE SUPERVISED OPERATOR CONSOLE" in text
    for section in ("Release:", "Runtime:", "Readiness:", "Blockers:", "Operator action:"):
        assert section in text
    assert "account" not in text.casefold()


@pytest.mark.parametrize(("field", "value", "action"), [
    ("suspension_status", "ACTIVE", OperatorAction.REVIEW_ACTIVE_SUSPENSION),
    ("recovery_status", "AMBIGUOUS", OperatorAction.REVIEW_AMBIGUOUS_RECOVERY),
    ("heartbeat_status", "STALE", OperatorAction.REVIEW_STALE_HEARTBEAT),
    ("alert_status", "UNCONFIRMED", OperatorAction.CONFIRM_ALERT_DELIVERY),
    ("acknowledgment_status", "PENDING", OperatorAction.COMPLETE_OPERATOR_REVIEW),
    ("operational_status", "CONFIGURATION_REVIEW_REQUIRED", OperatorAction.VERIFY_DEPLOYMENT_CONFIGURATION),
    ("evidence_completeness", "PARTIAL", OperatorAction.FAIL_CLOSED_INCOMPLETE_EVIDENCE),
    ("evidence_freshness", "STALE", OperatorAction.FAIL_CLOSED_INCOMPLETE_EVIDENCE),
])
def test_deterministic_action_dominance(tmp_path, field, value, action):
    assert _load(tmp_path, **{field: value}).recommended_operator_action is action


def test_identity_and_time_fail_closed(tmp_path):
    with pytest.raises(ConsoleIdentityError):
        _load(tmp_path, release_commit_sha="0" * 40)
    root = _fixture(tmp_path)
    with pytest.raises(ConsoleError, match="timezone-aware"):
        load_console_snapshot(runtime_root=root, as_of=datetime(2026, 8, 5, 16))
    with pytest.raises(ConsoleError, match="future"):
        _load(tmp_path, evidence_as_of="2026-08-05T17:00:00Z")


@pytest.mark.parametrize("field", ["strategy_enabled", "activation_approvals_present", "protection_grounded", "activation_approved", "permits_automated_action", "live_ready", "unattended_ready"])
def test_safety_boundary_rejects_true(field, tmp_path):
    with pytest.raises(ConsoleSafetyError):
        _load(tmp_path, **{field: True})


def test_protection_count_and_schema_are_closed(tmp_path):
    with pytest.raises(ConsoleSafetyError):
        _load(tmp_path, approved_protection_state_count=1)
    root = _fixture(tmp_path)
    path = root / "evidence/operator-console-evidence.json"
    raw = json.loads(path.read_text())
    raw["unexpected"] = True
    path.write_text(json.dumps(raw))
    with pytest.raises(ConsoleError):
        load_console_snapshot(runtime_root=root, as_of=AS_OF)


def test_missing_component_unsafe_root_and_privacy_are_rejected(tmp_path):
    root = _fixture(tmp_path)
    path = root / "evidence/operator-console-evidence.json"
    raw = json.loads(path.read_text())
    del raw["source_component_statuses"]["runner"]
    path.write_text(json.dumps(raw))
    with pytest.raises(ConsoleError):
        load_console_snapshot(runtime_root=root, as_of=AS_OF)
    with pytest.raises(ConsoleError):
        load_console_snapshot(runtime_root=ROOT, as_of=AS_OF)
    root = _fixture(tmp_path / "private")
    path = root / "evidence/operator-console-evidence.json"
    raw = json.loads(path.read_text())
    raw["blocker_reasons"] = ["C:\\Users\\private"]
    path.write_text(json.dumps(raw))
    with pytest.raises(ConsoleError):
        load_console_snapshot(runtime_root=root, as_of=AS_OF)


def test_contract_input_keys_are_exact():
    assert "schema_version" in EVIDENCE_KEYS
    assert len(PROFILE_KEYS) > 0
    assert len(LIFECYCLE_KEYS) > 0
