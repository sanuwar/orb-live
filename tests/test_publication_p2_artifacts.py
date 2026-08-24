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
import subprocess
from pathlib import Path

import pytest

import scripts.publication.generate_p2_hypothesis_ledger as gen

ROOT = Path(__file__).resolve().parents[1]
P2_DIR = ROOT / "docs" / "publication" / "p2"
LEDGER_JSON_PATH = P2_DIR / "HYPOTHESIS_LEDGER_M43_M55.json"
LEDGER_MD_PATH = P2_DIR / "HYPOTHESIS_LEDGER_M43_M55.md"
PROTOCOL_CANDIDATE_PATH = P2_DIR / "STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md"
READINESS_DECISION_PATH = P2_DIR / "P2_READINESS_DECISION.md"

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
    "inferential_evaluability",
    "inferential_evaluability_rationale",
}

VALID_EVIDENCE_CLASSES = {
    "retrospective_frozen_protocol",
    "blocked_validation",
    "literature_selection_gate",
    "synthesis",
    "prospective",
}

VALID_ENTRY_POLICIES = {"primary", "sensitivity"}

VALID_INFERENTIAL_EVALUABILITY = {
    "INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA",
    "DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE",
    "NOT_EVALUABLE_STRUCTURAL_NULL",
    "CONTROL_OR_DIAGNOSTIC_ONLY",
}

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


def _is_shallow_clone() -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _git_blob_hash_at_commit(commit_sha: str, relative_path: str) -> str:
    """Resolve the blob hash for `relative_path` as it existed at `commit_sha`.

    Uses only the local Git object database already present in this
    checkout -- no network access, no market-data cache. Historical commits
    are only reachable in a full (non-shallow) clone; callers should skip
    via `_is_shallow_clone()` rather than let this raise in CI's default
    shallow checkout.
    """
    completed = subprocess.run(
        ["git", "rev-parse", f"{commit_sha}:{relative_path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git could not resolve {commit_sha}:{relative_path} -- "
            f"stderr: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()

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
        "STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md",
        "LITERATURE_AND_NOVELTY_MAP.md",
        "DATA_LICENSE_REDISTRIBUTION_MATRIX.md",
        "ACTOR_PROVENANCE_AND_CORRECTION_TAXONOMY.md",
        "REPRODUCIBILITY_AND_ARCHIVE_BLUEPRINT.md",
        "EXTERNAL_REVIEW_AND_JOURNAL_PRECHECK_PLAN.md",
        "P2_READINESS_DECISION.md",
    }
    actual = {p.name for p in P2_DIR.iterdir() if p.is_file()}
    assert expected == actual
    # The old frozen-protocol filename must not linger anywhere in the package.
    assert not (P2_DIR / "FROZEN_STATISTICAL_ANALYSIS_PROTOCOL.md").exists()


def test_inferential_evaluability_values_are_valid(ledger):
    for entry in ledger["entries"]:
        assert entry["inferential_evaluability"] in VALID_INFERENTIAL_EVALUABILITY, entry["inferential_evaluability"]
        assert isinstance(entry["inferential_evaluability_rationale"], str)
        assert entry["inferential_evaluability_rationale"].strip()


def test_inferential_evaluability_breakdown_of_the_14_matches_spec(ledger):
    novel = [e for e in ledger["entries"] if gen._is_novel_decision_eligible(e)]
    assert len(novel) == 14

    by_category: dict[str, set[str]] = {}
    for e in novel:
        by_category.setdefault(e["inferential_evaluability"], set()).add(f"{e['milestone']}:{e['arm_id']}")

    assert by_category["INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA"] == {
        "M43:BE_AFTER_050R",
        "M43:PROFIT_LADDER",
        "M43:STRUCTURE_TRAIL_AFTER_1R",
        "M45:LATER_FIRST_VALID_1020",
        "M48:ETF_PULLBACK_RSI2",
        "M48:SECTOR_MOMENTUM_12_1",
        "M48:DIVERSIFIED_TREND_12M",
        "M49:PRACTICAL_CORE_SATELLITE_30K",
        "M52:TOP15_MOMENTUM_BASELINE",
        "M53:BREADTH_30",
        "M53:MARKET_VOL_15",
    }
    assert by_category["DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE"] == {
        "M44:FALLBACK_FIRST_VALID_0950",
        "M46:SMALL_MID",
    }
    assert by_category["NOT_EVALUABLE_STRUCTURAL_NULL"] == {"M47:SEQUENTIAL_SECOND_VALID_1020"}
    # No entry among the 14 may be CONTROL_OR_DIAGNOSTIC_ONLY -- that category
    # is definitionally for the 7 excluded controls/diagnostics, not the 14.
    assert "CONTROL_OR_DIAGNOSTIC_ONLY" not in by_category


def test_m44_and_m47_carry_no_fabricated_inference(ledger):
    # Regression test for the specific correction: M44 (n=2) and M47 (n=0)
    # must never be assigned a p-value, confidence interval, or any
    # fabricated observation anywhere in the ledger.
    m44 = next(e for e in ledger["entries"] if e["milestone"] == "M44" and e["arm_id"] == "FALLBACK_FIRST_VALID_0950")
    m47 = next(e for e in ledger["entries"] if e["milestone"] == "M47" and e["arm_id"] == "SEQUENTIAL_SECOND_VALID_1020")
    assert m44["inferential_evaluability"] == "DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE"
    assert m47["inferential_evaluability"] == "NOT_EVALUABLE_STRUCTURAL_NULL"
    for field in ("final_classification", "inferential_evaluability_rationale", "limitations"):
        for forbidden in ("p=0.", "p-value of 0.", "95% CI", "confidence interval of"):
            assert forbidden not in m44[field]
            assert forbidden not in m47[field]


def test_m46_family_membership_is_corrected(ledger):
    m46_entries = [e for e in ledger["entries"] if e["milestone"] == "M46"]
    assert len(m46_entries) == 3
    for e in m46_entries:
        assert e["strategy_family"] == "Intraday ORB entry/exit/frequency/universe extension", e["arm_id"]
        assert e["strategy_family"] != "ETF pullback / sector momentum / diversified trend"

    # And the ETF family must now contain exactly the 4 genuine ETF/core-satellite arms.
    etf_novel = {
        (e["milestone"], e["arm_id"])
        for e in ledger["entries"]
        if gen._is_novel_decision_eligible(e) and e["strategy_family"] == "ETF pullback / sector momentum / diversified trend"
    }
    assert etf_novel == {
        ("M48", "ETF_PULLBACK_RSI2"),
        ("M48", "SECTOR_MOMENTUM_12_1"),
        ("M48", "DIVERSIFIED_TREND_12M"),
        ("M49", "PRACTICAL_CORE_SATELLITE_30K"),
    }


def test_every_novel_variant_appears_in_protocol_family_table_exactly_once(ledger):
    novel = [e for e in ledger["entries"] if gen._is_novel_decision_eligible(e)]
    assert len(novel) == 14

    protocol_text = PROTOCOL_CANDIDATE_PATH.read_text(encoding="utf-8")
    # Extract just the §1 family *table* itself (the block of "|"-prefixed
    # lines), not the surrounding prose, which legitimately re-mentions an
    # arm_id (e.g. explaining the M46 correction) without that counting as a
    # second table entry.
    section_1 = protocol_text.split("## 1. Hypothesis families", 1)[1]
    section_1 = section_1.split("### 1a.", 1)[0]
    table_lines = [line for line in section_1.splitlines() if line.strip().startswith("|")]
    table_text = "\n".join(table_lines)

    for e in novel:
        occurrences = table_text.count(e["arm_id"])
        assert occurrences == 1, (
            f"{e['milestone']}:{e['arm_id']} appears {occurrences} times in the "
            f"protocol's §1 family table (expected exactly once)"
        )

    # Sum of the per-family "Count" column must equal 14.
    counts = re.findall(r"\|\s*[\w /\-]+\|\s*[^|]+\|\s*(\d+)\s*\|", table_text)
    assert counts, "could not locate the family-count column in the protocol table"
    assert sum(int(c) for c in counts) == 14


def test_protocol_candidate_is_not_labeled_executable_or_frozen():
    text = PROTOCOL_CANDIDATE_PATH.read_text(encoding="utf-8")
    assert "DRAFT CANDIDATE FOR EXTERNAL FINANCE/ECONOMETRICS REVIEW" in text
    assert "NOT AUTHORIZED FOR EXECUTION" in text
    assert "Status: FROZEN, NOT EXECUTED" not in text
    assert "Unresolved Decisions Registry" in text
    for item in (
        "Bootstrap replication count",
        "Random seed",
        "Exact block lengths",
        "Common vs. family-specific block selection",
        "Null hypotheses",
        "Comparators",
        "Test-statistic standardization",
        "Treatment of non-evaluable variants",
    ):
        assert item in text, f"missing unresolved-decision item: {item}"


def test_p3_readiness_remains_blocked_pending_review_and_preflight():
    text = READINESS_DECISION_PATH.read_text(encoding="utf-8")
    section_6 = text.split("## 6. Is the project ready to start P3 statistical reanalysis?", 1)[1]
    section_6 = section_6.split("## 7.", 1)[0]
    assert section_6.strip().startswith("**BLOCKED")
    assert "external finance/econometrics reviewer" in section_6
    assert "preflight" in section_6.lower()

    section_3 = text.split("## 3. Which P3 analyses are executable from committed evidence?", 1)[1]
    section_3 = section_3.split("## 4.", 1)[0]
    assert section_3.strip().startswith("**BLOCKED")

    section_7 = text.split("## 7. Can methods-manuscript drafting begin in parallel?", 1)[1]
    section_7 = section_7.split("## 8.", 1)[0]
    assert section_7.strip().startswith("**READY_WITH_LIMITATIONS")


def test_ledger_citations_resolve_against_actual_git_history_offline(ledger):
    """For every ledger citation, confirm commit:path exists, resolve the
    blob at that historical commit, and confirm it equals the ledger's
    recorded blob hash -- using only the local Git object database already
    present in this checkout. No network or market-data access.

    Requires a full (non-shallow) clone, since it resolves historical
    commits that predate HEAD; CI's default `actions/checkout` uses
    fetch-depth: 1, so this is skipped (not failed, not silently passed)
    there, and runs for real wherever full history is available."""
    if _is_shallow_clone():
        pytest.skip("shallow clone: historical commits not fetched, cannot verify offline")
    for entry in ledger["entries"]:
        resolved = _git_blob_hash_at_commit(entry["commit_sha"], entry["authoritative_artifact_path"])
        assert resolved == entry["git_blob_hash"], (
            f"{entry['milestone']}:{entry['arm_id']} -- ledger blob hash "
            f"{entry['git_blob_hash']} does not match the actual blob "
            f"{resolved} at {entry['commit_sha']}:{entry['authoritative_artifact_path']}"
        )
    for stage in ledger.get("non_ledger_evidence_stages", []):
        resolved = _git_blob_hash_at_commit(stage["commit_sha"], stage["authoritative_artifact_path"])
        assert resolved == stage["git_blob_hash"], (
            f"{stage['milestone']} -- ledger blob hash {stage['git_blob_hash']} "
            f"does not match the actual blob {resolved} at "
            f"{stage['commit_sha']}:{stage['authoritative_artifact_path']}"
        )


def test_m55_checkpoint_ledger_hash_matches_cited_evidence_cutoff(ledger):
    """Requires a full (non-shallow) clone for the same reason as
    test_ledger_citations_resolve_against_actual_git_history_offline."""
    if _is_shallow_clone():
        pytest.skip("shallow clone: historical commits not fetched, cannot verify offline")
    m55_stage = next(s for s in ledger["non_ledger_evidence_stages"] if s["milestone"] == "M55")
    checkpoint_path = m55_stage["checkpoint_ledger_path"]
    expected_hash = m55_stage["checkpoint_ledger_blob_hash"]

    # The blob at M55's own cited commit must match.
    resolved_at_m55_commit = _git_blob_hash_at_commit(m55_stage["commit_sha"], checkpoint_path)
    assert resolved_at_m55_commit == expected_hash

    # The blob at the ledger's overall evidence cutoff (main, post-merge)
    # must also still match -- proving the M55 checkpoint has not been
    # touched since it was cited.
    resolved_at_cutoff = _git_blob_hash_at_commit(ledger["evidence_cutoff_main_sha"], checkpoint_path)
    assert resolved_at_cutoff == expected_hash, (
        "M55 checkpoint ledger blob differs between its cited commit and the "
        "P2 evidence cutoff -- it may have been altered, which would violate "
        "the M55 untouched-evidence boundary"
    )
