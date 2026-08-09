from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts.validate_read_only_deployment_profile import main as validate_cli
from src.execution_gates import ACTIVATION_APPROVALS
from src.read_only_authorization_manifest import (
    PINNED_IMPLEMENTATION_CLOSURE, checkpoint_implementation_digest, resolve_head_sha,
)
from src.read_only_deployment_profile import (
    ProfileError,
    ProfileIdentityError,
    ProfilePathError,
    ProfileSafetyError,
    ReadOnlyDeploymentProfile,
    validate_profile,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "read-only-deployment" / "valid-supervised-read-only-profile.json"


def valid_raw() -> dict[str, object]:
    value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    value["release_commit_sha"] = resolve_head_sha(ROOT)
    value["implementation_digest"] = checkpoint_implementation_digest(ROOT)
    value["pinned_file_count"] = len(PINNED_IMPLEMENTATION_CLOSURE)
    return value


def profile() -> ReadOnlyDeploymentProfile:
    value = ReadOnlyDeploymentProfile.from_dict(valid_raw())
    return validate_profile(value, repository_root=ROOT)


def rejected(mutator, error=ProfileError) -> None:
    value = valid_raw()
    mutator(value)
    with pytest.raises(error):
        candidate = ReadOnlyDeploymentProfile.from_dict(value)
        validate_profile(candidate, repository_root=ROOT)


def test_valid_profile_is_accepted_and_is_frozen() -> None:
    value = profile()
    assert value.operating_mode.value == "SUPERVISED_READ_ONLY"
    assert value.broker_write_capability.value == "ABSENT"
    assert value.strategy_enabled is False
    assert value.activation_approvals_present is False
    assert value.approved_protection_state_count == 0
    assert value.permits_automated_action is False
    assert value.__dataclass_params__.frozen is True


def test_exact_schema_and_enums_are_enforced() -> None:
    rejected(lambda value: value.update(extra_field=True))
    rejected(lambda value: value.pop("timezone_name"))
    rejected(lambda value: value.update(environment_class="LIVE"))
    rejected(lambda value: value.update(operating_mode="UNATTENDED_READY"))


@pytest.mark.parametrize("field", [
    "strategy_enabled", "activation_approvals_present", "protection_grounded",
    "activation_approved", "permits_automated_action",
])
def test_safety_flags_are_closed(field: str) -> None:
    rejected(lambda value: value.update({field: True}), ProfileSafetyError)


def test_protection_count_and_broker_write_are_closed() -> None:
    rejected(lambda value: value.update(approved_protection_state_count=1), ProfileSafetyError)
    rejected(lambda value: value.update(broker_write_capability="PRESENT"))


@pytest.mark.parametrize("field", ["startup_mode", "restart_policy", "operator_supervision_required", "heartbeat_required", "alert_confirmation_required"])
def test_lifecycle_requirements_are_closed(field: str) -> None:
    value = valid_raw()
    value[field] = {"startup_mode": "AUTOMATIC", "restart_policy": "AUTOMATIC_RESTART", "operator_supervision_required": False, "heartbeat_required": False, "alert_confirmation_required": False}[field]
    with pytest.raises((ProfileError, ProfileSafetyError)):
        candidate = ReadOnlyDeploymentProfile.from_dict(value)
        validate_profile(candidate, repository_root=ROOT)


@pytest.mark.parametrize("field,value", [
    ("release_commit_sha", "not-a-sha"),
    ("implementation_digest", "0" * 63),
    ("pinned_file_count", 19),
    ("schema_version", 2),
])
def test_release_identity_is_bound(field: str, value: object) -> None:
    expected = ProfileError if field == "schema_version" else ProfileIdentityError
    rejected(lambda raw: raw.update({field: value}), expected)


def test_release_substitution_and_serialized_identity_tampering_are_rejected() -> None:
    rejected(lambda value: value.update(release_commit_sha="0" * 40), ProfileIdentityError)
    original = profile().serialize()
    altered = json.loads(original)
    altered["implementation_digest"] = "0" * 64
    with pytest.raises(ProfileIdentityError):
        validate_profile(ReadOnlyDeploymentProfile.from_dict(altered), repository_root=ROOT)


@pytest.mark.parametrize("field,value", [
    ("runtime_data_directory", "C:/private"),
    ("database_directory", "~/private"),
    ("log_directory", "src/logs"),
    ("evidence_directory", "data/evidence"),
    ("backup_directory", "runtime-data/../backup"),
    ("temporary_directory", ":memory:"),
])
def test_path_policy_rejects_private_source_and_memory_paths(field: str, value: str) -> None:
    rejected(lambda raw: raw.update({field: value}), ProfilePathError)


def test_path_policy_rejects_duplicate_directories_and_symlink_escape(tmp_path: Path) -> None:
    value = valid_raw()
    value["log_directory"] = value["database_directory"]
    with pytest.raises(ProfilePathError):
        validate_profile(ReadOnlyDeploymentProfile.from_dict(value), repository_root=ROOT, filesystem_root=tmp_path)
    outside = tmp_path.parent / "orb-live-profile-outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "runtime-data"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable in this Windows test environment")
    with pytest.raises(ProfilePathError):
        validate_profile(ReadOnlyDeploymentProfile.from_dict(valid_raw()), repository_root=ROOT, filesystem_root=tmp_path)


@pytest.mark.parametrize("field,value", [
    ("environment_name", "account-123"), ("environment_name", "credential-value"),
    ("environment_name", "symbol-AAPL"), ("environment_name", "quantity-1"),
    ("environment_name", "price-1"), ("environment_name", "balance-1"),
    ("environment_name", "order-1"), ("timezone_name", "https://host"),
    ("repository_root_policy", "password-value"), ("environment_name", "raw-exception"),
])
def test_privacy_and_network_values_are_not_profile_inputs(field: str, value: object) -> None:
    rejected(lambda raw: raw.update({field: value}))


def test_typed_api_does_not_accept_enum_bypass() -> None:
    value = ReadOnlyDeploymentProfile.from_dict(valid_raw())
    object.__setattr__(value, "operating_mode", "SUPERVISED_READ_ONLY")
    with pytest.raises(ProfileError):
        validate_profile(value, repository_root=ROOT)


def test_serialization_is_deterministic_and_has_no_private_paths() -> None:
    value = profile()
    assert value.serialize() == value.serialize()
    rendered = value.serialize().casefold()
    assert "c:\\" not in rendered and "/users/" not in rendered
    assert "password" not in rendered and "account" not in rendered
    assert json.loads(value.serialize()) == value.to_dict()


def test_examples_have_expected_fixed_failures() -> None:
    invalid_write = ROOT / "examples" / "read-only-deployment" / "invalid-write-capability-profile.json"
    invalid_release = ROOT / "examples" / "read-only-deployment" / "invalid-release-identity-profile.json"
    assert validate_cli(["--profile", str(invalid_write)]) == 10
    assert validate_cli(["--profile", str(invalid_release)]) == 20


def test_cli_valid_result_is_not_deployment_authorization(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "profile.json"
    path.write_text(profile().serialize(), encoding="utf-8")
    assert validate_cli(["--profile", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "VALID_FOR_OFFLINE_CONFIGURATION_REVIEW"
    assert "authorize" not in capsys.readouterr().out.casefold()


def test_cli_has_no_network_broker_or_runtime_execution_imports() -> None:
    tree = ast.parse((ROOT / "scripts" / "validate_read_only_deployment_profile.py").read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(any(token in name for token in ("socket", "urllib", "requests", "robinhood", "broker", "manifest")) for name in imports)
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"sleep", "now", "utcnow", "monotonic"} for node in ast.walk(tree))


def test_profile_validation_does_not_change_runtime_state() -> None:
    before = tuple(ACTIVATION_APPROVALS)
    profile()
    assert tuple(ACTIVATION_APPROVALS) == before == ()
