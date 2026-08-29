"""Focused process-isolated tests for the M53 startup network-denial guard.

Every attempted socket action is intercepted inside the child before an OS
connect/DNS/send syscall.  No test references a live host, opens a loopback
socket, or imports an ORB-LIVE module: network behaviour is exercised with
in-process stubs and non-resolvable ``.invalid`` values only.
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


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _run_guarded(
    tmp_path: pathlib.Path,
    code: str,
    *,
    token: str | None = None,
    expected_sha: str | None = None,
    log_path: pathlib.Path | None = None,
    checkout: pathlib.Path | None = None,
    guard_dir: pathlib.Path | None = None,
    python_path: list[pathlib.Path] | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
    core = _core()
    tmp_path.mkdir(parents=True, exist_ok=True)
    checkout = checkout or _repository(tmp_path)
    guard_dir = guard_dir or GUARD_DIR
    evidence = log_path or (tmp_path / "evidence" / "m53-network-guard.jsonl")
    if log_path is None:
        evidence.parent.mkdir(parents=True, exist_ok=True)
    entries = python_path if python_path is not None else [guard_dir, tmp_path]
    environment = os.environ.copy()
    environment.update({
        "PYTHONPATH": os.pathsep.join(os.fspath(entry) for entry in entries),
        "ORB_M53_GUARD_LOG": os.fspath(evidence),
        "ORB_M53_GUARD_TOKEN": core.REQUIRED_TOKEN if token is None else token,
        "ORB_M53_GUARD_EXPECTED_SHA256": _sha256(guard_dir / "guard_core.py") if expected_sha is None else expected_sha,
        "ORB_M53_GUARD_EXPECTED_SITECUSTOMIZE_SHA256": _sha256(guard_dir / "sitecustomize.py"),
        "ORB_M53_GUARD_CHECKOUT": os.fspath(checkout),
        "ORB_M53_GUARD_GIT_EXECUTABLE": os.fspath(_git()),
        "ORB_M53_GUARD_GIT_SHA256": _sha256(_git()),
        "ORB_M53_GUARD_PYTHON_SHA256": _sha256(pathlib.Path(sys.executable)),
    })
    if extra_env:
        environment.update(extra_env)
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, env=environment, cwd=tmp_path, timeout=60)
    return result, evidence


def _events(path: pathlib.Path) -> list[dict[str, object]]:
    return _core().verify_evidence_log(path)


# --------------------------------------------------------------------------
# Startup, activation semantics, and readiness evidence
# --------------------------------------------------------------------------

def test_activates_before_benign_target_module_and_reports_ready(tmp_path: pathlib.Path) -> None:
    (tmp_path / "benign_target.py").write_text("import pathlib\npathlib.Path('target-marker').write_text('imported')\n", encoding="utf-8")
    result, evidence = _run_guarded(tmp_path, "import benign_target")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "target-marker").read_text(encoding="utf-8") == "imported"
    events = [event["event"] for event in _events(evidence)]
    assert events[0] == "activation"
    assert events[1] == "guard_ready"
    assert events[-1] == "normal_interpreter_exit"
    assert not any(str(event).startswith("denied_") for event in events)
    # A completed clean run is exactly what the launcher-grade verifier accepts.
    assert _core().verify_guard_run(evidence)


def test_activation_record_alone_cannot_be_mistaken_for_ready_state(tmp_path: pathlib.Path) -> None:
    _, evidence = _run_guarded(tmp_path, "print('ok')")
    records = _events(evidence)
    activation = next(record for record in records if record["event"] == "activation")
    ready = next(record for record in records if record["event"] == "guard_ready")
    assert activation["socket_rules_loaded"] is False
    assert activation["subprocess_rules_loaded"] is False
    assert ready["socket_rules_loaded"] is True
    assert ready["subprocess_rules_loaded"] is True
    assert ready["audit_hook_installed"] is True


@pytest.mark.parametrize("kwargs", [
    {"log_path": pathlib.Path("relative.jsonl")},
    {"log_path": pathlib.Path(os.path.abspath(os.sep + "definitely-absent-parent") + os.sep + "m53-network-guard.jsonl")},
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


def test_bypass_variable_fails_closed(tmp_path: pathlib.Path) -> None:
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", extra_env={"ORB_M53_GUARD_DISABLE": "1"})
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


# --------------------------------------------------------------------------
# A. Bootstrap and import integrity
# --------------------------------------------------------------------------

def _shadow_guard_dir(tmp_path: pathlib.Path, *, guard_core_suffix: str = "", sitecustomize_body: str | None = None) -> pathlib.Path:
    shadow = tmp_path / "shadow-guard"
    shadow.mkdir(parents=True, exist_ok=True)
    (shadow / "guard_core.py").write_text((GUARD_DIR / "guard_core.py").read_text(encoding="utf-8") + guard_core_suffix, encoding="utf-8")
    body = sitecustomize_body if sitecustomize_body is not None else (GUARD_DIR / "sitecustomize.py").read_text(encoding="utf-8")
    (shadow / "sitecustomize.py").write_text(body, encoding="utf-8")
    return shadow


def test_sitecustomize_is_itself_covered_by_the_frozen_integrity_check(tmp_path: pathlib.Path) -> None:
    """The shim is hashed and pinned, not merely trusted because it ran."""
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", extra_env={"ORB_M53_GUARD_EXPECTED_SITECUSTOMIZE_SHA256": "0" * 64})
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout
    core = _core()
    assert core.sitecustomize_sha256() == _sha256(GUARD_DIR / "sitecustomize.py")
    assert core.guard_sha256() == _sha256(GUARD_DIR / "guard_core.py")


def test_every_evidence_record_carries_both_guard_file_digests(tmp_path: pathlib.Path) -> None:
    _, evidence = _run_guarded(tmp_path, "print('ok')")
    for record in _events(evidence):
        assert record["guard_sha256"] == _sha256(GUARD_DIR / "guard_core.py")
        assert record["sitecustomize_sha256"] == _sha256(GUARD_DIR / "sitecustomize.py")


def test_guard_core_shadowed_by_an_earlier_pythonpath_entry_fails_closed(tmp_path: pathlib.Path) -> None:
    """A ``guard_core``/``sitecustomize`` pair ahead of the intended directory is refused."""
    shadow = _shadow_guard_dir(tmp_path, guard_core_suffix="\n# shadowed copy\n")
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", python_path=[shadow, GUARD_DIR, tmp_path])
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


def test_checkout_or_cwd_cannot_shadow_the_startup_guard(tmp_path: pathlib.Path) -> None:
    """A hostile ``sitecustomize``/``guard_core`` inside the checkout never activates."""
    checkout = _repository(tmp_path)
    (checkout / "sitecustomize.py").write_text("import pathlib\npathlib.Path('shadow-ran').write_text('yes')\n", encoding="utf-8")
    (checkout / "guard_core.py").write_text("def activate():\n    pass\n", encoding="utf-8")
    (tmp_path / "sitecustomize.py").write_text("import pathlib\npathlib.Path('cwd-shadow-ran').write_text('yes')\n", encoding="utf-8")
    (tmp_path / "guard_core.py").write_text("def activate():\n    pass\n", encoding="utf-8")
    result, evidence = _run_guarded(tmp_path, "print('ok')", checkout=checkout)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "shadow-ran").exists()
    assert not (tmp_path / "cwd-shadow-ran").exists()
    assert _core().verify_guard_run(evidence)


def test_guard_directory_inside_the_checkout_fails_closed(tmp_path: pathlib.Path) -> None:
    checkout = _repository(tmp_path)
    inside = checkout / "embedded-guard"
    inside.mkdir()
    shutil.copy2(GUARD_DIR / "guard_core.py", inside / "guard_core.py")
    shutil.copy2(GUARD_DIR / "sitecustomize.py", inside / "sitecustomize.py")
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", checkout=checkout, guard_dir=inside, python_path=[inside, tmp_path])
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


def test_guard_directory_must_be_first_on_pythonpath(tmp_path: pathlib.Path) -> None:
    other = tmp_path / "unrelated"
    other.mkdir()
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", python_path=[other, GUARD_DIR, tmp_path])
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


def test_activate_refuses_when_not_started_from_the_sibling_shim() -> None:
    """Importing ``guard_core`` directly cannot self-declare a guarded process."""
    core = _core()
    sys.modules.pop("sitecustomize", None)
    with pytest.raises(core.GuardConfigurationError):
        core.activate()


# --------------------------------------------------------------------------
# B. Environment clearing
# --------------------------------------------------------------------------

PHASE_2B2A_CLEARED_NAMES = (
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY",
    "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_BASE_URL", "ALPACA_FEED",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


def test_cleared_set_covers_the_phase_2b2a_credential_and_proxy_list() -> None:
    names = _core()._SENSITIVE_ENV_NAMES
    assert set(PHASE_2B2A_CLEARED_NAMES) <= set(names)
    # Uppercase and lowercase spellings of every proxy variable are present.
    for stem in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy"):
        assert stem in names and stem.upper() in names
    # Conventional Alpaca/APCA endpoint aliases are cleared defensively too.
    assert {"APCA_API_BASE_URL", "APCA_API_DATA_URL"} <= set(names)


def test_sensitive_values_do_not_survive_into_the_target(tmp_path: pathlib.Path) -> None:
    secrets = {name: "sentinel-value-%s" % index for index, name in enumerate(PHASE_2B2A_CLEARED_NAMES)}
    code = (
        "import json, os, pathlib\n"
        "names = %r\n"
        "pathlib.Path('surviving.json').write_text(json.dumps(sorted(n for n in names if n in os.environ)))\n"
        "pathlib.Path('values.json').write_text(json.dumps([v for v in os.environ.values() if v.startswith('sentinel-value-')]))\n"
    ) % (list(PHASE_2B2A_CLEARED_NAMES),)
    result, evidence = _run_guarded(tmp_path, code, extra_env=secrets)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "surviving.json").read_text()) == []
    assert json.loads((tmp_path / "values.json").read_text()) == []
    raw = evidence.read_text(encoding="utf-8")
    for value in secrets.values():
        assert value not in raw


def test_allowed_git_child_receives_only_the_controlled_environment(tmp_path: pathlib.Path) -> None:
    core = _core()
    checkout = _repository(tmp_path)
    environment = core._safe_environment(checkout)
    assert set(environment) <= {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_ATTR_NOSYSTEM", "SystemRoot"}
    assert not any(name in environment for name in PHASE_2B2A_CLEARED_NAMES)
    assert environment["GIT_CONFIG_GLOBAL"] == os.fspath(checkout / ".git" / "info" / "empty-global-config")


def test_reintroducing_a_cleared_or_configuration_variable_is_denied(tmp_path: pathlib.Path) -> None:
    result, evidence = _run_guarded(tmp_path, "import os\nos.environ['HTTPS_PROXY'] = 'http://guard-test.invalid:8080'")
    assert result.returncode != 0
    event = next(event for event in _events(evidence) if event["event"] == "denied_environment")
    assert event["variable_name"].upper() == "HTTPS_PROXY"


# --------------------------------------------------------------------------
# C. Executable provenance
# --------------------------------------------------------------------------

def test_executable_evidence_hashes_file_bytes_not_only_the_path(tmp_path: pathlib.Path) -> None:
    _, evidence = _run_guarded(tmp_path, "print('ok')")
    activation = _events(evidence)[0]
    assert activation["executable_content_sha256"] == _sha256(pathlib.Path(sys.executable))
    assert activation["git_executable_content_sha256"] == _sha256(_git())
    # The path-derived digest is retained but is now named for what it is.
    assert activation["executable_path_sha256"] == hashlib.sha256(sys.executable.encode("utf-8")).hexdigest()
    assert activation["executable_content_sha256"] != activation["executable_path_sha256"]


@pytest.mark.parametrize("variable", ["ORB_M53_GUARD_GIT_SHA256", "ORB_M53_GUARD_PYTHON_SHA256"])
def test_mismatched_executable_content_pin_fails_closed(tmp_path: pathlib.Path, variable: str) -> None:
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", extra_env={variable: "0" * 64})
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


@pytest.mark.parametrize("variable", ["ORB_M53_GUARD_GIT_SHA256", "ORB_M53_GUARD_PYTHON_SHA256"])
def test_absent_executable_content_pin_fails_closed(tmp_path: pathlib.Path, variable: str) -> None:
    result, _ = _run_guarded(tmp_path, "print('must-not-run')", extra_env={variable: ""})
    assert result.returncode != 0
    assert "must-not-run" not in result.stdout


def test_allowed_git_child_records_the_pinned_executable_content_hash(tmp_path: pathlib.Path) -> None:
    repo = _repository(tmp_path)
    code = "import subprocess\nsubprocess.run(['git','rev-parse','HEAD'], cwd=r'%s', check=True)" % repo
    result, evidence = _run_guarded(tmp_path, code, checkout=repo)
    assert result.returncode == 0, result.stderr
    allowed = next(event for event in _events(evidence) if event["event"] == "allowed_subprocess")
    assert allowed["executable_content_sha256"] == _sha256(_git())


# --------------------------------------------------------------------------
# D. Network denial
# --------------------------------------------------------------------------

def test_creating_an_internet_socket_is_denied_before_any_os_call(tmp_path: pathlib.Path) -> None:
    result, evidence = _run_guarded(tmp_path, "import socket\nsocket.socket(socket.AF_INET, socket.SOCK_STREAM)")
    assert result.returncode != 0
    event = next(event for event in _events(evidence) if event["event"] == "denied_network")
    assert event["operation"] == "socket.__new__"


@pytest.mark.parametrize("operation, invocation", [
    ("socket.connect", "socket.socket.connect(_Stub(), ('guard-test.invalid', 443))"),
    ("socket.connect_ex", "socket.socket.connect_ex(_Stub(), ('guard-test.invalid', 443))"),
    ("socket.send", "socket.socket.send(_Stub(), b'x')"),
    ("socket.sendall", "socket.socket.sendall(_Stub(), b'x')"),
    ("socket.sendto", "socket.socket.sendto(_Stub(), b'x', ('guard-test.invalid', 9))"),
    ("socket.bind", "socket.socket.bind(_Stub(), ('0.0.0.0', 0))"),
    ("socket.getaddrinfo", "socket.getaddrinfo('guard-test.invalid', 443)"),
    ("socket.gethostbyname", "socket.gethostbyname('guard-test.invalid')"),
    ("socket.gethostbyname_ex", "socket.gethostbyname_ex('guard-test.invalid')"),
    ("socket.getnameinfo", "socket.getnameinfo(('guard-test.invalid', 0), 0)"),
    ("socket.create_connection", "socket.create_connection(('guard-test.invalid', 443))"),
    ("socket.socketpair", "socket.socketpair()"),
])
def test_socket_operations_are_denied_with_loopback_free_stubs(tmp_path: pathlib.Path, operation: str, invocation: str) -> None:
    """No real socket object exists: the patched callables are exercised directly."""
    code = "import socket\nclass _Stub:\n    pass\n%s\n" % invocation
    result, evidence = _run_guarded(tmp_path, code)
    assert result.returncode != 0
    denied = [event for event in _events(evidence) if event["event"] == "denied_network"]
    assert any(event["operation"] == operation for event in denied), denied


def test_http_library_reaches_denied_socket_layer_without_external_traffic(tmp_path: pathlib.Path) -> None:
    result, evidence = _run_guarded(tmp_path, "import urllib.request\nurllib.request.urlopen('http://guard-test.invalid/')")
    assert result.returncode != 0
    assert any(event["event"] == "denied_network" for event in _events(evidence))


def test_inherited_socket_reconstruction_from_a_descriptor_is_denied(tmp_path: pathlib.Path) -> None:
    """An fd inherited from the launcher cannot be rewrapped into a live socket.

    Wrapping an inherited descriptor still runs through ``socket.__new__``, so the
    audit layer denies it; the guarded child additionally proves no socket object
    is obtained.  The audit path is exercised directly to stay loopback-free.
    """
    import socket

    core = _core()
    core._ALLOWED_SOCKET_FAMILIES.clear()
    core._logger = _RecordingLog()
    try:
        with pytest.raises(core.GuardViolation):
            core._audit("socket.__new__", (None, socket.AF_INET, socket.SOCK_STREAM, 0))
    finally:
        core._logger = None
    result, _ = _run_guarded(tmp_path, "import socket\ns = socket.socket(fileno=0)\nprint('rewrapped')")
    assert result.returncode != 0
    assert "rewrapped" not in result.stdout


def test_unknown_future_socket_audit_events_fail_closed() -> None:
    """Deny-by-prefix means a socket event added by a later CPython is denied."""
    core = _core()
    core._logger = _RecordingLog()
    try:
        with pytest.raises(core.GuardViolation):
            core._audit("socket.some_future_event", (None,))
    finally:
        core._logger = None


class _RecordingLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object]]] = []

    def write(self, event: str, **fields: object) -> None:
        self.records.append((event, fields))


# --------------------------------------------------------------------------
# E. Descendant-process denial
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "import subprocess\nsubprocess.run(['unapproved-descendant'])",
    "import subprocess\nsubprocess.run('echo denied', shell=True)",
    "import subprocess,sys\nsubprocess.run([sys.executable, '-c', 'print(1)'])",
    "import subprocess\nsubprocess.run(['powershell', '-NoProfile'])",
    "import subprocess\nsubprocess.run(['cmd', '/c', 'echo denied'])",
    "import subprocess\nsubprocess.run(['curl', 'guard-test.invalid'])",
    "import subprocess\nsubprocess.Popen(args=['unapproved-descendant'])",
    "import os\nos.system('echo denied')",
    "import multiprocessing\nmultiprocessing.Process(target=print).start()",
])
def test_unapproved_descendants_are_denied_without_launching(tmp_path: pathlib.Path, code: str) -> None:
    result, evidence = _run_guarded(tmp_path, code)
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


@pytest.mark.parametrize("code", [
    "import os,sys\nos.execv(sys.executable, [sys.executable, '-c', 'print(1)'])",
    "import os,sys\nos.execl(sys.executable, sys.executable, '-c', 'print(1)')",
    "import os,sys\nos.execve(sys.executable, [sys.executable, '-c', 'print(1)'], {})",
    "import os,sys\nos.spawnv(os.P_NOWAIT, sys.executable, [sys.executable, '-c', 'print(1)'])",
])
def test_exec_and_spawn_family_process_replacement_is_denied(tmp_path: pathlib.Path, code: str) -> None:
    """``os.exec*`` replaces the process image and never reaches ``subprocess.Popen``."""
    result, evidence = _run_guarded(tmp_path, code)
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


@pytest.mark.skipif(not hasattr(os, "startfile"), reason="os.startfile is Windows-only")
def test_os_startfile_is_denied(tmp_path: pathlib.Path) -> None:
    (tmp_path / "probe.txt").write_text("x", encoding="utf-8")
    result, evidence = _run_guarded(tmp_path, "import os\nos.startfile('probe.txt')")
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork is POSIX-only")
def test_os_fork_is_denied(tmp_path: pathlib.Path) -> None:
    result, evidence = _run_guarded(tmp_path, "import os\nos.fork()")
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


def test_exact_local_git_provenance_commands_are_allowed(tmp_path: pathlib.Path) -> None:
    repo = _repository(tmp_path)
    code = "import subprocess\nsubprocess.run(['git','rev-parse','HEAD'], cwd=r'%s', check=True)\nsubprocess.run(['git','status','--porcelain','--','src/cross_sectional_momentum_research.py','src/momentum_risk_control_research.py','scripts/research_momentum_risk_control.py'], cwd=r'%s', check=True)" % (repo, repo)
    result, evidence = _run_guarded(tmp_path, code, checkout=repo)
    assert result.returncode == 0, result.stderr
    allowed = [event for event in _events(evidence) if event["event"] == "allowed_subprocess"]
    assert [event["command_arguments"] for event in allowed] == [["git", "rev-parse", "HEAD"], ["git", "status", "--porcelain", "--", "src/cross_sectional_momentum_research.py", "src/momentum_risk_control_research.py", "scripts/research_momentum_risk_control.py"]]
    assert len(allowed) == 2


@pytest.mark.parametrize("command", [
    "['git','status']",
    "['git','rev-parse','HEAD','--verify']",
    "['git','rev-parse','head']",
    "['git','config','--global','alias.x=!curl guard-test.invalid']",
    "['git','-c','core.hooksPath=x','status','--porcelain','--','src/cross_sectional_momentum_research.py','src/momentum_risk_control_research.py','scripts/research_momentum_risk_control.py']",
    "['git','fetch','origin']",
    "['git','ls-remote','https://guard-test.invalid/repo.git']",
    "['git','rev-parse','HEAD','']",
    "'git rev-parse HEAD'",
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


def test_unspecified_working_directory_is_denied(tmp_path: pathlib.Path) -> None:
    repo = _repository(tmp_path)
    result, evidence = _run_guarded(tmp_path, "import subprocess\nsubprocess.run(['git','rev-parse','HEAD'])", checkout=repo)
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


def test_substituted_executable_on_an_otherwise_exact_vector_is_denied(tmp_path: pathlib.Path) -> None:
    repo = _repository(tmp_path)
    code = "import subprocess,sys\nsubprocess.run(['git','rev-parse','HEAD'], cwd=r'%s', executable=sys.executable)" % repo
    result, evidence = _run_guarded(tmp_path, code, checkout=repo)
    assert result.returncode != 0
    assert any(event["event"] == "denied_subprocess" for event in _events(evidence))


@pytest.mark.parametrize("option", ["pass_fds=()", "preexec_fn=None", "env={'PATH': '.'}", "startupinfo=None"])
def test_dangerous_child_process_options_are_denied_on_an_approved_vector(tmp_path: pathlib.Path, option: str) -> None:
    """An exact Git vector must not smuggle code, descriptors, or an environment in."""
    repo = _repository(tmp_path)
    code = "import subprocess\nsubprocess.run(['git','rev-parse','HEAD'], cwd=r'%s', %s)" % (repo, option)
    result, evidence = _run_guarded(tmp_path, code, checkout=repo)
    if option.startswith("env="):
        # ``env`` is accepted but unconditionally replaced by the controlled set.
        assert result.returncode == 0, result.stderr
        return
    assert result.returncode != 0
    event = next(event for event in _events(evidence) if event["event"] == "denied_subprocess")
    assert event["reason"] == "unsupported_child_process_option"


def test_original_popen_reference_still_trips_the_audit_layer(tmp_path: pathlib.Path) -> None:
    """Even a saved pre-patch ``Popen`` is caught by the audit hook."""
    code = "import guard_core\nguard_core._original_popen(['unapproved-descendant'])"
    result, evidence = _run_guarded(tmp_path, code)
    assert result.returncode != 0
    event = next(event for event in _events(evidence) if event["event"] == "denied_subprocess")
    assert event["reason"] == "audit_unwrapped_process_creation"


# --------------------------------------------------------------------------
# F. Evidence semantics
# --------------------------------------------------------------------------

def test_evidence_redacts_secret_values_and_detects_tampering(tmp_path: pathlib.Path) -> None:
    secret = "never-log-this-secret"
    raw_cache_content = "never-log-this-raw-cache-content"
    username = os.environ.get("USERNAME") or os.environ.get("USER") or "never-log-this-username"
    result, evidence = _run_guarded(tmp_path, "import subprocess\nsubprocess.run(['curl', 'token=%s', 'cache=%s'])" % (secret, raw_cache_content), extra_env={"APCA_API_SECRET_KEY": secret})
    assert result.returncode != 0
    raw = evidence.read_text(encoding="utf-8")
    assert secret not in raw
    assert raw_cache_content not in raw
    assert username not in raw
    records = _events(evidence)
    # Names only, never values; the ambient environment may contribute more names.
    cleared = records[0]["environment_variable_names"]
    assert "APCA_API_SECRET_KEY" in cleared
    assert set(cleared) <= set(_core()._SENSITIVE_ENV_NAMES)
    evidence.write_text(raw.replace("denied_subprocess", "tampered_subprocess", 1), encoding="utf-8")
    with pytest.raises(Exception, match="hash chain"):
        _events(evidence)


def test_verifier_rejects_truncated_evidence(tmp_path: pathlib.Path) -> None:
    core = _core()
    _, evidence = _run_guarded(tmp_path, "print('ok')")
    raw = evidence.read_text(encoding="utf-8")
    evidence.write_text(raw[: len(raw) - 20], encoding="utf-8")
    with pytest.raises(core.GuardConfigurationError):
        core.verify_evidence_log(evidence)


def test_verifier_rejects_a_run_missing_its_terminal_record(tmp_path: pathlib.Path) -> None:
    core = _core()
    _, evidence = _run_guarded(tmp_path, "print('ok')")
    lines = evidence.read_text(encoding="utf-8").splitlines(keepends=True)
    evidence.write_text("".join(lines[:-1]), encoding="utf-8")
    with pytest.raises(core.GuardConfigurationError, match="terminal"):
        core.verify_guard_run(evidence)


def test_verifier_rejects_a_run_containing_any_denied_event(tmp_path: pathlib.Path) -> None:
    core = _core()
    _, evidence = _run_guarded(tmp_path, "import socket\nsocket.getaddrinfo('guard-test.invalid', 443)")
    with pytest.raises(core.GuardConfigurationError, match="denied"):
        core.verify_guard_run(evidence)


def test_verifier_rejects_absent_evidence(tmp_path: pathlib.Path) -> None:
    core = _core()
    with pytest.raises(core.GuardConfigurationError, match="absent"):
        core.verify_guard_run(tmp_path / "never-created" / "m53-network-guard.jsonl")


def test_verifier_rejects_a_run_without_a_ready_record(tmp_path: pathlib.Path) -> None:
    """Chain-valid evidence that stops at activation is not a guarded run."""
    core = _core()
    _, evidence = _run_guarded(tmp_path, "print('ok')")
    records = core.verify_evidence_log(evidence)
    kept = [record for record in records if record["event"] != "guard_ready"]
    rebuilt = []
    previous = "0" * 64
    for record in kept:
        record = {key: value for key, value in record.items() if key != "event_hash"}
        record["previous_hash"] = previous
        digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        record["event_hash"] = digest
        previous = digest
        rebuilt.append(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    evidence.write_text("\n".join(rebuilt) + "\n", encoding="utf-8")
    # A rewritten chain still verifies structurally: the chain proves internal
    # consistency, never authenticity against an actor who can rewrite the log.
    assert core.verify_evidence_log(evidence)
    with pytest.raises(core.GuardConfigurationError, match="ready"):
        core.verify_guard_run(evidence)


def test_guard_source_has_only_stdlib_imports_and_repeated_runs_are_structurally_deterministic(tmp_path: pathlib.Path) -> None:
    source = (GUARD_DIR / "guard_core.py").read_text(encoding="utf-8")
    assert "src." not in source and "scripts.research" not in source
    first, first_log = _run_guarded(tmp_path / "first", "print('ok')")
    second, second_log = _run_guarded(tmp_path / "second", "print('ok')")
    assert first.returncode == second.returncode == 0
    volatile = {"timestamp_utc", "pid", "ppid", "previous_hash", "event_hash", "working_directory", "working_directory_sha256", "guard_directory_sha256"}

    def normalized(path: pathlib.Path) -> list[dict[str, object]]:
        return [{k: v for k, v in record.items() if k not in volatile} for record in _events(path)]
    assert normalized(first_log) == normalized(second_log)
