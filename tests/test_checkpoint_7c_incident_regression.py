import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.one_share_commissioning import (
    AtomicEvidenceStore,
    AuthorizationInputError,
    CommissioningDiagnosticError,
    EASTERN,
    ENTRY_AUTHORIZATION_PHRASE,
    FLATTEN_AUTHORIZATION_PHRASE,
    State,
)
from src.one_share_commissioning_orchestrator import (
    OneShareCommissioningOrchestrator,
)
from src.robinhood_commissioning_adapter import AmbiguousWriteError
from src.robinhood_commissioning_session import (
    AccountIdentityMismatchError, AccountResolutionError,
    BrowserAuthenticationRequestError,
    CommissionedAccountResolution,
    RobinhoodCommissioningSessionBinding,
)
from tests.test_one_share_commissioning_orchestrator import (
    Clock,
    FakeAdapter,
)


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=EASTERN)
FIXTURE_ACCOUNT = "OFFLINE-COMMISSIONED-ACCOUNT"


def live_request(tmp_path, *, run_id="offline-incident-reproduction"):
    return launcher.LiveRunRequest(
        run_id=run_id,
        evidence_root=tmp_path,
        execution_date=date(2026, 7, 28),
        attended_start=time(11, 43),
        entry_cutoff=time(12, 5),
        attended_end=time(12, 20),
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


class CountingSessionFactory:
    def __init__(self, *, enter_error=None, close_error=None):
        self.enter_error = enter_error
        self.close_error = close_error
        self.created = 0
        self.entered = 0
        self.closed = 0

    @asynccontextmanager
    async def __call__(self):
        self.created += 1
        if self.enter_error is not None:
            raise self.enter_error
        self.entered += 1
        try:
            yield SimpleNamespace(offline_fixture=True)
        finally:
            self.closed += 1
            if self.close_error is not None:
                raise self.close_error


def actual_binding(
    tmp_path,
    *,
    secret_reader,
    run_id="offline-incident-reproduction",
    session_factory=None,
):
    request = live_request(tmp_path, run_id=run_id)
    provider = launcher.InteractiveAuthorizationProvider(
        request,
        now_eastern=lambda: NOW,
        secret_reader=secret_reader,
    )
    factory = session_factory or CountingSessionFactory()
    counts = {"resolver": 0, "adapter": 0, "orchestrator": 0}

    def resolver(_session):
        counts["resolver"] += 1
        return CommissionedAccountResolution(FIXTURE_ACCOUNT, {})

    def adapter_factory(_session, _account):
        counts["adapter"] += 1
        return FakeAdapter()

    def orchestrator_factory(**kwargs):
        counts["orchestrator"] += 1
        return OneShareCommissioningOrchestrator(**kwargs)

    store = AtomicEvidenceStore(tmp_path, run_id)
    binding = RobinhoodCommissioningSessionBinding(
        run_id=run_id,
        session_factory=factory,
        commissioned_account_resolver=resolver,
        monotonic=Clock().monotonic,
        sleep=Clock().sleep,
        now_eastern=lambda: NOW,
        authorization_provider=provider,
        evidence_store=store,
        emergency_handoff=lambda payload: None,
        adapter_factory=adapter_factory,
        orchestrator_factory=orchestrator_factory,
    )
    return binding, factory, counts, store


def read_sanitized(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_exact_incident_regression_is_authorization_input_before_write(tmp_path):
    binding, factory, counts, store = actual_binding(
        tmp_path,
        secret_reader=lambda prompt: "REJECTED-OFFLINE-INPUT",
    )
    result = asyncio.run(binding.run())
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")
    assert result.state is State.FAILED_CLOSED
    assert evidence["state_transitions"] == [
        "PREFLIGHT", "ENTRY_REVIEWED", "FAILED_CLOSED"
    ]
    assert evidence["failure_stage"] == "ENTRY_AUTHORIZATION_INPUT"
    assert evidence["failure_category"] == "AUTHORIZATION_INPUT_FAILED"
    assert evidence["write_boundary_reached"] is False
    assert evidence["write_attempt_counters"] == {
        "entry_place": 0,
        "stop_place": 0,
        "stop_cancel": 0,
        "exit_place": 0,
    }
    assert evidence["call_counts"]["review_equity_order"] == 1
    assert evidence["call_counts"].get("place_equity_order", 0) == 0
    assert factory.created == 1 and factory.entered == 1 and factory.closed == 1
    assert counts == {"resolver": 1, "adapter": 1, "orchestrator": 1}


def test_faithful_offline_binding_reproduction_completes_after_repair(tmp_path):
    supplied = iter([
        ENTRY_AUTHORIZATION_PHRASE,
        FLATTEN_AUTHORIZATION_PHRASE,
    ])
    binding, factory, counts, store = actual_binding(
        tmp_path,
        secret_reader=lambda prompt: next(supplied),
    )
    result = asyncio.run(binding.run())
    evidence = read_sanitized(result.evidence_path)
    assert result.state is State.COMPLETE
    assert evidence["boolean_validations"]["verified_flat"] is True
    assert evidence["boolean_validations"]["no_related_nonterminal_orders"] is True
    assert evidence["call_counts"]["place_equity_order"] == 3
    assert evidence["call_counts"]["cancel_equity_order"] == 1
    assert factory.created == 1 and factory.entered == 1 and factory.closed == 1
    assert counts == {"resolver": 1, "adapter": 1, "orchestrator": 1}


def test_authorization_window_is_narrow_and_cutoff_bounded(tmp_path):
    provider = launcher.InteractiveAuthorizationProvider(
        live_request(tmp_path),
        now_eastern=lambda: NOW,
        secret_reader=lambda prompt: ENTRY_AUTHORIZATION_PHRASE,
    )
    authorization = provider("entry")
    seconds = (
        authorization.window_end - authorization.window_start
    ).total_seconds()
    assert 0 < seconds <= 15 * 60
    assert authorization.window_end <= datetime(
        2026, 7, 28, 12, 5, tzinfo=EASTERN
    )


def test_raw_authorization_exception_is_sanitized(tmp_path):
    private_text = "private authorization input details"

    def fail(_prompt):
        raise RuntimeError(private_text)

    binding, _, _, store = actual_binding(tmp_path, secret_reader=fail)
    result = asyncio.run(binding.run())
    rendered = (store.partial / "commissioning_evidence.json").read_text(
        encoding="utf-8"
    )
    assert result.state is State.FAILED_CLOSED
    assert private_text not in rendered
    assert FIXTURE_ACCOUNT not in rendered
    assert ENTRY_AUTHORIZATION_PHRASE not in rendered
    assert FLATTEN_AUTHORIZATION_PHRASE not in rendered
    assert "AUTHORIZATION_INPUT_FAILED" in rendered


class ReviewFailureAdapter(FakeAdapter):
    async def review_equity_order(self, arguments, *, role):
        raise RuntimeError("private review failure")


class BeforeReviewFailureAdapter(FakeAdapter):
    @property
    def commissioned_account_number(self):
        raise RuntimeError("private pre-review failure")


def test_failure_before_entry_review_is_classified_without_write(tmp_path):
    clock = Clock()
    store = AtomicEvidenceStore(tmp_path, "review-failure")
    orchestrator = OneShareCommissioningOrchestrator(
        run_id="review-failure",
        adapter=BeforeReviewFailureAdapter(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now_eastern=lambda: NOW,
        authorization_provider=lambda stage: None,
        evidence_store=store,
        emergency_handoff=lambda payload: None,
        strategy_enabled=False,
        regular_market_hours=True,
    )
    result = asyncio.run(orchestrator.run())
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")
    assert result.state is State.FAILED_CLOSED
    assert evidence["failure_category"] == "ENTRY_REVIEW_FAILED_BEFORE_WRITE"
    assert evidence["write_boundary_reached"] is False
    assert evidence["call_counts"].get("place_equity_order", 0) == 0


def test_failure_during_entry_review_is_classified_without_write(tmp_path):
    clock = Clock()
    store = AtomicEvidenceStore(tmp_path, "review-call-failure")
    orchestrator = OneShareCommissioningOrchestrator(
        run_id="review-call-failure",
        adapter=ReviewFailureAdapter(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now_eastern=lambda: NOW,
        authorization_provider=lambda stage: None,
        evidence_store=store,
        emergency_handoff=lambda payload: None,
        strategy_enabled=False,
        regular_market_hours=True,
    )
    result = asyncio.run(orchestrator.run())
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")
    assert result.state is State.FAILED_CLOSED
    assert evidence["failure_category"] == "ENTRY_REVIEW_FAILED_BEFORE_WRITE"
    assert evidence["write_boundary_reached"] is False


def test_ambiguous_entry_is_one_write_and_operator_required(tmp_path):
    supplied = iter([
        ENTRY_AUTHORIZATION_PHRASE,
        FLATTEN_AUTHORIZATION_PHRASE,
    ])
    request = live_request(tmp_path, run_id="ambiguous-entry")
    provider = launcher.InteractiveAuthorizationProvider(
        request,
        now_eastern=lambda: NOW,
        secret_reader=lambda prompt: next(supplied),
    )
    clock = Clock()
    store = AtomicEvidenceStore(tmp_path, request.run_id)
    orchestrator = OneShareCommissioningOrchestrator(
        run_id=request.run_id,
        adapter=FakeAdapter(ambiguous_role="entry"),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now_eastern=lambda: NOW,
        authorization_provider=provider,
        evidence_store=store,
        emergency_handoff=lambda payload: None,
        strategy_enabled=False,
        regular_market_hours=True,
    )
    result = asyncio.run(orchestrator.run())
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")
    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["failure_category"] == "ENTRY_PLACE_AMBIGUOUS"
    assert evidence["write_boundary_reached"] is True
    assert evidence["write_attempt_counters"]["entry_place"] == 1
    assert evidence["call_counts"]["place_equity_order"] == 1
    rendered = json.dumps(evidence)
    assert "entry-1" not in rendered
    assert FIXTURE_ACCOUNT not in rendered


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("session", "SESSION_INITIALIZATION_FAILED"),
        ("silent", "SILENT_AUTH_FAILED"),
        ("account", "ACCOUNT_RESOLUTION_FAILED"),
        ("identity", "ACCOUNT_IDENTITY_MISMATCH"),
        ("adapter", "ADAPTER_INITIALIZATION_FAILED"),
        ("orchestrator", "ORCHESTRATOR_INITIALIZATION_FAILED"),
    ],
)
def test_pre_orchestrator_failures_are_typed_and_close_sessions(
    tmp_path, stage, expected
):
    factory = CountingSessionFactory(
        enter_error=(
            BrowserAuthenticationRequestError("private")
            if stage == "silent"
            else RuntimeError("private")
            if stage == "session"
            else None
        )
    )
    store = AtomicEvidenceStore(tmp_path, f"failure-{stage}")

    def resolver(_session):
        if stage == "account":
            raise AccountResolutionError("private")
        if stage == "identity":
            raise AccountIdentityMismatchError("private")
        return CommissionedAccountResolution(FIXTURE_ACCOUNT, {})

    def adapter_factory(_session, _account):
        if stage == "adapter":
            raise RuntimeError("private")
        return SimpleNamespace()

    def orchestrator_factory(**_kwargs):
        if stage == "orchestrator":
            raise RuntimeError("private")
        return SimpleNamespace(run=lambda: None)

    binding = RobinhoodCommissioningSessionBinding(
        run_id=f"failure-{stage}",
        session_factory=(
            (lambda: (_ for _ in ()).throw(RuntimeError("private")))
            if stage == "session"
            else factory
        ),
        commissioned_account_resolver=resolver,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
        now_eastern=lambda: NOW,
        authorization_provider=lambda value: None,
        evidence_store=store,
        emergency_handoff=lambda payload: None,
        adapter_factory=adapter_factory,
        orchestrator_factory=orchestrator_factory,
    )
    with pytest.raises(CommissioningDiagnosticError) as caught:
        asyncio.run(binding.run())
    assert caught.value.category == expected
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")
    assert evidence["failure_category"] == expected
    assert evidence["write_boundary_reached"] is False
    assert FIXTURE_ACCOUNT not in json.dumps(evidence)
    if stage not in {"session", "silent"}:
        assert factory.closed == 1


def test_session_close_failure_is_sanitized(tmp_path):
    factory = CountingSessionFactory(close_error=RuntimeError("private close"))
    store = AtomicEvidenceStore(tmp_path, "close-failure")

    class Orchestrator:
        async def run(self):
            return SimpleNamespace(state=State.FAILED_CLOSED)

    binding = RobinhoodCommissioningSessionBinding(
        run_id="close-failure",
        session_factory=factory,
        commissioned_account_resolver=lambda session: CommissionedAccountResolution(
            FIXTURE_ACCOUNT, {}
        ),
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
        now_eastern=lambda: NOW,
        authorization_provider=lambda value: None,
        evidence_store=store,
        emergency_handoff=lambda payload: None,
        adapter_factory=lambda session, account: SimpleNamespace(),
        orchestrator_factory=lambda **kwargs: Orchestrator(),
    )
    with pytest.raises(CommissioningDiagnosticError) as caught:
        asyncio.run(binding.run())
    assert caught.value.category == "SESSION_CLOSE_FAILED"
    assert "private close" not in str(caught.value)
    assert factory.closed == 1
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")
    assert evidence["failure_category"] == "SESSION_CLOSE_FAILED"
    assert evidence["write_boundary_reached"] is False


def test_retired_failed_run_id_is_never_reusable(tmp_path):
    request = live_request(
        tmp_path,
        run_id="cp7c-live-aapl-one-share-20260728-05",
    )
    with pytest.raises(Exception, match="permanently retired"):
        launcher.validate_live_run_request(request, now=NOW)
