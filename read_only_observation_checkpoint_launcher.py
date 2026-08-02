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
* two hidden `y/N` answers at the terminal, both defaulting to decline.

Absent any one of them it refuses and performs no read.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.read_only_observation_runtime import (  # noqa: E402
    EASTERN,
    READ_ONLY_AUTHORIZATION_FLAG,
    CheckpointOutcome,
    CheckpointResult,
    ReadOnlyAuthorizationGate,
    ReadOnlyCheckpointRequest,
    ReadOnlyRuntimeError,
    RestrictedReadOnlyReader,
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
    )


def assert_real_read_preconditions(namespace: argparse.Namespace) -> None:
    """Every gate is independent, and all of them must be open."""
    if not namespace.real_reads:
        raise ReadOnlyRuntimeError("real-read acknowledgment is absent")
    if os.environ.get(REAL_READ_RUNTIME_FLAG) != "1":
        raise ReadOnlyRuntimeError("real-read runtime flag is absent")
    if namespace.authorization != READ_ONLY_AUTHORIZATION_FLAG:
        raise ReadOnlyRuntimeError("read-only authorization flag is absent")
    if not namespace.operator_present:
        raise ReadOnlyRuntimeError("an operator must be present")


async def _run_with_session(request: ReadOnlyCheckpointRequest) -> CheckpointResult:
    """Open one session, hand a *restricted reader* to the runtime, close it.

    The session factory is imported here rather than at module scope so that
    importing this launcher — which tests do — never reaches broker
    transport code. The session object itself never leaves this function: the
    runtime receives only the restricted reader.
    """
    from src.robinhood_commissioning_session import (  # noqa: PLC0415
        silent_robinhood_session_factory,
    )

    gate = ReadOnlyAuthorizationGate(secret_reader=_read_secret)
    async with silent_robinhood_session_factory() as session:
        reader = RestrictedReadOnlyReader(
            session=session,
            account_number=request.account_number,
            maximum_calls_per_tool=1,
        )
        return await run_read_only_observation_checkpoint(
            request,
            reader_factory=lambda: reader,
            authorization=gate,
            monotonic=time.monotonic,
            now=lambda: datetime.now(tz=EASTERN),
        )


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
    if result.evidence_path is not None:
        print(f"evidence: {result.evidence_path}")
    print(
        "NOTE: an observation is not an approval. "
        "ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES is unchanged."
    )


def main(argv: Sequence[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    try:
        assert_real_read_preconditions(namespace)
        request = _build_request(namespace)
    except ReadOnlyRuntimeError as exc:
        print(f"refused: {exc}")
        return 2
    result = asyncio.run(_run_with_session(request))
    report(result)
    return 0 if result.outcome in {
        CheckpointOutcome.OBSERVATION_RECORDED,
        CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED,
        CheckpointOutcome.DECLINED_BY_OPERATOR,
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
