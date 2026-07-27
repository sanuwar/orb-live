from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.one_share_commissioning import CommissioningError
from src.one_share_commissioning import EASTERN, State


def test_direct_launcher_is_inert_and_fail_closed(capsys):
    assert launcher.main() == 2
    output = capsys.readouterr().out
    assert "BLOCKED" in output
    assert "planned_risk_usd=15" in output
    assert "actual loss may exceed" in output


def test_reviewed_hash_manifest_required(tmp_path):
    with pytest.raises(CommissioningError):
        launcher.verify_reviewed_hashes(tmp_path, {})


def test_reviewed_hash_verification(tmp_path):
    path = tmp_path / "reviewed.txt"
    path.write_text("reviewed\n", encoding="utf-8")
    launcher.verify_reviewed_hashes(tmp_path, {"reviewed.txt": launcher.sha256(path)})
    with pytest.raises(CommissioningError):
        launcher.verify_reviewed_hashes(tmp_path, {"reviewed.txt": "0" * 64})


def test_pinned_commissioning_source_hashes_match():
    root = Path(__file__).resolve().parent
    launcher.verify_reviewed_hashes(root, launcher.REVIEWED_HASHES)


def test_fixed_order_payloads():
    entry = launcher.entry_arguments("transient")
    stop = launcher.stop_arguments("transient", Decimal("185.00"))
    exit_order = launcher.exit_arguments("transient")
    assert entry | {"side": "buy"} == entry
    assert entry["symbol"] == "AAPL" and entry["quantity"] == "1"
    assert entry["market_hours"] == "regular_hours"
    assert stop["side"] == "sell" and stop["type"] == "stop_market"
    assert stop["time_in_force"] == "gtc" and stop["stop_price"] == "185.00"
    assert stop["market_hours"] == "regular_hours"
    assert exit_order["side"] == "sell" and exit_order["quantity"] == "1"
    assert exit_order["market_hours"] == "regular_hours"


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
    root = Path(__file__).resolve().parent
    for relative in ("run.py", "src/main.py"):
        path = root / relative
        if path.exists():
            assert "one_share_commissioning" not in path.read_text(encoding="utf-8")


def launcher_kwargs(tmp_path):
    return dict(
        root=Path(__file__).resolve().parent,
        session=SimpleNamespace(offline_fixture=True),
        commissioned_account_number="transient", run_id="fake-run",
        evidence_root=tmp_path, monotonic=lambda: 0.0,
        sleep=lambda seconds: None,
        now_eastern=lambda: __import__("datetime").datetime(
            2026, 7, 27, 11, 0, tzinfo=EASTERN),
        authorization_provider=lambda stage: None,
        emergency_handoff=lambda payload: None, strategy_enabled=False,
    )


def test_launcher_fake_session_mode_creates_one_adapter_and_orchestrator(
        tmp_path, monkeypatch, capsys):
    created = {"adapter": 0, "orchestrator": 0}

    def fake_adapter(session, account):
        created["adapter"] += 1
        return SimpleNamespace(commissioned_account_number=account)

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            created["orchestrator"] += 1

        async def run(self):
            return SimpleNamespace(state=State.COMPLETE)

    monkeypatch.setattr(launcher, "verify_git_posture", lambda root: None)
    monkeypatch.setattr(launcher, "verify_reviewed_hashes", lambda root, hashes: None)
    monkeypatch.setattr(launcher, "RobinhoodCommissioningAdapter", fake_adapter)
    monkeypatch.setattr(launcher, "OneShareCommissioningOrchestrator", FakeOrchestrator)
    result = launcher.run_fake_demo(**launcher_kwargs(tmp_path))
    assert result.state is State.COMPLETE
    assert created == {"adapter": 1, "orchestrator": 1}
    assert "commissioning_state=COMPLETE" in capsys.readouterr().out


@pytest.mark.parametrize("changes", [
    {}, {"mode": "real"},
    {"mode": "real", "runtime_flag": launcher.REAL_SESSION_RUNTIME_FLAG},
])
def test_launcher_real_session_mode_requires_both_explicit_gates(tmp_path, changes):
    kwargs = launcher_kwargs(tmp_path)
    kwargs.update(changes)
    with pytest.raises(CommissioningError):
        __import__("asyncio").run(launcher.execute_commissioning(**kwargs))


def test_launcher_fake_mode_rejects_unmarked_session(tmp_path):
    kwargs = launcher_kwargs(tmp_path)
    kwargs.update(mode="fake", session=object())
    with pytest.raises(CommissioningError, match="offline fixture"):
        __import__("asyncio").run(launcher.execute_commissioning(**kwargs))


def test_launcher_rejects_strategy_enabled_before_adapter(tmp_path):
    kwargs = launcher_kwargs(tmp_path)
    kwargs.update(mode="fake", strategy_enabled=True)
    with pytest.raises(CommissioningError):
        __import__("asyncio").run(launcher.execute_commissioning(**kwargs))
