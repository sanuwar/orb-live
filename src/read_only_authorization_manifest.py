"""Operator-issued authorization manifest for the read-only observation checkpoint.

The approved destination is unattended operation, and a person answering `y`
at a terminal does not scale to scheduled execution. This module replaces that
person with something narrower than a person, not broader: a signed-in-spirit
statement, written by an operator, that authorizes **one** read-only
observation, of **one** implementation, at **one** commit, **once**.

What this is deliberately **not**:

* It is not an always-yes mode. A manifest authorizes one checkpoint identity
  and is consumed when used.
* It is not auto-typing an answer. Nothing here produces the string `y`, and
  the interactive gate is untouched.
* It is not environment-based approval. An environment flag remains necessary
  and remains insufficient — the manifest is the approval.
* It is not transferable. Scope, commit, implementation digest, capability
  list, and observation identity are all pinned, so a manifest cannot drift
  onto a different checkpoint, a different build, a later date, a wider
  capability, or any future write-enabled runtime.

What `checkpoint_implementation_digest` approves is the **complete reviewed
implementation closure** — every repository runtime file the checkpoint can
reach at execution time, including through the launcher's deferred session
import. It is not a list of the five most interesting files, and it was once
exactly that: a review found that the checkpoint reached fifteen modules the
digest did not cover, any of which could change behaviour while an already
issued manifest kept validating. Signing off on a digest now means signing off
on everything that can change what the run does.

**The agent never creates, renews, broadens, or raises a manifest.** This
module only ever *reads and refuses*. There is no write path, no template
generator, and no renewal helper — asserted by test, because a convenience
function that produced a manifest would let the thing being authorized author
its own authorization.

Every failure mode fails closed: absent, unreadable, malformed, unknown field,
wrong scope, wrong commit, wrong digest, widened capability, non-zero write
limit, more than one session, not yet valid, expired, over-long validity,
mismatched identity, or already consumed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from src.canonical_source_hash import canonical_source_sha256
from src.read_only_observation_runtime import (
    AUTHORIZATION_STAGES,
    PERMITTED_READ_TOOLS,
    REQUIRED_ZERO_COUNTERS,
)


class AuthorizationManifestError(RuntimeError):
    """Any manifest refusal. Never carries manifest or broker content."""


#: The only scope this checkpoint accepts. A manifest issued for anything
#: else — notably any future write-enabled runtime — is refused here, so a
#: read-only authorization can never be replayed into a trading context.
READ_ONLY_SCOPE = "READ_ONLY_BROKER_OBSERVATION"

MANIFEST_VERSION = 1

#: Exact key set. Missing *and* unexpected keys are both refused: an unknown
#: field may be an operator typo for a field that then silently defaults, or a
#: hand-edit adding a permission this validator does not know to check.
MANIFEST_REQUIRED_KEYS: frozenset[str] = frozenset({
    "manifest_version",
    "scope",
    "approved_main_sha",
    "checkpoint_implementation_digest",
    "allowed_broker_operations",
    "write_limits",
    "maximum_sessions",
    "checkpoint_observation_id",
    "issued_at",
    "expires_at",
    "issued_by",
})

#: An authorization that outlives the review that produced it is not an
#: authorization. A manifest may not be valid for more than one day.
MAXIMUM_VALIDITY = timedelta(hours=24)

#: Every repository runtime file the checkpoint can reach at execution time.
#:
#: This is the **implementation closure**, not a list of the interesting files.
#: The distinction was a review finding: an earlier version pinned five files
#: while the checkpoint reached fifteen more by import, so a change to any of
#: those fifteen altered what the checkpoint did while leaving the digest — and
#: therefore any previously issued manifest — still valid. An operator was
#: approving less than they thought.
#:
#: Derived by walking imports from the five entry points (the launcher, the
#: manifest path, the runtime, the state model, and the order-state model) and
#: kept here as an **explicit reviewed list** rather than rediscovered
#: dynamically at validation time. Dynamic discovery on the authorization path
#: would mean the set being approved is computed by the same process asking for
#: approval; a literal list is auditable by reading it.
#:
#: A guard test walks the real closure and fails if it and this list disagree,
#: so the list cannot silently fall behind the code.
#:
#: What is deliberately **absent**: tests, documentation, caches, generated
#: artifacts, evidence, databases, credentials, and token files. Note that
#: `src/config.py` is present because it is the configuration *loader* — runtime
#: code that can change behaviour. The YAML it reads is data, is not pinned, and
#: must never be.
PINNED_IMPLEMENTATION_CLOSURE: tuple[str, ...] = (
    # Entry point.
    "read_only_observation_checkpoint_launcher.py",
    # Authorization.
    "src/canonical_source_hash.py",
    "src/read_only_authorization_manifest.py",
    # Read-only runtime, decision model, and calendar.
    "src/exchange_calendar.py",
    "src/read_only_broker_state.py",
    "src/read_only_observation_runtime.py",
    # Order-state normalization and lifecycle interpretation.
    "src/live_broker_capabilities.py",
    "src/live_order_contract.py",
    "src/live_order_state_model.py",
    "src/order_lifecycle_model.py",
    # Session creation and transport initialization. Reached through the
    # launcher's deferred session-factory import, which is what actually opens
    # the one permitted broker session.
    "src/broker_action_saga.py",
    "src/broker_identity.py",
    "src/broker_snapshot.py",
    "src/config.py",
    "src/one_share_commissioning.py",
    "src/one_share_commissioning_orchestrator.py",
    "src/robinhood_client.py",
    "src/robinhood_commissioning_adapter.py",
    "src/robinhood_commissioning_session.py",
    "src/trade_state.py",
    # M13: protection-grounding integration, reached through the launcher's
    # top-level import of src/protection_checkpoint_integration.py. Added in
    # Milestone 13 Issue 5. Every one of these performs no broker call; the
    # integration module consumes an already-completed CheckpointResult from
    # the runtime already pinned above.
    "src/offline_path_safety.py",
    "src/protection_checkpoint_integration.py",
    "src/protection_grounding_assessment.py",
    "src/protection_observation_ledger.py",
    "src/protection_observation_model.py",
    "src/protection_promotion_policy.py",
    # M13 Issue 7: reached through the launcher's deferred import of
    # src/protection_assessment_store.py (mirrors the existing deferred
    # import of src/robinhood_commissioning_session.py above) when the
    # optional --protection-assessment-store flag is used. Performs no
    # broker call; persists the already-computed assessment unchanged.
    "src/protection_assessment_store.py",
    # M14 Issue 1: the safety-governance boundary imposed on the existing
    # commissioning write. Reached because src/robinhood_commissioning_
    # adapter.py and src/one_share_commissioning_orchestrator.py (both
    # already pinned above) now import it. Performs no broker call; decides
    # only whether an already-requested write may proceed.
    "src/execution_safety_governance.py",
    # M14 Issue 7: the commissioning suspension gate. Reached because
    # src/robinhood_commissioning_session.py (already pinned above) now
    # imports src/commissioning_suspension_identity.py to derive the
    # suspension controller identity and open its durable store; that
    # module's own make_gate() helper (a deferred, function-local import
    # for a not-yet-called-here convenience path) in turn reaches src/
    # execution_safety_governance_deferred_gates.py, and THAT file's three
    # gate-factory functions each deferred-import a different module. All
    # five are pulled in together because this closure walk deliberately
    # follows deferred imports (see discovered_execution_closure's
    # docstring in tests/test_read_only_authorization_manifest.py) --
    # reachability, not activation: none of the risk/suspension/
    # operational-health gates are in DEFAULT_COMMISSIONING_GATES except
    # the suspension one added in Issue 7 (see execution_safety_
    # governance.py). Pinning the other two is a conservative side effect
    # of one shared deferred-gates module, not a decision to activate them.
    "src/commissioning_suspension_identity.py",
    "src/durable_suspension_controller.py",
    "src/execution_safety_governance_deferred_gates.py",
    "src/attestation_authority.py",
    "src/durable_scheduler.py",
    "src/execution_gates.py",
    "src/operational_monitor.py",
    "src/risk_limits.py",
)

#: Domain separation. A digest of this closure must never collide with a digest
#: of anything else that happens to hash file paths and contents, and the tag
#: changes if the digest construction itself ever changes.
_DIGEST_DOMAIN = b"orb-live/read-only-checkpoint-implementation-closure/v2"


def _canonical_pinned_paths() -> tuple[str, ...]:
    """Validate and canonically order the closure.

    Sorted, so reordering the declaration above — for readability, for grouping
    — cannot change the digest and cannot invalidate an outstanding manifest.
    Only the *set* of files and their contents are load-bearing.
    """
    seen: set[str] = set()
    for relative in PINNED_IMPLEMENTATION_CLOSURE:
        if not isinstance(relative, str) or not relative:
            raise AuthorizationManifestError("pinned path is not a string")
        if "\\" in relative:
            raise AuthorizationManifestError("pinned path is not POSIX-normalized")
        if "\0" in relative:
            raise AuthorizationManifestError("pinned path contains a null byte")
        if relative != PurePosixPath(relative).as_posix():
            raise AuthorizationManifestError("pinned path is not normalized")
        if PurePosixPath(relative).is_absolute() or relative.startswith("/"):
            raise AuthorizationManifestError("pinned path is absolute")
        if ".." in PurePosixPath(relative).parts:
            raise AuthorizationManifestError("pinned path escapes the repository")
        if not relative.endswith(".py"):
            raise AuthorizationManifestError("pinned path is not a Python module")
        if relative in seen:
            raise AuthorizationManifestError("pinned path is duplicated")
        seen.add(relative)
    return tuple(sorted(seen))


def checkpoint_implementation_digest(root: Path) -> str:
    """One digest over the entire reviewed implementation closure.

    Deterministic by construction: canonical relative POSIX paths, sorted, with
    the file count and each path length folded in so that two files cannot be
    substituted for one another or concatenated into an ambiguous encoding.
    Nothing environmental participates — no timestamp, no absolute path, no
    working directory, no environment variable, no generated artifact.

    `canonical_source_sha256` supplies the per-file content hash, so a
    line-ending difference between checkouts does not change the answer.
    """
    paths = _canonical_pinned_paths()
    accumulator = hashlib.sha256()
    accumulator.update(_DIGEST_DOMAIN)
    accumulator.update(b"\0")
    accumulator.update(str(len(paths)).encode("ascii"))
    accumulator.update(b"\0")
    for relative in paths:
        target = root / relative
        if not target.is_file():
            raise AuthorizationManifestError("pinned implementation file is absent")
        encoded = relative.encode("utf-8")
        # Length-prefixed as well as delimited: a delimiter alone can be
        # ambiguous if a path could ever contain one.
        accumulator.update(str(len(encoded)).encode("ascii"))
        accumulator.update(b"\0")
        accumulator.update(encoded)
        accumulator.update(b"\0")
        accumulator.update(canonical_source_sha256(target).encode("ascii"))
        accumulator.update(b"\0")
    return accumulator.hexdigest()


def resolve_head_sha(root: Path) -> str:
    """Read HEAD from the git directory directly.

    No subprocess: this runs on the authorization path, and shelling out
    there would add a process-spawn surface to a module whose entire purpose
    is to be small enough to audit.
    """
    head = root / ".git" / "HEAD"
    try:
        content = head.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AuthorizationManifestError("repository HEAD is unreadable") from exc
    if content.startswith("ref:"):
        reference = content.split(":", 1)[1].strip()
        candidate = root / ".git" / reference
        try:
            content = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            packed = root / ".git" / "packed-refs"
            content = ""
            try:
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(f" {reference}"):
                        content = line.split(" ", 1)[0].strip()
                        break
            except OSError as exc:
                raise AuthorizationManifestError("repository HEAD is unreadable") from exc
    if len(content) != 40 or any(character not in "0123456789abcdef" for character in content):
        raise AuthorizationManifestError("repository HEAD is not a commit SHA")
    return content


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise AuthorizationManifestError(f"{label} is not a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AuthorizationManifestError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        # A naive timestamp is ambiguous by exactly the amount that matters
        # for an expiry check.
        raise AuthorizationManifestError(f"{label} has no timezone")
    return parsed.astimezone(timezone.utc)


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise AuthorizationManifestError(f"{label} is not a commit SHA")
    if any(character not in "0123456789abcdef" for character in value):
        raise AuthorizationManifestError(f"{label} is not a commit SHA")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise AuthorizationManifestError(f"{label} is not a sha256 digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise AuthorizationManifestError(f"{label} is not a sha256 digest")
    return value


@dataclass(frozen=True)
class AuthorizationManifest:
    """A parsed manifest. Parsing is not approval; validation is."""

    scope: str
    approved_main_sha: str
    checkpoint_implementation_digest: str
    allowed_broker_operations: tuple[str, ...]
    write_limits: Mapping[str, int]
    maximum_sessions: int
    checkpoint_observation_id: str
    issued_at: datetime
    expires_at: datetime
    issued_by: str


def parse_manifest(payload: Any) -> AuthorizationManifest:
    """Structural parse. Every type is checked; nothing is coerced."""
    if not isinstance(payload, Mapping):
        raise AuthorizationManifestError("manifest is not an object")
    present = set(payload)
    if present != MANIFEST_REQUIRED_KEYS:
        raise AuthorizationManifestError("manifest field set is not the required set")
    if payload["manifest_version"] != MANIFEST_VERSION:
        raise AuthorizationManifestError("manifest version is unsupported")

    operations = payload["allowed_broker_operations"]
    if not isinstance(operations, list) or not all(
        isinstance(item, str) for item in operations
    ):
        raise AuthorizationManifestError("allowed operations is not a list of strings")

    limits = payload["write_limits"]
    if not isinstance(limits, Mapping):
        raise AuthorizationManifestError("write limits is not an object")
    checked: dict[str, int] = {}
    for name, value in limits.items():
        if not isinstance(name, str):
            raise AuthorizationManifestError("write limit name is not a string")
        if not isinstance(value, int) or isinstance(value, bool):
            raise AuthorizationManifestError("write limit is not an integer")
        checked[name] = value

    sessions = payload["maximum_sessions"]
    if not isinstance(sessions, int) or isinstance(sessions, bool):
        raise AuthorizationManifestError("maximum sessions is not an integer")

    observation_id = payload["checkpoint_observation_id"]
    if not isinstance(observation_id, str) or not observation_id.strip():
        raise AuthorizationManifestError("checkpoint observation ID is missing")

    issued_by = payload["issued_by"]
    if not isinstance(issued_by, str) or not issued_by.strip():
        raise AuthorizationManifestError("issuer is missing")

    scope = payload["scope"]
    if not isinstance(scope, str):
        raise AuthorizationManifestError("scope is not a string")

    return AuthorizationManifest(
        scope=scope,
        approved_main_sha=_sha(payload["approved_main_sha"], "approved main SHA"),
        checkpoint_implementation_digest=_digest(
            payload["checkpoint_implementation_digest"], "implementation digest"
        ),
        allowed_broker_operations=tuple(operations),
        write_limits=checked,
        maximum_sessions=sessions,
        checkpoint_observation_id=observation_id,
        issued_at=_timestamp(payload["issued_at"], "issued_at"),
        expires_at=_timestamp(payload["expires_at"], "expires_at"),
        issued_by=issued_by,
    )


def load_manifest(path: Path) -> AuthorizationManifest:
    """Read and parse a manifest file. Never consumes anything.

    Decoded with ``utf-8-sig``, which accepts UTF-8 both with and without a
    byte-order mark. This is not laxity: the operator authoring a manifest is
    frequently on Windows, where PowerShell's ``>``, ``Out-File``, and
    ``Set-Content`` all emit a BOM by default, so a BOM was the *default*
    outcome of writing this file the obvious way. It cost a real checkpoint
    preparation on 2026-08-03, where a correctly-authored manifest was refused
    as malformed JSON.

    Nothing else is relaxed. ``utf-8-sig`` strips a leading BOM and is
    otherwise identical to ``utf-8``, so malformed JSON, a wrong field set, a
    wrong commit, a wrong digest, and every other refusal are unchanged. A
    manifest that fails here is **not** consumed — the ledger is only touched
    by ``ManifestAuthorization``, which runs after this returns — so an
    operator can fix the encoding and reuse the same identity.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        # A file that is not UTF-8 at all is unreadable as a manifest, and is
        # refused as such rather than escaping as a decoding traceback.
        raise AuthorizationManifestError("manifest is absent or unreadable") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise AuthorizationManifestError("manifest is not valid JSON") from exc
    return parse_manifest(payload)


def validate_manifest(
    manifest: AuthorizationManifest,
    *,
    repository_root: Path,
    checkpoint_observation_id: str,
    now: datetime,
    head_sha: str | None = None,
    implementation_digest: str | None = None,
) -> None:
    """Every check is independent, and all of them must pass.

    Ordered cheapest-and-most-specific first so a refusal names the narrowest
    true reason, but no check is skipped on the strength of another.
    """
    if manifest.scope != READ_ONLY_SCOPE:
        raise AuthorizationManifestError("manifest scope is not read-only observation")

    if manifest.issued_by.strip().lower() in {"agent", "claude", "automation", "system"}:
        # Not a security control on its own — an operator-created manifest is
        # enforced by this module having no write path at all — but a manifest
        # that *says* an agent issued it is refused rather than honoured.
        raise AuthorizationManifestError("manifest was not operator-issued")

    if set(manifest.allowed_broker_operations) != set(PERMITTED_READ_TOOLS):
        raise AuthorizationManifestError(
            "manifest capability list is not the three reviewed reads"
        )
    if len(manifest.allowed_broker_operations) != len(PERMITTED_READ_TOOLS):
        raise AuthorizationManifestError("manifest capability list has duplicates")

    if set(manifest.write_limits) != set(REQUIRED_ZERO_COUNTERS):
        raise AuthorizationManifestError("manifest write-limit set is not the required set")
    for name in REQUIRED_ZERO_COUNTERS:
        if manifest.write_limits[name] != 0:
            raise AuthorizationManifestError(f"manifest permits a non-zero {name}")

    if manifest.maximum_sessions != 1:
        raise AuthorizationManifestError("manifest permits more than one session")

    if manifest.checkpoint_observation_id != checkpoint_observation_id:
        raise AuthorizationManifestError("manifest is for a different checkpoint identity")

    if now.tzinfo is None:
        raise AuthorizationManifestError("evaluation time has no timezone")
    moment = now.astimezone(timezone.utc)
    if manifest.expires_at <= manifest.issued_at:
        raise AuthorizationManifestError("manifest expires before it is issued")
    if manifest.expires_at - manifest.issued_at > MAXIMUM_VALIDITY:
        raise AuthorizationManifestError("manifest validity window is too long")
    if moment < manifest.issued_at:
        raise AuthorizationManifestError("manifest is not yet valid")
    if moment >= manifest.expires_at:
        raise AuthorizationManifestError("manifest has expired")

    actual_sha = head_sha if head_sha is not None else resolve_head_sha(repository_root)
    if manifest.approved_main_sha != actual_sha:
        raise AuthorizationManifestError("manifest is pinned to a different commit")

    actual_digest = (
        implementation_digest
        if implementation_digest is not None
        else checkpoint_implementation_digest(repository_root)
    )
    if manifest.checkpoint_implementation_digest != actual_digest:
        # "a different implementation" means the whole reviewed closure, not
        # merely a handful of files. Any runtime module the checkpoint can
        # reach changing is enough to land here, which is the intent.
        raise AuthorizationManifestError(
            "manifest is pinned to a different implementation"
        )


# --------------------------------------------------------------------------
# One use, recorded outside sanitized evidence.
# --------------------------------------------------------------------------


class ManifestConsumptionLedger:
    """Records that an observation identity's authorization has been spent.

    Deliberately **not** inside the sanitized evidence tree. Evidence is a
    record of what a run saw; this is a control input governing whether a run
    may happen. Storing the second inside the first would mean clearing
    evidence silently restores authorization — the same class of gap that
    retired Run IDs closed for commissioning.

    Consumption uses directory creation, which is atomic on both POSIX and
    Windows, so two concurrent launches cannot both win.
    """

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise AuthorizationManifestError("ledger root is required")
        self.root = root

    def _entry(self, observation_id: str) -> Path:
        if (
            not observation_id
            or observation_id in {".", ".."}
            or observation_id != observation_id.strip()
            or any(character in observation_id for character in "/\\:*?\"<>|")
        ):
            raise AuthorizationManifestError("observation ID is not a safe path component")
        return self.root / observation_id

    def is_consumed(self, observation_id: str) -> bool:
        return self._entry(observation_id).exists()

    def consume(self, observation_id: str) -> None:
        entry = self._entry(observation_id)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            entry.mkdir()
        except FileExistsError as exc:
            raise AuthorizationManifestError(
                "manifest authorization has already been used"
            ) from exc


# --------------------------------------------------------------------------
# The non-interactive authorization provider.
# --------------------------------------------------------------------------


@dataclass
class ManifestAuthorization:
    """Drop-in replacement for the interactive gate, with the same shape.

    It validates once, consumes once, and then answers each stage exactly
    once — the same single-use discipline the interactive gate enforces, so
    the runner cannot tell which one it is holding and cannot be looser with
    either.

    It never returns True for a stage it was not asked about in order, and it
    never re-reads or re-validates the manifest: a manifest that became valid
    after the first refusal is still refused for this run.
    """

    manifest: AuthorizationManifest
    ledger: ManifestConsumptionLedger
    repository_root: Path
    checkpoint_observation_id: str
    now: datetime
    head_sha: str | None = None
    implementation_digest: str | None = None

    def __post_init__(self) -> None:
        validate_manifest(
            self.manifest,
            repository_root=self.repository_root,
            checkpoint_observation_id=self.checkpoint_observation_id,
            now=self.now,
            head_sha=self.head_sha,
            implementation_digest=self.implementation_digest,
        )
        if self.ledger.is_consumed(self.checkpoint_observation_id):
            raise AuthorizationManifestError(
                "manifest authorization has already been used"
            )
        self.ledger.consume(self.checkpoint_observation_id)
        self._remaining = set(AUTHORIZATION_STAGES)

    def __call__(self, stage: str) -> bool:
        if stage not in AUTHORIZATION_STAGES:
            raise AuthorizationManifestError("authorization stage is invalid")
        if stage not in self._remaining:
            raise AuthorizationManifestError("authorization is not reusable")
        self._remaining.discard(stage)
        return True


# --------------------------------------------------------------------------
# The write-enabled policy this manifest is NOT.
# --------------------------------------------------------------------------

#: A future unattended *write* milestone needs a different, richer policy.
#: Naming its required fields here is a specification, not an implementation:
#: nothing in this repository reads or honours them today, and this manifest
#: format cannot be widened into them — a write-enabled runtime must be
#: authorized by an object that does not exist yet, reviewed on its own terms.
UNATTENDED_WRITE_POLICY_REQUIRED_FIELDS: tuple[str, ...] = (
    "capital_tier",
    "strategy_version",
    "symbol_allowlist",
    "position_limits",
    "exposure_limits",
    "daily_loss_limit",
    "weekly_loss_limit",
    "drawdown_limit",
    "permitted_trading_window",
    "deployment_expiry",
    "global_kill_switch_state",
)


def assert_not_write_authorization(manifest: AuthorizationManifest) -> None:
    """A read-only manifest may never be read as write authorization."""
    if manifest.scope != READ_ONLY_SCOPE:
        raise AuthorizationManifestError("manifest scope is not read-only observation")
    for name in UNATTENDED_WRITE_POLICY_REQUIRED_FIELDS:
        if name in manifest.write_limits:
            raise AuthorizationManifestError(
                "a read-only manifest cannot carry write-policy fields"
            )


__all__ = [
    "MANIFEST_REQUIRED_KEYS",
    "MANIFEST_VERSION",
    "MAXIMUM_VALIDITY",
    "PINNED_IMPLEMENTATION_CLOSURE",
    "READ_ONLY_SCOPE",
    "UNATTENDED_WRITE_POLICY_REQUIRED_FIELDS",
    "AuthorizationManifest",
    "AuthorizationManifestError",
    "ManifestAuthorization",
    "ManifestConsumptionLedger",
    "assert_not_write_authorization",
    "checkpoint_implementation_digest",
    "load_manifest",
    "parse_manifest",
    "resolve_head_sha",
    "validate_manifest",
]
