"""Durable action ledger — idempotency that survives a restart.

Every test uses `tmp_path`. Nothing here opens, reads, or creates a database
inside the repository's `data/` directory, and the module refuses to.

The property that matters most: **a key spent before a crash is still spent
after it.** An in-memory ledger forgets exactly when duplicate protection
matters most, because the first thing a scheduler does after a crash is retry.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from decimal import Decimal

import pytest

import src.durable_action_ledger as ledger_module
from src.durable_action_ledger import (
    REPOSITORY_DATA_DIRECTORY,
    SCHEMA_VERSION,
    DurableActionLedger,
    DurableLedgerError,
    is_repository_data_path,
)
from src.risk_limits import (
    AccountRiskState,
    ActionLedger,
    ActionLedgerBase,
    ActionProposal,
    KillSwitchState,
    RiskDecision,
    RiskLimits,
    evaluate,
)

MODULE_PATH = pathlib.Path(ledger_module.__file__)
REPOSITORY_ROOT = MODULE_PATH.parent.parent


def ledger(tmp_path, name: str = "ledger.sqlite3") -> DurableActionLedger:
    return DurableActionLedger(tmp_path / "state" / name)


# --------------------------------------------------------------------------
# 1. Durability across a restart.
# --------------------------------------------------------------------------


def test_a_spent_key_is_still_spent_after_a_restart(tmp_path):
    """The whole reason this module exists."""
    path = tmp_path / "state" / "ledger.sqlite3"
    first = DurableActionLedger(path)
    first.record("action-1", 3)
    assert first.has_seen("action-1") is True
    first.close()

    reopened = DurableActionLedger(path)          # simulated restart
    assert reopened.has_seen("action-1") is True
    with pytest.raises(DurableLedgerError):
        reopened.record("action-1", 3)
    reopened.close()


def test_approved_exposure_survives_a_restart(tmp_path):
    path = tmp_path / "state" / "ledger.sqlite3"
    with DurableActionLedger(path) as first:
        first.record("a", 4)
        first.record("b", 3)
        assert first.approved_quantity == 7
    with DurableActionLedger(path) as reopened:
        assert reopened.approved_quantity == 7
        assert reopened.recorded_count == 2


def test_an_in_memory_ledger_forgets_where_this_one_does_not(tmp_path):
    """States the contrast the module exists to fix."""
    volatile = ActionLedger()
    volatile.record("action-1", 3)
    assert ActionLedger().has_seen("action-1") is False   # a fresh process

    path = tmp_path / "state" / "ledger.sqlite3"
    with DurableActionLedger(path) as durable:
        durable.record("action-1", 3)
    with DurableActionLedger(path) as after_restart:
        assert after_restart.has_seen("action-1") is True


def test_a_fresh_ledger_starts_empty(tmp_path):
    with ledger(tmp_path) as book:
        assert book.recorded_count == 0
        assert book.approved_quantity == 0
        assert book.has_seen("anything") is False


# --------------------------------------------------------------------------
# 2. Idempotency is enforced by the database.
# --------------------------------------------------------------------------


def test_recording_the_same_key_twice_is_refused(tmp_path):
    with ledger(tmp_path) as book:
        book.record("k", 1)
        with pytest.raises(DurableLedgerError):
            book.record("k", 1)
        assert book.recorded_count == 1


def test_a_refused_duplicate_adds_no_exposure(tmp_path):
    with ledger(tmp_path) as book:
        book.record("k", 5)
        with pytest.raises(DurableLedgerError):
            book.record("k", 5)
        assert book.approved_quantity == 5


def test_uniqueness_is_a_primary_key_not_a_python_check(tmp_path):
    """A check-then-insert has a window; two processes both win in it."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "idempotency_key  TEXT PRIMARY KEY" in source
    assert "IntegrityError" in source
    with ledger(tmp_path) as book:
        book.record("k", 1)
        with pytest.raises(sqlite3.IntegrityError):
            book._connection.execute(
                "INSERT INTO spent_actions "
                "(idempotency_key, approved_quantity, recorded_sequence) "
                "VALUES ('k', 1, 99)"
            )


def test_a_second_connection_cannot_reuse_a_key(tmp_path):
    """Concurrency, not just sequence: two open handles on one file."""
    path = tmp_path / "state" / "ledger.sqlite3"
    first = DurableActionLedger(path)
    second = DurableActionLedger(path)
    first.record("shared", 2)
    assert second.has_seen("shared") is True
    with pytest.raises(DurableLedgerError):
        second.record("shared", 2)
    first.close()
    second.close()


def test_there_is_no_removal_method():
    """A durable ledger makes forgetting worse, not better."""
    removal = {"remove", "delete", "clear", "forget", "reset", "drop", "purge"}
    for name in dir(DurableActionLedger):
        if name.startswith("_"):
            continue
        assert not (set(name.lower().split("_")) & removal), name


def test_no_sql_statement_deletes_or_drops():
    source = MODULE_PATH.read_text(encoding="utf-8").upper()
    for statement in ("DELETE FROM", "DROP TABLE", "TRUNCATE", "UPDATE "):
        assert statement not in source, statement


# --------------------------------------------------------------------------
# 3. Repository data is refused.
# --------------------------------------------------------------------------


def test_a_path_inside_repository_data_is_refused():
    """Prohibited, and enforced where the file would actually be opened."""
    with pytest.raises(DurableLedgerError):
        DurableActionLedger(REPOSITORY_ROOT / "data" / "ledger.sqlite3")
    with pytest.raises(DurableLedgerError):
        DurableActionLedger(REPOSITORY_ROOT / "data" / "nested" / "ledger.sqlite3")
    with pytest.raises(DurableLedgerError):
        DurableActionLedger(REPOSITORY_ROOT / "data")


def test_the_repository_data_check_is_path_based_not_name_based():
    assert is_repository_data_path(REPOSITORY_ROOT / "data" / "x.sqlite3") is True
    assert is_repository_data_path(REPOSITORY_ROOT / "data") is True
    assert is_repository_data_path(pathlib.Path("/tmp/data/x.sqlite3")) is False
    assert is_repository_data_path(REPOSITORY_ROOT / "datasets" / "x") is False


def test_a_relative_path_into_repository_data_is_still_refused(monkeypatch):
    """Resolution happens before the comparison, so `..` does not slip past."""
    monkeypatch.chdir(REPOSITORY_ROOT / "src")
    assert is_repository_data_path(pathlib.Path("../data/ledger.sqlite3")) is True


def test_no_test_here_touches_repository_data(tmp_path):
    with ledger(tmp_path) as book:
        resolved = str(book.database_path.resolve())
    assert REPOSITORY_DATA_DIRECTORY.lower() not in resolved.lower()


@pytest.mark.parametrize("value", ["ledger.sqlite3", None, 42, b"path"])
def test_a_non_path_is_refused(value):
    with pytest.raises(DurableLedgerError):
        DurableActionLedger(value)


# --------------------------------------------------------------------------
# 4. Schema handling.
# --------------------------------------------------------------------------


def test_the_schema_version_is_recorded(tmp_path):
    with ledger(tmp_path) as book:
        row = book._connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row[0] == str(SCHEMA_VERSION)


def test_an_unknown_schema_version_is_refused(tmp_path):
    """Guessing at an unknown layout is how a ledger silently forgets."""
    path = tmp_path / "state" / "ledger.sqlite3"
    with DurableActionLedger(path) as book:
        book._connection.execute(
            "UPDATE ledger_meta SET value = '99' WHERE key = 'schema_version'"
        )
    with pytest.raises(DurableLedgerError):
        DurableActionLedger(path)


def test_reopening_an_existing_ledger_does_not_reset_it(tmp_path):
    path = tmp_path / "state" / "ledger.sqlite3"
    with DurableActionLedger(path) as book:
        book.record("k", 2)
    for _ in range(3):
        with DurableActionLedger(path) as reopened:
            assert reopened.recorded_count == 1
            assert reopened.approved_quantity == 2


# --------------------------------------------------------------------------
# 5. Input validation.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["", "   ", None, 7, b"k"])
def test_an_invalid_key_is_refused(tmp_path, key):
    with ledger(tmp_path) as book:
        with pytest.raises(DurableLedgerError):
            book.has_seen(key)
        with pytest.raises(DurableLedgerError):
            book.record(key, 1)


@pytest.mark.parametrize("quantity", [-1, 1.5, True, "1", None])
def test_an_invalid_approved_quantity_is_refused(tmp_path, quantity):
    with ledger(tmp_path) as book:
        with pytest.raises(DurableLedgerError):
            book.record("k", quantity)


def test_recording_requires_an_explicit_quantity(tmp_path):
    with ledger(tmp_path) as book:
        with pytest.raises(TypeError):
            book.record("k")


def test_a_zero_quantity_record_is_allowed(tmp_path):
    """Spending a key without adding exposure is legitimate."""
    with ledger(tmp_path) as book:
        book.record("k", 0)
        assert book.has_seen("k") is True
        assert book.approved_quantity == 0


# --------------------------------------------------------------------------
# 6. Interchangeable with the in-memory ledger.
# --------------------------------------------------------------------------


def limits() -> RiskLimits:
    return RiskLimits(
        maximum_position_quantity=10,
        maximum_order_value=Decimal("1000"),
        maximum_daily_loss=Decimal("45"),
        maximum_open_positions=3,
    )


def proposal(key: str, quantity: int) -> ActionProposal:
    return ActionProposal(
        idempotency_key=key, quantity=quantity,
        order_value=Decimal("100"), observed_monotonic=100.0,
    )


def test_the_durable_ledger_satisfies_the_risk_contract():
    """A durable ledger nobody can pass to the risk check is decoration."""
    assert issubclass(DurableActionLedger, ActionLedgerBase)
    assert issubclass(ActionLedger, ActionLedgerBase)


def test_risk_evaluation_accepts_the_durable_ledger(tmp_path):
    with ledger(tmp_path) as book:
        result = evaluate(
            proposal("a", 1), limits(),
            AccountRiskState(realized_daily_loss=Decimal("0"), open_position_count=0),
            kill_switch=KillSwitchState.DISENGAGED, ledger=book,
            current_monotonic=101.0,
        )
    assert result.decision is RiskDecision.WITHIN_LIMITS


def test_cumulative_exposure_is_bounded_across_a_restart(tmp_path):
    """The in-memory ledger would have allowed all three after a crash."""
    path = tmp_path / "state" / "ledger.sqlite3"
    state = AccountRiskState(realized_daily_loss=Decimal("0"), open_position_count=0)

    with DurableActionLedger(path) as book:
        for key in ("a", "b"):
            result = evaluate(proposal(key, 4), limits(), state,
                              kill_switch=KillSwitchState.DISENGAGED,
                              ledger=book, current_monotonic=101.0)
            assert result.decision is RiskDecision.WITHIN_LIMITS
            book.record(key, 4)

    with DurableActionLedger(path) as after_restart:      # crash and retry
        result = evaluate(proposal("c", 4), limits(), state,
                          kill_switch=KillSwitchState.DISENGAGED,
                          ledger=after_restart, current_monotonic=101.0)
    assert result.decision is RiskDecision.DENIED_POSITION_SIZE


def test_a_replayed_key_is_denied_after_a_restart(tmp_path):
    path = tmp_path / "state" / "ledger.sqlite3"
    state = AccountRiskState(realized_daily_loss=Decimal("0"), open_position_count=0)
    with DurableActionLedger(path) as book:
        book.record("a", 1)
    with DurableActionLedger(path) as after_restart:
        result = evaluate(proposal("a", 1), limits(), state,
                          kill_switch=KillSwitchState.DISENGAGED,
                          ledger=after_restart, current_monotonic=101.0)
    assert result.decision is RiskDecision.DENIED_DUPLICATE_ACTION


def test_a_bare_object_is_still_refused_by_the_risk_check():
    """Relaxing to a base class must not become duck typing."""
    class Impostor:
        def has_seen(self, key): return False
        def record(self, key, approved_quantity): pass
        approved_quantity = 0

    with pytest.raises(Exception):
        evaluate(
            proposal("a", 1), limits(),
            AccountRiskState(realized_daily_loss=Decimal("0"), open_position_count=0),
            kill_switch=KillSwitchState.DISENGAGED, ledger=Impostor(),
            current_monotonic=101.0,
        )


def test_the_base_class_refuses_to_be_used_directly():
    base = ActionLedgerBase()
    for call in (lambda: base.has_seen("k"), lambda: base.record("k", 1),
                 lambda: base.approved_quantity):
        with pytest.raises(NotImplementedError):
            call()


# --------------------------------------------------------------------------
# 7. Independence.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_ledger_imports_no_pinned_checkpoint_file():
    for pinned in ("read_only_observation_runtime", "read_only_broker_state",
                   "read_only_authorization_manifest", "live_order_state_model"):
        assert not any(pinned in name for name in _imports(MODULE_PATH)), pinned


def test_the_ledger_makes_no_network_call():
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "httpx", "subprocess"}
    )
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "urlopen" not in source and "http://" not in source


def test_the_ledger_reads_no_wall_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.time(" not in source


def test_no_broker_manifest_or_credential_path_exists():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for token in ("robinhood", "call_tool", "place_equity", "cancel_equity",
                  "manifest", "local.yaml", ".rh_tokens", "password", "environ"):
        assert token not in source, token


def test_the_ledger_stores_no_symbol_price_or_account_value(tmp_path):
    """Only a key, a quantity, and an ordering. Nothing describing a trade."""
    with ledger(tmp_path) as book:
        book.record("a", 3)
        columns = {
            row[1] for row in book._connection.execute(
                "PRAGMA table_info(spent_actions)"
            )
        }
    assert columns == {"idempotency_key", "approved_quantity", "recorded_sequence"}
    for forbidden in ("symbol", "price", "account", "order_id", "side"):
        assert not any(forbidden in column for column in columns), forbidden
