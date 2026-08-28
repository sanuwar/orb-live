"""Isolated, fail-closed startup guard for a future cache-only M53 process.

This module is deliberately stdlib-only and must be exposed from an isolated
``PYTHONPATH``.  It does not import any ORB-LIVE module.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import Any


GUARD_VERSION = "m53-network-denial-guard-v1"
REQUIRED_TOKEN = "M53_NETWORK_DENIAL_GUARD_V1"
ENV_LOG = "ORB_M53_GUARD_LOG"
ENV_TOKEN = "ORB_M53_GUARD_TOKEN"
ENV_SHA256 = "ORB_M53_GUARD_EXPECTED_SHA256"
ENV_CHECKOUT = "ORB_M53_GUARD_CHECKOUT"
ENV_GIT = "ORB_M53_GUARD_GIT_EXECUTABLE"
ENV_DISABLE = "ORB_M53_GUARD_DISABLE"
_CONFIG_NAMES = frozenset((ENV_LOG, ENV_TOKEN, ENV_SHA256, ENV_CHECKOUT, ENV_GIT, ENV_DISABLE))
_SENSITIVE_ENV_NAMES = frozenset(("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "RH_USERNAME", "RH_PASSWORD", "ROBINHOOD_TOKEN"))
_STATUS_VECTOR = ("git", "status", "--porcelain", "--", "src/cross_sectional_momentum_research.py", "src/momentum_risk_control_research.py", "scripts/research_momentum_risk_control.py")
_REV_PARSE_VECTOR = ("git", "rev-parse", "HEAD")


class GuardViolation(RuntimeError):
    """Raised before a prohibited network or descendant operation reaches the OS."""


class GuardConfigurationError(GuardViolation):
    """Raised when trustworthy guard activation cannot be established."""


def guard_sha256() -> str:
    return hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


class EvidenceLog:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self._previous = "0" * 64
        self._lock = threading.RLock()
        try:
            # A fresh, non-symlink evidence file avoids appending to an unknown record.
            fd = os.open(os.fspath(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o600)
            self._file = os.fdopen(fd, "a", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise GuardConfigurationError("evidence log cannot be created exclusively") from exc

    def write(self, event: str, **fields: Any) -> None:
        with self._lock:
            record: dict[str, Any] = {
                "event": event,
                "guard_sha256": guard_sha256(),
                "guard_version": GUARD_VERSION,
                "pid": os.getpid(),
                "ppid": os.getppid(),
                "previous_hash": self._previous,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                **fields,
            }
            digest = hashlib.sha256(_canonical(record)).hexdigest()
            record["event_hash"] = digest
            try:
                self._file.write(_canonical(record).decode("ascii") + "\n")
                self._file.flush()
                os.fsync(self._file.fileno())
            except OSError as exc:
                raise GuardConfigurationError("evidence log cannot be durably written") from exc
            self._previous = digest

    def close(self) -> None:
        self._file.close()


def verify_evidence_log(path: pathlib.Path) -> list[dict[str, Any]]:
    """Verify JSONL hash-chain canonicalization; return validated records."""
    previous = "0" * 64
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        actual = record.pop("event_hash", None)
        if record.get("previous_hash") != previous or actual != hashlib.sha256(_canonical(record)).hexdigest():
            raise GuardConfigurationError("evidence hash chain verification failed")
        previous = actual
        record["event_hash"] = actual
        records.append(record)
    if not records:
        raise GuardConfigurationError("evidence log is empty")
    return records


_logger: EvidenceLog | None = None
_original_popen = subprocess.Popen
_original_socket_methods: dict[str, Any] = {}
_subprocess_permit = threading.local()


def _require_absolute_directory(value: str | None, name: str) -> pathlib.Path:
    if not value:
        raise GuardConfigurationError(f"{name} is required")
    path = pathlib.Path(value)
    if not path.is_absolute() or not path.exists() or not path.is_dir() or path.is_symlink():
        raise GuardConfigurationError(f"{name} must be an existing non-symlink absolute directory")
    return path.resolve()


def _configuration() -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    if os.environ.get(ENV_DISABLE) is not None:
        raise GuardConfigurationError("guard bypass variable is present")
    if os.environ.get(ENV_TOKEN) != REQUIRED_TOKEN:
        raise GuardConfigurationError("guard token mismatch")
    if os.environ.get(ENV_SHA256) != guard_sha256():
        raise GuardConfigurationError("guard sha256 mismatch")
    log_value = os.environ.get(ENV_LOG)
    if not log_value:
        raise GuardConfigurationError("evidence log path is required")
    log_path = pathlib.Path(log_value)
    if not log_path.is_absolute() or log_path.name != "m53-network-guard.jsonl" or log_path.exists() or log_path.is_symlink():
        raise GuardConfigurationError("evidence log must be a fresh absolute m53-network-guard.jsonl path")
    parent = _require_absolute_directory(os.fspath(log_path.parent), "evidence log parent")
    checkout = _require_absolute_directory(os.environ.get(ENV_CHECKOUT), ENV_CHECKOUT)
    git = pathlib.Path(os.environ.get(ENV_GIT, ""))
    if not git.is_absolute() or not git.exists() or not git.is_file() or git.is_symlink():
        raise GuardConfigurationError("pinned Git executable is required")
    return log_path, checkout, git.resolve()


def _safe_environment(checkout: pathlib.Path) -> dict[str, str]:
    config = checkout / ".git" / "info" / "empty-global-config"
    if not config.is_file() or config.is_symlink():
        raise GuardViolation("isolated checkout has no controlled Git global configuration")
    result = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.fspath(config), "GIT_ATTR_NOSYSTEM": "1"}
    # Windows Git needs SystemRoot to load system libraries. It is not sensitive.
    if os.name == "nt" and os.environ.get("SystemRoot"):
        result["SystemRoot"] = os.environ["SystemRoot"]
    return result


def _redacted_cwd(cwd: Any) -> dict[str, Any]:
    if cwd is None:
        return {"working_directory": "UNSPECIFIED"}
    rendered = os.fspath(cwd)
    try:
        scope = "CHECKOUT" if pathlib.Path(rendered).resolve() == _STATE.get("checkout") else "OUTSIDE_CHECKOUT"
    except OSError:
        scope = "UNRESOLVED"
    return {"working_directory": scope, "working_directory_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest()}

def _redacted_command(args: Any) -> dict[str, Any]:
    if isinstance(args, (list, tuple)):
        rendered = [str(x) for x in args]
        if tuple(rendered) in (_REV_PARSE_VECTOR, _STATUS_VECTOR):
            return {"command_arguments": rendered}
        return {"command_argument_count": len(rendered), "command_sha256": hashlib.sha256(_canonical({"args": rendered})).hexdigest()}
    return {"command_argument_count": 1, "command_sha256": hashlib.sha256(str(args).encode("utf-8")).hexdigest()}


def _deny(kind: str, **fields: Any) -> None:
    assert _logger is not None
    _logger.write("denied_" + kind, **fields)
    raise GuardViolation("M53 guard denied " + kind)


def _allowed_git(args: Any, cwd: Any, shell: bool, executable: Any, checkout: pathlib.Path, git: pathlib.Path) -> bool:
    if shell or executable not in (None, os.fspath(git)):
        return False
    if not isinstance(args, (list, tuple)):
        return False
    vector = tuple(map(str, args))
    if vector and vector[0] == os.fspath(git):
        vector = ("git",) + vector[1:]
    if vector not in (_REV_PARSE_VECTOR, _STATUS_VECTOR):
        return False
    try:
        return pathlib.Path(cwd).resolve() == checkout
    except (TypeError, OSError):
        return False


def _guarded_popen(*args: Any, **kwargs: Any) -> Any:
    assert _logger is not None
    command = args[0] if args else kwargs.get("args")
    cwd = kwargs.get("cwd")
    shell = bool(kwargs.get("shell", False))
    checkout = _STATE["checkout"]
    git = _STATE["git"]
    if not _allowed_git(command, cwd, shell, kwargs.get("executable"), checkout, git):
        _deny("subprocess", shell=shell, **_redacted_cwd(cwd), **_redacted_command(command))
    try:
        kwargs["executable"] = os.fspath(git)
        kwargs["env"] = _safe_environment(checkout)
    except GuardViolation:
        _deny("subprocess", reason="controlled_child_environment_unavailable", **_redacted_command(command))
    _logger.write("allowed_subprocess", working_directory="CHECKOUT", executable=git.name, executable_sha256=hashlib.sha256(os.fspath(git).encode("utf-8")).hexdigest(), **_redacted_command(command))
    _subprocess_permit.active = True
    try:
        return _original_popen(*args, **kwargs)
    finally:
        _subprocess_permit.active = False


def _install_network_denial() -> None:
    import socket

    def blocked(name: str):
        def inner(*args: Any, **kwargs: Any) -> Any:
            _deny("network", operation=name)
        return inner
    for name in ("connect", "connect_ex", "sendto", "send"):
        _original_socket_methods[name] = getattr(socket.socket, name)
        setattr(socket.socket, name, blocked("socket." + name))
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "getnameinfo"):
        _original_socket_methods[name] = getattr(socket, name)
        setattr(socket, name, blocked("socket." + name))
    _original_socket_methods["create_connection"] = socket.create_connection
    socket.create_connection = blocked("socket.create_connection")


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "subprocess.Popen":
        executable, command, cwd, _env = args
        if not getattr(_subprocess_permit, "active", False):
            _deny("subprocess", reason="audit_unwrapped_process_creation", **_redacted_cwd(cwd), **_redacted_command(command))
    elif event in {"os.system", "os.posix_spawn", "os.spawn"}:
        _deny("subprocess", reason="os_process_api", audit_event=event)
    elif event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex", "socket.getnameinfo", "socket.sendto", "socket.send"}:
        _deny("network", operation=event)
    elif event in {"os.putenv", "os.unsetenv"}:
        name = str(args[0], "utf-8", "ignore") if isinstance(args[0], bytes) else str(args[0])
        if name in _CONFIG_NAMES or name in _SENSITIVE_ENV_NAMES:
            _deny("environment", variable_name=name)


_STATE: dict[str, pathlib.Path] = {}


def activate() -> None:
    """Validate configuration, establish durable evidence, then install hooks."""
    global _logger
    log_path, checkout, git = _configuration()
    _logger = EvidenceLog(log_path)
    try:
        _STATE.update(checkout=checkout, git=git)
        # Clear proxy/credential variables after recording names; audit protects later replacement.
        present = sorted(name for name in _SENSITIVE_ENV_NAMES if name in os.environ)
        for name in present:
            del os.environ[name]
        _logger.write("activation", executable=pathlib.Path(sys.executable).name, executable_sha256=hashlib.sha256(sys.executable.encode("utf-8")).hexdigest(), **_redacted_cwd(os.getcwd()), environment_variable_names=present, socket_rules_loaded=True, subprocess_rules_loaded=True)
        sys.addaudithook(_audit)
        subprocess.Popen = _guarded_popen
        _install_network_denial()
        import multiprocessing.process
        def blocked_start(self: Any) -> None:
            _deny("subprocess", reason="multiprocessing")
        multiprocessing.process.BaseProcess.start = blocked_start
        atexit.register(lambda: _logger and _logger.write("normal_interpreter_exit"))
    except BaseException as exc:
        try:
            _logger.write("guard_failure", failure_type=type(exc).__name__)
        except BaseException:
            pass
        raise
