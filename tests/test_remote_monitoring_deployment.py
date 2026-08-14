from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cloudflare_tunnel_documentation_exposes_only_remote_read_only_port():
    text = (ROOT / "M25_REMOTE_READ_ONLY_MONITORING.md").read_text(encoding="utf-8")
    assert "service: http://127.0.0.1:8777" in text
    assert "service: http://127.0.0.1:8766" not in text
    assert "No router port forwarding" in text
    assert "ORB_REMOTE_PASSWORD_HASH" in text and "ORB_REMOTE_ORIGIN" in text
    assert "cloudflared tunnel run orb-live-monitor" in text
    assert "<configured-domain>" in text


def test_remote_server_and_static_bundle_are_separate_from_local_control():
    runner = (ROOT / "scripts" / "run_remote_monitoring.py").read_text(encoding="utf-8")
    remote_vite = (ROOT / "site" / "vite.remote.config.js").read_text(encoding="utf-8")
    package = (ROOT / "site" / "package.json").read_text(encoding="utf-8")
    assert 'outDir: \'dist-remote\'' in remote_vite
    assert "vite build --config vite.remote.config.js" in package
    assert '"dist-remote"' in runner
    assert "localControlClient" not in remote_vite
    assert "8766" not in runner


def test_remote_python_boundary_has_no_broker_write_or_process_launch_reachability():
    paths = [
        ROOT / "src" / "remote_monitoring_projection.py",
        ROOT / "src" / "remote_monitoring_source.py",
        ROOT / "src" / "remote_monitoring_service.py",
        ROOT / "scripts" / "run_remote_monitoring.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    forbidden = {
        "RobinhoodCommissioningAdapter", "call_tool", "subprocess", "Popen",
        "place_equity_order", "cancel_equity_order", "replace_equity_order", "flatten",
    }
    assert not (names & forbidden)
    assert "src.local_watcher_control" not in source
    assert "ACTIVATION_APPROVALS" not in source
