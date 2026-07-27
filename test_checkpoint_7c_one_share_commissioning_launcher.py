from decimal import Decimal
from pathlib import Path

import pytest

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.one_share_commissioning import CommissioningError


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
    root = Path(__file__).resolve().parent
    for relative in ("run.py", "src/main.py"):
        path = root / relative
        if path.exists():
            assert "one_share_commissioning" not in path.read_text(encoding="utf-8")
