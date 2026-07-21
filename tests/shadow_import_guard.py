"""
Static AST import guard for the Milestone 6 shadow-mode modules.

The bounded shadow runtime and its append-only campaign store must never
couple to any executable lifecycle, saga, executor, operator-resolution,
or pre-existing broker-monitoring module. This guard parses each Milestone
6 module and flags a forbidden import whether it appears directly or
inside a try/except fallback import block, because ``ast.walk`` visits
both branches.
"""
from __future__ import annotations

import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPOSITORY_ROOT / "src"

# The Milestone 6 modules whose imports are constrained.
MILESTONE_6_MODULES = (
    "hypothetical_strategy_runtime.py",
    "shadow_campaign_store.py",
)

# Module leaf names that must never be imported by a Milestone 6 module.
FORBIDDEN_IMPORT_NAMES = frozenset(
    {
        "shadow_runtime",
        "trade_state",
        "broker_action_saga",
        "broker_saga_orchestrator",
        "broker_saga_executor",
        "operator_resolution",
        "entry_lifecycle",
        "exit_order_lifecycle",
        "protective_stop_lifecycle",
        "order_monitor",
        "order_state",
        "broker_adapter",
        "broker_action_plan",
        "broker_action_authorization",
        "simulated_broker_transport",
    }
)


def _imported_name_parts(tree: ast.AST) -> set[str]:
    """Every dotted component named by any import in the tree."""

    parts: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                parts.update(node.module.split("."))
            for alias in node.names:
                parts.update(alias.name.split("."))

    return parts


def forbidden_imports_in_source(source: str) -> set[str]:
    tree = ast.parse(source)
    return _imported_name_parts(tree) & FORBIDDEN_IMPORT_NAMES


def forbidden_imports_in_module(path: Path) -> set[str]:
    return forbidden_imports_in_source(
        path.read_text(encoding="utf-8-sig")
    )


def milestone_6_module_paths() -> list[Path]:
    return [SOURCE_ROOT / name for name in MILESTONE_6_MODULES]
