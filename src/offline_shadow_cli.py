"""One-shot command-line boundary for the bounded offline shadow runner.

The entrypoint parses only a finite injected schedule.  Callers may inject a
typed runner factory; the process default composes only local durable stores
and deliberately unavailable providers, so absent offline evidence fails
closed.  It has no clock, retry, service, broker, manifest, credential, or
authorization surface.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum
from pathlib import Path
from typing import Callable, Protocol, Sequence, TextIO

from src.durable_scheduler import DurableScheduler
from src.durable_suspension_controller import DurableSuspensionController, SuspensionControllerError
from src.offline_cycle_driver import DriverProviders, OfflineCycleDriver
from src.offline_safety_orchestrator import OfflineSafetyOrchestrator
from src.offline_shadow_runner import (
    BoundedOfflineShadowRunner,
    MAXIMUM_TICKS,
    ReasonSetting,
    RunnerConfig,
    RunnerMode,
    RunnerOutcome,
    RunnerReason,
    RunnerResult,
    RunnerState,
    derive_driver_tick,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_PRIVATE_PARTS = (
    "account", "credential", "email", "order", "phone", "production",
    "prod-data", "symbol", "token", "username", "webhook",
)


class CliError(RuntimeError):
    """Fixed invalid-invocation signal; never contains caller input."""


class ExitCode(IntEnum):
    COMPLETED_SAFELY = 0
    OPERATOR_REVIEW_REQUIRED = 10
    SUSPENDED = 20
    FAILED_CLOSED = 30
    AMBIGUOUS_RESTART = 40
    INVALID_INVOCATION = 50


class RunnerInvocation(Protocol):
    config: RunnerConfig

    def execute(self) -> RunnerResult: ...
    def close(self) -> None: ...


RunnerFactory = Callable[[RunnerConfig], RunnerInvocation]


@dataclass(frozen=True)
class CliDependencies:
    runner_factory: RunnerFactory

    def __post_init__(self) -> None:
        if not callable(self.runner_factory):
            raise CliError("INVALID_DEPENDENCIES")


@dataclass(frozen=True)
class CliConfig:
    runner: RunnerConfig
    scheduler_database_path: Path
    controller_database_path: Path
    orchestrator_database_path: Path
    receipt_path: Path


@dataclass
class _ProcessRunner:
    config: RunnerConfig
    runner: BoundedOfflineShadowRunner
    driver: OfflineCycleDriver
    orchestrator: OfflineSafetyOrchestrator
    scheduler: DurableScheduler
    controller: DurableSuspensionController

    def execute(self) -> RunnerResult:
        return self.runner.execute()

    def close(self) -> None:
        for collaborator in (
            self.runner, self.driver, self.orchestrator, self.scheduler, self.controller,
        ):
            try:
                collaborator.close()
            except Exception:
                pass


class _UnavailableOfflineProvider:
    """Process default: refuse absent typed offline evidence, without discovery."""

    def collect(self, context: object) -> object:
        raise CliError("OFFLINE_EVIDENCE_UNAVAILABLE")


RECEIPT_KEYS = frozenset({
    "runner_id", "runner_state", "runner_outcome", "runner_reason",
    "completed_tick_count", "next_tick_index", "transition_sequence",
    "exit_code", "permits_automated_action",
})


@dataclass(frozen=True)
class CliReceipt:
    runner_id: str
    runner_state: RunnerState
    runner_outcome: RunnerOutcome
    runner_reason: RunnerReason
    completed_tick_count: int
    next_tick_index: int
    transition_sequence: int
    exit_code: ExitCode
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.permits_automated_action is not False:
            raise CliError("AUTOMATED_ACTION_PROHIBITED")
        if not isinstance(self.exit_code, ExitCode):
            raise CliError("INVALID_RECEIPT")
        for value in (
            self.completed_tick_count, self.next_tick_index, self.transition_sequence
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise CliError("INVALID_RECEIPT")
        if not isinstance(self.runner_id, str) or not self.runner_id.startswith("runner-"):
            raise CliError("INVALID_RECEIPT")
        if not isinstance(self.runner_state, RunnerState) or not isinstance(
            self.runner_outcome, RunnerOutcome
        ) or not isinstance(self.runner_reason, RunnerReason):
            raise CliError("INVALID_RECEIPT")

    def to_dict(self) -> dict[str, object]:
        return {
            "completed_tick_count": self.completed_tick_count,
            "exit_code": int(self.exit_code),
            "next_tick_index": self.next_tick_index,
            "permits_automated_action": False,
            "runner_id": self.runner_id,
            "runner_outcome": self.runner_outcome.value,
            "runner_reason": self.runner_reason.value,
            "runner_state": self.runner_state.value,
            "transition_sequence": self.transition_sequence,
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError("INVALID_ARGUMENTS") from None

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise CliError("INVALID_ARGUMENTS") from None


def _parser() -> _Parser:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--max-ticks", required=True)
    parser.add_argument("--tick", action="append", default=[])
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--runner-db", required=True)
    parser.add_argument("--driver-db", required=True)
    parser.add_argument("--scheduler-db", required=True)
    parser.add_argument("--controller-db", required=True)
    parser.add_argument("--orchestrator-db", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--mode", required=True)
    return parser


def _parse_count(raw: object) -> int:
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise CliError("INVALID_TICK_BOUND")
    value = int(raw)
    if not 1 <= value <= MAXIMUM_TICKS:
        raise CliError("INVALID_TICK_BOUND")
    return value


def _parse_date(raw: object) -> date:
    if not isinstance(raw, str):
        raise CliError("INVALID_SESSION_DATE")
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        raise CliError("INVALID_SESSION_DATE") from None
    if value.isoformat() != raw:
        raise CliError("INVALID_SESSION_DATE")
    return value


def _parse_timestamp(raw: object) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise CliError("INVALID_TIMESTAMP")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise CliError("INVALID_TIMESTAMP") from None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise CliError("INVALID_TIMESTAMP")
    return value


def _path(raw: object, *, receipt: bool = False) -> Path:
    if not isinstance(raw, str) or not raw or raw == ":memory:":
        raise CliError("INVALID_PATH")
    path = Path(raw)
    if not path.is_absolute():
        raise CliError("INVALID_PATH")
    resolved = path.resolve(strict=False)
    try:
        if os.path.commonpath((os.fspath(resolved), os.fspath(REPOSITORY_ROOT))) == os.fspath(REPOSITORY_ROOT):
            raise CliError("INVALID_PATH")
    except ValueError:
        pass
    if any(
        any(fragment in part.casefold() for fragment in _PRIVATE_PARTS)
        or part.casefold().startswith(("prod-", "prod_", "production-", "production_"))
        for part in resolved.parts
    ):
        raise CliError("INVALID_PATH")
    expected = {".json"} if receipt else {".sqlite", ".sqlite3", ".db"}
    if resolved.suffix.casefold() not in expected:
        raise CliError("INVALID_PATH")
    if receipt and (resolved.exists() or not resolved.parent.is_dir()):
        raise CliError("INVALID_PATH")
    return resolved


def parse_config(argv: Sequence[str]) -> CliConfig:
    try:
        parsed = _parser().parse_args(list(argv))
        maximum = _parse_count(parsed.max_ticks)
        ticks = tuple(_parse_timestamp(value) for value in parsed.tick)
        session_date = _parse_date(parsed.session_date)
        if parsed.mode != "offline-shadow":
            raise CliError("INVALID_MODE")
        config = RunnerConfig(
            runner_id=parsed.runner_id,
            maximum_tick_count=maximum,
            tick_timestamps=ticks,
            session_date=session_date,
            mode=RunnerMode.OFFLINE_SHADOW,
            runner_database_path=_path(parsed.runner_db),
            driver_database_path=_path(parsed.driver_db),
            reason_setting=ReasonSetting.FIXED_CODES_ONLY,
        )
        config.validate()
        scheduler_path = _path(parsed.scheduler_db)
        controller_path = _path(parsed.controller_db)
        orchestrator_path = _path(parsed.orchestrator_db)
        receipt_path = _path(parsed.receipt, receipt=True)
        durable_paths = {
            config.runner_database_path.resolve(strict=False),
            config.driver_database_path.resolve(strict=False),
            scheduler_path, controller_path, orchestrator_path,
        }
        if len(durable_paths) != 5 or receipt_path in durable_paths:
            raise CliError("INVALID_PATH")
        return CliConfig(
            config, scheduler_path, controller_path, orchestrator_path, receipt_path,
        )
    except CliError:
        raise
    except Exception:
        raise CliError("INVALID_INVOCATION") from None


def exit_code_for_result(result: RunnerResult) -> ExitCode:
    if not isinstance(result, RunnerResult) or result.permits_automated_action is not False:
        raise CliError("INVALID_RUNNER_RESULT")
    mapping = {
        RunnerOutcome.COMPLETED_BOUND: ExitCode.COMPLETED_SAFELY,
        RunnerOutcome.STOPPED_ON_REVIEW: ExitCode.OPERATOR_REVIEW_REQUIRED,
        RunnerOutcome.STOPPED_ON_SUSPENSION: ExitCode.SUSPENDED,
        RunnerOutcome.STOPPED_ON_FAILURE: ExitCode.FAILED_CLOSED,
        RunnerOutcome.FAILED_CLOSED: ExitCode.FAILED_CLOSED,
        RunnerOutcome.EXPIRED: ExitCode.FAILED_CLOSED,
        RunnerOutcome.STOPPED_ON_AMBIGUITY: ExitCode.AMBIGUOUS_RESTART,
    }
    return mapping[result.outcome]


def _validate_result(config: RunnerConfig, result: RunnerResult) -> None:
    if result.runner_id != config.runner_id:
        raise CliError("RUNNER_RESULT_IDENTITY_MISMATCH")
    scheduled = len(config.tick_timestamps)
    if result.completed_tick_count > scheduled:
        raise CliError("RUNNER_RESULT_BOUND_MISMATCH")
    if result.completed_tick_count == 0:
        if result.current_driver_id is not None:
            raise CliError("RUNNER_RESULT_DRIVER_MISMATCH")
    else:
        expected = derive_driver_tick(
            config, result.completed_tick_count - 1,
        ).driver_id
        if result.current_driver_id != expected:
            raise CliError("RUNNER_RESULT_DRIVER_MISMATCH")
    if result.outcome is RunnerOutcome.COMPLETED_BOUND:
        if result.reason is RunnerReason.EMPTY_SCHEDULE:
            if scheduled != 0 or result.completed_tick_count != 0:
                raise CliError("RUNNER_RESULT_BOUND_MISMATCH")
        elif result.completed_tick_count != scheduled:
            raise CliError("RUNNER_RESULT_BOUND_MISMATCH")


def _process_runner(invocation: CliConfig) -> _ProcessRunner:
    """Compose only the local durable stack; unavailable evidence fails closed."""
    scheduler: DurableScheduler | None = None
    controller: DurableSuspensionController | None = None
    orchestrator: OfflineSafetyOrchestrator | None = None
    driver: OfflineCycleDriver | None = None
    runner: BoundedOfflineShadowRunner | None = None
    try:
        scheduler = DurableScheduler(invocation.scheduler_database_path)
        controller = DurableSuspensionController(invocation.controller_database_path)
        for index in range(len(invocation.runner.tick_timestamps)):
            tick = derive_driver_tick(invocation.runner, index)
            try:
                record = controller.register(tick.controller_id, now=tick.now)
            except SuspensionControllerError:
                record = controller.load(tick.controller_id)
            if record.controller_id != tick.controller_id or record.created_at != tick.now:
                raise CliError("CONTROLLER_IDENTITY_MISMATCH")
        orchestrator = OfflineSafetyOrchestrator(
            invocation.orchestrator_database_path,
            scheduler=scheduler,
            controller=controller,
        )
        providers = DriverProviders(
            calendar=_UnavailableOfflineProvider(),
            reconciliation=_UnavailableOfflineProvider(),
            risk=_UnavailableOfflineProvider(),
            monitoring=_UnavailableOfflineProvider(),
            gate=_UnavailableOfflineProvider(),
            attestations=_UnavailableOfflineProvider(),
        )
        driver = OfflineCycleDriver(
            invocation.runner.driver_database_path,
            scheduler=scheduler,
            controller=controller,
            orchestrator=orchestrator,
            providers=providers,
        )
        runner = BoundedOfflineShadowRunner(invocation.runner, driver=driver)
        return _ProcessRunner(
            invocation.runner, runner, driver, orchestrator, scheduler, controller,
        )
    except Exception:
        for collaborator in (runner, driver, orchestrator, scheduler, controller):
            if collaborator is not None:
                try:
                    collaborator.close()
                except Exception:
                    pass
        raise CliError("PROCESS_COMPOSITION_FAILED") from None


def _receipt(result: RunnerResult, code: ExitCode) -> CliReceipt:
    return CliReceipt(
        runner_id=result.runner_id,
        runner_state=result.state,
        runner_outcome=result.outcome,
        runner_reason=result.reason,
        completed_tick_count=result.completed_tick_count,
        next_tick_index=result.next_tick_index,
        transition_sequence=result.transition_sequence,
        exit_code=code,
    )


def _persist_receipt(path: Path, receipt: CliReceipt) -> None:
    payload = receipt.serialize() + "\n"
    encoded = payload.encode("utf-8")
    pending = path.with_name(f".{path.name}.pending")
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            _verify_pending_receipt(pending, encoded)
        else:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        os.link(pending, path)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise CliError("RECEIPT_PERSISTENCE_FAILED") from None
    # Publication is the commit point.  Nothing after the no-overwrite link
    # may contradict the now-visible receipt.  Directory syncing is available
    # on POSIX; Windows does not expose an openable directory handle here.
    try:
        _sync_directory_best_effort(path.parent)
    except Exception:
        pass
    try:
        os.unlink(pending)
    except Exception:
        pass
    try:
        _sync_directory_best_effort(path.parent)
    except Exception:
        pass


def _verify_pending_receipt(path: Path, expected: bytes) -> None:
    descriptor: int | None = None
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_size != len(expected):
            raise CliError("RECEIPT_RECOVERY_MISMATCH")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or os.read(descriptor, len(expected) + 1) != expected
        ):
            raise CliError("RECEIPT_RECOVERY_MISMATCH")
    except CliError:
        raise
    except Exception:
        raise CliError("RECEIPT_RECOVERY_FAILED") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass
def _sync_directory_best_effort(directory: Path) -> None:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except Exception:
        pass
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass


def run_cli(
    argv: Sequence[str],
    *,
    dependencies: CliDependencies,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    try:
        if not isinstance(dependencies, CliDependencies):
            raise CliError("INVALID_DEPENDENCIES")
        invocation = parse_config(argv)
    except Exception:
        stderr.write("INVALID_INVOCATION\n")
        return int(ExitCode.INVALID_INVOCATION)
    return _run_invocation(
        invocation, dependencies=dependencies, stdout=stdout, stderr=stderr,
    )


def _run_invocation(
    invocation: CliConfig,
    *,
    dependencies: CliDependencies,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    runner: RunnerInvocation | None = None
    config, receipt_path = invocation.runner, invocation.receipt_path
    try:
        runner = dependencies.runner_factory(config)
        if runner is None or runner.config != config:
            raise CliError("RUNNER_IDENTITY_MISMATCH")
        result = runner.execute()
        _validate_result(config, result)
        code = exit_code_for_result(result)
        receipt = _receipt(result, code)
        _persist_receipt(receipt_path, receipt)
        try:
            stdout.write(receipt.serialize() + "\n")
        except Exception:
            pass
        return int(code)
    except Exception:
        stderr.write("FAILED_CLOSED\n")
        return int(ExitCode.FAILED_CLOSED)
    finally:
        if runner is not None:
            try:
                runner.close()
            except Exception:
                pass


def main(argv: Sequence[str] | None = None, *, dependencies: CliDependencies | None = None) -> int:
    """Process entrypoint with a local, action-inert default composition."""
    selected = tuple(sys.argv[1:] if argv is None else argv)
    if dependencies is None:
        try:
            invocation = parse_config(selected)
            def build_process_runner(config: RunnerConfig) -> _ProcessRunner:
                if config != invocation.runner:
                    raise CliError("RUNNER_IDENTITY_MISMATCH")
                return _process_runner(invocation)
            dependencies = CliDependencies(build_process_runner)
        except Exception:
            sys.stderr.write("INVALID_INVOCATION\n")
            return int(ExitCode.INVALID_INVOCATION)
        return _run_invocation(
            invocation, dependencies=dependencies, stdout=sys.stdout, stderr=sys.stderr,
        )
    return run_cli(selected, dependencies=dependencies, stdout=sys.stdout, stderr=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
