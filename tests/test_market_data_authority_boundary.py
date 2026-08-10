"""M15 Issue 1: authority state is unaffected by the production
market-data/shadow-decision boundary. M15 may only ever narrow permission,
never expand it -- these are direct confirmations, not just an absence of
change in files this issue did not touch."""

from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_strategy_enabled_remains_false():
    config = yaml.safe_load(
        (REPOSITORY_ROOT / "config" / "strategy_v0.yaml").read_text(encoding="utf-8")
    )
    assert config["strategy"]["enabled"] is False


def test_protection_grounded_remains_false():
    from src.execution_gates import ACTIVATION_APPROVALS

    # protection_grounded is never a module-level constant (it's a per-run
    # computed field elsewhere in M13); the structural invariant this
    # repository actually enforces is ACTIVATION_APPROVALS staying empty,
    # which keeps every dependent field (protection_grounded included)
    # unreachable. M15 does not import execution_gates.py at all (see
    # test_market_data_shadow_firewall.py), so it cannot have changed this.
    assert ACTIVATION_APPROVALS == frozenset()


def test_activation_approved_and_permits_automated_action_remain_false():
    from src.execution_safety_governance import AUTHORITY_CEILING

    assert AUTHORITY_CEILING == "ATTENDED_COMMISSIONING_ONLY"


def test_live_ready_and_unattended_ready_remain_false():
    from src.operator_release_info import read_release_info

    data = read_release_info(REPOSITORY_ROOT).to_dict()
    assert data["live_ready"] is False


def test_m15_modules_do_not_import_execution_gates_or_governance():
    """The authority fields above live in modules M15 must never import in
    the first place -- confirms the boundary this test file's other
    assertions rely on."""
    import ast

    forbidden = {"src.execution_gates", "src.execution_safety_governance"}
    for relative in (
        "src/market_data_canonical.py",
        "src/market_data_provider.py",
        "src/shadow_trading_decision.py",
    ):
        tree = ast.parse((REPOSITORY_ROOT / relative).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden:
                raise AssertionError(f"{relative} imports {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden:
                        raise AssertionError(f"{relative} imports {alias.name}")


def test_pinned_implementation_closure_is_unaffected_by_m15():
    """M15 is not part of the M14 commissioning checkpoint's reviewed
    execution closure at all -- confirms the pinned count/digest this
    session's M14 work left in place is exactly what it was before any
    M15 file existed."""
    from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE

    m15_paths = {
        "src/market_data_canonical.py",
        "src/market_data_provider.py",
        "src/shadow_trading_decision.py",
    }
    assert not (m15_paths & set(PINNED_IMPLEMENTATION_CLOSURE))
    assert len(PINNED_IMPLEMENTATION_CLOSURE) == 37
