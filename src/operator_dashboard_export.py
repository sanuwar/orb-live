"""Deterministic standalone HTML export for the read-only operator console."""

from __future__ import annotations

import hashlib
import html
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from src.offline_path_safety import is_repository_data_path
from src.supervised_operator_console import (
    ConsoleError,
    ConsoleIdentityError,
    ConsoleSafetyError,
    OperatorAction,
    SupervisedOperatorConsoleResult,
    load_console_snapshot,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
EXPORT_SCHEMA_VERSION = 1
INDEX_KEYS = frozenset({
    "schema_version", "generated_as_of", "release_commit_sha", "implementation_digest",
    "output_files", "content_hashes", "export_status", "permits_automated_action",
    "live_ready", "unattended_ready",
})
_CSS = """
:root { color-scheme: light; font-family: system-ui, sans-serif; }
body { background: #f5f7fa; color: #17202a; line-height: 1.45; margin: 0; }
main { margin: 0 auto; max-width: 980px; padding: 2rem; }
header, section { background: #fff; border: 1px solid #cbd5e1; border-radius: .5rem; margin: 0 0 1rem; padding: 1rem 1.25rem; }
h1, h2 { margin-top: 0; } h1 { font-size: 1.65rem; }
.grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.card { border: 1px solid #cbd5e1; border-radius: .35rem; padding: .7rem; }
.label { color: #475569; display: block; font-size: .85rem; } .value { font-weight: 650; }
.status-normal { border-left: .35rem solid #15803d; } .status-warning { border-left: .35rem solid #b45309; }
.status-blocked { border-left: .35rem solid #b91c1c; } .status-unknown { border-left: .35rem solid #475569; }
table { border-collapse: collapse; width: 100%; } th, td { border-bottom: 1px solid #e2e8f0; padding: .45rem; text-align: left; }
.notice { font-weight: 650; }
@media print { body { background: #fff; } main { max-width: none; padding: 0; } header, section { break-inside: avoid; } }
""".strip()


def _safe_output_root(output_root: Path) -> Path:
    raw = Path(output_root)
    if raw.exists() and raw.is_symlink():
        raise ConsoleError("output root is unsafe")
    parent = raw.parent.resolve(strict=True)
    candidate = (parent / raw.name).resolve(strict=False)
    if is_repository_data_path(candidate) or candidate == REPOSITORY_ROOT or REPOSITORY_ROOT in candidate.parents:
        raise ConsoleError("output root is unsafe")
    if any(part.casefold() in {"src", "tests", "test", "examples"} for part in candidate.parts):
        raise ConsoleError("output root is unsafe")
    return candidate


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _class(status: str) -> str:
    upper = status.upper()
    if upper in {"ACTIVE", "SUSPENDED", "STALE", "MISSED", "UNCONFIRMED", "AMBIGUOUS", "REVIEW_REQUIRED", "PARTIAL", "FAILED_CLOSED"}:
        return "status-blocked"
    if upper in {"CURRENT", "CONFIRMED", "CLEAR", "COMPLETE", "AVAILABLE", "IDLE", "BOUNDED_IDLE", "OFFLINE_HEALTHY", "REVIEW_ONLY", "NOT_AVAILABLE"}:
        return "status-normal"
    return "status-warning"


def _card(label: str, value: object) -> str:
    text = _esc(value)
    return f'<div class="card {_class(str(value))}"><span class="label">{_esc(label)}</span><span class="value">{text}</span></div>'


def render_dashboard(result: SupervisedOperatorConsoleResult) -> str:
    data = result.to_dict()
    blockers = "".join(f"<li>{_esc(value)}</li>" for value in result.blocker_reasons) or "<li>None recorded</li>"
    components = "".join(
        f"<tr><th>{_esc(key)}</th><td class=\"{_class(value)}\">{_esc(value)}</td></tr>"
        for key, value in result.source_component_statuses
    )
    safety = "".join(_card(label, "NO" if not data[field] else "YES") for label, field in (
        ("Strategy enabled", "strategy_enabled"), ("Approvals present", "activation_approvals_present"),
        ("Protection grounded", "protection_grounded"), ("Activation approved", "activation_approved"),
        ("Automated action permitted", "permits_automated_action"), ("Live ready", "live_ready"),
        ("Unattended ready", "unattended_ready"),
    ))
    runtime = "".join(_card(label, data[field]) for label, field in (
        ("Scheduler", "scheduler_status"), ("Runner", "runner_status"), ("Suspension", "suspension_status"),
        ("Heartbeat", "heartbeat_status"), ("Alert delivery", "alert_status"),
        ("Acknowledgment", "acknowledgment_status"), ("Recovery", "recovery_status"),
    ))
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ORB-LIVE Supervised Operator Dashboard</title><style>""" + _CSS + """</style></head>
<body><main>
<header><h1>ORB-LIVE Supervised Operator Dashboard</h1>
<p>Generated as of <strong>""" + _esc(data["generated_as_of"]) + """</strong>; release <strong>""" + _esc(data["release_commit_sha"][:12]) + """</strong>; digest <strong>""" + _esc(data["implementation_digest"][:12]) + """</strong>.</p>
<p>Operating mode: <strong>""" + _esc(data["operating_mode"]) + """</strong></p></header>
<section><h2>Overall status</h2><div class="grid">""" + "".join(_card(label, data[field]) for label, field in (
        ("Operational status", "operational_status"), ("Supervised readiness", "supervised_readiness_status"),
        ("Recommended operator action", "recommended_operator_action"), ("Evidence completeness", "evidence_completeness"),
        ("Evidence freshness", "evidence_freshness"),
    )) + """</div></section>
<section><h2>Runtime</h2><div class="grid">""" + runtime + """</div></section>
<section><h2>Safety boundaries</h2><div class="grid">""" + safety + """</div></section>
<section><h2>Blockers and adverse components</h2><ul>""" + blockers + """</ul>
<table><caption>Source component status</caption><tbody>""" + components + """</tbody></table></section>
<section><h2>Operator guidance</h2><p class="notice">""" + _esc(data["recommended_operator_action"]) + """</p>
<p>This dashboard is static and read-only. It does not authorize deployment, broker access, live trading, or unattended operation.</p></section>
</main></body></html>
"""


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + ".partial")
    if temporary.exists() or path.exists():
        raise ConsoleError("completed or partial export already exists")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ConsoleError("export publication failed") from exc


def export_dashboard(*, runtime_root: Path, as_of: datetime, output_root: Path, include_json: bool = False, repository_root: Path = REPOSITORY_ROOT) -> tuple[SupervisedOperatorConsoleResult, dict[str, Any]]:
    result = load_console_snapshot(runtime_root=runtime_root, as_of=as_of, repository_root=repository_root)
    destination = _safe_output_root(output_root)
    destination.mkdir(parents=True, exist_ok=True)
    html_content = render_dashboard(result).encode("utf-8")
    files: dict[str, str] = {"dashboard": "operator-dashboard.html"}
    contents: dict[str, bytes] = {"operator-dashboard.html": html_content}
    if include_json:
        files["snapshot"] = "operator-snapshot.json"
        contents["operator-snapshot.json"] = result.serialize().encode("utf-8")
    for name in (*contents, "dashboard-export-index.json"):
        if (destination / name).exists() or (destination / (name + ".partial")).exists():
            raise ConsoleError("completed or partial export already exists")
    index = {
        "schema_version": EXPORT_SCHEMA_VERSION, "generated_as_of": result.generated_as_of,
        "release_commit_sha": result.release_commit_sha, "implementation_digest": result.implementation_digest,
        "output_files": files, "content_hashes": {name: _sha256(value) for name, value in sorted(contents.items())},
        "export_status": "EXPORTED_STATIC_READ_ONLY_DASHBOARD", "permits_automated_action": False,
        "live_ready": False, "unattended_ready": False,
    }
    index_content = (json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        for name, content in contents.items():
            _write_atomic(destination / name, content)
        _write_atomic(destination / "dashboard-export-index.json", index_content)
    except ConsoleError:
        raise
    return result, index


def exit_code(result: SupervisedOperatorConsoleResult) -> int:
    return {
        OperatorAction.NO_ACTION_OFFLINE_STATE_HEALTHY: 0,
        OperatorAction.COMPLETE_OPERATOR_REVIEW: 10,
        OperatorAction.REVIEW_ACTIVE_SUSPENSION: 20,
        OperatorAction.REVIEW_STALE_HEARTBEAT: 30,
        OperatorAction.CONFIRM_ALERT_DELIVERY: 40,
        OperatorAction.REVIEW_AMBIGUOUS_RECOVERY: 50,
        OperatorAction.VERIFY_DEPLOYMENT_CONFIGURATION: 60,
        OperatorAction.FAIL_CLOSED_INCOMPLETE_EVIDENCE: 60,
    }[result.recommended_operator_action]


__all__ = ["EXPORT_SCHEMA_VERSION", "INDEX_KEYS", "exit_code", "export_dashboard", "render_dashboard"]
