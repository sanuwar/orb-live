from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from src import durable_action_ledger, durable_scheduler, durable_suspension_controller, offline_safety_orchestrator
from src.offline_path_safety import REPOSITORY_DATA_DIRECTORY, is_repository_data_path
import src.offline_path_safety as path_safety


ROOT = Path(__file__).resolve().parents[1]
CONSUMERS = (
    durable_action_ledger,
    durable_scheduler,
    durable_suspension_controller,
    offline_safety_orchestrator,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ROOT / "data", True),
        (ROOT / "data" / "store.sqlite", True),
        (ROOT / "datasets" / "store.sqlite", False),
        (ROOT / "src", False),
        (Path("data") / "relative.sqlite", True),
        (Path("..") / ROOT.name / "data" / "normalized.sqlite", True),
        (None, False),
    ],
)
def test_shared_predicate_preserves_exact_path_contract(value: object, expected: bool) -> None:
    assert is_repository_data_path(value) is expected  # type: ignore[arg-type]


def test_former_consumers_reexport_the_exact_shared_contract() -> None:
    for consumer in CONSUMERS:
        assert consumer.is_repository_data_path is is_repository_data_path
        assert consumer.REPOSITORY_DATA_DIRECTORY == REPOSITORY_DATA_DIRECTORY


def test_consolidation_does_not_create_an_import_cycle() -> None:
    tree = ast.parse((ROOT / "src" / "offline_path_safety.py").read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(value.startswith("src.") for value in imports)


def test_shared_module_has_no_database_network_or_write_capability() -> None:
    source = (ROOT / "src" / "offline_path_safety.py").read_text(encoding="utf-8")
    for forbidden in ("sqlite3", "requests", "httpx", "socket", "robin_stocks", "write_text", "open("):
        assert forbidden not in source


def test_symlink_alias_into_repository_data_is_refused_and_lexical_refusal_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    data = repository / "data"
    outside = tmp_path / "outside"
    data.mkdir(parents=True)
    outside.mkdir()
    alias_into_data = outside / "data-alias"
    alias_out_of_data = data / "outside-alias"
    try:
        os.symlink(data, alias_into_data, target_is_directory=True)
        os.symlink(outside, alias_out_of_data, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    monkeypatch.setattr(
        path_safety, "REPOSITORY_DATA_DIRECTORY",
        os.path.normcase(os.path.abspath(os.fspath(data))),
    )
    assert is_repository_data_path(alias_into_data / "store.sqlite") is True
    assert is_repository_data_path(alias_out_of_data / "store.sqlite") is True


def test_malformed_path_like_value_remains_false() -> None:
    class MalformedPath:
        def __fspath__(self) -> str:
            raise ValueError("private provider detail")

    assert is_repository_data_path(MalformedPath()) is False  # type: ignore[arg-type]
