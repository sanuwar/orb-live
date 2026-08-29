"""Deterministic, verification-only M53 reproduction preflight gate (Issue #384).

Phase 2B-2G assembles the already-reviewed M53 reproduction controls from
Publication P3B Phases 2A, 2B-1, 2B-1C, 2B-2A, and 2B-2N into one mechanical,
fail-closed preflight.  It answers exactly one question:

    Could a later, *separately authorized* M53 reproduction begin without
    changing any frozen source, parameter, historical input, cache, output
    criterion, or safety boundary?

It never answers "should it run".  This module contains no code path that
imports, launches, simulates, regenerates, or fetches anything.  It starts no
subprocess and opens no socket.  Every emitted record carries
``execution_authorized=false`` and ``m53_executed=false``, and no option,
argument, manifest field, or environment variable can change either value.

Design notes
------------

*Manifest driven.*  All frozen hashes, sizes, counts, paths, command vectors,
and safety requirements arrive in one explicit JSON manifest.  The manifest is
schema-checked, rejects unknown and duplicate fields, and — when it declares the
real ``M53_PHASE2B2A`` profile — is additionally reconciled field by field
against ``FROZEN_REFERENCE`` below, which is transcribed from the merged
Phase 2A/2B-1/2B-2A/2B-2N records.

*Evidence driven.*  Filesystem inputs (an isolated historical checkout, a guard
directory, scratch cache copies, executables, evidence and output paths) are
optional.  Supplying none is not "nothing to check"; it is a materialization
failure, and the gate blocks.  Nothing is materialized by this module.

*Deterministic and leak-free.*  The evidence record contains no timestamp, no
absolute path, no username, no credential, no raw market data, and no cache
content.  Paths appear only as SHA-256 digests of their normalized text, so two
runs over identical inputs produce byte-identical evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "orb-live/m53-execution-gate/1"

PROFILE_M53 = "M53_PHASE2B2A"
PROFILE_SYNTHETIC = "SYNTHETIC_TEST_ONLY"

DECISION_READY = "READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION"
DECISION_SOURCE = "BLOCKED_SOURCE_OR_MATERIALIZATION"
DECISION_INPUT = "BLOCKED_INPUT_ARTIFACT"
DECISION_PARAMETERS = "BLOCKED_PARAMETERS"
DECISION_CACHE = "BLOCKED_CACHE_OR_ISOLATION"
DECISION_GUARD = "BLOCKED_GUARD_OR_EXECUTABLE"
DECISION_OUTPUT = "BLOCKED_OUTPUT_CRITERION"
DECISION_EVIDENCE = "BLOCKED_EVIDENCE_CONFIGURATION"

# Category -> decision.  The order of this mapping is the decision precedence:
# when several categories block, the earliest listed one names the decision.
# Source and input problems are reported ahead of downstream ones because a
# wrong source or a wrong historical input invalidates everything after it.
CATEGORY_DECISIONS: tuple[tuple[str, str], ...] = (
    ("source_or_materialization", DECISION_SOURCE),
    ("input_artifact", DECISION_INPUT),
    ("parameters", DECISION_PARAMETERS),
    ("cache_or_isolation", DECISION_CACHE),
    ("guard_or_executable", DECISION_GUARD),
    ("output_criterion", DECISION_OUTPUT),
    ("evidence_configuration", DECISION_EVIDENCE),
)
CATEGORIES: tuple[str, ...] = tuple(name for name, _ in CATEGORY_DECISIONS)

# The credential and proxy names a future launcher must clear.  This is the
# Phase 2B-2A list plus the additional aliases Phase 2B-2N added after review
# found eight of the sixteen originally missing.
REQUIRED_CLEARED_ENVIRONMENT: tuple[str, ...] = (
    "ALL_PROXY",
    "ALPACA_API_KEY",
    "ALPACA_API_KEY_ID",
    "ALPACA_API_SECRET_KEY",
    "ALPACA_BASE_URL",
    "ALPACA_FEED",
    "ALPACA_SECRET_KEY",
    "APCA_API_BASE_URL",
    "APCA_API_DATA_URL",
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "FTP_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "all_proxy",
    "ftp_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

REQUIRED_GUARD_ENVIRONMENT: tuple[str, ...] = (
    "ORB_M53_GUARD_CHECKOUT",
    "ORB_M53_GUARD_EXPECTED_SHA256",
    "ORB_M53_GUARD_EXPECTED_SITECUSTOMIZE_SHA256",
    "ORB_M53_GUARD_GIT_EXECUTABLE",
    "ORB_M53_GUARD_GIT_SHA256",
    "ORB_M53_GUARD_LOG",
    "ORB_M53_GUARD_PYTHON_SHA256",
    "ORB_M53_GUARD_TOKEN",
)

FORBIDDEN_GUARD_ENVIRONMENT: tuple[str, ...] = ("ORB_M53_GUARD_DISABLE",)

CACHE_ROLES: tuple[str, ...] = ("daily_bar", "openfigi", "sec_nport")

# ---------------------------------------------------------------------------
# Frozen reference, transcribed from the merged P3B records.
# ---------------------------------------------------------------------------

FROZEN_REFERENCE: dict[str, Any] = {
    "historical_commit": "4c43fe96ab80d739fd10a9bd6bfa4c1319f67865",
    "source_closure_file_count": 30,
    "source_closure_crlf_file_count": 2,
    "input_artifact_sha256": (
        "590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9"
    ),
    "input_artifact_bytes": 34124,
    "input_artifact_relative_path": (
        "artifacts/research-cross-sectional-momentum/results/m52_result.json"
    ),
    "input_artifact_classification": (
        "EXACT_BYTE_REPRODUCTION_FAIL",
        "RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH",
    ),
    "parameter_record_count": 49,
    "parameter_record_bytes": 5018,
    "parameter_digest_sha256": (
        "7c2b2da4168333c198e44bb573655d995c129931ac2ae8b973a0985b77e4a0ad"
    ),
    "output_complete_sha256": (
        "a3d3ebbd62f45331115a74583f7f8c378e9f2e786fb693b12865fff8860122c1"
    ),
    "output_complete_bytes": 28715,
    "output_canonical_exclusions": ("/generated_at",),
    "output_canonical_payload_sha256": (
        "9747dec356d11d8cdbcb30d3ac3838fe962dad85674c0c4c635029c094b8a412"
    ),
    "output_canonical_payload_bytes": 22055,
    "output_artifact_relative_path": (
        "artifacts/research-momentum-risk-control/results/m53_result.json"
    ),
    "guard_token": "M53_NETWORK_DENIAL_GUARD_V1",
    "guard_files": {
        "scripts/publication/m53_network_guard/guard_core.py": {
            "git_object_sha256": (
                "df625207220042ed88405b6f57dcbe357571f9fbe58df3a782ceebc39e4d47b9"
            ),
            "git_object_bytes": 23251,
            "materialization": "lf",
        },
        "scripts/publication/m53_network_guard/sitecustomize.py": {
            "git_object_sha256": (
                "34fdd6c487d7a53449deccf0be56d508b5dabf8e4503d50f9daad8b21f2313dc"
            ),
            "git_object_bytes": 620,
            "materialization": "lf",
        },
    },
    "cache_trees": {
        "daily_bar": {
            "repo_relative_root": (
                "artifacts/research-cross-sectional-momentum/daily-bar-cache"
            ),
            "files": 942,
            "bytes": 177025917,
            "aggregate_sha256": (
                "0698d51e8f3c1d599e5d85884fa192b2abf8bfde1cd8495574404681626ebf8d"
            ),
        },
        "openfigi": {
            "repo_relative_root": (
                "artifacts/research-cross-sectional-momentum/openfigi-cache"
            ),
            "files": 5243,
            "bytes": 786008,
            "aggregate_sha256": (
                "b2ad68c940fe3854bca92918290a94211a056cee06887e1e79a0a9020dd5ef86"
            ),
        },
        "sec_nport": {
            "repo_relative_root": (
                "artifacts/research-cross-sectional-momentum/sec-nport-cache"
            ),
            "files": 29,
            "bytes": 25130891,
            "aggregate_sha256": (
                "889fb5dc0a8b5d5f6ecf6693cd48bb0beae62cd38524fa73c308f44cf546c4fc"
            ),
        },
    },
    "command_module": "scripts.research_momentum_risk_control",
    "command_argv_after_module": (),
    "fetch_missing": False,
    "evidence_log_basename": "m53-network-guard.jsonl",
    "guard_ready_event": "guard_ready",
    "guard_terminal_event": "normal_interpreter_exit",
    "guard_post_run_verifier": "verify_guard_run",
    "guard_scope": "PROCESS_SCOPED_PYTHON_GUARD",
}

# Regenerated-M52 artifacts are permanently ineligible as the M53 handoff.  The
# Phase 2B-1 regeneration was preserved as scratch-only diagnostic evidence and
# is not in this repository, so it is identified by the exclusion rule rather
# than by a committed hash: any handoff hash other than the historical one is
# refused, and a manifest that names the historical hash as "regenerated" is
# refused as self-contradictory.
REGENERATED_M52_IS_INELIGIBLE = True


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str  # "str" | "int" | "bool" | "list" | "dict"
    required: bool = True


SOURCE_FILE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("path", "str"),
    FieldSpec("git_blob_sha1", "str"),
    FieldSpec("git_object_sha256", "str"),
    FieldSpec("git_object_bytes", "int"),
    FieldSpec("materialization", "str"),
    FieldSpec("expected_sha256", "str"),
    FieldSpec("expected_bytes", "int"),
)

GUARD_FILE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("path", "str"),
    FieldSpec("git_object_sha256", "str"),
    FieldSpec("git_object_bytes", "int"),
    FieldSpec("materialization", "str"),
    FieldSpec("expected_sha256", "str"),
    FieldSpec("expected_bytes", "int"),
)

CACHE_TREE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("role", "str"),
    FieldSpec("repo_relative_root", "str"),
    FieldSpec("files", "int"),
    FieldSpec("bytes", "int"),
    FieldSpec("aggregate_sha256", "str"),
)

TOP_LEVEL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("schema_version", "str"),
    FieldSpec("profile", "str"),
    FieldSpec("historical_commit", "str"),
    FieldSpec("source_closure", "dict"),
    FieldSpec("input_artifact", "dict"),
    FieldSpec("parameters", "dict"),
    FieldSpec("output_criterion", "dict"),
    FieldSpec("guard", "dict"),
    FieldSpec("executables", "dict"),
    FieldSpec("caches", "dict"),
    FieldSpec("isolation", "dict"),
    FieldSpec("execution_command", "dict"),
    FieldSpec("output_paths", "dict"),
)

SECTION_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "source_closure": (
        FieldSpec("file_count", "int"),
        FieldSpec("files", "list"),
    ),
    "input_artifact": (
        FieldSpec("relative_path", "str"),
        FieldSpec("sha256", "str"),
        FieldSpec("bytes", "int"),
        FieldSpec("classification", "list"),
        FieldSpec("regenerated_artifact_eligible", "bool"),
    ),
    "parameters": (
        FieldSpec("record_count", "int"),
        FieldSpec("record_bytes", "int"),
        FieldSpec("digest_sha256", "str"),
    ),
    "output_criterion": (
        FieldSpec("complete_sha256", "str"),
        FieldSpec("complete_bytes", "int"),
        FieldSpec("canonical_exclusions", "list"),
        FieldSpec("canonical_payload_sha256", "str"),
        FieldSpec("canonical_payload_bytes", "int"),
        FieldSpec("raw_and_canonical_reported_separately", "bool"),
    ),
    "guard": (
        FieldSpec("scope", "str"),
        FieldSpec("files", "list"),
        FieldSpec("token", "str"),
        FieldSpec("required_environment", "list"),
        FieldSpec("forbidden_environment", "list"),
        FieldSpec("cleared_environment", "list"),
        FieldSpec("external_pre_launch_verification_required", "bool"),
        FieldSpec("ready_event", "str"),
        FieldSpec("terminal_event", "str"),
        FieldSpec("post_run_verifier", "str"),
    ),
    "executables": (
        FieldSpec("python_content_pin_required", "bool"),
        FieldSpec("git_content_pin_required", "bool"),
    ),
    "caches": (
        FieldSpec("trees", "list"),
        FieldSpec("original_exposure_forbidden", "bool"),
        FieldSpec("fetch_missing", "bool"),
        FieldSpec("aggregate_algorithm", "str"),
    ),
    "isolation": (
        FieldSpec("scratch_root_required", "bool"),
        FieldSpec("preserved_scratch_evidence_untouched", "bool"),
        FieldSpec("original_cache_roots", "list"),
    ),
    "execution_command": (
        FieldSpec("module", "str"),
        FieldSpec("argv_after_module", "list"),
    ),
    "output_paths": (
        FieldSpec("artifact_relative_path", "str"),
        FieldSpec("evidence_log_basename", "str"),
    ),
}

_KIND_TYPES: dict[str, Any] = {
    "str": str,
    "int": int,
    "bool": bool,
    "list": list,
    "dict": dict,
}

_HEX64 = frozenset("0123456789abcdef")


class ManifestError(Exception):
    """Raised when the manifest cannot be interpreted at all."""


# Windows reserved device names.  "NUL" and friends resolve to a device rather
# than a file, and reading one yields empty bytes, so a manifest row naming one
# must never be treated as a source file.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)


def validate_relative_path(value: object) -> str | None:
    """Return a problem string, or None when ``value`` is a safe relative path.

    A manifest path must name a location *inside* the root it is resolved
    against, and must mean the same thing on every platform.  Traversal,
    absolute, drive-relative, UNC, and device paths are refused because they let
    a manifest point verification outside the isolated checkout — which would
    defeat the isolation this gate exists to enforce.  Backslash separators are
    refused because ``src\\module.py`` is a directory separator on Windows and a
    literal filename character on POSIX, so accepting it would make one manifest
    mean two different things.
    """
    if not isinstance(value, str):
        return "must be a string"
    if not value:
        return "must not be empty"
    if "\\" in value:
        return "must use '/' separators only"
    if value.startswith("/") or value.startswith("//"):
        return "must be relative, not absolute or UNC"
    if len(value) >= 2 and value[1] == ":":
        return "must not be a drive-qualified path"
    if value.endswith("/"):
        return "must not end with a separator"
    segments = value.split("/")
    for segment in segments:
        if not segment:
            return "must not contain an empty path segment"
        if segment in (".", ".."):
            return "must not contain '.' or '..' segments"
        if segment.strip() != segment:
            return "must not contain leading or trailing whitespace in a segment"
        stem = segment.split(".")[0].upper()
        if stem in _RESERVED_NAMES:
            return f"must not name the reserved device '{stem}'"
    return None


def _check_string_list(
    section: str,
    values: object,
    *,
    allow_empty_list: bool = False,
    paths: bool = False,
) -> list[str]:
    """Validate a list of strings: element types, emptiness, and duplicates.

    A non-string element here previously reached path arithmetic and raised,
    which produced no evidence record at all.
    """
    problems: list[str] = []
    if not isinstance(values, list):
        return [f"{section}: must be a list"]
    if not values and not allow_empty_list:
        return [f"{section}: must not be empty"]
    seen: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, str):
            problems.append(f"{section}[{index}]: must be a string")
            continue
        if not value:
            problems.append(f"{section}[{index}]: must not be empty")
            continue
        if value in seen:
            problems.append(f"{section}: duplicate entry {value!r}")
        seen.add(value)
        if paths:
            problem = validate_relative_path(value)
            if problem is not None:
                problems.append(f"{section}[{index}]: {problem}")
    return problems


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _is_sha1(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and set(value) <= _HEX64


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _value in pairs:
        if key in seen:
            raise ManifestError(f"duplicate manifest key: {key}")
        seen.add(key)
    return dict(pairs)


def load_manifest_text(text: str) -> dict[str, Any]:
    """Parse manifest JSON, rejecting duplicate keys and non-finite numbers."""
    try:
        value = json.loads(
            text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant
        )
    except ManifestError:
        raise
    except Exception as exc:  # noqa: BLE001 - any parse failure is fail-closed
        raise ManifestError(f"manifest is not valid JSON: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ManifestError("manifest root must be a JSON object")
    return value


def _reject_constant(name: str) -> Any:
    raise ManifestError(f"manifest contains forbidden JSON constant: {name}")


def _check_fields(
    section: str,
    obj: dict[str, Any],
    specs: tuple[FieldSpec, ...],
    problems: list[str],
) -> None:
    known = {spec.name for spec in specs}
    for spec in specs:
        if spec.name not in obj:
            if spec.required:
                problems.append(f"{section}: missing field '{spec.name}'")
            continue
        expected = _KIND_TYPES[spec.kind]
        value = obj[spec.name]
        # bool is a subclass of int; keep the two distinct.
        if expected is int and isinstance(value, bool):
            problems.append(f"{section}: field '{spec.name}' must be an integer, not a boolean")
            continue
        if not isinstance(value, expected):
            problems.append(f"{section}: field '{spec.name}' has the wrong type")
    for name in sorted(obj):
        if name not in known:
            problems.append(f"{section}: unknown field '{name}'")


def validate_manifest_schema(manifest: dict[str, Any]) -> list[str]:
    """Return a sorted list of schema problems; empty means structurally valid."""
    problems: list[str] = []
    _check_fields("manifest", manifest, TOP_LEVEL_FIELDS, problems)

    version = manifest.get("schema_version")
    if version != SCHEMA_VERSION:
        problems.append(f"manifest: unsupported schema_version {version!r}")

    profile = manifest.get("profile")
    if profile not in (PROFILE_M53, PROFILE_SYNTHETIC):
        problems.append(f"manifest: unsupported profile {profile!r}")

    if not _is_sha1(manifest.get("historical_commit")):
        problems.append("manifest: historical_commit is not a 40-character hex commit id")

    for section, specs in SECTION_FIELDS.items():
        obj = manifest.get(section)
        if isinstance(obj, dict):
            _check_fields(section, obj, specs, problems)

    closure = manifest.get("source_closure")
    if isinstance(closure, dict):
        problems.extend(_check_row_list("source_closure.files", closure.get("files"), SOURCE_FILE_FIELDS, "path"))
    guard = manifest.get("guard")
    if isinstance(guard, dict):
        problems.extend(_check_row_list("guard.files", guard.get("files"), GUARD_FILE_FIELDS, "path"))
    caches = manifest.get("caches")
    if isinstance(caches, dict):
        problems.extend(_check_row_list("caches.trees", caches.get("trees"), CACHE_TREE_FIELDS, "role"))

    # String-list fields.  Element types matter: a non-string here used to reach
    # path arithmetic and raise, producing no evidence record at all.
    isolation = manifest.get("isolation")
    if isinstance(isolation, dict):
        problems.extend(
            _check_string_list(
                "isolation.original_cache_roots",
                isolation.get("original_cache_roots"),
                allow_empty_list=True,
                paths=True,
            )
        )
    if isinstance(guard, dict):
        # Empty is allowed structurally so the *semantic* guard checks report a
        # missing requirement in the guard category, which is far more useful
        # than a generic schema error.
        for name in ("required_environment", "forbidden_environment", "cleared_environment"):
            problems.extend(_check_string_list(f"guard.{name}", guard.get(name), allow_empty_list=True))
    artifact = manifest.get("input_artifact")
    if isinstance(artifact, dict):
        problems.extend(
            _check_string_list(
                "input_artifact.classification", artifact.get("classification"), allow_empty_list=True
            )
        )
        problems.extend(
            _relative_path_problems("input_artifact.relative_path", artifact.get("relative_path"))
        )
    criterion = manifest.get("output_criterion")
    if isinstance(criterion, dict):
        problems.extend(
            _check_string_list(
                "output_criterion.canonical_exclusions",
                criterion.get("canonical_exclusions"),
                allow_empty_list=True,
            )
        )
    command = manifest.get("execution_command")
    if isinstance(command, dict):
        problems.extend(
            _check_string_list(
                "execution_command.argv_after_module",
                command.get("argv_after_module"),
                allow_empty_list=True,
            )
        )
        problems.extend(_non_empty_problems("execution_command.module", command.get("module")))
    output_paths = manifest.get("output_paths")
    if isinstance(output_paths, dict):
        problems.extend(
            _relative_path_problems(
                "output_paths.artifact_relative_path", output_paths.get("artifact_relative_path")
            )
        )
        problems.extend(
            _non_empty_problems("output_paths.evidence_log_basename", output_paths.get("evidence_log_basename"))
        )
    if isinstance(guard, dict):
        for name in ("scope", "token", "ready_event", "terminal_event", "post_run_verifier"):
            problems.extend(_non_empty_problems(f"guard.{name}", guard.get(name)))
    if isinstance(caches, dict):
        problems.extend(
            _non_empty_problems("caches.aggregate_algorithm", caches.get("aggregate_algorithm"))
        )

    return sorted(set(problems))


def _non_empty_problems(section: str, value: object) -> list[str]:
    if isinstance(value, str) and not value.strip():
        return [f"{section}: must not be empty"]
    return []


def _relative_path_problems(section: str, value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    problem = validate_relative_path(value)
    return [] if problem is None else [f"{section}: {problem}"]


def _check_row_list(
    section: str,
    rows: object,
    specs: tuple[FieldSpec, ...],
    identity_field: str,
) -> list[str]:
    problems: list[str] = []
    if not isinstance(rows, list):
        return [f"{section}: must be a list"]
    if not rows:
        return [f"{section}: must not be empty"]
    identities: list[str] = []
    for index, row in enumerate(rows):
        label = f"{section}[{index}]"
        if not isinstance(row, dict):
            problems.append(f"{label}: must be an object")
            continue
        _check_fields(label, row, specs, problems)
        identity = row.get(identity_field)
        if isinstance(identity, str):
            identities.append(identity)
        if identity_field == "path" and isinstance(identity, str):
            problem = validate_relative_path(identity)
            if problem is not None:
                problems.append(f"{label}: path {problem}")
        for name in ("git_object_sha256", "expected_sha256", "aggregate_sha256"):
            if name in row and not _is_sha256(row[name]):
                problems.append(f"{label}: {name} is not a 64-character hex digest")
        if "git_blob_sha1" in row and not _is_sha1(row["git_blob_sha1"]):
            problems.append(f"{label}: git_blob_sha1 is not a 40-character hex id")
        if row.get("materialization") not in (None, "lf", "crlf"):
            problems.append(f"{label}: materialization must be 'lf' or 'crlf'")
        for name in ("git_object_bytes", "expected_bytes", "bytes", "files"):
            value = row.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value <= 0:
                problems.append(f"{label}: {name} must be positive")
    duplicates = sorted({name for name in identities if identities.count(name) > 1})
    for name in duplicates:
        problems.append(f"{section}: duplicate {identity_field} {name!r}")
    # Case-insensitive collision: on a case-insensitive filesystem two rows
    # differing only in case name one file, so the closure would not really
    # contain the distinct number of files it declares.
    folded = [name.lower() for name in identities]
    for name in sorted({name for name in folded if folded.count(name) > 1}):
        if name not in {value.lower() for value in duplicates}:
            problems.append(f"{section}: {identity_field} values collide when case-folded: {name!r}")
    return problems


# ---------------------------------------------------------------------------
# Findings and gate environment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Finding:
    """One blocking condition.  ``detail`` must never carry a path or secret."""

    category: str
    code: str
    detail: str

    def as_record(self) -> dict[str, str]:
        return {"category": self.category, "code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class GateEnvironment:
    """Filesystem inputs a future launcher would supply.

    Every field is optional.  An absent field is a *materialization failure*,
    not a skipped check: the gate blocks rather than assuming the launcher
    would have got it right.
    """

    checkout_root: Path | None = None
    guard_dir: Path | None = None
    scratch_cache_root: Path | None = None
    evidence_dir: Path | None = None
    python_executable: Path | None = None
    git_executable: Path | None = None
    repository_root: Path | None = None
    environment_names: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Path helpers.  Nothing here ever emits a path.
# ---------------------------------------------------------------------------


def path_digest(path: Path | str) -> str:
    """SHA-256 of a normalized path string, so paths correlate without leaking."""
    text = str(path).replace("\\", "/")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _link_state(path: Path) -> str:
    """Classify a path as 'absent', 'ambiguous', 'file', or 'dir'.

    'ambiguous' covers symlinks and Windows directory junctions: any path whose
    real location differs from its literal one.  A reproduction gate cannot
    hash what it cannot name unambiguously, so ambiguity is a failure.
    """
    try:
        if path.is_symlink():
            return "ambiguous"
        if not path.exists():
            return "absent"
        if os.path.realpath(path) != os.path.abspath(path):
            return "ambiguous"
        return "dir" if path.is_dir() else "file"
    except OSError:
        return "ambiguous"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(child)).relative_to(Path(os.path.realpath(parent)))
    except (ValueError, OSError):
        return False
    return True


# ---------------------------------------------------------------------------
# Category checks
# ---------------------------------------------------------------------------


def _materialization_consistent(row: dict[str, Any]) -> str | None:
    """Check a row's declared EOL materialization against its own hashes.

    Git blobs in this closure are LF-only.  An ``lf`` row must therefore
    materialize to exactly the Git-object bytes, and a ``crlf`` row must
    materialize to strictly more bytes with a different digest.  This is a real
    internal invariant, not a restatement.
    """
    mode = row["materialization"]
    if mode == "lf":
        if row["expected_sha256"] != row["git_object_sha256"]:
            return "lf materialization must equal the Git-object digest"
        if row["expected_bytes"] != row["git_object_bytes"]:
            return "lf materialization must equal the Git-object byte count"
        return None
    if row["expected_sha256"] == row["git_object_sha256"]:
        return "crlf materialization must differ from the Git-object digest"
    if row["expected_bytes"] <= row["git_object_bytes"]:
        return "crlf materialization must be larger than the Git-object byte count"
    return None


def check_source_closure(
    manifest: dict[str, Any], environment: GateEnvironment
) -> list[Finding]:
    findings: list[Finding] = []
    category = "source_or_materialization"
    closure = manifest["source_closure"]
    rows: list[dict[str, Any]] = closure["files"]

    if closure["file_count"] != len(rows):
        findings.append(
            Finding(category, "closure_count_mismatch", "declared file_count does not equal the number of rows")
        )
    for row in rows:
        problem = _materialization_consistent(row)
        if problem is not None:
            findings.append(
                Finding(
                    category,
                    "materialization_inconsistent",
                    f"{row['path']}: {problem}",
                )
            )

    root = environment.checkout_root
    if root is None:
        findings.append(
            Finding(category, "checkout_not_materialized", "no isolated historical checkout was supplied")
        )
        return sorted(findings)

    state = _link_state(root)
    if state != "dir":
        findings.append(
            Finding(category, "checkout_root_unusable", f"checkout root is {state}, not a real directory")
        )
        return sorted(findings)

    for row in rows:
        target = root / row["path"]
        # Defence in depth behind the schema's relative-path rule: confirm the
        # resolved location really is inside the isolated checkout, so no alias,
        # link, or platform path quirk can move verification outside it.
        if not _is_within(target, root):
            findings.append(Finding(category, "source_file_outside_checkout", row["path"]))
            continue
        target_state = _link_state(target)
        if target_state == "absent":
            findings.append(Finding(category, "source_file_absent", row["path"]))
            continue
        if target_state != "file":
            findings.append(
                Finding(category, "source_file_ambiguous", f"{row['path']}: {target_state}")
            )
            continue
        try:
            digest, size = _hash_file(target)
        except OSError:
            findings.append(Finding(category, "source_file_unreadable", row["path"]))
            continue
        if digest != row["expected_sha256"] or size != row["expected_bytes"]:
            findings.append(
                Finding(category, "source_file_hash_mismatch", row["path"])
            )
    return sorted(findings)


def check_input_artifact(
    manifest: dict[str, Any], environment: GateEnvironment
) -> list[Finding]:
    findings: list[Finding] = []
    category = "input_artifact"
    artifact = manifest["input_artifact"]

    if artifact["regenerated_artifact_eligible"] is not False:
        findings.append(
            Finding(
                category,
                "regenerated_m52_declared_eligible",
                "the regenerated M52 artifact is permanently ineligible as the M53 handoff",
            )
        )
    classification = artifact["classification"]
    if list(classification) != list(FROZEN_REFERENCE["input_artifact_classification"]):
        findings.append(
            Finding(
                category,
                "m52_classification_altered",
                "M52 must remain EXACT_BYTE_REPRODUCTION_FAIL plus RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH",
            )
        )
    if not _is_sha256(artifact["sha256"]):
        findings.append(Finding(category, "m52_digest_malformed", "handoff digest is not a SHA-256"))
    if artifact["bytes"] <= 0:
        findings.append(Finding(category, "m52_size_invalid", "handoff byte count must be positive"))

    root = environment.checkout_root
    if root is None:
        findings.append(
            Finding(category, "handoff_not_materialized", "no checkout was supplied to hold the M52 handoff")
        )
        return sorted(findings)

    target = root / artifact["relative_path"]
    if not _is_within(target, root):
        findings.append(
            Finding(category, "handoff_outside_checkout", "the M52 handoff path resolves outside the checkout")
        )
        return sorted(findings)
    state = _link_state(target)
    if state == "absent":
        findings.append(Finding(category, "handoff_absent", "the M52 handoff copy is not present"))
        return sorted(findings)
    if state != "file":
        findings.append(Finding(category, "handoff_ambiguous", f"M52 handoff path is {state}"))
        return sorted(findings)
    try:
        digest, size = _hash_file(target)
    except OSError:
        findings.append(Finding(category, "handoff_unreadable", "the M52 handoff copy could not be read"))
        return sorted(findings)
    if digest != artifact["sha256"] or size != artifact["bytes"]:
        findings.append(
            Finding(category, "handoff_hash_mismatch", "the M52 handoff copy is not the frozen historical artifact")
        )
    return sorted(findings)


def check_parameters(manifest: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    category = "parameters"
    parameters = manifest["parameters"]
    if parameters["record_count"] <= 0:
        findings.append(Finding(category, "record_count_invalid", "record_count must be positive"))
    if parameters["record_bytes"] <= 0:
        findings.append(Finding(category, "record_bytes_invalid", "record_bytes must be positive"))
    if not _is_sha256(parameters["digest_sha256"]):
        findings.append(Finding(category, "digest_malformed", "parameter digest is not a SHA-256"))
    return sorted(findings)


def check_caches_and_isolation(
    manifest: dict[str, Any], environment: GateEnvironment
) -> list[Finding]:
    findings: list[Finding] = []
    category = "cache_or_isolation"
    caches = manifest["caches"]
    isolation = manifest["isolation"]
    trees: list[dict[str, Any]] = caches["trees"]

    if caches["fetch_missing"] is not False:
        findings.append(
            Finding(category, "fetch_missing_enabled", "a cache miss must never be allowed to trigger a fetch")
        )
    if caches["original_exposure_forbidden"] is not True:
        findings.append(
            Finding(category, "original_exposure_permitted", "original cache trees must never be exposed to executable code")
        )
    if isolation["scratch_root_required"] is not True:
        findings.append(
            Finding(category, "scratch_root_not_required", "a scratch cache root must be required")
        )
    if isolation["preserved_scratch_evidence_untouched"] is not True:
        findings.append(
            Finding(category, "preserved_evidence_not_protected", "preserved Phase 2B-1 scratch evidence must remain untouched")
        )
    if not isolation["original_cache_roots"]:
        findings.append(
            Finding(
                category,
                "original_cache_roots_undeclared",
                "at least one original cache root must be declared or the overlap check verifies nothing",
            )
        )
    for tree in trees:
        if tree["files"] <= 0 or tree["bytes"] <= 0:
            findings.append(Finding(category, "cache_tree_counts_invalid", tree["role"]))

    scratch = environment.scratch_cache_root
    if scratch is None:
        findings.append(
            Finding(category, "scratch_caches_not_materialized", "no scratch cache root was supplied")
        )
        return sorted(findings)

    state = _link_state(scratch)
    if state != "dir":
        findings.append(
            Finding(category, "scratch_root_unusable", f"scratch cache root is {state}, not a real directory")
        )
        return sorted(findings)

    # Original-cache exposure.  The original trees live under the repository
    # working tree.  A scratch root that sits inside the repository, or that
    # contains (or is contained by) a declared original cache root, would put
    # the real caches within reach of the historical command.
    repository_root = environment.repository_root
    if repository_root is not None and _link_state(repository_root) == "dir":
        if _is_within(scratch, repository_root):
            findings.append(
                Finding(category, "scratch_root_inside_repository", "scratch caches must live outside the repository working tree")
            )
        for original_root in isolation["original_cache_roots"]:
            original = repository_root / original_root
            if _is_within(original, scratch) or _is_within(scratch, original):
                findings.append(
                    Finding(category, "original_cache_root_overlap", "the scratch root overlaps a declared original cache root")
                )
                break
        if environment.checkout_root is not None and _is_within(environment.checkout_root, repository_root):
            findings.append(
                Finding(category, "checkout_inside_repository", "the historical checkout must be isolated from the repository working tree")
            )

    for tree in trees:
        subtree = scratch / tree["role"]
        subtree_state = _link_state(subtree)
        if subtree_state == "absent":
            findings.append(Finding(category, "scratch_tree_absent", tree["role"]))
            continue
        if subtree_state != "dir":
            findings.append(
                Finding(category, "scratch_tree_ambiguous", f"{tree['role']}: {subtree_state}")
            )
            continue
        measured = measure_tree(subtree)
        if measured is None:
            findings.append(Finding(category, "scratch_tree_unreadable", tree["role"]))
            continue
        files, total_bytes, aggregate = measured
        if files != tree["files"] or total_bytes != tree["bytes"] or aggregate != tree["aggregate_sha256"]:
            findings.append(Finding(category, "scratch_tree_digest_mismatch", tree["role"]))
    return sorted(findings)


def measure_tree(root: Path) -> tuple[int, int, str] | None:
    """Count, size, and aggregate-digest one tree, reading bytes opaquely.

    The aggregate is SHA-256 over LF-joined, lexicographically sorted lines
    ``SHA256(relative_path_utf8):byte_size:SHA256(file_bytes)`` — the Phase 2A
    algorithm, unspaced as Phase 2B-1 confirmed the frozen values require.  File
    bytes are hashed, never decoded, parsed, or retained.
    """
    lines: list[str] = []
    total_bytes = 0
    count = 0
    try:
        for current, directory_names, file_names in os.walk(root):
            directory_names.sort()
            for name in sorted(file_names):
                target = Path(current) / name
                if target.is_symlink():
                    return None
                data = target.read_bytes()
                relative = target.relative_to(root).as_posix()
                lines.append(
                    "%s:%d:%s"
                    % (
                        hashlib.sha256(relative.encode("utf-8")).hexdigest(),
                        len(data),
                        hashlib.sha256(data).hexdigest(),
                    )
                )
                total_bytes += len(data)
                count += 1
    except OSError:
        return None
    joined = "\n".join(sorted(lines))
    return count, total_bytes, hashlib.sha256(joined.encode("utf-8")).hexdigest()


def check_guard_and_executables(
    manifest: dict[str, Any], environment: GateEnvironment
) -> list[Finding]:
    findings: list[Finding] = []
    category = "guard_or_executable"
    guard = manifest["guard"]
    executables = manifest["executables"]

    if guard["scope"] != FROZEN_REFERENCE["guard_scope"]:
        findings.append(
            Finding(category, "guard_scope_misdescribed", "the guard is a process-scoped Python guard, not an OS sandbox")
        )
    if guard["external_pre_launch_verification_required"] is not True:
        findings.append(
            Finding(category, "external_verification_not_required", "a trusted external pre-launch hash check of both guard files is mandatory")
        )
    if guard["ready_event"] != FROZEN_REFERENCE["guard_ready_event"]:
        findings.append(
            Finding(category, "ready_event_wrong", "guard_ready must be required before the target import")
        )
    if guard["terminal_event"] != FROZEN_REFERENCE["guard_terminal_event"]:
        findings.append(
            Finding(category, "terminal_event_wrong", "a terminal normal_interpreter_exit record must be required")
        )
    if guard["post_run_verifier"] != FROZEN_REFERENCE["guard_post_run_verifier"]:
        findings.append(
            Finding(category, "post_run_verifier_wrong", "verify_guard_run() must be required after termination")
        )
    if guard["token"] != FROZEN_REFERENCE["guard_token"]:
        findings.append(Finding(category, "guard_token_wrong", "the guard activation handshake token is wrong"))

    missing_required = sorted(set(REQUIRED_GUARD_ENVIRONMENT) - set(guard["required_environment"]))
    for name in missing_required:
        findings.append(Finding(category, "guard_environment_missing", name))
    missing_forbidden = sorted(set(FORBIDDEN_GUARD_ENVIRONMENT) - set(guard["forbidden_environment"]))
    for name in missing_forbidden:
        findings.append(Finding(category, "guard_bypass_not_forbidden", name))
    missing_cleared = sorted(set(REQUIRED_CLEARED_ENVIRONMENT) - set(guard["cleared_environment"]))
    for name in missing_cleared:
        findings.append(Finding(category, "credential_or_proxy_not_cleared", name))

    if executables["python_content_pin_required"] is not True:
        findings.append(
            Finding(category, "python_pin_not_required", "the Python executable must be pinned by file-byte hash")
        )
    if executables["git_content_pin_required"] is not True:
        findings.append(
            Finding(category, "git_pin_not_required", "the Git executable must be pinned by file-byte hash")
        )

    for row in guard["files"]:
        problem = _materialization_consistent(row)
        if problem is not None:
            findings.append(
                Finding(category, "guard_materialization_inconsistent", f"{row['path']}: {problem}")
            )

    for name in sorted(FORBIDDEN_GUARD_ENVIRONMENT):
        if name in environment.environment_names:
            findings.append(Finding(category, "guard_bypass_variable_present", name))

    guard_dir = environment.guard_dir
    if guard_dir is None:
        findings.append(
            Finding(category, "guard_not_materialized", "no guard directory was supplied")
        )
    else:
        state = _link_state(guard_dir)
        if state != "dir":
            findings.append(
                Finding(category, "guard_dir_unusable", f"guard directory is {state}, not a real directory")
            )
        else:
            if environment.checkout_root is not None and (
                _is_within(guard_dir, environment.checkout_root)
                or _is_within(environment.checkout_root, guard_dir)
            ):
                findings.append(
                    Finding(category, "guard_dir_overlaps_checkout", "the guard directory must be outside the historical checkout")
                )
            for row in guard["files"]:
                target = guard_dir / Path(row["path"]).name
                target_state = _link_state(target)
                if target_state == "absent":
                    findings.append(Finding(category, "guard_file_absent", Path(row["path"]).name))
                    continue
                if target_state != "file":
                    findings.append(
                        Finding(category, "guard_file_ambiguous", f"{Path(row['path']).name}: {target_state}")
                    )
                    continue
                try:
                    digest, size = _hash_file(target)
                except OSError:
                    findings.append(Finding(category, "guard_file_unreadable", Path(row["path"]).name))
                    continue
                if digest != row["expected_sha256"] or size != row["expected_bytes"]:
                    findings.append(
                        Finding(category, "guard_file_hash_mismatch", Path(row["path"]).name)
                    )

    findings.extend(_check_executable(category, environment.python_executable, "python"))
    findings.extend(_check_executable(category, environment.git_executable, "git"))
    return sorted(findings)


def _check_executable(category: str, executable: Path | None, label: str) -> list[Finding]:
    if executable is None:
        return [
            Finding(category, "executable_not_pinned", f"no {label} executable was supplied for content pinning")
        ]
    # A symlinked interpreter is normal for a virtual environment: resolve it
    # and pin the target's bytes rather than refusing it.  Content, not path
    # shape, is the control.
    try:
        resolved = Path(os.path.realpath(executable))
    except OSError:
        return [Finding(category, "executable_unresolvable", label)]
    if not resolved.is_file():
        return [Finding(category, "executable_absent", label)]
    try:
        resolved.read_bytes()
    except OSError:
        return [Finding(category, "executable_unreadable", label)]
    return []


def check_output_criterion(manifest: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    category = "output_criterion"
    criterion = manifest["output_criterion"]

    if not _is_sha256(criterion["complete_sha256"]):
        findings.append(Finding(category, "complete_digest_malformed", "complete artifact digest is not a SHA-256"))
    if not _is_sha256(criterion["canonical_payload_sha256"]):
        findings.append(Finding(category, "canonical_digest_malformed", "canonical payload digest is not a SHA-256"))
    if criterion["complete_bytes"] <= 0 or criterion["canonical_payload_bytes"] <= 0:
        findings.append(Finding(category, "criterion_sizes_invalid", "criterion byte counts must be positive"))
    elif criterion["canonical_payload_bytes"] >= criterion["complete_bytes"]:
        findings.append(
            Finding(category, "canonical_payload_not_smaller", "removing the excluded pointer must shrink the payload")
        )
    if criterion["complete_sha256"] == criterion["canonical_payload_sha256"]:
        findings.append(
            Finding(category, "criterion_digests_identical", "raw-byte and canonical-payload digests must be distinct")
        )
    if criterion["raw_and_canonical_reported_separately"] is not True:
        findings.append(
            Finding(
                category,
                "classifications_merged",
                "raw-byte equality and canonical-payload equality remain separate classifications",
            )
        )
    exclusions = criterion["canonical_exclusions"]
    if list(exclusions) != list(FROZEN_REFERENCE["output_canonical_exclusions"]):
        findings.append(
            Finding(category, "exclusion_set_altered", "/generated_at is the sole canonical exclusion")
        )
    return sorted(findings)


def check_evidence_configuration(
    manifest: dict[str, Any], environment: GateEnvironment
) -> list[Finding]:
    findings: list[Finding] = []
    category = "evidence_configuration"
    command = manifest["execution_command"]
    output_paths = manifest["output_paths"]

    if list(command["argv_after_module"]):
        findings.append(
            Finding(category, "unexpected_command_argument", "the frozen command takes no argument after the module name")
        )
    if output_paths["evidence_log_basename"] != FROZEN_REFERENCE["evidence_log_basename"]:
        findings.append(
            Finding(category, "evidence_basename_wrong", "the guard evidence log basename is fixed")
        )

    evidence_dir = environment.evidence_dir
    if evidence_dir is None:
        findings.append(
            Finding(category, "evidence_dir_not_supplied", "no evidence directory was supplied")
        )
    else:
        state = _link_state(evidence_dir)
        if state != "dir":
            findings.append(
                Finding(category, "evidence_dir_unusable", f"evidence directory is {state}, not a real directory")
            )
        elif not evidence_dir.is_absolute():
            findings.append(
                Finding(category, "evidence_dir_not_absolute", "the evidence directory must be an absolute path")
            )
        else:
            log_state = _link_state(evidence_dir / output_paths["evidence_log_basename"])
            if log_state != "absent":
                findings.append(
                    Finding(category, "evidence_log_pre_exists", "a guard evidence log already exists at the target path")
                )

    checkout_root = environment.checkout_root
    if checkout_root is not None and _link_state(checkout_root) == "dir":
        artifact_state = _link_state(checkout_root / output_paths["artifact_relative_path"])
        if artifact_state != "absent":
            findings.append(
                Finding(category, "output_artifact_pre_exists", "an M53 output artifact already exists in the checkout")
            )
    return sorted(findings)


# ---------------------------------------------------------------------------
# Frozen-reference reconciliation
# ---------------------------------------------------------------------------


def reconcile_with_frozen_reference(manifest: dict[str, Any]) -> list[Finding]:
    """Compare an ``M53_PHASE2B2A`` manifest to the merged Phase 2A/2B record.

    A synthetic manifest is exempt from this comparison and can never be
    presented as the real one: its profile is recorded on every evidence record.
    """
    if manifest.get("profile") != PROFILE_M53:
        return []
    findings: list[Finding] = []
    reference = FROZEN_REFERENCE

    def mismatch(category: str, code: str, detail: str) -> None:
        findings.append(Finding(category, code, detail))

    if manifest["historical_commit"] != reference["historical_commit"]:
        mismatch("source_or_materialization", "historical_commit_mismatch", "the historical M53 commit does not match the reviewed commit")

    closure = manifest["source_closure"]
    if closure["file_count"] != reference["source_closure_file_count"]:
        mismatch("source_or_materialization", "closure_size_changed", "the reviewed source closure has exactly 30 files")
    crlf_rows = [row for row in closure["files"] if row["materialization"] == "crlf"]
    if len(crlf_rows) != reference["source_closure_crlf_file_count"]:
        mismatch("source_or_materialization", "crlf_policy_changed", "the reviewed materialization policy has exactly two CRLF files")

    artifact = manifest["input_artifact"]
    if artifact["sha256"] != reference["input_artifact_sha256"]:
        mismatch("input_artifact", "m52_handoff_not_historical", "only the original frozen historical M52 artifact is eligible")
    if artifact["bytes"] != reference["input_artifact_bytes"]:
        mismatch("input_artifact", "m52_handoff_size_mismatch", "the historical M52 artifact is 34124 bytes")
    if artifact["relative_path"] != reference["input_artifact_relative_path"]:
        mismatch("input_artifact", "m52_handoff_path_changed", "the M53 wrapper reads a fixed M52 path")

    parameters = manifest["parameters"]
    if parameters["record_count"] != reference["parameter_record_count"]:
        mismatch("parameters", "parameter_count_changed", "the reviewed record has exactly 49 parameters")
    if parameters["record_bytes"] != reference["parameter_record_bytes"]:
        mismatch("parameters", "parameter_bytes_changed", "the reviewed parameter serialization is 5018 bytes")
    if parameters["digest_sha256"] != reference["parameter_digest_sha256"]:
        mismatch("parameters", "parameter_digest_mismatch", "the parameter-record digest does not match the reviewed value")

    criterion = manifest["output_criterion"]
    if criterion["complete_sha256"] != reference["output_complete_sha256"]:
        mismatch("output_criterion", "complete_digest_mismatch", "the expected complete M53 artifact digest changed")
    if criterion["complete_bytes"] != reference["output_complete_bytes"]:
        mismatch("output_criterion", "complete_size_mismatch", "the expected complete M53 artifact size changed")
    if criterion["canonical_payload_sha256"] != reference["output_canonical_payload_sha256"]:
        mismatch("output_criterion", "canonical_digest_mismatch", "the expected canonical payload digest changed")
    if criterion["canonical_payload_bytes"] != reference["output_canonical_payload_bytes"]:
        mismatch("output_criterion", "canonical_size_mismatch", "the expected canonical payload size changed")

    guard_rows = {row["path"]: row for row in manifest["guard"]["files"]}
    for path, expected in reference["guard_files"].items():
        row = guard_rows.get(path)
        if row is None:
            mismatch("guard_or_executable", "guard_file_missing_from_manifest", Path(path).name)
            continue
        if row["git_object_sha256"] != expected["git_object_sha256"]:
            mismatch("guard_or_executable", "guard_file_digest_mismatch", Path(path).name)
        if row["git_object_bytes"] != expected["git_object_bytes"]:
            mismatch("guard_or_executable", "guard_file_size_mismatch", Path(path).name)
        if row["materialization"] != expected["materialization"]:
            mismatch("guard_or_executable", "guard_file_materialization_mismatch", Path(path).name)
    for path in sorted(set(guard_rows) - set(reference["guard_files"])):
        mismatch("guard_or_executable", "unexpected_guard_file", Path(path).name)

    cache_rows = {row["role"]: row for row in manifest["caches"]["trees"]}
    for role, expected_tree in reference["cache_trees"].items():
        row = cache_rows.get(role)
        if row is None:
            mismatch("cache_or_isolation", "cache_tree_missing_from_manifest", role)
            continue
        if row["repo_relative_root"] != expected_tree["repo_relative_root"]:
            mismatch("cache_or_isolation", "cache_root_changed", role)
        if row["files"] != expected_tree["files"]:
            mismatch("cache_or_isolation", "cache_file_count_mismatch", role)
        if row["bytes"] != expected_tree["bytes"]:
            mismatch("cache_or_isolation", "cache_byte_total_mismatch", role)
        if row["aggregate_sha256"] != expected_tree["aggregate_sha256"]:
            mismatch("cache_or_isolation", "cache_digest_mismatch", role)
    for role in sorted(set(cache_rows) - set(reference["cache_trees"])):
        mismatch("cache_or_isolation", "unexpected_cache_tree", role)

    command = manifest["execution_command"]
    if command["module"] != reference["command_module"]:
        mismatch("evidence_configuration", "command_module_changed", "the frozen command module changed")
    if manifest["output_paths"]["artifact_relative_path"] != reference["output_artifact_relative_path"]:
        mismatch("output_criterion", "output_path_changed", "the M53 output artifact path changed")

    return sorted(findings)


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def decide(findings: list[Finding]) -> str:
    """Map findings to one decision using the fixed category precedence."""
    present = {finding.category for finding in findings}
    for category, decision in CATEGORY_DECISIONS:
        if category in present:
            return decision
    return DECISION_READY


def evaluate(
    manifest: dict[str, Any],
    environment: GateEnvironment | None = None,
) -> dict[str, Any]:
    """Run every check and return the deterministic preflight evidence record.

    This function performs no execution of any kind.  ``execution_authorized``
    and ``m53_executed`` are literal ``False`` here and nowhere else in this
    module is either name assigned.
    """
    environment = environment or GateEnvironment()
    try:
        return _evaluate_checked(manifest, environment)
    except Exception as exc:  # noqa: BLE001 - any internal failure must fail closed
        # An unexpected internal failure must still produce a blocked evidence
        # record, never a traceback with no record and never a partially
        # successful one.  Only the exception *type* is recorded: a message can
        # carry a filesystem path, and evidence must stay leak-free.
        return _build_record(
            None,
            [Finding("evidence_configuration", "internal_error", type(exc).__name__)],
            environment,
            schema_valid=False,
        )


def _evaluate_checked(
    manifest: dict[str, Any],
    environment: GateEnvironment,
) -> dict[str, Any]:
    schema_problems = validate_manifest_schema(manifest)
    if schema_problems:
        findings = [
            Finding("evidence_configuration", "manifest_schema_invalid", problem)
            for problem in schema_problems
        ]
        return _build_record(None, findings, environment, schema_valid=False)

    findings: list[Finding] = []
    findings.extend(check_source_closure(manifest, environment))
    findings.extend(check_input_artifact(manifest, environment))
    findings.extend(check_parameters(manifest))
    findings.extend(check_caches_and_isolation(manifest, environment))
    findings.extend(check_guard_and_executables(manifest, environment))
    findings.extend(check_output_criterion(manifest))
    findings.extend(check_evidence_configuration(manifest, environment))
    findings.extend(reconcile_with_frozen_reference(manifest))
    return _build_record(manifest, sorted(set(findings)), environment, schema_valid=True)


def _build_record(
    manifest: dict[str, Any] | None,
    findings: list[Finding],
    environment: GateEnvironment,
    *,
    schema_valid: bool,
) -> dict[str, Any]:
    counts = {category: 0 for category in CATEGORIES}
    for finding in findings:
        counts[finding.category] += 1
    record: dict[str, Any] = {
        "record_kind": "m53_execution_gate_preflight",
        "schema_version": SCHEMA_VERSION,
        "mode": "verification_only",
        "execution_authorized": False,
        "m53_executed": False,
        "manifest_schema_valid": schema_valid,
        "manifest_profile": (manifest or {}).get("profile"),
        "decision": decide(findings),
        "finding_counts": counts,
        "findings": [finding.as_record() for finding in findings],
        "supplied_input_digests": {
            "checkout_root": _optional_digest(environment.checkout_root),
            "evidence_dir": _optional_digest(environment.evidence_dir),
            "git_executable": _optional_digest(environment.git_executable),
            "guard_dir": _optional_digest(environment.guard_dir),
            "python_executable": _optional_digest(environment.python_executable),
            "repository_root": _optional_digest(environment.repository_root),
            "scratch_cache_root": _optional_digest(environment.scratch_cache_root),
        },
    }
    return record


def _optional_digest(path: Path | None) -> str | None:
    return None if path is None else path_digest(path)


def render_record(record: dict[str, Any]) -> str:
    """Serialize the evidence record deterministically (no timestamp anywhere)."""
    return (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verification-only M53 reproduction preflight gate. It never imports, "
            "launches, or authorizes M53, and offers no option that could."
        )
    )
    parser.add_argument("--manifest", required=True, help="path to the frozen gate manifest JSON")
    parser.add_argument("--checkout-root", default=None, help="isolated historical M53 checkout")
    parser.add_argument("--guard-dir", default=None, help="isolated network-guard directory")
    parser.add_argument("--scratch-cache-root", default=None, help="root holding scratch cache copies")
    parser.add_argument("--evidence-dir", default=None, help="directory that will hold the guard evidence log")
    parser.add_argument("--python-executable", default=None, help="interpreter to pin by content hash")
    parser.add_argument("--git-executable", default=None, help="Git executable to pin by content hash")
    parser.add_argument("--repository-root", default=None, help="repository working tree the scratch copies must stay outside of")
    parser.add_argument("--output", default=None, help="write the evidence record here instead of stdout")
    return parser


def _optional_path(value: str | None) -> Path | None:
    return None if value is None else Path(value)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest_text(Path(args.manifest).read_text(encoding="utf-8"))
    except (ManifestError, OSError, UnicodeDecodeError) as exc:
        record = _build_record(
            None,
            [Finding("evidence_configuration", "manifest_unreadable", type(exc).__name__)],
            GateEnvironment(),
            schema_valid=False,
        )
    else:
        environment = GateEnvironment(
            checkout_root=_optional_path(args.checkout_root),
            guard_dir=_optional_path(args.guard_dir),
            scratch_cache_root=_optional_path(args.scratch_cache_root),
            evidence_dir=_optional_path(args.evidence_dir),
            python_executable=_optional_path(args.python_executable),
            git_executable=_optional_path(args.git_executable),
            repository_root=_optional_path(args.repository_root),
            environment_names=frozenset(os.environ),
        )
        record = evaluate(manifest, environment)

    text = render_record(record)
    if args.output:
        # Write-then-replace, so a reader never observes a half-written record.
        # os.replace is atomic within a filesystem; it is not a durability
        # guarantee across a power loss, and nothing here claims one.
        destination = Path(args.output)
        staging = destination.with_name(destination.name + ".partial")
        staging.write_text(text, encoding="utf-8", newline="\n")
        os.replace(staging, destination)
    else:
        sys.stdout.write(text)
    return 0 if record["decision"] == DECISION_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
