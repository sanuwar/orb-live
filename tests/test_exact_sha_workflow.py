from pathlib import Path

import yaml


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")


def test_exact_sha_dispatch_is_declared_and_pinned() -> None:
    assert "workflow_dispatch:" in WORKFLOW
    assert "commit_sha:" in WORKFLOW
    assert "required: true" in WORKFLOW
    assert "REQUESTED_SHA: ${{ inputs.commit_sha }}" in WORKFLOW
    assert "^[0-9a-fA-F]{40}$" in WORKFLOW
    assert "ref: ${{ inputs.commit_sha }}" in WORKFLOW
    assert "uses: actions/checkout@v7" in WORKFLOW
    assert "persist-credentials: true" in WORKFLOW
    assert 'git fetch --no-tags --depth=1 origin "$requested_sha_lower"' in WORKFLOW
    assert 'git checkout --detach "$requested_sha_lower"' in WORKFLOW
    assert 'actual_sha="$(git rev-parse HEAD)"' in WORKFLOW
    assert 'test "$actual_sha" = "$requested_sha_lower"' in WORKFLOW
    assert "github.ref" not in WORKFLOW


def test_exact_sha_workflow_keeps_minimal_permissions_and_required_verifiers() -> None:
    assert "permissions:\n  contents: read" in WORKFLOW
    assert "python scripts/verify_operator_routes.py" in WORKFLOW
    assert "python scripts/verify_operator_milestone.py" in WORKFLOW
    assert "python -m pytest -q tests/test_architecture_audit.py" in WORKFLOW


def test_workflow_yaml_is_well_formed() -> None:
    parsed = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert "concurrency" in parsed
    assert "jobs" in parsed and "test" in parsed["jobs"]


def test_concurrency_cancels_superseded_runs_without_using_ref() -> None:
    concurrency = yaml.safe_load(WORKFLOW)["concurrency"]
    assert concurrency["group"] == "${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}"
    assert concurrency["cancel-in-progress"] is True
    # The exact-SHA dispatch path's own invariant (never reference the ref
    # context expression anywhere in this file) must still hold with the
    # concurrency block present.
    assert "github.ref" not in WORKFLOW


def test_push_trigger_still_scoped_to_main_only() -> None:
    triggers = yaml.safe_load(WORKFLOW)[True]  # bare `on:` parses as the YAML 1.1 boolean key True
    assert triggers["push"]["branches"] == ["main"]
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers
