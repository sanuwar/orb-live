"""Offline restart recovery — decide whether a interrupted run may resume.

A process that dies mid-run leaves two things behind: what it *believed* was
true, and whatever the broker actually holds. Recovery is the act of comparing
them and deciding whether the two still describe the same world.

This module makes that decision **offline**, from a durable journal and a set
of already-observed order states. It opens no session, reads no broker, and
performs no write of any kind.

## What it refuses to do

It never concludes "resume". The strongest thing it can say is
`CONSISTENT_RESUME_CANDIDATE` — the persisted and observed views agree, so
nothing *rules out* resuming. Whether a run may actually continue is a
question for a supervised-execution gate that does not exist yet, and
`permits_automated_action` is `False` on every result here.

## Why the journal holds no identifiers

Recovery needs to correlate what we recorded with what we see. The obvious way
is to persist broker order identifiers, and that is precisely what the
evidence rules forbid. So the journal records **role → last known lifecycle
state** instead: `ENTRY`, `PROTECTIVE_STOP`, `EXIT`. A run has at most one
order per role, which makes the role a sufficient key and leaves the
identifier in memory where it belongs.

The cost is real and worth stating: recovery cannot tell *which* order it is
looking at, only what a given role's state was. That is enough to decide
"stop and get a human", which is the only decision this module is allowed to
reach.

## Torn writes

A journal written by a process that died mid-write is worse than no journal,
because it looks like data. Each record carries a checksum over its own
content; a mismatch is `OPERATOR_REQUIRED_JOURNAL_CORRUPT`, never a
best-effort parse.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from src.order_lifecycle_model import (
    TERMINAL_STATES,
    LifecycleState,
    normalize_label,
    transition_is_allowed,
    transition_is_backward,
)


class RecoveryError(RuntimeError):
    """A malformed recovery input. Never carries broker text."""


class OrderRole(str, Enum):
    """A run holds at most one order per role, which is what makes the role a
    usable key without persisting an identifier."""

    ENTRY = "ENTRY"
    PROTECTIVE_STOP = "PROTECTIVE_STOP"
    EXIT = "EXIT"


class RecoveryOutcome(str, Enum):
    """Every way recovery can end. Only two are not operator-requiring."""

    #: Persisted and observed views agree and work remains. Not permission.
    CONSISTENT_RESUME_CANDIDATE = "CONSISTENT_RESUME_CANDIDATE"
    #: Everything the journal knew about has reached a terminal state.
    NO_PENDING_WORK = "NO_PENDING_WORK"

    OPERATOR_REQUIRED_JOURNAL_ABSENT = "OPERATOR_REQUIRED_JOURNAL_ABSENT"
    OPERATOR_REQUIRED_JOURNAL_CORRUPT = "OPERATOR_REQUIRED_JOURNAL_CORRUPT"
    OPERATOR_REQUIRED_JOURNAL_STALE = "OPERATOR_REQUIRED_JOURNAL_STALE"
    OPERATOR_REQUIRED_RUN_ID_MISMATCH = "OPERATOR_REQUIRED_RUN_ID_MISMATCH"
    OPERATOR_REQUIRED_SEQUENCE_REGRESSION = "OPERATOR_REQUIRED_SEQUENCE_REGRESSION"
    OPERATOR_REQUIRED_UNKNOWN_STATE = "OPERATOR_REQUIRED_UNKNOWN_STATE"
    OPERATOR_REQUIRED_IMPOSSIBLE_TRANSITION = "OPERATOR_REQUIRED_IMPOSSIBLE_TRANSITION"
    OPERATOR_REQUIRED_BACKWARD_TRANSITION = "OPERATOR_REQUIRED_BACKWARD_TRANSITION"
    OPERATOR_REQUIRED_UNEXPECTED_ROLE = "OPERATOR_REQUIRED_UNEXPECTED_ROLE"
    OPERATOR_REQUIRED_MISSING_ROLE = "OPERATOR_REQUIRED_MISSING_ROLE"
    #: A role was superseded. `REPLACED` is terminal for the old identifier,
    #: but the successor is a different order this journal never recorded — so
    #: "terminal" here would mean "nothing left to do" while an untracked
    #: order may be live at the venue.
    OPERATOR_REQUIRED_REPLACED_ROLE = "OPERATOR_REQUIRED_REPLACED_ROLE"


#: Outcomes that do not demand a human. Neither authorizes anything.
NON_ESCALATING_OUTCOMES: frozenset[RecoveryOutcome] = frozenset({
    RecoveryOutcome.CONSISTENT_RESUME_CANDIDATE,
    RecoveryOutcome.NO_PENDING_WORK,
})

RESUME_AUTHORITY_EXPLANATION = (
    "A consistent journal does not authorize resumption. Whether a run may "
    "continue is decided by a supervised-execution gate that does not exist "
    "yet; this module only establishes that nothing rules resumption out."
)


# --------------------------------------------------------------------------
# The journal.
# --------------------------------------------------------------------------

JOURNAL_VERSION = 1


@dataclass(frozen=True)
class JournalRecord:
    """One durable snapshot of what a run believed.

    Deliberately tiny. Every field is a fixed string, an int, or a float —
    there is no field capable of holding an identifier, quantity, symbol,
    price, or broker payload.
    """

    run_id: str
    sequence: int
    recorded_monotonic: float
    role_states: Mapping[str, str] = field(default_factory=dict)
    version: int = JOURNAL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise RecoveryError("a run identity is required")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise RecoveryError("sequence must be an int")
        if self.sequence < 0:
            raise RecoveryError("sequence must not be negative")
        if not isinstance(self.recorded_monotonic, (int, float)) or isinstance(
            self.recorded_monotonic, bool
        ):
            raise RecoveryError("recorded time must be a number")
        if not isinstance(self.role_states, Mapping):
            raise RecoveryError("role states must be a mapping")
        for role, state in self.role_states.items():
            if role not in {member.value for member in OrderRole}:
                raise RecoveryError("unknown order role in journal")
            if state not in {member.value for member in LifecycleState}:
                raise RecoveryError("unknown lifecycle state in journal")

    def checksum(self) -> str:
        """Over the record's own canonical content, so a torn write shows."""
        payload = json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def states(self) -> dict[OrderRole, LifecycleState]:
        return {
            OrderRole(role): LifecycleState(state)
            for role, state in self.role_states.items()
        }


class RunJournal:
    """One file, written whole or not at all.

    Uses write-temporary-then-rename rather than in-place update: a process
    killed mid-write leaves the previous good record intact rather than a
    half-updated one. The checksum catches the remaining case, where the
    filesystem hands back a file that was never fully flushed.
    """

    filename = "run_journal.json"

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise RecoveryError("a journal root is required")
        self.root = root
        self.path = root / self.filename

    def write(self, record: JournalRecord) -> Path:
        if not isinstance(record, JournalRecord):
            raise RecoveryError("a journal record is required")
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"record": asdict(record), "checksum": record.checksum()}
        temporary = self.root / f"{self.filename}.tmp"
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        return self.path

    def read(self) -> JournalRecord | None:
        """Returns None when absent. Raises when present but untrustworthy."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RecoveryError("journal is unreadable") from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise RecoveryError("journal is not valid JSON") from exc
        if not isinstance(payload, Mapping) or set(payload) != {"record", "checksum"}:
            raise RecoveryError("journal envelope is malformed")
        body = payload["record"]
        if not isinstance(body, Mapping):
            raise RecoveryError("journal record is malformed")
        expected_fields = {
            "run_id", "sequence", "recorded_monotonic", "role_states", "version",
        }
        if set(body) != expected_fields:
            raise RecoveryError("journal record shape is invalid")
        try:
            record = JournalRecord(
                run_id=body["run_id"],
                sequence=body["sequence"],
                recorded_monotonic=body["recorded_monotonic"],
                role_states=dict(body["role_states"]),
                version=body["version"],
            )
        except RecoveryError:
            raise
        except Exception as exc:
            raise RecoveryError("journal record is malformed") from exc
        if record.version != JOURNAL_VERSION:
            raise RecoveryError("journal version is unsupported")
        if record.checksum() != payload["checksum"]:
            # A record that fails its own checksum is worse than no record,
            # because it looks like data.
            raise RecoveryError("journal checksum does not match")
        return record


# --------------------------------------------------------------------------
# Observations.
# --------------------------------------------------------------------------


def normalize_role_states(observed: Mapping[Any, Any]) -> dict[OrderRole, LifecycleState]:
    """Broker labels or lifecycle states, keyed by role, normalized.

    An unrecognized label becomes `UNKNOWN` rather than being dropped: a role
    we cannot classify must remain visible to the decision, because silently
    omitting it would make an incomplete view look complete.
    """
    if not isinstance(observed, Mapping):
        raise RecoveryError("observed role states must be a mapping")
    normalized: dict[OrderRole, LifecycleState] = {}
    for role, value in observed.items():
        if isinstance(role, OrderRole):
            key = role
        elif isinstance(role, str) and role in {m.value for m in OrderRole}:
            key = OrderRole(role)
        else:
            raise RecoveryError("unknown order role in observation")
        if isinstance(value, LifecycleState):
            normalized[key] = value
        else:
            normalized[key] = normalize_label(value)
    return normalized


# --------------------------------------------------------------------------
# The decision.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryResult:
    outcome: RecoveryOutcome
    pending_roles: tuple[OrderRole, ...] = ()
    permits_automated_action: bool = False
    explanation: str = RESUME_AUTHORITY_EXPLANATION

    def __post_init__(self) -> None:
        if self.permits_automated_action:
            raise RecoveryError("recovery never authorizes automated action")

    @property
    def requires_operator(self) -> bool:
        return self.outcome not in NON_ESCALATING_OUTCOMES

    def sanitized_facts(self) -> dict[str, str]:
        """Fixed keys, fixed values. Roles are counted, never listed."""
        return {
            "recovery_outcome": self.outcome.value,
            "requires_operator": "true" if self.requires_operator else "false",
            "pending_role_count": (
                "none" if not self.pending_roles
                else "one" if len(self.pending_roles) == 1
                else "many"
            ),
            "permits_automated_action": "false",
        }


def recover(
    journal: JournalRecord | None,
    observed: Mapping[Any, Any],
    *,
    expected_run_id: str,
    current_monotonic: float,
    maximum_journal_age_seconds: float = 900.0,
    minimum_sequence: int = 0,
) -> RecoveryResult:
    """Compare a persisted view with an observed one and fail closed on drift.

    Order matters, and each step guards the next: a journal that is absent,
    corrupt, stale, or for a different run cannot support any conclusion about
    roles, and comparing roles first would produce a confident answer about
    the wrong run.
    """
    if not isinstance(expected_run_id, str) or not expected_run_id.strip():
        raise RecoveryError("an expected run identity is required")

    if journal is None:
        return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_ABSENT)
    if not isinstance(journal, JournalRecord):
        raise RecoveryError("journal must be a JournalRecord or None")

    if journal.run_id != expected_run_id:
        return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_RUN_ID_MISMATCH)
    if journal.sequence < minimum_sequence:
        # The journal is older than a record we already acted on.
        return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_SEQUENCE_REGRESSION)
    if current_monotonic < journal.recorded_monotonic:
        return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_STALE)
    if current_monotonic - journal.recorded_monotonic > maximum_journal_age_seconds:
        return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_STALE)

    persisted = journal.states()
    current = normalize_role_states(observed)

    unexpected = set(current) - set(persisted)
    if unexpected:
        # The broker holds a role this run never recorded. Something else
        # acted, or correlation is wrong; either way a human decides.
        return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_UNEXPECTED_ROLE)

    missing = {
        role for role in set(persisted) - set(current)
        if persisted[role] not in TERMINAL_STATES
    }
    if missing:
        # A non-terminal role we recorded has vanished from the observation.
        return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_MISSING_ROLE)

    for role, previous_state in persisted.items():
        if role not in current:
            continue
        observed_state = current[role]
        if observed_state is LifecycleState.UNKNOWN:
            return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_UNKNOWN_STATE)
        if observed_state is LifecycleState.REPLACED:
            # Terminal for this identifier, but the successor is a different
            # order the journal never recorded. Counting it as "done" would
            # report no pending work while an untracked order may be resting
            # at the venue — the exact fail-open this check exists to prevent.
            return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_REPLACED_ROLE)
        if observed_state is previous_state:
            continue
        if transition_is_backward(previous_state, observed_state):
            return RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_BACKWARD_TRANSITION)
        if not transition_is_allowed(previous_state, observed_state):
            return RecoveryResult(
                RecoveryOutcome.OPERATOR_REQUIRED_IMPOSSIBLE_TRANSITION
            )

    pending = tuple(
        sorted(
            (role for role, state in current.items() if state not in TERMINAL_STATES),
            key=lambda item: item.value,
        )
    )
    if not pending:
        return RecoveryResult(RecoveryOutcome.NO_PENDING_WORK)
    return RecoveryResult(RecoveryOutcome.CONSISTENT_RESUME_CANDIDATE, pending)


__all__ = [
    "JOURNAL_VERSION",
    "NON_ESCALATING_OUTCOMES",
    "RESUME_AUTHORITY_EXPLANATION",
    "JournalRecord",
    "OrderRole",
    "RecoveryError",
    "RecoveryOutcome",
    "RecoveryResult",
    "RunJournal",
    "normalize_role_states",
    "recover",
]
