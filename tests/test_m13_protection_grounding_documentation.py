"""M13 Issue 9: documentation and runbook contract tests.

Documentation-only does not mean unverified. These tests check that the
architecture document and the operator runbook say things that are true
right now, not things that sound true.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE_DOC = REPOSITORY_ROOT / "MILESTONE_13_PROTECTION_GROUNDING.md"
RUNBOOK = REPOSITORY_ROOT / "M13_LIVE_READ_ONLY_PROTECTION_OBSERVATION_RUNBOOK.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The documents exist and reference real files.
# --------------------------------------------------------------------------


def test_documents_exist():
    assert ARCHITECTURE_DOC.is_file()
    assert RUNBOOK.is_file()


def test_architecture_doc_references_only_files_that_exist():
    referenced = [
        "src/protection_observation_model.py",
        "src/protection_observation_ledger.py",
        "src/protection_promotion_policy.py",
        "src/protection_grounding_assessment.py",
        "src/protection_checkpoint_integration.py",
        "src/protection_assessment_store.py",
        "src/protection_operator_evidence.py",
        "src/operator_web_app.py",
        "read_only_observation_checkpoint_launcher.py",
        "tests/call_site_guard.py",
        "src/read_only_observation_runtime.py",
        "scripts/verify_m13_protection_grounding.py",
    ]
    text = _text(ARCHITECTURE_DOC)
    for relative in referenced:
        assert relative in text, f"{relative} not mentioned in the architecture doc"
        assert (REPOSITORY_ROOT / relative).is_file(), f"{relative} does not exist"


def test_runbook_references_the_real_launcher_and_verifier():
    text = _text(RUNBOOK)
    assert "read_only_observation_checkpoint_launcher.py" in text
    assert (REPOSITORY_ROOT / "read_only_observation_checkpoint_launcher.py").is_file()
    assert "scripts/verify_m13_protection_grounding.py" in text
    assert (REPOSITORY_ROOT / "scripts" / "verify_m13_protection_grounding.py").is_file()
    assert "MILESTONE_13_PROTECTION_GROUNDING.md" in text


def test_architecture_doc_cross_references_the_runbook():
    text = _text(ARCHITECTURE_DOC)
    assert "M13_LIVE_READ_ONLY_PROTECTION_OBSERVATION_RUNBOOK.md" in text


# --------------------------------------------------------------------------
# The documented CLI contract matches the real launcher parser.
# --------------------------------------------------------------------------


def _actual_launcher_flags() -> set[str]:
    source = (REPOSITORY_ROOT / "read_only_observation_checkpoint_launcher.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    flags: set[str] = set()
    string_constants: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            string_constants[node.targets[0].id] = node.value.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("--"):
                flags.add(first.value)
            elif isinstance(first, ast.Name) and first.id in string_constants:
                value = string_constants[first.id]
                if value.startswith("--"):
                    flags.add(value)
    return flags


def test_documented_cli_flags_match_the_real_launcher_parser():
    documented_flags = {
        "--run-id", "--account-number", "--symbol", "--expected-quantity",
        "--evidence-root", "--authorization", "--operator-present",
        "--i-understand-this-performs-real-broker-reads",
        "--authorization-manifest", "--authorization-ledger", "--interactive",
        "--protection-ledger-root", "--protection-assessment-store",
    }
    actual_flags = _actual_launcher_flags()
    assert documented_flags == actual_flags, (
        f"documented-but-not-real={documented_flags - actual_flags} "
        f"real-but-not-documented={actual_flags - documented_flags}"
    )
    runbook_text = _text(RUNBOOK)
    for flag in documented_flags:
        assert flag in runbook_text, f"{flag} documented in the flag table but missing from the runbook body"


def test_documented_authorization_flag_value_and_env_var_are_real():
    from src.read_only_observation_runtime import READ_ONLY_AUTHORIZATION_FLAG

    runbook_text = _text(RUNBOOK)
    assert READ_ONLY_AUTHORIZATION_FLAG == "AUTHORIZE_MILESTONE_9_READ_ONLY_OBSERVATION"
    assert READ_ONLY_AUTHORIZATION_FLAG in runbook_text

    launcher_source = (REPOSITORY_ROOT / "read_only_observation_checkpoint_launcher.py").read_text(encoding="utf-8")
    assert 'REAL_READ_RUNTIME_FLAG = "ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION"' in launcher_source
    assert "ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION" in runbook_text


# --------------------------------------------------------------------------
# Repository-truth claims: digest, pinned count, verifier baseline.
# --------------------------------------------------------------------------


def test_documented_broker_authoritative_source_class_matches_repository_truth():
    from src.protection_observation_model import BROKER_AUTHORITATIVE_SOURCE_CLASSES, EvidenceSourceClass

    assert BROKER_AUTHORITATIVE_SOURCE_CLASSES == frozenset({EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION})
    text = _text(ARCHITECTURE_DOC)
    assert "LIVE_READ_ONLY_BROKER_OBSERVATION" in text
    assert "SIMULATED_BROKER" in text
    for member in EvidenceSourceClass:
        assert member.value in text, f"{member.value} not documented"


def test_documented_call_site_baseline_matches_current_guard_output():
    from tests.call_site_guard import LITERAL, SAFE_WRAPPER, analyze_runtime_tree

    sites = analyze_runtime_tree()
    literal_count = sum(1 for s in sites if s.kind == LITERAL)
    safe_wrapper_count = sum(1 for s in sites if s.kind == SAFE_WRAPPER)
    text = _text(ARCHITECTURE_DOC)
    assert f"{len(sites)} total call sites: {literal_count} literal, {safe_wrapper_count} safe_wrapper" in text


def test_documented_verifier_check_count_matches_reality():
    from scripts.verify_m13_protection_grounding import run_all_checks

    results = run_all_checks(REPOSITORY_ROOT)
    text = _text(ARCHITECTURE_DOC)
    assert f"Its {len(results)} checks" in text


def test_documented_verifier_tamper_test_count_matches_reality():
    """Counts actual pytest-collected test cases, not function definitions --
    a single parametrized function collects into multiple test cases, and
    the documented number must match what a reviewer actually sees run."""
    import subprocess
    import sys

    test_file = REPOSITORY_ROOT / "tests" / "test_m13_protection_grounding_verifier.py"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(test_file)],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True,
    )
    last_line = [line for line in result.stdout.splitlines() if line.strip()][-1]
    collected_count = int(last_line.split()[0])
    text = _text(ARCHITECTURE_DOC)
    assert f"and {collected_count} tamper/independence tests" in text


def test_promotion_policy_defaults_match_documented_claims():
    from src.protection_promotion_policy import DEFAULT_PROTECTION_PROMOTION_POLICY

    assert DEFAULT_PROTECTION_PROMOTION_POLICY.promotion_enabled is False
    assert DEFAULT_PROTECTION_PROMOTION_POLICY.independent_review_required is True
    text = _text(ARCHITECTURE_DOC)
    assert DEFAULT_PROTECTION_PROMOTION_POLICY.policy_version in text
    assert "promotion_enabled=False" in text
    assert "independent_review_required=True" in text


def test_automatic_promotion_permitted_cannot_be_true():
    from src.protection_promotion_policy import (
        DEFAULT_PROTECTION_PROMOTION_POLICY,
        ProtectionPromotionPolicy,
        ProtectionPromotionPolicyError,
    )
    import pytest

    with pytest.raises(ProtectionPromotionPolicyError):
        ProtectionPromotionPolicy(**{**DEFAULT_PROTECTION_PROMOTION_POLICY.__dict__, "automatic_promotion_permitted": True})


# --------------------------------------------------------------------------
# Safety-language contract on the runbook.
# --------------------------------------------------------------------------


def test_runbook_states_zero_write_requirement():
    text = _text(RUNBOOK).casefold()
    assert "write count is exactly `0`" in text or "write count is exactly 0" in text or "expected write count" in text
    assert "zero" in text


def test_runbook_never_instructs_a_broker_write():
    text = _text(RUNBOOK).casefold()
    forbidden = ["place order", "place_order", "cancel_order", "replace_order", "place_equity_order", "cancel_equity_order", "replace_equity_order", "submit an order"]
    for token in forbidden:
        assert token not in text, f"runbook appears to instruct a write action: {token!r}"
    assert "does not place, cancel, replace, or modify an order" in text.replace("\n", " ") or "never places, cancels, replaces, or modifies an order" in text.replace("\n", " ")


def test_runbook_contains_no_credential_or_secret_value():
    text = _text(RUNBOOK)
    forbidden_tokens = ["password", "api_key", "apikey", "secret_key", "bearer ", "Authorization: Bearer"]
    lowered = text.casefold()
    for token in forbidden_tokens:
        assert token.casefold() not in lowered, f"runbook appears to contain a credential-shaped token: {token!r}"


def test_runbook_never_shows_a_raw_account_or_order_identifier():
    text = _text(RUNBOOK)
    # Only placeholder-shaped tokens (angle brackets) are permitted for
    # anything account/order/position-identifier-shaped.
    for line in text.splitlines():
        casefolded = line.casefold()
        if "account" in casefolded and "number" in casefolded:
            assert (
                "<account_number>" in casefolded
                or "account number" in casefolded
                or "account-number" in casefolded
                or "raw account" in casefolded
                or "account fingerprint" in casefolded
            )


def test_runbook_requires_explicit_operator_authorization():
    text = _text(RUNBOOK).casefold()
    assert "explicit operator authorization" in text


def test_runbook_contains_abort_conditions():
    text = _text(RUNBOOK).casefold()
    assert "abort" in text
    assert "stop immediately" in text or "do not \"click through\"" in text or "do not" in text


def test_runbook_distinguishes_observation_from_approval():
    text = _text(RUNBOOK).casefold()
    assert "does not authorize another authority level" in text
    assert "evidence for review" in text or "not an approval" in text


def test_runbook_states_simulated_evidence_is_not_live_grounding():
    architecture_text = _text(ARCHITECTURE_DOC).casefold()
    assert "can never satisfy live grounding" in architecture_text


def test_runbook_states_no_protection_is_a_valid_outcome():
    text = _text(RUNBOOK)
    assert "NO_PROTECTIVE_ORDER_OBSERVED" in text
    assert "complete, successful observation" in text.casefold() or "successful observation" in text.casefold()


def test_runbook_does_not_claim_live_ready_or_unattended_ready():
    text = _text(RUNBOOK)
    assert "`live_ready` remains `false`" in text
    assert "`unattended_ready` remains `false`" in text
    assert "live_ready` is `true" not in text
    assert "unattended_ready` is `true" not in text


def test_runbook_never_proposes_a_corrective_broker_write_on_abort():
    text = _text(RUNBOOK).casefold()
    assert "do not** attempt a corrective broker write" in text or "not attempt a corrective broker write" in text
    assert "no cancel/replace capability" in text or "no cancel" in text


# --------------------------------------------------------------------------
# Q1/Q2 documentation matches source/policy.
# --------------------------------------------------------------------------


def test_q1_documentation_matches_source_authority_restriction():
    from src.protection_observation_model import BROKER_AUTHORITATIVE_SOURCE_CLASSES, EvidenceSourceClass

    assert len(BROKER_AUTHORITATIVE_SOURCE_CLASSES) == 1
    assert EvidenceSourceClass.SIMULATED_BROKER not in BROKER_AUTHORITATIVE_SOURCE_CLASSES
    text = _text(ARCHITECTURE_DOC)
    assert "NO_PROTECTIVE_ORDER_OBSERVED" in text
    assert "flat account is not a failed observation" in text


def test_q2_documentation_states_unresolved():
    text = _text(ARCHITECTURE_DOC)
    assert "Unresolved during M13" in text
    assert "minimum_distinct_eligible_live_runs" in text
    from src.protection_promotion_policy import DEFAULT_PROTECTION_PROMOTION_POLICY

    assert DEFAULT_PROTECTION_PROMOTION_POLICY.promotion_enabled is False


def test_architecture_doc_does_not_declare_m13_accepted():
    text = _text(ARCHITECTURE_DOC).casefold()
    assert "m13_infrastructure_accepted" not in text
    assert "milestone 13 is accepted" not in text
    assert "issue 10" in text


# --------------------------------------------------------------------------
# No live activity is implied as already having happened.
# --------------------------------------------------------------------------


def test_documents_do_not_claim_a_live_run_already_occurred():
    for doc in (ARCHITECTURE_DOC, RUNBOOK):
        text = _text(doc).casefold()
        assert "we ran the live observation" not in text
        assert "the live observation completed" not in text


def test_runbook_explicitly_forbids_execution_during_documentation():
    text = " ".join(_text(RUNBOOK).split())
    assert "does not authorize running it" in text


# --------------------------------------------------------------------------
# Route/evidence-view claims match repository truth.
# --------------------------------------------------------------------------


def test_operator_app_route_claims_match_repository_truth():
    from src.operator_web_app import build_operator_route_table

    table = build_operator_route_table()
    mutating = [r for r in table.routes if any(m in r.methods for m in ("POST", "PUT", "PATCH", "DELETE"))]
    assert mutating == []
    unsafe = [r for r in table.routes if r.safety_authority != "NONE" or r.permits_database_write or r.permits_runtime_write or r.permits_outbound_network or r.exposes_private_data]
    assert unsafe == []


def test_operator_web_app_source_never_imports_checkpoint_or_broker_modules():
    text = (REPOSITORY_ROOT / "src" / "operator_web_app.py").read_text(encoding="utf-8")
    for forbidden in ("protection_checkpoint_integration", "read_only_observation_runtime", "robinhood_client", "robinhood_live_adapter", "robinhood_commissioning"):
        assert forbidden not in text
