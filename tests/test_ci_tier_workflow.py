"""Tests pinning the change-aware tier wiring in the CI workflow (Issue #370).

These guard the properties that make the tiering safe: the full tier is
the default for anything that is not explicitly a lighter tier, the
classifier cannot silently downgrade a run, and the cost controls the
issue asked for are actually declared.
"""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
PARSED = yaml.safe_load(WORKFLOW)
STEPS = PARSED["jobs"]["test"]["steps"]


def _step(name: str) -> dict:
    for step in STEPS:
        if step.get("name") == name:
            return step
    raise AssertionError(f"workflow step not found: {name}")


def test_classifier_step_exists_and_is_addressable():
    step = _step("Classify change tier")
    assert step["id"] == "classify"


def test_full_suite_steps_default_to_running_when_tier_is_absent():
    # The guard is `!= 'false'`, so an unset/empty output (classifier step
    # crashed, output never written) still runs the full suite. A guard of
    # `== 'true'` would silently skip everything instead.
    for name in ("Run tests", "Run tracked operator verification"):
        condition = _step(name)["if"]
        assert condition == "steps.classify.outputs.run_full != 'false'", name
        assert "== 'true'" not in condition, name


def test_full_suite_runs_the_complete_pytest_suite():
    assert _step("Run tests")["run"].strip() == "python -m pytest -v"


def test_lightweight_tiers_verify_safety_state():
    for name in ("Verify strategy remains disabled", "Verify protected and M55 files are untouched"):
        assert _step(name)["if"] == "steps.classify.outputs.run_full == 'false'", name


def test_lightweight_tiers_fail_closed_on_protected_or_m55_paths():
    body = _step("Verify protected and M55 files are untouched")["run"]
    assert "src/|config/|data/|docs/research/|" in body
    assert "MOMENTUM_PROSPECTIVE_MONITOR_M55" in body
    assert "exit 1" in body


def test_tier_2_runs_publication_tests_and_regeneration_check():
    assert _step("Run publication-focused tests")["if"] == (
        "steps.classify.outputs.tier == 'TIER_2_PUBLICATION_TOOLING'"
    )
    regen = _step("Verify deterministic publication-table regeneration")["run"]
    assert regen.count("generate_p2_hypothesis_ledger") == 2
    assert "git diff --exit-code" in regen


def test_whitespace_check_runs_on_every_tier():
    assert "if" not in _step("Check whitespace")


def test_cost_controls_are_declared():
    triggers = PARSED[True]
    assert "schedule" in triggers, "weekly full-suite verification on main is required"
    assert triggers["schedule"][0]["cron"].endswith("* * 1"), "cadence should be weekly"
    assert PARSED["concurrency"]["cancel-in-progress"] is True
    assert "cache: pip" in WORKFLOW
    assert triggers["workflow_dispatch"]["inputs"]["full_suite"]["default"] is True


def test_single_job_so_lighter_tiers_do_not_spin_extra_runners():
    assert list(PARSED["jobs"]) == ["test"]


def test_permissions_stay_least_privilege():
    assert PARSED["permissions"] == {"contents": "read", "pull-requests": "read"}


def test_classifier_step_emits_full_on_every_failure_mode():
    body = _step("Classify change tier")["run"]
    # Non-pull-request events, override, missing PR number, API failure,
    # empty file list, classifier failure, unparseable output, unknown tier.
    assert body.count("emit_full") >= 9
    assert "is not a pull request" in body
    assert "refusing to skip verification" in body
    assert "unrecognized tier" in body


def test_step_summary_reports_the_selected_tier():
    body = _step("Classify change tier")["run"]
    assert "GITHUB_STEP_SUMMARY" in body
    assert "CI tier:" in body
