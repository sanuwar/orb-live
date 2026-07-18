from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from src.trade_state import TradeStateStore


REPOSITORY_ROOT = Path(__file__).parent.parent
REPOSITORY_DATA_DIRECTORY = os.path.normcase(
    os.path.abspath(
        os.fspath(REPOSITORY_ROOT / "data")
    )
)


def is_repository_data_database_path(
    database,
) -> bool:
    if isinstance(database, int):
        return False

    try:
        raw_path = os.fspath(database)
    except TypeError:
        return False

    if isinstance(raw_path, bytes):
        raw_path = os.fsdecode(raw_path)

    if raw_path == ":memory:":
        return False

    if raw_path.startswith("file:"):
        raw_path = unquote(
            raw_path[5:].split("?", 1)[0]
        )

        if raw_path == ":memory:":
            return False

    candidate = os.path.normcase(
        os.path.abspath(raw_path)
    )

    try:
        return (
            os.path.commonpath(
                [
                    candidate,
                    REPOSITORY_DATA_DIRECTORY,
                ]
            )
            == REPOSITORY_DATA_DIRECTORY
        )
    except ValueError:
        return False


@pytest.fixture(scope="session", autouse=True)
def repository_data_database_guard():
    attempts: list[str] = []
    original_connect = sqlite3.connect
    original_store_init = TradeStateStore.__init__

    def reject(database) -> None:
        if not is_repository_data_database_path(
            database
        ):
            return

        attempted_path = os.fspath(database)
        attempts.append(str(attempted_path))
        raise AssertionError(
            "Tests must not access a database under "
            "the repository data directory."
        )

    def guarded_connect(
        database,
        *args,
        **kwargs,
    ):
        reject(database)
        return original_connect(
            database,
            *args,
            **kwargs,
        )

    def guarded_store_init(
        self,
        db_path,
    ):
        reject(db_path)
        return original_store_init(
            self,
            db_path,
        )

    guarded_connect.orb_repository_data_guard = True
    guarded_store_init.orb_repository_data_guard = True

    sqlite3.connect = guarded_connect
    TradeStateStore.__init__ = guarded_store_init

    state = SimpleNamespace(
        attempts=attempts,
        repository_data_directory=(
            REPOSITORY_DATA_DIRECTORY
        ),
    )

    try:
        yield state
    finally:
        sqlite3.connect = original_connect
        TradeStateStore.__init__ = (
            original_store_init
        )

        if attempts:
            pytest.fail(
                "The test suite attempted repository "
                "data database access: "
                + ", ".join(attempts)
            )
