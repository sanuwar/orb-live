"""Deterministic tests for the change-aware CI tier classifier (Issue #370).

The classifier's safety property is one-directional: it may only ever be
wrong by demanding *more* verification than strictly necessary. These
tests pin that property, including for malformed and unknown input.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci.classify_changes import (
    TIER_1,
    TIER_2,
    TIER_3,
    Classification,
    classify,
)

ROOT = Path(__file__).resolve().parents[1]


# --- the twelve required classification cases -------------------------------


def test_case_01_publication_markdown_only_is_tier_1():
    result = classify(
        [
            "docs/publication/p3a/README.md",
            "docs/publication/p2/STATISTICAL_INPUT_INVENTORY.md",
        ]
    )
    assert result.tier == TIER_1
    assert result.runs_full_suite is False


def test_case_02_publication_generator_and_publication_test_is_tier_2():
    result = classify(
        [
            "docs/publication/p2/README.md",
            "scripts/publication/generate_p2_hypothesis_ledger.py",
            "tests/test_publication_p2_artifacts.py",
        ]
    )
    assert result.tier == TIER_2
    assert result.runs_full_suite is False


def test_case_03_src_change_is_tier_3():
    result = classify(["src/trade_state.py"])
    assert result.tier == TIER_3
    assert result.runs_full_suite is True


def test_case_04_config_change_is_tier_3():
    result = classify(["config/strategy_v0.yaml"])
    assert result.tier == TIER_3


def test_case_05_m55_ledger_or_checkpoint_change_is_tier_3():
    for path in (
        "docs/research/MOMENTUM_PROSPECTIVE_MONITOR_M55_CHECKPOINTS.json",
        "docs/research/MOMENTUM_PROSPECTIVE_MONITOR_M55.md",
        "scripts/monitor_momentum_prospective.py",
        "src/momentum_prospective_monitor.py",
    ):
        assert classify([path]).tier == TIER_3, path


def test_case_06_docs_research_change_is_tier_3():
    result = classify(["docs/research/CROSS_SECTIONAL_MOMENTUM_FEASIBILITY_M52.md"])
    assert result.tier == TIER_3


def test_case_07_workflow_change_is_tier_3():
    result = classify([".github/workflows/tests.yml"])
    assert result.tier == TIER_3


def test_case_08_dependency_file_change_is_tier_3():
    for path in ("requirements.txt", "pyproject.toml", "pytest.ini", "conftest.py"):
        assert classify([path]).tier == TIER_3, path


def test_case_09_mixed_publication_and_runtime_change_is_tier_3():
    result = classify(
        [
            "docs/publication/p3a/README.md",
            "src/exit_engine.py",
        ]
    )
    assert result.tier == TIER_3
    assert any("src/exit_engine.py" in r for r in result.reasons)


def test_case_10_unknown_path_is_tier_3():
    for path in ("some/new/thing.py", "README.md", "site/index.html", "Justfile"):
        assert classify([path]).tier == TIER_3, path


@pytest.mark.parametrize(
    "bad",
    [
        [],
        None,
        [""],
        ["   "],
        ["../escape.md"],
        ["/etc/passwd"],
        ["docs/publication/../research/M52.md"],
        [None],
        [123],
        object(),
    ],
)
def test_case_11_empty_or_invalid_input_falls_back_to_full(bad):
    result = classify(bad)
    assert result.tier == TIER_3
    assert result.runs_full_suite is True
    assert result.reasons


def test_case_12_manual_full_suite_override_is_tier_3():
    result = classify(["docs/publication/p3a/README.md"], force_full=True)
    assert result.tier == TIER_3
    assert any("override" in r for r in result.reasons)


# --- safety properties ------------------------------------------------------


def test_traversal_normalizing_into_a_protected_path_is_tier_3():
    # docs/publication/../research/... normalizes to docs/research/... and
    # must not be able to sneak in as a publication path.
    result = classify(["docs/publication/../research/M52.md"])
    assert result.tier == TIER_3


def test_every_protected_prefix_forces_full_even_with_a_publication_extension():
    from scripts.ci.classify_changes import PROTECTED_PREFIXES

    for prefix in PROTECTED_PREFIXES:
        probe = prefix + ("x.md" if prefix.endswith("/") else "x.md")
        assert classify([probe]).tier == TIER_3, probe


def test_publication_path_with_executable_extension_is_not_tier_1():
    # A .py under docs/publication/ is not an approved Tier-1 artifact.
    result = classify(["docs/publication/p2/helper.py"])
    assert result.tier == TIER_3


def test_non_publication_test_file_is_tier_3():
    assert classify(["tests/test_trade_state.py"]).tier == TIER_3
    assert classify(["tests/test_ci_change_classifier.py"]).tier == TIER_3


def test_classifier_never_raises_on_hostile_input():
    class Hostile:
        def __iter__(self):
            raise RuntimeError("boom")

    result = classify(Hostile())
    assert result.tier == TIER_3
    assert result.runs_full_suite is True


def test_classification_is_deterministic_and_order_independent():
    a = classify(["docs/publication/b.md", "docs/publication/a.md"])
    b = classify(["docs/publication/a.md", "docs/publication/b.md"])
    assert a == b
    assert a.paths == ("docs/publication/a.md", "docs/publication/b.md")


def test_only_three_tiers_exist_and_full_is_the_default_shape():
    assert {TIER_1, TIER_2, TIER_3} == {
        "TIER_1_PUBLICATION_DOCS",
        "TIER_2_PUBLICATION_TOOLING",
        "TIER_3_FULL",
    }
    assert Classification(TIER_3).runs_full_suite is True
    assert Classification(TIER_1).runs_full_suite is False


# --- CLI contract used by the workflow --------------------------------------


def _cli(*args, stdin=""):
    return subprocess.run(
        [sys.executable, "-m", "scripts.ci.classify_changes", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        input=stdin,
    )


def test_cli_github_format_emits_tier_and_run_full():
    proc = _cli("docs/publication/p3a/README.md", "--format", "github")
    assert proc.returncode == 0
    assert "tier=TIER_1_PUBLICATION_DOCS" in proc.stdout
    assert "run_full=false" in proc.stdout


def test_cli_reads_stdin_and_reports_full_for_runtime_change():
    proc = _cli("--format", "github", stdin="src/trade_state.py\n")
    assert proc.returncode == 0
    assert "tier=TIER_3_FULL" in proc.stdout
    assert "run_full=true" in proc.stdout


def test_cli_empty_stdin_is_full_not_silently_skipped():
    proc = _cli("--format", "github", stdin="")
    assert proc.returncode == 0
    assert "tier=TIER_3_FULL" in proc.stdout
    assert "run_full=true" in proc.stdout


def test_cli_force_full_override():
    proc = _cli("docs/publication/p3a/README.md", "--force-full", "--format", "github")
    assert "tier=TIER_3_FULL" in proc.stdout


def test_cli_json_format_includes_reasons():
    proc = _cli("src/trade_state.py", "--format", "json")
    payload = json.loads(proc.stdout)
    assert payload["tier"] == TIER_3
    assert payload["run_full"] is True
    assert payload["reasons"]


def test_cli_exit_code_is_zero_so_workflow_reads_the_tier_not_an_error():
    # The workflow relies on a successful exit plus the emitted tier; a
    # non-zero exit would be handled as "unknown" and forced to full, but
    # the normal path must be a clean success.
    for args in (["src/x.py"], ["docs/publication/a.md"], []):
        proc = _cli(*args, "--format", "github", stdin="\n")
        assert proc.returncode == 0


# --- correction pass: rename/move safety ------------------------------------


def test_protected_path_renamed_into_publication_is_tier_3():
    # The GitHub files API reports both names for a rename. The workflow
    # sends both, so the old protected path is present and forces full.
    result = classify(["docs/publication/example.md", "src/example.py"])
    assert result.tier == TIER_3
    assert any("src/example.py" in r for r in result.reasons)


def test_docs_research_renamed_into_publication_is_tier_3():
    result = classify(
        ["docs/publication/moved.md", "docs/research/CROSS_SECTIONAL_MOMENTUM_FEASIBILITY_M52.md"]
    )
    assert result.tier == TIER_3


def test_m55_checkpoint_renamed_into_publication_is_tier_3():
    result = classify(
        [
            "docs/publication/p3a/notes.md",
            "docs/research/MOMENTUM_PROSPECTIVE_MONITOR_M55_CHECKPOINTS.json",
        ]
    )
    assert result.tier == TIER_3


def test_publication_markdown_renamed_within_publication_stays_tier_1():
    # Both old and new names are publication prose, so nothing escalates.
    result = classify(["docs/publication/p3a/new_name.md", "docs/publication/p3a/old_name.md"])
    assert result.tier == TIER_1


# --- correction pass: manifest-bounded Tier 2 -------------------------------


def test_tier_2_is_exactly_the_manifest():
    from scripts.ci.classify_changes import MANIFEST_PATHS

    for path in MANIFEST_PATHS:
        assert classify([path]).tier == TIER_2, path


def test_unlisted_publication_generator_is_tier_3():
    result = classify(["scripts/publication/generate_p3a_tables.py"])
    assert result.tier == TIER_3
    assert any("manifest" in r for r in result.reasons)


def test_unlisted_publication_test_is_tier_3():
    result = classify(["tests/test_publication_p3a_artifacts.py"])
    assert result.tier == TIER_3
    assert any("manifest" in r for r in result.reasons)


def test_generated_markdown_artifact_is_tier_2_not_tier_1():
    # Hand-editing the generated ledger must trigger the regeneration
    # determinism check, not pass as ordinary prose.
    result = classify(["docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.md"])
    assert result.tier == TIER_2


# --- correction pass: structured data ---------------------------------------


def test_approved_ledger_json_is_tier_2():
    assert classify(["docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.json"]).tier == TIER_2


def test_unknown_publication_json_is_tier_3():
    result = classify(["docs/publication/p3a/new_dataset.json"])
    assert result.tier == TIER_3


@pytest.mark.parametrize(
    "path",
    [
        "docs/publication/p2/table.csv",
        "docs/publication/p2/config.yaml",
        "docs/publication/p2/config.yml",
        "docs/publication/p2/data.parquet",
        "docs/publication/p2/notes.txt",
    ],
)
def test_publication_structured_or_unknown_data_is_tier_3(path):
    assert classify([path]).tier == TIER_3


def test_publication_markdown_remains_tier_1():
    assert classify(["docs/publication/p3a/README.md"]).tier == TIER_1


def test_manifest_entries_declare_their_own_verification():
    from scripts.ci.classify_changes import PUBLICATION_TOOLING_MANIFEST

    assert PUBLICATION_TOOLING_MANIFEST, "manifest must not be empty"
    for entry in PUBLICATION_TOOLING_MANIFEST:
        assert entry.tests, f"{entry.path} declares no focused tests"
        for test_path in entry.tests:
            assert (ROOT / test_path).exists(), f"{entry.path} names a missing test {test_path}"
        assert (ROOT / entry.path).exists(), f"manifest lists a missing file: {entry.path}"


def test_checks_for_returns_covering_manifest_entries():
    from scripts.ci.classify_changes import checks_for

    covered = checks_for(["scripts/publication/generate_p2_hypothesis_ledger.py"])
    assert len(covered) == 1
    assert covered[0].regeneration is not None
    assert checks_for(["docs/publication/p3a/README.md"]) == ()
