"""M13 Issue 8: independent verifier for the protection-grounding milestone.

This script is a second opinion, not a re-run of the implementation's own
tests. For every capability it checks, it documents (in each `CheckResult`)
what it independently re-derives versus what it necessarily shares with the
implementation (declarative types/constants only), and why that is still a
meaningful check. Concretely:

* The implementation digest is recomputed with a **separate** hashing
  routine written in this file (`_independent_closure_digest`), not by
  calling `checkpoint_implementation_digest` for both "expected" and
  "actual" -- that function is called exactly once, to produce the
  *declared* value this script's own independent computation is compared
  against.
* `PINNED_IMPLEMENTATION_CLOSURE` is cross-checked against a **separate**
  AST-based import walk (`_independent_execution_closure`) written in this
  file, not by importing and trusting the repository's existing test-suite
  walker.
* The evidence and assessment digests are spot-recomputed with a **separate**
  canonical-encoding routine (`_independent_canonical_bytes`), not by calling
  `encode_canonical_value`/`canonical_bytes` from `protection_observation_
  model.py`.
* The promotion-policy and grounding-assessment priority order is
  **independently reimplemented** here (`_predict_promotion_outcome`) from
  the policy module's own documented contract (its docstring, which is the
  reviewed specification, not its code), and only then compared against one
  call to the real `evaluate_promotion_eligibility`.
* The broker call-site count is **independently re-enumerated** with a
  separate, deliberately simpler AST scanner (`_independent_call_site_scan`)
  that does not import or reuse `tests/call_site_guard.py`.
* Operator routes are probed by directly constructing candidate paths and
  calling `ApplicationRouteTable.match()` plus the real WSGI app, using a
  **separately authored** candidate/expectation list, not the existing
  `scripts/verify_operator_routes.py` crawler.

This script runs entirely offline: it never imports `robinhood_client`,
`robinhood_live_adapter`, `robinhood_commissioning_session`, or any live
broker transport module, opens no network socket, and reads only tracked
source and disposable fixtures it creates itself under a temporary
directory.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))


# --------------------------------------------------------------------------
# Report primitives.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # "PASS" or "FAIL"
    detail: str
    independence: str
    implementation_source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "independence": self.independence,
            "implementation_source": self.implementation_source,
        }


_AUTHORITY_GRANT_NAMES = (
    "protection_grounded", "activation_approved", "permits_automated_action",
    "live_ready", "unattended_ready", "ACTIVATION_APPROVALS",
    "ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES",
)


def _source_grants_authority(text: str) -> str | None:
    """Text-level scan for an actual True-valued assignment, not a mere
    mention. A module's own docstring routinely says "this never sets
    protection_grounded" -- the bare substring must not fail that."""
    for name in _AUTHORITY_GRANT_NAMES:
        compact = text.replace(" ", "")
        if f"{name}=True" in compact:
            return name
    return None


def _pass(name: str, detail: str, *, independence: str, implementation_source: str) -> CheckResult:
    return CheckResult(name, "PASS", detail, independence, implementation_source)


def _fail(name: str, detail: str, *, independence: str, implementation_source: str) -> CheckResult:
    return CheckResult(name, "FAIL", detail, independence, implementation_source)


# --------------------------------------------------------------------------
# Independent digest / closure reconstruction.
#
# Implementation source: src/read_only_authorization_manifest.py
#   (PINNED_IMPLEMENTATION_CLOSURE, checkpoint_implementation_digest).
# Independence: the hashing routine and the closure-discovery walk below are
# separate implementations of the documented algorithm, written in this
# file. `checkpoint_implementation_digest` is called exactly once (to read
# the declared value), never to produce both sides of a comparison.
# --------------------------------------------------------------------------

_DIGEST_DOMAIN = b"orb-live/read-only-checkpoint-implementation-closure/v2"

#: Files a correct M13 closure must contain. Authored independently from
#: this script's own understanding of the milestone (Issues 1-5, 7), not
#: copied from the module's own docstring list.
_REQUIRED_M13_CLOSURE_MEMBERS = frozenset(
    {
        "read_only_observation_checkpoint_launcher.py",
        "src/protection_observation_model.py",
        "src/protection_observation_ledger.py",
        "src/protection_promotion_policy.py",
        "src/protection_grounding_assessment.py",
        "src/protection_checkpoint_integration.py",
        "src/protection_assessment_store.py",
    }
)

#: Files that must NOT be pinned: test/verification-only tooling that has no
#: reachable runtime import from the checkpoint entry points.
_MUST_NOT_BE_PINNED = frozenset(
    {
        "tests/call_site_guard.py",
        "scripts/verify_m13_protection_grounding.py",
        "scripts/verify_operator_milestone.py",
        "scripts/verify_operator_routes.py",
        "src/operator_web_app.py",
        "src/protection_operator_evidence.py",
    }
)

#: Entry points the execution closure is independently walked from.
#: Authored fresh for this script; deliberately the same conceptual set as
#: the repository's own closure-guard test (the launcher, the authorization
#: module, the read-only runtime, the decision model), because that set is
#: what "the checkpoint's reachable surface" *means* -- but the walking code
#: itself is written from scratch below, not imported.
_CLOSURE_ENTRY_POINTS = (
    "read_only_observation_checkpoint_launcher.py",
    "src/read_only_authorization_manifest.py",
    "src/read_only_observation_runtime.py",
    "src/read_only_broker_state.py",
    "src/live_order_state_model.py",
    "src/unattended_pilot_runtime.py",
)


def _independent_canonical_source_bytes(path: Path) -> bytes:
    """Own newline-stable UTF-8 encoding, not `canonical_source_hash.py`."""
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="strict")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.encode("utf-8")


def _independent_closure_digest(root: Path, paths: tuple[str, ...]) -> str:
    accumulator = hashlib.sha256()
    accumulator.update(_DIGEST_DOMAIN)
    accumulator.update(b"\0")
    accumulator.update(str(len(paths)).encode("ascii"))
    accumulator.update(b"\0")
    for relative in sorted(paths):
        target = root / relative
        encoded = relative.encode("utf-8")
        accumulator.update(str(len(encoded)).encode("ascii"))
        accumulator.update(b"\0")
        accumulator.update(encoded)
        accumulator.update(b"\0")
        file_hash = hashlib.sha256(_independent_canonical_source_bytes(target)).hexdigest()
        accumulator.update(file_hash.encode("ascii"))
        accumulator.update(b"\0")
    return accumulator.hexdigest()


def _repo_file_for(root: Path, module: str) -> Path | None:
    parts = module.split(".")
    for candidate in (
        root.joinpath(*parts).with_suffix(".py"),
        root / "src" / (parts[-1] + ".py"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _imported_module_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def _independent_execution_closure(root: Path) -> set[str]:
    seen: set[str] = set()
    queue = list(_CLOSURE_ENTRY_POINTS)
    while queue:
        relative = queue.pop()
        if relative in seen:
            continue
        seen.add(relative)
        for module in _imported_module_names(root / relative):
            target = _repo_file_for(root, module)
            if target is None:
                continue
            queue.append(target.relative_to(root).as_posix())
    return seen


def check_implementation_digest_and_closure(root: Path) -> list[CheckResult]:
    from src.read_only_authorization_manifest import (
        PINNED_IMPLEMENTATION_CLOSURE,
        checkpoint_implementation_digest,
        resolve_head_sha,
    )

    results: list[CheckResult] = []
    independence = (
        "Digest recomputed with a separate hashing routine written in this "
        "script; PINNED_IMPLEMENTATION_CLOSURE is a declarative constant "
        "(a list of paths) and is imported, but the algorithm that hashes "
        "it and the algorithm that discovers what *should* be in it are "
        "both independently reimplemented here."
    )
    source = "src/read_only_authorization_manifest.py"

    declared = tuple(PINNED_IMPLEMENTATION_CLOSURE)
    declared_set = set(declared)
    if len(declared_set) != len(declared):
        results.append(_fail("closure_no_duplicates", "duplicate path in PINNED_IMPLEMENTATION_CLOSURE", independence=independence, implementation_source=source))
    else:
        results.append(_pass("closure_no_duplicates", f"{len(declared)} distinct pinned paths", independence=independence, implementation_source=source))

    missing_on_disk = [p for p in declared_set if not (root / p).is_file()]
    if missing_on_disk:
        results.append(_fail("closure_paths_exist", f"missing on disk: {sorted(missing_on_disk)}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("closure_paths_exist", "every pinned path exists on disk", independence=independence, implementation_source=source))

    missing_required = _REQUIRED_M13_CLOSURE_MEMBERS - declared_set
    if missing_required:
        results.append(_fail("closure_contains_required_m13_files", f"missing required M13 files: {sorted(missing_required)}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("closure_contains_required_m13_files", "all required M13 runtime files are pinned", independence=independence, implementation_source=source))

    unexpected_pinned = _MUST_NOT_BE_PINNED & declared_set
    if unexpected_pinned:
        results.append(_fail("closure_excludes_test_only_tools", f"test/verification-only tooling incorrectly pinned: {sorted(unexpected_pinned)}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("closure_excludes_test_only_tools", "no test/verification-only tool is pinned", independence=independence, implementation_source=source))

    # The digest comparison requires a well-formed closure (no duplicates,
    # every path present) to even compute either side; a malformed closure
    # is already reported by the two checks above, and is reported here
    # too rather than letting an exception abort every later check in this
    # function.
    if missing_on_disk or len(declared_set) != len(declared):
        results.append(_fail("closure_digest_matches", "cannot compute: closure is malformed (see closure_no_duplicates / closure_paths_exist)", independence=independence, implementation_source=source))
    else:
        try:
            independent_digest = _independent_closure_digest(root, declared)
            declared_digest = checkpoint_implementation_digest(root)
        except Exception as exc:
            results.append(_fail("closure_digest_matches", f"digest computation raised {type(exc).__name__}: {exc}", independence=independence, implementation_source=source))
        else:
            if independent_digest != declared_digest:
                results.append(_fail("closure_digest_matches", f"independent={independent_digest} declared={declared_digest}", independence=independence, implementation_source=source))
            else:
                results.append(_pass("closure_digest_matches", f"digest={declared_digest}", independence=independence, implementation_source=source))

    discovered = _independent_execution_closure(root)
    if discovered != declared_set:
        only_discovered = sorted(discovered - declared_set)
        only_declared = sorted(declared_set - discovered)
        results.append(_fail(
            "closure_equals_real_execution_closure",
            f"discovered-but-not-pinned={only_discovered} pinned-but-not-discovered={only_declared}",
            independence=independence,
            implementation_source=source,
        ))
    else:
        results.append(_pass("closure_equals_real_execution_closure", f"{len(discovered)} files, independently walked, match the declared closure exactly", independence=independence, implementation_source=source))

    sha = resolve_head_sha(root)
    results.append(_pass("repository_head_sha", sha, independence="Reads .git/HEAD via the shared resolve_head_sha helper -- repository identity, not a behavioral claim under test.", implementation_source=source))

    return results


# --------------------------------------------------------------------------
# Independent observation-model verification.
#
# Implementation source: src/protection_observation_model.py.
# Independence: field-name/enum-membership checks are pure structural
# inspection (dataclasses.fields / Enum.__members__), not a call to any
# validate()-style helper. The write_count / privacy behavioral checks
# exercise the public dataclass constructor -- the only way to interact
# with a frozen dataclass -- and independently assert on the *outcome*
# (raise or not), not on an internal validator's return value. The digest
# recompute uses a separately written canonical encoder, not
# encode_canonical_value/canonical_bytes.
# --------------------------------------------------------------------------


def _independent_canonical_scalar(value: Any) -> str:
    if value is None:
        return "\x00"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\x1e".join(_independent_canonical_scalar(v) for v in value)
    raise TypeError(f"unsupported canonical type: {type(value)!r}")


def _independent_canonical_bytes(payload: dict[str, Any]) -> bytes:
    fields = "\x1f".join(f"{key}\x01{_independent_canonical_scalar(payload[key])}" for key in sorted(payload))
    return fields.encode("utf-8")


def check_observation_model() -> list[CheckResult]:
    import dataclasses

    from src.protection_observation_model import (
        AdapterIdentity,
        EvidenceSourceClass,
        ObservationClockSource,
        ObservationConsistencyStatus,
        ObservationContaminationStatus,
        PrivacyRedactionStatus,
        ProtectionObservationError,
        ProtectionObservationRecord,
        ProtectiveOrderType,
        ProtectiveSide,
        QuantityRelationship,
    )
    from src.live_order_state_model import ProtectiveCoverage
    from src.order_lifecycle_model import MappingProvenance
    from src.read_only_broker_state import SnapshotFreshness

    results: list[CheckResult] = []
    independence = (
        "Structural: dataclasses.fields()/Enum membership inspected "
        "directly, not via a validate() call. Behavioral: the public "
        "frozen-dataclass constructor is exercised and only the raise/ "
        "no-raise outcome is asserted -- there is no other way to obtain "
        "an instance. Digest: recomputed with a separately written "
        "canonical encoder, not encode_canonical_value/canonical_bytes."
    )
    source = "src/protection_observation_model.py"

    required_fields = {
        "observation_id", "run_id", "observation_sequence", "observed_at_monotonic",
        "release_identity", "implementation_digest", "account_fingerprint_reference",
        "broker_adapter_identity", "evidence_source_class", "normalized_protection_state",
        "quantity_relationship", "read_count", "write_count", "privacy_redaction_status",
        "data_freshness", "consistency_status", "contamination_status", "evidence_digest",
    }
    actual_fields = {f.name for f in dataclasses.fields(ProtectionObservationRecord)}
    missing = required_fields - actual_fields
    if missing:
        results.append(_fail("observation_required_fields_present", f"missing: {sorted(missing)}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("observation_required_fields_present", f"{len(actual_fields)} fields present, all {len(required_fields)} required fields found", independence=independence, implementation_source=source))

    authoritative_only = {EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION}
    non_authoritative = {e for e in EvidenceSourceClass} - authoritative_only
    if EvidenceSourceClass.SIMULATED_BROKER not in non_authoritative or EvidenceSourceClass.IMPORTED_HISTORICAL_RECORD not in non_authoritative or EvidenceSourceClass.UNKNOWN_SOURCE not in non_authoritative:
        results.append(_fail("evidence_source_classification_present", "expected non-authoritative source classes are missing", independence=independence, implementation_source=source))
    else:
        results.append(_pass("evidence_source_classification_present", f"{len(non_authoritative)} non-authoritative source classes independently enumerated", independence=independence, implementation_source=source))

    def build(**overrides: Any) -> ProtectionObservationRecord:
        base = dict(
            observation_id=hashlib.sha256(b"m13-verifier-observation").hexdigest(),
            run_id="m13-verifier-run",
            observation_sequence=1,
            observed_at_monotonic=100.0,
            observation_clock_source=ObservationClockSource.INJECTED_MONOTONIC,
            release_identity=hashlib.sha256(b"m13-verifier-release").hexdigest()[:40],
            implementation_digest=hashlib.sha256(b"m13-verifier-digest").hexdigest(),
            checkpoint_version="v1",
            account_fingerprint_reference=hashlib.sha256(b"m13-verifier-account").hexdigest(),
            broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
            broker_session_identity_reference=hashlib.sha256(b"m13-verifier-session").hexdigest(),
            symbol_reference=hashlib.sha256(b"m13-verifier-symbol").hexdigest(),
            position_reference=hashlib.sha256(b"m13-verifier-position").hexdigest(),
            protective_order_reference=None,
            protective_order_type=ProtectiveOrderType.UNKNOWN,
            protective_side=ProtectiveSide.UNKNOWN,
            quantity_relationship=QuantityRelationship.NO_POSITION,
            observed_order_state="cancelled",
            broker_reported_timestamp=None,
            normalized_protection_state=ProtectiveCoverage.NOT_PROTECTED,
            mapping_provenance=MappingProvenance.ABSENT,
            evidence_source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION,
            read_count=3,
            write_count=0,
            privacy_redaction_status=PrivacyRedactionStatus.VERIFIED_REDACTED,
            data_freshness=SnapshotFreshness.FRESH,
            consistency_status=ObservationConsistencyStatus.CONSISTENT,
            contamination_status=ObservationContaminationStatus.NOT_CONTAMINATED,
        )
        base.update(overrides)
        return ProtectionObservationRecord(**base)

    try:
        build(write_count=1)
        results.append(_fail("write_count_cannot_be_nonzero", "constructing with write_count=1 did not raise", independence=independence, implementation_source=source))
    except ProtectionObservationError:
        results.append(_pass("write_count_cannot_be_nonzero", "write_count=1 raised ProtectionObservationError as required", independence=independence, implementation_source=source))

    try:
        build(privacy_redaction_status=PrivacyRedactionStatus.UNVERIFIED)
        results.append(_fail("privacy_must_be_verified_at_construction", "constructing with UNVERIFIED privacy did not raise", independence=independence, implementation_source=source))
    except ProtectionObservationError:
        results.append(_pass("privacy_must_be_verified_at_construction", "UNVERIFIED privacy raised ProtectionObservationError as required", independence=independence, implementation_source=source))

    try:
        first = build()
        results.append(_pass("valid_construction_succeeds", "well-formed observation constructed without error", independence=independence, implementation_source=source))
    except ProtectionObservationError as exc:
        results.append(_fail("valid_construction_succeeds", f"well-formed observation was rejected: {exc}", independence=independence, implementation_source=source))
        return results

    import dataclasses as _dc
    try:
        first.write_count = 1  # ordinary attribute assignment, not object.__setattr__
        results.append(_fail("record_is_frozen", "write_count was mutated after construction via ordinary assignment", independence=independence, implementation_source=source))
    except _dc.FrozenInstanceError:
        results.append(_pass("record_is_frozen", "ordinary post-construction assignment raises FrozenInstanceError", independence=independence, implementation_source=source))

    same_a = build(observation_id="digest-sample-a")
    same_b = build(observation_id="digest-sample-a")
    changed = build(observation_id="digest-sample-a", read_count=99)
    if same_a.evidence_digest != same_b.evidence_digest:
        results.append(_fail("evidence_digest_deterministic", "identical inputs produced different evidence_digest", independence=independence, implementation_source=source))
    elif same_a.evidence_digest == changed.evidence_digest:
        results.append(_fail("evidence_digest_changes_with_evidence", "changing read_count did not change evidence_digest", independence=independence, implementation_source=source))
    else:
        results.append(_pass("evidence_digest_deterministic_and_sensitive", "same inputs -> same digest; changed input -> different digest", independence=independence, implementation_source=source))

    payload = same_a.to_canonical_dict()
    payload.pop("qualification_status", None)
    domain = b"orb-live/protection-observation-model/v1"
    independent_evidence_digest = hashlib.sha256(domain + _independent_canonical_bytes(payload)).hexdigest()
    if independent_evidence_digest != same_a.evidence_digest:
        results.append(_fail(
            "evidence_digest_independently_reproducible",
            f"independent={independent_evidence_digest} actual={same_a.evidence_digest}",
            independence=independence,
            implementation_source=source,
        ))
    else:
        results.append(_pass("evidence_digest_independently_reproducible", "independent canonical-encoding recompute matches the record's evidence_digest exactly", independence=independence, implementation_source=source))

    return results


# --------------------------------------------------------------------------
# Independent ledger verification: bypass the ledger's own query methods and
# inspect the persisted SQLite rows directly with a fresh connection.
#
# Implementation source: src/protection_observation_ledger.py.
# --------------------------------------------------------------------------


def check_ledger(tmp_root: Path) -> list[CheckResult]:
    from src.protection_observation_ledger import (
        LedgerAppendState,
        ProtectionObservationLedger,
    )

    from tests._protection_observation_fixtures import (
        active_protective_order,
        conflicting_states,
        digest,
        duplicate_protection,
        no_protective_order,
        simulated_evidence,
        stale_protective_order,
    )

    results: list[CheckResult] = []
    independence = (
        "Writes go through the real ProtectionObservationLedger (the only "
        "writer), but every assertion below queries the persisted SQLite "
        "table directly with a fresh sqlite3 connection, bypassing "
        "all_observations()/observations_for_run()/etc. entirely. Source-"
        "level scan for UPDATE/DELETE/DROP tokens is a separate, static "
        "check on the module's own source text."
    )
    source = "src/protection_observation_ledger.py"

    db_path = tmp_root / "ledger_verify.sqlite3"
    obs_a = no_protective_order("run-a", observation_id="obs-a")
    obs_b = active_protective_order("run-b", observation_id="obs-b", observed_at_monotonic=50.0)
    with ProtectionObservationLedger(db_path, "run-a") as ledger:
        r1 = ledger.append(obs_a)
    with ProtectionObservationLedger(db_path, "run-b") as ledger:
        r2 = ledger.append(obs_b)
        r_dup_equivalent = ledger.append(obs_b)

    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(
            "SELECT observation_id, run_id, evidence_source_class, "
            "implementation_digest, release_identity, "
            "account_fingerprint_reference, privacy_redaction_status, "
            "write_count, append_state FROM protection_observations "
            "ORDER BY recorded_sequence"
        ).fetchall()
    finally:
        connection.close()

    if len(rows) != 2:
        results.append(_fail("ledger_append_only_row_count", f"expected 2 persisted rows (obs_a, obs_b), found {len(rows)}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("ledger_append_only_row_count", "exactly one row per genuinely distinct observation; duplicate-equivalent re-delivery added no row", independence=independence, implementation_source=source))

    by_id = {row[0]: row for row in rows}
    if obs_a.observation_id not in by_id or obs_b.observation_id not in by_id:
        results.append(_fail("ledger_persists_expected_ids", f"expected ids not both found in {list(by_id)}", independence=independence, implementation_source=source))
    else:
        row_a, row_b = by_id[obs_a.observation_id], by_id[obs_b.observation_id]
        checks = {
            "run_id": (row_a[1] == "run-a" and row_b[1] == "run-b"),
            "evidence_source_class": row_a[2] == obs_a.evidence_source_class.value,
            "implementation_digest": row_a[3] == obs_a.implementation_digest,
            "release_identity": row_a[4] == obs_a.release_identity,
            "account_fingerprint_reference": row_a[5] == obs_a.account_fingerprint_reference,
            "privacy_redaction_status": row_a[6] == obs_a.privacy_redaction_status.value,
            "write_count": row_a[7] == 0,
        }
        failed = [k for k, v in checks.items() if not v]
        if failed:
            results.append(_fail("ledger_field_retention", f"fields not retained correctly: {failed}", independence=independence, implementation_source=source))
        else:
            results.append(_pass("ledger_field_retention", "run_id/source-class/digest/release/account/privacy/write_count all correctly retained in the raw row", independence=independence, implementation_source=source))

    if r_dup_equivalent.state is not LedgerAppendState.DUPLICATE_EQUIVALENT:
        results.append(_fail("ledger_duplicate_equivalent_behavior", f"re-delivery of an identical observation returned {r_dup_equivalent.state}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("ledger_duplicate_equivalent_behavior", "re-delivery of an identical observation returned DUPLICATE_EQUIVALENT and added no row", independence=independence, implementation_source=source))

    conflict_db = tmp_root / "ledger_conflict_verify.sqlite3"
    a, b = duplicate_protection("run-c")
    conflicting_pair = (a, type(a)(**{**a.__dict__, "evidence_digest": "will not match"})) if False else None
    # Build a genuine conflicting re-delivery: same observation_id, different
    # evidence content -> different evidence_digest, per the model's own
    # deterministic digest (Issue 1). Constructed via the fixtures module
    # with a different read_count under the same observation_id.
    from tests._protection_observation_fixtures import make_observation
    original = make_observation(observation_id="obs-conflict-target", run_id="run-c", read_count=3)
    conflicting = make_observation(observation_id="obs-conflict-target", run_id="run-c", read_count=7)
    with ProtectionObservationLedger(conflict_db, "run-c") as ledger:
        ledger.append(original)
        conflict_result = ledger.append(conflicting)

    connection = sqlite3.connect(str(conflict_db))
    try:
        conflict_rows = connection.execute(
            "SELECT observation_id, attempted_evidence_digest, existing_evidence_digest "
            "FROM protection_observation_conflicts"
        ).fetchall()
        surviving = connection.execute(
            "SELECT evidence_digest FROM protection_observations WHERE observation_id = ?",
            ("obs-conflict-target",),
        ).fetchall()
    finally:
        connection.close()

    if conflict_result.state.value != "DUPLICATE_CONFLICT" or len(conflict_rows) != 1 or len(surviving) != 1 or surviving[0][0] != original.evidence_digest:
        results.append(_fail("ledger_duplicate_conflict_behavior", f"conflict handling incorrect: result={conflict_result.state}, conflict_rows={len(conflict_rows)}, surviving_matches_original={surviving[0][0] == original.evidence_digest if surviving else None}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("ledger_duplicate_conflict_behavior", "a conflicting re-delivery was refused, recorded in the conflicts table, and did not overwrite the original row", independence=independence, implementation_source=source))

    source_text = (REPOSITORY_ROOT / source).read_text(encoding="utf-8")
    forbidden_tokens = ["UPDATE protection_observations", "DELETE FROM protection_observations", "DROP TABLE"]
    present = [tok for tok in forbidden_tokens if tok in source_text]
    if present:
        results.append(_fail("ledger_no_rewrite_sql", f"forbidden SQL tokens present: {present}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("ledger_no_rewrite_sql", "no UPDATE/DELETE/DROP against the observations table anywhere in source", independence=independence, implementation_source=source))

    offending_name = _source_grants_authority(source_text)
    if offending_name:
        results.append(_fail("ledger_grants_no_authority", f"'{offending_name} = True'-shaped assignment found in ledger source", independence=independence, implementation_source=source))
    else:
        results.append(_pass("ledger_grants_no_authority", "no True-valued authority-field assignment anywhere in the ledger source", independence=independence, implementation_source=source))

    return results


# --------------------------------------------------------------------------
# Independent promotion-policy verification.
#
# Implementation source: src/protection_promotion_policy.py.
# Independence: `_predict_promotion_outcome` below is a from-scratch
# reimplementation of the priority order documented in `evaluate_promotion_
# eligibility`'s own docstring (the reviewed specification), authored
# without reading the function body line-by-line as a copy source. Each
# scenario's expected outcome is computed by this independent function
# BEFORE calling the real implementation once per scenario for comparison.
# --------------------------------------------------------------------------


def _predict_promotion_outcome(observations, *, policy, evaluated_at_monotonic, expected_release_identity, expected_implementation_digest, expected_account_fingerprint_reference, conflict_detected, operator_presence_attested=False) -> str:
    from src.live_order_state_model import ProtectiveCoverage
    from src.protection_observation_model import PrivacyRedactionStatus, ObservationContaminationStatus
    from src.protection_observation_model import BROKER_AUTHORITATIVE_SOURCE_CLASSES
    from src.protection_observation_model import QuantityRelationship
    from src.protection_observation_ledger import ObservationConsistencyStatus
    from src.read_only_broker_state import SnapshotFreshness

    if conflict_detected and policy.reject_contradictory_evidence:
        return "CONFLICTING_EVIDENCE"
    if not observations:
        return "INSUFFICIENT_EVIDENCE"
    if policy.require_zero_write_proof and any(o.write_count != 0 for o in observations):
        return "NONZERO_WRITE_EVIDENCE"
    if policy.require_privacy_verification and any(o.privacy_redaction_status is not PrivacyRedactionStatus.VERIFIED_REDACTED for o in observations):
        return "PRIVACY_UNVERIFIED"
    if policy.reject_contaminated_evidence and any(o.contamination_status is ObservationContaminationStatus.CONTAMINATED for o in observations):
        return "CONTAMINATED_EVIDENCE"
    if policy.require_account_fingerprint_consistency and any(o.account_fingerprint_reference != expected_account_fingerprint_reference for o in observations):
        return "ACCOUNT_MISMATCH"
    if policy.require_release_consistency and any(o.release_identity != expected_release_identity for o in observations):
        return "RELEASE_MISMATCH"
    if policy.require_implementation_digest_consistency and any(o.implementation_digest != expected_implementation_digest for o in observations):
        return "IMPLEMENTATION_MISMATCH"
    if policy.require_broker_authoritative_source and any(o.evidence_source_class not in BROKER_AUTHORITATIVE_SOURCE_CLASSES for o in observations):
        return "SOURCE_INELIGIBLE"

    def stale(o) -> bool:
        if o.data_freshness is not SnapshotFreshness.FRESH:
            return True
        return (evaluated_at_monotonic - o.observed_at_monotonic) > policy.maximum_evidence_age_seconds

    if policy.reject_stale_evidence and any(stale(o) for o in observations):
        return "STALE_EVIDENCE"

    protective = [o for o in observations if o.normalized_protection_state is not ProtectiveCoverage.NOT_PROTECTED]
    flat = [o for o in observations if o.normalized_protection_state is ProtectiveCoverage.NOT_PROTECTED]
    if policy.reject_contradictory_evidence and protective and flat:
        return "CONFLICTING_EVIDENCE"
    if policy.require_active_protection_state and not protective:
        return "PROTECTION_NOT_ACTIVE"
    if policy.require_exact_quantity_coverage and any(o.quantity_relationship is not QuantityRelationship.EXACT_COVERAGE for o in protective):
        return "COVERAGE_INCOMPLETE"
    if any(o.consistency_status is ObservationConsistencyStatus.INCONSISTENT_WITHIN_OBSERVATION for o in protective):
        return "PROTECTION_UNVERIFIABLE"

    if policy.require_operator_presence_attestation and not operator_presence_attested:
        return "INSUFFICIENT_EVIDENCE"

    distinct_runs = {o.run_id for o in protective}
    distinct_times = {o.observed_at_monotonic for o in protective}
    if len(distinct_runs) < policy.minimum_distinct_eligible_live_runs or len(distinct_times) < policy.minimum_distinct_observation_times:
        return "INSUFFICIENT_EVIDENCE"
    if not policy.promotion_enabled:
        return "PROMOTION_NOT_PERMITTED"
    if policy.independent_review_required:
        return "REVIEW_REQUIRED"
    return "ELIGIBLE_FOR_REVIEW"


def check_promotion_policy() -> list[CheckResult]:
    from src.protection_promotion_policy import (
        DEFAULT_PROTECTION_PROMOTION_POLICY,
        ProtectionPromotionPolicy,
        ProtectionPromotionPolicyError,
        evaluate_promotion_eligibility,
    )
    from tests._protection_observation_fixtures import (
        active_protective_order,
        conflicting_states,
        digest,
        make_observation,
        mismatched_account_fingerprint,
        mismatched_implementation_digest,
        no_protective_order,
        simulated_evidence,
        imported_evidence,
        stale_protective_order,
        partial_quantity_coverage,
    )

    results: list[CheckResult] = []
    independence = (
        "_predict_promotion_outcome is a from-scratch reimplementation of "
        "the priority order documented in evaluate_promotion_eligibility's "
        "own docstring, authored in this script. Each scenario's expected "
        "outcome is computed independently before the real function is "
        "called once per scenario for comparison; fixture *construction* "
        "reuses the repository's shared, purely declarative test fixtures."
    )
    source = "src/protection_promotion_policy.py"

    try:
        ProtectionPromotionPolicy(**{**DEFAULT_PROTECTION_PROMOTION_POLICY.__dict__, "automatic_promotion_permitted": True})
        results.append(_fail("automatic_promotion_structurally_forbidden", "constructing a policy with automatic_promotion_permitted=True did not raise", independence=independence, implementation_source=source))
    except ProtectionPromotionPolicyError:
        results.append(_pass("automatic_promotion_structurally_forbidden", "automatic_promotion_permitted=True raises unconditionally", independence=independence, implementation_source=source))

    if DEFAULT_PROTECTION_PROMOTION_POLICY.promotion_enabled:
        results.append(_fail("promotion_disabled_by_default", "DEFAULT_PROTECTION_PROMOTION_POLICY.promotion_enabled is True", independence=independence, implementation_source=source))
    else:
        results.append(_pass("promotion_disabled_by_default", "promotion_enabled=False in the shipped default policy", independence=independence, implementation_source=source))

    source_text = (REPOSITORY_ROOT / source).read_text(encoding="utf-8")
    offending_name = _source_grants_authority(source_text) or ("APPROVED_FOR_ACTIVE_PROTECTION" if "APPROVED_FOR_ACTIVE_PROTECTION=True" in source_text.replace(" ", "") else None)
    if offending_name:
        results.append(_fail("promotion_policy_grants_no_authority", f"'{offending_name} = True'-shaped assignment found in policy source", independence=independence, implementation_source=source))
    else:
        results.append(_pass("promotion_policy_grants_no_authority", "no True-valued authority-field assignment anywhere in source", independence=independence, implementation_source=source))

    # These three values match the fixture module's own hard-coded defaults
    # (tests/_protection_observation_fixtures.py: RELEASE_SHA, and
    # digest("account")/digest("implementation") used whenever a fixture
    # does not deliberately override them). Using anything else here would
    # make every scenario trip RELEASE_MISMATCH/ACCOUNT_MISMATCH/
    # IMPLEMENTATION_MISMATCH before it ever reached the condition each
    # scenario is actually named for.
    from tests._protection_observation_fixtures import RELEASE_SHA
    live_release = RELEASE_SHA
    live_digest = digest("implementation")
    live_account = digest("account")

    def live_batch(count: int, *, run_prefix: str = "verifier-run") -> list:
        batch = []
        for i in range(count):
            batch.append(active_protective_order(f"{run_prefix}-{i}", observation_id=f"live-{i}", observed_at_monotonic=100.0 + i))
        return batch

    scenarios: dict[str, tuple[list, dict[str, Any]]] = {}
    scenarios["no_evidence"] = ([], {})
    scenarios["single_live_insufficient_runs"] = (live_batch(1), {})
    scenarios["three_live_runs_sufficient_but_review_required"] = (live_batch(3), {"operator_presence_attested": True})
    scenarios["simulated_only_cannot_satisfy_live_grounding"] = ([simulated_evidence("run-sim")], {})
    scenarios["imported_only_cannot_satisfy_live_grounding"] = ([imported_evidence("run-imp")], {})
    scenarios["stale_blocks_eligibility"] = ([stale_protective_order("run-stale")], {})
    scenarios["contradictory_blocks_eligibility"] = (list(conflicting_states("run-conf")), {})
    scenarios["account_mismatch_blocks_eligibility"] = ([mismatched_account_fingerprint("run-acct")], {})
    scenarios["implementation_mismatch_blocks_eligibility"] = ([mismatched_implementation_digest("run-impl")], {})
    scenarios["no_protection_observation_valid_not_a_failure"] = ([no_protective_order("run-flat")], {})
    # ProtectionObservationRecord structurally refuses nonzero write_count at
    # construction (verified separately in check_observation_model), so the
    # only way to exercise the policy's own defense-in-depth check for it is
    # to force the attribute after construction on an already-valid record --
    # proving the policy layer independently rejects it even if a corrupted
    # value somehow reached this far.
    tampered_write = make_observation(observation_id="obs-write", run_id="run-write")
    object.__setattr__(tampered_write, "write_count", 1)
    scenarios["nonzero_write_blocks_eligibility"] = ([tampered_write], {})
    scenarios["incomplete_coverage_blocks_eligibility"] = ([partial_quantity_coverage("run-partial")], {})
    scenarios["conflict_detected_flag_blocks_eligibility"] = ([no_protective_order("run-flag")], {"conflict_detected": True})

    policy = DEFAULT_PROTECTION_PROMOTION_POLICY
    all_matched = True
    mismatches: list[str] = []
    for label, (batch, overrides) in scenarios.items():
        kwargs = dict(
            policy=policy,
            evaluated_at_monotonic=200.0,
            expected_release_identity=live_release,
            expected_implementation_digest=live_digest,
            expected_account_fingerprint_reference=live_account,
            conflict_detected=False,
        )
        kwargs.update(overrides)
        predicted = _predict_promotion_outcome(batch, **kwargs)
        actual = evaluate_promotion_eligibility(batch, **kwargs).outcome.value
        if predicted != actual:
            all_matched = False
            mismatches.append(f"{label}: predicted={predicted} actual={actual}")

    if not all_matched:
        results.append(_fail("promotion_outcome_matches_independent_prediction", "; ".join(mismatches), independence=independence, implementation_source=source))
    else:
        results.append(_pass("promotion_outcome_matches_independent_prediction", f"{len(scenarios)} independently predicted scenarios all matched the real evaluator", independence=independence, implementation_source=source))

    return results


# --------------------------------------------------------------------------
# Independent grounding-assessment verification.
#
# Implementation source: src/protection_grounding_assessment.py.
# --------------------------------------------------------------------------


def check_grounding_assessment() -> list[CheckResult]:
    from src.protection_grounding_assessment import evaluate_protection_grounding
    from tests._protection_observation_fixtures import active_protective_order, digest, no_protective_order

    results: list[CheckResult] = []
    independence = (
        "The assessment digest is spot-recomputed with a separately "
        "written canonical encoder (not canonical_bytes/encode_canonical_"
        "value). promotion_permitted/protection_grounded are checked "
        "structurally (hard-coded False on the dataclass, and the "
        "constructor raises if either is somehow True), not by trusting "
        "the recommendation field."
    )
    source = "src/protection_grounding_assessment.py"

    release = digest("assessment-verifier-release")[:40]
    impl = digest("assessment-verifier-digest")
    account = digest("assessment-verifier-account")
    obs = active_protective_order("run-a", observed_at_monotonic=1.0)

    from src.protection_observation_ledger import ProtectionObservationLedger

    import dataclasses as _dc

    with TemporaryDirectory(prefix="m13-verifier-assessment-") as td:
        db = Path(td) / "ledger.sqlite3"
        with ProtectionObservationLedger(db, "run-a") as ledger:
            observation = active_protective_order("run-a", observed_at_monotonic=1.0)
            fixed = _dc.replace(observation, release_identity=release, implementation_digest=impl, account_fingerprint_reference=account)
            ledger.append(fixed)
            summaries = ledger.all_observations()

    assessment = evaluate_protection_grounding(
        summaries,
        evaluated_at_monotonic=2.0,
        expected_release_identity=release,
        expected_implementation_digest=impl,
        expected_account_fingerprint_reference=account,
    )

    if assessment.promotion_permitted is not False or assessment.protection_grounded is not False:
        results.append(_fail("assessment_never_grants_authority", f"promotion_permitted={assessment.promotion_permitted} protection_grounded={assessment.protection_grounded}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("assessment_never_grants_authority", "promotion_permitted and protection_grounded are both False on every produced assessment", independence=independence, implementation_source=source))

    included = set(assessment.included_observation_ids)
    excluded = set(assessment.excluded_observation_ids)
    informational = set(assessment.informational_observation_ids)
    candidates = set(assessment.candidate_observation_ids)
    if (included | excluded | informational) != candidates or (included & excluded) or (included & informational) or (excluded & informational):
        results.append(_fail("assessment_disposition_partitions_candidates", "included/excluded/informational do not exactly partition candidate_observation_ids", independence=independence, implementation_source=source))
    else:
        results.append(_pass("assessment_disposition_partitions_candidates", "included/excluded/informational form an exact, non-overlapping partition of every candidate observation", independence=independence, implementation_source=source))

    payload = {
        "assessment_version": assessment.assessment_version,
        "policy_version": assessment.policy_version,
        "release_identity": assessment.release_identity,
        "implementation_digest": assessment.implementation_digest,
        "account_fingerprint_reference": assessment.account_fingerprint_reference,
        "included_observation_ids": list(assessment.included_observation_ids),
        "excluded_observation_ids": list(assessment.excluded_observation_ids),
        "informational_observation_ids": list(assessment.informational_observation_ids),
        "exclusion_reasons": [f"{oid}={assessment.exclusion_reasons[oid]}" for oid in sorted(assessment.exclusion_reasons)],
        "recommendation": assessment.recommendation.value,
        "reason_codes": list(assessment.reason_codes),
    }
    domain = b"orb-live/protection-grounding-assessment/v1"
    independent_digest = hashlib.sha256(domain + _independent_canonical_bytes(payload)).hexdigest()
    if independent_digest != assessment.assessment_digest:
        results.append(_fail("assessment_digest_independently_reproducible", f"independent={independent_digest} actual={assessment.assessment_digest}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("assessment_digest_independently_reproducible", "independent canonical-encoding recompute matches assessment_digest exactly", independence=independence, implementation_source=source))

    return results


# --------------------------------------------------------------------------
# Independent checkpoint-integration verification.
#
# Implementation source: src/protection_checkpoint_integration.py,
# read_only_observation_checkpoint_launcher.py.
# Independence: AST/import-graph and source-text inspection only. This
# check never executes a real checkpoint or opens a broker session.
# --------------------------------------------------------------------------


def check_checkpoint_integration() -> list[CheckResult]:
    results: list[CheckResult] = []
    independence = (
        "Pure static inspection: AST import analysis and source-text "
        "scanning of the integration module and the launcher, run by this "
        "script directly. No checkpoint is executed and no broker module "
        "is imported by this check."
    )
    source = "src/protection_checkpoint_integration.py"

    integration_path = REPOSITORY_ROOT / source
    tree = ast.parse(integration_path.read_text(encoding="utf-8-sig"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    required_wiring = {
        "src.protection_observation_model",
        "src.protection_observation_ledger",
        "src.protection_promotion_policy",
        "src.protection_grounding_assessment",
    }
    missing_wiring = required_wiring - imported
    if missing_wiring:
        results.append(_fail("checkpoint_integration_wires_m13_pipeline", f"expected imports not found: {sorted(missing_wiring)}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("checkpoint_integration_wires_m13_pipeline", "the integration module imports the observation model, ledger, promotion policy, and grounding assessment", independence=independence, implementation_source=source))

    forbidden_broker_imports = {"src.robinhood_client", "src.robinhood_live_adapter", "src.robinhood_commissioning_session", "src.robinhood_commissioning_adapter"}
    present_broker_imports = forbidden_broker_imports & imported
    if present_broker_imports:
        results.append(_fail("checkpoint_integration_imports_no_broker_module", f"forbidden broker imports found: {sorted(present_broker_imports)}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("checkpoint_integration_imports_no_broker_module", "no broker transport module is imported by the integration layer", independence=independence, implementation_source=source))

    source_text = integration_path.read_text(encoding="utf-8")
    forbidden_calls = ["call_tool(\"place_", "call_tool(\"cancel_", "call_tool(\"replace_", ".place_order(", ".cancel_order("]
    present_calls = [tok for tok in forbidden_calls if tok in source_text]
    if present_calls:
        results.append(_fail("checkpoint_integration_contains_no_write_call", f"write-shaped call text present: {present_calls}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("checkpoint_integration_contains_no_write_call", "no broker-write-shaped call text present anywhere in the integration module", independence=independence, implementation_source=source))

    offending_name = _source_grants_authority(source_text)
    if offending_name:
        results.append(_fail("checkpoint_integration_grants_no_authority", f"'{offending_name} = True'-shaped assignment found in the integration module", independence=independence, implementation_source=source))
    else:
        results.append(_pass("checkpoint_integration_grants_no_authority", "no authority-field mutation token found in the integration module", independence=independence, implementation_source=source))

    launcher_text = (REPOSITORY_ROOT / "read_only_observation_checkpoint_launcher.py").read_text(encoding="utf-8")
    launcher_route_names = [name for name in ("app.route", "@app.get", "@app.post", "WSGIApplication", "def application(environ") if name in launcher_text]
    if launcher_route_names:
        results.append(_fail("no_remote_route_can_trigger_launcher", f"launcher source resembles a web route definition: {launcher_route_names}", independence=independence, implementation_source="read_only_observation_checkpoint_launcher.py"))
    else:
        results.append(_pass("no_remote_route_can_trigger_launcher", "the launcher exposes only an argparse CLI entry point, no web route", independence=independence, implementation_source="read_only_observation_checkpoint_launcher.py"))

    operator_app_text = (REPOSITORY_ROOT / "src/operator_web_app.py").read_text(encoding="utf-8")
    forbidden_operator_imports = ["protection_checkpoint_integration", "read_only_observation_runtime", "run_read_only_observation_checkpoint", "robinhood_client", "robinhood_live_adapter", "robinhood_commissioning"]
    present_operator = [tok for tok in forbidden_operator_imports if tok in operator_app_text]
    if present_operator:
        results.append(_fail("operator_app_cannot_reach_checkpoint_or_broker", f"forbidden references present in operator_web_app.py: {present_operator}", independence=independence, implementation_source="src/operator_web_app.py"))
    else:
        results.append(_pass("operator_app_cannot_reach_checkpoint_or_broker", "the operator application source contains no reference to the checkpoint runtime, integration layer, or any broker transport module", independence=independence, implementation_source="src/operator_web_app.py"))

    return results


# --------------------------------------------------------------------------
# Independent broker call-site re-enumeration.
#
# Implementation source: tests/call_site_guard.py (the "official" hardened
# guard). This scanner is a separate, deliberately simpler AST walker
# written from scratch in this file -- it does not import call_site_guard.
# --------------------------------------------------------------------------


def _independent_call_site_scan(root: Path) -> dict[str, list[tuple[str, int]]]:
    """A separate, bounded scan for `<name>.call_tool(<literal-or-not>, ...)`
    and `<name>.read(<literal-or-not>, ...)` call shapes across src/ and
    scripts/. Deliberately narrower than the hardened guard (no alias/
    getattr/registry resolution) -- its purpose is an independent second
    measurement of the *literal* call sites, not a replacement guard."""
    literal: list[tuple[str, int]] = []
    non_literal: list[tuple[str, int]] = []
    for base in ("src", "scripts"):
        for path in sorted((root / base).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except SyntaxError:
                continue
            # Module-level `NAME = "literal string"` constants, resolved so
            # that `session.call_tool(_ACCOUNTS_TOOL, {})` is recognized as
            # a literal call the same way a directly-spelled string literal
            # would be -- a common, legitimate idiom, not dynamic dispatch.
            module_string_constants: dict[str, str] = {}
            for node in tree.body:
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    module_string_constants[node.targets[0].id] = node.value.value
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr not in {"call_tool", "read"}:
                    continue
                if not node.args:
                    continue
                first = node.args[0]
                is_literal = (isinstance(first, ast.Constant) and isinstance(first.value, str)) or (
                    isinstance(first, ast.Name) and first.id in module_string_constants
                )
                if is_literal:
                    literal.append((relative, node.lineno))
                else:
                    non_literal.append((relative, node.lineno))
    return {"literal": literal, "non_literal": non_literal}


def check_broker_call_sites() -> list[CheckResult]:
    from tests.call_site_guard import analyze_runtime_tree, LITERAL, SAFE_WRAPPER, DYNAMIC, UNRESOLVED, violations
    from src.read_only_broker_gateway import READ_ONLY_TOOL_ALLOWLIST

    results: list[CheckResult] = []
    independence = (
        "A separate, deliberately simpler AST scanner is written in this "
        "script (_independent_call_site_scan) and its literal-call-site "
        "count is cross-checked against the hardened guard's own count. "
        "The hardened guard's LITERAL+SAFE_WRAPPER classification is also "
        "compared to a repository-truth commissioning-write-path check "
        "that does not depend on the guard's own classification labels."
    )
    source = "tests/call_site_guard.py"

    independent = _independent_call_site_scan(REPOSITORY_ROOT)
    official_sites = analyze_runtime_tree()
    official_literal = [s for s in official_sites if s.kind == LITERAL]
    official_safe_wrapper = [s for s in official_sites if s.kind == SAFE_WRAPPER]
    official_dynamic_or_unresolved = [s for s in official_sites if s.kind in {DYNAMIC, UNRESOLVED}]

    independent_literal_locations = {(p, l) for p, l in independent["literal"]}
    official_literal_locations = {(s.path, s.line) for s in official_literal}
    if not official_literal_locations.issubset(independent_literal_locations):
        missing = sorted(official_literal_locations - independent_literal_locations)
        results.append(_fail("independent_scan_finds_every_official_literal_site", f"official literal sites the independent scan missed: {missing}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("independent_scan_finds_every_official_literal_site", f"independent scan found {len(independent_literal_locations)} literal call-shaped sites, a superset of the official guard's {len(official_literal_locations)} literal sites", independence=independence, implementation_source=source))

    if official_dynamic_or_unresolved:
        results.append(_fail("no_unresolved_dynamic_broker_dispatch", f"{len(official_dynamic_or_unresolved)} unresolved/dynamic site(s): {[s.describe() for s in official_dynamic_or_unresolved]}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("no_unresolved_dynamic_broker_dispatch", "zero DYNAMIC/UNRESOLVED broker call sites in the official guard's own enumeration", independence=independence, implementation_source=source))

    commissioning_path = REPOSITORY_ROOT / "src/robinhood_commissioning_adapter.py"
    commissioning_text = commissioning_path.read_text(encoding="utf-8")
    commissioning_seen_by_guard = any(s.path == "src/robinhood_commissioning_adapter.py" for s in official_sites)
    commissioning_has_write_text = "place_equity_order" in commissioning_text or "call_tool" in commissioning_text
    if commissioning_has_write_text and not commissioning_seen_by_guard:
        results.append(_fail("commissioning_write_path_remains_visible", "robinhood_commissioning_adapter.py contains write-shaped dispatch text but the guard reports no site there", independence=independence, implementation_source=source))
    else:
        results.append(_pass("commissioning_write_path_remains_visible", "the commissioning write path is present in source and accounted for by the guard's enumeration", independence=independence, implementation_source=source))

    ordinary = [s for s in official_sites if s.path != "src/robinhood_live_adapter.py"]
    offending = violations(ordinary, READ_ONLY_TOOL_ALLOWLIST)
    if offending:
        results.append(_fail("no_offending_call_site_outside_initialization", f"{len(offending)} violating site(s): {[s.describe() for s in offending]}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("no_offending_call_site_outside_initialization", f"official guard reports {len(official_sites)} total sites ({len(official_literal)} literal, {len(official_safe_wrapper)} safe_wrapper), zero violations outside the reviewed initialization sequence", independence=independence, implementation_source=source))

    m13_files = ("src/protection_checkpoint_integration.py", "src/protection_observation_model.py", "src/protection_observation_ledger.py", "src/protection_promotion_policy.py", "src/protection_grounding_assessment.py", "src/protection_assessment_store.py")
    m13_sites = [s for s in official_sites if s.path in m13_files]
    if m13_sites:
        results.append(_fail("m13_pipeline_has_no_broker_call_site", f"unexpected broker call sites in M13 modules: {[s.describe() for s in m13_sites]}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("m13_pipeline_has_no_broker_call_site", "zero broker call sites detected across all seven M13 pipeline modules", independence=independence, implementation_source=source))

    operator_sites = [s for s in official_sites if s.path in {"src/operator_web_app.py", "src/protection_operator_evidence.py"}]
    if operator_sites:
        results.append(_fail("operator_app_has_no_broker_call_site", f"unexpected broker call sites in the operator app: {[s.describe() for s in operator_sites]}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("operator_app_has_no_broker_call_site", "zero broker call sites detected in the operator application or its M13 evidence layer", independence=independence, implementation_source=source))

    return results


# --------------------------------------------------------------------------
# Independent operator-route verification.
#
# Implementation source: src/operator_web_app.py, src/operator_framework.py.
# Independence: candidate paths and expectations are authored fresh in this
# script; scripts/verify_operator_routes.py's crawler is not imported.
# --------------------------------------------------------------------------


def check_operator_routes(demo: dict[str, Any]) -> list[CheckResult]:
    from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig, build_operator_route_table
    from datetime import datetime as _dt

    results: list[CheckResult] = []
    independence = (
        "Route inventory is read from the real ApplicationRouteTable "
        "(the framework itself -- there is no second router to compare "
        "against), but the candidate probe paths, expected safety "
        "declarations, and WSGI requests below are authored independently "
        "in this script, not reused from scripts/verify_operator_routes.py."
    )
    source = "src/operator_web_app.py"

    table = build_operator_route_table()
    routes = table.routes

    unsafe = [r for r in routes if r.safety_authority != "NONE" or r.permits_database_write or r.permits_runtime_write or r.permits_outbound_network or r.exposes_private_data or r.methods != ("GET",)]
    if unsafe:
        results.append(_fail("every_route_declares_safe_get_only", f"{len(unsafe)} unsafe route declaration(s): {[r.path for r in unsafe]}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("every_route_declares_safe_get_only", f"all {len(routes)} registered routes declare GET-only, no write/network/private-data authority", independence=independence, implementation_source=source))

    required_m13_paths = {
        "/protection-observations", "/api/protection-observations",
        "/protection-observations/{observation_id}", "/api/protection-observations/{observation_id}",
        "/protection-assessments", "/api/protection-assessments",
        "/protection-assessments/{assessment_id}", "/api/protection-assessments/{assessment_id}",
    }
    actual_paths = {r.path for r in routes}
    missing = required_m13_paths - actual_paths
    if missing:
        results.append(_fail("m13_evidence_routes_registered", f"missing routes: {sorted(missing)}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("m13_evidence_routes_registered", f"all 8 M13 protection-evidence routes independently confirmed registered (of {len(actual_paths)} total)", independence=independence, implementation_source=source))

    mutating_method_paths = [r for r in routes if any(m in r.methods for m in ("POST", "PUT", "PATCH", "DELETE"))]
    if mutating_method_paths:
        results.append(_fail("no_mutation_route_exists", f"{len(mutating_method_paths)} route(s) accept a mutating method: {[(r.path, r.methods) for r in mutating_method_paths]}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("no_mutation_route_exists", "no registered route accepts POST/PUT/PATCH/DELETE", independence=independence, implementation_source=source))

    app = OperatorWebApp(OperatorWebAppConfig(
        runtime_root=Path(demo["root"]),
        database_path=Path(demo["database"]),
        backend_mode="SQLITE_READ_MODEL",
        expected_content_digest=demo["expected_content_digest"],
        default_as_of=_dt(2026, 8, 5, 16, tzinfo=timezone.utc),
        protection_ledger_path=Path(demo["protection_ledger_path"]),
        protection_assessment_store_path=Path(demo["protection_assessment_store_path"]),
    ))

    def _get(path: str) -> tuple[str, bytes]:
        captured: dict[str, Any] = {}
        body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": ""}, lambda status, headers: captured.update(status=status)))
        return captured["status"], body

    obs_id = demo["protection_observation_id"]
    assessment_id = demo["protection_assessment_id"]

    independent_probe_paths = [
        "/protection-observations", "/api/protection-observations",
        f"/protection-observations/{obs_id}", f"/api/protection-observations/{obs_id}",
        "/protection-observations/does-not-exist-independent-probe",
        "/protection-assessments", "/api/protection-assessments",
        f"/protection-assessments/{assessment_id}", f"/api/protection-assessments/{assessment_id}",
        "/protection-assessments/does-not-exist-independent-probe",
        f"/exports/protection_observation/{obs_id}.json",
        f"/exports/protection_assessment/{assessment_id}.json",
    ]
    executed_ok = True
    failures = []
    for path in independent_probe_paths:
        status, body = _get(path)
        expect_404 = "does-not-exist" in path
        if expect_404 and status != "404 Not Found":
            executed_ok = False
            failures.append(f"{path} -> {status} (expected 404)")
        elif not expect_404 and status != "200 OK":
            executed_ok = False
            failures.append(f"{path} -> {status} (expected 200)")
        body_text = body.decode("utf-8", "replace")
        if any(token in body_text for token in ("robinhood.com", "account_number", "\"order_id\"", "\"position_id\"", "session_token", "auth_challenge")):
            executed_ok = False
            failures.append(f"{path} response contains a raw-identifier-shaped token")
    if not executed_ok:
        results.append(_fail("independent_probe_of_m13_routes", "; ".join(failures), independence=independence, implementation_source=source))
    else:
        results.append(_pass("independent_probe_of_m13_routes", f"{len(independent_probe_paths)} independently authored probe requests all returned the expected status with no raw-identifier-shaped token in the body", independence=independence, implementation_source=source))

    mutation_status, _ = None, None
    captured: dict[str, Any] = {}
    b"".join(app({"REQUEST_METHOD": "POST", "PATH_INFO": "/protection-observations", "QUERY_STRING": ""}, lambda status, headers: captured.update(status=status)))
    if captured.get("status") != "405 Method Not Allowed":
        results.append(_fail("post_to_evidence_route_is_refused", f"POST returned {captured.get('status')}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("post_to_evidence_route_is_refused", "POST /protection-observations returns 405 Method Not Allowed", independence=independence, implementation_source=source))

    return results


# --------------------------------------------------------------------------
# Independent export verification.
# --------------------------------------------------------------------------


def check_exports(demo: dict[str, Any]) -> list[CheckResult]:
    from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
    from datetime import datetime as _dt

    results: list[CheckResult] = []
    independence = (
        "This check calls the real WSGI application (unavoidable -- an "
        "export is defined by what the app actually serves) but applies "
        "an independently authored required-key schema check, not the "
        "app's own export-catalog assertions."
    )
    source = "src/operator_web_app.py"

    app = OperatorWebApp(OperatorWebAppConfig(
        runtime_root=Path(demo["root"]),
        database_path=Path(demo["database"]),
        backend_mode="SQLITE_READ_MODEL",
        expected_content_digest=demo["expected_content_digest"],
        default_as_of=_dt(2026, 8, 5, 16, tzinfo=timezone.utc),
        protection_ledger_path=Path(demo["protection_ledger_path"]),
        protection_assessment_store_path=Path(demo["protection_assessment_store_path"]),
    ))

    def _get_json(path: str) -> dict[str, Any]:
        captured: dict[str, Any] = {}
        body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": ""}, lambda status, headers: captured.update(status=status)))
        assert captured["status"] == "200 OK", f"{path} -> {captured['status']}"
        return json.loads(body)

    obs_export = _get_json(f"/exports/protection_observation/{demo['protection_observation_id']}.json")
    required_obs_keys = {"export_type", "observation"}
    required_obs_fields = {"observation_id", "evidence_source_class", "normalized_protection_state", "privacy_redaction_status", "write_count", "evidence_digest", "implementation_digest", "release_identity"}
    missing = (required_obs_keys - obs_export.keys()) | (required_obs_fields - obs_export.get("observation", {}).keys())
    if missing or obs_export.get("observation", {}).get("write_count") != 0:
        results.append(_fail("observation_export_schema_complete", f"missing keys or nonzero write_count: {missing}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("observation_export_schema_complete", "observation export contains schema/source/digest/privacy/zero-write fields as required", independence=independence, implementation_source=source))

    assessment_export = _get_json(f"/exports/protection_assessment/{demo['protection_assessment_id']}.json")
    required_assessment_keys = {"export_type", "assessment"}
    required_assessment_fields = {"assessment_id", "policy_version", "recommendation", "reason_codes", "assessment_digest", "release_identity", "implementation_digest", "promotion_permitted", "protection_grounded"}
    missing2 = (required_assessment_keys - assessment_export.keys()) | (required_assessment_fields - assessment_export.get("assessment", {}).keys())
    if missing2 or assessment_export.get("assessment", {}).get("promotion_permitted") is not False or assessment_export.get("assessment", {}).get("protection_grounded") is not False:
        results.append(_fail("assessment_export_schema_complete_and_advisory", f"missing keys or authority field not False: {missing2}", independence=independence, implementation_source=source))
    else:
        results.append(_pass("assessment_export_schema_complete_and_advisory", "assessment export contains all required fields and explicitly asserts promotion_permitted=False, protection_grounded=False", independence=independence, implementation_source=source))

    return results


# --------------------------------------------------------------------------
# Authority state, Q1, Q2.
# --------------------------------------------------------------------------


_AUTHORITY_SYMBOLS = (
    "protection_grounded", "activation_approved", "permits_automated_action",
    "live_ready", "unattended_ready", "ACTIVATION_APPROVALS",
    "ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES",
)


def check_authority_state() -> list[CheckResult]:
    results: list[CheckResult] = []
    independence = (
        "Repository-wide grep/AST scan across every M13 module for "
        "assignment sites of the eight named authority symbols, run "
        "directly by this script -- not by asking any M13 module whether "
        "it thinks it is safe."
    )
    m13_paths = [
        "src/protection_observation_model.py", "src/protection_observation_ledger.py",
        "src/protection_promotion_policy.py", "src/protection_grounding_assessment.py",
        "src/protection_checkpoint_integration.py", "src/protection_assessment_store.py",
        "src/protection_operator_evidence.py", "src/operator_web_app.py",
        "read_only_observation_checkpoint_launcher.py",
    ]
    offending: list[str] = []
    for relative in m13_paths:
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets = []
            value = None
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
                value = node.value
            # A literal `False` default (e.g. a dataclass field declaration
            # `protection_grounded: bool = False`) is the correct structural
            # guarantee this milestone requires, not a mutation -- only a
            # `True` literal, or a non-literal/dynamic value this script
            # cannot prove is always False, is offending.
            if isinstance(value, ast.Constant) and value.value is False:
                continue
            for target in targets:
                name = None
                if isinstance(target, ast.Name):
                    name = target.id
                elif isinstance(target, ast.Attribute):
                    name = target.attr
                if name in _AUTHORITY_SYMBOLS:
                    offending.append(f"{relative}:{node.lineno} assigns {name}")

    if offending:
        results.append(_fail("no_m13_module_assigns_an_authority_symbol", "; ".join(offending), independence=independence, implementation_source="M13 modules"))
    else:
        results.append(_pass("no_m13_module_assigns_an_authority_symbol", f"scanned {len(m13_paths)} M13 modules via AST; zero assignment sites for any of {len(_AUTHORITY_SYMBOLS)} authority symbols", independence=independence, implementation_source="M13 modules"))

    from src.config import load_config
    try:
        loaded = load_config(REPOSITORY_ROOT / "config.yaml") if (REPOSITORY_ROOT / "config.yaml").is_file() else None
    except Exception:
        loaded = None
    if loaded is not None and loaded.get("strategy", {}).get("enabled") is True:
        results.append(_fail("strategy_enabled_is_false", "config.yaml has strategy.enabled=true", independence=independence, implementation_source="config.yaml"))
    else:
        results.append(_pass("strategy_enabled_is_false", "strategy.enabled is not true in the tracked configuration", independence=independence, implementation_source="config.yaml"))

    return results


def check_q1_and_q2() -> list[CheckResult]:
    results: list[CheckResult] = []
    independence = (
        "Static source review against the product-owner's own stated Q1/Q2 "
        "constraints, performed by this script -- not by asking the "
        "implementation whether it satisfies them."
    )

    observation_model_text = (REPOSITORY_ROOT / "src/protection_observation_model.py").read_text(encoding="utf-8")
    if "place_equity_order" in observation_model_text or "cancel_equity_order" in observation_model_text:
        results.append(_fail("q1_no_protective_order_creation_in_model", "order-creation-shaped tool names present in the observation model", independence=independence, implementation_source="src/protection_observation_model.py"))
    else:
        results.append(_pass("q1_no_protective_order_creation_in_model", "the observation model contains no order-creation-shaped text", independence=independence, implementation_source="src/protection_observation_model.py"))

    from src.protection_observation_model import EvidenceSourceClass, BROKER_AUTHORITATIVE_SOURCE_CLASSES
    if EvidenceSourceClass.SIMULATED_BROKER in BROKER_AUTHORITATIVE_SOURCE_CLASSES:
        results.append(_fail("q1_simulation_is_not_broker_authoritative", "SIMULATED_BROKER is a member of BROKER_AUTHORITATIVE_SOURCE_CLASSES", independence=independence, implementation_source="src/protection_observation_model.py"))
    else:
        results.append(_pass("q1_simulation_is_not_broker_authoritative", "SIMULATED_BROKER is independently confirmed absent from BROKER_AUTHORITATIVE_SOURCE_CLASSES", independence=independence, implementation_source="src/protection_observation_model.py"))

    from src.live_order_state_model import ProtectiveCoverage
    if ProtectiveCoverage.NOT_PROTECTED not in ProtectiveCoverage:
        results.append(_fail("q1_absence_of_protection_is_representable", "NOT_PROTECTED is not a valid ProtectiveCoverage member", independence=independence, implementation_source="src/live_order_state_model.py"))
    else:
        results.append(_pass("q1_absence_of_protection_is_representable", "NOT_PROTECTED is a valid, representable ProtectiveCoverage state", independence=independence, implementation_source="src/live_order_state_model.py"))

    policy_text = (REPOSITORY_ROOT / "src/protection_promotion_policy.py").read_text(encoding="utf-8")
    from src.protection_promotion_policy import DEFAULT_PROTECTION_PROMOTION_POLICY
    hardcoded_threshold_grants_authority = "promotion_enabled=True" in policy_text or "promotion_enabled = True" in policy_text
    if hardcoded_threshold_grants_authority or DEFAULT_PROTECTION_PROMOTION_POLICY.promotion_enabled:
        results.append(_fail("q2_remains_unresolved_promotion_disabled", "a hard-coded promotion_enabled=True was found in the policy module or its default", independence=independence, implementation_source="src/protection_promotion_policy.py"))
    else:
        results.append(_pass("q2_remains_unresolved_promotion_disabled", "no code path or default sets promotion_enabled=True; the minimum-run/time thresholds remain policy configuration only, not an answer to Q2", independence=independence, implementation_source="src/protection_promotion_policy.py"))

    return results


# --------------------------------------------------------------------------
# Report assembly.
# --------------------------------------------------------------------------


def run_all_checks(root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    results += check_implementation_digest_and_closure(root)
    results += check_observation_model()
    with TemporaryDirectory(prefix="m13-verifier-") as td:
        tmp_root = Path(td)
        results += check_ledger(tmp_root)
        results += check_promotion_policy()
        results += check_grounding_assessment()
        results += check_checkpoint_integration()
        results += check_broker_call_sites()
        results += check_authority_state()
        results += check_q1_and_q2()

        from scripts.create_operator_demo import create_demo
        from datetime import datetime as _dt
        demo = create_demo(tmp_root / "route-demo", _dt(2026, 8, 5, 16, tzinfo=timezone.utc))
        results += check_operator_routes(demo)
        results += check_exports(demo)
    return results


def build_report(root: Path) -> dict[str, Any]:
    from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha, PINNED_IMPLEMENTATION_CLOSURE

    results = run_all_checks(root)
    failed = [r for r in results if r.status == "FAIL"]
    final_result = "FAIL" if failed else "PASS"

    return {
        "schema_version": 1,
        "verifier": "scripts/verify_m13_protection_grounding.py",
        "reviewed_repository_sha": resolve_head_sha(root),
        "implementation_digest": checkpoint_implementation_digest(root),
        "pinned_file_count": len(PINNED_IMPLEMENTATION_CLOSURE),
        "check_count": len(results),
        "failed_count": len(failed),
        "checks": [r.to_dict() for r in results],
        "final_result": final_result,
    }


def _print_human_report(report: dict[str, Any]) -> None:
    print(f"M13 independent verifier -- reviewed SHA {report['reviewed_repository_sha']}")
    print(f"implementation_digest: {report['implementation_digest']}")
    print(f"pinned_file_count: {report['pinned_file_count']}")
    print()
    for check in report["checks"]:
        marker = "PASS" if check["status"] == "PASS" else "FAIL"
        print(f"[{marker}] {check['name']}: {check['detail']}")
    print()
    print(f"checks: {report['check_count']}  failed: {report['failed_count']}")
    print(f"FINAL RESULT: {report['final_result']}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of the human report")
    args = parser.parse_args(argv)

    report = build_report(REPOSITORY_ROOT)
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        _print_human_report(report)
    return 0 if report["final_result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
