from __future__ import annotations

from pathlib import Path

import pytest

from tests.call_site_guard import (
    DYNAMIC,
    LITERAL,
    SAFE_WRAPPER,
    analyze_runtime_tree,
    analyze_source,
    runtime_files,
    violations,
)

from src.read_only_broker_gateway import (
    READ_ONLY_TOOL_ALLOWLIST,
)


REPOSITORY_ROOT = Path(__file__).parent.parent


def scanned_paths() -> set[str]:
    return {
        str(path.relative_to(REPOSITORY_ROOT)).replace(
            "\\", "/"
        )
        for path in runtime_files()
    }


# ---------- coverage of the scanned tree ----------

def test_guard_includes_src():
    paths = scanned_paths()

    assert "src/account_state.py" in paths
    assert "src/read_only_broker_gateway.py" in paths
    assert "src/robinhood_client.py" in paths


def test_guard_includes_scripts():
    paths = scanned_paths()

    assert "scripts/inspect_equity_order_shape.py" in paths
    assert "scripts/inspect_order_tools.py" in paths


def test_guard_excludes_tests_and_environments():
    for path in scanned_paths():
        assert not path.startswith("tests/")
        assert ".venv" not in path
        assert "__pycache__" not in path
        assert "scratchpad" not in path


# ---------- the live repository must be clean ----------

def test_repository_has_no_violating_call_site():
    offending = violations(
        analyze_runtime_tree(), READ_ONLY_TOOL_ALLOWLIST
    )

    assert [site.describe() for site in offending] == []


def test_every_runtime_call_site_is_accounted_for():
    sites = analyze_runtime_tree()

    assert sites, "the guard found no call sites at all"

    for site in sites:
        assert site.kind in {LITERAL, SAFE_WRAPPER}

        if site.kind == LITERAL:
            assert site.tool in READ_ONLY_TOOL_ALLOWLIST


def test_manual_read_only_scripts_pass():
    sites = [
        site
        for site in analyze_runtime_tree()
        if site.path.startswith("scripts/")
    ]

    assert violations(sites, READ_ONLY_TOOL_ALLOWLIST) == []

    shape_sites = [
        site
        for site in sites
        if "inspect_equity_order_shape" in site.path
    ]

    assert len(shape_sites) == 1
    assert shape_sites[0].tool == "get_equity_orders"


def test_capability_enumeration_script_has_no_tool_call():
    """
    scripts/inspect_order_tools.py calls session.list_tools() only.

    It enumerates the server's advertised capabilities — which will
    include write tools — but invokes none of them. It is a manual
    diagnostic and is not part of the reconciliation path.
    """

    sites = [
        site
        for site in analyze_runtime_tree()
        if "inspect_order_tools" in site.path
    ]

    assert sites == []


# ---------- synthetic rejection cases ----------

WRITE_CALL = """
async def go(session):
    return await session.call_tool("place_equity_order", {})
"""

GATEWAY_WRITE_CALL = """
async def go(gateway):
    return await gateway.read("cancel_equity_order", {})
"""

DYNAMIC_CALL = """
async def go(session, name):
    return await session.call_tool(name, {})
"""

DYNAMIC_ATTRIBUTE_CALL = """
async def go(session, config):
    return await session.call_tool(config.tool_name, {})
"""

FSTRING_CALL = """
async def go(session, verb):
    return await session.call_tool(f"{verb}_equity_order", {})
"""

CONSTANT_WRITE_CALL = """
TOOL = "replace_equity_order"

async def go(session):
    return await session.call_tool(TOOL, {})
"""

SAFE_WRAPPER_CALL = """
async def go(session, requested):
    allowed = assert_tool_allowed(requested)
    return await session.call_tool(allowed, {})
"""

REJECTION_FIXTURE = """
WRITE_TOOL_NAMES = (
    "place_equity_order",
    "cancel_equity_order",
    "review_equity_order",
)

def test_rejected():
    for name in WRITE_TOOL_NAMES:
        assert name not in ALLOWLIST
"""

INNOCENT_READS = """
def load(path, handle, gate):
    a = path.read_text(encoding="utf-8")
    b = handle.read()
    c = gate.read_bytes()
    return a, b, c
"""


@pytest.mark.parametrize(
    "source",
    [WRITE_CALL, GATEWAY_WRITE_CALL, CONSTANT_WRITE_CALL],
)
def test_synthetic_write_call_is_rejected(source):
    sites = analyze_source(source, path="src/synthetic.py")
    offending = violations(sites, READ_ONLY_TOOL_ALLOWLIST)

    assert len(offending) == 1


def test_synthetic_write_call_under_src_is_rejected(
    tmp_path,
):
    root = tmp_path
    (root / "src").mkdir()
    (root / "src" / "leak.py").write_text(
        WRITE_CALL, encoding="utf-8"
    )

    offending = violations(
        analyze_runtime_tree(root), READ_ONLY_TOOL_ALLOWLIST
    )

    assert len(offending) == 1
    assert offending[0].path == "src/leak.py"


def test_synthetic_write_call_under_scripts_is_rejected(
    tmp_path,
):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "scripts" / "leak.py").write_text(
        GATEWAY_WRITE_CALL, encoding="utf-8"
    )

    offending = violations(
        analyze_runtime_tree(root), READ_ONLY_TOOL_ALLOWLIST
    )

    assert len(offending) == 1
    assert offending[0].path == "scripts/leak.py"


@pytest.mark.parametrize(
    "source",
    [DYNAMIC_CALL, DYNAMIC_ATTRIBUTE_CALL, FSTRING_CALL],
)
def test_dynamic_tool_name_is_rejected(source):
    sites = analyze_source(source, path="src/synthetic.py")

    assert [site.kind for site in sites] == [DYNAMIC]
    assert len(
        violations(sites, READ_ONLY_TOOL_ALLOWLIST)
    ) == 1


def test_documented_safe_wrapper_is_accepted():
    sites = analyze_source(
        SAFE_WRAPPER_CALL, path="src/synthetic.py"
    )

    assert [site.kind for site in sites] == [SAFE_WRAPPER]
    assert violations(sites, READ_ONLY_TOOL_ALLOWLIST) == []


ALIASED_GATEWAY_CALL = """
async def go(g):
    return await g.read("modify_equity_order", {})
"""

BUFFERED_READ = """
def load(handle, stream):
    return handle.read(), stream.read(1024)
"""


def test_aliased_gateway_receiver_is_still_caught():
    """A gateway bound to an unrevealing name must not slip past."""

    sites = analyze_source(
        ALIASED_GATEWAY_CALL, path="src/synthetic.py"
    )

    assert len(sites) == 1
    assert (
        len(violations(sites, READ_ONLY_TOOL_ALLOWLIST))
        == 1
    )


def test_buffered_reads_are_not_call_sites():
    sites = analyze_source(
        BUFFERED_READ, path="src/synthetic.py"
    )

    assert sites == []


def test_rejection_fixtures_are_not_false_positives():
    sites = analyze_source(
        REJECTION_FIXTURE, path="tests/synthetic.py"
    )

    assert sites == []


def test_ordinary_read_methods_are_not_call_sites():
    sites = analyze_source(
        INNOCENT_READS, path="src/synthetic.py"
    )

    assert sites == []
