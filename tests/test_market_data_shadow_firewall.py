"""M15 Issue 1: structural proof that the production market-data / shadow
decision runtime has no call path to any broker-write-capable code.

This does NOT merely rely on the fact that nothing currently calls a write
tool -- it independently scans the AST of every M15 module for a reference
to anything broker-write-capable, at all, anywhere (including inside a
function body), matching the same rigor `scripts/verify_execution_safety_
governance.py` applies to the M14 commissioning boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: Every M15 (production market-data / shadow decision) module.
M15_MODULES = (
    "src/market_data_canonical.py",
    "src/market_data_provider.py",
    "src/shadow_trading_decision.py",
)

#: Names that, if referenced anywhere in an M15 module's AST, would mean a
#: structural path toward a real broker write exists. Each is either a
#: broker-write-capable class/function, the Robinhood client itself (M15
#: must never authenticate as a broker, even read-only, to stay clear of
#: data/.rh_tokens.json), or the raw MCP dispatch method writes go through.
FORBIDDEN_IDENTIFIERS = frozenset({
    "RobinhoodCommissioningAdapter",
    "RobinhoodCommissioningSessionBinding",
    "OneShareCommissioningOrchestrator",
    "place_equity_order",
    "cancel_equity_order",
    "call_tool",
    "RobinhoodClient",
    "RobinhoodClientError",
    "evaluate_execution_safety",
    "ExecutionSafetyDecision",
})

#: Modules an M15 file must never import, by dotted name.
FORBIDDEN_IMPORT_MODULES = frozenset({
    "src.robinhood_commissioning_adapter",
    "src.robinhood_commissioning_session",
    "src.one_share_commissioning_orchestrator",
    "src.one_share_commissioning",
    "src.robinhood_client",
    "src.execution_safety_governance",
    "src.execution_safety_governance_deferred_gates",
})


def _module_tree(relative: str) -> ast.Module:
    return ast.parse((REPOSITORY_ROOT / relative).read_text(encoding="utf-8-sig"))


def test_m15_modules_exist_and_parse():
    for relative in M15_MODULES:
        path = REPOSITORY_ROOT / relative
        assert path.is_file(), relative
        _module_tree(relative)  # raises SyntaxError if malformed


def test_m15_modules_never_import_broker_write_capable_modules():
    for relative in M15_MODULES:
        tree = _module_tree(relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in FORBIDDEN_IMPORT_MODULES, (
                        relative, alias.name,
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module not in FORBIDDEN_IMPORT_MODULES, (relative, module)


def test_m15_modules_never_name_a_broker_write_identifier():
    """Every forbidden identifier is absent from each module's own AST
    names -- not merely absent from its imports. A `Name`/`Attribute`
    reference without a matching import would still mean the concept
    leaked in some other way (a local re-definition, a string built into
    an identifier some other way); this proves it did not, for every
    identifier this firewall claims is absent."""
    for relative in M15_MODULES:
        tree = _module_tree(relative)
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
        offending = referenced & FORBIDDEN_IDENTIFIERS
        assert not offending, (relative, offending)


def test_m15_modules_never_call_session_call_tool():
    """A narrower, syntax-level check specifically for the
    `<receiver>.call_tool(...)` dispatch shape M14's own verifier treats as
    the one real broker-write idiom in this repository -- redundant with
    the identifier scan above (which already forbids the bare name
    `call_tool`), kept as an independent technique rather than relying on
    a single detection method."""
    for relative in M15_MODULES:
        tree = _module_tree(relative)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "call_tool"
            ):
                raise AssertionError(f"{relative} calls .call_tool(...) at line {node.lineno}")


def test_shadow_trading_decision_cannot_reach_the_adapter_write_boundary():
    """End-to-end, not just structural: construct a real ShadowTradingDecision
    and confirm RobinhoodCommissioningAdapter's write boundary refuses it
    the same way it refuses any other non-ExecutionSafetyDecision object."""
    import asyncio
    from datetime import datetime, timezone
    from decimal import Decimal

    from src.market_data_canonical import MarketDataQuality
    from src.robinhood_commissioning_adapter import (
        ExecutionSafetyDeniedError, RobinhoodCommissioningAdapter,
    )
    from src.shadow_trading_decision import ShadowDecisionType, ShadowTradingDecision

    now = datetime.now(timezone.utc)
    shadow_decision = ShadowTradingDecision(
        shadow_run_id="shadow-run-firewall-test",
        decision_id="d" * 64,
        symbol="AAPL",
        trading_date=now.date(),
        strategy_version="orb_long_v0-0.1.0",
        implementation_digest="a" * 64,
        market_data_digest="b" * 64,
        opening_range_identity="c" * 64,
        signal_timestamp=now,
        decision_type=ShadowDecisionType.LONG_CANDIDATE,
        proposed_entry=Decimal("204.10"),
        proposed_stop=Decimal("202.00"),
        proposed_target=Decimal("208.30"),
        reason="ORB V0 long-entry signal approved.",
        market_data_quality=MarketDataQuality.VALID,
        exchange_session="REGULAR",
        created_at=now,
    )

    class _RecordingSession:
        def __init__(self):
            self.calls = 0

        async def call_tool(self, name, arguments):
            self.calls += 1
            raise AssertionError("a broker call must never be reached")

    session = _RecordingSession()
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    entry_payload = {
        "account_number": "transient", "symbol": "AAPL", "side": "buy",
        "type": "market", "quantity": "1", "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }
    with pytest.raises(ExecutionSafetyDeniedError):
        asyncio.run(adapter.place_equity_order(
            entry_payload, role="entry", safety_decision=shadow_decision,
        ))
    assert session.calls == 0


def test_broker_call_site_inventory_is_unchanged_by_m15():
    """M15 must not introduce a new write-tool-capable call_tool site.
    Reuses the M14 Issue 4 verifier's own independent dispatch scanner
    rather than a third, competing implementation."""
    import sys

    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
    try:
        import verify_execution_safety_governance as verifier
    finally:
        sys.path.pop(0)

    sites = verifier._independent_dispatch_scan(REPOSITORY_ROOT)
    write_capable_files = sorted({
        site.path for site in sites
        if site.classification == "validated_write_capable"
    })
    assert write_capable_files == [
        "src/robinhood_commissioning_adapter.py",
        "src/robinhood_commissioning_session.py",
    ], write_capable_files
    unresolved = [site for site in sites if site.classification == "unresolved"]
    assert unresolved == [], unresolved
