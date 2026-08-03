"""Tracked launcher for the Milestone 9 attended read-only observation checkpoint.

This launcher performs **broker reads only**. It has no placement, cancellation,
replacement, or strategy-activation path, and it never constructs a
write-capable adapter. The only broker capability it builds is
`RestrictedReadOnlyReader`, which can express exactly three read calls.

It is tracked rather than delivered as an untracked day-of wrapper, because
unlike a commissioning wrapper it carries reviewed safety behaviour rather than
a date and a Run ID. Nothing here changes between runs except parameters.

Executing this file requires:

* `--i-understand-this-performs-real-broker-reads`,
* `ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION=1` in the environment,
* `--authorization AUTHORIZE_MILESTONE_9_READ_ONLY_OBSERVATION`,
* an unused Run ID, and
* **exactly one** authorization mode.

Absent any one of them it refuses and performs no read.

## Two authorization modes, and no default

`--authorization-manifest PATH` — the scheduled, unattended mode. An
operator-issued manifest pins scope, commit, implementation digest, capability
list, zero write limits, one session, observation identity, and an expiry
window. It is validated and consumed before any read. This is not an
always-yes mode and nothing here types an answer: the manifest authorizes one
checkpoint identity, once.

`--interactive` — manual development only. Two hidden `y/N` prompts, both
defaulting to decline.

Supplying **neither** is refused, and supplying **both** is refused. That is
deliberate: a scheduled run must never silently fall back to a prompt nobody
is present to answer, and a manual run must never silently skip the manifest.

The environment flag is necessary and **not sufficient** — its presence is
never itself approval.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.read_only_observation_runtime import (  # noqa: E402
    ATTENDED_MODE,
    AUTHORIZATION_STAGES,
    CALENDAR_EXPIRES_AFTER,
    CALENDAR_SOURCE,
    CALENDAR_SUPPORTED_RANGE,
    EASTERN,
    READ_ONLY_AUTHORIZATION_FLAG,
    UNATTENDED_MODE,
    CheckpointOutcome,
    CheckpointResult,
    PreResolvedAuthorization,
    ReadOnlyAuthorizationGate,
    ReadOnlyCheckpointRequest,
    ReadOnlyEvidenceStore,
    ReadOnlyRuntimeError,
    RestrictedReadOnlyReader,
    calendar_metadata,
    record_session_failure,
    run_read_only_observation_checkpoint,
)

REAL_READ_RUNTIME_FLAG = "ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION"
REAL_READ_ACKNOWLEDGMENT = "--i-understand-this-performs-real-broker-reads"

#: Read-only observation identities that have been spent or abandoned. As with
#: the commissioning launcher, this tracked list is the only authority: an
#: on-disk evidence directory is corroboration, never protection.
RETIRED_OBSERVATION_RUN_IDS: frozenset[str] = frozenset()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attended read-only broker-state observation checkpoint.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--account-number", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--expected-quantity", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--operator-present", action="store_true")
    parser.add_argument(REAL_READ_ACKNOWLEDGMENT, action="store_true", dest="real_reads")
    parser.add_argument(
        "--authorization-manifest",
        help="Operator-issued manifest authorizing one read-only observation.",
    )
    parser.add_argument(
        "--authorization-ledger",
        help="Directory recording spent manifests. Outside the evidence tree.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Manual development mode: two hidden y/N prompts.",
    )
    return parser


def _read_secret(prompt: str) -> str:
    """Hidden entry, with an explicit non-TTY path.

    `getpass` reads the console handle directly on Windows, so it hangs rather
    than failing when stdin is redirected. When stdin is not a terminal the
    line is read directly instead — a rehearsal harness gets a definite
    answer, and an unanswerable prompt still declines rather than approving.
    """
    if not sys.stdin.isatty():
        sys.stderr.write(prompt)
        sys.stderr.flush()
        line = sys.stdin.readline()
        if not line:
            return ""
        return line.rstrip("\r\n")
    return getpass.getpass(prompt)


def _build_request(namespace: argparse.Namespace) -> ReadOnlyCheckpointRequest:
    try:
        quantity = Decimal(str(namespace.expected_quantity))
    except InvalidOperation as exc:
        raise ReadOnlyRuntimeError("expected quantity is not a decimal") from exc
    if namespace.run_id in RETIRED_OBSERVATION_RUN_IDS:
        raise ReadOnlyRuntimeError("run ID is permanently retired")
    return ReadOnlyCheckpointRequest(
        run_id=namespace.run_id,
        account_number=namespace.account_number,
        symbol=namespace.symbol,
        expected_quantity=quantity,
        evidence_root=Path(namespace.evidence_root),
        authorization_flag=namespace.authorization,
        operator_present=bool(namespace.operator_present),
        authorization_mode=ATTENDED_MODE if namespace.interactive else UNATTENDED_MODE,
    )


def assert_real_read_preconditions(namespace: argparse.Namespace) -> None:
    """Every gate is independent, and all of them must be open.

    Note what the environment flag does *not* do: it never approves anything
    on its own. It is one necessary condition among several, and approval
    comes only from a manifest or a person.
    """
    if not namespace.real_reads:
        raise ReadOnlyRuntimeError("real-read acknowledgment is absent")
    if os.environ.get(REAL_READ_RUNTIME_FLAG) != "1":
        raise ReadOnlyRuntimeError("real-read runtime flag is absent")
    if namespace.authorization != READ_ONLY_AUTHORIZATION_FLAG:
        raise ReadOnlyRuntimeError("read-only authorization flag is absent")
    if bool(namespace.authorization_manifest) == bool(namespace.interactive):
        raise ReadOnlyRuntimeError(
            "supply exactly one of --authorization-manifest or --interactive"
        )
    if namespace.authorization_manifest and not namespace.authorization_ledger:
        raise ReadOnlyRuntimeError("a manifest requires an authorization ledger")
    if namespace.interactive and not namespace.operator_present:
        raise ReadOnlyRuntimeError("interactive mode requires an operator present")


def build_authorization(
    namespace: argparse.Namespace, *, now: datetime
) -> PreResolvedAuthorization:
    """Resolve authorization **fully, before any session is opened**.

    Both modes return the same `PreResolvedAuthorization`, so the runtime
    cannot tell them apart and cannot be looser with either. Resolving up
    front is the point: a declined operator, an expired manifest, or a spent
    identity must cost zero session opens and zero broker reads.
    """
    if namespace.interactive:
        gate = ReadOnlyAuthorizationGate(secret_reader=_read_secret)
        # Asked here, at the terminal, before any transport exists.
        decisions = {stage: gate(stage) for stage in AUTHORIZATION_STAGES}
        return PreResolvedAuthorization(decisions=decisions)

    from src.read_only_authorization_manifest import (  # noqa: PLC0415
        ManifestAuthorization,
        ManifestConsumptionLedger,
        assert_not_write_authorization,
        load_manifest,
    )

    manifest = load_manifest(Path(namespace.authorization_manifest))
    assert_not_write_authorization(manifest)
    provider = ManifestAuthorization(
        manifest=manifest,
        ledger=ManifestConsumptionLedger(Path(namespace.authorization_ledger)),
        repository_root=REPOSITORY_ROOT,
        checkpoint_observation_id=namespace.run_id,
        now=now,
    )
    # Validation and consumption already happened in the constructor above; a
    # manifest that got this far approves both stages.
    return PreResolvedAuthorization(
        decisions={stage: provider(stage) for stage in AUTHORIZATION_STAGES}
    )


def _unreachable_reader() -> Any:
    """Proves the declined path never asks for a reader."""
    raise ReadOnlyRuntimeError("no session was opened")


async def _execute(
    request: ReadOnlyCheckpointRequest,
    authorization: PreResolvedAuthorization,
    *,
    session_factory: Any = None,
) -> CheckpointResult:
    """Run the checkpoint, opening a session **only** if authorized.

    A decline short-circuits before any transport exists: the runtime asks
    for authorization before it asks for a reader, so the reader factory
    below is never invoked and no session is opened.

    Session-open and session-close failures are caught and converted into a
    recorded `SESSION_UNAVAILABLE` outcome. Nothing from the transport — no
    traceback, no exception text — crosses this boundary.
    """
    if not authorization.all_approved:
        return await run_read_only_observation_checkpoint(
            request,
            reader_factory=_unreachable_reader,
            authorization=authorization,
            monotonic=time.monotonic,
            now=lambda: datetime.now(tz=EASTERN),
        )

    if session_factory is None:
        # Imported here, not at module scope, so importing this launcher —
        # which tests do — never reaches broker transport code.
        from src.robinhood_commissioning_session import (  # noqa: PLC0415
            silent_robinhood_session_factory,
        )

        session_factory = silent_robinhood_session_factory

    result: CheckpointResult | None = None
    try:
        async with session_factory() as session:
            reader = RestrictedReadOnlyReader(
                session=session,
                account_number=request.account_number,
                # Per-tool, from the reviewed plan rather than restated here,
                # so the launcher cannot grant a budget the plan does not.
                call_ceilings=request.plan.call_ceilings,
            )
            result = await run_read_only_observation_checkpoint(
                request,
                reader_factory=lambda: reader,
                authorization=authorization,
                monotonic=time.monotonic,
                now=lambda: datetime.now(tz=EASTERN),
            )
    except Exception:
        # `result is None` means the session never opened; otherwise the body
        # completed and the failure came from closing it.
        return record_session_failure(
            request, phase="open" if result is None else "close", existing=result
        )
    return result


def report(result: CheckpointResult) -> None:
    """Print the outcome and nothing broker-derived."""
    print(f"outcome: {result.outcome.value}")
    print(f"decision: {result.evidence.decision}")
    print(f"freshness: {result.evidence.freshness}")
    print(f"observed_protection_state: {result.evidence.observed_protection_state}")
    print(f"proposed_provenance: {result.evidence.proposed_provenance}")
    print(f"operator_action_required: {result.evidence.operator_action_required}")
    for name, value in sorted(result.evidence.write_counters.items()):
        print(f"{name}: {value}")
    for name, value in sorted(calendar_metadata().items()):
        print(f"{name}: {value}")
    if result.evidence_path is not None:
        print(f"evidence: {result.evidence_path}")
    print(
        f"NOTE: market_session comes from a {CALENDAR_SOURCE} covering "
        f"{CALENDAR_SUPPORTED_RANGE} only, with no authoritative feed. "
        f"It classifies nothing after {CALENDAR_EXPIRES_AFTER.isoformat()}."
    )
    print(
        "NOTE: an observation is not an approval. "
        "ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES is unchanged."
    )


def assert_preconditions_before_authorization(
    request: ReadOnlyCheckpointRequest,
) -> None:
    """Validate everything deterministic **before** authorization is spent.

    Authorization is resolved before any transport exists, which is correct and
    was a review finding in its own right (F5). But `build_authorization`
    *consumes* the manifest, while the request itself was not validated until
    the checkpoint had already started — so a blank account number alongside a
    perfectly good manifest spent the manifest and the Run ID and then finished
    as `PRECONDITION_REJECTED`. The authorization was burned to discover a
    typo.

    Everything checkable without a broker, a session, or a secret is therefore
    checked here first. A refusal at this point costs nothing: the ledger is
    untouched, the Run ID is unspent, and the operator can correct the input
    and reuse both.

    `ReadOnlyCheckpointRequest.validate()` still runs inside the checkpoint.
    This is an earlier gate, not a replacement — a caller reaching the runner
    by another path must still be validated there.
    """
    request.validate()
    # Constructing the store validates that the Run ID is a safe path
    # component; it creates nothing. `start()` is what claims the directory,
    # and that still happens inside the checkpoint.
    store = ReadOnlyEvidenceStore(request.evidence_root, request.run_id)
    if store.completed.exists():
        raise ReadOnlyRuntimeError("run ID already used")


def main(argv: Sequence[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    try:
        assert_real_read_preconditions(namespace)
        request = _build_request(namespace)
        # Before authorization, so a rejected precondition never spends it.
        assert_preconditions_before_authorization(request)
        authorization = build_authorization(
            namespace, now=datetime.now(tz=timezone.utc)
        )
    except Exception as exc:
        # Authorization refusals are printed by type, not by content: a
        # manifest's fields are operator material and do not belong in a log.
        print(f"refused: {type(exc).__name__}: {exc}")
        return 2
    try:
        result = asyncio.run(_execute(request, authorization))
    except Exception as exc:
        # Nothing should reach here — `_execute` converts session failures
        # itself. If it does, it is still an outcome, never a traceback.
        print(f"failed closed: {type(exc).__name__}")
        return 3
    report(result)
    return 0 if result.outcome in {
        CheckpointOutcome.OBSERVATION_RECORDED,
        CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED,
        CheckpointOutcome.DECLINED_BY_OPERATOR,
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
