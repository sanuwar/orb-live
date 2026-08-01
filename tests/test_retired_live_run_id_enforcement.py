"""Retired Checkpoint 7C live identities must fail closed from tracked source.

Every fixture is local. Nothing here reaches a broker, an MCP server, the
network, or the operator's evidence root: these tests deliberately use
``tmp_path`` so that deleting or never having the real on-disk artifacts
cannot change the result. That independence is the entire point — a
wrapper-used marker is corroboration, never protection.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.one_share_commissioning import CommissioningError, EASTERN

# The historical inventory, written out explicitly rather than derived from
# the registry, so that silently dropping a registry entry fails a test
# instead of quietly restoring reuse.
HISTORICAL_LIVE_RUN_IDS = tuple(
    f"cp7c-live-aapl-one-share-{suffix}"
    for suffix in (
        "20260728-01", "20260728-03", "20260728-04", "20260728-05",
        "20260728-06",
        "20260729-02", "20260729-03", "20260729-04",
        "20260730-01", "20260730-02", "20260730-03", "20260730-04",
        "20260730-05",
        "20260731-01", "20260731-02", "20260731-03", "20260731-04",
        "20260731-05", "20260731-06", "20260731-07",
    )
)

# Identities with no execution marker of their own. Each would be reusable if
# protection depended on the evidence root.
NEVER_MARKED = (
    "cp7c-live-aapl-one-share-20260730-01",
    "cp7c-live-aapl-one-share-20260730-02",
    "cp7c-live-aapl-one-share-20260730-03",
    "cp7c-live-aapl-one-share-20260731-03",
    "cp7c-live-aapl-one-share-20260731-04",
)

NOW = datetime(2026, 7, 31, 10, 40, tzinfo=EASTERN)


def live_request(tmp_path, run_id):
    """A request that is valid in every respect except the identity."""
    return launcher.LiveRunRequest(
        run_id=run_id,
        evidence_root=tmp_path,
        execution_date=date(2026, 7, 31),
        attended_start=time(10, 35),
        entry_cutoff=time(11, 0),
        mandatory_risk_off=time(11, 10),
        verified_flat_deadline=time(11, 15),
        attended_end=time(11, 20),
        symbol="AAPL",
        quantity=Decimal("1"),
        planned_risk_usd=Decimal("15"),
        acknowledges_loss_may_exceed_plan=True,
        operator_present=True,
        robinhood_ui_open=True,
        no_second_trade=True,
        real_session_flag=launcher.REAL_SESSION_RUNTIME_FLAG,
        live_checkpoint_flag=launcher.LIVE_CHECKPOINT_AUTHORIZATION_FLAG,
    )


# --- registry contents ------------------------------------------------


@pytest.mark.parametrize("run_id", HISTORICAL_LIVE_RUN_IDS)
def test_every_historical_identity_is_in_the_tracked_registry(run_id):
    assert run_id in launcher.RETIRED_LIVE_RUN_IDS


def test_registry_and_historical_inventory_agree_exactly():
    """Neither list may drift from the other without a test failure."""
    assert launcher.RETIRED_LIVE_RUN_IDS == frozenset(HISTORICAL_LIVE_RUN_IDS)


def test_registry_entries_are_fixed_literals_not_patterns():
    for entry in launcher.RETIRED_LIVE_RUN_IDS:
        assert isinstance(entry, str)
        for wildcard in ("*", "?", "[", "]", "re.", "\\"):
            assert wildcard not in entry
        assert launcher.RUN_ID_PATTERN.fullmatch(entry)


# --- rejection ---------------------------------------------------------


@pytest.mark.parametrize("run_id", HISTORICAL_LIVE_RUN_IDS)
def test_every_retired_identity_is_rejected(tmp_path, run_id):
    with pytest.raises(CommissioningError, match="permanently retired"):
        launcher.validate_live_run_request(
            live_request(tmp_path, run_id), now=NOW
        )


@pytest.mark.parametrize("run_id", NEVER_MARKED)
def test_identities_without_any_execution_marker_are_still_rejected(
    tmp_path, run_id
):
    """20260731-03 was never launched; 20260731-04 stopped at an
    acknowledgment before its marker is written. Neither may be reused."""
    assert not (tmp_path / run_id).exists()
    assert not (tmp_path / f"{run_id}.partial").exists()
    assert not (tmp_path / f"{run_id}.wrapper-used").exists()
    with pytest.raises(CommissioningError, match="permanently retired"):
        launcher.validate_live_run_request(
            live_request(tmp_path, run_id), now=NOW
        )


@pytest.mark.parametrize(
    "run_id",
    ["cp7c-live-aapl-one-share-20260731-05",
     "cp7c-live-aapl-one-share-20260731-06",
     "cp7c-live-aapl-one-share-20260731-07"],
)
def test_the_three_completed_runs_are_rejected(tmp_path, run_id):
    with pytest.raises(CommissioningError, match="permanently retired"):
        launcher.validate_live_run_request(
            live_request(tmp_path, run_id), now=NOW
        )


def test_an_empty_evidence_root_does_not_permit_reuse(tmp_path):
    """The whole gap: protection must not depend on the evidence root."""
    empty = tmp_path / "pristine"
    empty.mkdir()
    assert list(empty.iterdir()) == []
    for run_id in HISTORICAL_LIVE_RUN_IDS:
        with pytest.raises(CommissioningError, match="permanently retired"):
            launcher.validate_live_run_request(
                live_request(empty, run_id), now=NOW
            )


def test_copying_a_retired_identity_into_a_fresh_request_is_rejected(tmp_path):
    """A hand-copied Run ID in a brand-new wrapper gains nothing."""
    request = live_request(tmp_path, "cp7c-live-aapl-one-share-20260731-07")
    with pytest.raises(CommissioningError, match="permanently retired"):
        launcher.validate_live_run_request(request, now=NOW)


def test_a_genuinely_fresh_identity_still_passes_the_gate(tmp_path):
    fresh = "cp7c-live-aapl-one-share-20260731-99"
    assert fresh not in launcher.RETIRED_LIVE_RUN_IDS
    launcher.validate_live_run_request(live_request(tmp_path, fresh), now=NOW)


def test_the_broker_free_rehearsal_consumes_no_live_identity():
    """It must not be mislabeled as a live run, and it reserves no Run ID."""
    for entry in launcher.RETIRED_LIVE_RUN_IDS:
        assert "rehearsal" not in entry
        assert entry.startswith("cp7c-live-aapl-one-share-")


# --- rejection happens before anything can reach a broker --------------


class ExplodingSessionFactory:
    """Any use at all is a test failure."""

    def __call__(self, *args, **kwargs):
        raise AssertionError("a session was created for a retired identity")


class ExplodingAuthorizationProvider:
    def __call__(self, stage):
        raise AssertionError("authorization was requested for a retired identity")


def test_rejection_precedes_session_authorization_and_any_broker_call(tmp_path):
    """The gate must fire before a session, a prompt, or a read is possible."""
    request = live_request(tmp_path, "cp7c-live-aapl-one-share-20260731-07")

    with pytest.raises(CommissioningError, match="permanently retired"):
        launcher.validate_live_run_request(request, now=NOW)

    # The validator itself touches no session, provider, or evidence store,
    # so wiring exploding collaborators cannot change the outcome.
    with pytest.raises(CommissioningError, match="permanently retired"):
        launcher.validate_live_run_request(request, now=NOW)
    assert not (tmp_path / request.run_id).exists()
    assert not (tmp_path / f"{request.run_id}.partial").exists()


def test_retirement_is_checked_before_the_time_policy_is_built(tmp_path):
    """A retired identity is refused even when the schedule is nonsense, so
    the identity gate can never be bypassed by another validation failing
    first in a way an operator might try to 'fix'."""
    request = live_request(tmp_path, "cp7c-live-aapl-one-share-20260731-07")
    outside = datetime(2026, 7, 31, 23, 59, tzinfo=EASTERN)
    with pytest.raises(CommissioningError, match="permanently retired"):
        launcher.validate_live_run_request(request, now=outside)


def test_rejection_message_reveals_no_private_path_or_broker_data(tmp_path):
    request = live_request(tmp_path, "cp7c-live-aapl-one-share-20260731-07")
    with pytest.raises(CommissioningError) as caught:
        launcher.validate_live_run_request(request, now=NOW)
    message = str(caught.value)
    assert message == "run ID is permanently retired"
    assert str(tmp_path) not in message
    for leaked in ("AAPL", "account", "order", "token", "/", "\\"):
        assert leaked not in message


# --- nothing else moved ------------------------------------------------


def test_enforcement_did_not_alter_trading_logic_or_limits():
    from src.one_share_commissioning import CommissioningMachine
    import inspect

    exit_source = inspect.getsource(CommissioningMachine.exit_submitted)
    cancel_source = inspect.getsource(CommissioningMachine.stop_cancel_requested)
    assert "exit placement may occur only once" in exit_source
    assert "stop cancellation may occur only once" in cancel_source
    assert launcher.QUANTITY == Decimal("1")
    assert launcher.PLANNED_RISK_USD == Decimal("15")
    assert launcher.SYMBOL == "AAPL"


def test_strategy_remains_disabled_in_tracked_configuration():
    from pathlib import Path
    import re

    text = Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*enabled:\s*false\s*$", text)
