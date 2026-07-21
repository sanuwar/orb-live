from __future__ import annotations

import pytest

from tests.shadow_import_guard import (
    FORBIDDEN_IMPORT_NAMES,
    forbidden_imports_in_module,
    forbidden_imports_in_source,
    milestone_6_module_paths,
)


def test_milestone_6_modules_exist():
    paths = milestone_6_module_paths()
    assert len(paths) == 2
    for path in paths:
        assert path.exists(), f"expected {path} to exist"


@pytest.mark.parametrize("path", milestone_6_module_paths())
def test_milestone_6_module_has_no_forbidden_import(path):
    offending = forbidden_imports_in_module(path)
    assert offending == set(), (
        f"{path.name} imports forbidden modules: {sorted(offending)}"
    )


def test_guard_detects_direct_import():
    source = "import trade_state\n"
    assert forbidden_imports_in_source(source) == {"trade_state"}


def test_guard_detects_src_prefixed_import():
    source = "from src.shadow_runtime import ShadowRuntime\n"
    assert forbidden_imports_in_source(source) == {"shadow_runtime"}


def test_guard_detects_relative_import():
    source = "from .broker_action_saga import generate_ref_id\n"
    assert forbidden_imports_in_source(source) == {"broker_action_saga"}


def test_guard_detects_try_except_fallback_form():
    source = (
        "try:\n"
        "    from .operator_resolution import Resolver\n"
        "except ImportError:\n"
        "    from operator_resolution import Resolver\n"
    )
    assert forbidden_imports_in_source(source) == {"operator_resolution"}


def test_guard_detects_from_package_import_of_forbidden_name():
    source = "from src import broker_saga_executor\n"
    assert forbidden_imports_in_source(source) == {
        "broker_saga_executor"
    }


def test_guard_allows_permitted_reuse_imports():
    source = (
        "from .market_data import MinuteBar\n"
        "from .signal_engine import SignalEngine\n"
        "from .risk_engine import RiskEngine\n"
        "from .broker_snapshot import validate_snapshot\n"
        "from .broker_identity import CommissionedAccount\n"
        "from .live_scanner import latest_completed_bar\n"
        "from .shadow_campaign_store import ShadowCampaignStore\n"
    )
    assert forbidden_imports_in_source(source) == set()


def test_every_forbidden_name_is_detectable():
    for name in FORBIDDEN_IMPORT_NAMES:
        source = f"import {name}\n"
        assert forbidden_imports_in_source(source) == {name}
