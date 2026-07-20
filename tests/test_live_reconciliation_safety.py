from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.config import load_config
from src.read_only_broker_gateway import (
    FORBIDDEN_TOOL_MARKERS,
    READ_ONLY_TOOL_ALLOWLIST,
)


REPOSITORY_ROOT = Path(__file__).parent.parent
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"
TESTS_DIRECTORY = REPOSITORY_ROOT / "tests"

MILESTONE_MODULES = (
    "read_only_broker_gateway.py",
    "broker_identity.py",
    "live_reconciliation.py",
)

# This module is the scanner. It is excluded from the scanned set
# because it necessarily contains the forbidden markers as literals.
MILESTONE_TESTS = (
    "test_read_only_broker_gateway.py",
    "test_broker_identity.py",
    "test_live_reconciliation.py",
)

CALL_TOOL_PATTERN = re.compile(
    r"call_tool\(\s*[\"']([A-Za-z0-9_]+)[\"']"
)

NETWORK_MARKERS = (
    "socket.socket",
    "urllib.request",
    "requests.get",
    "requests.post",
    "httpx.",
    "aiohttp.",
)

CREDENTIAL_MARKERS = (
    ".rh_tokens.json",
    "tokens.json",
    "credentials.json",
    "cookies.json",
)


def source_files():
    return sorted(SOURCE_DIRECTORY.glob("*.py"))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_write_tool_is_called_anywhere_in_src():
    offenders: list[str] = []

    for path in source_files():
        for tool_name in CALL_TOOL_PATTERN.findall(
            read(path)
        ):
            lowered = tool_name.lower()

            if any(
                marker in lowered
                for marker in FORBIDDEN_TOOL_MARKERS
            ):
                offenders.append(
                    f"{path.name}: {tool_name}"
                )

    assert offenders == []


def test_every_call_tool_site_is_allowlisted():
    unexpected: list[str] = []

    for path in source_files():
        for tool_name in CALL_TOOL_PATTERN.findall(
            read(path)
        ):
            if (
                tool_name
                not in READ_ONLY_TOOL_ALLOWLIST
            ):
                unexpected.append(
                    f"{path.name}: {tool_name}"
                )

    assert unexpected == []


def test_allowlist_has_no_write_capability():
    for tool_name in READ_ONLY_TOOL_ALLOWLIST:
        lowered = tool_name.lower()

        assert lowered.startswith("get_")

        for marker in FORBIDDEN_TOOL_MARKERS:
            assert marker not in lowered


@pytest.mark.parametrize(
    "module_name", MILESTONE_MODULES
)
def test_milestone_modules_import_no_order_builders(
    module_name,
):
    source = read(SOURCE_DIRECTORY / module_name)

    assert "broker_adapter" not in source
    assert "build_marketable_entry_limit" not in source
    assert "build_protective_stop_market" not in source
    assert "build_marketable_exit_limit" not in source
    assert "review_arguments" not in source


@pytest.mark.parametrize(
    "module_name", MILESTONE_MODULES
)
def test_milestone_modules_touch_no_credentials(
    module_name,
):
    source = read(SOURCE_DIRECTORY / module_name)

    for marker in CREDENTIAL_MARKERS:
        assert marker not in source


@pytest.mark.parametrize(
    "module_name", MILESTONE_MODULES
)
def test_milestone_modules_open_no_network(
    module_name,
):
    source = read(SOURCE_DIRECTORY / module_name)

    for marker in NETWORK_MARKERS:
        assert marker not in source


@pytest.mark.parametrize(
    "module_name", MILESTONE_MODULES
)
def test_milestone_modules_use_no_default_database(
    module_name,
):
    source = read(SOURCE_DIRECTORY / module_name)

    assert "DEFAULT_DB_PATH" not in source
    assert "data/trade_state.sqlite" not in source


@pytest.mark.parametrize(
    "test_name", MILESTONE_TESTS
)
def test_milestone_tests_open_no_live_session(
    test_name,
):
    source = read(TESTS_DIRECTORY / test_name)

    for marker in NETWORK_MARKERS:
        assert marker not in source

    for marker in CREDENTIAL_MARKERS:
        assert marker not in source

    assert "RobinhoodClient(" not in source
    assert "AccountStateReader(" not in source


@pytest.mark.parametrize(
    "test_name", MILESTONE_TESTS
)
def test_milestone_tests_reference_no_repository_data(
    test_name,
):
    source = read(TESTS_DIRECTORY / test_name)

    assert 'Path("data' not in source
    assert "data/trade_state.sqlite" not in source


def test_gateway_has_no_order_payload_surface():
    source = read(
        SOURCE_DIRECTORY
        / "read_only_broker_gateway.py"
    )

    assert "def place" not in source
    assert "def cancel" not in source
    assert "def replace" not in source
    assert "def modify" not in source
    assert "def review" not in source


def test_commissioned_fingerprint_is_not_committed():
    config = load_config()
    broker = config.get("broker") or {}

    assert (
        broker.get("expected_account_fingerprint")
        is None
    )


def test_local_override_is_gitignored():
    ignored = read(REPOSITORY_ROOT / ".gitignore")

    assert "config/local.yaml" in ignored


def test_strategy_enabled_remains_false():
    assert (
        load_config()["strategy"]["enabled"]
        is False
    )


def test_risk_configuration_is_unchanged():
    assert load_config()["risk"] == {
        "risk_per_trade_usd": 15,
        "maximum_position_usd": 3000,
        "maximum_open_positions": 1,
        "maximum_entries_per_day": 2,
        "daily_loss_limit_usd": 45,
        "weekly_loss_limit_usd": 120,
        "total_drawdown_halt_usd": 300,
        "margin_allowed": False,
        "short_selling_allowed": False,
        "averaging_down_allowed": False,
    }
