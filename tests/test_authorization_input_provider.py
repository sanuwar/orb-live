from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.one_share_commissioning import (
    AuthorizationPhraseMismatchError, AuthorizationReaderError,
    CommissioningError, EASTERN, ENTRY_AUTHORIZATION_PHRASE,
)

NOW = datetime(2026, 7, 28, 15, 35, tzinfo=EASTERN)


def request(tmp_path, run_id="fake-authorization-provider"):
    return launcher.LiveRunRequest(
        run_id=run_id, evidence_root=tmp_path,
        execution_date=date(2026, 7, 28),
        attended_start=time(15, 30), entry_cutoff=time(15, 45),
        attended_end=time(15, 55), symbol="AAPL", quantity=Decimal("1"),
        planned_risk_usd=Decimal("15"), acknowledges_loss_may_exceed_plan=True,
        operator_present=True, robinhood_ui_open=True, no_second_trade=True,
        real_session_flag=launcher.REAL_SESSION_RUNTIME_FLAG,
        live_checkpoint_flag=launcher.LIVE_CHECKPOINT_AUTHORIZATION_FLAG,
    )


@pytest.mark.parametrize("supplied", [
    "incorrect", "", "   ", ENTRY_AUTHORIZATION_PHRASE.lower(),
    f" {ENTRY_AUTHORIZATION_PHRASE}", f"{ENTRY_AUTHORIZATION_PHRASE} ",
    "FAKE AUTHORIZATION ACCEPTED",
])
def test_authorization_phrase_requires_exact_case_sensitive_input(tmp_path, supplied):
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW,
        secret_reader=lambda _prompt: supplied,
    )
    with pytest.raises(AuthorizationPhraseMismatchError):
        provider("entry")


@pytest.mark.parametrize("error", [EOFError(), RuntimeError("private")])
def test_authorization_reader_failure_is_distinct_and_redacted(tmp_path, error):
    def fail(_prompt):
        raise error

    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW, secret_reader=fail,
    )
    with pytest.raises(AuthorizationReaderError) as caught:
        provider("entry")
    assert "private" not in str(caught.value)


def test_exact_authorization_is_single_use(tmp_path):
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW,
        secret_reader=lambda _prompt: ENTRY_AUTHORIZATION_PHRASE,
    )
    authorization = provider("entry")
    authorization.consume(
        now=NOW, expected_phrase=ENTRY_AUTHORIZATION_PHRASE,
        run_id="fake-authorization-provider",
    )
    with pytest.raises(CommissioningError):
        provider("entry")
    with pytest.raises(CommissioningError):
        authorization.consume(
            now=NOW, expected_phrase=ENTRY_AUTHORIZATION_PHRASE,
            run_id="fake-authorization-provider",
        )


def test_expired_authorization_is_rejected(tmp_path):
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW,
        secret_reader=lambda _prompt: ENTRY_AUTHORIZATION_PHRASE,
    )
    authorization = provider("entry")
    with pytest.raises(CommissioningError):
        authorization.consume(
            now=authorization.window_end + timedelta(microseconds=1),
            expected_phrase=ENTRY_AUTHORIZATION_PHRASE,
            run_id="fake-authorization-provider",
        )


def test_used_run_id_is_rejected_before_live_request(tmp_path):
    live_request = request(tmp_path, run_id="fake-run-id-reuse")
    live_request.evidence_root.joinpath("fake-run-id-reuse.partial").mkdir(parents=True)
    with pytest.raises(CommissioningError):
        launcher.validate_live_run_request(live_request, now=NOW)
