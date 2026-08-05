from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from scripts.run_offline_commissioning_campaign import (
    CAMPAIGN_INDEX,
    SCENARIOS,
    main,
    run_campaign,
    verify_campaign,
)


CAMPAIGN_ID = "campaign-offline-20260803"


@pytest.fixture()
def campaign(tmp_path: Path) -> Path:
    output = tmp_path / "campaign"
    assert run_campaign(campaign_id=CAMPAIGN_ID, output=output) == 0
    return output


def _index(path: Path) -> dict:
    return json.loads((path / CAMPAIGN_INDEX).read_text(encoding="utf-8"))


def _write_index(path: Path, value: dict) -> None:
    (path / CAMPAIGN_INDEX).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def test_all_eight_run_once_in_fixed_order_and_match_results(campaign: Path) -> None:
    index = _index(campaign)
    assert index["scenario_order"] == list(SCENARIOS)
    entries = index["scenarios"]
    assert [item["scenario_id"] for item in entries] == list(SCENARIOS)
    assert len({item["commissioning_run_id"] for item in entries}) == 8
    assert len({item["occurrence_id"] for item in entries}) == 8
    assert all(item["expected_exit_classification"] == item["observed_exit_classification"] for item in entries)
    assert all(item["verification_status"] == "VERIFIED" for item in entries)


def test_deterministic_output_and_exact_allowlist(campaign: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    run_campaign(campaign_id=CAMPAIGN_ID, output=other)
    assert sorted(p.relative_to(campaign).as_posix() for p in campaign.rglob("*")) == sorted(p.relative_to(other).as_posix() for p in other.rglob("*"))
    assert (campaign / CAMPAIGN_INDEX).read_bytes() == (other / CAMPAIGN_INDEX).read_bytes()
    assert set(_index(campaign)) == {"campaign_id", "schema_version", "scenario_order", "scenarios", "campaign_result", "permits_automated_action", "live_ready", "unattended_ready"}
    assert all(set(item) == {"scenario_id", "relative_bundle_directory", "commissioning_run_id", "occurrence_id", "expected_exit_classification", "observed_exit_classification", "bundle_index_hash", "verification_status"} for item in _index(campaign)["scenarios"])


def test_relative_paths_and_bundle_hashes_verify(campaign: Path) -> None:
    raw = (campaign / CAMPAIGN_INDEX).read_text(encoding="utf-8")
    assert str(campaign) not in raw and "\\" not in raw
    assert verify_campaign(campaign)["campaign_result"] == "VERIFIED_OFFLINE_CAMPAIGN"
    for item in _index(campaign)["scenarios"]:
        bundle = campaign / item["relative_bundle_directory"]
        assert item["bundle_index_hash"] == __import__("hashlib").sha256((bundle / "commissioning-index.json").read_bytes()).hexdigest()


@pytest.mark.parametrize("mutation", ["duplicate", "missing"])
def test_duplicate_or_missing_scenario_rejected(campaign: Path, mutation: str) -> None:
    index = _index(campaign)
    if mutation == "duplicate":
        index["scenarios"][1] = dict(index["scenarios"][0])
    else:
        index["scenarios"] = index["scenarios"][:-1]
    _write_index(campaign, index)
    with pytest.raises(ValueError):
        verify_campaign(campaign)


@pytest.mark.parametrize("field", ["commissioning_run_id", "occurrence_id"])
def test_duplicate_identity_rejected(campaign: Path, field: str) -> None:
    index = _index(campaign)
    index["scenarios"][1][field] = index["scenarios"][0][field]
    _write_index(campaign, index)
    with pytest.raises(ValueError):
        verify_campaign(campaign)


def test_cross_campaign_bundle_substitution_rejected(campaign: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    run_campaign(campaign_id="campaign-offline-20260804", output=other)
    shutil.rmtree(campaign / "safe-offline-completion")
    shutil.copytree(other / "safe-offline-completion", campaign / "safe-offline-completion")
    with pytest.raises(ValueError):
        verify_campaign(campaign)


def test_tampered_bundle_index_hash_and_swapped_directories_rejected(campaign: Path) -> None:
    index = _index(campaign)
    index["scenarios"][0]["bundle_index_hash"] = "0" * 64
    _write_index(campaign, index)
    with pytest.raises(ValueError):
        verify_campaign(campaign)
    index = _index(campaign)
    index["scenarios"][0]["relative_bundle_directory"], index["scenarios"][1]["relative_bundle_directory"] = index["scenarios"][1]["relative_bundle_directory"], index["scenarios"][0]["relative_bundle_directory"]
    _write_index(campaign, index)
    with pytest.raises(ValueError):
        verify_campaign(campaign)


def test_tampered_bundle_and_favorable_adverse_result_rejected(campaign: Path) -> None:
    bundle = campaign / "safe-offline-completion" / "evidence-summary.json"
    bundle.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_campaign(campaign)
    index = _index(campaign)
    index["scenarios"][1]["observed_exit_classification"] = "COMPLETED"
    _write_index(campaign, index)
    with pytest.raises(ValueError):
        verify_campaign(campaign)


def test_completed_and_partial_campaigns_fail_closed(campaign: Path, tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        run_campaign(campaign_id=CAMPAIGN_ID, output=campaign)
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "safe-offline-completion").mkdir()
    assert main(("--campaign-id", CAMPAIGN_ID, "--output", str(partial))) == 50
    with pytest.raises((FileNotFoundError, ValueError)):
        verify_campaign(partial)


def test_no_network_broker_sleep_polling_or_unbounded_loop() -> None:
    path = Path("scripts/run_offline_commissioning_campaign.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"sleep", "now", "utcnow", "monotonic"} for node in ast.walk(tree))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(any(token in name for token in ("socket", "urllib", "requests", "broker", "robinhood")) for name in imports if name)


def test_safety_invariants_remain_false_and_approvals_empty(campaign: Path) -> None:
    from src.execution_gates import ACTIVATION_APPROVALS
    assert not ACTIVATION_APPROVALS
    assert all(_index(campaign)[key] is False for key in ("permits_automated_action", "live_ready", "unattended_ready"))
    for report in campaign.rglob("*.json"):
        data = json.loads(report.read_text(encoding="utf-8"))
        for key in ("permits_automated_action", "live_ready", "unattended_ready", "protection_grounded", "activation_approved"):
            if key in data:
                assert data[key] is False

