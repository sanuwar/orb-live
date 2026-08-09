"""Canonical commissioning suspension-controller identity and durable-store
convention (Milestone 14 Issue 3).

`src/execution_safety_governance_deferred_gates.py`'s `make_suspension_gate`
wraps `DurableSuspensionController.assess` correctly and is independently
tested against the real controller API, but that module's docstring names an
honest gap: "commissioning has no registered controller identity or durable
store convention today." This module is that convention.

Two things are established here, and nothing else:

1. **Identity** -- `commissioning_suspension_controller_id` derives one
   deterministic `controller_id` from inputs this repository already treats
   as identity: a release commit SHA (the same `release_sha` field
   `CommissioningGovernanceEvidence` and `HostProcessLifecycleContract`
   already carry), an `account_fingerprint_reference` (the same sha256
   reference convention used throughout `src/broker_identity.py`,
   `src/execution_safety_governance.py`, and the `protection_*` modules --
   never a raw account number), a fixed execution domain, and a fixed
   commissioning namespace. No token, credential, or raw account identifier
   is ever an input. The same inputs always produce the same identity, which
   is what makes restart durability possible: a fresh process only finds a
   suspension a previous process recorded if it computes the same
   `controller_id` to look it up under.

2. **Durable-store binding** -- `CommissioningSuspensionStateProvider` binds
   that identity to one `DurableSuspensionController` opened against an
   explicit, caller-supplied database path. Consistent with every other
   durable store in this repository (see `src/offline_shadow_cli.py`), the
   path is never hardcoded and never defaulted inside this module, and
   `DurableSuspensionController` itself already refuses a path inside
   repository `data/`.

Deliberately **not imported** by `src/execution_safety_governance.py`. Wiring
this into `DEFAULT_COMMISSIONING_GATES` is a future milestone's explicit
decision, not a side effect of adding the identity/store foundation.

Every failure mode here fails closed, matching
`src/durable_suspension_controller.py`'s own contract: CLEAR is the only
state that permits continuation; SUSPENDED, CLEARANCE_PENDING_REVIEW,
PERMANENT_HALT, MALFORMED_STATE, an unregistered identity, and any read
exception are all treated as blocking. Nothing here ever clears a
suspension, and registering an identity that already has a row never
resets or overwrites that row -- a suspension set before a process started
is exactly as binding after the process restarts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.durable_suspension_controller import (
    ControllerRecord,
    DurableSuspensionController,
    SuspensionControllerError,
    SuspensionDecision,
)


class CommissioningSuspensionIdentityError(RuntimeError):
    """Any refusal from this module. Never carries broker, account, operator,
    or secret data -- only the same enum/text vocabulary the underlying
    controller already uses."""


#: Domain separation for the identity digest. Changing this deliberately
#: changes every derived `controller_id`, which is the point: a namespace
#: bump is how this convention would ever be intentionally rotated.
COMMISSIONING_SUSPENSION_NAMESPACE = "orb-live/commissioning-suspension/v1"

#: Restated rather than imported from `src/execution_safety_governance.py`'s
#: `AUTHORITY_CEILING`: this module must derive a stable identity even if
#: that module's own constant ever changed for unrelated policy reasons, and
#: a leaf identity module has no need to import the governance module it is
#: not wired into.
EXECUTION_DOMAIN = "ATTENDED_COMMISSIONING_ONLY"

_CONTROLLER_ID_PREFIX = "commissioning-suspension-"
_SHA1_HEX = re.compile(r"^[0-9a-f]{40}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DOMAIN_TOKEN = re.compile(r"^[A-Za-z0-9_\-/]{1,128}$")


def _validated_release_sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA1_HEX.fullmatch(value):
        raise CommissioningSuspensionIdentityError(
            "release_sha must be a 40-character lowercase hex commit SHA"
        )
    return value


def _validated_fingerprint_reference(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise CommissioningSuspensionIdentityError(
            "account_fingerprint_reference must be a 64-character lowercase "
            "sha256 hex digest, never a raw account number"
        )
    return value


def _validated_domain_token(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DOMAIN_TOKEN.fullmatch(value):
        raise CommissioningSuspensionIdentityError(f"{label} is invalid")
    return value


@dataclass(frozen=True)
class CommissioningSuspensionIdentity:
    """The resolved identity bundle. Carries the derived `controller_id` and
    the reference-only inputs that produced it -- never a raw private
    identifier."""

    controller_id: str
    release_sha: str
    account_fingerprint_reference: str
    execution_domain: str
    namespace: str

    @classmethod
    def derive(
        cls,
        *,
        release_sha: str,
        account_fingerprint_reference: str,
        execution_domain: str = EXECUTION_DOMAIN,
        namespace: str = COMMISSIONING_SUSPENSION_NAMESPACE,
    ) -> "CommissioningSuspensionIdentity":
        controller_id = commissioning_suspension_controller_id(
            release_sha=release_sha,
            account_fingerprint_reference=account_fingerprint_reference,
            execution_domain=execution_domain,
            namespace=namespace,
        )
        return cls(
            controller_id=controller_id,
            release_sha=release_sha,
            account_fingerprint_reference=account_fingerprint_reference,
            execution_domain=execution_domain,
            namespace=namespace,
        )


def commissioning_suspension_controller_id(
    *,
    release_sha: str,
    account_fingerprint_reference: str,
    execution_domain: str = EXECUTION_DOMAIN,
    namespace: str = COMMISSIONING_SUSPENSION_NAMESPACE,
) -> str:
    """Derive the one canonical commissioning suspension `controller_id`.

    Deterministic and content-derived, never operator-chosen: the same
    release/account/domain/namespace always produce the same identity, so a
    fresh process re-derives the exact identity an earlier process
    registered rather than needing that identity handed to it out of band.

    Every input is validated against a convention already established
    elsewhere in this repository (`release_sha` as a 40-hex commit SHA,
    `account_fingerprint_reference` as a 64-hex sha256 reference), so this
    can never be satisfied by a raw account number, a token, or free text.

    The result satisfies `DurableSuspensionController`'s own identifier
    contract (alphanumeric plus `-`/`_`, no whitespace, bounded length).
    """
    sha = _validated_release_sha(release_sha)
    fingerprint = _validated_fingerprint_reference(account_fingerprint_reference)
    domain = _validated_domain_token(execution_domain, "execution_domain")
    ns = _validated_domain_token(namespace, "namespace")

    digest = hashlib.sha256(
        ns.encode("utf-8")
        + b"\0"
        + domain.encode("utf-8")
        + b"\0"
        + sha.encode("utf-8")
        + b"\0"
        + fingerprint.encode("utf-8")
    ).hexdigest()
    return f"{_CONTROLLER_ID_PREFIX}{digest[:32]}"


class CommissioningSuspensionStateProvider:
    """Binds one `CommissioningSuspensionIdentity` to one durable
    `DurableSuspensionController` store, opened at a caller-supplied path.

    This is the durable-store convention the deferred suspension gate was
    missing: a fixed identity, a fixed store, and read/registration methods
    that never weaken the underlying controller's fail-closed contract.
    Nothing here can clear a suspension -- that remains
    `DurableSuspensionController.apply_clearance` /
    `complete_review`, reached only through reviewed, verified,
    non-self-declared evidence, exactly as today.
    """

    def __init__(
        self,
        *,
        database_path: Path,
        identity: CommissioningSuspensionIdentity,
    ) -> None:
        if not isinstance(identity, CommissioningSuspensionIdentity):
            raise CommissioningSuspensionIdentityError(
                "a CommissioningSuspensionIdentity is required"
            )
        self._identity = identity
        self._controller = DurableSuspensionController(database_path)

    @property
    def controller_id(self) -> str:
        return self._identity.controller_id

    @property
    def identity(self) -> CommissioningSuspensionIdentity:
        return self._identity

    def close(self) -> None:
        self._controller.close()

    def __enter__(self) -> "CommissioningSuspensionStateProvider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ensure_registered(self, *, now: datetime) -> ControllerRecord:
        """Idempotent first-use registration. Creates a CLEAR row only when
        this identity has never been seen by this store; otherwise reads the
        existing row back completely unchanged, whatever state it is in.

        This is deliberately not a way to reset a suspension: a controller
        that already has a row -- SUSPENDED or otherwise -- is returned as
        it is. A process restarting is not evidence that anything was
        reviewed.
        """
        try:
            return self._controller.load(self._identity.controller_id)
        except SuspensionControllerError:
            return self._controller.register(self._identity.controller_id, now=now)

    def recover_on_restart(self) -> ControllerRecord:
        """Read the persisted state back, unchanged. Exists so that
        "restart preserves this identity's suspension" is a tested claim
        against the commissioning identity specifically, not only against
        the underlying controller in the abstract."""
        return self._controller.load(self._identity.controller_id)

    def assess(self) -> SuspensionDecision:
        """The fail-closed decision for this identity: CLEAR is the only
        state that permits continuation. An unregistered identity, a
        malformed/corrupt row, or any read exception all resolve through
        `DurableSuspensionController.assess` to a blocking decision --
        never silently to CLEAR."""
        return self._controller.assess(self._identity.controller_id)

    def make_gate(self):
        """Build the real `execution_safety_governance` gate evaluator for
        this identity and store, using the already-tested adapter in
        `src/execution_safety_governance_deferred_gates.py`.

        Constructing this does not wire anything into
        `DEFAULT_COMMISSIONING_GATES` -- the caller decides what to do with
        the returned evaluator. This method only proves the identity and
        store this module establishes are actually sufficient to build one.
        """
        from src.execution_safety_governance_deferred_gates import (
            make_suspension_gate,
        )

        return make_suspension_gate(
            controller=self._controller, controller_id=self._identity.controller_id
        )


__all__ = [
    "COMMISSIONING_SUSPENSION_NAMESPACE",
    "EXECUTION_DOMAIN",
    "CommissioningSuspensionIdentity",
    "CommissioningSuspensionIdentityError",
    "CommissioningSuspensionStateProvider",
    "commissioning_suspension_controller_id",
]
