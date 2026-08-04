from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

import scripts.run_offline_shadow_rehearsal as module
from src.execution_gates import ACTIVATION_APPROVALS
from src.read_only_broker_state import approved_active_protection_states


MODULE_PATH = Path(module.__file__)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    root = tmp_path_factory.mktemp("rehearsal") / "run"
    return module.run_rehearsal(root)


def test_all_ten_scenarios_pass_with_real_durable_files(report) -> None:
    assert [item.scenario for item in report.scenarios] == list(module._SCENARIOS)
    assert all(item.passed for item in report.scenarios)
    assert all(item.durable_files for item in report.scenarios)
    assert any("runner.sqlite" in item.durable_files for item in report.scenarios)
    assert any("liveness.sqlite" in item.durable_files for item in report.scenarios)
    assert any("outbox.sqlite" in item.durable_files for item in report.scenarios)
    assert any("ack.sqlite" in item.durable_files for item in report.scenarios)


def test_scenario_matrix_has_expected_actual_results(report) -> None:
    assert {item.scenario: item.actual_state for item in report.scenarios} == {
        "safe_bounded_completion": "COMPLETED_BOUND",
        "operator_review_required": "STOPPED_ON_REVIEW",
        "active_suspension": "STOPPED_ON_SUSPENSION",
        "ambiguous_restart": "SUSPENSION_REQUIRED",
        "missed_heartbeat": "SUSPENSION_REQUIRED",
        "failed_alert_confirmation": "DELIVERY_UNCONFIRMED",
        "acknowledgment_preserves_suspension": "ACKNOWLEDGED_SUSPENSION_ACTIVE",
        "readiness_live_blocked": "SUPERVISED_BLOCKED",
        "stale_evidence": "STALE",
        "restart_and_recovery": "RECOVERED",
    }


def test_serialization_is_deterministic_allowlisted_and_inert(report) -> None:
    payload = json.loads(report.serialize())
    assert report.serialize() == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert set(payload) == {
        "scenarios", "all_passed", "permits_automated_action",
        "protection_grounded", "activation_approved",
    }
    assert set(payload["scenarios"][0]) == {
        "scenario", "expected_state", "actual_state", "pass", "durable_files",
        "privacy_validation", "safety_invariants",
    }
    assert payload["all_passed"] is True
    assert payload["permits_automated_action"] is False
    assert payload["protection_grounded"] is False
    assert payload["activation_approved"] is False
    encoded = report.serialize().casefold()
    assert not any(value in encoded for value in (
        "account", "symbol", "credential", "token", "password", "robinhood",
    ))


def test_two_runs_are_deterministic(tmp_path: Path) -> None:
    first = module.run_rehearsal(tmp_path / "first").to_dict()
    second = module.run_rehearsal(tmp_path / "second").to_dict()
    assert first == second


def test_existing_or_repository_roots_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="new directory"):
        module.run_rehearsal(tmp_path)
    with pytest.raises(ValueError, match="outside the repository"):
        module.run_rehearsal(module.REPOSITORY_ROOT / "unsafe-rehearsal")


def test_cli_main_is_finite_and_fail_closed(tmp_path: Path, capsys) -> None:
    target = tmp_path / "cli-run"
    assert module.main(["--root", str(target)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_passed"] is True
    assert module.main(["--root", str(target)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"all_passed": False, "failed_closed": True}


def test_source_uses_public_offline_contracts_without_private_persistence() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports = {
        name for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for name in (
            ([node.module] if isinstance(node, ast.ImportFrom) else [])
            + [alias.name for alias in node.names]
        ) if name is not None
    }
    assert not any(
        name != "src.read_only_broker_state"
        and any(term in name.casefold() for term in (
            "robinhood", "broker", "mcp", "requests", "httpx", "urllib", "socket",
        )) for name in imports
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr.startswith("_")
        and node.attr not in {"__file__", "__name__"}
        for node in ast.walk(tree)
    )
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "_connection" not in source
    assert "sqlite3" not in source


def test_no_clock_sleep_retry_or_unbounded_loop() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.While) for node in ast.walk(tree))
    forbidden = {"sleep", "utcnow", "now", "time", "monotonic", "perf_counter"}
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden
        for node in ast.walk(tree)
    )


def test_global_safety_invariants_remain_false(report) -> None:
    assert not ACTIVATION_APPROVALS
    assert not approved_active_protection_states()
    assert report.permits_automated_action is False
    assert report.protection_grounded is False
    assert report.activation_approved is False


def test_standalone_run_fails_its_safety_claim_if_strategy_is_enabled(tmp_path: Path, monkeypatch) -> None:
    original = module.load_config()
    unsafe = dict(original)
    unsafe["strategy"] = {**original["strategy"], "enabled": True}
    monkeypatch.setattr(module, "load_config", lambda: unsafe)
    selected = module.run_rehearsal(tmp_path / "unsafe-global")
    assert selected.to_dict()["all_passed"] is False
    assert all(item.safety_invariants is False for item in selected.scenarios)


def test_scenario_requires_explicit_permission_evidence(tmp_path: Path) -> None:
    selected = module._scenario("stale_evidence", "STALE", "STALE", tmp_path, True, object())
    assert selected.safety_invariants is False
    assert selected.passed is False


def test_restart_rejects_mutated_liveness_record(tmp_path: Path, monkeypatch) -> None:
    original = module.DurableRunnerLiveness.load_record
    seen = 0

    def changed(instance, transition_id):
        nonlocal seen
        record = original(instance, transition_id)
        if transition_id == "liveness-transition-ambiguous" and record is not None:
            seen += 1
            if seen > 1:
                return replace(record, transition_sequence=record.transition_sequence + 1)
        return record

    monkeypatch.setattr(module.DurableRunnerLiveness, "load_record", changed)
    selected = module.run_rehearsal(tmp_path / "mutated-liveness")
    restart = selected.scenarios[-1]
    assert restart.actual_state == "INVALID"
    assert restart.passed is False
