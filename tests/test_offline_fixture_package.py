from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.offline_fixture_package as module
from src.execution_gates import ACTIVATION_APPROVALS
from src.read_only_broker_state import approved_active_protection_states


NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
MODULE_PATH = Path(module.__file__)


def test_catalog_is_fixed_and_every_scenario_binds_unique_ids() -> None:
    scenarios = module.available_scenarios()
    assert len(scenarios) == 10
    packages = tuple(module.build_fixture(item) for item in scenarios)
    assert len({item.fixture_id for item in packages}) == len(packages)
    assert len({item.runner_id for item in packages}) == len(packages)
    assert all(item.permits_automated_action is False for item in packages)


def test_serialization_is_deterministic_and_exact() -> None:
    selected = module.build_fixture(module.FixtureScenario.READINESS_BLOCKED)
    encoded = selected.serialize_manifest()
    assert encoded == selected.serialize_manifest()
    assert module.load_fixture_manifest(encoded) == selected
    assert set(json.loads(encoded)) == {
        "schema_version", "fixture_id", "scenario", "runner_id",
        "correlation_id", "session_date", "tick_timestamps",
        "permits_automated_action", "manifest_digest",
    }


@pytest.mark.parametrize("extra", ["account", "symbol", "quantity", "exception_text", "endpoint"])
def test_prohibited_or_unknown_fields_are_rejected(extra: str) -> None:
    raw = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION).to_dict()
    raw[extra] = "private-value"
    with pytest.raises(module.OfflineFixtureError):
        module.load_fixture_manifest(json.dumps(raw))


def test_wrong_scenario_identity_and_permission_are_rejected() -> None:
    raw = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION).to_dict()
    raw["scenario"] = module.FixtureScenario.STALE_EVIDENCE.value
    with pytest.raises(module.OfflineFixtureError):
        module.load_fixture_manifest(json.dumps(raw))


def test_timestamp_tampering_after_manifest_digest_is_rejected() -> None:
    raw = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION).to_dict()
    raw["tick_timestamps"] = [(NOW + timedelta(minutes=1)).isoformat()]
    with pytest.raises(module.OfflineFixtureError, match="digest"):
        module.load_fixture_manifest(json.dumps(raw))
    raw = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION).to_dict()
    raw["permits_automated_action"] = True
    with pytest.raises(module.OfflineFixtureError):
        module.load_fixture_manifest(json.dumps(raw))


def test_duplicate_or_out_of_order_timestamps_are_rejected() -> None:
    with pytest.raises(module.OfflineFixtureError):
        module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION, tick_timestamps=(NOW, NOW))
    with pytest.raises(module.OfflineFixtureError):
        module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION, tick_timestamps=(NOW + timedelta(minutes=1), NOW))


def test_naive_wrong_date_future_and_stale_timestamps_are_rejected() -> None:
    with pytest.raises(module.OfflineFixtureError):
        module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION, tick_timestamps=(datetime(2026, 8, 3, 14),))
    with pytest.raises(module.OfflineFixtureError):
        module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION, tick_timestamps=(NOW + timedelta(days=1),))
    selected = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION)
    with pytest.raises(module.OfflineFixtureError):
        selected.validate(evaluation_time=NOW + timedelta(minutes=2), maximum_age=timedelta(minutes=1))
    with pytest.raises(module.OfflineFixtureError):
        selected.validate(evaluation_time=NOW - timedelta(seconds=1), maximum_age=timedelta(minutes=1))


def test_malformed_ids_and_json_are_rejected() -> None:
    with pytest.raises(module.OfflineFixtureError):
        module.build_fixture("SAFE_OFFLINE_COMPLETION")  # type: ignore[arg-type]
    with pytest.raises(module.OfflineFixtureError):
        module.load_fixture_manifest("not-json")
    raw = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION).to_dict()
    raw["fixture_id"] = "fixture-other"
    with pytest.raises(module.OfflineFixtureError):
        module.load_fixture_manifest(json.dumps(raw))


def test_direct_constructor_and_duplicate_or_coerced_schema_are_rejected() -> None:
    selected = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION)
    with pytest.raises(module.OfflineFixtureError):
        module.OfflineFixturePackage(
            selected.fixture_id, "SAFE_OFFLINE_COMPLETION", selected.runner_id,
            selected.correlation_id, selected.session_date, selected.tick_timestamps,
        )  # type: ignore[arg-type]
    encoded = selected.serialize_manifest()
    duplicate = encoded[:-1] + ',"schema_version":1}'
    with pytest.raises(module.OfflineFixtureError, match="duplicate"):
        module.load_fixture_manifest(duplicate)
    for value in (True, 1.0):
        raw = selected.to_dict()
        raw["schema_version"] = value
        with pytest.raises(module.OfflineFixtureError):
            module.load_fixture_manifest(json.dumps(raw))


def test_no_paths_are_serialized_or_accepted() -> None:
    raw = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION).to_dict()
    raw["database_path"] = str(Path.cwd() / "data" / "fixture.sqlite")
    with pytest.raises(module.OfflineFixtureError):
        module.load_fixture_manifest(json.dumps(raw))
    assert "data" not in module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION).serialize_manifest()


def test_source_has_no_clock_network_broker_or_private_persistence_access() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    source = MODULE_PATH.read_text(encoding="utf-8")
    imports = {
        name for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for name in (([node.module] if isinstance(node, ast.ImportFrom) else []) + [alias.name for alias in node.names])
        if name is not None
    }
    assert not any(term in name.casefold() for name in imports for term in ("broker", "mcp", "requests", "httpx", "urllib", "socket"))
    assert not any(isinstance(node, ast.While) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"now", "utcnow", "sleep", "monotonic"} for node in ast.walk(tree))
    assert "sqlite3" not in source


def test_global_safety_invariants_are_inert() -> None:
    assert not ACTIVATION_APPROVALS
    assert not approved_active_protection_states()
    selected = module.build_fixture(module.FixtureScenario.SAFE_OFFLINE_COMPLETION)
    assert selected.permits_automated_action is False
