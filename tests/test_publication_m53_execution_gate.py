"""Deterministic tests for the Phase 2B-2G M53 execution gate (Issue #384).

Every fixture here is synthetic: tiny files written into ``tmp_path`` whose
hashes are computed from the bytes actually written.  No test materializes the
historical M53 checkout, touches a real cache tree, reads the preserved
Phase 2B-1 scratch evidence, imports M52 or M53, starts a process, or opens a
socket.  The only real repository artifacts read are the committed gate manifest
and the gate module's own source text.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.publication import m53_execution_gate as gate

BACKSLASH = chr(92)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_MANIFEST_PATH = (
    REPOSITORY_ROOT / "docs" / "publication" / "p3b" / "phase2b2g" / "M53_EXECUTION_GATE_MANIFEST.json"
)


# ---------------------------------------------------------------------------
# Synthetic fixture construction
# ---------------------------------------------------------------------------


def _write(path: Path, data: bytes) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def synthetic(tmp_path: Path) -> dict[str, object]:
    """A fully valid synthetic manifest plus the fixture tree it describes."""
    checkout = tmp_path / "checkout"
    guard_dir = tmp_path / "guard"
    scratch = tmp_path / "scratch"
    evidence = tmp_path / "evidence"
    repository = tmp_path / "repository"
    for directory in (checkout, guard_dir, scratch, evidence, repository):
        directory.mkdir(parents=True)

    # Two source rows: one LF-materialized, one CRLF-materialized.  The CRLF row
    # is the LF bytes with expanded line endings, exactly as the reviewed
    # closure describes.
    lf_bytes = b"alpha\nbeta\n"
    crlf_source_lf = b"gamma\ndelta\n"
    crlf_bytes = crlf_source_lf.replace(b"\n", b"\r\n")
    lf_digest, lf_size = _write(checkout / "src" / "lf_module.py", lf_bytes)
    crlf_digest, crlf_size = _write(checkout / "src" / "crlf_module.py", crlf_bytes)

    source_files = [
        {
            "path": "src/lf_module.py",
            "git_blob_sha1": "0" * 40,
            "git_object_sha256": lf_digest,
            "git_object_bytes": lf_size,
            "materialization": "lf",
            "expected_sha256": lf_digest,
            "expected_bytes": lf_size,
        },
        {
            "path": "src/crlf_module.py",
            "git_blob_sha1": "1" * 40,
            "git_object_sha256": _sha256(crlf_source_lf),
            "git_object_bytes": len(crlf_source_lf),
            "materialization": "crlf",
            "expected_sha256": crlf_digest,
            "expected_bytes": crlf_size,
        },
    ]

    handoff_bytes = b'{"milestone": "M52"}'
    handoff_digest, handoff_size = _write(
        checkout / "artifacts" / "input" / "m52_result.json", handoff_bytes
    )

    guard_core_digest, guard_core_size = _write(guard_dir / "guard_core.py", b"guard core\n")
    shim_digest, shim_size = _write(guard_dir / "sitecustomize.py", b"shim\n")
    guard_files = [
        {
            "path": "scripts/publication/m53_network_guard/guard_core.py",
            "git_object_sha256": guard_core_digest,
            "git_object_bytes": guard_core_size,
            "materialization": "lf",
            "expected_sha256": guard_core_digest,
            "expected_bytes": guard_core_size,
        },
        {
            "path": "scripts/publication/m53_network_guard/sitecustomize.py",
            "git_object_sha256": shim_digest,
            "git_object_bytes": shim_size,
            "materialization": "lf",
            "expected_sha256": shim_digest,
            "expected_bytes": shim_size,
        },
    ]

    trees = []
    for role in ("daily_bar", "openfigi", "sec_nport"):
        subtree = scratch / role
        _write(subtree / "a.bin", role.encode("utf-8"))
        _write(subtree / "nested" / "b.bin", role.upper().encode("utf-8"))
        files, total_bytes, aggregate = gate.measure_tree(subtree)
        trees.append(
            {
                "role": role,
                "repo_relative_root": f"artifacts/original/{role}",
                "files": files,
                "bytes": total_bytes,
                "aggregate_sha256": aggregate,
            }
        )

    python_executable = tmp_path / "python.bin"
    git_executable = tmp_path / "git.bin"
    python_executable.write_bytes(b"python\n")
    git_executable.write_bytes(b"git\n")

    manifest = {
        "schema_version": gate.SCHEMA_VERSION,
        "profile": gate.PROFILE_SYNTHETIC,
        "historical_commit": "4" * 40,
        "source_closure": {"file_count": len(source_files), "files": source_files},
        "input_artifact": {
            "relative_path": "artifacts/input/m52_result.json",
            "sha256": handoff_digest,
            "bytes": handoff_size,
            "classification": list(gate.FROZEN_REFERENCE["input_artifact_classification"]),
            "regenerated_artifact_eligible": False,
        },
        "parameters": {"record_count": 49, "record_bytes": 5018, "digest_sha256": "a" * 64},
        "output_criterion": {
            "complete_sha256": "b" * 64,
            "complete_bytes": 28715,
            "canonical_exclusions": ["/generated_at"],
            "canonical_payload_sha256": "c" * 64,
            "canonical_payload_bytes": 22055,
            "raw_and_canonical_reported_separately": True,
        },
        "guard": {
            "scope": gate.FROZEN_REFERENCE["guard_scope"],
            "files": guard_files,
            "token": gate.FROZEN_REFERENCE["guard_token"],
            "required_environment": list(gate.REQUIRED_GUARD_ENVIRONMENT),
            "forbidden_environment": list(gate.FORBIDDEN_GUARD_ENVIRONMENT),
            "cleared_environment": list(gate.REQUIRED_CLEARED_ENVIRONMENT),
            "external_pre_launch_verification_required": True,
            "ready_event": "guard_ready",
            "terminal_event": "normal_interpreter_exit",
            "post_run_verifier": "verify_guard_run",
        },
        "executables": {"python_content_pin_required": True, "git_content_pin_required": True},
        "caches": {
            "trees": trees,
            "original_exposure_forbidden": True,
            "fetch_missing": False,
            "aggregate_algorithm": "synthetic",
        },
        "isolation": {
            "scratch_root_required": True,
            "preserved_scratch_evidence_untouched": True,
            "original_cache_roots": [tree["repo_relative_root"] for tree in trees],
        },
        "execution_command": {"module": "scripts.research_momentum_risk_control", "argv_after_module": []},
        "output_paths": {
            "artifact_relative_path": "artifacts/output/m53_result.json",
            "evidence_log_basename": "m53-network-guard.jsonl",
        },
    }

    environment = gate.GateEnvironment(
        checkout_root=checkout,
        guard_dir=guard_dir,
        scratch_cache_root=scratch,
        evidence_dir=evidence,
        python_executable=python_executable,
        git_executable=git_executable,
        repository_root=repository,
        environment_names=frozenset({"PATH"}),
    )
    return {
        "manifest": manifest,
        "environment": environment,
        "tmp_path": tmp_path,
        "checkout": checkout,
        "guard_dir": guard_dir,
        "scratch": scratch,
        "evidence": evidence,
        "repository": repository,
    }


def _evaluate(synthetic: dict[str, object], **overrides) -> dict:
    environment = overrides.pop("environment", synthetic["environment"])
    manifest = overrides.pop("manifest", synthetic["manifest"])
    assert not overrides
    return gate.evaluate(manifest, environment)


def _mutate(synthetic: dict[str, object]) -> dict:
    return deepcopy(synthetic["manifest"])


# ---------------------------------------------------------------------------
# The one passing configuration
# ---------------------------------------------------------------------------


def test_fully_valid_manifest_is_ready_but_never_authorized(synthetic):
    record = _evaluate(synthetic)
    assert record["findings"] == []
    assert record["decision"] == gate.DECISION_READY
    assert record["execution_authorized"] is False
    assert record["m53_executed"] is False
    assert record["mode"] == "verification_only"


def test_ready_record_still_reports_the_synthetic_profile(synthetic):
    record = _evaluate(synthetic)
    assert record["manifest_profile"] == gate.PROFILE_SYNTHETIC


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_unknown_schema_version_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["schema_version"] = "orb-live/m53-execution-gate/2"
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert record["manifest_schema_valid"] is False


def test_unknown_profile_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["profile"] = "OPERATOR_OVERRIDE"
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_EVIDENCE


@pytest.mark.parametrize(
    "section",
    ["source_closure", "input_artifact", "parameters", "output_criterion", "guard", "caches", "isolation"],
)
def test_missing_top_level_section_fails(synthetic, section):
    manifest = _mutate(synthetic)
    del manifest[section]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert record["manifest_schema_valid"] is False


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("parameters", "digest_sha256"),
        ("output_criterion", "canonical_payload_sha256"),
        ("guard", "token"),
        ("caches", "fetch_missing"),
        ("isolation", "scratch_root_required"),
        ("execution_command", "module"),
        ("output_paths", "evidence_log_basename"),
    ],
)
def test_missing_nested_field_fails(synthetic, section, field):
    manifest = _mutate(synthetic)
    del manifest[section][field]
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_EVIDENCE


def test_unknown_field_is_rejected(synthetic):
    manifest = _mutate(synthetic)
    manifest["parameters"]["skip_verification"] = True
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert any("unknown field" in finding["detail"] for finding in record["findings"])


def test_duplicate_json_keys_are_rejected(synthetic):
    text = '{"schema_version": "a", "schema_version": "b"}'
    with pytest.raises(gate.ManifestError):
        gate.load_manifest_text(text)


def test_duplicate_source_rows_are_rejected(synthetic):
    manifest = _mutate(synthetic)
    manifest["source_closure"]["files"].append(deepcopy(manifest["source_closure"]["files"][0]))
    manifest["source_closure"]["file_count"] = len(manifest["source_closure"]["files"])
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert any(finding["detail"].startswith("source_closure.files: duplicate") for finding in record["findings"])


def test_duplicate_cache_roles_are_rejected(synthetic):
    manifest = _mutate(synthetic)
    manifest["caches"]["trees"].append(deepcopy(manifest["caches"]["trees"][0]))
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_EVIDENCE


def test_non_finite_numbers_are_rejected():
    with pytest.raises(gate.ManifestError):
        gate.load_manifest_text('{"parameters": {"record_bytes": NaN}}')


def test_boolean_is_not_accepted_where_an_integer_is_required(synthetic):
    manifest = _mutate(synthetic)
    manifest["parameters"]["record_count"] = True
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_EVIDENCE


def test_empty_row_list_is_rejected(synthetic):
    manifest = _mutate(synthetic)
    manifest["guard"]["files"] = []
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_EVIDENCE


# ---------------------------------------------------------------------------
# Source closure and materialization
# ---------------------------------------------------------------------------


def test_source_hash_mismatch_fails_closed(synthetic):
    manifest = _mutate(synthetic)
    manifest["source_closure"]["files"][0]["expected_sha256"] = "d" * 64
    manifest["source_closure"]["files"][0]["git_object_sha256"] = "d" * 64
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_SOURCE
    assert any(finding["code"] == "source_file_hash_mismatch" for finding in record["findings"])


def test_source_size_mismatch_fails_closed(synthetic):
    manifest = _mutate(synthetic)
    row = manifest["source_closure"]["files"][0]
    row["expected_bytes"] += 1
    row["git_object_bytes"] += 1
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_SOURCE


def test_declared_count_that_disagrees_with_the_rows_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["source_closure"]["file_count"] = 30
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_SOURCE
    assert any(finding["code"] == "closure_count_mismatch" for finding in record["findings"])


def test_lf_row_claiming_a_different_materialized_hash_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["source_closure"]["files"][0]["expected_sha256"] = "e" * 64
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_SOURCE
    assert any(finding["code"] == "materialization_inconsistent" for finding in record["findings"])


def test_crlf_row_that_does_not_grow_fails(synthetic):
    manifest = _mutate(synthetic)
    row = manifest["source_closure"]["files"][1]
    row["git_object_bytes"] = row["expected_bytes"]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_SOURCE
    assert any(finding["code"] == "materialization_inconsistent" for finding in record["findings"])


def test_wrong_eol_materialization_on_disk_fails(synthetic):
    # Materialize the CRLF-required file as LF instead.
    (synthetic["checkout"] / "src" / "crlf_module.py").write_bytes(b"gamma\ndelta\n")
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_SOURCE
    assert any(finding["code"] == "source_file_hash_mismatch" for finding in record["findings"])


def test_absent_source_file_fails(synthetic):
    (synthetic["checkout"] / "src" / "lf_module.py").unlink()
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_SOURCE
    assert any(finding["code"] == "source_file_absent" for finding in record["findings"])


def test_no_checkout_supplied_blocks_on_materialization(synthetic):
    record = _evaluate(synthetic, environment=gate.GateEnvironment())
    assert record["decision"] == gate.DECISION_SOURCE


# ---------------------------------------------------------------------------
# M52 handoff
# ---------------------------------------------------------------------------


def test_wrong_m52_artifact_fails(synthetic):
    (synthetic["checkout"] / "artifacts" / "input" / "m52_result.json").write_bytes(b'{"milestone": "M52b"}')
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_INPUT
    assert any(finding["code"] == "handoff_hash_mismatch" for finding in record["findings"])


def test_declaring_the_regenerated_m52_eligible_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["input_artifact"]["regenerated_artifact_eligible"] = True
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_INPUT
    assert any(finding["code"] == "regenerated_m52_declared_eligible" for finding in record["findings"])


def test_softening_the_m52_classification_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["input_artifact"]["classification"] = ["RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH"]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_INPUT
    assert any(finding["code"] == "m52_classification_altered" for finding in record["findings"])


def test_absent_m52_handoff_fails(synthetic):
    (synthetic["checkout"] / "artifacts" / "input" / "m52_result.json").unlink()
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_INPUT


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def test_malformed_parameter_digest_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["parameters"]["digest_sha256"] = "not-a-digest"
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_PARAMETERS


def test_wrong_parameter_digest_fails_against_the_frozen_reference():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["parameters"]["digest_sha256"] = "f" * 64
    findings = gate.reconcile_with_frozen_reference(manifest)
    assert any(finding.code == "parameter_digest_mismatch" for finding in findings)


def test_wrong_parameter_record_count_fails_against_the_frozen_reference():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["parameters"]["record_count"] = 48
    findings = gate.reconcile_with_frozen_reference(manifest)
    assert any(finding.code == "parameter_count_changed" for finding in findings)


# ---------------------------------------------------------------------------
# Caches and isolation
# ---------------------------------------------------------------------------


def test_cache_file_count_mismatch_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["caches"]["trees"][0]["files"] += 1
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "scratch_tree_digest_mismatch" for finding in record["findings"])


def test_cache_byte_total_mismatch_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["caches"]["trees"][1]["bytes"] += 1
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_CACHE


def test_cache_aggregate_digest_mismatch_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["caches"]["trees"][2]["aggregate_sha256"] = "0" * 64
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_CACHE


def test_mutating_a_scratch_cache_tree_is_detected(synthetic):
    (synthetic["scratch"] / "daily_bar" / "a.bin").write_bytes(b"mutated")
    assert _evaluate(synthetic)["decision"] == gate.DECISION_CACHE


def test_enabling_fetch_missing_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["caches"]["fetch_missing"] = True
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "fetch_missing_enabled" for finding in record["findings"])


def test_permitting_original_cache_exposure_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["caches"]["original_exposure_forbidden"] = False
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "original_exposure_permitted" for finding in record["findings"])


def test_unprotected_preserved_scratch_evidence_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["isolation"]["preserved_scratch_evidence_untouched"] = False
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "preserved_evidence_not_protected" for finding in record["findings"])


def test_scratch_root_inside_the_repository_fails(synthetic):
    repository = synthetic["repository"]
    inside = repository / "scratch"
    inside.mkdir()
    for role in ("daily_bar", "openfigi", "sec_nport"):
        (inside / role).mkdir()
    environment = gate.GateEnvironment(
        checkout_root=synthetic["checkout"],
        guard_dir=synthetic["guard_dir"],
        scratch_cache_root=inside,
        evidence_dir=synthetic["evidence"],
        python_executable=synthetic["tmp_path"] / "python.bin",
        git_executable=synthetic["tmp_path"] / "git.bin",
        repository_root=repository,
    )
    record = _evaluate(synthetic, environment=environment)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "scratch_root_inside_repository" for finding in record["findings"])


def test_original_cache_root_overlap_fails(synthetic):
    """A declared original cache root that resolves into the scratch tree fails.

    The overlap is expressed with a legal relative path, because a traversal
    path is now refused by the schema before this check is ever reached.
    """
    manifest = _mutate(synthetic)
    manifest["isolation"]["original_cache_roots"] = ["scratch/daily_bar"]
    environment = gate.GateEnvironment(
        checkout_root=synthetic["checkout"],
        guard_dir=synthetic["guard_dir"],
        scratch_cache_root=synthetic["scratch"],
        evidence_dir=synthetic["evidence"],
        python_executable=synthetic["tmp_path"] / "python.bin",
        git_executable=synthetic["tmp_path"] / "git.bin",
        repository_root=synthetic["tmp_path"],
    )
    record = _evaluate(synthetic, manifest=manifest, environment=environment)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "original_cache_root_overlap" for finding in record["findings"])


def test_declaring_no_original_cache_roots_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["isolation"]["original_cache_roots"] = []
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "original_cache_roots_undeclared" for finding in record["findings"])


def test_checkout_inside_the_repository_fails(synthetic):
    repository = synthetic["repository"]
    record = _evaluate(
        synthetic,
        environment=gate.GateEnvironment(
            checkout_root=synthetic["checkout"],
            guard_dir=synthetic["guard_dir"],
            scratch_cache_root=synthetic["scratch"],
            evidence_dir=synthetic["evidence"],
            python_executable=synthetic["tmp_path"] / "python.bin",
            git_executable=synthetic["tmp_path"] / "git.bin",
            repository_root=synthetic["tmp_path"],
        ),
    )
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "checkout_inside_repository" for finding in record["findings"])
    assert repository.exists()


def test_absent_scratch_tree_fails(synthetic):
    for child in (synthetic["scratch"] / "openfigi").rglob("*"):
        if child.is_file():
            child.unlink()
    for child in sorted((synthetic["scratch"] / "openfigi").rglob("*"), reverse=True):
        child.rmdir()
    (synthetic["scratch"] / "openfigi").rmdir()
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "scratch_tree_absent" for finding in record["findings"])


# ---------------------------------------------------------------------------
# Guard and executables
# ---------------------------------------------------------------------------


def test_guard_file_hash_mismatch_fails(synthetic):
    (synthetic["guard_dir"] / "guard_core.py").write_bytes(b"tampered\n")
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "guard_file_hash_mismatch" for finding in record["findings"])


def test_sitecustomize_hash_mismatch_fails(synthetic):
    (synthetic["guard_dir"] / "sitecustomize.py").write_bytes(b"tampered shim\n")
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "guard_file_hash_mismatch" for finding in record["findings"])


def test_absent_guard_file_fails(synthetic):
    (synthetic["guard_dir"] / "sitecustomize.py").unlink()
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "guard_file_absent" for finding in record["findings"])


def test_guard_directory_inside_the_checkout_fails(synthetic):
    nested = synthetic["checkout"] / "guard"
    nested.mkdir()
    for name in ("guard_core.py", "sitecustomize.py"):
        (nested / name).write_bytes((synthetic["guard_dir"] / name).read_bytes())
    environment = gate.GateEnvironment(
        checkout_root=synthetic["checkout"],
        guard_dir=nested,
        scratch_cache_root=synthetic["scratch"],
        evidence_dir=synthetic["evidence"],
        python_executable=synthetic["tmp_path"] / "python.bin",
        git_executable=synthetic["tmp_path"] / "git.bin",
        repository_root=synthetic["repository"],
    )
    record = _evaluate(synthetic, environment=environment)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "guard_dir_overlaps_checkout" for finding in record["findings"])


def test_missing_python_content_pin_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["executables"]["python_content_pin_required"] = False
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "python_pin_not_required" for finding in record["findings"])


def test_missing_git_content_pin_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["executables"]["git_content_pin_required"] = False
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "git_pin_not_required" for finding in record["findings"])


def test_unsupplied_executables_fail(synthetic):
    environment = gate.GateEnvironment(
        checkout_root=synthetic["checkout"],
        guard_dir=synthetic["guard_dir"],
        scratch_cache_root=synthetic["scratch"],
        evidence_dir=synthetic["evidence"],
        repository_root=synthetic["repository"],
    )
    record = _evaluate(synthetic, environment=environment)
    assert record["decision"] == gate.DECISION_GUARD
    assert sum(finding["code"] == "executable_not_pinned" for finding in record["findings"]) == 2


def test_absent_executable_file_fails(synthetic):
    (synthetic["tmp_path"] / "git.bin").unlink()
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "executable_absent" for finding in record["findings"])


def test_missing_guard_ready_requirement_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["guard"]["ready_event"] = "activation"
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "ready_event_wrong" for finding in record["findings"])


def test_missing_terminal_record_requirement_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["guard"]["terminal_event"] = "activation"
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "terminal_event_wrong" for finding in record["findings"])


def test_missing_post_run_verifier_requirement_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["guard"]["post_run_verifier"] = "verify_evidence_log"
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "post_run_verifier_wrong" for finding in record["findings"])


def test_dropping_the_external_pre_launch_check_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["guard"]["external_pre_launch_verification_required"] = False
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "external_verification_not_required" for finding in record["findings"])


def test_calling_the_guard_a_sandbox_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["guard"]["scope"] = "OPERATING_SYSTEM_SANDBOX"
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "guard_scope_misdescribed" for finding in record["findings"])


@pytest.mark.parametrize("name", ["ALPACA_API_KEY_ID", "https_proxy", "APCA_API_SECRET_KEY", "NO_PROXY"])
def test_omitting_any_credential_or_proxy_name_fails(synthetic, name):
    manifest = _mutate(synthetic)
    manifest["guard"]["cleared_environment"] = [
        value for value in manifest["guard"]["cleared_environment"] if value != name
    ]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(
        finding["code"] == "credential_or_proxy_not_cleared" and finding["detail"] == name
        for finding in record["findings"]
    )


@pytest.mark.parametrize("name", list(gate.REQUIRED_GUARD_ENVIRONMENT))
def test_omitting_any_required_guard_variable_fails(synthetic, name):
    manifest = _mutate(synthetic)
    manifest["guard"]["required_environment"] = [
        value for value in manifest["guard"]["required_environment"] if value != name
    ]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(
        finding["code"] == "guard_environment_missing" and finding["detail"] == name
        for finding in record["findings"]
    )


def test_a_present_guard_bypass_variable_fails(synthetic):
    environment = gate.GateEnvironment(
        checkout_root=synthetic["checkout"],
        guard_dir=synthetic["guard_dir"],
        scratch_cache_root=synthetic["scratch"],
        evidence_dir=synthetic["evidence"],
        python_executable=synthetic["tmp_path"] / "python.bin",
        git_executable=synthetic["tmp_path"] / "git.bin",
        repository_root=synthetic["repository"],
        environment_names=frozenset({"ORB_M53_GUARD_DISABLE"}),
    )
    record = _evaluate(synthetic, environment=environment)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "guard_bypass_variable_present" for finding in record["findings"])


def test_not_forbidding_the_bypass_variable_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["guard"]["forbidden_environment"] = []
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_GUARD
    assert any(finding["code"] == "guard_bypass_not_forbidden" for finding in record["findings"])


# ---------------------------------------------------------------------------
# Output criterion
# ---------------------------------------------------------------------------


def test_merging_raw_and_canonical_classifications_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["output_criterion"]["raw_and_canonical_reported_separately"] = False
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_OUTPUT
    assert any(finding["code"] == "classifications_merged" for finding in record["findings"])


def test_adding_an_exclusion_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["output_criterion"]["canonical_exclusions"] = ["/generated_at", "/m53_decision"]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_OUTPUT
    assert any(finding["code"] == "exclusion_set_altered" for finding in record["findings"])


def test_removing_the_only_exclusion_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["output_criterion"]["canonical_exclusions"] = []
    assert _evaluate(synthetic, manifest=manifest)["decision"] == gate.DECISION_OUTPUT


def test_identical_raw_and_canonical_digests_fail(synthetic):
    manifest = _mutate(synthetic)
    manifest["output_criterion"]["canonical_payload_sha256"] = manifest["output_criterion"]["complete_sha256"]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_OUTPUT
    assert any(finding["code"] == "criterion_digests_identical" for finding in record["findings"])


def test_canonical_payload_not_smaller_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["output_criterion"]["canonical_payload_bytes"] = manifest["output_criterion"]["complete_bytes"]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_OUTPUT
    assert any(finding["code"] == "canonical_payload_not_smaller" for finding in record["findings"])


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("complete_sha256", "complete_digest_mismatch"),
        ("canonical_payload_sha256", "canonical_digest_mismatch"),
    ],
)
def test_changing_a_frozen_output_digest_fails_reconciliation(field, code):
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["output_criterion"][field] = "9" * 64
    findings = gate.reconcile_with_frozen_reference(manifest)
    assert any(finding.code == code for finding in findings)


# ---------------------------------------------------------------------------
# Evidence and output paths
# ---------------------------------------------------------------------------


def test_pre_existing_evidence_log_fails(synthetic):
    (synthetic["evidence"] / "m53-network-guard.jsonl").write_text("stale\n", encoding="utf-8")
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert any(finding["code"] == "evidence_log_pre_exists" for finding in record["findings"])


def test_pre_existing_output_artifact_fails(synthetic):
    target = synthetic["checkout"] / "artifacts" / "output" / "m53_result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert any(finding["code"] == "output_artifact_pre_exists" for finding in record["findings"])


def test_extra_command_argument_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["execution_command"]["argv_after_module"] = ["--fetch-missing"]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert any(finding["code"] == "unexpected_command_argument" for finding in record["findings"])


def test_renamed_evidence_log_fails(synthetic):
    manifest = _mutate(synthetic)
    manifest["output_paths"]["evidence_log_basename"] = "guard.jsonl"
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert any(finding["code"] == "evidence_basename_wrong" for finding in record["findings"])


def test_missing_evidence_directory_fails(synthetic):
    environment = gate.GateEnvironment(
        checkout_root=synthetic["checkout"],
        guard_dir=synthetic["guard_dir"],
        scratch_cache_root=synthetic["scratch"],
        python_executable=synthetic["tmp_path"] / "python.bin",
        git_executable=synthetic["tmp_path"] / "git.bin",
        repository_root=synthetic["repository"],
    )
    record = _evaluate(synthetic, environment=environment)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert any(finding["code"] == "evidence_dir_not_supplied" for finding in record["findings"])


# ---------------------------------------------------------------------------
# Symlink and ambiguous-path handling
# ---------------------------------------------------------------------------


def _can_symlink(tmp_path: Path) -> bool:
    probe = tmp_path / "symlink_probe"
    target = tmp_path / "symlink_target"
    target.write_text("x", encoding="utf-8")
    try:
        probe.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def test_symlinked_source_file_pointing_outside_the_checkout_fails(synthetic):
    """A symlink whose target escapes the checkout is refused as an escape.

    Containment is evaluated before ambiguity, so this reports
    ``source_file_outside_checkout`` rather than ``source_file_ambiguous``.
    Both are fail-closed; the containment code is the more precise one, because
    escaping the isolated checkout is the specific thing that matters here.
    """
    tmp_path = synthetic["tmp_path"]
    if not _can_symlink(tmp_path):
        pytest.skip("this platform/account cannot create symlinks")
    target = synthetic["checkout"] / "src" / "lf_module.py"
    real = tmp_path / "real_lf_module.py"
    real.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(real)
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_SOURCE
    assert any(finding["code"] == "source_file_outside_checkout" for finding in record["findings"])


def test_symlinked_source_file_pointing_inside_the_checkout_fails(synthetic):
    """A symlink that stays inside the checkout is still refused, as ambiguous.

    This is the case containment cannot catch, so it proves the ambiguity check
    is still doing work rather than being shadowed by the new rule.
    """
    tmp_path = synthetic["tmp_path"]
    if not _can_symlink(tmp_path):
        pytest.skip("this platform/account cannot create symlinks")
    target = synthetic["checkout"] / "src" / "lf_module.py"
    real = synthetic["checkout"] / "src" / "real_lf_module.py"
    real.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(real)
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_SOURCE
    assert any(finding["code"] == "source_file_ambiguous" for finding in record["findings"])


def test_symlinked_evidence_log_path_fails(synthetic):
    tmp_path = synthetic["tmp_path"]
    if not _can_symlink(tmp_path):
        pytest.skip("this platform/account cannot create symlinks")
    real = tmp_path / "real-guard.jsonl"
    real.write_text("stale\n", encoding="utf-8")
    (synthetic["evidence"] / "m53-network-guard.jsonl").symlink_to(real)
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert any(finding["code"] == "evidence_log_pre_exists" for finding in record["findings"])


def test_symlinked_scratch_cache_file_fails(synthetic):
    tmp_path = synthetic["tmp_path"]
    if not _can_symlink(tmp_path):
        pytest.skip("this platform/account cannot create symlinks")
    real = tmp_path / "real_cache_byte.bin"
    real.write_bytes(b"daily_bar")
    target = synthetic["scratch"] / "daily_bar" / "a.bin"
    target.unlink()
    target.symlink_to(real)
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "scratch_tree_unreadable" for finding in record["findings"])


def test_link_state_reports_ambiguous_when_the_real_path_differs(tmp_path, monkeypatch):
    """A junction-like path whose real location differs is ambiguous, not usable.

    Windows directory junctions are not symlinks and cannot be created without
    elevation on every account, so the divergence itself is exercised directly.
    """
    target = tmp_path / "plain.txt"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(gate.os.path, "realpath", lambda path: str(tmp_path / "elsewhere.txt"))
    assert gate._link_state(target) == "ambiguous"


def test_link_state_classifies_an_absent_path(tmp_path):
    assert gate._link_state(tmp_path / "nothing") == "absent"


# ---------------------------------------------------------------------------
# Evidence-record properties
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_byte_identical_evidence(synthetic):
    first = gate.render_record(_evaluate(synthetic))
    second = gate.render_record(_evaluate(synthetic))
    assert first == second


def test_evidence_contains_no_timestamp_field(synthetic):
    text = gate.render_record(_evaluate(synthetic))
    for token in ("timestamp", "generated_at", "utc", "_at\""):
        assert token not in text.lower()


def test_evidence_leaks_no_path_username_or_content(synthetic):
    record = _evaluate(synthetic)
    text = gate.render_record(record)
    tmp_path = synthetic["tmp_path"]
    for fragment in (str(tmp_path), str(tmp_path).replace("\\", "/"), tmp_path.name):
        assert fragment not in text
    username = os.environ.get("USERNAME") or os.environ.get("USER")
    if username:
        assert username not in text
    # Cache and source bytes never appear.
    for fragment in ("daily_barDAILY_BAR", "alpha\\nbeta", "guard core"):
        assert fragment not in text
    # Only digests represent supplied paths.
    for value in record["supplied_input_digests"].values():
        assert value is None or (len(value) == 64 and set(value) <= set("0123456789abcdef"))


def test_evidence_records_a_finding_count_for_every_category(synthetic):
    record = _evaluate(synthetic, manifest=_mutate(synthetic))
    assert set(record["finding_counts"]) == set(gate.CATEGORIES)


def test_blocked_record_still_denies_authorization(synthetic):
    record = _evaluate(synthetic, environment=gate.GateEnvironment())
    assert record["execution_authorized"] is False
    assert record["m53_executed"] is False
    assert record["decision"] != gate.DECISION_READY


def test_decision_precedence_is_deterministic():
    findings = [
        gate.Finding("output_criterion", "x", "x"),
        gate.Finding("source_or_materialization", "y", "y"),
        gate.Finding("guard_or_executable", "z", "z"),
    ]
    assert gate.decide(findings) == gate.DECISION_SOURCE
    assert gate.decide([]) == gate.DECISION_READY


# ---------------------------------------------------------------------------
# The committed manifest
# ---------------------------------------------------------------------------


def test_committed_manifest_declares_the_real_profile():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["profile"] == gate.PROFILE_M53
    assert manifest["schema_version"] == gate.SCHEMA_VERSION


def test_committed_manifest_is_structurally_valid():
    manifest = gate.load_manifest_text(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert gate.validate_manifest_schema(manifest) == []


def test_committed_manifest_reconciles_with_the_frozen_reference():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert gate.reconcile_with_frozen_reference(manifest) == []


def test_committed_manifest_pins_the_reviewed_closure_shape():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    rows = manifest["source_closure"]["files"]
    assert manifest["source_closure"]["file_count"] == 30
    assert len(rows) == 30
    assert sum(1 for row in rows if row["materialization"] == "crlf") == 2
    assert manifest["historical_commit"] == "4c43fe96ab80d739fd10a9bd6bfa4c1319f67865"


def test_committed_manifest_pins_the_guard_files_at_current_main():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in manifest["guard"]["files"]}
    assert set(rows) == set(gate.FROZEN_REFERENCE["guard_files"])
    for path, expected in gate.FROZEN_REFERENCE["guard_files"].items():
        assert rows[path]["git_object_sha256"] == expected["git_object_sha256"]
        assert rows[path]["expected_sha256"] == expected["git_object_sha256"]


def test_committed_manifest_blocks_today_because_nothing_is_materialized():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    record = gate.evaluate(manifest, gate.GateEnvironment())
    assert record["decision"] == gate.DECISION_SOURCE
    assert record["execution_authorized"] is False
    assert record["m53_executed"] is False


def test_wrong_historical_commit_fails_reconciliation():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["historical_commit"] = "0" * 40
    findings = gate.reconcile_with_frozen_reference(manifest)
    assert any(finding.code == "historical_commit_mismatch" for finding in findings)


def test_regenerated_m52_handoff_fails_reconciliation():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    # Any digest other than the frozen historical one is refused, which is what
    # makes a regenerated M52 artifact ineligible without needing its hash.
    manifest["input_artifact"]["sha256"] = "3" * 64
    findings = gate.reconcile_with_frozen_reference(manifest)
    assert any(finding.code == "m52_handoff_not_historical" for finding in findings)


def test_changed_guard_file_hash_fails_reconciliation():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["guard"]["files"][0]["git_object_sha256"] = "5" * 64
    findings = gate.reconcile_with_frozen_reference(manifest)
    assert any(finding.code == "guard_file_digest_mismatch" for finding in findings)


@pytest.mark.parametrize("field", ["files", "bytes", "aggregate_sha256"])
def test_changed_cache_evidence_fails_reconciliation(field):
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    tree = manifest["caches"]["trees"][0]
    tree[field] = "7" * 64 if field == "aggregate_sha256" else tree[field] + 1
    findings = gate.reconcile_with_frozen_reference(manifest)
    assert findings


def test_synthetic_profile_is_exempt_from_reconciliation(synthetic):
    assert gate.reconcile_with_frozen_reference(synthetic["manifest"]) == []


# ---------------------------------------------------------------------------
# Independent-review regressions (Phase 2B-2G review of PR #385)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../outside_secret.py",
        "src/../../outside_secret.py",
        "C:/Windows/System32/x.py",
        "/etc/passwd",
        "//server/share/x.py",
        "//./PhysicalDrive0",
    ],
)
def test_source_path_escaping_the_checkout_is_refused(synthetic, path):
    """A manifest must not be able to verify a file outside the isolated checkout.

    Before this regression the gate returned READY while hashing a real,
    hash-matching file outside the checkout, which defeats the isolation the
    gate exists to enforce.
    """
    outside = synthetic["tmp_path"] / "outside_secret.py"
    outside.write_bytes((synthetic["checkout"] / "src" / "lf_module.py").read_bytes())
    manifest = _mutate(synthetic)
    manifest["source_closure"]["files"][0]["path"] = path
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY
    assert record["manifest_schema_valid"] is False


def test_absolute_source_path_to_a_real_matching_file_is_refused(synthetic):
    outside = synthetic["tmp_path"] / "outside_secret.py"
    outside.write_bytes((synthetic["checkout"] / "src" / "lf_module.py").read_bytes())
    manifest = _mutate(synthetic)
    manifest["source_closure"]["files"][0]["path"] = str(outside).replace(BACKSLASH, "/")
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY


def test_handoff_path_escaping_the_checkout_is_refused(synthetic):
    manifest = _mutate(synthetic)
    manifest["input_artifact"]["relative_path"] = "../outside.json"
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY


def test_backslash_separator_is_refused(synthetic):
    """One manifest must not mean two different things on Windows and POSIX."""
    manifest = _mutate(synthetic)
    manifest["source_closure"]["files"][0]["path"] = "src" + BACKSLASH + "lf_module.py"
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY
    assert any("separators" in finding["detail"] for finding in record["findings"])


@pytest.mark.parametrize("name", ["NUL", "CON", "COM1", "nul.py", "src/AUX"])
def test_reserved_device_names_are_refused(synthetic, name):
    manifest = _mutate(synthetic)
    manifest["source_closure"]["files"][0]["path"] = name
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY


def test_case_folded_duplicate_paths_are_refused(synthetic):
    manifest = _mutate(synthetic)
    duplicate = deepcopy(manifest["source_closure"]["files"][0])
    duplicate["path"] = duplicate["path"].upper()
    manifest["source_closure"]["files"].append(duplicate)
    manifest["source_closure"]["file_count"] = len(manifest["source_closure"]["files"])
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY
    assert any("case-folded" in finding["detail"] for finding in record["findings"])


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("isolation", "original_cache_roots"),
        ("guard", "required_environment"),
        ("guard", "cleared_environment"),
        ("guard", "forbidden_environment"),
        ("output_criterion", "canonical_exclusions"),
        ("input_artifact", "classification"),
        ("execution_command", "argv_after_module"),
    ],
)
@pytest.mark.parametrize("bad", [123, None, {}, []])
def test_non_string_entries_in_string_lists_fail_closed(synthetic, section, field, bad):
    """A non-string entry used to reach path arithmetic and raise with no record."""
    manifest = _mutate(synthetic)
    manifest[section][field] = [bad]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY
    assert record["execution_authorized"] is False
    assert record["m53_executed"] is False


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("guard", "cleared_environment"),
        ("guard", "required_environment"),
        ("output_criterion", "canonical_exclusions"),
        ("isolation", "original_cache_roots"),
    ],
)
def test_duplicate_string_list_entries_are_refused(synthetic, section, field):
    manifest = _mutate(synthetic)
    values = list(manifest[section][field])
    manifest[section][field] = values + [values[0]]
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY
    assert any("duplicate entry" in finding["detail"] for finding in record["findings"])


@pytest.mark.parametrize("size", [0, -1, -4096])
def test_non_positive_byte_counts_are_refused(synthetic, size):
    manifest = _mutate(synthetic)
    row = manifest["source_closure"]["files"][0]
    row["git_object_bytes"] = size
    row["expected_bytes"] = size
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] != gate.DECISION_READY


def test_non_positive_guard_byte_counts_are_refused(synthetic):
    manifest = _mutate(synthetic)
    manifest["guard"]["files"][0]["git_object_bytes"] = 0
    manifest["guard"]["files"][0]["expected_bytes"] = 0
    assert _evaluate(synthetic, manifest=manifest)["decision"] != gate.DECISION_READY


@pytest.mark.parametrize(
    ("section", "field"),
    [("execution_command", "module"), ("output_paths", "evidence_log_basename"), ("guard", "scope")],
)
def test_blank_identity_strings_are_refused(synthetic, section, field):
    manifest = _mutate(synthetic)
    manifest[section][field] = "   "
    assert _evaluate(synthetic, manifest=manifest)["decision"] != gate.DECISION_READY


def test_uppercase_digests_are_refused(synthetic):
    """Digests are compared as text; a non-canonical form must not slip through."""
    manifest = _mutate(synthetic)
    row = manifest["source_closure"]["files"][0]
    row["git_object_sha256"] = row["git_object_sha256"].upper()
    row["expected_sha256"] = row["expected_sha256"].upper()
    assert _evaluate(synthetic, manifest=manifest)["decision"] != gate.DECISION_READY


def test_internal_error_fails_closed_without_leaking_the_message(synthetic):
    """An unexpected internal failure must still produce a blocked record."""

    class Exploding(dict):
        def __getitem__(self, key):
            if key == "files":
                raise RuntimeError("C:/private/secret/path")
            return dict.__getitem__(self, key)

    manifest = _mutate(synthetic)
    manifest["source_closure"] = Exploding(manifest["source_closure"])
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert record["execution_authorized"] is False
    assert record["m53_executed"] is False
    assert [finding["code"] for finding in record["findings"]] == ["internal_error"]
    assert "C:/private/secret/path" not in gate.render_record(record)


def test_row_order_does_not_change_the_decision_or_evidence(synthetic):
    """Order independence is a property, not an accident: prove it explicitly."""
    reordered = _mutate(synthetic)
    reordered["source_closure"]["files"].reverse()
    reordered["caches"]["trees"].reverse()
    reordered["guard"]["files"].reverse()
    assert gate.render_record(_evaluate(synthetic, manifest=reordered)) == gate.render_record(
        _evaluate(synthetic)
    )


def test_cli_output_write_is_atomic(synthetic, tmp_path):
    """The staging file must not survive a successful write."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(synthetic["manifest"]), encoding="utf-8")
    output = tmp_path / "evidence.json"
    gate.main(["--manifest", str(manifest_path), "--output", str(output)])
    assert output.exists()
    assert not (tmp_path / "evidence.json.partial").exists()


def test_validate_relative_path_accepts_ordinary_repository_paths():
    for good in ("src/config.py", "scripts/research_momentum_risk_control.py", "a/b/c.json"):
        assert gate.validate_relative_path(good) is None


def test_committed_manifest_paths_all_pass_the_relative_path_rule():
    manifest = json.loads(COMMITTED_MANIFEST_PATH.read_text(encoding="utf-8"))
    paths = [row["path"] for row in manifest["source_closure"]["files"]]
    paths += [row["path"] for row in manifest["guard"]["files"]]
    paths += list(manifest["isolation"]["original_cache_roots"])
    paths += [
        manifest["input_artifact"]["relative_path"],
        manifest["output_paths"]["artifact_relative_path"],
    ]
    assert [p for p in paths if gate.validate_relative_path(p) is not None] == []


# ---------------------------------------------------------------------------
# Reparse-point coverage that does not depend on symlink privilege
# ---------------------------------------------------------------------------

# Creating a symlink needs Developer Mode or elevation on Windows, and creating
# a directory junction needs an external tool, so the three symlink tests above
# skip on ordinary accounts.  These tests exercise the same divergence the guard
# actually keys on -- a resolved location that differs from the literal one --
# by redirecting realpath, so junction/reparse behaviour is covered everywhere.


def test_junctioned_parent_directory_makes_a_source_file_ambiguous(synthetic, monkeypatch):
    """A junctioned *parent* must be caught, not only a linked leaf file."""
    checkout = synthetic["checkout"]
    real_realpath = gate.os.path.realpath
    target = checkout / "src" / "lf_module.py"

    def fake_realpath(path, *args, **kwargs):
        if str(path) == str(target):
            return str(synthetic["tmp_path"] / "elsewhere" / "lf_module.py")
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(gate.os.path, "realpath", fake_realpath)
    record = _evaluate(synthetic)
    assert record["decision"] != gate.DECISION_READY
    codes = {finding["code"] for finding in record["findings"]}
    assert codes & {"source_file_ambiguous", "source_file_outside_checkout"}


def test_junctioned_scratch_root_is_unusable(synthetic, monkeypatch):
    scratch = synthetic["scratch"]
    real_realpath = gate.os.path.realpath

    def fake_realpath(path, *args, **kwargs):
        if str(path) == str(scratch):
            return str(synthetic["tmp_path"] / "junction_target")
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(gate.os.path, "realpath", fake_realpath)
    record = _evaluate(synthetic)
    assert record["decision"] != gate.DECISION_READY
    assert any(finding["code"] == "scratch_root_unusable" for finding in record["findings"])


def test_original_cache_overlap_is_detected_through_a_resolved_alias(synthetic, monkeypatch):
    """An alias that resolves onto the scratch root must still count as overlap."""
    manifest = _mutate(synthetic)
    manifest["isolation"]["original_cache_roots"] = ["aliased/daily_bar"]
    repository = synthetic["repository"]
    alias = repository / "aliased" / "daily_bar"
    scratch_tree = synthetic["scratch"] / "daily_bar"
    real_realpath = gate.os.path.realpath

    def fake_realpath(path, *args, **kwargs):
        if str(path) == str(alias):
            return real_realpath(scratch_tree)
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(gate.os.path, "realpath", fake_realpath)
    record = _evaluate(synthetic, manifest=manifest)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "original_cache_root_overlap" for finding in record["findings"])


def test_guard_directory_overlap_is_detected_through_a_resolved_alias(synthetic, monkeypatch):
    guard_dir = synthetic["guard_dir"]
    checkout = synthetic["checkout"]
    real_realpath = gate.os.path.realpath

    def fake_realpath(path, *args, **kwargs):
        if str(path) == str(guard_dir):
            return real_realpath(checkout / "nested_guard")
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(gate.os.path, "realpath", fake_realpath)
    record = _evaluate(synthetic)
    assert record["decision"] != gate.DECISION_READY
    codes = {finding["code"] for finding in record["findings"]}
    assert codes & {"guard_dir_overlaps_checkout", "guard_dir_unusable"}


def test_unreadable_source_file_fails_closed(synthetic, monkeypatch):
    """An OSError while hashing must produce a finding, never an exception."""
    real_read = gate.Path.read_bytes

    def fake_read(self, *args, **kwargs):
        if self.name == "lf_module.py":
            raise OSError("device not ready")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(gate.Path, "read_bytes", fake_read)
    record = _evaluate(synthetic)
    assert record["decision"] != gate.DECISION_READY
    assert any(finding["code"] == "source_file_unreadable" for finding in record["findings"])


def test_unreadable_scratch_tree_fails_closed(synthetic, monkeypatch):
    def fake_walk(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(gate.os, "walk", fake_walk)
    record = _evaluate(synthetic)
    assert record["decision"] == gate.DECISION_CACHE
    assert any(finding["code"] == "scratch_tree_unreadable" for finding in record["findings"])


# ---------------------------------------------------------------------------
# Static proof that the implementation cannot run anything
# ---------------------------------------------------------------------------


GATE_SOURCE_PATH = REPOSITORY_ROOT / "scripts" / "publication" / "m53_execution_gate.py"

FORBIDDEN_IMPORTS = frozenset(
    {
        "subprocess",
        "socket",
        "ssl",
        "asyncio",
        "multiprocessing",
        "ctypes",
        "urllib",
        "urllib.request",
        "http",
        "http.client",
        "requests",
        "httpx",
        "ftplib",
        "smtplib",
        "telnetlib",
        "webbrowser",
        "pty",
        "shutil",
        "pickle",
        "importlib",
        "runpy",
    }
)

FORBIDDEN_CALL_NAMES = frozenset(
    {
        "system",
        "popen",
        "spawn",
        "spawnl",
        "spawnv",
        "posix_spawn",
        "fork",
        "forkpty",
        "startfile",
        "execv",
        "execve",
        "execl",
        "exec",
        "eval",
        "compile",
        "__import__",
        "import_module",
        "run_module",
        "run_path",
        "urlopen",
        "connect",
        "socket",
        "load",
        "loads_pickle",
    }
)


def _gate_ast() -> ast.Module:
    return ast.parse(GATE_SOURCE_PATH.read_text(encoding="utf-8"))


def test_gate_imports_nothing_that_can_execute_or_network():
    imported: set[str] = set()
    for node in ast.walk(_gate_ast()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported & FORBIDDEN_IMPORTS == set()


def test_gate_calls_nothing_that_can_execute_or_network():
    called: set[str] = set()
    for node in ast.walk(_gate_ast()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    # json.load/loads are legitimate here; the pickle-style loader is not.
    assert called & (FORBIDDEN_CALL_NAMES - {"load"}) == set()


def test_gate_never_imports_or_names_the_m53_entrypoint():
    text = GATE_SOURCE_PATH.read_text(encoding="utf-8")
    tree = _gate_ast()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("scripts.research")
                assert not alias.name.startswith("src.")
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("scripts.research")
            assert not node.module.startswith("src.")
    # The module name appears only as frozen manifest data, never as an import.
    assert "import scripts.research" not in text


def test_authorization_flags_are_only_ever_assigned_false():
    tree = _gate_ast()
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value in ("execution_authorized", "m53_executed"):
                assert isinstance(value, ast.Constant) and value.value is False
                seen += 1
    assert seen == 2


def test_no_command_line_option_can_authorize_execution():
    parser = gate.build_parser()
    option_text = " ".join(
        " ".join(action.option_strings) + " " + (action.help or "") for action in parser._actions
    ).lower()
    for forbidden in ("--execute", "--run", "--authorize", "--launch", "--force"):
        assert forbidden not in option_text


def test_gate_writes_nothing_outside_the_requested_output(synthetic, tmp_path):
    output = tmp_path / "evidence.json"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(synthetic["manifest"]), encoding="utf-8")
    exit_code = gate.main(["--manifest", str(manifest_path), "--output", str(output)])
    assert exit_code == 1  # nothing materialized via the CLI, so it blocks
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["execution_authorized"] is False
    assert record["m53_executed"] is False


def test_unreadable_manifest_fails_closed(tmp_path):
    missing = tmp_path / "absent.json"
    output = tmp_path / "out.json"
    assert gate.main(["--manifest", str(missing), "--output", str(output)]) == 1
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["decision"] == gate.DECISION_EVIDENCE
    assert record["manifest_schema_valid"] is False
