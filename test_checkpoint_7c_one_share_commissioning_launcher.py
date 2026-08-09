import argparse
import asyncio
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.canonical_source_hash import canonical_source_sha256
from src.one_share_commissioning_orchestrator import CommissioningTimePolicy
from src.one_share_commissioning import (
    CommissioningError,
    EASTERN,
    ENTRY_AUTHORIZATION_STAGE,
    FLATTEN_AUTHORIZATION_STAGE,
    State,
)


ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 7, 29, 10, 40, tzinfo=EASTERN)
TIME_POLICY = CommissioningTimePolicy(
    execution_date=date(2026, 7, 29),
    entry_start=datetime(2026, 7, 29, 10, 35, tzinfo=EASTERN),
    entry_cutoff=datetime(2026, 7, 29, 11, 0, tzinfo=EASTERN),
    mandatory_risk_off=datetime(2026, 7, 29, 11, 10, tzinfo=EASTERN),
    verified_flat_deadline=datetime(2026, 7, 29, 11, 15, tzinfo=EASTERN),
    attended_end=datetime(2026, 7, 29, 11, 20, tzinfo=EASTERN),
)


def test_direct_launcher_is_inert_and_fail_closed(capsys):
    assert launcher.main([]) == 2
    output = capsys.readouterr().out
    assert "BLOCKED" in output
    assert "planned_risk_usd=15" in output
    assert "actual loss may exceed" in output


def git_outputs(changes=None):
    values = {
        ("branch", "--show-current"): "main",
        ("rev-parse", "HEAD"): "a" * 40,
        ("rev-parse", "origin/main"): "a" * 40,
        ("status", "--porcelain", "--untracked-files=no"): "",
        ("diff", "--cached", "--name-only"): "",
    }
    values.update(changes or {})
    return values


def install_git(monkeypatch, values):
    monkeypatch.setattr(
        launcher,
        "_git_output",
        lambda root, *arguments: values[tuple(arguments)],
    )


def test_clean_synchronized_main_git_posture_passes(monkeypatch):
    install_git(monkeypatch, git_outputs())
    launcher.verify_git_posture(ROOT)


@pytest.mark.parametrize(
    "changes",
    [
        {("branch", "--show-current"): "feature"},
        {("branch", "--show-current"): ""},
        {("rev-parse", "origin/main"): "b" * 40},
        {("status", "--porcelain", "--untracked-files=no"): " M tracked.py"},
        {("status", "--porcelain", "--untracked-files=no"): "M  staged.py"},
        {("diff", "--cached", "--name-only"): "staged.py"},
    ],
)
def test_unsafe_git_postures_fail_closed(monkeypatch, changes):
    install_git(monkeypatch, git_outputs(changes))
    with pytest.raises(CommissioningError):
        launcher.verify_git_posture(ROOT)


def test_wrong_repository_root_fails_closed(tmp_path):
    with pytest.raises(CommissioningError, match="root"):
        launcher.verify_git_posture(tmp_path)


def test_reviewed_hash_manifest_required(tmp_path):
    with pytest.raises(CommissioningError):
        launcher.verify_reviewed_hashes(tmp_path, {})


def test_reviewed_hash_verification_and_source_drift(tmp_path):
    path = tmp_path / "reviewed.py"
    path.write_bytes(b"value = 1\r\n")
    expected = canonical_source_sha256(path)
    launcher.verify_reviewed_hashes(tmp_path, {"reviewed.py": expected})
    path.write_bytes(b"value = 2\r\n")
    with pytest.raises(CommissioningError, match="mismatch"):
        launcher.verify_reviewed_hashes(tmp_path, {"reviewed.py": expected})


def test_pinned_commissioning_source_hashes_match():
    launcher.verify_reviewed_hashes(ROOT, launcher.REVIEWED_HASHES)


def test_strategy_enabled_true_fails_closed(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "load_config",
        lambda path: {"strategy": {"enabled": True}},
    )
    with pytest.raises(CommissioningError, match="strategy.enabled"):
        launcher.verify_strategy_disabled(ROOT)


def test_fixed_order_payloads():
    entry = launcher.entry_arguments("transient")
    stop = launcher.stop_arguments("transient", Decimal("185.00"))
    exit_order = launcher.exit_arguments("transient")
    assert entry["symbol"] == "AAPL" and entry["quantity"] == "1"
    assert entry["side"] == "buy" and entry["market_hours"] == "regular_hours"
    assert stop["side"] == "sell" and stop["type"] == "stop_market"
    assert stop["time_in_force"] == "gtc" and stop["stop_price"] == "185.00"
    assert exit_order["side"] == "sell" and exit_order["quantity"] == "1"


def test_sanitized_review_omits_prices_and_identity():
    summary = launcher.sanitized_review_summary({
        "symbol": "AAPL", "quantity": "1", "side": "buy", "type": "market",
        "account_number": "private", "stop_price": "private",
    })
    assert summary == {
        "symbol_match": True, "quantity_match": True, "side": "buy",
        "order_type": "market", "planned_risk_usd": 15,
        "review_json_type": "dict",
    }


def test_normal_strategy_does_not_import_commissioning_launcher():
    for relative in ("run.py", "src/main.py"):
        path = ROOT / relative
        if path.exists():
            assert "one_share_commissioning" not in path.read_text(encoding="utf-8")


def launcher_kwargs(tmp_path):
    return dict(
        root=ROOT,
        session=SimpleNamespace(offline_fixture=True),
        commissioned_account_number="transient",
        run_id="fake-run",
        evidence_root=tmp_path,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
        now_eastern=lambda: NOW,
        authorization_provider=lambda stage: None,
        emergency_handoff=lambda payload: None,
        strategy_enabled=False,
        time_policy=TIME_POLICY,
    )


def test_launcher_fake_session_mode_creates_one_adapter_and_orchestrator(
    tmp_path, monkeypatch, capsys
):
    created = {"adapter": 0, "orchestrator": 0}

    def fake_adapter(session, account, **kwargs):
        created["adapter"] += 1
        return SimpleNamespace(commissioned_account_number=account)

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            created["orchestrator"] += 1

        async def run(self):
            return SimpleNamespace(state=State.COMPLETE)

    monkeypatch.setattr(launcher, "verify_runtime_posture", lambda root: None)
    monkeypatch.setattr(launcher, "RobinhoodCommissioningAdapter", fake_adapter)
    monkeypatch.setattr(launcher, "OneShareCommissioningOrchestrator", FakeOrchestrator)
    result = launcher.run_fake_demo(**launcher_kwargs(tmp_path))
    assert result.state is State.COMPLETE
    assert created == {"adapter": 1, "orchestrator": 1}
    assert "commissioning_state=COMPLETE" in capsys.readouterr().out


def test_fake_session_mode_never_creates_real_client(tmp_path, monkeypatch):
    import src.robinhood_commissioning_session as binding_module

    monkeypatch.setattr(
        binding_module,
        "SilentRobinhoodCommissioningClient",
        lambda: (_ for _ in ()).throw(AssertionError("real client created")),
    )
    monkeypatch.setattr(launcher, "verify_runtime_posture", lambda root: None)
    monkeypatch.setattr(
        launcher,
        "RobinhoodCommissioningAdapter",
        lambda session, account, **kwargs: SimpleNamespace(commissioned_account_number=account),
    )

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            return SimpleNamespace(state=State.COMPLETE)

    monkeypatch.setattr(launcher, "OneShareCommissioningOrchestrator", FakeOrchestrator)
    assert launcher.run_fake_demo(**launcher_kwargs(tmp_path)).state is State.COMPLETE


@pytest.mark.parametrize("mode", ["blocked", "real"])
def test_injected_entry_point_rejects_non_fake_modes(tmp_path, mode):
    kwargs = launcher_kwargs(tmp_path)
    kwargs["mode"] = mode
    with pytest.raises(CommissioningError, match="tracked"):
        asyncio.run(launcher.execute_commissioning(**kwargs))


def test_launcher_fake_mode_rejects_unmarked_session(tmp_path):
    kwargs = launcher_kwargs(tmp_path)
    kwargs.update(mode="fake", session=object())
    with pytest.raises(CommissioningError, match="offline fixture"):
        asyncio.run(launcher.execute_commissioning(**kwargs))


def live_request(tmp_path, **changes):
    values = dict(
        run_id="cp7c-live-aapl-one-share-20260729-test",
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
    values.update(changes)
    return launcher.LiveRunRequest(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("attended_start", time(10, 35, tzinfo=timezone.utc)),
        ("entry_cutoff", time(11, 0, tzinfo=timezone.utc)),
    ],
)
def test_live_request_rejects_offset_bearing_runtime_times(
    tmp_path, field, value
):
    with pytest.raises(CommissioningError):
        live_request(tmp_path, **{field: value}).time_policy()


@pytest.mark.parametrize("value", ["bad", "10:35:00-04:00"])
def test_runtime_time_parser_fails_closed(value):
    with pytest.raises(argparse.ArgumentTypeError):
        launcher._parse_time(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"real_session_flag": ""},
        {"live_checkpoint_flag": ""},
        {"run_id": "../bad"},
        {"execution_date": date(2026, 7, 30)},
        {"attended_start": time(11, 0)},
        {"entry_cutoff": time(10, 35)},
        {"mandatory_risk_off": time(10, 59)},
        {"verified_flat_deadline": time(11, 9)},
        {"attended_end": time(11, 14)},
        {"symbol": "MSFT"},
        {"quantity": Decimal("2")},
        {"planned_risk_usd": Decimal("14")},
        {"acknowledges_loss_may_exceed_plan": False},
        {"operator_present": False},
        {"robinhood_ui_open": False},
        {"no_second_trade": False},
    ],
)
def test_real_session_request_requires_every_authorization_field(tmp_path, changes):
    with pytest.raises(CommissioningError):
        launcher.validate_live_run_request(live_request(tmp_path, **changes), now=NOW)


def test_used_run_id_fails_before_session_creation(tmp_path):
    request = live_request(tmp_path)
    (tmp_path / request.run_id).mkdir()
    with pytest.raises(CommissioningError, match="already"):
        launcher.validate_live_run_request(request, now=NOW)


def test_entry_and_flatten_authorizations_are_separate_and_single_use(tmp_path):
    supplied = iter(["y", "Y"])
    provider = launcher.InteractiveAuthorizationProvider(
        live_request(tmp_path),
        now_eastern=lambda: NOW,
        secret_reader=lambda prompt: next(supplied),
    )
    entry = provider("entry")
    flatten = provider("flatten")
    assert entry.stage == ENTRY_AUTHORIZATION_STAGE
    assert flatten.stage == FLATTEN_AUTHORIZATION_STAGE
    with pytest.raises(CommissioningError, match="reusable"):
        provider("entry")


def test_real_session_execution_constructs_exactly_one_binding(
    tmp_path, monkeypatch, capsys
):
    created = []

    class Binding:
        def __init__(self, **kwargs):
            created.append(kwargs)

        async def run(self):
            return SimpleNamespace(state=State.COMPLETE)

    monkeypatch.setattr(launcher, "verify_runtime_posture", lambda root: None)
    result = asyncio.run(
        launcher.execute_real_session(
            live_request(tmp_path),
            now_eastern=lambda: NOW,
            session_factory=lambda: None,
            commissioned_account_resolver=lambda session: None,
            authorization_provider=lambda stage: None,
            evidence_store=SimpleNamespace(),
            emergency_handoff=lambda payload: None,
            binding_factory=Binding,
        )
    )
    assert result.state is State.COMPLETE
    assert len(created) == 1
    assert created[0]["time_policy"] == live_request(tmp_path).time_policy()
    output = capsys.readouterr().out
    assert "commissioning_state=COMPLETE" in output
    assert "account" not in output.lower()


def test_parser_requires_explicit_real_and_live_flags(tmp_path):
    parser = launcher.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
