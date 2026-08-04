from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.architecture_audit import CLASSIFICATIONS, audit_repository, render_markdown


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    return audit_repository(ROOT)


def test_audit_is_deterministic_and_json_safe() -> None:
    first = audit_repository(ROOT)
    second = audit_repository(ROOT)
    assert first == second
    assert json.loads(json.dumps(first, sort_keys=True)) == first


def test_audit_uses_only_tracked_python_files_and_ignores_untracked_paths(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    source = repository / "src"
    source.mkdir(parents=True)
    tracked = source / "tracked.py"
    untracked = source / "already-present.py"
    tracked.write_text("VALUE = 1\n", encoding="utf-8")
    untracked.write_text("DO_NOT_TOUCH = 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "src/tracked.py"], cwd=repository, check=True)
    report = audit_repository(repository)
    assert [item["path"] for item in report["modules"]] == ["src/tracked.py"]
    assert untracked.read_text(encoding="utf-8") == "DO_NOT_TOUCH = 2\n"


def test_public_zero_reference_is_uncertainty_not_dead_code(report: dict[str, object]) -> None:
    finding = next(item for item in report["findings"] if item["title"] == "Public surfaces without static repository callers")
    assert finding["classification"] == "NEEDS_RUNTIME_EVIDENCE"
    assert "not proven dead code" in finding["interpretation"]
    assert all(item["classification"] != "DELETE_CANDIDATE" for item in report["findings"])


def test_enum_absence_requires_runtime_evidence(report: dict[str, object]) -> None:
    finding = next(item for item in report["findings"] if item["title"] == "Enum members without qualified references")
    assert finding["classification"] == "NEEDS_RUNTIME_EVIDENCE"
    assert "Persisted values" in finding["interpretation"]


def test_required_inventory_sections_and_classifications_are_exact(report: dict[str, object]) -> None:
    assert set(report) == {
        "schema_version", "scope", "production_loc", "test_loc", "modules",
        "public_surfaces", "enums", "reason_codes_without_construction", "helper_purposes",
        "exact_helper_groups", "pattern_inventory", "table_purposes", "duplicate_test_bodies",
        "test_heavy_modules", "findings", "estimated_removable_production_loc",
        "estimated_removable_test_loc", "exact_body_consolidation_candidates",
        "consolidation_decision", "limitations",
    }
    assert len(report["findings"]) == 10
    assert {item["classification"] for item in report["findings"]} <= set(CLASSIFICATIONS)
    assert [item["rank"] for item in report["findings"]] == list(range(1, 11))
    assert report["exact_body_consolidation_candidates"] == []


def test_reference_counts_are_module_qualified_and_not_off_by_one(tmp_path: Path) -> None:
    repository = tmp_path / "references"
    source = repository / "src"
    source.mkdir(parents=True)
    (source / "a.py").write_text(
        "from enum import Enum\ndef public():\n    return 1\nclass Reason(Enum):\n    USED = 'USED'\n    UNUSED = 'UNUSED'\n",
        encoding="utf-8",
    )
    (source / "b.py").write_text(
        "from src.a import Reason, public\nRESULT = public()\nVALUE = Reason.USED\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "src/a.py", "src/b.py"], cwd=repository, check=True)
    selected = audit_repository(repository)
    surface = next(item for item in selected["public_surfaces"] if item["name"] == "public")
    enum = next(item for item in selected["enums"] if item["name"] == "Reason")
    assert surface["repository_references"] == 1
    assert enum["unused_members"] == ["UNUSED"]


def test_audit_inventories_new_offline_modules_and_sqlite_tables(report: dict[str, object]) -> None:
    modules = {item["path"]: item for item in report["modules"]}
    assert modules["src/durable_alert_outbox.py"]["sqlite_tables"] == [
        "alert_origins", "alert_transitions", "alerts", "delivery_evidence",
        "outbox_meta", "spent_identities",
    ]
    assert modules["src/operator_acknowledgment.py"]["sqlite_tables"] == [
        "acknowledgment_artifacts", "acknowledgment_meta", "acknowledgment_origins",
        "acknowledgment_spent", "acknowledgment_transitions", "acknowledgments",
    ]
    assert modules["src/shadow_readiness_report.py"]["sqlite_tables"] == []


def test_repeated_safety_ledgers_require_equivalence_proof(report: dict[str, object]) -> None:
    findings = {item["title"]: item for item in report["findings"]}
    assert findings["Transition journals are intentional durable evidence"]["classification"] == "NEEDS_RUNTIME_EVIDENCE"
    assert findings["Spent-identity ledgers protect different replay domains"]["classification"] == "NEEDS_RUNTIME_EVIDENCE"
    assert findings["Fingerprint and provenance layers bind distinct trust boundaries"]["classification"] == "NEEDS_RUNTIME_EVIDENCE"


def test_markdown_is_concise_prioritized_and_privacy_safe(report: dict[str, object]) -> None:
    output = render_markdown(report)
    assert output.startswith("# Lean Architecture Audit\n")
    assert output.count("Estimated removable LOC:") == 10
    assert "PRIVATE" not in output
    assert "account number" not in output.casefold()
    assert len(output.splitlines()) < 100
    assert (ROOT / "LEAN_ARCHITECTURE_AUDIT.md").read_text(encoding="utf-8") == output


def test_cli_output_matches_library_output() -> None:
    completed = subprocess.run(
        ["python", "scripts/architecture_audit.py", "--format", "json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == audit_repository(ROOT)


def test_audit_has_no_repository_write_or_network_surface() -> None:
    source = (ROOT / "scripts" / "architecture_audit.py").read_text(encoding="utf-8")
    for forbidden in ("write_text", "write_bytes", "requests", "httpx", "socket", "sqlite3.connect", "robin_stocks"):
        assert forbidden not in source
