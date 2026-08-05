from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_offline_commissioning import FILES, INDEX, main


SCENARIOS = (
    "SAFE_OFFLINE_COMPLETION", "OPERATOR_REVIEW_REQUIRED", "ACTIVE_SUSPENSION",
    "AMBIGUOUS_RESTART", "MISSED_HEARTBEAT", "ALERT_DELIVERY_UNCONFIRMED",
    "READINESS_BLOCKED", "STALE_EVIDENCE",
)
BASE = ("--run-id", "commissioning-test-20260803", "--start", "2026-08-03T13:59:00+00:00", "--tick-count", "2")


def invoke(tmp_path: Path, scenario: str = SCENARIOS[0], *extra: str) -> tuple[int, Path]:
    output = tmp_path / scenario.lower()
    return main(("--scenario", scenario, *BASE, "--output", str(output), *extra)), output


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_fixed_scenario_produces_the_same_safe_bundle(tmp_path: Path, scenario: str) -> None:
    code, output = invoke(tmp_path, scenario)
    assert code in {0, 10, 20, 30, 40}
    assert {path.name for path in output.iterdir()} == {*FILES, INDEX}
    index = json.loads((output / INDEX).read_text())
    assert set(index) == {"commissioning_run_id", "scenario_id", "injected_start_timestamp", "injected_end_timestamp", "files", "completion_status", "permits_automated_action", "live_ready", "unattended_ready"}
    assert index["completion_status"] == "COMPLETED"
    assert all(index[key] is False for key in ("permits_automated_action", "live_ready", "unattended_ready"))
    for name in FILES:
        content = (output / name).read_bytes()
        assert index["files"][name] == hashlib.sha256(content).hexdigest()
        assert "account" not in content.decode().casefold()


def test_deterministic_and_refuses_overwrite(tmp_path: Path) -> None:
    first, output = invoke(tmp_path)
    assert first == 0
    second = main(("--scenario", SCENARIOS[0], *BASE, "--output", str(output)))
    assert second == 50
    other = tmp_path / "other"
    assert main(("--scenario", SCENARIOS[0], *BASE, "--output", str(other))) == 0
    assert (output / "session-report.json").read_bytes() == (other / "session-report.json").read_bytes()


@pytest.mark.parametrize("args", [
    ("--scenario", "UNKNOWN"),
    ("--run-id", "bad"),
    ("--start", "2026-08-03T13:59:00"),
    ("--start", "not-a-time"),
    ("--tick-count", "1"),
])
def test_invalid_invocation_is_fixed_closed(tmp_path: Path, args: tuple[str, str]) -> None:
    values = list(BASE)
    for index in range(0, len(values), 2):
        if values[index] == args[0]:
            values[index + 1] = args[1]
    if args[0] == "--scenario":
        command = ("--scenario", args[1], *BASE, "--output", str(tmp_path / "invalid"))
    else:
        command = ("--scenario", SCENARIOS[0], *values, "--output", str(tmp_path / "invalid"))
    assert main(command) == 50


def test_partial_bundle_and_tampering_are_detectable(tmp_path: Path) -> None:
    output = tmp_path / "partial"
    output.mkdir()
    (output / "session-report.json").write_text("partial")
    assert main(("--scenario", SCENARIOS[0], *BASE, "--output", str(output))) == 50
    output2 = tmp_path / "tampered"
    assert main(("--scenario", SCENARIOS[0], *BASE, "--output", str(output2))) == 0
    (output2 / "evidence-summary.json").write_text("tampered")
    index = json.loads((output2 / INDEX).read_text())
    assert index["files"]["evidence-summary.json"] != hashlib.sha256((output2 / "evidence-summary.json").read_bytes()).hexdigest()


def test_path_and_runtime_guards(tmp_path: Path) -> None:
    assert main(("--scenario", SCENARIOS[0], *BASE, "--output", str(Path(__file__).parents[1] / "data" / "commissioning"))) == 50
    source = Path("scripts/run_offline_commissioning.py").read_text()
    tree = ast.parse(source)
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"now", "utcnow", "monotonic", "sleep"} for node in ast.walk(tree))
    assert "robinhood" not in source.casefold()
    assert "socket" not in source.casefold()
