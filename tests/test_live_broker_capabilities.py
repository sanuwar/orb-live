"""
Offline tests for the grounded live broker capability model.

No broker, MCP, network, credential, token, or database access occurs.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.live_broker_capabilities import (
    APPROVED_PROTECTION_INVARIANT,
    AtomicStopReplacementUnsupportedError,
    BracketOrderUnsupportedError,
    CapabilityEvidence,
    EQUITY_ORDER_CAPABILITIES,
    EQUITY_ORDER_CAPABILITY_EVIDENCE,
    LiveBrokerCapabilities,
    LookupByRefUnsupportedError,
    OcoUnsupportedError,
    OrderReplacementUnsupportedError,
    ProtectionGapReason,
    UnsupportedCapabilityError,
    assess_absolute_protection_invariant,
    capability_evidence,
    unverified_capabilities,
)


REPOSITORY_ROOT = Path(__file__).parent.parent
NEW_MODULES = (
    "live_broker_capabilities.py",
    "live_order_contract.py",
    "same_ref_payload_guard.py",
)


# ---------- every capability flag ----------

SUPPORTED_FLAGS = (
    "supports_non_committing_review",
    "supports_submit_with_ref_id",
    "supports_cancel_by_order_id",
    "cancel_is_asynchronous",
    "supports_order_status_read",
    "supports_position_snapshot",
)

UNSUPPORTED_FLAGS = (
    "supports_lookup_by_ref",
    "supports_replace",
    "supports_bracket_order",
    "supports_oco",
    "supports_attached_protective_stop",
)


@pytest.mark.parametrize("flag", SUPPORTED_FLAGS)
def test_supported_flags_are_true(flag):
    assert getattr(EQUITY_ORDER_CAPABILITIES, flag) is True
    assert EQUITY_ORDER_CAPABILITIES.supports(flag) is True


@pytest.mark.parametrize("flag", UNSUPPORTED_FLAGS)
def test_unsupported_flags_are_false(flag):
    assert getattr(EQUITY_ORDER_CAPABILITIES, flag) is False
    assert EQUITY_ORDER_CAPABILITIES.supports(flag) is False


def test_capability_mapping_covers_every_flag():
    mapping = EQUITY_ORDER_CAPABILITIES.as_mapping()
    assert set(mapping) == set(SUPPORTED_FLAGS) | set(UNSUPPORTED_FLAGS)
    assert len(mapping) == 11


def test_capabilities_are_immutable():
    with pytest.raises(Exception):
        EQUITY_ORDER_CAPABILITIES.supports_replace = True  # type: ignore


def test_unknown_capability_is_not_supported():
    assert EQUITY_ORDER_CAPABILITIES.supports("supports_teleportation") is False


# ---------- unsupported-operation failures ----------

@pytest.mark.parametrize(
    "flag, error",
    [
        ("supports_lookup_by_ref", LookupByRefUnsupportedError),
        ("supports_replace", OrderReplacementUnsupportedError),
        ("supports_bracket_order", BracketOrderUnsupportedError),
        ("supports_oco", OcoUnsupportedError),
        (
            "supports_attached_protective_stop",
            AtomicStopReplacementUnsupportedError,
        ),
    ],
)
def test_require_raises_specific_error(flag, error):
    with pytest.raises(error):
        EQUITY_ORDER_CAPABILITIES.require(flag)
    # Every specific error is also a general unsupported-capability error.
    assert issubclass(error, UnsupportedCapabilityError)


def test_require_unknown_capability_fails_closed():
    with pytest.raises(UnsupportedCapabilityError):
        EQUITY_ORDER_CAPABILITIES.require("supports_anything_else")


@pytest.mark.parametrize("flag", SUPPORTED_FLAGS)
def test_require_supported_capability_passes(flag):
    EQUITY_ORDER_CAPABILITIES.require(flag)


def test_lookup_by_ref_is_never_emulated():
    """No helper may offer a ref_id lookup by any name."""
    source = (
        REPOSITORY_ROOT / "src" / "live_broker_capabilities.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name.lower()
            assert "lookup_order_by_ref" not in name
            assert not (
                name.startswith("lookup") and "ref" in name
            ), f"{node.name} looks like a ref lookup implementation"


# ---------- evidence separation ----------

def test_every_flag_has_recorded_evidence():
    recorded = {fact.name for fact in EQUITY_ORDER_CAPABILITY_EVIDENCE}
    assert recorded == set(EQUITY_ORDER_CAPABILITIES.as_mapping())


def test_evidence_matches_flag_values():
    for fact in EQUITY_ORDER_CAPABILITY_EVIDENCE:
        assert (
            EQUITY_ORDER_CAPABILITIES.supports(fact.name) is fact.supported
        )


def test_evidence_classes_are_distinct():
    assert CapabilityEvidence.SCHEMA_ADVERTISED.value == "schema_advertised"
    assert CapabilityEvidence.LIVE_OBSERVED.value == "live_observed"
    assert CapabilityEvidence.UNVERIFIED.value == "unverified"


def test_ref_id_dedup_is_advertised_not_verified():
    """The advertised idempotency must never be labelled verified."""
    fact = capability_evidence("supports_submit_with_ref_id")
    assert fact.supported is True
    assert fact.evidence is CapabilityEvidence.SCHEMA_ADVERTISED
    assert fact.is_verified_live is False
    assert "not been verified" in fact.note


def test_unverified_capabilities_are_reported():
    unverified = unverified_capabilities()
    assert "supports_submit_with_ref_id" in unverified
    assert "supports_cancel_by_order_id" in unverified
    assert "supports_position_snapshot" in unverified
    # Directly observed capabilities are not listed as unverified.
    assert "supports_order_status_read" not in unverified
    assert "supports_non_committing_review" not in unverified


def test_live_observed_capabilities_are_marked():
    assert capability_evidence(
        "supports_order_status_read"
    ).is_verified_live is True
    assert capability_evidence(
        "supports_non_committing_review"
    ).is_verified_live is True


def test_missing_evidence_fails_closed():
    with pytest.raises(UnsupportedCapabilityError):
        capability_evidence("supports_nonexistent")


def test_lookup_by_ref_evidence_is_honest_about_sampling():
    fact = capability_evidence("supports_lookup_by_ref")
    assert fact.supported is False
    assert "not proof" in fact.note.lower()


# ---------- absolute protection invariant ----------

def test_invariant_text_is_verbatim_and_absolute():
    assert APPROVED_PROTECTION_INVARIANT == (
        "No filled position may exist without verified protective-stop "
        "coverage for its exact filled quantity."
    )


def test_absolute_invariant_is_unsatisfiable_on_grounded_surface():
    assessment = assess_absolute_protection_invariant()
    assert assessment.technically_satisfiable is False
    assert assessment.invariant == APPROVED_PROTECTION_INVARIANT
    assert set(assessment.reason_codes) == {
        "ENTRY_TO_STOP_GAP",
        "CANCEL_THEN_EXIT_GAP",
        "EXIT_WITH_STOP_DOUBLE_SELL_RISK",
        "STOP_REPLACEMENT_GAP",
    }


def test_entry_to_stop_gap_requires_attached_or_bracket():
    assessment = assess_absolute_protection_invariant(
        LiveBrokerCapabilities(supports_attached_protective_stop=True)
    )
    assert ProtectionGapReason.ENTRY_TO_STOP_GAP not in assessment.reasons

    assessment = assess_absolute_protection_invariant(
        LiveBrokerCapabilities(supports_bracket_order=True)
    )
    assert ProtectionGapReason.ENTRY_TO_STOP_GAP not in assessment.reasons


def test_oco_removes_exit_gaps():
    assessment = assess_absolute_protection_invariant(
        LiveBrokerCapabilities(supports_oco=True)
    )
    assert ProtectionGapReason.CANCEL_THEN_EXIT_GAP not in assessment.reasons
    assert (
        ProtectionGapReason.EXIT_WITH_STOP_DOUBLE_SELL_RISK
        not in assessment.reasons
    )


def test_replace_removes_stop_replacement_gap():
    assessment = assess_absolute_protection_invariant(
        LiveBrokerCapabilities(supports_replace=True)
    )
    assert (
        ProtectionGapReason.STOP_REPLACEMENT_GAP not in assessment.reasons
    )


def test_fully_capable_surface_satisfies_invariant():
    assessment = assess_absolute_protection_invariant(
        LiveBrokerCapabilities(
            supports_attached_protective_stop=True,
            supports_bracket_order=True,
            supports_oco=True,
            supports_replace=True,
        )
    )
    assert assessment.technically_satisfiable is True
    assert assessment.reasons == ()


def test_assessment_implements_no_emergency_behaviour():
    source = (
        REPOSITORY_ROOT / "src" / "live_broker_capabilities.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("flatten", "emergency_exit", "market_sell", "liquidate"):
        assert forbidden not in source


# ---------- no live reachability ----------

@pytest.mark.parametrize("module_name", NEW_MODULES)
def test_new_modules_have_no_broker_reachability(module_name):
    source = (REPOSITORY_ROOT / "src" / module_name).read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "call_tool",
        "RobinhoodClient",
        "robinhood_client",
        "streamablehttp_client",
        "ClientSession",
        "httpx",
        "webbrowser",
        "place_equity_order(",
        "cancel_equity_order(",
        "review_equity_order(",
    ):
        assert forbidden not in source, f"{module_name} references {forbidden}"


@pytest.mark.parametrize("module_name", NEW_MODULES)
def test_new_modules_touch_no_credentials_or_databases(module_name):
    source = (REPOSITORY_ROOT / "src" / module_name).read_text(
        encoding="utf-8"
    )
    for forbidden in (
        ".rh_tokens.json",
        "sqlite3",
        "DEFAULT_DB_PATH",
        "local.yaml",
        "socket.socket",
        "urllib.request",
    ):
        assert forbidden not in source


@pytest.mark.parametrize("module_name", NEW_MODULES)
def test_new_modules_declare_no_async_entry_points(module_name):
    """No coroutine can be awaited into a broker call from these modules."""
    tree = ast.parse(
        (REPOSITORY_ROOT / "src" / module_name).read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        assert not isinstance(node, ast.AsyncFunctionDef)
        assert not isinstance(node, ast.Await)


@pytest.mark.parametrize("module_name", NEW_MODULES)
def test_new_modules_contain_no_retry_behaviour(module_name):
    source = (REPOSITORY_ROOT / "src" / module_name).read_text(
        encoding="utf-8"
    ).lower()
    for forbidden in ("time.sleep", "asyncio.sleep", "def retry", "max_retries"):
        assert forbidden not in source


# ---------- the simulated-only executor guard is untouched ----------

def test_saga_executor_simulated_only_guard_is_intact():
    from src.broker_saga_executor import SagaExecutor

    source = (
        REPOSITORY_ROOT / "src" / "broker_saga_executor.py"
    ).read_text(encoding="utf-8")
    assert "_validate_transport" in source
    assert "simulated_only" in source
    assert hasattr(SagaExecutor, "_validate_transport")


def test_capability_model_does_not_bind_the_executor():
    """The new modules must not be referenced by the executor."""
    source = (
        REPOSITORY_ROOT / "src" / "broker_saga_executor.py"
    ).read_text(encoding="utf-8")
    for module_name in NEW_MODULES:
        assert module_name.replace(".py", "") not in source


def test_executor_still_refuses_a_non_simulated_transport():
    from src.broker_saga_executor import SagaExecutor, SagaExecutorError

    class NotSimulated:
        simulated_only = False

    with pytest.raises(SagaExecutorError):
        SagaExecutor._validate_transport(NotSimulated())


# ---------- configuration is unchanged ----------

def test_strategy_enabled_remains_false():
    from src.config import load_config

    assert load_config()["strategy"]["enabled"] is False


def test_risk_configuration_is_unchanged():
    from src.config import load_config

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
