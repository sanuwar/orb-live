from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.one_share_commissioning import (
    AuthorizationDeclinedError,
    AuthorizationInterruptedError,
    CommissioningError,
    EASTERN,
    ENTRY_AUTHORIZATION_STAGE,
    FLATTEN_AUTHORIZATION_STAGE,
)

NOW = datetime(2026, 7, 29, 10, 40, tzinfo=EASTERN)


def request(tmp_path, run_id="fake-authorization-provider"):
    return launcher.LiveRunRequest(
        run_id=run_id,
        evidence_root=tmp_path,
        execution_date=date(2026, 7, 29),
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


@pytest.mark.parametrize("supplied", ["y", "Y"])
@pytest.mark.parametrize(
    "stage,prompt",
    [
        (ENTRY_AUTHORIZATION_STAGE, "Authorize this one-share entry? [y/N] "),
        (FLATTEN_AUTHORIZATION_STAGE, "Authorize controlled flatten? [y/N] "),
    ],
)
def test_explicit_y_approves_once_for_the_requested_stage(
    tmp_path, supplied, stage, prompt
):
    prompts = []
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path),
        now_eastern=lambda: NOW,
        secret_reader=lambda value: prompts.append(value) or supplied,
    )

    authorization = provider(stage)

    assert authorization.stage == stage
    assert prompts == [prompt]
    with pytest.raises(CommissioningError, match="not reusable"):
        provider(stage)
    assert prompts == [prompt]


@pytest.mark.parametrize(
    "supplied",
    ["", "n", "N", "arbitrary", "   ", " y", "y ", "yes"],
)
@pytest.mark.parametrize(
    "stage,category",
    [
        (ENTRY_AUTHORIZATION_STAGE, "ENTRY_AUTHORIZATION_DECLINED"),
        (FLATTEN_AUTHORIZATION_STAGE, "FLATTEN_AUTHORIZATION_DECLINED"),
    ],
)
def test_every_value_other_than_exact_y_declines_without_retry(
    tmp_path, supplied, stage, category
):
    prompts = []
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path),
        now_eastern=lambda: NOW,
        secret_reader=lambda prompt: prompts.append(prompt) or supplied,
    )

    with pytest.raises(AuthorizationDeclinedError) as caught:
        provider(stage)

    assert caught.value.category == category
    assert str(caught.value) == category
    with pytest.raises(CommissioningError, match="not reusable"):
        provider(stage)
    assert len(prompts) == 1


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt(), RuntimeError("private")])
@pytest.mark.parametrize(
    "stage,category",
    [
        (ENTRY_AUTHORIZATION_STAGE, "ENTRY_AUTHORIZATION_INTERRUPTED"),
        (FLATTEN_AUTHORIZATION_STAGE, "FLATTEN_AUTHORIZATION_INTERRUPTED"),
    ],
)
def test_reader_failure_and_ctrl_c_are_sanitized_and_single_use(
    tmp_path, error, stage, category
):
    prompts = []

    def fail(prompt):
        prompts.append(prompt)
        raise error

    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW, secret_reader=fail
    )
    with pytest.raises(AuthorizationInterruptedError) as caught:
        provider(stage)

    assert caught.value.category == category
    assert "private" not in str(caught.value)
    with pytest.raises(CommissioningError, match="not reusable"):
        provider(stage)
    assert len(prompts) == 1


def test_entry_and_flatten_approvals_are_separate_and_not_cross_usable(tmp_path):
    supplied = iter(["y", "Y"])
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path),
        now_eastern=lambda: NOW,
        secret_reader=lambda _prompt: next(supplied),
    )
    entry = provider(ENTRY_AUTHORIZATION_STAGE)
    flatten = provider(FLATTEN_AUTHORIZATION_STAGE)

    entry.consume(
        now=NOW,
        expected_stage=ENTRY_AUTHORIZATION_STAGE,
        run_id="fake-authorization-provider",
    )
    flatten.consume(
        now=NOW,
        expected_stage=FLATTEN_AUTHORIZATION_STAGE,
        run_id="fake-authorization-provider",
    )
    with pytest.raises(CommissioningError, match="not reusable"):
        entry.consume(
            now=NOW,
            expected_stage=FLATTEN_AUTHORIZATION_STAGE,
            run_id="fake-authorization-provider",
        )


def test_entry_approval_cannot_authorize_flatten(tmp_path):
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW, secret_reader=lambda _: "y"
    )
    entry = provider(ENTRY_AUTHORIZATION_STAGE)
    with pytest.raises(CommissioningError, match="scope mismatch"):
        entry.consume(
            now=NOW,
            expected_stage=FLATTEN_AUTHORIZATION_STAGE,
            run_id="fake-authorization-provider",
        )
    assert entry.used is True
    with pytest.raises(CommissioningError, match="not reusable"):
        entry.consume(
            now=NOW,
            expected_stage=ENTRY_AUTHORIZATION_STAGE,
            run_id="fake-authorization-provider",
        )


def test_flatten_approval_cannot_authorize_entry(tmp_path):
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW, secret_reader=lambda _: "Y"
    )
    flatten = provider(FLATTEN_AUTHORIZATION_STAGE)
    with pytest.raises(CommissioningError, match="scope mismatch"):
        flatten.consume(
            now=NOW,
            expected_stage=ENTRY_AUTHORIZATION_STAGE,
            run_id="fake-authorization-provider",
        )
    assert flatten.used is True
    with pytest.raises(CommissioningError, match="not reusable"):
        flatten.consume(
            now=NOW,
            expected_stage=FLATTEN_AUTHORIZATION_STAGE,
            run_id="fake-authorization-provider",
        )


def test_approval_is_scoped_to_one_run_id(tmp_path):
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW, secret_reader=lambda _: "y"
    )
    authorization = provider(ENTRY_AUTHORIZATION_STAGE)
    with pytest.raises(CommissioningError, match="scope mismatch"):
        authorization.consume(
            now=NOW,
            expected_stage=ENTRY_AUTHORIZATION_STAGE,
            run_id="different-run",
        )


def test_expired_authorization_is_rejected(tmp_path):
    provider = launcher.InteractiveAuthorizationProvider(
        request(tmp_path), now_eastern=lambda: NOW, secret_reader=lambda _: "y"
    )
    authorization = provider(ENTRY_AUTHORIZATION_STAGE)
    with pytest.raises(CommissioningError):
        authorization.consume(
            now=authorization.window_end + timedelta(microseconds=1),
            expected_stage=ENTRY_AUTHORIZATION_STAGE,
            run_id="fake-authorization-provider",
        )


def test_runtime_prompts_contain_only_the_reviewed_concise_questions():
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert launcher.ENTRY_AUTHORIZATION_PROMPT in source
    assert launcher.FLATTEN_AUTHORIZATION_PROMPT in source
    assert "exact single-use entry authorization" not in source
    assert "exact separate flatten authorization" not in source
    assert "compare_digest" not in source


def test_used_run_id_is_rejected_before_live_request(tmp_path):
    live_request = request(tmp_path, run_id="fake-run-id-reuse")
    live_request.evidence_root.joinpath("fake-run-id-reuse.partial").mkdir(
        parents=True
    )
    with pytest.raises(CommissioningError):
        launcher.validate_live_run_request(live_request, now=NOW)