"""Isolated, fail-closed startup guard for a future cache-only M53 process.

This module is deliberately stdlib-only and must be exposed from an isolated
``PYTHONPATH``.  It does not import any ORB-LIVE module.

Scope statement: this is a *process-scoped CPython guard*.  It denies Python
level network and descendant-process operations before they reach the operating
system.  It is not an operating-system sandbox and does not contain native code
that bypasses CPython's socket, audit, and subprocess surfaces.
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


GUARD_VERSION = "m53-network-denial-guard-v2"
REQUIRED_TOKEN = "M53_NETWORK_DENIAL_GUARD_V1"
ENV_LOG = "ORB_M53_GUARD_LOG"
ENV_TOKEN = "ORB_M53_GUARD_TOKEN"
ENV_SHA256 = "ORB_M53_GUARD_EXPECTED_SHA256"
ENV_SITECUSTOMIZE_SHA256 = "ORB_M53_GUARD_EXPECTED_SITECUSTOMIZE_SHA256"
ENV_CHECKOUT = "ORB_M53_GUARD_CHECKOUT"
ENV_GIT = "ORB_M53_GUARD_GIT_EXECUTABLE"
ENV_GIT_SHA256 = "ORB_M53_GUARD_GIT_SHA256"
ENV_PYTHON_SHA256 = "ORB_M53_GUARD_PYTHON_SHA256"
ENV_DISABLE = "ORB_M53_GUARD_DISABLE"
_CONFIG_NAMES = frozenset((
    ENV_LOG, ENV_TOKEN, ENV_SHA256, ENV_SITECUSTOMIZE_SHA256, ENV_CHECKOUT,
    ENV_GIT, ENV_GIT_SHA256, ENV_PYTHON_SHA256, ENV_DISABLE,
))
# Reconciled against the Phase 2B-2A credential/proxy clearing list plus the
# conventional Alpaca/APCA aliases and lowercase proxy spellings.
_SENSITIVE_ENV_NAMES = frozenset((
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY",
    "ALPACA_BASE_URL", "ALPACA_FEED", "ALPACA_DATA_URL", "ALPACA_API_BASE_URL",
    "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "APCA_API_BASE_URL", "APCA_API_DATA_URL",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy",
    "RH_USERNAME", "RH_PASSWORD", "ROBINHOOD_TOKEN",
))
_STATUS_VECTOR = ("git", "status", "--porcelain", "--", "src/cross_sectional_momentum_research.py", "src/momentum_risk_control_research.py", "scripts/research_momentum_risk_control.py")
_REV_PARSE_VECTOR = ("git", "rev-parse", "HEAD")
# Descendant-creation audit events denied outright.  ``os.exec``/``os.startfile``
# replace or launch a process image entirely outside ``subprocess.Popen``.
_PROCESS_AUDIT_EVENTS = frozenset((
    "os.system", "os.posix_spawn", "os.spawn", "os.exec", "os.fork", "os.forkpty",
    "os.startfile", "pty.spawn",
))
_REJECTED_POPEN_KWARGS = frozenset((
    "preexec_fn", "pass_fds", "startupinfo", "creationflags", "group", "user",
    "extra_groups", "umask", "process_group", "restore_signals",
))
# Denied kwargs above plus these are the only keys an approved Git child may use.
_ALLOWED_POPEN_KWARGS = frozenset((
    "args", "cwd", "shell", "executable", "env", "stdin", "stdout", "stderr",
    "text", "universal_newlines", "encoding", "errors", "bufsize", "close_fds",
    "timeout", "check", "capture_output", "input",
))


class GuardViolation(RuntimeError):
    """Raised before a prohibited network or descendant operation reaches the OS."""


class GuardConfigurationError(GuardViolation):
    """Raised when trustworthy guard activation cannot be established."""


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(os.fspath(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_FILE_DIGESTS: dict[str, str] = {}


def _cached_file_sha256(path: pathlib.Path) -> str:
    key = os.fspath(path)
    if key not in _FILE_DIGESTS:
        _FILE_DIGESTS[key] = _file_sha256(path)
    return _FILE_DIGESTS[key]


def guard_sha256() -> str:
    """SHA-256 of this module's own file bytes."""
    return _cached_file_sha256(pathlib.Path(__file__))


def sitecustomize_sha256() -> str:
    """SHA-256 of the sibling ``sitecustomize.py`` bootstrap file bytes."""
    return _cached_file_sha256(pathlib.Path(__file__).resolve().parent / "sitecustomize.py")


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
                "sitecustomize_sha256": sitecustomize_sha256(),
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
    """Verify JSONL hash-chain canonicalization; return validated records.

    The chain proves *internal consistency and self-contained tamper evidence*:
    any edit, reordering, deletion, or truncation of a record invalidates it
    unless the editor recomputes every following hash.  It is not a
    cryptographic authenticity proof: an actor able to rewrite the whole log can
    forge a self-consistent chain, because the guard holds no private key.
    """
    previous = "0" * 64
    records: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise GuardConfigurationError("evidence log is truncated: final record has no terminator")
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise GuardConfigurationError("evidence log contains an unparsable or truncated record") from exc
        if not isinstance(record, dict):
            raise GuardConfigurationError("evidence log record is not an object")
        actual = record.pop("event_hash", None)
        if record.get("previous_hash") != previous or actual != hashlib.sha256(_canonical(record)).hexdigest():
            raise GuardConfigurationError("evidence hash chain verification failed")
        previous = actual
        record["event_hash"] = actual
        records.append(record)
    if not records:
        raise GuardConfigurationError("evidence log is empty")
    return records


def verify_guard_run(path: pathlib.Path) -> list[dict[str, Any]]:
    """Full launcher-grade readiness check for one completed guarded run.

    Fails closed on a missing file, a broken or truncated chain, missing
    ``activation``, a missing affirmative ``guard_ready``, any ``guard_failure``,
    any ``denied_*`` event, or a missing terminal ``normal_interpreter_exit``.
    """
    if not path.is_file():
        raise GuardConfigurationError("evidence log is absent")
    records = verify_evidence_log(path)
    events = [str(record.get("event")) for record in records]
    if events[0] != "activation":
        raise GuardConfigurationError("first evidence record is not activation")
    if "guard_ready" not in events:
        raise GuardConfigurationError("guard never reported ready after installing all controls")
    if any(event == "guard_failure" for event in events):
        raise GuardConfigurationError("guard reported an activation failure")
    denied = [event for event in events if event.startswith("denied_")]
    if denied:
        raise GuardConfigurationError("guarded run recorded denied events: " + ",".join(sorted(set(denied))))
    if events[-1] != "normal_interpreter_exit":
        raise GuardConfigurationError("evidence lacks a terminal normal_interpreter_exit record")
    return records


_logger: EvidenceLog | None = None
_original_popen = subprocess.Popen
_original_socket_methods: dict[str, Any] = {}
_subprocess_permit = threading.local()
_ALLOWED_SOCKET_FAMILIES: set[int] = set()


def _require_absolute_directory(value: str | None, name: str) -> pathlib.Path:
    if not value:
        raise GuardConfigurationError(f"{name} is required")
    path = pathlib.Path(value)
    if not path.is_absolute() or not path.exists() or not path.is_dir() or path.is_symlink():
        raise GuardConfigurationError(f"{name} must be an existing non-symlink absolute directory")
    return path.resolve()


def _require_pinned_executable(value: str | None, expected: str | None, name: str) -> pathlib.Path:
    """Resolve an executable and pin it by the SHA-256 of its file bytes.

    Symlinks are resolved rather than refused: a venv or toolchain interpreter is
    normally a symlink, and refusing one would block a legitimate launch without
    adding protection.  Content pinning, not path shape, is the control here --
    a substituted target changes the digest and fails closed.
    """
    path = pathlib.Path(value or "")
    if not path.is_absolute():
        raise GuardConfigurationError(f"{name} must be an absolute path")
    resolved = path.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise GuardConfigurationError(f"{name} must resolve to an existing regular file")
    if not expected or len(expected) != 64:
        raise GuardConfigurationError(f"{name} requires an explicit expected SHA-256")
    if _cached_file_sha256(resolved) != expected.lower():
        raise GuardConfigurationError(f"{name} content SHA-256 does not match the pinned value")
    return resolved


def _is_within(child: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _verify_bootstrap(checkout: pathlib.Path) -> pathlib.Path:
    """Prove the intended sibling guard pair was the code that actually ran.

    Guards against a ``guard_core`` or ``sitecustomize`` shadowed by the
    historical checkout, the current directory, ``site-packages``, or a
    different ``PYTHONPATH`` entry.  Self-verification cannot be complete on its
    own -- a substituted pair could also substitute its own expected digests --
    so the launcher must additionally perform the trusted external pre-launch
    hash check documented in ``GUARD_DESIGN_AND_ACTIVATION.md``.
    """
    core_file = pathlib.Path(__file__)
    if core_file.is_symlink():
        raise GuardConfigurationError("guard_core.py must not be a symlink")
    guard_dir = core_file.resolve().parent
    shim = guard_dir / "sitecustomize.py"
    if not shim.is_file() or shim.is_symlink():
        raise GuardConfigurationError("sibling sitecustomize.py is missing or a symlink")
    module = sys.modules.get("sitecustomize")
    loaded = getattr(module, "__file__", None)
    if module is None or loaded is None:
        raise GuardConfigurationError("guard was not started from the sitecustomize bootstrap")
    if pathlib.Path(loaded).resolve() != shim.resolve():
        raise GuardConfigurationError("the executing sitecustomize is not the intended sibling shim")
    entries = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
    if not entries or pathlib.Path(entries[0]).resolve() != guard_dir:
        raise GuardConfigurationError("the guard directory must be the first PYTHONPATH entry")
    if _is_within(guard_dir, checkout) or _is_within(checkout, guard_dir):
        raise GuardConfigurationError("the guard directory must be outside the historical checkout")
    if os.environ.get(ENV_SITECUSTOMIZE_SHA256) != sitecustomize_sha256():
        raise GuardConfigurationError("sitecustomize sha256 mismatch")
    return guard_dir


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
    _require_absolute_directory(os.fspath(log_path.parent), "evidence log parent")
    checkout = _require_absolute_directory(os.environ.get(ENV_CHECKOUT), ENV_CHECKOUT)
    git = _require_pinned_executable(os.environ.get(ENV_GIT), os.environ.get(ENV_GIT_SHA256), "pinned Git executable")
    _require_pinned_executable(sys.executable, os.environ.get(ENV_PYTHON_SHA256), "running Python executable")
    return log_path, checkout, git


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
    if _logger is None:
        raise GuardConfigurationError("M53 guard denied " + kind + " before evidence was available")
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
    command = args[0] if args else kwargs.get("args")
    cwd = kwargs.get("cwd")
    shell = bool(kwargs.get("shell", False))
    checkout = _STATE["checkout"]
    git = _STATE["git"]
    if not _allowed_git(command, cwd, shell, kwargs.get("executable"), checkout, git):
        _deny("subprocess", shell=shell, **_redacted_cwd(cwd), **_redacted_command(command))
    rejected = sorted(name for name in kwargs if name in _REJECTED_POPEN_KWARGS or name not in _ALLOWED_POPEN_KWARGS)
    if rejected:
        _deny("subprocess", reason="unsupported_child_process_option", rejected_option_names=rejected, **_redacted_command(command))
    environment: dict[str, str] = {}
    try:
        environment = _safe_environment(checkout)
    except GuardViolation:
        _deny("subprocess", reason="controlled_child_environment_unavailable", **_redacted_command(command))
    kwargs["executable"] = os.fspath(git)
    kwargs["env"] = environment
    kwargs["cwd"] = os.fspath(checkout)
    kwargs["shell"] = False
    kwargs["close_fds"] = True
    assert _logger is not None
    _logger.write(
        "allowed_subprocess",
        working_directory="CHECKOUT",
        executable=git.name,
        executable_path_sha256=hashlib.sha256(os.fspath(git).encode("utf-8")).hexdigest(),
        executable_content_sha256=_cached_file_sha256(git),
        **_redacted_command(command),
    )
    _subprocess_permit.active = True
    try:
        return _original_popen(*args, **kwargs)
    finally:
        _subprocess_permit.active = False


def _install_network_denial() -> None:
    import socket

    unix = getattr(socket, "AF_UNIX", None)
    if unix is not None:
        _ALLOWED_SOCKET_FAMILIES.add(int(unix))

    def blocked(name: str):
        def inner(*args: Any, **kwargs: Any) -> Any:
            _deny("network", operation=name)
        return inner
    # Send-family methods matter because CPython raises no audit event for
    # ``socket.send``/``sendall``; an inherited or duplicated socket object would
    # otherwise remain writable from Python.
    for name in ("connect", "connect_ex", "bind", "send", "sendall", "sendto", "sendmsg", "sendfile", "dup", "makefile"):
        method = getattr(socket.socket, name, None)
        if method is not None:
            _original_socket_methods[name] = method
            setattr(socket.socket, name, blocked("socket." + name))
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo", "create_connection", "create_server", "socketpair"):
        function = getattr(socket, name, None)
        if function is not None:
            _original_socket_methods[name] = function
            setattr(socket, name, blocked("socket." + name))


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "subprocess.Popen":
        executable, command, cwd, _env = args
        if not getattr(_subprocess_permit, "active", False):
            _deny("subprocess", reason="audit_unwrapped_process_creation", **_redacted_cwd(cwd), **_redacted_command(command))
    elif event in _PROCESS_AUDIT_EVENTS or event.startswith("os.startfile"):
        # ``os.fork``/``os.exec`` are also raised by the POSIX machinery of an
        # already-approved Git child, which is permitted for that window only.
        if not getattr(_subprocess_permit, "active", False):
            _deny("subprocess", reason="os_process_api", audit_event=event)
    elif event == "socket.__new__":
        family = args[1] if len(args) > 1 else None
        try:
            allowed = int(family) in _ALLOWED_SOCKET_FAMILIES
        except (TypeError, ValueError):
            allowed = False
        if not allowed:
            _deny("network", operation=event)
    elif event.startswith("socket."):
        # Deny-by-prefix: every remaining CPython socket audit event is denied,
        # so a socket event added by a future CPython release fails closed.
        _deny("network", operation=event)
    elif event in {"urllib.Request", "ftplib.connect", "smtplib.connect", "imaplib.open", "poplib.connect", "nntplib.connect", "telnetlib.Telnet", "webbrowser.open"}:
        _deny("network", operation=event)
    elif event in {"os.putenv", "os.unsetenv"}:
        name = str(args[0], "utf-8", "ignore") if isinstance(args[0], bytes) else str(args[0])
        if name in _CONFIG_NAMES or name in _SENSITIVE_ENV_NAMES:
            _deny("environment", variable_name=name)


_STATE: dict[str, pathlib.Path] = {}


def activate() -> None:
    """Validate configuration and bootstrap, establish durable evidence, then
    install every control and only then record an affirmative ready state."""
    global _logger
    log_path, checkout, git = _configuration()
    guard_dir = _verify_bootstrap(checkout)
    _logger = EvidenceLog(log_path)
    try:
        _STATE.update(checkout=checkout, git=git)
        # Clear proxy/credential variables after recording names; audit protects later replacement.
        # ``pop`` rather than ``del``: Windows environment names are case-folded, so
        # removing ``HTTP_PROXY`` also removes ``http_proxy`` from ``os.environ``.
        present = sorted(name for name in _SENSITIVE_ENV_NAMES if name in os.environ)
        for name in present:
            os.environ.pop(name, None)
        _logger.write(
            "activation",
            executable=pathlib.Path(sys.executable).name,
            executable_path_sha256=hashlib.sha256(sys.executable.encode("utf-8")).hexdigest(),
            executable_content_sha256=_cached_file_sha256(pathlib.Path(sys.executable)),
            git_executable_content_sha256=_cached_file_sha256(git),
            guard_directory_sha256=hashlib.sha256(os.fspath(guard_dir).encode("utf-8")).hexdigest(),
            **_redacted_cwd(os.getcwd()),
            environment_variable_names=present,
            socket_rules_loaded=False,
            subprocess_rules_loaded=False,
        )
        sys.addaudithook(_audit)
        subprocess.Popen = _guarded_popen
        _install_network_denial()
        import multiprocessing.process

        def blocked_start(self: Any) -> None:
            _deny("subprocess", reason="multiprocessing")
        multiprocessing.process.BaseProcess.start = blocked_start
        atexit.register(lambda: _logger and _logger.write("normal_interpreter_exit"))
        # Only now may a launcher treat the process as guarded: activation alone
        # proves configuration was accepted, never that the controls are live.
        _logger.write("guard_ready", audit_hook_installed=True, socket_rules_loaded=True, subprocess_rules_loaded=True, multiprocessing_rules_loaded=True)
    except BaseException as exc:
        try:
            _logger.write("guard_failure", failure_type=type(exc).__name__)
        except BaseException:
            pass
        raise
