"""Deterministic repository-evidence audit for supervised read-only deployment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from scripts.architecture_audit import audit_repository  # noqa: E402
from src.read_only_authorization_manifest import (  # noqa: E402
    PINNED_IMPLEMENTATION_CLOSURE,
    checkpoint_implementation_digest,
    resolve_head_sha,
)


SCHEMA_VERSION = 1
ALLOWED_STATUSES = frozenset({
    "VERIFIED_COMPLETE", "PARTIALLY_VERIFIED", "MISSING",
    "INTENTIONALLY_BLOCKED", "NOT_APPLICABLE",
})
ALLOWED_PRIORITIES = frozenset({"P0_BLOCKER", "P1_REQUIRED_BEFORE_DEPLOYMENT", "P2_REQUIRED_DURING_PILOT", "P3_LATER_HARDENING"})
ALLOWED_TYPES = frozenset({"CODE", "CONFIGURATION", "INFRASTRUCTURE", "OPERATIONS", "HUMAN_APPROVAL", "BROKER_DEPENDENCY", "DOCUMENTATION", "EVIDENCE"})


def _definition(category: str, status: str, refs: tuple[str, ...], missing: str,
                action: str, priority: str, kind: str, applicable: bool,
                *, code: bool = False, human: bool = False,
                broker: bool = False, manifest: bool = False,
                stronger: bool = False, risk: str = "No additional risk is established within repository scope.") -> dict[str, object]:
    return {
        "category": category, "status": status, "repository_evidence": "Repository contracts are present and bounded." if status == "VERIFIED_COMPLETE" else "Repository evidence is partial or absent for deployment use.",
        "exact_file_or_api_references": list(refs),
        "test_evidence": "Existing tests exercise the cited contract; tests are not treated as operational proof." if status == "VERIFIED_COMPLETE" else "No test result alone is treated as deployment evidence.",
        "operational_evidence": "No live operational evidence was used by this offline audit.",
        "missing_evidence": missing,
        "risk_if_unresolved": risk,
        "recommended_next_action": action,
        "code_required": code, "human_action_required": human,
        "broker_access_required": broker, "manifest_or_checkpoint_required": manifest,
        "stronger_model_review_recommended": stronger,
        "priority": priority, "type": kind,
        "applicable_to_supervised_read_only": applicable,
    }


_ROWS = (
    _definition("Runtime entrypoint", "PARTIALLY_VERIFIED", ("read_only_observation_checkpoint_launcher.py", "src/read_only_observation_runtime.py"), "A supervised host entrypoint contract is not documented as a deployment artifact.", "Define and review a read-only host invocation without adding write capability.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "CODE", True, human=True),
    _definition("Process lifecycle", "MISSING", ("src/read_only_observation_runtime.py",), "No host process start, stop, restart, or supervision evidence exists.", "Choose an operator-controlled process lifecycle and document it.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True, risk="A process can stop or duplicate without controlled operator visibility."),
    _definition("Configuration loading", "VERIFIED_COMPLETE", ("src/config.py:load_config", "config/strategy_v0.yaml"), "None within repository scope.", "Retain fail-closed configuration validation.", "P3_LATER_HARDENING", "CONFIGURATION", True),
    _definition("Environment separation", "PARTIALLY_VERIFIED", ("src/config.py", "config/strategy_v0.yaml"), "No host-specific environment separation or promotion procedure is evidenced.", "Define separate offline and supervised-read-only configuration handling.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "CONFIGURATION", True, human=True),
    _definition("Secret-handling boundaries", "VERIFIED_COMPLETE", ("src/read_only_authorization_manifest.py", "src/read_only_broker_state.py"), "No operational secret-scope exercise is evidenced.", "Review secret injection and redaction at the host boundary without exposing values.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "BROKER_DEPENDENCY", True, human=True, broker=True),
    _definition("Broker read-only interface boundary", "PARTIALLY_VERIFIED", ("src/read_only_broker_state.py", "src/read_only_observation_runtime.py"), "A real read-only connection and least-privilege contract remain unverified.", "Perform a separately controlled read-only interface review.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "BROKER_DEPENDENCY", True, human=True, broker=True),
    _definition("Write-capability exclusion", "VERIFIED_COMPLETE", ("src/execution_gates.py", "tests/test_execution_gates.py", "src/read_only_broker_state.py"), "None within structural repository scope.", "Preserve the closed gate and read-only boundary.", "P3_LATER_HARDENING", "EVIDENCE", True),
    _definition("Scheduler/calendar behavior", "PARTIALLY_VERIFIED", ("src/exchange_calendar.py", "src/durable_scheduler.py"), "Host scheduling and timezone deployment behavior are not operationally exercised.", "Document the finite scheduling contract and host timezone controls.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True),
    _definition("Durable restart", "VERIFIED_COMPLETE", ("src/offline_reconciliation.py", "src/durable_scheduler.py"), "A supervised host restart exercise is not evidenced.", "Run and record a bounded restart exercise before pilot use.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "EVIDENCE", True, human=True),
    _definition("Suspension and fail-closed behavior", "VERIFIED_COMPLETE", ("src/offline_safety_orchestrator.py", "src/durable_suspension_controller.py"), "No live operational suspension exercise is evidenced.", "Retain fail-closed behavior and document operator response.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "EVIDENCE", True, human=True),
    _definition("Liveness and heartbeat monitoring", "VERIFIED_COMPLETE", ("src/offline_shadow_runner.py", "src/durable_runner_liveness.py"), "Host monitoring ownership and exercise are missing.", "Assign ownership and test the bounded liveness response.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True),
    _definition("Alert delivery and confirmation", "PARTIALLY_VERIFIED", ("src/durable_alert_outbox.py", "src/operational_monitor.py"), "A deployed alert route, recipient, and delivery exercise are absent.", "Name the alert route and test delivery with an operator.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True),
    _definition("Operator acknowledgment", "PARTIALLY_VERIFIED", ("src/operator_acknowledgment.py", "src/shadow_readiness_report.py"), "A human review procedure and ownership are not established.", "Document who acknowledges review items and how evidence is retained.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "HUMAN_APPROVAL", True, human=True),
    _definition("Evidence persistence", "VERIFIED_COMPLETE", ("src/offline_evidence_summary.py", "src/durable_alert_outbox.py"), "Deployment storage lifecycle is not specified.", "Choose an operator-owned evidence storage location.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "INFRASTRUCTURE", True, human=True),
    _definition("Evidence integrity and tamper detection", "VERIFIED_COMPLETE", ("src/canonical_source_hash.py", "scripts/run_offline_commissioning_campaign.py", "scripts/verify_offline_campaign_acceptance.py"), "No external evidence custody exercise is evidenced.", "Retain canonical hashes and record them in the runbook.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "EVIDENCE", True, human=True),
    _definition("Privacy controls", "VERIFIED_COMPLETE", ("src/offline_path_safety.py", "scripts/verify_offline_campaign_acceptance.py"), "No host-specific privacy review is evidenced.", "Review host paths and logs for private-value exclusion.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "EVIDENCE", True, human=True),
    _definition("Repository-data path safety", "VERIFIED_COMPLETE", ("src/offline_path_safety.py", "scripts/run_offline_commissioning_campaign.py"), "No deployed filesystem review is evidenced.", "Define safe host directories without placing evidence in repository data paths.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "INFRASTRUCTURE", True, human=True),
    _definition("Logging and exception sanitization", "PARTIALLY_VERIFIED", ("src/offline_path_safety.py", "scripts/verify_offline_campaign_acceptance.py"), "Deployment log policy and exception redaction are not operationally evidenced.", "Define sanitized logging, retention, and operator access.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True),
    _definition("Operational status projection", "VERIFIED_COMPLETE", ("src/operational_status_report.py", "src/shadow_readiness_report.py"), "No deployed status display or ownership is evidenced.", "Select the operator-facing status view and document its limits.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True),
    _definition("Readiness projection", "VERIFIED_COMPLETE", ("src/shadow_readiness_report.py", "src/offline_evidence_summary.py"), "The projection is not deployment authorization and has no host operating procedure.", "Use readiness only as evidence; keep authorization external and closed.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "EVIDENCE", True, human=True),
    _definition("Commissioning execution", "VERIFIED_COMPLETE", ("scripts/run_offline_commissioning.py", "tests/test_offline_commissioning.py"), "No deployment execution is implied by offline commissioning.", "Retain commissioning as an offline preflight only.", "P3_LATER_HARDENING", "EVIDENCE", True),
    _definition("Campaign verification", "VERIFIED_COMPLETE", ("scripts/run_offline_commissioning_campaign.py", "tests/test_offline_commissioning_campaign.py"), "No host deployment evidence is created by a campaign.", "Use the campaign as bounded evidence only.", "P3_LATER_HARDENING", "EVIDENCE", True),
    _definition("Operator acceptance packaging", "VERIFIED_COMPLETE", ("scripts/verify_offline_campaign_acceptance.py", "OFFLINE_CAMPAIGN_OPERATOR_CHECKLIST.md"), "Checklist acknowledgment is not deployment approval.", "Keep acceptance evidence separate from runtime authorization.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "HUMAN_APPROVAL", True, human=True),
    _definition("Deployment configuration", "PARTIALLY_VERIFIED", ("src/read_only_deployment_profile.py:ReadOnlyDeploymentProfile", "scripts/validate_read_only_deployment_profile.py", "examples/read-only-deployment/"), "A validated profile exists, but host application and external deployment controls are not defined.", "Review a profile for a controlled host without treating it as deployment authorization.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "CONFIGURATION", True, human=True),
    _definition("Host/process supervision", "MISSING", ("src/read_only_observation_runtime.py",), "No process supervisor, ownership, or restart policy exists in repository evidence.", "Define operator-controlled host supervision outside the runtime.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "INFRASTRUCTURE", True, human=True, risk="Unsupervised process failure can silently remove observation coverage."),
    _definition("Backup and restore", "MISSING", ("src/offline_evidence_summary.py",), "No backup target or restore exercise is documented.", "Define backup and restore procedures for evidence only.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True),
    _definition("Database durability", "PARTIALLY_VERIFIED", ("src/durable_alert_outbox.py", "src/operator_acknowledgment.py"), "SQLite durability exists in modules, but host storage and recovery semantics are absent.", "Choose durable host storage and exercise recovery.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "INFRASTRUCTURE", True, human=True),
    _definition("Recovery-point objectives", "MISSING", ("src/offline_evidence_summary.py",), "No evidence-loss tolerance is defined.", "Set an operator-approved evidence recovery point objective.", "P2_REQUIRED_DURING_PILOT", "OPERATIONS", True, human=True),
    _definition("Recovery-time objectives", "MISSING", ("src/offline_safety_orchestrator.py",), "No restoration time target is defined.", "Set and exercise a bounded recovery time objective.", "P2_REQUIRED_DURING_PILOT", "OPERATIONS", True, human=True),
    _definition("Log retention", "MISSING", ("src/offline_path_safety.py",), "No log retention or deletion policy exists.", "Define retention and privacy-safe disposal.", "P2_REQUIRED_DURING_PILOT", "OPERATIONS", True, human=True),
    _definition("Alert routing ownership", "MISSING", ("src/durable_alert_outbox.py",), "No recipient or escalation owner is identified.", "Assign a route owner and test it.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "HUMAN_APPROVAL", True, human=True),
    _definition("On-call or operator ownership", "MISSING", ("OFFLINE_CAMPAIGN_OPERATOR_CHECKLIST.md",), "No named operating owner is evidenced.", "Assign the supervised operator and backup owner.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "HUMAN_APPROVAL", True, human=True),
    _definition("Runbook completeness", "PARTIALLY_VERIFIED", ("OFFLINE_COMMISSIONING_CAMPAIGN.md", "OFFLINE_CAMPAIGN_ACCEPTANCE.md"), "Host deployment, recovery, and rollback procedures are absent.", "Extend the runbook with only supervised read-only procedures.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "DOCUMENTATION", True, human=True),
    _definition("Rollback procedure", "MISSING", ("src/read_only_observation_runtime.py",), "No deployment rollback procedure is documented.", "Document removal and rollback without changing runtime authorization.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True),
    _definition("Controlled shutdown", "PARTIALLY_VERIFIED", ("src/offline_safety_orchestrator.py", "src/durable_suspension_controller.py"), "Host shutdown ownership and exercise are absent.", "Document operator-controlled shutdown and verify evidence closure.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "OPERATIONS", True, human=True),
    _definition("Clock/timezone behavior", "PARTIALLY_VERIFIED", ("src/exchange_calendar.py", "src/offline_fixture_package.py"), "Host clock synchronization and timezone policy are not operationally evidenced.", "Verify host clock and timezone before observation.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "INFRASTRUCTURE", True, human=True),
    _definition("Dependency pinning", "MISSING", ("requirements.txt",), "Dependency versions are not a complete deployment lock.", "Create a reviewed environment lock before host installation.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "CONFIGURATION", True, human=True),
    _definition("Version stamping", "PARTIALLY_VERIFIED", ("src/read_only_deployment_profile.py:release_commit_sha", "src/read_only_deployment_profile.py:implementation_digest"), "The profile binds source identity, but release packaging and promotion records are absent.", "Retain the bound identity in an externally reviewed release record.", "P2_REQUIRED_DURING_PILOT", "CONFIGURATION", True, human=True),
    _definition("Release artifact identity", "PARTIALLY_VERIFIED", ("src/read_only_authorization_manifest.py", "src/canonical_source_hash.py"), "A deployable artifact identity and release record are not defined.", "Record source SHA and implementation digest in the external release record.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "EVIDENCE", True, human=True),
    _definition("Production observability", "MISSING", ("src/operational_status_report.py",), "No host metrics, dashboards, or observation coverage view exists.", "Define minimal supervised observation visibility without a backend in this change.", "P2_REQUIRED_DURING_PILOT", "INFRASTRUCTURE", True, human=True),
    _definition("Read-only credential scope", "MISSING", ("src/read_only_broker_state.py", "src/read_only_authorization_manifest.py"), "No real least-privilege credential review is evidenced.", "Verify a read-only scope through an external controlled process.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "BROKER_DEPENDENCY", True, human=True, broker=True),
    _definition("Network allowlisting", "MISSING", ("src/read_only_broker_state.py",), "No deployment network path or allowlist is defined.", "Approve the required read-only network path externally.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "INFRASTRUCTURE", True, human=True, broker=True),
    _definition("Broker rate-limit handling", "MISSING", ("src/read_only_broker_state.py",), "No broker rate-limit exercise is evidenced.", "Review provider limits and bounded retry behavior before any connection.", "P2_REQUIRED_DURING_PILOT", "BROKER_DEPENDENCY", True, human=True, broker=True),
    _definition("Broker outage behavior", "PARTIALLY_VERIFIED", ("src/offline_safety_orchestrator.py", "src/durable_suspension_controller.py"), "Provider outage behavior is not exercised against a real read-only path.", "Define and test operator response to a read-only outage.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "BROKER_DEPENDENCY", True, human=True, broker=True),
    _definition("Real read-only checkpoint status", "INTENTIONALLY_BLOCKED", ("read_only_observation_checkpoint_launcher.py", "src/read_only_authorization_manifest.py"), "No real-account checkpoint is permitted in this offline audit.", "Keep the checkpoint blocked until separately authorized by the required external process.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "BROKER_DEPENDENCY", True, human=True, broker=True, manifest=True, stronger=True),
    _definition("Protective-state grounding status", "INTENTIONALLY_BLOCKED", ("src/read_only_observation_runtime.py", "src/execution_gates.py"), "Protection grounding is intentionally absent for read-only observation.", "Do not convert read-only evidence into a protection claim.", "P3_LATER_HARDENING", "HUMAN_APPROVAL", False, human=True),
    _definition("Human approval gates", "VERIFIED_COMPLETE", ("src/execution_gates.py", "tests/test_execution_gates.py"), "No approval is issued by this audit.", "Keep approvals empty and external to the application.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "HUMAN_APPROVAL", True, human=True),
    _definition("Supervised deployment approval", "MISSING", ("OFFLINE_CAMPAIGN_OPERATOR_CHECKLIST.md",), "No external supervised deployment approval is recorded.", "Obtain and retain a human-controlled deployment decision outside runtime.", "P1_REQUIRED_BEFORE_DEPLOYMENT", "HUMAN_APPROVAL", True, human=True, stronger=True),
    _definition("Live-write structural exclusion", "VERIFIED_COMPLETE", ("src/execution_gates.py", "src/read_only_broker_state.py", "tests/test_execution_gates.py"), "None within structural repository scope.", "Preserve the write exclusion and do not add deployment write paths.", "P3_LATER_HARDENING", "EVIDENCE", True),
    _definition("Unattended-operation structural exclusion", "VERIFIED_COMPLETE", ("src/execution_gates.py", "src/durable_scheduler.py", "tests/test_execution_gates.py"), "None within structural repository scope.", "Preserve the absence of unattended approval and services.", "P3_LATER_HARDENING", "EVIDENCE", True),
)


def _validate_rows(rows: Iterable[dict[str, object]]) -> tuple[dict[str, object], ...]:
    values = tuple(rows)
    expected = {row["category"] for row in _ROWS}
    names = [row.get("category") for row in values]
    if len(values) != 50 or len(set(names)) != len(names) or set(names) != expected:
        raise ValueError("deployment audit categories are incomplete or duplicated")
    for row in values:
        if row.get("status") not in ALLOWED_STATUSES or row.get("priority") not in ALLOWED_PRIORITIES or row.get("type") not in ALLOWED_TYPES:
            raise ValueError("deployment audit classification is invalid")
        if not row.get("exact_file_or_api_references") or not row.get("missing_evidence") or not row.get("recommended_next_action"):
            raise ValueError("deployment audit evidence is incomplete")
        if row["status"] == "VERIFIED_COMPLETE" and not row.get("repository_evidence"):
            raise ValueError("completion lacks repository evidence")
    return values


def _config_disabled(root: Path) -> bool:
    config = (root / "config" / "strategy_v0.yaml").read_text(encoding="utf-8")
    gates = (root / "src" / "execution_gates.py").read_text(encoding="utf-8")
    return "enabled: false" in config.casefold() and "ACTIVATION_APPROVALS:" in gates and "frozenset()" in gates


def _protection_count_closed(root: Path) -> bool:
    runtime = (root / "src" / "read_only_observation_runtime.py").read_text(encoding="utf-8")
    tests = (root / "tests" / "test_read_only_observation_runtime.py").read_text(encoding="utf-8")
    return "approved_active_protection_state_count: int = 0" in runtime and 'approved_active_protection_state_count"] == 0' in tests


def _percentage(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 0.0


def audit_deployment_readiness(root: Path = ROOT) -> dict[str, object]:
    """Return a deterministic, non-authorizing repository gap audit."""
    root = Path(root).resolve()
    rows = _validate_rows(_ROWS)
    counts = Counter(row["status"] for row in rows)
    applicable = tuple(row for row in rows if row["applicable_to_supervised_read_only"])
    complete_applicable = sum(row["status"] == "VERIFIED_COMPLETE" for row in applicable)
    architecture = audit_repository(root)
    report = {
        "schema_version": SCHEMA_VERSION,
        "audit_question": "What concrete repository, operational, and human-controlled gaps remain before ORB-LIVE may be deployed for supervised read-only operation, while live writes and unattended operation remain structurally impossible?",
        "repository_sha": resolve_head_sha(root),
        "implementation_digest": checkpoint_implementation_digest(root),
        "pinned_file_count": len(PINNED_IMPLEMENTATION_CLOSURE),
        "architecture_audit_reference": "scripts/architecture_audit.py:audit_repository",
        "executive_summary": "Offline engineering contracts are substantially evidenced, but supervised read-only deployment remains blocked by host, operational, broker-scope, recovery, ownership, and external-approval gaps.",
        "categories": list(rows),
        "category_counts": {status: counts.get(status, 0) for status in sorted(ALLOWED_STATUSES)},
        "readiness_percentages": {
            "offline_engineering": {"verified_complete": counts["VERIFIED_COMPLETE"], "applicable_categories": len(rows), "percent": _percentage(counts["VERIFIED_COMPLETE"], len(rows))},
            "supervised_read_only_deployment": {"verified_complete": complete_applicable, "applicable_categories": len(applicable), "percent": _percentage(complete_applicable, len(applicable))},
            "supervised_live_trading": "NOT_EVALUATED_FOR_AUTHORIZATION",
            "unattended_operation": "NOT_EVALUATED_FOR_AUTHORIZATION",
        },
        "top_p0_p1_gaps": [
            {"category": row["category"], "priority": row["priority"], "next_action": row["recommended_next_action"]}
            for row in rows if row["priority"] in {"P0_BLOCKER", "P1_REQUIRED_BEFORE_DEPLOYMENT"}
        ][:12],
        "completed_categories": [row["category"] for row in rows if row["status"] == "VERIFIED_COMPLETE"],
        "partial_categories": [row["category"] for row in rows if row["status"] == "PARTIALLY_VERIFIED"],
        "missing_categories": [row["category"] for row in rows if row["status"] == "MISSING"],
        "intentionally_blocked_categories": [row["category"] for row in rows if row["status"] == "INTENTIONALLY_BLOCKED"],
        "safety_invariants": {
            "strategy_enabled": not _config_disabled(root), "approved_protection_state_count": 0,
            "activation_approvals_empty": "ACTIVATION_APPROVALS:" in (root / "src" / "execution_gates.py").read_text(encoding="utf-8") and "frozenset()" in (root / "src" / "execution_gates.py").read_text(encoding="utf-8"),
            "protection_grounded": False, "activation_approved": False,
            "permits_automated_action": False, "live_ready": False, "unattended_ready": False,
        },
        "authorization_statement": "This audit creates no deployment authorization, live readiness, unattended readiness, approval, manifest, checkpoint, or runtime state change.",
        "architecture_audit_schema_version": architecture["schema_version"],
    }
    if report["safety_invariants"]["strategy_enabled"] or not report["safety_invariants"]["activation_approvals_empty"] or not _protection_count_closed(root):
        raise ValueError("safety invariant is not closed")
    return json.loads(json.dumps(report, sort_keys=True))


def render_markdown(report: dict[str, object]) -> str:
    percentages = report["readiness_percentages"]
    lines = [
        "# Deployment readiness gap audit", "", report["executive_summary"], "",
        f"Audit question: {report['audit_question']}", "",
        f"Repository SHA: `{report['repository_sha']}`", f"Implementation digest: `{report['implementation_digest']}`", f"Pinned implementation files: {report['pinned_file_count']}", "",
        "## Readiness percentages", "",
        f"- Offline engineering readiness: {percentages['offline_engineering']['percent']}% ({percentages['offline_engineering']['verified_complete']}/{percentages['offline_engineering']['applicable_categories']})",
        f"- Supervised read-only deployment readiness: {percentages['supervised_read_only_deployment']['percent']}% ({percentages['supervised_read_only_deployment']['verified_complete']}/{percentages['supervised_read_only_deployment']['applicable_categories']})",
        "- Supervised live-trading readiness: NOT_EVALUATED_FOR_AUTHORIZATION",
        "- Unattended-operation readiness: NOT_EVALUATED_FOR_AUTHORIZATION", "",
        "## Category totals", "",
    ]
    for status, count in report["category_counts"].items():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Top P0/P1 gaps", ""])
    blockers = report["top_p0_p1_gaps"]
    lines.extend(f"- {item['priority']}: {item['category']} — {item['next_action']}" for item in blockers)
    lines.extend(["", "## Category audit", "", "| Category | Status | Priority | Type | Applicable to supervised read-only |"])
    lines.append("|---|---|---|---|---|")
    for row in report["categories"]:
        lines.append(f"| {row['category']} | {row['status']} | {row['priority']} | {row['type']} | {row['applicable_to_supervised_read_only']} |")
    lines.extend(["", "## Evidence and next actions", ""])
    for row in report["categories"]:
        lines.extend([
            f"### {row['category']} — {row['status']}",
            f"- Repository evidence: {row['repository_evidence']}",
            f"- References: {', '.join(row['exact_file_or_api_references'])}",
            f"- Test evidence: {row['test_evidence']}",
            f"- Operational evidence: {row['operational_evidence']}",
            f"- Missing evidence: {row['missing_evidence']}",
            f"- Risk: {row['risk_if_unresolved']}",
            f"- Next action: {row['recommended_next_action']}",
            f"- Code required: {row['code_required']}; human action required: {row['human_action_required']}; broker access required: {row['broker_access_required']}; manifest/checkpoint required: {row['manifest_or_checkpoint_required']}; stronger-model review: {row['stronger_model_review_recommended']}", "",
        ])
    lines.extend(["## Explicit boundary", "", report["authorization_statement"], "", "The shortest safe path is to close the P1 host, operational ownership, read-only scope, network, recovery, and external approval gaps while preserving the structural write and unattended exclusions.", ""])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = audit_deployment_readiness(args.root)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(report, sort_keys=True, separators=(",", ":")) if args.format == "json" else render_markdown(report), end="")
        return 0
    except Exception:
        return 50


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["audit_deployment_readiness", "main", "render_markdown"]
