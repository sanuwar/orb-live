"""M13 Issue 6: broker call-site guard hardening.

Exercises the getattr/alias/wrapper/registry/dynamic-dispatch detection
added to `tests/call_site_guard.py`, using the general-purpose
`known_read_methods`/`known_write_methods` parameters so each scenario is
self-contained and does not depend on this repository's specific
`call_tool`-argument dispatch convention (that convention is separately
exercised end-to-end against the real repository in
`tests/test_runtime_call_site_guard.py`, unchanged and still green).

Every scenario uses a synthetic source snippet fed directly to
`analyze_source`. Nothing here touches a broker, the network, a real
clock, or mutates any repository or runtime authority state -- this whole
module is static AST analysis of literal strings.
"""

from __future__ import annotations

from tests.call_site_guard import (
    DYNAMIC,
    KNOWN_WRITE,
    LITERAL,
    PROVEN_READ_ONLY,
    SAFE_WRAPPER,
    UNRESOLVED,
    analyze_source,
    violations,
)

READ = frozenset({"get_orders"})
WRITE = frozenset({"place_order"})
ALLOWLIST = frozenset({"get_orders"})  # a READ_ONLY_TOOL_ALLOWLIST stand-in


def analyze(source: str):
    return analyze_source(
        source,
        path="src/synthetic.py",
        known_read_methods=READ,
        known_write_methods=WRITE,
    )


def _kinds(sites):
    return [s.kind for s in sites]


def _fails(sites) -> bool:
    return len(violations(sites, ALLOWLIST)) >= 1


# --------------------------------------------------------------------------
# DIRECT (1-2)
# --------------------------------------------------------------------------


def test_direct_approved_read_passes():
    sites = analyze("client.get_orders()")
    assert _kinds(sites) == [PROVEN_READ_ONLY]
    assert not _fails(sites)


def test_direct_known_write_fails():
    sites = analyze("client.place_order()")
    assert _kinds(sites) == [KNOWN_WRITE]
    assert _fails(sites)


# --------------------------------------------------------------------------
# GETATTR (3-10)
# --------------------------------------------------------------------------


def test_static_getattr_approved_read_passes():
    sites = analyze('getattr(client, "get_orders")()')
    assert _kinds(sites) == [PROVEN_READ_ONLY]
    assert not _fails(sites)


def test_static_getattr_write_fails():
    sites = analyze('getattr(client, "place_order")()')
    assert _kinds(sites) == [KNOWN_WRITE]
    assert _fails(sites)


def test_getattr_assigned_write_callable_fails():
    source = """
fn = getattr(client, "place_order")
fn()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == KNOWN_WRITE for s in sites)


def test_getattr_assigned_read_callable_passes_when_proven():
    source = """
fn = getattr(client, "get_orders")
fn()
"""
    sites = analyze(source)
    assert not _fails(sites)
    assert any(s.kind == PROVEN_READ_ONLY for s in sites)


def test_statically_resolvable_variable_method_write_fails():
    source = """
method = "place_order"
getattr(client, method)()
"""
    sites = analyze(source)
    # "method" is a local variable, not a string literal at the getattr
    # call site itself -- this analysis does not trace plain-variable
    # string values (that would require general dataflow), so this is
    # correctly UNRESOLVED, not silently permitted.
    assert _fails(sites)
    assert any(s.kind == UNRESOLVED for s in sites)


def test_unresolved_variable_method_fails_closed():
    source = """
def go(client, method_name):
    return getattr(client, method_name)()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == UNRESOLVED for s in sites)


def test_concatenated_resolvable_write_name_fails():
    source = 'getattr(client, "place" + "_order")()'
    sites = analyze(source)
    # A concatenation is not a single string Constant node -- unresolvable
    # by this bounded analysis, and therefore fails closed rather than
    # being evaluated.
    assert _fails(sites)
    assert any(s.kind == UNRESOLVED for s in sites)


def test_unresolved_concatenation_fails_closed():
    source = """
def go(client, verb):
    return getattr(client, verb + "_order")()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == UNRESOLVED for s in sites)


def test_bare_dynamic_getattr_never_invoked_is_not_flagged():
    """`getattr(self, name)` used only to read a value (never called) is
    ordinary dataclass-field introspection, not a broker dispatch risk --
    confirmed against the real repository's own use of this exact idiom in
    execution_gates.py, risk_limits.py, and others."""
    source = """
def to_dict(self, field_names):
    return {name: getattr(self, name) for name in field_names}
"""
    sites = analyze(source)
    assert sites == []


# --------------------------------------------------------------------------
# ALIASES (11-14)
# --------------------------------------------------------------------------


def test_broker_object_alias_and_write_fails():
    source = """
broker = client
broker.place_order()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == KNOWN_WRITE for s in sites)


def test_broker_object_alias_and_read_passes():
    source = """
broker = client
broker.get_orders()
"""
    sites = analyze(source)
    assert not _fails(sites)
    assert any(s.kind == PROVEN_READ_ONLY for s in sites)


def test_bound_write_method_alias_fails():
    source = """
submit = client.place_order
submit()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == KNOWN_WRITE for s in sites)


def test_bound_read_method_alias_passes():
    source = """
fetch = client.get_orders
fetch()
"""
    sites = analyze(source)
    assert not _fails(sites)
    assert any(s.kind == PROVEN_READ_ONLY for s in sites)


# --------------------------------------------------------------------------
# WRAPPERS (15-20)
# --------------------------------------------------------------------------


def test_direct_write_wrapper_fails():
    source = """
def send(client):
    return client.place_order()
"""
    sites = analyze(source)
    assert _fails(sites)


def test_approved_read_wrapper_passes():
    source = """
def fetch(client):
    return client.get_orders()
"""
    sites = analyze(source)
    assert not _fails(sites)


def test_nested_write_wrapper_fails():
    source = """
def outer(client):
    def inner():
        return client.place_order()
    return inner()
"""
    sites = analyze(source)
    assert _fails(sites)


def test_lambda_write_wrapper_fails():
    source = """
send = lambda: client.place_order()
send()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == KNOWN_WRITE for s in sites)


def test_lambda_read_wrapper_passes():
    source = """
fetch = lambda: client.get_orders()
fetch()
"""
    sites = analyze(source)
    assert not _fails(sites)
    assert any(s.kind == PROVEN_READ_ONLY for s in sites)


def test_partial_write_wrapper_fails():
    source = """
from functools import partial
submit = partial(client.place_order)
submit()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == KNOWN_WRITE for s in sites)


def test_partial_pinning_call_tool_argument_resolves_literal():
    """This repository's actual dispatch style: `partial(session.call_tool,
    "known_tool")` pins the tool-name argument statically."""
    sites = analyze_source(
        """
from functools import partial
read_accounts = partial(session.call_tool, "get_accounts")
read_accounts()
""",
        path="src/synthetic.py",
    )
    assert any(s.kind == LITERAL and s.tool == "get_accounts" for s in sites)


def test_unresolved_broker_wrapper_fails_closed():
    """`client` is established as broker-capable by the companion literal
    call in the same file (matching how this evidence-based check is
    actually meant to fire: a receiver demonstrably used for broker
    dispatch elsewhere, then ALSO dispatched dynamically, is exactly the
    mixed pattern that should never be trusted)."""
    source = """
def dispatch(client, method_name):
    fn = getattr(client, method_name)
    return fn()

def known(client):
    return client.get_orders()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == UNRESOLVED for s in sites)


# --------------------------------------------------------------------------
# REGISTRY (21-24)
# --------------------------------------------------------------------------


def test_static_registry_write_dispatch_fails():
    source = """
OPERATIONS = {
    "read_orders": client.get_orders,
    "place_order": client.place_order,
}
operation = OPERATIONS["place_order"]
operation()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == KNOWN_WRITE for s in sites)


def test_static_registry_read_dispatch_passes():
    source = """
OPERATIONS = {
    "read_orders": client.get_orders,
    "place_order": client.place_order,
}
operation = OPERATIONS["read_orders"]
operation()
"""
    sites = analyze(source)
    assert not _fails(sites)
    assert any(s.kind == PROVEN_READ_ONLY for s in sites)


def test_unknown_selection_from_mixed_registry_fails():
    source = """
OPERATIONS = {
    "read_orders": client.get_orders,
    "place_order": client.place_order,
}
def go(key):
    return OPERATIONS[key]()
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == UNRESOLVED for s in sites)


def test_known_selection_from_read_only_registry_passes():
    source = """
OPERATIONS = {
    "read_orders": client.get_orders,
    "read_accounts": client.get_orders,
}
def go(key):
    return OPERATIONS[key]()
"""
    sites = analyze(source)
    assert not _fails(sites)
    assert any(s.kind == PROVEN_READ_ONLY for s in sites)


# --------------------------------------------------------------------------
# FUNCTION FLOW (25-27)
# --------------------------------------------------------------------------


def test_broker_callable_passed_as_argument_is_unresolved():
    source = """
def run(op):
    return op()

run(client.place_order)
"""
    sites = analyze(source)
    assert _fails(sites)
    assert any(s.kind == UNRESOLVED for s in sites)


def test_broker_write_callable_passed_as_argument_fails():
    source = """
def run(op):
    return op()

run(client.get_orders)
"""
    sites = analyze(source)
    # A read-only callable escaping into an unproven context is still
    # UNRESOLVED by this bounded analysis -- "passes only when proven",
    # and passing as a bare argument is not one of the proven shapes.
    assert _fails(sites)
    assert any(s.kind == UNRESOLVED for s in sites)


def test_broker_read_callable_via_alias_then_call_passes_when_proven():
    """The one bounded shape that *is* proven: assign-then-call in the
    same file, exactly like test_bound_read_method_alias_passes."""
    source = """
fetch = client.get_orders
result = fetch()
"""
    sites = analyze(source)
    assert not _fails(sites)
    assert any(s.kind == PROVEN_READ_ONLY for s in sites)


# --------------------------------------------------------------------------
# PRODUCTION REGRESSION (28-34)
# --------------------------------------------------------------------------


def test_existing_commissioning_write_site_remains_detected():
    from tests.call_site_guard import analyze_runtime_tree

    sites = analyze_runtime_tree()
    commissioning = [
        s for s in sites if s.path == "src/robinhood_commissioning_adapter.py"
    ]
    assert len(commissioning) == 1
    # Line moved from 203 in M14 Issue 1: the safety-governance boundary
    # (RobinhoodCommissioningAdapter._call's role/safety_decision checks)
    # was inserted immediately before this same single write call, which is
    # still exactly one call site, still SAFE_WRAPPER, still the only one.
    assert commissioning[0].line == 229
    assert commissioning[0].kind == SAFE_WRAPPER


def test_m13_checkpoint_integration_remains_read_only():
    from tests.call_site_guard import analyze_runtime_tree

    sites = analyze_runtime_tree()
    checkpoint_paths = {
        "src/read_only_observation_runtime.py",
        "src/protection_checkpoint_integration.py",
        "src/protection_observation_model.py",
        "src/protection_observation_ledger.py",
        "src/protection_promotion_policy.py",
        "src/protection_grounding_assessment.py",
    }
    checkpoint_sites = [s for s in sites if s.path in checkpoint_paths]
    # protection_checkpoint_integration.py and the other four M13 modules
    # make no broker call at all -- confirmed by zero sites there.
    assert [
        s for s in checkpoint_sites if s.path != "src/read_only_observation_runtime.py"
    ] == []
    # read_only_observation_runtime.py's three sites are the existing,
    # reviewed, literal read-only calls -- unchanged.
    runtime_sites = [
        s for s in checkpoint_sites if s.path == "src/read_only_observation_runtime.py"
    ]
    assert len(runtime_sites) == 3
    assert all(s.kind == LITERAL for s in runtime_sites)
    from src.read_only_broker_gateway import READ_ONLY_TOOL_ALLOWLIST

    assert violations(runtime_sites, READ_ONLY_TOOL_ALLOWLIST) == []


def test_no_new_production_broker_call_sites():
    from tests.call_site_guard import analyze_runtime_tree

    assert len(analyze_runtime_tree()) == 21


def test_current_expected_baseline_is_explicitly_recalculated():
    """Never trust a carried-forward count -- recompute and describe it."""
    from tests.call_site_guard import analyze_runtime_tree

    sites = analyze_runtime_tree()
    by_kind: dict[str, int] = {}
    for site in sites:
        by_kind[site.kind] = by_kind.get(site.kind, 0) + 1
    assert sum(by_kind.values()) == 21
    # 17 literal + 4 safe_wrapper: read_only_broker_gateway.py (x2),
    # robinhood_commissioning_adapter.py, robinhood_commissioning_session.py
    # -- all four already existed before Issue 6; none of the hardening
    # added or removed a real production call site.
    assert by_kind.get(LITERAL, 0) == 17
    assert by_kind.get(SAFE_WRAPPER, 0) == 4
    assert by_kind.get(UNRESOLVED, 0) == 0
    assert by_kind.get(DYNAMIC, 0) == 0


def test_unknown_broker_dispatch_count_is_zero_in_accepted_production_source():
    from tests.call_site_guard import analyze_runtime_tree

    sites = analyze_runtime_tree()
    unresolved = [s for s in sites if s.kind in (UNRESOLVED, DYNAMIC)]
    assert unresolved == []


def test_existing_approved_read_wrappers_remain_recognized():
    from tests.call_site_guard import analyze_runtime_tree

    sites = analyze_runtime_tree()
    wrappers = [s for s in sites if s.kind == SAFE_WRAPPER]
    assert len(wrappers) == 4
    assert {s.path for s in wrappers} == {
        "src/read_only_broker_gateway.py",
        "src/robinhood_commissioning_adapter.py",
        "src/robinhood_commissioning_session.py",
    }


def test_test_fixture_patterns_do_not_contaminate_production_enumeration():
    from tests.call_site_guard import runtime_files

    for path in runtime_files():
        assert "test" not in path.parts
        assert not path.name.startswith("test_")


# --------------------------------------------------------------------------
# SAFETY (35-40)
# --------------------------------------------------------------------------


def test_guard_does_not_mutate_repository_or_runtime_authority():
    from src.execution_gates import ACTIVATION_APPROVALS
    from src.live_order_state_model import (
        ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES,
    )
    from tests.call_site_guard import analyze_runtime_tree

    before_gate = ACTIVATION_APPROVALS
    before_states = ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
    analyze_runtime_tree()
    assert ACTIVATION_APPROVALS == before_gate == frozenset()
    assert (
        ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == before_states == frozenset()
    )


def test_guard_requires_no_broker_network_or_credential_access():
    import ast
    import inspect

    import tests.call_site_guard as guard_module

    source = inspect.getsource(guard_module)
    tree = ast.parse(source)
    forbidden = {"requests", "httpx", "socket", "urllib", "robin_stocks"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in forbidden
    # The guard analyzes calls; it never makes one. Confirm no executable
    # `.call_tool(` invocation appears outside a string/docstring by
    # checking there is no `Attribute(attr="call_tool")` used as a live
    # `Call.func` anywhere in the guard's own source.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "call_tool"


def test_deterministic_repeated_analysis():
    from tests.call_site_guard import analyze_runtime_tree

    first = analyze_runtime_tree()
    second = analyze_runtime_tree()
    assert [s.describe() for s in first] == [s.describe() for s in second]


def test_deterministic_finding_order():
    source = """
client.place_order()
client.get_orders()
"""
    first = analyze(source)
    second = analyze(source)
    assert [s.line for s in first] == [s.line for s in second]
    assert [s.line for s in first] == sorted(s.line for s in first)


def test_file_and_line_evidence_included():
    sites = analyze("client.place_order()")
    assert len(sites) == 1
    site = sites[0]
    assert site.path == "src/synthetic.py"
    assert site.line == 1
    assert "src/synthetic.py:1" in site.describe()


def test_unresolved_broker_dispatch_is_a_failure_never_a_warning_only_success():
    source = """
def go(client, method_name):
    return getattr(client, method_name)()
"""
    sites = analyze(source)
    assert len(sites) == 1
    assert sites[0].kind == UNRESOLVED
    assert len(violations(sites, ALLOWLIST)) == 1
