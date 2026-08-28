"""Focused process-isolated tests for the M53 startup network-denial guard.

Every attempted socket action is intercepted inside the child before an OS
connect/DNS/send syscall.  No test references a live host or ORB-LIVE module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUARD_DIR = ROOT / "scripts" / "publication" / "m53_network_guard"


def _core():
    spec = importlib.util.spec_from_file_location("m53_guard_test_core", GUARD_DIR / "guard_core.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git() -> pathlib.Path:
    value = shutil.which("git")
    if value is None:
        pytest.skip("Git executable unavailable")
    path = pathlib.Path(value).resolve()
    if path.is_symlink():
        pytest.skip("platform Git resolution is a symlink and cannot be pinned by this guard")
    return path


def _repository(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "isolated-m53"
    repo.mkdir()
    subprocess.run([os.fspath(_git()), "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run([os.fspath(_git()), "config", "user.email", "guard@example.invalid"], cwd=repo, check=True, capture_output=True)
    subprocess.run([os.fspath(_git()), "config", "user.name", "Guard Test"], cwd=repo, check=True, capture_output=True)
    (repo / "probe.txt").write_text("local only\n", encoding="utf-8")
    subprocess.run([os.fspath(_git()), "add", "probe.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run([os.fspath(_git()), "commit", "-m", "local fixture"], cwd=repo, check=True, capture_output=True)
    controlled = repo / ".git" / "info" / "empty-global-config"
    controlled.write_text("", encoding="utf-8")
    return repo


def _run_guarded(tmp_path: pathlib.Path, code: str, *, token: str | None = None, expected_sha: str | None = None, log_path: pathlib.Path | None = None, checkout: pathlib.Path | None = None, extra_env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    core = _core()
    tmp_path.mkdir(parents=True, exist_ok=True)
    checkout = checkout or _repository(tmp_path)
    evidence = log_path or (tmp_path / "evidence" / "m53-network-guard.jsonl")
    if log_path is None:
        evidence.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        "PYTHONPATH": os.pathsep.join((os.fspath(GUARD_DIR), os.fspath(tmp_path))),
        "ORB_M53_GUARD_LOG": os.fspath(evidence),
        "ORB_M53_GUARD_TOKEN": core.REQUIRED_TOKEN if token is None else token,
        "ORB_M53_GUARD_EXPECTED_SHA256": core.guard_sha256() if expected_sha is None else expected_sha,
        "ORB_M53_GUARD_CHECKOUT": os.fspath(checkout),
        "ORB_M53_GUARD_GIT_EXECUTABLE": os.fspath(_git()),
    })
    if extra_env:
        environment.update(extra_env)
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, env=environment, cwd=tmp_path, timeout=20)
    return result, evidence


def _events(path: pathlib.Path) -> list[dict[str, object]]:
    return _core().verify_evidence_log(path)


def test_activates_before_benign_target_module_and_exits_normally(tmp_path: pathlib.Path) -> None:
    (tmp_path / "benign_target.py").write_text("import pathlib\npathlib.Path('target-marker').write_text('imported')\n", encoding="utf-8")
    result, evidence = _run_guarded(tmp_path, "import benign_target")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "target-marker").read_text(encoding="utf-8") == "imported"
    events = _events(evidence)
    assert [event["event"] for event in events][0] == "activation"
    assert events[-1]["event"] == "normal_interpreter_exit"
    assert not any(event["event"] == "denied_network" for event in events)


@pytest.mark.parametrize("kwargs", [
    {"log_path": pathlib.Path("relative.jsonl")},
    {"log_path": pathlib.Path("C:/definitely-absent-parent/m53-network-guard.jsonl")},
])
def test_missing_or_invalid_evidence_destination_fails_closed(tmp_path: pathlib.Path, kwargs: dict[str, pathlib.Path]) -> None:
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", **kwargs)
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


def test_existing_evidence_destination_fails_closed(tmp_path: pathlib.Path) -> None:
    evidence = tmp_path / "evidence" / "m53-network-guard.jsonl"
    evidence.parent.mkdir()
    evidence.write_text("not a fresh evidence file", encoding="utf-8")
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", log_path=evidence)
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


@pytest.mark.parametrize("token, expected_sha", [("wrong", None), (None, "0" * 64)])
def test_invalid_token_or_hash_fails_closed(tmp_path: pathlib.Path, token: str | None, expected_sha: str | None) -> None:
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", token=token, expected_sha=expected_sha)
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


@pytest.mark.parametrize("code, operation", [
    ("import socket\nsocket.getaddrinfo('guard-test.invalid', 443)", "socket.getaddrinfo"),
    ("import socket\ns=socket.socket()\ns.connect(('guard-test.invalid', 443))", "socket.connect"),
    ("import socket\ns=socket.socket()\ns.connect_ex(('guard-test.invalid', 443))", "socket.connect_ex"),
    ("import socket\ns=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\ns.sendto(b'x', ('guard-test.invalid', 9))", "socket.sendto"),
])
def test_dns_and_socket_operations_are_denied_before_os_io(tmp_path: pathlib.Path, code: str, operation: str) -> None:
    result, evidence = _run_guarded(tmp_path, code)
    assert result.returncode != 0
    event = next(event for event in _events(evidence) if event["event"] == "denied_network")
    assert event["operation"] == operation


def test_http_library_reaches_denied_socket_layer_without_external_traffic(tmp_path: pathlib.Path) -> None:
    result, evidence = _run_guarded(tmp_path, "import urllib.request\nurllib.request.urlopen('http://guard-test.invalid/')")
    assert result.returncode != 0
    assert any(event["event"] == "denied_network" for event in _events(evidence))


@pytest.mark.parametrize("code", [
    "import subprocess\nsubprocess.run(['unapproved-descendant'])",
    "import subprocess\nsubprocess.run('echo denied', shell=True)",
    "import subprocess,sys\nsubprocess.run([sys.executable, '-c', 'print(1)'])",
    "import subprocess\nsubprocess.run(['powershell', '-NoProfile'])",
    "import subprocess\nsubprocess.run(['cmd', '/c', 'echo denied'])",
    "import subprocess\nsubprocess.run(['curl', 'guard-test.invalid'])",
])
def test_unapproved_descendants_are_denied_without_launching(tmp_path: pathlib.Path, code: str) -> None:
    result, evidence = _run_guarded(tmp_path, code)
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


def test_exact_local_git_provenance_commands_are_allowed(tmp_path: pathlib.Path) -> None:
    repo = _repository(tmp_path)
    code = "import subprocess\nsubprocess.run(['git','rev-parse','HEAD'], cwd=r'%s', check=True)\nsubprocess.run(['git','status','--porcelain','--','src/cross_sectional_momentum_research.py','src/momentum_risk_control_research.py','scripts/research_momentum_risk_control.py'], cwd=r'%s', check=True)" % (repo, repo)
    result, evidence = _run_guarded(tmp_path, code, checkout=repo)
    assert result.returncode == 0, result.stderr
    allowed = [event for event in _events(evidence) if event["event"] == "allowed_subprocess"]
    assert [event["command_arguments"] for event in allowed] == [["git", "rev-parse", "HEAD"], ["git", "status", "--porcelain", "--", "src/cross_sectional_momentum_research.py", "src/momentum_risk_control_research.py", "scripts/research_momentum_risk_control.py"]]


@pytest.mark.parametrize("command", [
    "['git','status']",
    "['git','rev-parse','HEAD','--verify']",
    "['git','config','--global','alias.x=!curl guard-test.invalid']",
    "['git','-c','core.hooksPath=x','status','--porcelain','--','src/cross_sectional_momentum_research.py','src/momentum_risk_control_research.py','scripts/research_momentum_risk_control.py']",
])
def test_near_match_remote_config_alias_and_hook_git_commands_are_denied(tmp_path: pathlib.Path, command: str) -> None:
    repo = _repository(tmp_path)
    result, evidence = _run_guarded(tmp_path, "import subprocess\nsubprocess.run(%s, cwd=r'%s')" % (command, repo), checkout=repo)
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


def test_wrong_working_directory_is_denied(tmp_path: pathlib.Path) -> None:
    repo = _repository(tmp_path)
    result, evidence = _run_guarded(tmp_path, "import subprocess\nsubprocess.run(['git','rev-parse','HEAD'], cwd=r'%s')" % tmp_path, checkout=repo)
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


def test_evidence_redacts_secret_values_and_detects_tampering(tmp_path: pathlib.Path) -> None:
    secret = "never-log-this-secret"
    raw_cache_content = "never-log-this-raw-cache-content"
    username = os.environ.get("USERNAME", "never-log-this-username")
    result, evidence = _run_guarded(tmp_path, "import subprocess\nsubprocess.run(['curl', 'token=%s', 'cache=%s'])" % (secret, raw_cache_content), extra_env={"APCA_API_SECRET_KEY": secret})
    assert result.returncode != 0
    raw = evidence.read_text(encoding="utf-8")
    assert secret not in raw
    assert raw_cache_content not in raw
    assert username not in raw
    records = _events(evidence)
    assert records[0]["environment_variable_names"] == ["APCA_API_SECRET_KEY"]
    evidence.write_text(raw.replace("denied_subprocess", "tampered_subprocess", 1), encoding="utf-8")
    with pytest.raises(Exception, match="hash chain"):
        _events(evidence)


def test_guard_source_has_only_stdlib_imports_and_repeated_runs_are_structurally_deterministic(tmp_path: pathlib.Path) -> None:
    source = (GUARD_DIR / "guard_core.py").read_text(encoding="utf-8")
    assert "src." not in source and "scripts.research" not in source
    first, first_log = _run_guarded(tmp_path / "first", "print('ok')")
    second, second_log = _run_guarded(tmp_path / "second", "print('ok')")
    assert first.returncode == second.returncode == 0
    def normalized(path: pathlib.Path) -> list[dict[str, object]]:
        return [{k: v for k, v in record.items() if k not in {"timestamp_utc", "pid", "ppid", "previous_hash", "event_hash", "working_directory", "working_directory_sha256"}} for record in _events(path)]
    assert normalized(first_log) == normalized(second_log)
