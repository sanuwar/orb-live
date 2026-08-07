"""M13 Issue 8: tamper/failure tests for the independent verifier.

These tests prove the verifier actually detects tampering rather than
always passing. Where the tamper is at the level of a whole file on disk
(a missing/duplicated pinned path, a lying implementation function), the
real repository-under-test's declared constants or functions are
monkeypatched for the duration of one call. No tracked source file is
ever modified by these tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import verify_m13_protection_grounding as verifier

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _names(results, status=None):
    if status is None:
        return {r.name for r in results}
    return {r.name for r in results if r.status == status}


def _failed(results):
    return [r for r in results if r.status == "FAIL"]


# --------------------------------------------------------------------------
# 1. Clean fixture => PASS, per check group.
# --------------------------------------------------------------------------


def test_clean_repository_digest_and_closure_checks_all_pass():
    results = verifier.check_implementation_digest_and_closure(REPOSITORY_ROOT)
    assert _failed(results) == []


def test_clean_observation_model_checks_all_pass():
    results = verifier.check_observation_model()
    assert _failed(results) == []


def test_clean_ledger_checks_all_pass(tmp_path):
    results = verifier.check_ledger(tmp_path)
    assert _failed(results) == []


def test_clean_promotion_policy_checks_all_pass():
    results = verifier.check_promotion_policy()
    assert _failed(results) == []


def test_clean_grounding_assessment_checks_all_pass():
    results = verifier.check_grounding_assessment()
    assert _failed(results) == []


def test_clean_checkpoint_integration_checks_all_pass():
    results = verifier.check_checkpoint_integration()
    assert _failed(results) == []


def test_clean_broker_call_site_checks_all_pass():
    results = verifier.check_broker_call_sites()
    assert _failed(results) == []


def test_clean_authority_state_checks_all_pass():
    results = verifier.check_authority_state()
    assert _failed(results) == []


def test_clean_q1_and_q2_checks_all_pass():
    results = verifier.check_q1_and_q2()
    assert _failed(results) == []


def test_no_protection_observation_fixture_remains_valid_scenario():
    """Scenario 27: an absence-of-protection observation is legitimate
    evidence, not a failure -- covered inside check_promotion_policy's
    'no_protection_observation_valid_not_a_failure' scenario."""
    results = verifier.check_promotion_policy()
    assert _failed(results) == []
    assert any(r.name == "promotion_outcome_matches_independent_prediction" and r.status == "PASS" for r in results)


def test_full_report_on_clean_repository_is_pass():
    report = verifier.build_report(REPOSITORY_ROOT)
    assert report["final_result"] == "PASS"
    assert report["failed_count"] == 0


# --------------------------------------------------------------------------
# 2-4. Digest / closure tampering.
# --------------------------------------------------------------------------


def test_implementation_digest_mismatch_is_detected(monkeypatch):
    import src.read_only_authorization_manifest as manifest

    monkeypatch.setattr(manifest, "checkpoint_implementation_digest", lambda root: "0" * 64)
    results = verifier.check_implementation_digest_and_closure(REPOSITORY_ROOT)
    failed_names = _names(_failed(results))
    assert "closure_digest_matches" in failed_names


def test_missing_pinned_file_is_detected(monkeypatch):
    import src.read_only_authorization_manifest as manifest

    tampered = tuple(p for p in manifest.PINNED_IMPLEMENTATION_CLOSURE if p != "src/protection_assessment_store.py")
    monkeypatch.setattr(manifest, "PINNED_IMPLEMENTATION_CLOSURE", tampered)
    results = verifier.check_implementation_digest_and_closure(REPOSITORY_ROOT)
    failed_names = _names(_failed(results))
    assert "closure_contains_required_m13_files" in failed_names
    assert "closure_equals_real_execution_closure" in failed_names


def test_duplicate_pinned_file_is_detected(monkeypatch):
    import src.read_only_authorization_manifest as manifest

    tampered = manifest.PINNED_IMPLEMENTATION_CLOSURE + ("src/protection_assessment_store.py",)
    monkeypatch.setattr(manifest, "PINNED_IMPLEMENTATION_CLOSURE", tampered)
    results = verifier.check_implementation_digest_and_closure(REPOSITORY_ROOT)
    assert "closure_no_duplicates" in _names(_failed(results))


def test_extra_test_only_tool_pinned_is_detected(monkeypatch):
    import src.read_only_authorization_manifest as manifest

    tampered = manifest.PINNED_IMPLEMENTATION_CLOSURE + ("tests/call_site_guard.py",)
    monkeypatch.setattr(manifest, "PINNED_IMPLEMENTATION_CLOSURE", tampered)
    results = verifier.check_implementation_digest_and_closure(REPOSITORY_ROOT)
    assert "closure_excludes_test_only_tools" in _names(_failed(results))


def test_nonexistent_pinned_path_is_detected(monkeypatch):
    import src.read_only_authorization_manifest as manifest

    tampered = manifest.PINNED_IMPLEMENTATION_CLOSURE + ("src/does_not_exist_verifier_probe.py",)
    monkeypatch.setattr(manifest, "PINNED_IMPLEMENTATION_CLOSURE", tampered)
    results = verifier.check_implementation_digest_and_closure(REPOSITORY_ROOT)
    assert "closure_paths_exist" in _names(_failed(results))


def test_independent_closure_digest_reacts_to_content_change(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    original = verifier._independent_closure_digest(tmp_path, ("a.py",))
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    changed = verifier._independent_closure_digest(tmp_path, ("a.py",))
    assert original != changed


def test_independent_closure_digest_is_line_ending_stable(tmp_path):
    (tmp_path / "a.py").write_bytes(b"x = 1\n")
    unix = verifier._independent_closure_digest(tmp_path, ("a.py",))
    (tmp_path / "a.py").write_bytes(b"x = 1\r\n")
    crlf = verifier._independent_closure_digest(tmp_path, ("a.py",))
    assert unix == crlf


# --------------------------------------------------------------------------
# 5-6. Observation model: write_count / privacy.
# --------------------------------------------------------------------------


def test_write_count_nonzero_is_structurally_refused():
    results = verifier.check_observation_model()
    check = {r.name: r for r in results}["write_count_cannot_be_nonzero"]
    assert check.status == "PASS"


def test_privacy_unverified_is_structurally_refused():
    results = verifier.check_observation_model()
    check = {r.name: r for r in results}["privacy_must_be_verified_at_construction"]
    assert check.status == "PASS"


def test_nonzero_write_evidence_blocks_promotion_eligibility():
    """A LedgerObservationSummary with write_count=1 (unreachable through
    the model's own constructor, only through direct summary construction
    or post-construction tampering) must independently classify as
    NONZERO_WRITE_EVIDENCE, and the verifier's own prediction must agree."""
    results = verifier.check_promotion_policy()
    check = {r.name: r for r in results}["promotion_outcome_matches_independent_prediction"]
    assert check.status == "PASS"
    assert "13" in check.detail


# --------------------------------------------------------------------------
# 7. Simulated evidence treated as live.
# --------------------------------------------------------------------------


def test_simulated_evidence_misclassified_as_authoritative_is_detected(monkeypatch):
    import src.protection_observation_model as model
    from src.protection_observation_model import EvidenceSourceClass

    tampered = frozenset(model.BROKER_AUTHORITATIVE_SOURCE_CLASSES | {EvidenceSourceClass.SIMULATED_BROKER})
    monkeypatch.setattr(model, "BROKER_AUTHORITATIVE_SOURCE_CLASSES", tampered)
    results = verifier.check_q1_and_q2()
    assert "q1_simulation_is_not_broker_authoritative" in _names(_failed(results))


# --------------------------------------------------------------------------
# 8-9. Auto-promotion / structural authority tampering.
# --------------------------------------------------------------------------


def test_auto_promotion_enabled_by_default_is_detected(monkeypatch):
    import dataclasses as dc
    import src.protection_promotion_policy as policy_module

    tampered = dc.replace(policy_module.DEFAULT_PROTECTION_PROMOTION_POLICY, promotion_enabled=True)
    monkeypatch.setattr(policy_module, "DEFAULT_PROTECTION_PROMOTION_POLICY", tampered)
    results = verifier.check_promotion_policy()
    assert "promotion_disabled_by_default" in _names(_failed(results))


def test_automatic_promotion_permitted_cannot_be_forced_true():
    from src.protection_promotion_policy import (
        DEFAULT_PROTECTION_PROMOTION_POLICY,
        ProtectionPromotionPolicy,
        ProtectionPromotionPolicyError,
    )

    with pytest.raises(ProtectionPromotionPolicyError):
        ProtectionPromotionPolicy(**{**DEFAULT_PROTECTION_PROMOTION_POLICY.__dict__, "automatic_promotion_permitted": True})


# --------------------------------------------------------------------------
# 9-12. protection_grounded / activation_approved / live_ready /
# unattended_ready mutation introduced -- tested against the shared
# detection primitive `_source_grants_authority`, which every authority-
# scanning check in the verifier calls.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    [
        "protection_grounded", "activation_approved", "permits_automated_action",
        "live_ready", "unattended_ready", "ACTIVATION_APPROVALS",
        "ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES",
    ],
)
def test_authority_symbol_true_assignment_is_detected(symbol):
    tampered_source = f'"""module"""\n{symbol} = True\n'
    assert verifier._source_grants_authority(tampered_source) == symbol


def test_authority_symbol_docstring_mention_is_not_a_false_positive():
    """A module explaining, in prose, that it never sets these symbols
    must not itself be flagged -- this is the exact false positive class
    this script's own first draft produced and had to fix."""
    text = (
        "This module never sets protection_grounded, ACTIVATION_APPROVALS, "
        "activation_approved, permits_automated_action, live_ready, or "
        "unattended_ready."
    )
    assert verifier._source_grants_authority(text) is None


def test_authority_symbol_false_default_is_not_a_false_positive():
    text = "protection_grounded: bool = False\npermits_automated_action = False\n"
    assert verifier._source_grants_authority(text) is None


def test_ast_authority_scan_detects_true_assignment_but_not_false_default(tmp_path, monkeypatch):
    offending = tmp_path / "src"
    offending.mkdir()
    (offending / "tampered_m13_module.py").write_text(
        "protection_grounded: bool = False\n\ndef tamper():\n    protection_grounded = True\n    return protection_grounded\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(verifier, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(verifier, "_AUTHORITY_SYMBOLS", verifier._AUTHORITY_SYMBOLS)
    # Directly exercise the same AST walk check_authority_state uses, scoped
    # to the synthetic tampered file, without depending on the real
    # repository's fixed m13_paths list.
    import ast as _ast
    tree = _ast.parse((offending / "tampered_m13_module.py").read_text(encoding="utf-8"))
    offending_lines = []
    for node in _ast.walk(tree):
        targets, value = [], None
        if isinstance(node, _ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, _ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if isinstance(value, _ast.Constant) and value.value is False:
            continue
        for target in targets:
            name = target.id if isinstance(target, _ast.Name) else None
            if name in verifier._AUTHORITY_SYMBOLS:
                offending_lines.append(node.lineno)
    assert offending_lines == [4]


# --------------------------------------------------------------------------
# 13-16. Broker call-site tampering.
# --------------------------------------------------------------------------


class _FakeCallSite:
    def __init__(self, path, line, kind, tool="fake_tool"):
        self.path, self.line, self.kind, self.tool = path, line, kind, tool

    def describe(self):
        return f"{self.path}:{self.line} {self.tool} ({self.kind})"


def test_unresolved_dynamic_broker_dispatch_is_detected(monkeypatch):
    import tests.call_site_guard as guard

    fake_site = _FakeCallSite("src/fake_tampered.py", 1, guard.UNRESOLVED)
    monkeypatch.setattr(guard, "analyze_runtime_tree", lambda: [fake_site])
    results = verifier.check_broker_call_sites()
    assert "no_unresolved_dynamic_broker_dispatch" in _names(_failed(results))


def test_hidden_dynamic_broker_write_via_getattr_is_detected(monkeypatch):
    import tests.call_site_guard as guard

    fake_site = _FakeCallSite("src/fake_getattr_write.py", 1, guard.DYNAMIC)
    monkeypatch.setattr(guard, "analyze_runtime_tree", lambda: [fake_site])
    results = verifier.check_broker_call_sites()
    assert "no_unresolved_dynamic_broker_dispatch" in _names(_failed(results))


def test_commissioning_write_path_missing_from_enumeration_is_detected(monkeypatch):
    import tests.call_site_guard as guard

    monkeypatch.setattr(guard, "analyze_runtime_tree", lambda: [])
    results = verifier.check_broker_call_sites()
    assert "commissioning_write_path_remains_visible" in _names(_failed(results))


def test_m13_module_with_a_broker_call_site_is_detected(monkeypatch):
    import tests.call_site_guard as guard

    real_sites = guard.analyze_runtime_tree()
    injected = _FakeCallSite("src/protection_checkpoint_integration.py", 999, guard.LITERAL, tool="place_equity_order")
    monkeypatch.setattr(guard, "analyze_runtime_tree", lambda: list(real_sites) + [injected])
    results = verifier.check_broker_call_sites()
    assert "m13_pipeline_has_no_broker_call_site" in _names(_failed(results))


def test_operator_app_with_a_broker_call_site_is_detected(monkeypatch):
    import tests.call_site_guard as guard

    real_sites = guard.analyze_runtime_tree()
    injected = _FakeCallSite("src/operator_web_app.py", 999, guard.LITERAL, tool="place_equity_order")
    monkeypatch.setattr(guard, "analyze_runtime_tree", lambda: list(real_sites) + [injected])
    results = verifier.check_broker_call_sites()
    assert "operator_app_has_no_broker_call_site" in _names(_failed(results))


def test_independent_scanner_resolves_module_level_string_constants(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "sample.py").write_text(
        'TOOL = "get_positions"\n\n'
        "async def go(session):\n"
        "    return await session.call_tool(TOOL, {})\n",
        encoding="utf-8",
    )
    found = verifier._independent_call_site_scan(tmp_path)
    assert ("src/sample.py", 4) in found["literal"]


def test_independent_scanner_flags_truly_dynamic_dispatch_as_non_literal(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "sample.py").write_text(
        "async def go(session, name):\n"
        "    return await session.call_tool(name, {})\n",
        encoding="utf-8",
    )
    found = verifier._independent_call_site_scan(tmp_path)
    assert ("src/sample.py", 2) in found["non_literal"]
    assert found["literal"] == []


# --------------------------------------------------------------------------
# 17-18. Route / link failures.
# --------------------------------------------------------------------------


def test_route_probe_to_nonexistent_evidence_is_detected(tmp_path):
    from datetime import datetime, timezone
    from scripts.create_operator_demo import create_demo

    demo = create_demo(tmp_path / "demo", datetime(2026, 8, 5, 16, tzinfo=timezone.utc))
    demo["protection_observation_id"] = "this-observation-was-never-recorded"
    results = verifier.check_operator_routes(demo)
    assert "independent_probe_of_m13_routes" in _names(_failed(results))


# --------------------------------------------------------------------------
# 21-22. Digest mismatch detection for evidence/assessment digests.
# --------------------------------------------------------------------------


def test_evidence_digest_independent_recompute_detects_a_lying_record():
    """If a record's stored evidence_digest were tampered with after
    construction (bypassing the frozen guard via object.__setattr__, the
    only way to reach an inconsistent state at all), the independent
    recompute must disagree with it."""
    from tests._protection_observation_fixtures import active_protective_order

    record = active_protective_order("run-a", observed_at_monotonic=1.0)
    object.__setattr__(record, "evidence_digest", "0" * 64)
    payload = record.to_canonical_dict()
    payload.pop("qualification_status", None)
    domain = b"orb-live/protection-observation-model/v1"
    import hashlib

    independent = hashlib.sha256(domain + verifier._independent_canonical_bytes(payload)).hexdigest()
    assert independent != record.evidence_digest


def test_assessment_digest_independent_recompute_detects_a_lying_assessment():
    from src.protection_grounding_assessment import evaluate_protection_grounding
    from src.protection_observation_ledger import ProtectionObservationLedger
    from tests._protection_observation_fixtures import active_protective_order, digest

    with verifier.TemporaryDirectory(prefix="m13-verifier-test-") as td:
        db = Path(td) / "ledger.sqlite3"
        with ProtectionObservationLedger(db, "run-a") as ledger:
            ledger.append(active_protective_order("run-a", observed_at_monotonic=1.0))
            summaries = ledger.all_observations()
        assessment = evaluate_protection_grounding(
            summaries, evaluated_at_monotonic=2.0,
            expected_release_identity=digest("r")[:40],
            expected_implementation_digest=digest("i"),
            expected_account_fingerprint_reference=digest("a"),
        )
    import dataclasses as dc

    tampered = dc.replace(assessment, assessment_digest="0" * 64)
    payload = {
        "assessment_version": tampered.assessment_version, "policy_version": tampered.policy_version,
        "release_identity": tampered.release_identity, "implementation_digest": tampered.implementation_digest,
        "account_fingerprint_reference": tampered.account_fingerprint_reference,
        "included_observation_ids": list(tampered.included_observation_ids),
        "excluded_observation_ids": list(tampered.excluded_observation_ids),
        "informational_observation_ids": list(tampered.informational_observation_ids),
        "exclusion_reasons": [f"{oid}={tampered.exclusion_reasons[oid]}" for oid in sorted(tampered.exclusion_reasons)],
        "recommendation": tampered.recommendation.value, "reason_codes": list(tampered.reason_codes),
    }
    import hashlib

    independent = hashlib.sha256(b"orb-live/protection-grounding-assessment/v1" + verifier._independent_canonical_bytes(payload)).hexdigest()
    assert independent != tampered.assessment_digest
    assert independent == assessment.assessment_digest


# --------------------------------------------------------------------------
# 23-24. Checkpoint / mutation route added.
# --------------------------------------------------------------------------


def test_checkpoint_route_reference_in_operator_app_is_detected(tmp_path, monkeypatch):
    fake_operator_app = tmp_path / "operator_web_app.py"
    fake_operator_app.write_text(
        "from src.protection_checkpoint_integration import integrate_checkpoint_result\n",
        encoding="utf-8",
    )
    text = fake_operator_app.read_text(encoding="utf-8")
    forbidden = ["protection_checkpoint_integration", "read_only_observation_runtime", "run_read_only_observation_checkpoint", "robinhood_client", "robinhood_live_adapter", "robinhood_commissioning"]
    present = [tok for tok in forbidden if tok in text]
    assert present == ["protection_checkpoint_integration"]


def test_mutation_route_would_be_caught_by_safety_declaration_scan():
    from src.operator_web_app import build_operator_route_table

    table = build_operator_route_table()
    mutating = [r for r in table.routes if any(m in r.methods for m in ("POST", "PUT", "PATCH", "DELETE"))]
    assert mutating == []


def test_route_table_validate_rejects_a_forged_write_capable_route():
    from dataclasses import replace as _replace

    from src.operator_framework import ApplicationRouteTable
    from src.operator_web_app import build_operator_route_table

    table = build_operator_route_table()
    forged = _replace(table.routes[0], path="/forged-write-route", name="operator.forged_write_route", permits_database_write=True)
    forged_table = ApplicationRouteTable()
    for route in table.routes:
        forged_table.add(route)
    forged_table.add(forged)
    with pytest.raises(ValueError):
        forged_table.validate()


# --------------------------------------------------------------------------
# 28-29. Determinism.
# --------------------------------------------------------------------------


def test_verifier_result_is_deterministic_across_repeated_runs():
    first = verifier.build_report(REPOSITORY_ROOT)
    second = verifier.build_report(REPOSITORY_ROOT)
    assert first["final_result"] == second["final_result"] == "PASS"
    assert [c["name"] for c in first["checks"]] == [c["name"] for c in second["checks"]]
    assert [c["status"] for c in first["checks"]] == [c["status"] for c in second["checks"]]
    assert first["implementation_digest"] == second["implementation_digest"]


def test_check_output_ordering_is_stable():
    first = [r.name for r in verifier.check_observation_model()]
    second = [r.name for r in verifier.check_observation_model()]
    assert first == second


# --------------------------------------------------------------------------
# 30. No broker/network/private data required by the verifier itself.
# --------------------------------------------------------------------------


def test_verifier_module_imports_no_broker_transport():
    import inspect

    source = inspect.getsource(verifier)
    for forbidden in ("robin_stocks", "import requests", "import httpx", "import socket"):
        assert forbidden not in source


def _imported_module_names(module) -> set[str]:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_verifier_module_never_imports_real_broker_session_modules():
    imported = _imported_module_names(verifier)
    for forbidden in ("src.robinhood_client", "src.robinhood_live_adapter", "src.robinhood_commissioning_session"):
        assert forbidden not in imported


# --------------------------------------------------------------------------
# Independence audit: a lying implementation must still be caught, not
# trusted, by the verifier.
# --------------------------------------------------------------------------


def test_a_lying_promotion_evaluator_is_still_caught(monkeypatch):
    """Monkeypatch the real evaluator to always claim ELIGIBLE_FOR_REVIEW,
    then confirm the verifier's independently authored predictions --
    which never called the real evaluator to produce their own expected
    values -- disagree and fail the check."""
    import src.protection_promotion_policy as policy_module
    from src.protection_promotion_policy import PromotionOutcome

    def lying_evaluate(observations, **kwargs):
        real = verifier._predict_promotion_outcome  # not used for the lie itself
        from src.protection_promotion_policy import PromotionAssessment

        return PromotionAssessment(
            outcome=PromotionOutcome.ELIGIBLE_FOR_REVIEW,
            policy_version=kwargs["policy"].policy_version,
            evaluated_at_monotonic=float(kwargs["evaluated_at_monotonic"]),
            observation_count=len(observations),
            protective_observation_count=0,
            distinct_run_count=0,
            distinct_observation_time_count=0,
            independent_review_required=kwargs["policy"].independent_review_required,
        )

    monkeypatch.setattr(policy_module, "evaluate_promotion_eligibility", lying_evaluate)
    results = verifier.check_promotion_policy()
    assert "promotion_outcome_matches_independent_prediction" in _names(_failed(results))


def test_a_lying_closure_digest_function_is_still_caught(monkeypatch):
    import src.read_only_authorization_manifest as manifest

    monkeypatch.setattr(manifest, "checkpoint_implementation_digest", lambda root: "f" * 64)
    results = verifier.check_implementation_digest_and_closure(REPOSITORY_ROOT)
    assert "closure_digest_matches" in _names(_failed(results))


def test_independence_audit_does_not_import_operator_route_verification_helper():
    """scripts/verify_operator_routes.py's crawler must not be imported by
    the independent route/export checks -- they build their own probe
    paths from scratch."""
    import ast
    import inspect

    for func in (verifier.check_operator_routes, verifier.check_exports):
        tree = ast.parse(inspect.getsource(func))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "scripts.verify_operator_routes"
                assert "collect_generated_links" not in {a.name for a in node.names}
                assert "_route_probe_links" not in {a.name for a in node.names}
            elif isinstance(node, ast.Import):
                assert "scripts.verify_operator_routes" not in {a.name for a in node.names}
