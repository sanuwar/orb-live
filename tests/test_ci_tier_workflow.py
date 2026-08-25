"""Tests pinning the change-aware tier wiring in the CI workflow (Issue #370).

These guard the properties that make the tiering safe: the full tier is
the default for anything that is not explicitly a lighter tier, the
classifier cannot silently downgrade a run, and the cost controls the
issue asked for are actually declared.
"""
from __future__ import annotations

import re
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
    assert "if" not in _step("Check whitespace on the committed base-to-head diff")


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


# --- correction pass: rename/move safety ------------------------------------


def test_changed_file_retrieval_includes_previous_filename():
    # A rename reports both names; sending only `filename` would let a
    # protected path move into docs/publication and be classified light.
    for name in ("Classify change tier", "Verify protected and M55 files are untouched"):
        body = _step(name)["run"]
        assert ".previous_filename" in body, name
        assert ".[].filename'" not in body, f"{name} still sends only the new filename"


def test_changed_file_retrieval_deduplicates():
    for name in ("Classify change tier", "Verify protected and M55 files are untouched"):
        assert "sort -u" in _step(name)["run"], name


def test_both_retrieval_sites_use_the_same_jq_projection():
    projection = ".[] | .filename, (.previous_filename // empty)"
    for name in ("Classify change tier", "Verify protected and M55 files are untouched"):
        assert projection in _step(name)["run"], name


# --- correction pass: real whitespace check ---------------------------------


def test_whitespace_check_compares_explicit_base_and_head_shas():
    step = _step("Check whitespace on the committed base-to-head diff")
    body = step["run"]
    env = step["env"]
    assert env["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert 'git diff --check "$BASE_SHA" "$HEAD_SHA"' in body
    # The bare working-tree form would pass trivially on a clean checkout.
    assert "\ngit diff --check\n" not in body
    assert body.strip() != "git diff --check"


def test_whitespace_check_validates_sha_shape_and_fails_closed():
    body = _step("Check whitespace on the committed base-to-head diff")["run"]
    assert "^[0-9a-f]{40}$" in body
    assert "exit 1" in body


def test_whitespace_check_handles_non_pull_request_events_explicitly():
    body = _step("Check whitespace on the committed base-to-head diff")["run"]
    assert "has no pull-request base-to-head diff" in body
    assert "HEAD^" in body


def test_whitespace_check_never_uses_the_ref_context():
    assert "github.ref" not in WORKFLOW


# --- correction pass: manifest / workflow coverage must not drift -----------


def test_every_manifest_entry_has_its_checks_wired_into_the_workflow():
    """The manifest is the Tier-2 allowlist; the workflow must actually run
    what each entry declares. Adding an entry without adding its check
    would silently give a new file lighter verification than it needs."""
    from scripts.ci.classify_changes import PUBLICATION_TOOLING_MANIFEST

    tier2_bodies = "\n".join(
        step.get("run", "")
        for step in STEPS
        if step.get("if") == "steps.classify.outputs.tier == 'TIER_2_PUBLICATION_TOOLING'"
    )
    assert tier2_bodies.strip(), "no Tier-2 steps found in the workflow"

    for entry in PUBLICATION_TOOLING_MANIFEST:
        for test_path in entry.tests:
            assert test_path in tier2_bodies, (
                f"manifest entry {entry.path} declares tests {test_path}, "
                "but no Tier-2 workflow step runs them"
            )
        if entry.regeneration:
            assert entry.regeneration in tier2_bodies, (
                f"manifest entry {entry.path} declares regeneration "
                f"{entry.regeneration!r}, but no Tier-2 workflow step runs it"
            )


def test_tier_2_steps_do_not_run_checks_for_unlisted_tooling():
    """Guard the converse: a Tier-2 step must not reference a publication
    test module that the manifest does not claim, which would mean the
    workflow verifies something the classifier never routes to it."""
    from scripts.ci.classify_changes import PUBLICATION_TOOLING_MANIFEST

    declared = {t for entry in PUBLICATION_TOOLING_MANIFEST for t in entry.tests}
    tier2_bodies = "\n".join(
        step.get("run", "")
        for step in STEPS
        if step.get("if") == "steps.classify.outputs.tier == 'TIER_2_PUBLICATION_TOOLING'"
    )
    referenced = set(re.findall(r"tests/test_publication_[A-Za-z0-9_]+\.py", tier2_bodies))
    assert referenced <= declared, f"workflow runs undeclared publication tests: {referenced - declared}"
