from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from scripts.run_offline_commissioning_campaign import SCENARIOS, run_campaign
import scripts.verify_offline_campaign_acceptance as acceptance


CAMPAIGN_ID = "campaign-offline-20260803"
ACCEPTANCE_ID = "acceptance-offline-20260803"
VERIFIED_AT = "2026-08-03T14:00:00+00:00"
ACCEPTANCE_KEYS = {
    "schema_version", "acceptance_id", "campaign_id", "injected_verified_at",
    "campaign_index_hash", "scenario_count", "scenario_set_status",
    "identity_uniqueness_status", "hash_verification_status",
    "privacy_verification_status", "adverse_outcome_preservation_status",
    "safety_invariant_status", "operator_checklist_status", "acceptance_result",
    "blocker_reasons", "permits_automated_action", "live_ready",
    "unattended_ready", "protection_grounded", "activation_approved",
}


@pytest.fixture(scope="module")
def campaign_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("campaign-source") / "campaign"
    assert run_campaign(campaign_id=CAMPAIGN_ID, output=output) == 0
    return output


@pytest.fixture()
def campaign(tmp_path: Path, campaign_source: Path) -> Path:
    output = tmp_path / "campaign"
    shutil.copytree(campaign_source, output)
    return output


def _index(campaign: Path) -> dict:
    return json.loads((campaign / "commissioning-campaign-index.json").read_text(encoding="utf-8"))


def _write_index(campaign: Path, value: dict) -> None:
    (campaign / "commissioning-campaign-index.json").write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def _accept(campaign: Path, output: Path) -> int:
    return acceptance.main((
        "--campaign-output", str(campaign), "--acceptance-id", ACCEPTANCE_ID,
        "--verified-at", VERIFIED_AT, "--output", str(output),
    ))


def test_valid_campaign_is_accepted_with_exact_closed_schema(campaign: Path, tmp_path: Path) -> None:
    output = tmp_path / "offline-campaign-acceptance.json"
    assert _accept(campaign, output) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert set(result) == ACCEPTANCE_KEYS
    assert result["acceptance_result"] == "ACCEPTED_FOR_OFFLINE_EVIDENCE_REVIEW"
    assert result["scenario_count"] == 8
    assert result["blocker_reasons"] == []
    for key in ("permits_automated_action", "live_ready", "unattended_ready", "protection_grounded", "activation_approved"):
        assert result[key] is False


def test_missing_campaign_index_is_rejected(campaign: Path, tmp_path: Path) -> None:
    (campaign / "commissioning-campaign-index.json").unlink()
    assert _accept(campaign, tmp_path / "result.json") == 50


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order"])
def test_scenario_set_and_order_mutations_are_rejected(campaign: Path, tmp_path: Path, mutation: str) -> None:
    index = _index(campaign)
    if mutation == "missing":
        index["scenarios"] = index["scenarios"][:-1]
    elif mutation == "duplicate":
        index["scenarios"][1] = dict(index["scenarios"][0])
    else:
        index["scenario_order"] = list(reversed(index["scenario_order"]))
    _write_index(campaign, index)
    assert _accept(campaign, tmp_path / "result.json") == 50


def test_missing_bundle_and_partial_output_are_rejected(campaign: Path, tmp_path: Path) -> None:
    shutil.rmtree(campaign / "stale-evidence")
    assert _accept(campaign, tmp_path / "missing.json") == 50
    partial = tmp_path / "partial"
    shutil.copytree(campaign, partial)
    (partial / "stale-evidence").mkdir()
    assert _accept(partial, tmp_path / "partial-result.json") == 50


def test_tampered_bundle_and_campaign_index_are_rejected(campaign: Path, tmp_path: Path) -> None:
    bundle = campaign / "safe-offline-completion" / "evidence-summary.json"
    bundle.write_text("tampered\n", encoding="utf-8")
    assert _accept(campaign, tmp_path / "bundle.json") == 50
    index = _index(campaign)
    index["campaign_result"] = "FAILED_CLOSED"
    _write_index(campaign, index)
    assert _accept(campaign, tmp_path / "index.json") == 50


def test_cross_campaign_substitution_and_duplicate_identities_are_rejected(campaign: Path, campaign_source: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    run_campaign(campaign_id="campaign-offline-20260804", output=other)
    shutil.rmtree(campaign / "safe-offline-completion")
    shutil.copytree(other / "safe-offline-completion", campaign / "safe-offline-completion")
    assert _accept(campaign, tmp_path / "cross.json") == 50
    index = _index(campaign)
    index["scenarios"][1]["commissioning_run_id"] = index["scenarios"][0]["commissioning_run_id"]
    _write_index(campaign, index)
    assert _accept(campaign, tmp_path / "identity.json") == 50


@pytest.mark.parametrize("field", ["occurrence_id", "expected_exit_classification", "observed_exit_classification"])
def test_identity_outcome_and_adverse_result_mutations_are_rejected(campaign: Path, tmp_path: Path, field: str) -> None:
    index = _index(campaign)
    if field == "occurrence_id":
        index["scenarios"][1][field] = index["scenarios"][0][field]
    elif field == "expected_exit_classification":
        index["scenarios"][1][field] = "COMPLETED"
    else:
        index["scenarios"][1][field] = "COMPLETED"
    _write_index(campaign, index)
    assert _accept(campaign, tmp_path / (field + ".json")) == 50


@pytest.mark.parametrize("field", ["account", "symbol", "quantity", "price", "balance", "order_id", "credential", "username", "exception_text", "private_path", "test_count"])
def test_private_readiness_and_arbitrary_fields_are_rejected(campaign: Path, tmp_path: Path, field: str) -> None:
    index = _index(campaign)
    index[field] = "private" if field != "test_count" else 5255
    _write_index(campaign, index)
    assert _accept(campaign, tmp_path / (field + ".json")) == 50


@pytest.mark.parametrize("field", ["permits_automated_action", "live_ready", "unattended_ready"])
def test_true_campaign_safety_flags_are_rejected(campaign: Path, tmp_path: Path, field: str) -> None:
    index = _index(campaign)
    index[field] = True
    _write_index(campaign, index)
    assert _accept(campaign, tmp_path / (field + ".json")) == 50


def test_strategy_and_approval_changes_are_rejected(campaign: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "load_config", lambda: {"strategy": {"enabled": True}})
    assert _accept(campaign, tmp_path / "strategy.json") == 50
    monkeypatch.setattr(acceptance.execution_gates, "ACTIVATION_APPROVALS", frozenset({"unexpected"}))
    assert _accept(campaign, tmp_path / "approval.json") == 50


def test_absolute_paths_and_all_safety_fields_are_rejected(campaign: Path, tmp_path: Path) -> None:
    index = _index(campaign)
    index["relative_bundle_directory"] = "C:\\private"
    _write_index(campaign, index)
    assert _accept(campaign, tmp_path / "path.json") == 50
    index = _index(campaign)
    index["protection_grounded"] = True
    _write_index(campaign, index)
    assert _accept(campaign, tmp_path / "protection.json") == 50


def test_output_is_deterministic_and_cannot_be_overwritten(campaign: Path, tmp_path: Path) -> None:
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    assert _accept(campaign, first) == 0
    assert _accept(campaign, second) == 0
    assert first.read_bytes() == second.read_bytes()
    assert _accept(campaign, first) == 50


def test_checklist_is_non_authorizing_and_runtime_is_unchanged(campaign: Path, tmp_path: Path) -> None:
    from src import execution_gates
    config_path = Path("config/strategy_v0.yaml")
    before = config_path.read_bytes()
    approvals = execution_gates.ACTIVATION_APPROVALS
    assert _accept(campaign, tmp_path / "result.json") == 0
    assert config_path.read_bytes() == before
    assert execution_gates.ACTIVATION_APPROVALS == approvals
    checklist = Path("OFFLINE_CAMPAIGN_OPERATOR_CHECKLIST.md").read_text(encoding="utf-8")
    assert "does not" in checklist and "authorize" in checklist


def test_no_network_broker_manifest_checkpoint_or_unbounded_execution() -> None:
    path = Path("scripts/verify_offline_campaign_acceptance.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"sleep", "now", "utcnow", "monotonic"} for node in ast.walk(tree))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(any(token in name for token in ("socket", "urllib", "requests", "broker", "robinhood", "manifest")) for name in imports)
