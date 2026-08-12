from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import scripts.deployment_readiness_audit as audit


def test_audit_has_exactly_fifty_categories_and_allowed_classifications() -> None:
    report = audit.audit_deployment_readiness()
    categories = report["categories"]
    assert len(categories) == 50
    assert len({item["category"] for item in categories}) == 50
    assert {item["status"] for item in categories} <= audit.ALLOWED_STATUSES
    assert {item["priority"] for item in categories} <= audit.ALLOWED_PRIORITIES
    assert {item["type"] for item in categories} <= audit.ALLOWED_TYPES


def test_every_category_has_evidence_missing_evidence_and_next_action() -> None:
    for item in audit.audit_deployment_readiness()["categories"]:
        assert item["exact_file_or_api_references"]
        assert item["repository_evidence"] or item["missing_evidence"]
        assert item["missing_evidence"]
        assert item["recommended_next_action"]
        assert item["operational_evidence"]


def test_completion_is_not_claimed_from_tests_alone() -> None:
    for item in audit.audit_deployment_readiness()["categories"]:
        if item["status"] == "VERIFIED_COMPLETE":
            assert item["repository_evidence"]
            assert any("test" not in reference.casefold() for reference in item["exact_file_or_api_references"])


def test_broker_and_human_action_classification_is_explicit() -> None:
    categories = audit.audit_deployment_readiness()["categories"]
    broker_gaps = [item for item in categories if item["broker_access_required"]]
    human_gaps = [item for item in categories if item["human_action_required"]]
    assert broker_gaps
    assert {item["category"] for item in broker_gaps} >= {"Broker read-only interface boundary", "Read-only credential scope", "Network allowlisting"}
    assert human_gaps
    assert all("next_action" not in item for item in human_gaps)


def test_live_and_unattended_are_blocked_and_not_authorized() -> None:
    report = audit.audit_deployment_readiness()
    assert report["readiness_percentages"]["supervised_live_trading"] == "NOT_EVALUATED_FOR_AUTHORIZATION"
    assert report["readiness_percentages"]["unattended_operation"] == "NOT_EVALUATED_FOR_AUTHORIZATION"
    assert {item["status"] for item in report["categories"] if "structural exclusion" in item["category"]} == {"VERIFIED_COMPLETE"}
    assert "no deployment authorization" in report["authorization_statement"].casefold()


def test_safety_invariants_remain_closed() -> None:
    safety = audit.audit_deployment_readiness()["safety_invariants"]
    assert safety == {
        "strategy_enabled": False, "approved_protection_state_count": 0,
        "activation_approvals_empty": True, "protection_grounded": False,
        "activation_approved": False, "permits_automated_action": False,
        "live_ready": False, "unattended_ready": False,
    }


def test_percentages_are_deterministic_and_denominators_are_explicit() -> None:
    first = audit.audit_deployment_readiness()
    second = audit.audit_deployment_readiness()
    assert first == second
    percentages = first["readiness_percentages"]
    for key in ("offline_engineering", "supervised_read_only_deployment"):
        value = percentages[key]
        assert value["applicable_categories"] > 0
        assert value["percent"] == round(100 * value["verified_complete"] / value["applicable_categories"], 2)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "status", "priority", "type"])
def test_invalid_category_catalog_is_rejected(mutation: str) -> None:
    rows = [dict(item) for item in audit._ROWS]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[1]["category"] = rows[0]["category"]
    elif mutation == "unknown":
        rows[0]["category"] = "UNKNOWN"
    elif mutation == "status":
        rows[0]["status"] = "UNKNOWN"
    elif mutation == "priority":
        rows[0]["priority"] = "UNKNOWN"
    else:
        rows[0]["type"] = "UNKNOWN"
    with pytest.raises(ValueError):
        audit._validate_rows(rows)


def test_no_private_values_or_absolute_paths_are_serialized() -> None:
    rendered = json.dumps(audit.audit_deployment_readiness(), sort_keys=True)
    assert "C:\\" not in rendered and "C:/" not in rendered and "/Users/" not in rendered
    for value in ("AAPL", "1234.56", "SECRET-KEY-7", "password-value", "raw-exception"):
        assert value.casefold() not in rendered.casefold()


def test_report_does_not_serialize_operational_counts_as_readiness() -> None:
    rendered = json.dumps(audit.audit_deployment_readiness(), sort_keys=True).casefold()
    assert "github" not in rendered
    assert "commit_count" not in rendered
    assert "test_count" not in rendered
    assert "deployment authorization" in rendered


def test_no_network_broker_or_runtime_write_imports_or_unbounded_execution() -> None:
    path = Path("scripts/deployment_readiness_audit.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(any(token in name for token in ("socket", "urllib", "requests", "robinhood", "broker")) for name in imports)
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"sleep", "now", "utcnow", "monotonic"} for node in ast.walk(tree))


def test_no_runtime_module_is_modified_and_checklist_cannot_change_state() -> None:
    assert not any(path.name.endswith(".py") and path.parts[0] == "src" for path in Path(".").glob("*.changed"))
    config = Path("config/strategy_v0.yaml")
    before = config.read_bytes()
    approvals_before = __import__("src.execution_gates", fromlist=["ACTIVATION_APPROVALS"]).ACTIVATION_APPROVALS
    checklist = Path("SUPERVISED_READ_ONLY_DEPLOYMENT_CHECKLIST.md").read_text(encoding="utf-8")
    assert config.read_bytes() == before
    assert __import__("src.execution_gates", fromlist=["ACTIVATION_APPROVALS"]).ACTIVATION_APPROVALS == approvals_before
    assert "cannot change" in checklist and "authorize" in checklist


def test_audit_uses_existing_contracts_and_expected_digest() -> None:
    report = audit.audit_deployment_readiness()
    # M14 Issue 7 added 8 newly-reachable files to the pinned closure
    # (src/robinhood_commissioning_session.py, already pinned, now imports
    # src/commissioning_suspension_identity.py, which reaches src/
    # execution_safety_governance_deferred_gates.py and its three deferred
    # gate factories' own dependencies) -- reachability, not activation.
    # M14 Issue 2 then added 1 more: src/commissioning_risk_state.py,
    # reached because execution_safety_governance_deferred_gates.py
    # (already pinned) deferred-imports it. Never copy a historical value
    # forward -- this asserts the current, live-recomputed values.
    assert report["implementation_digest"] == "c45f7610cde1e491016dccc49029c9df5c3eeb87e1ab504d625bb5f92edad9f6"
    assert report["pinned_file_count"] == 51
    assert report["architecture_audit_reference"] == "scripts/architecture_audit.py:audit_repository"
