from __future__ import annotations

import pytest

from src.broker_identity import (
    FINGERPRINT_ENVIRONMENT_VARIABLE,
    CommissionedAccount,
    CommissionedIdentityError,
    load_local_override,
    normalize_fingerprint,
    resolve_commissioned_fingerprint,
)
from src.broker_snapshot import account_fingerprint


COMMISSIONED = account_fingerprint("TEST12345678")
OTHER = account_fingerprint("OTHER87654321")


def config_with(value):
    return {
        "broker": {
            "expected_account_fingerprint": value
        }
    }


def test_valid_fingerprint_normalizes():
    assert (
        normalize_fingerprint(
            COMMISSIONED.upper(),
            source="test",
        )
        == COMMISSIONED
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "TEST12345678",
        "not-a-fingerprint",
        COMMISSIONED[:-1],
        COMMISSIONED + "a",
        12345,
    ],
)
def test_malformed_fingerprint_fails_closed(value):
    with pytest.raises(CommissionedIdentityError):
        normalize_fingerprint(value, source="test")


def test_missing_fingerprint_fails_closed():
    with pytest.raises(CommissionedIdentityError):
        resolve_commissioned_fingerprint(
            config_with(None),
            environment={},
        )


def test_absent_broker_section_fails_closed():
    with pytest.raises(CommissionedIdentityError):
        resolve_commissioned_fingerprint(
            {"strategy": {"name": "orb"}},
            environment={},
        )


def test_raw_account_number_is_refused():
    with pytest.raises(CommissionedIdentityError):
        resolve_commissioned_fingerprint(
            config_with("TEST12345678"),
            environment={},
        )


def test_local_override_wins_over_base_config():
    resolved = resolve_commissioned_fingerprint(
        config_with(OTHER),
        local_config=config_with(COMMISSIONED),
        environment={},
    )

    assert resolved == COMMISSIONED


def test_environment_supplies_value_when_unconfigured():
    resolved = resolve_commissioned_fingerprint(
        config_with(None),
        environment={
            FINGERPRINT_ENVIRONMENT_VARIABLE: (
                COMMISSIONED
            )
        },
    )

    assert resolved == COMMISSIONED


def test_environment_may_not_silently_override_local():
    with pytest.raises(
        CommissionedIdentityError
    ) as error:
        resolve_commissioned_fingerprint(
            config_with(None),
            local_config=config_with(COMMISSIONED),
            environment={
                FINGERPRINT_ENVIRONMENT_VARIABLE: OTHER
            },
        )

    assert "silently overriding" in str(error.value)


def test_matching_environment_value_is_accepted():
    resolved = resolve_commissioned_fingerprint(
        config_with(None),
        local_config=config_with(COMMISSIONED),
        environment={
            FINGERPRINT_ENVIRONMENT_VARIABLE: (
                COMMISSIONED.upper()
            )
        },
    )

    assert resolved == COMMISSIONED


def test_verify_accepts_the_commissioned_account():
    account = CommissionedAccount(
        expected_fingerprint=COMMISSIONED
    )

    account.verify(COMMISSIONED)
    assert account.matches(COMMISSIONED)


def test_verify_rejects_another_account():
    account = CommissionedAccount(
        expected_fingerprint=COMMISSIONED
    )

    with pytest.raises(CommissionedIdentityError):
        account.verify(OTHER)

    assert not account.matches(OTHER)


def test_verify_never_adopts_the_observed_account():
    account = CommissionedAccount(
        expected_fingerprint=COMMISSIONED
    )

    with pytest.raises(CommissionedIdentityError):
        account.verify(OTHER)

    assert (
        account.expected_fingerprint == COMMISSIONED
    )


def test_no_trust_on_first_use_helper_exists():
    for name in dir(CommissionedAccount):
        lowered = name.lower()
        assert "trust" not in lowered
        assert "adopt" not in lowered
        assert "commission_from" not in lowered


def test_account_number_is_not_stored():
    account = CommissionedAccount(
        expected_fingerprint=COMMISSIONED
    )

    assert "TEST12345678" not in repr(account)


def test_missing_local_override_is_not_an_error(
    tmp_path,
):
    assert (
        load_local_override(
            tmp_path / "absent.yaml"
        )
        == {}
    )


def test_local_override_reads_yaml(tmp_path):
    path = tmp_path / "local.yaml"
    path.write_text(
        "broker:\n"
        f"  expected_account_fingerprint: {COMMISSIONED}\n",
        encoding="utf-8",
    )

    assert load_local_override(path) == {
        "broker": {
            "expected_account_fingerprint": (
                COMMISSIONED
            )
        }
    }


def test_malformed_local_override_fails_closed(
    tmp_path,
):
    path = tmp_path / "local.yaml"
    path.write_text(
        "- not-an-object\n", encoding="utf-8"
    )

    with pytest.raises(CommissionedIdentityError):
        load_local_override(path)


def test_repository_config_ships_without_a_real_value():
    from src.config import load_config

    config = load_config()
    broker = config.get("broker") or {}

    assert (
        broker.get("expected_account_fingerprint")
        is None
    )
