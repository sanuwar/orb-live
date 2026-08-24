"""Focused tests for the Publication P2 (Issue #366) evidence-hardening
package: the canonical hypothesis ledger JSON, its deterministic Markdown
generator, and the P2 documentation set under docs/publication/p2/.

These tests read only committed files and the generator script. They fetch
no network resource, read no market-data cache, and modify no trading,
strategy, or M55 checkpoint artifact.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import scripts.publication.generate_p2_hypothesis_ledger as gen

ROOT = Path(__file__).resolve().parents[1]
P2_DIR = ROOT / "docs" / "publication" / "p2"
LEDGER_JSON_PATH = P2_DIR / "HYPOTHESIS_LEDGER_M43_M55.json"
LEDGER_MD_PATH = P2_DIR / "HYPOTHESIS_LEDGER_M43_M55.md"

REQUIRED_ENTRY_FIELDS = {
    "milestone",
    "strategy_family",
    "arm_id",
    "entry_policy",
    "control_status",
    "diagnostic_sleeve",
    "decision_eligible",
    "evidence_class",
    "primary_research_question",
    "evaluation_window",
    "primary_endpoint_proposed_for_p3",
    "secondary_descriptive_outcomes",
    "final_classification",
    "authoritative_artifact_path",
    "commit_sha",
    "git_blob_hash",
    "superseded_artifacts_or_figures",
    "correction_lineage",
    "limitations",
    "m55_prospective_status",
}

VALID_EVIDENCE_CLASSES = {
    "retrospective_frozen_protocol",
    "blocked_validation",
    "literature_selection_gate",
    "synthesis",
    "prospective",
}

VALID_ENTRY_POLICIES = {"primary", "sensitivity"}

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")

FORBIDDEN_CLAIM_PATTERNS = [
    re.compile(r"\bproduction[- ]ready\b", re.IGNORECASE),
    re.compile(r"\breliably profitable\b", re.IGNORECASE),
    re.compile(r"\bguaranteed (income|profit|return)\b", re.IGNORECASE),
    re.compile(r"\bsafe to (deploy|trade|activate)\b", re.IGNORECASE),
]

# Phrases that legitimately contain "not" + a forbidden word (explicit
# disclaimers) and must not trip the forbidden-claim scan.
DISCLAIMER_SNIPPETS = [
    "no strategy is production-ready, deployable, or reliably profitable",
    "no production-ready or reliably profitable strategy is claimed",
    "not production-ready, deployable, or reliably profitable",
    "no production-ready or profitable strategy is claimed",
]

# A forbidden phrase inside a 80-char window that also contains an explicit
# negation token is treated as a disclaimer, not an unsupported claim.
NEGATION_TOKENS = (
    "no ",
    "not ",
    "none ",
    "never ",
    "n't ",
    "nothing ",
    "disclaim",
    "unsupported",
    "prohibited",
    "must not",
)


@pytest.fixture(scope="module")
def ledger() -> dict:
    return json.loads(LEDGER_JSON_PATH.read_text(encoding="utf-8"))


def test_ledger_json_is_valid_json_with_required_top_level_keys(ledger):
    assert "schema_version" in ledger
    assert "evidence_cutoff_main_sha" in ledger
    assert SHA1_RE.match(ledger["evidence_cutoff_main_sha"])
    assert "counting_rules" in ledger
    assert "entries" in ledger
    assert isinstance(ledger["entries"], list)
    assert len(ledger["entries"]) > 0


def test_every_entry_has_all_required_fields(ledger):
    for i, entry in enumerate(ledger["entries"]):
        missing = REQUIRED_ENTRY_FIELDS - set(entry.keys())
        assert not missing, f"entry {i} ({entry.get('arm_id')}) missing fields: {missing}"


def test_evidence_classifications_are_valid(ledger):
    for entry in ledger["entries"]:
        assert entry["evidence_class"] in VALID_EVIDENCE_CLASSES, entry["evidence_class"]
    for stage in ledger.get("non_ledger_evidence_stages", []):
        assert stage["evidence_class"] in VALID_EVIDENCE_CLASSES, stage["evidence_class"]


def test_entry_policy_values_are_valid(ledger):
    for entry in ledger["entries"]:
        assert entry["entry_policy"] in VALID_ENTRY_POLICIES


def test_boolean_fields_are_actual_booleans(ledger):
    for entry in ledger["entries"]:
        for field in ("control_status", "diagnostic_sleeve", "decision_eligible"):
            assert isinstance(entry[field], bool), f"{entry['arm_id']}.{field} is not bool"


def test_referenced_artifact_paths_exist(ledger):
    for entry in ledger["entries"]:
        path = ROOT / entry["authoritative_artifact_path"]
        assert path.is_file(), f"missing artifact: {entry['authoritative_artifact_path']}"
    for stage in ledger.get("non_ledger_evidence_stages", []):
        path = ROOT / stage["authoritative_artifact_path"]
        assert path.is_file(), f"missing artifact: {stage['authoritative_artifact_path']}"


def test_commit_and_blob_hashes_are_well_formed_sha1(ledger):
    for entry in ledger["entries"]:
        assert SHA1_RE.match(entry["commit_sha"]), entry["commit_sha"]
        assert SHA1_RE.match(entry["git_blob_hash"]), entry["git_blob_hash"]
    for stage in ledger.get("non_ledger_evidence_stages", []):
        assert SHA1_RE.match(stage["commit_sha"]), stage["commit_sha"]
        assert SHA1_RE.match(stage["git_blob_hash"]), stage["git_blob_hash"]


def test_exact_14_17_21_reconciliation(ledger):
    entries = ledger["entries"]
    rules = ledger["counting_rules"]

    novel = [e for e in entries if gen._is_novel_decision_eligible(e)]
    displayed = [e for e in entries if gen._is_displayed_arm_label(e)]

    assert len(novel) == 14 == rules["novel_decision_eligible_variants"]["count"]
    assert len(displayed) == 17 == rules["displayed_arm_labels"]["count"]
    assert len(entries) == 21 == rules["arm_by_entry_policy_diagnostic_evaluations"]["count"]

    # The 17-count must be a strict superset of the 14-count (controls and
    # diagnostic sleeves added, nothing removed).
    novel_ids = {(e["milestone"], e["arm_id"]) for e in novel}
    displayed_ids = {(e["milestone"], e["arm_id"]) for e in displayed}
    assert novel_ids.issubset(displayed_ids)

    # The 21-count must be the 17-count plus exactly the 4 M43 sensitivity
    # replays, and nothing else.
    sensitivity_entries = [e for e in entries if e["entry_policy"] == "sensitivity"]
    assert len(sensitivity_entries) == 4
    assert all(e["milestone"] == "M43" for e in sensitivity_entries)
    assert len(displayed) + len(sensitivity_entries) == len(entries)


def test_no_hidden_experiments_were_invented(ledger):
    # Every entry must trace to one of the milestones actually present in
    # the committed M43-M55 research-report set; no milestone number outside
    # that closed range may appear.
    valid_milestones = {"M43", "M44", "M45", "M46", "M47", "M48", "M49", "M52", "M53"}
    for entry in ledger["entries"]:
        assert entry["milestone"] in valid_milestones, entry["milestone"]


def test_no_prospective_m55_performance_record_present(ledger):
    m55_stage = next(s for s in ledger["non_ledger_evidence_stages"] if s["milestone"] == "M55")
    note = m55_stage["note"].lower()
    assert "zero transactions" in note
    assert "zero positions" in note or "empty" in note
    assert "initialized_waiting_for_first_signal" in note.lower()
    # No entry in the main ledger may claim to be an M55 performance result.
    for entry in ledger["entries"]:
        assert entry["milestone"] != "M55"


def test_no_unsupported_deployability_or_profitability_claim_in_p2_docs():
    for path in P2_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_CLAIM_PATTERNS:
            for match in pattern.finditer(text):
                start = max(0, match.start() - 80)
                end = min(len(text), match.end() + 80)
                window = text[start:end].lower()
                is_disclaimer = (
                    any(snippet in window for snippet in DISCLAIMER_SNIPPETS)
                    or any(token in window for token in NEGATION_TOKENS)
                )
                assert is_disclaimer, (
                    f"possible unsupported claim in {path.name}: "
                    f"...{text[start:end]}..."
                )


def test_final_newline_and_whitespace_discipline():
    md_files = list(P2_DIR.glob("*.md")) + [
        ROOT / "scripts" / "publication" / "generate_p2_hypothesis_ledger.py"
    ]
    for path in md_files:
        raw = path.read_bytes()
        assert raw.endswith(b"\n"), f"{path.name} missing trailing newline"
        text = raw.decode("utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            assert line == line.rstrip(), f"{path.name}:{lineno} has trailing whitespace"
        assert "\t" not in text, f"{path.name} contains a tab character"


def test_generator_is_idempotent_and_matches_committed_markdown(tmp_path):
    ledger_data = gen.load_ledger()
    rendered_once = gen.render_markdown(ledger_data)
    rendered_twice = gen.render_markdown(ledger_data)
    assert rendered_once == rendered_twice

    committed = LEDGER_MD_PATH.read_text(encoding="utf-8")
    assert rendered_once == committed, (
        "HYPOTHESIS_LEDGER_M43_M55.md is stale relative to the JSON ledger; "
        "regenerate with `python -m scripts.publication.generate_p2_hypothesis_ledger`"
    )


def test_generator_requires_no_network_access(monkeypatch):
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("generator attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    ledger_data = gen.load_ledger()
    gen.render_markdown(ledger_data)  # must not raise


def test_generator_reads_no_market_data_cache():
    source = (ROOT / "scripts" / "publication" / "generate_p2_hypothesis_ledger.py").read_text(
        encoding="utf-8"
    )
    assert "artifacts/research-" not in source
    assert "alpaca" not in source.lower()
    assert "robin_stocks" not in source.lower()


def test_p2_deliverable_manifest_is_complete():
    expected = {
        "README.md",
        "HYPOTHESIS_LEDGER_M43_M55.json",
        "HYPOTHESIS_LEDGER_M43_M55.md",
        "STATISTICAL_INPUT_INVENTORY.md",
        "FROZEN_STATISTICAL_ANALYSIS_PROTOCOL.md",
        "LITERATURE_AND_NOVELTY_MAP.md",
        "DATA_LICENSE_REDISTRIBUTION_MATRIX.md",
        "ACTOR_PROVENANCE_AND_CORRECTION_TAXONOMY.md",
        "REPRODUCIBILITY_AND_ARCHIVE_BLUEPRINT.md",
        "EXTERNAL_REVIEW_AND_JOURNAL_PRECHECK_PLAN.md",
        "P2_READINESS_DECISION.md",
    }
    actual = {p.name for p in P2_DIR.iterdir() if p.is_file()}
    assert expected == actual
