from __future__ import annotations

import hashlib
import json
from datetime import timezone
from pathlib import Path

import pytest

from src.operator_dashboard_export import INDEX_KEYS, ConsoleError, export_dashboard, exit_code, render_dashboard
from src.supervised_operator_console import ConsoleSafetyError, OperatorAction, load_console_snapshot
from test_supervised_operator_console import AS_OF, _fixture


def _evidence(root: Path) -> tuple[Path, dict]:
    path = root / "evidence/operator-console-evidence.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return path, raw


def _export(tmp_path: Path, include_json: bool = True, **changes):
    root = _fixture(tmp_path / "runtime")
    path, raw = _evidence(root)
    raw.update(changes)
    path.write_text(json.dumps(raw), encoding="utf-8")
    output = tmp_path / "export"
    result, index = export_dashboard(runtime_root=root, as_of=AS_OF, output_root=output, include_json=include_json)
    return root, output, result, index


def test_healthy_export_is_standalone_and_hashed(tmp_path):
    root, output, result, index = _export(tmp_path)
    assert result.recommended_operator_action is OperatorAction.NO_ACTION_OFFLINE_STATE_HEALTHY
    assert set(index) == INDEX_KEYS
    assert (output / "operator-dashboard.html").is_file()
    assert (output / "operator-snapshot.json").is_file()
    assert (output / "dashboard-export-index.json").is_file()
    for relative, digest in index["content_hashes"].items():
        content = (output / relative).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest
        assert not Path(relative).is_absolute()
    html = (output / "operator-dashboard.html").read_text(encoding="utf-8")
    assert all(section in html for section in ("Overall status", "Runtime", "Safety boundaries", "Blockers", "Operator guidance"))
    assert "<script" not in html.casefold() and "http://" not in html and "https://" not in html
    assert root.exists()


def test_export_is_deterministic_and_json_is_optional(tmp_path):
    first = _export(tmp_path / "first", include_json=False)
    second = _export(tmp_path / "second", include_json=False)
    assert (first[1] / "operator-dashboard.html").read_bytes() == (second[1] / "operator-dashboard.html").read_bytes()
    assert (first[1] / "dashboard-export-index.json").read_bytes() == (second[1] / "dashboard-export-index.json").read_bytes()
    assert not (first[1] / "operator-snapshot.json").exists()


@pytest.mark.parametrize(("field", "value", "action"), [
    ("suspension_status", "ACTIVE", OperatorAction.REVIEW_ACTIVE_SUSPENSION),
    ("recovery_status", "AMBIGUOUS", OperatorAction.REVIEW_AMBIGUOUS_RECOVERY),
    ("heartbeat_status", "STALE", OperatorAction.REVIEW_STALE_HEARTBEAT),
    ("alert_status", "UNCONFIRMED", OperatorAction.CONFIRM_ALERT_DELIVERY),
    ("acknowledgment_status", "PENDING", OperatorAction.COMPLETE_OPERATOR_REVIEW),
    ("evidence_completeness", "PARTIAL", OperatorAction.FAIL_CLOSED_INCOMPLETE_EVIDENCE),
])
def test_adverse_projection_remains_visible(field, value, action, tmp_path):
    _, output, result, _ = _export(tmp_path, **{field: value})
    assert result.recommended_operator_action is action
    html = (output / "operator-dashboard.html").read_text(encoding="utf-8")
    assert value in html
    assert "read-only" in html.casefold()


def test_html_escapes_arbitrary_input_and_never_serializes_private_data(tmp_path):
    root = _fixture(tmp_path / "runtime")
    path, raw = _evidence(root)
    raw["blocker_reasons"] = ["<b>unsafe</b>"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    _, _ = export_dashboard(runtime_root=root, as_of=AS_OF, output_root=tmp_path / "export")
    output = tmp_path / "export"
    html = (output / "operator-dashboard.html").read_text(encoding="utf-8")
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in html and "<b>unsafe</b>" not in html
    raw["blocker_reasons"] = ["C:\\Users\\private"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConsoleError):
        export_dashboard(runtime_root=root, as_of=AS_OF, output_root=tmp_path / "second")


@pytest.mark.parametrize("field", ["permits_automated_action", "live_ready", "unattended_ready", "strategy_enabled"])
def test_safety_boundary_is_rejected(field, tmp_path):
    with pytest.raises(ConsoleSafetyError):
        _export(tmp_path, **{field: True})


def test_completed_or_partial_export_is_not_overwritten(tmp_path):
    _, output, _, _ = _export(tmp_path)
    with pytest.raises(ConsoleError):
        export_dashboard(runtime_root=_fixture(tmp_path / "another-runtime"), as_of=AS_OF, output_root=output)
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "operator-dashboard.html.partial").write_text("partial", encoding="utf-8")
    with pytest.raises(ConsoleError):
        export_dashboard(runtime_root=_fixture(tmp_path / "partial-runtime"), as_of=AS_OF, output_root=partial)


def test_no_durable_store_change_and_exit_code_matches_snapshot(tmp_path):
    root, output, result, _ = _export(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert exit_code(result) == 0
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after
    assert result.to_dict()["live_ready"] is False


def test_cli_help_and_timezone_contract_are_available(tmp_path):
    from scripts.export_operator_dashboard import main
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
