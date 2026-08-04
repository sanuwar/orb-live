from __future__ import annotations

import ast
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.offline_shadow_cli as cli_module
from src.execution_gates import ACTIVATION_APPROVALS
from src.offline_shadow_cli import (
    RECEIPT_KEYS,
    CliDependencies,
    CliError,
    CliReceipt,
    ExitCode,
    exit_code_for_result,
    parse_config,
    run_cli,
)
from src.offline_shadow_runner import (
    RunnerConfig,
    RunnerOutcome,
    RunnerReason,
    RunnerResult,
    RunnerState,
)
from src.read_only_broker_state import approved_active_protection_states
from tests.test_offline_shadow_runner import close_stack, stack


NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
MODULE_PATH = Path(cli_module.__file__)


def argv(tmp_path: Path, *, ticks: tuple[datetime, ...] = (NOW,), maximum: int = 3) -> list[str]:
    values = [
        "--runner-id", "runner-one",
        "--max-ticks", str(maximum),
        "--session-date", "2026-08-03",
        "--runner-db", str((tmp_path / "runner.sqlite").resolve()),
        "--driver-db", str((tmp_path / "driver.sqlite").resolve()),
        "--receipt", str((tmp_path / "receipt.json").resolve()),
        "--mode", "offline-shadow",
    ]
    for tick in ticks:
        values.extend(("--tick", tick.isoformat()))
    return values


@dataclass
class FixedRunner:
    config: RunnerConfig
    result: RunnerResult
    calls: int = 0
    closes: int = 0

    def execute(self) -> RunnerResult:
        self.calls += 1
        return self.result

    def close(self) -> None:
        self.closes += 1


def result(outcome: RunnerOutcome) -> RunnerResult:
    values = {
        RunnerOutcome.COMPLETED_BOUND: (RunnerState.COMPLETED, RunnerReason.DRIVER_COMPLETE),
        RunnerOutcome.STOPPED_ON_REVIEW: (RunnerState.COMPLETED, RunnerReason.DRIVER_REVIEW_REQUIRED),
        RunnerOutcome.STOPPED_ON_SUSPENSION: (RunnerState.COMPLETED, RunnerReason.DRIVER_SUSPENDED),
        RunnerOutcome.STOPPED_ON_FAILURE: (RunnerState.FAILED_CLOSED, RunnerReason.DRIVER_FAILED),
        RunnerOutcome.FAILED_CLOSED: (RunnerState.FAILED_CLOSED, RunnerReason.DURABLE_STATE_INVALID),
        RunnerOutcome.STOPPED_ON_AMBIGUITY: (RunnerState.AMBIGUOUS_RESTART, RunnerReason.DRIVER_AMBIGUOUS),
        RunnerOutcome.EXPIRED: (RunnerState.EXPIRED, RunnerReason.DRIVER_EXPIRED),
    }
    state, reason = values[outcome]
    return RunnerResult("runner-one", state, outcome, reason, 1, 1, 4, "driver-safe")


def invoke_fixed(tmp_path: Path, selected: RunnerOutcome) -> tuple[int, FixedRunner, str, str]:
    config, _ = parse_config(argv(tmp_path))
    fixed = FixedRunner(config, result(selected))
    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_cli(
        argv(tmp_path),
        dependencies=CliDependencies(lambda received: fixed),
        stdout=stdout,
        stderr=stderr,
    )
    return code, fixed, stdout.getvalue(), stderr.getvalue()


def test_valid_one_tick_invokes_real_persistent_runner_once(tmp_path: Path) -> None:
    made = []

    def build(config: RunnerConfig):
        items = stack(tmp_path, moments=config.tick_timestamps)
        assert items[0] == config
        made.append(items)
        return items[1]

    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_cli(argv(tmp_path), dependencies=CliDependencies(build), stdout=stdout, stderr=stderr)
    assert code == ExitCode.COMPLETED_SAFELY
    assert stderr.getvalue() == ""
    assert made[0][3].cycle_count == 1
    payload = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert set(payload) == RECEIPT_KEYS
    assert payload["completed_tick_count"] == 1
    assert payload["permits_automated_action"] is False
    assert stdout.getvalue() == CliReceipt(**{
        "runner_id": payload["runner_id"],
        "runner_state": RunnerState(payload["runner_state"]),
        "runner_outcome": RunnerOutcome(payload["runner_outcome"]),
        "runner_reason": RunnerReason(payload["runner_reason"]),
        "completed_tick_count": payload["completed_tick_count"],
        "next_tick_index": payload["next_tick_index"],
        "transition_sequence": payload["transition_sequence"],
        "exit_code": ExitCode(payload["exit_code"]),
    }).serialize() + "\n"
    close_stack(made[0])


def test_valid_finite_multi_tick_invokes_each_real_tick_once(tmp_path: Path) -> None:
    ticks = (NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2))
    made = []

    def build(config: RunnerConfig):
        items = stack(tmp_path, moments=config.tick_timestamps)
        assert items[0] == config
        made.append(items)
        return items[1]

    code = run_cli(
        argv(tmp_path, ticks=ticks), dependencies=CliDependencies(build),
        stdout=io.StringIO(), stderr=io.StringIO(),
    )
    assert code == ExitCode.COMPLETED_SAFELY
    assert made[0][3].cycle_count == 3
    close_stack(made[0])


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (RunnerOutcome.COMPLETED_BOUND, ExitCode.COMPLETED_SAFELY),
        (RunnerOutcome.STOPPED_ON_REVIEW, ExitCode.OPERATOR_REVIEW_REQUIRED),
        (RunnerOutcome.STOPPED_ON_SUSPENSION, ExitCode.SUSPENDED),
        (RunnerOutcome.STOPPED_ON_FAILURE, ExitCode.FAILED_CLOSED),
        (RunnerOutcome.FAILED_CLOSED, ExitCode.FAILED_CLOSED),
        (RunnerOutcome.EXPIRED, ExitCode.FAILED_CLOSED),
        (RunnerOutcome.STOPPED_ON_AMBIGUITY, ExitCode.AMBIGUOUS_RESTART),
    ],
)
def test_every_runner_outcome_has_fixed_non_escalating_exit_code(tmp_path: Path, outcome: RunnerOutcome, expected: ExitCode) -> None:
    code, fixed, stdout, stderr = invoke_fixed(tmp_path, outcome)
    assert code == expected
    assert fixed.calls == 1
    assert fixed.closes == 1
    assert stderr == ""
    assert json.loads(stdout)["exit_code"] == expected


def test_ambiguity_and_suspension_can_never_return_success(tmp_path: Path) -> None:
    for outcome in (RunnerOutcome.STOPPED_ON_AMBIGUITY, RunnerOutcome.STOPPED_ON_SUSPENSION):
        location = tmp_path / outcome.value
        location.mkdir()
        code, *_ = invoke_fixed(location, outcome)
        assert code != ExitCode.COMPLETED_SAFELY


@pytest.mark.parametrize(
    "mutation",
    [
        lambda values: values + ["--unknown", "PRIVATE-account-value"],
        lambda values: ["--runner-id", "bad"] + values[2:],
        lambda values: ["--max-ticks", "0"] + values[2:],
        lambda values: ["--max-ticks", "1001"] + values[2:],
        lambda values: values + ["--tick", "2026-08-03T14:00:00"],
        lambda values: values + ["--tick", NOW.isoformat()],
    ],
)
def test_invalid_arguments_are_fixed_and_do_not_call_runner(tmp_path: Path, mutation) -> None:
    called = []
    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_cli(
        mutation(argv(tmp_path)),
        dependencies=CliDependencies(lambda config: called.append(config)),
        stdout=stdout,
        stderr=stderr,
    )
    assert code == ExitCode.INVALID_INVOCATION
    assert called == []
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "INVALID_INVOCATION\n"


def test_tick_list_cannot_exceed_bound(tmp_path: Path) -> None:
    with pytest.raises(CliError):
        parse_config(argv(tmp_path, ticks=(NOW, NOW + timedelta(minutes=1)), maximum=1))


@pytest.mark.parametrize("field", ["--runner-db", "--driver-db"])
def test_memory_repository_and_production_database_paths_are_rejected(tmp_path: Path, field: str) -> None:
    for bad in (":memory:", str((Path.cwd() / "data" / "x.sqlite").resolve()), str((tmp_path / "production" / "x.sqlite").resolve())):
        values = argv(tmp_path)
        values[values.index(field) + 1] = bad
        with pytest.raises(CliError):
            parse_config(values)


def test_receipt_symlink_escape_and_existing_output_are_rejected(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text("existing", encoding="utf-8")
    with pytest.raises(CliError):
        parse_config(argv(tmp_path))


def test_symlinked_repository_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(Path.cwd(), target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable")
    values = argv(tmp_path)
    values[values.index("--runner-db") + 1] = str((alias / "data" / "x.sqlite").absolute())
    with pytest.raises(CliError):
        parse_config(values)


def test_raw_factory_exception_is_never_printed_or_persisted(tmp_path: Path) -> None:
    def raised(config):
        raise RuntimeError("PRIVATE account-123 token-secret")

    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_cli(argv(tmp_path), dependencies=CliDependencies(raised), stdout=stdout, stderr=stderr)
    assert code == ExitCode.FAILED_CLOSED
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "FAILED_CLOSED\n"
    assert not (tmp_path / "receipt.json").exists()


def test_wrong_runner_result_identity_fails_closed_without_receipt(tmp_path: Path) -> None:
    config, _ = parse_config(argv(tmp_path))
    wrong = RunnerResult(
        "runner-other", RunnerState.COMPLETED, RunnerOutcome.COMPLETED_BOUND,
        RunnerReason.DRIVER_COMPLETE, 1, 1, 4, "driver-safe",
    )
    fixed = FixedRunner(config, wrong)
    stderr = io.StringIO()
    code = run_cli(
        argv(tmp_path), dependencies=CliDependencies(lambda received: fixed),
        stdout=io.StringIO(), stderr=stderr,
    )
    assert code == ExitCode.FAILED_CLOSED
    assert stderr.getvalue() == "FAILED_CLOSED\n"
    assert not (tmp_path / "receipt.json").exists()


def test_partial_receipt_publish_fails_closed_without_visible_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = parse_config(argv(tmp_path))
    fixed = FixedRunner(config, result(RunnerOutcome.COMPLETED_BOUND))
    monkeypatch.setattr(cli_module.os, "link", lambda source, target: (_ for _ in ()).throw(OSError("PRIVATE token")))
    stderr = io.StringIO()
    code = run_cli(
        argv(tmp_path), dependencies=CliDependencies(lambda received: fixed),
        stdout=io.StringIO(), stderr=stderr,
    )
    assert code == ExitCode.FAILED_CLOSED
    assert stderr.getvalue() == "FAILED_CLOSED\n"
    assert not (tmp_path / "receipt.json").exists()
    assert (tmp_path / ".receipt.json.pending").exists()


def test_cleanup_failure_after_publication_cannot_contradict_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = parse_config(argv(tmp_path))
    fixed = FixedRunner(config, result(RunnerOutcome.COMPLETED_BOUND))
    monkeypatch.setattr(cli_module.os, "unlink", lambda path: (_ for _ in ()).throw(OSError("PRIVATE token")))
    code = run_cli(
        argv(tmp_path), dependencies=CliDependencies(lambda received: fixed),
        stdout=io.StringIO(), stderr=io.StringIO(),
    )
    assert code == ExitCode.COMPLETED_SAFELY
    assert json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))["exit_code"] == 0
    assert (tmp_path / ".receipt.json.pending").exists()


def test_stdout_failure_after_publication_cannot_contradict_receipt(tmp_path: Path) -> None:
    class BrokenOutput(io.StringIO):
        def write(self, value: str) -> int:
            raise OSError("PRIVATE account")

    config, _ = parse_config(argv(tmp_path))
    fixed = FixedRunner(config, result(RunnerOutcome.COMPLETED_BOUND))
    code = run_cli(
        argv(tmp_path), dependencies=CliDependencies(lambda received: fixed),
        stdout=BrokenOutput(), stderr=io.StringIO(),
    )
    assert code == ExitCode.COMPLETED_SAFELY
    assert json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))["exit_code"] == 0


def test_publication_attempts_directory_metadata_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli_module, "_sync_directory_best_effort", lambda directory: calls.append(directory))
    config, _ = parse_config(argv(tmp_path))
    fixed = FixedRunner(config, result(RunnerOutcome.COMPLETED_BOUND))
    assert run_cli(
        argv(tmp_path), dependencies=CliDependencies(lambda received: fixed),
        stdout=io.StringIO(), stderr=io.StringIO(),
    ) == ExitCode.COMPLETED_SAFELY
    assert calls == [tmp_path.resolve(), tmp_path.resolve()]


def test_directory_sync_exception_after_commit_cannot_change_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "_sync_directory_best_effort",
        lambda directory: (_ for _ in ()).throw(OSError("PRIVATE account")),
    )
    config, _ = parse_config(argv(tmp_path))
    fixed = FixedRunner(config, result(RunnerOutcome.COMPLETED_BOUND))
    code = run_cli(
        argv(tmp_path), dependencies=CliDependencies(lambda received: fixed),
        stdout=io.StringIO(), stderr=io.StringIO(),
    )
    assert code == ExitCode.COMPLETED_SAFELY
    assert json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))["exit_code"] == 0


def test_private_receipt_filename_is_rejected(tmp_path: Path) -> None:
    values = argv(tmp_path)
    values[values.index("--receipt") + 1] = str((tmp_path / "account-123.json").resolve())
    with pytest.raises(CliError):
        parse_config(values)


def test_receipt_is_exact_allowlisted_and_permission_is_structurally_false() -> None:
    receipt = CliReceipt("runner-one", RunnerState.COMPLETED, RunnerOutcome.COMPLETED_BOUND, RunnerReason.BOUND_EXHAUSTED, 1, 1, 4, ExitCode.COMPLETED_SAFELY)
    assert set(receipt.to_dict()) == RECEIPT_KEYS
    assert receipt.serialize() == receipt.serialize()
    with pytest.raises(CliError):
        CliReceipt("runner-one", RunnerState.COMPLETED, RunnerOutcome.COMPLETED_BOUND, RunnerReason.BOUND_EXHAUSTED, 1, 1, 4, ExitCode.COMPLETED_SAFELY, True)
    with pytest.raises(CliError):
        CliReceipt("runner-one", "PRIVATE account", RunnerOutcome.COMPLETED_BOUND, RunnerReason.BOUND_EXHAUSTED, 1, 1, 4, ExitCode.COMPLETED_SAFELY)  # type: ignore[arg-type]


def test_source_has_no_hidden_retry_loop_sleep_clock_broker_network_manifest_or_service() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint({"requests", "httpx", "socket", "smtplib", "subprocess", "robin_stocks"})
    assert not any(isinstance(node, ast.While) for node in ast.walk(tree))
    lowered = source.lower()
    for forbidden in (
        "datetime.now", "datetime.utcnow", "time.time", "time.monotonic", ".sleep(",
        "authorization_manifest", "activation_approvals", "clear_suspension",
        "task scheduler", "service installation", "create_order", "submit_order",
    ):
        assert forbidden not in lowered


def test_global_safety_invariants_remain_closed() -> None:
    assert ACTIVATION_APPROVALS == frozenset()
    assert approved_active_protection_states() == frozenset()
    assert "enabled: false" in Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
