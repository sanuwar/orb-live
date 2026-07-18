import sqlite3

import pytest

from src.journal import Journal


def test_append_event_and_count(tmp_path):
    journal = Journal(tmp_path / "test_journal.sqlite")

    event_id = journal.append_event(
        strategy_id="orb_long_v0",
        event_type="signal_approved",
        symbol="AAPL",
        config_hash="abc123",
        payload={"entry_price": "200.00"},
    )

    assert event_id == 1
    assert journal.count_events() == 1


def test_config_hash_is_repeatable():
    config_a = {
        "strategy": {"name": "orb_long_v0"},
        "risk": {"risk_per_trade_usd": 15},
    }

    config_b = {
        "risk": {"risk_per_trade_usd": 15},
        "strategy": {"name": "orb_long_v0"},
    }

    assert Journal.config_hash(config_a) == Journal.config_hash(config_b)


def test_journal_event_cannot_be_updated(tmp_path):
    journal = Journal(tmp_path / "test_journal.sqlite")

    journal.append_event(
        strategy_id="orb_long_v0",
        event_type="test",
        symbol="AAPL",
        config_hash="abc123",
        payload={"value": 1},
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="append-only",
    ):
        with journal._connect() as connection:
            connection.execute(
                "UPDATE events SET event_type = ? WHERE id = 1",
                ("changed",),
            )


def test_journal_event_cannot_be_deleted(tmp_path):
    journal = Journal(tmp_path / "test_journal.sqlite")

    journal.append_event(
        strategy_id="orb_long_v0",
        event_type="test",
        symbol="AAPL",
        config_hash="abc123",
        payload={"value": 1},
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="append-only",
    ):
        with journal._connect() as connection:
            connection.execute(
                "DELETE FROM events WHERE id = 1"
            )