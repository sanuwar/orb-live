"""Shared repository-data path refusal for offline durable stores."""

from __future__ import annotations

import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_DATA_DIRECTORY = os.path.normcase(
    os.path.abspath(os.fspath(REPOSITORY_ROOT / "data"))
)


def is_repository_data_path(path: Path) -> bool:
    """Return whether ``path`` or its resolved target is in repository ``data/``."""
    try:
        raw = os.fspath(path)
        lexical = os.path.normcase(os.path.abspath(raw))
    except (TypeError, ValueError):
        return False
    lexical_match = (
        lexical == REPOSITORY_DATA_DIRECTORY
        or os.path.commonpath([lexical, REPOSITORY_DATA_DIRECTORY])
        == REPOSITORY_DATA_DIRECTORY
    )
    if lexical_match:
        return True
    target = os.path.normcase(os.path.realpath(raw))
    return (
        target == REPOSITORY_DATA_DIRECTORY
        or os.path.commonpath([target, REPOSITORY_DATA_DIRECTORY])
        == REPOSITORY_DATA_DIRECTORY
    )


__all__ = ["REPOSITORY_DATA_DIRECTORY", "is_repository_data_path"]
