"""M13 Issue 10: acceptance-package contract tests.

Prove MILESTONE_13_ACCEPTANCE.md's claims are true right now, the same
way Issue 9's documentation-contract tests did for the architecture doc
and runbook. This is the milestone's closeout gate -- these tests must
independently recompute what they check, not just search for expected
substrings.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ACCEPTANCE_DOC = REPOSITORY_ROOT / "MILESTONE_13_ACCEPTANCE.md"


def _text() -> str:
    return ACCEPTANCE_DOC.read_text(encoding="utf-8")


def _unwrapped_text() -> str:
    """Whitespace-normalized text, for substring checks that must not
    depend on where Markdown happens to wrap a line."""
    return " ".join(_text().split())


def test_acceptance_document_exists():
    assert ACCEPTANCE_DOC.is_file()


def test_acceptance_document_cross_references_architecture_and_runbook():
    text = _text()
    assert "MILESTONE_13_PROTECTION_GROUNDING.md" in text
    assert "M13_LIVE_READ_ONLY_PROTECTION_OBSERVATION_RUNBOOK.md" in text
    assert (REPOSITORY_ROOT / "MILESTONE_13_PROTECTION_GROUNDING.md").is_file()
    assert (REPOSITORY_ROOT / "M13_LIVE_READ_ONLY_PROTECTION_OBSERVATION_RUNBOOK.md").is_file()


def test_documented_digest_matches_live_computation():
    from src.read_only_authorization_manifest import checkpoint_implementation_digest

    digest = checkpoint_implementation_digest(REPOSITORY_ROOT)
    assert digest in _text()


def test_documented_pinned_file_count_matches_live_computation():
    from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE

    count = len(PINNED_IMPLEMENTATION_CLOSURE)
    text = _text()
    assert f"**{count}**, independently confirmed" in text


def test_documented_route_count_matches_live_computation():
    from src.operator_web_app import build_operator_route_table

    count = len(build_operator_route_table().routes)
    text = _text()
    assert f"**{count}** registered routes" in text
    assert f"{count}/{count}/{count}/{count}" in text


def test_documented_call_site_counts_match_live_computation():
    from tests.call_site_guard import DYNAMIC, LITERAL, SAFE_WRAPPER, UNRESOLVED, analyze_runtime_tree

    sites = analyze_runtime_tree()
    total = len(sites)
    literal = sum(1 for s in sites if s.kind == LITERAL)
    safe_wrapper = sum(1 for s in sites if s.kind == SAFE_WRAPPER)
    dynamic = sum(1 for s in sites if s.kind == DYNAMIC)
    unresolved = sum(1 for s in sites if s.kind == UNRESOLVED)
    text = _text()
    assert f"**{total} total: {literal} literal, {safe_wrapper} safe_wrapper, {dynamic} dynamic, {unresolved} unresolved.**" in text
    assert dynamic == 0
    assert unresolved == 0


def test_documented_verifier_check_count_matches_live_computation():
    from scripts.verify_m13_protection_grounding import run_all_checks

    results = run_all_checks(REPOSITORY_ROOT)
    assert all(r.status == "PASS" for r in results)
    text = _unwrapped_text()
    assert f"{len(results)}/{len(results)} checks, 0 failed" in text


def test_m13_verifier_passes_end_to_end():
    from scripts.verify_m13_protection_grounding import build_report

    report = build_report(REPOSITORY_ROOT)
    assert report["final_result"] == "PASS"
    assert report["failed_count"] == 0


def test_operator_milestone_verifier_passes():
    import json

    result = subprocess.run(
        [sys.executable, "scripts/verify_operator_milestone.py"],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    counts = report["route_runtime"]["route_counts"]
    assert counts["registered"] == counts["matchable"] == counts["executable"] == counts["contract_compliant"]


def test_deployment_readiness_audit_passes():
    result = subprocess.run(
        [sys.executable, "scripts/deployment_readiness_audit.py"],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0


def test_architecture_audit_matches_checked_in_file():
    result = subprocess.run(
        [sys.executable, "scripts/architecture_audit.py", "--format", "markdown"],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True,
    )
    checked_in = (REPOSITORY_ROOT / "LEAN_ARCHITECTURE_AUDIT.md").read_text(encoding="utf-8")
    assert result.stdout == checked_in


# --------------------------------------------------------------------------
# Authority / classification claims.
# --------------------------------------------------------------------------


def test_final_classification_is_infrastructure_accepted_awaiting_live_evidence():
    text = _text()
    assert "M13_INFRASTRUCTURE_ACCEPTED_AWAITING_ELIGIBLE_LIVE_EVIDENCE" in text
    assert "protection_grounded=false" in text
    assert "activation_approved=false" in text
    assert "permits_automated_action=false" in text
    assert "live_ready=false" in text
    assert "unattended_ready=false" in text


def test_document_never_claims_an_authority_field_is_true():
    text = _text()
    for field in ("protection_grounded", "activation_approved", "permits_automated_action", "live_ready", "unattended_ready"):
        assert f"{field}=true" not in text
        assert f"{field}=True" not in text


def test_live_evidence_status_states_not_yet_collected():
    text = _text()
    assert "NOT YET COLLECTED" in text


def test_document_states_q2_unresolved_by_design():
    text = _text()
    assert "UNRESOLVED_BY_DESIGN" in text
    from src.protection_promotion_policy import DEFAULT_PROTECTION_PROMOTION_POLICY

    assert DEFAULT_PROTECTION_PROMOTION_POLICY.promotion_enabled is False


def test_document_states_q1_satisfied_with_evidence():
    text = _text()
    assert "**SATISFIED.**" in text
    from src.protection_observation_model import BROKER_AUTHORITATIVE_SOURCE_CLASSES

    assert len(BROKER_AUTHORITATIVE_SOURCE_CLASSES) == 1


def test_authority_state_is_actually_closed_repository_wide():
    """Independent of the document's own claim: re-derive the authority
    state directly from source, the same way the M13 verifier does."""
    import ast

    m13_paths = [
        "src/protection_observation_model.py", "src/protection_observation_ledger.py",
        "src/protection_promotion_policy.py", "src/protection_grounding_assessment.py",
        "src/protection_checkpoint_integration.py", "src/protection_assessment_store.py",
        "src/protection_operator_evidence.py", "src/operator_web_app.py",
        "read_only_observation_checkpoint_launcher.py",
    ]
    authority_symbols = {
        "protection_grounded", "activation_approved", "permits_automated_action",
        "live_ready", "unattended_ready", "ACTIVATION_APPROVALS",
        "ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES",
    }
    offending = []
    for relative in m13_paths:
        path = REPOSITORY_ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            targets, value = [], None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            if isinstance(value, ast.Constant) and value.value is False:
                continue
            for target in targets:
                name = target.id if isinstance(target, ast.Name) else (target.attr if isinstance(target, ast.Attribute) else None)
                if name in authority_symbols:
                    offending.append(f"{relative}:{node.lineno}")
    assert offending == []


def test_strategy_enabled_is_false_in_tracked_config():
    text = (REPOSITORY_ROOT / "config" / "strategy_v0.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in text


# --------------------------------------------------------------------------
# Lineage integrity: merge SHAs in the document are real, reachable commits.
# --------------------------------------------------------------------------


def test_documented_merge_shas_are_present_in_the_document():
    merge_shas = [
        "e237fea168c635572ac915b134c67081bacd6f07",
        "cac8b8b53d74b61f1811ba0785b3002ac1ae98f0",
        "389062d3d8f332cc2fab801100e3de48b58fba8f",
        "2b493e9cdbdc2babee3c0fc4e69abedcb5499701",
        "ac4f3111448f396c40e858562d07306f6decab79",
        "5a13281564a8fff095607dfb97b18d19597e0f0d",
        "911ea101ccf1ef89b89fbe980c824071b379c882",
        "345297e03db785045515f8e61a06f8b86f0c262e",
        "d5b0358535fa0d638bb1a70ead5c41fb57eb3957",
    ]
    text = _text()
    for sha in merge_shas:
        assert sha in text


def test_documented_merge_shas_are_real_ancestors_of_head_when_history_available():
    """A shallow checkout (CI's default, fetch-depth 1) has no historical
    commit objects available at all -- only HEAD itself. In that
    environment there is nothing to verify ancestry against, so this
    test only runs its real assertions against a full, unshallowed
    checkout (this developer's local clone); it is not skipped outright
    so a genuinely broken local clone still fails it."""
    is_shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if is_shallow == "true":
        import pytest

        pytest.skip("shallow checkout: no historical commit objects available to verify ancestry against")

    merge_shas = [
        "e237fea168c635572ac915b134c67081bacd6f07",
        "cac8b8b53d74b61f1811ba0785b3002ac1ae98f0",
        "389062d3d8f332cc2fab801100e3de48b58fba8f",
        "2b493e9cdbdc2babee3c0fc4e69abedcb5499701",
        "ac4f3111448f396c40e858562d07306f6decab79",
        "5a13281564a8fff095607dfb97b18d19597e0f0d",
        "911ea101ccf1ef89b89fbe980c824071b379c882",
        "345297e03db785045515f8e61a06f8b86f0c262e",
        "d5b0358535fa0d638bb1a70ead5c41fb57eb3957",
    ]
    for sha in merge_shas:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=REPOSITORY_ROOT,
        )
        assert result.returncode == 0, f"{sha} is not an ancestor of HEAD"


# --------------------------------------------------------------------------
# Non-authorization boundaries are stated explicitly.
# --------------------------------------------------------------------------


def test_document_states_non_authorization_boundaries():
    text = _text().casefold()
    assert "does not authorize" in text
    assert "automated broker access" in text
    assert "unattended execution" in text


def test_document_discloses_roadmap_staleness_rather_than_hiding_it():
    text = _text()
    assert "ROADMAP.md" in text
    assert "capital tiering" in _unwrapped_text().casefold()


def test_document_does_not_start_the_recommended_next_milestone():
    text = _text()
    assert "This issue does not start that milestone." in text
