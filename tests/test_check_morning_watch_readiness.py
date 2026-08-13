from __future__ import annotations

import ast
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import check_morning_watch_readiness as subject


ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = "a" * 64
ENVIRONMENT = {
    "ALPACA_API_KEY_ID": "key-id-value-must-not-appear",
    "ALPACA_API_SECRET_KEY": "secret-value-must-not-appear",
}
LOCAL_CONFIG = {"broker": {"expected_account_fingerprint": FINGERPRINT}}


def readiness(**changes):
    arguments = {
        "environment": ENVIRONMENT,
        "local_config": LOCAL_CONFIG,
        "local_config_present": True,
        "now": datetime(2026, 8, 13, 9, 0, tzinfo=ZoneInfo("America/New_York")),
        "root": ROOT,
    }
    arguments.update(changes)
    return subject.assess_morning_readiness(**arguments)


def test_present_credentials_and_resolvable_identity_are_ready_without_disclosure():
    value = readiness()
    output = subject.render_readiness(value)

    assert value.ready
    assert value.alpaca_key_present and value.alpaca_secret_present
    assert value.account_fingerprint_resolvable
    assert "READY_FOR_MORNING_WATCH" in output
    assert ENVIRONMENT["ALPACA_API_KEY_ID"] not in output
    assert ENVIRONMENT["ALPACA_API_SECRET_KEY"] not in output
    assert FINGERPRINT not in output


def test_missing_each_alpaca_credential_blocks_locally():
    no_key = readiness(environment={"ALPACA_API_SECRET_KEY": "x"})
    no_secret = readiness(environment={"ALPACA_API_KEY_ID": "x"})

    assert not no_key.ready and "ALPACA_API_KEY_ID_MISSING" in no_key.blockers
    assert not no_secret.ready and "ALPACA_API_SECRET_KEY_MISSING" in no_secret.blockers


def test_unresolvable_fingerprint_blocks_without_printing_identity():
    value = readiness(local_config={}, environment=ENVIRONMENT)
    output = subject.render_readiness(value)

    assert not value.ready
    assert "COMMISSIONED_ACCOUNT_FINGERPRINT_NOT_RESOLVABLE" in value.blockers
    assert "Account fingerprint: NOT RESOLVABLE" in output


def test_pre_entry_window_is_informational_not_a_configuration_failure():
    value = readiness()

    assert value.market_session == "PRE_MARKET"
    assert value.ready


def test_authority_is_attended_and_every_activation_flag_is_false():
    output = subject.render_readiness(readiness())

    assert "Authority: ATTENDED_COMMISSIONING_ONLY" in output
    assert "Activation approved: FALSE" in output
    assert "Automated action: DISABLED" in output
    assert "Live ready: FALSE" in output
    assert "Unattended ready: FALSE" in output
    assert "Broker writes: DISABLED" in output


def test_absolute_external_suspension_path_is_accepted_and_repository_path_is_rejected(tmp_path):
    external = (tmp_path / "commissioning-suspension.sqlite").resolve()
    accepted = readiness(suspension_db=external)
    rejected = readiness(suspension_db=ROOT / "data" / "commissioning-suspension.sqlite")

    assert accepted.suspension_db_status.startswith("ACCEPTABLE")
    assert accepted.ready
    assert "SUSPENSION_DB_PATH_INVALID" in rejected.blockers


def test_help_never_needs_network_or_configuration():
    completed = subprocess.run(
        [sys.executable, "scripts/check_morning_watch_readiness.py", "--help"],
        cwd=ROOT, capture_output=True, text=True,
    )

    assert completed.returncode == 0
    assert "local-only readiness" in completed.stdout


def test_readiness_script_has_no_network_or_broker_reachability_surface():
    source = Path(subject.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    forbidden = {
        "AccountStateReader", "RobinhoodCommissioningAdapter", "RobinhoodClient",
        "AlpacaMarketDataProvider", "UrllibMarketDataHttpClient", "call_tool",
        "place_equity_order", "cancel_equity_order", "replace_equity_order", "flatten",
    }
    assert not (names & forbidden)
    assert "_call" not in source
