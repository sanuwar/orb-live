from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import src.bounded_shadow_session as module
from src.execution_gates import ACTIVATION_APPROVALS
from src.read_only_broker_state import approved_active_protection_states


MODULE_PATH = Path(module.__file__)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return module.run_bounded_shadow_session(tmp_path_factory.mktemp("market-day") / "session")


def test_all_ten_market_day_scenarios_pass(report) -> None:
    assert report.all_passed is True
    assert len(report.observations) == 10
    assert [item.scenario for item in report.observations] == list(module.MarketDayScenario)
    assert all(item.permits_automated_action is False for item in report.observations)


def test_expected_boundary_and_rehearsal_results(report) -> None:
    values = {item.scenario: item.observed_state for item in report.observations}
    assert values[module.MarketDayScenario.PRE_MARKET_WAIT] == "PRE_MARKET"
    assert values[module.MarketDayScenario.REGULAR_SESSION_CALENDAR_BOUNDARY] == "REGULAR"
    assert values[module.MarketDayScenario.EARLY_CLOSE_BOUNDARY] == "CLOSED"
    assert values[module.MarketDayScenario.UNSUPPORTED_DATE] == "UNKNOWN"
    assert values[module.MarketDayScenario.OPERATOR_REVIEW_STOP] == "STOPPED_ON_REVIEW"
    assert values[module.MarketDayScenario.SUSPENSION_STOP] == "STOPPED_ON_SUSPENSION"
    assert values[module.MarketDayScenario.DURABLE_RESTART_RECOVERY] == "RECOVERED"


def test_deterministic_allowlisted_serialization(report) -> None:
    payload = json.loads(report.serialize())
    assert report.serialize() == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert set(payload) == {
        "session_id", "observations", "evaluated_at", "all_passed", "live_ready",
        "unattended_ready", "protection_grounded", "activation_approved",
        "permits_automated_action",
    }
    assert set(payload["observations"][0]) == {
        "scenario", "fixture_id", "fixture_digest", "expected_state", "observed_state", "durable_files",
        "pass", "permits_automated_action",
    }
    assert payload["live_ready"] is False
    assert payload["unattended_ready"] is False
    assert payload["permits_automated_action"] is False


def test_two_runs_are_deterministic(tmp_path: Path) -> None:
    first = module.run_bounded_shadow_session(tmp_path / "first").to_dict()
    second = module.run_bounded_shadow_session(tmp_path / "second").to_dict()
    assert first == second


def test_existing_or_repository_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(module.ShadowSessionError):
        module.run_bounded_shadow_session(tmp_path)
    with pytest.raises(module.ShadowSessionError):
        module.run_bounded_shadow_session(module.REPOSITORY_ROOT / "data" / "unsafe")


def test_main_has_fixed_exit_codes_and_no_exception_output(tmp_path: Path, capsys) -> None:
    root = tmp_path / "cli"
    assert module.main(("--root", str(root))) == 0
    assert json.loads(capsys.readouterr().out)["all_passed"] is True
    assert module.main(("--bad", str(tmp_path))) == 50
    assert capsys.readouterr().out == ""


def test_schedule_is_finite_and_bound_to_runner(tmp_path: Path) -> None:
    ticks = (module.SESSION_NOW - module.timedelta(minutes=2), module.SESSION_NOW - module.timedelta(minutes=1))
    report = module.run_bounded_shadow_session(tmp_path / "scheduled", tick_timestamps=ticks, maximum_tick_count=2)
    assert report.all_passed
    assert report.observations[1].observed_state == "REGULAR"
    with pytest.raises(module.ShadowSessionError):
        module.run_bounded_shadow_session(tmp_path / "too-many", tick_timestamps=ticks, maximum_tick_count=1)
    with pytest.raises(module.ShadowSessionError):
        module.run_bounded_shadow_session(tmp_path / "empty", tick_timestamps=(), maximum_tick_count=2)


def test_private_fixture_and_non_temporary_root_are_rejected(tmp_path: Path) -> None:
    fixture = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION)
    with pytest.raises(module.ShadowSessionError):
        module.SessionObservation(module.MarketDayScenario.PRE_MARKET_WAIT, fixture, "PRE_MARKET", "PRE_MARKET", ("account.sqlite",))
    with pytest.raises(module.ShadowSessionError):
        module.run_bounded_shadow_session(module.REPOSITORY_ROOT / "outside")


def test_no_sleep_polling_threads_subprocess_or_network_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"sleep", "now", "utcnow", "monotonic"} for node in ast.walk(tree))
    assert not any(term in source.casefold() for term in ("thread", "subprocess", "socket", "requests", "robinhood", "mcp"))


def test_global_safety_invariants_remain_false(report) -> None:
    assert not ACTIVATION_APPROVALS
    assert not approved_active_protection_states()
    assert report.live_ready is False
    assert report.unattended_ready is False
    assert report.protection_grounded is False
    assert report.activation_approved is False
    assert report.permits_automated_action is False
