from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_one_share_commissioning_orchestrator import TIME_POLICY

from src.broker_snapshot import account_fingerprint
from src.execution_safety_governance import (
    CommissioningGovernanceEvidence, CommissioningMode, OperatorAcknowledgements,
)
from src.one_share_commissioning import CommissioningError, State
from src.one_share_commissioning import CommissioningDiagnosticError
from src.robinhood_commissioning_session import (
    BrowserAuthenticationRequestError,
    CommissionedAccountResolution,
    CommissionedAccountResolver,
    CommissioningSessionBindingError,
    RobinhoodCommissioningSessionBinding,
    silent_robinhood_session_factory,
)


ACCOUNT = "OFFLINE-FIXTURE-ACCOUNT"
OTHER = "OFFLINE-FIXTURE-OTHER"
GOVERNANCE_EVIDENCE = CommissioningGovernanceEvidence(
    commissioning_mode=CommissioningMode.ATTENDED,
    operator_attended=True,
    operator_acknowledgements=OperatorAcknowledgements(
        acknowledges_loss_may_exceed_plan=True, operator_present=True,
        robinhood_ui_open=True, no_second_trade=True,
    ),
    source_hash_verified=True,
    release_sha="a" * 40,
    implementation_digest="b" * 64,
)


def account(number=ACCOUNT, **changes):
    value = {
        "account_number": number,
        "agentic_allowed": True,
        "state": "active",
        "deactivated": False,
        "permanently_deactivated": False,
    }
    value.update(changes)
    return value


class Session:
    def __init__(self, accounts=None):
        self.accounts = [account()] if accounts is None else accounts
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        assert name == "get_accounts"
        assert arguments == {}
        return {"accounts": self.accounts}


class SessionFactory:
    def __init__(self, session=None, enter_error=None):
        self.session = session or Session()
        self.enter_error = enter_error
        self.enters = 0
        self.exits = 0

    @asynccontextmanager
    async def __call__(self):
        self.enters += 1
        if self.enter_error:
            raise self.enter_error
        try:
            yield self.session
        finally:
            self.exits += 1


def resolver_for(expected=ACCOUNT):
    return CommissionedAccountResolver(
        config_loader=lambda: {
            "broker": {
                "expected_account_fingerprint": account_fingerprint(expected)
            }
        },
        local_config_loader=lambda: {},
    )


def binding(factory, resolver, *, orchestrator_failure=False):
    counts = {"adapter": 0, "orchestrator": 0}

    def adapter_factory(session, commissioned_account):
        counts["adapter"] += 1
        return SimpleNamespace(
            session=session,
            commissioned_account_number=commissioned_account,
        )

    class Orchestrator:
        def __init__(self, **kwargs):
            counts["orchestrator"] += 1
            self.adapter = kwargs["adapter"]

        async def run(self):
            if orchestrator_failure:
                raise RuntimeError("offline orchestrator failure")
            replayed = await self.adapter.session.call_tool("get_accounts", {})
            assert isinstance(replayed, dict)
            return SimpleNamespace(state=State.COMPLETE, evidence_path=Path("safe"))

    instance = RobinhoodCommissioningSessionBinding(
        run_id="offline-run",
        time_policy=TIME_POLICY,
        session_factory=factory,
        commissioned_account_resolver=resolver,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
        now_eastern=lambda: None,
        authorization_provider=lambda stage: None,
        evidence_store=SimpleNamespace(),
        emergency_handoff=lambda payload: None,
        governance_evidence=GOVERNANCE_EVIDENCE,
        adapter_factory=adapter_factory,
        orchestrator_factory=Orchestrator,
    )
    return instance, counts


@pytest.mark.anyio
async def test_exactly_one_session_adapter_orchestrator_and_resolution():
    session = Session()
    factory = SessionFactory(session)
    resolver = resolver_for()
    instance, counts = binding(factory, resolver)
    result = await instance.run()
    assert result.state is State.COMPLETE
    assert factory.enters == 1 and factory.exits == 1
    assert counts == {"adapter": 1, "orchestrator": 1}
    assert session.calls == ["get_accounts"]
    with pytest.raises(CommissioningSessionBindingError, match="single-use"):
        await instance.run()


@pytest.mark.anyio
async def test_session_closes_after_orchestrator_failure():
    factory = SessionFactory()
    instance, _ = binding(factory, resolver_for(), orchestrator_failure=True)
    with pytest.raises(CommissioningDiagnosticError) as caught:
        await instance.run()
    assert caught.value.category == "INTERNAL_INVARIANT_FAILURE"
    assert "offline orchestrator failure" not in str(caught.value)
    assert factory.enters == 1 and factory.exits == 1


@pytest.mark.anyio
async def test_browser_authentication_requirement_fails_closed():
    factory = SessionFactory(
        enter_error=BrowserAuthenticationRequestError("private detail")
    )
    instance, counts = binding(factory, resolver_for())
    with pytest.raises(CommissioningDiagnosticError) as caught:
        await instance.run()
    assert caught.value.category == "SILENT_AUTH_FAILED"
    assert "private detail" not in str(caught.value)
    assert counts == {"adapter": 0, "orchestrator": 0}


@pytest.mark.anyio
async def test_default_factory_creates_and_closes_one_silent_client(monkeypatch):
    import src.robinhood_commissioning_session as module

    observed = {"clients": 0, "sessions": 0, "closed": 0, "browser": None}

    class Client:
        def __init__(self):
            observed["clients"] += 1

        @asynccontextmanager
        async def session(self):
            observed["sessions"] += 1
            observed["browser"] = False
            try:
                yield Session()
            finally:
                observed["closed"] += 1

    monkeypatch.setattr(module, "SilentRobinhoodCommissioningClient", Client)
    async with silent_robinhood_session_factory() as session:
        assert isinstance(session, Session)
    assert observed == {
        "clients": 1, "sessions": 1, "closed": 1, "browser": False
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "rows",
    [
        [],
        [account(), account(OTHER)],
    ],
)
async def test_zero_or_multiple_account_matches_fail_closed(rows):
    session = Session(rows)
    with pytest.raises(CommissioningSessionBindingError) as caught:
        await resolver_for()(session)
    assert ACCOUNT not in str(caught.value)
    assert OTHER not in str(caught.value)
    assert session.calls == ["get_accounts"]


@pytest.mark.anyio
async def test_identity_mismatch_fails_closed_without_identity_values():
    session = Session()
    with pytest.raises(CommissioningSessionBindingError) as caught:
        await resolver_for(OTHER)(session)
    assert ACCOUNT not in str(caught.value)
    assert account_fingerprint(OTHER) not in str(caught.value)


@pytest.mark.anyio
async def test_identity_unavailable_fails_closed():
    resolver = CommissionedAccountResolver(
        config_loader=lambda: {"broker": {}},
        local_config_loader=lambda: {},
    )
    with pytest.raises(CommissioningSessionBindingError):
        await resolver(Session())


@pytest.mark.anyio
async def test_account_identifier_never_reaches_console_or_result(capsys):
    session = Session()
    resolution = await resolver_for()(session)
    assert ACCOUNT not in repr(resolution)
    factory = SessionFactory(session)
    instance, _ = binding(
        factory,
        lambda ignored: CommissionedAccountResolution(
            resolution.account_number, resolution.initial_accounts_result
        ),
    )
    result = await instance.run()
    rendered = capsys.readouterr().out + capsys.readouterr().err + repr(result)
    assert ACCOUNT not in rendered


def test_no_strategy_runner_or_saga_executor_binding():
    source = Path("src/robinhood_commissioning_session.py").read_text(
        encoding="utf-8"
    )
    assert "strategy runner" not in source.lower()
    assert "SagaExecutor" not in source
