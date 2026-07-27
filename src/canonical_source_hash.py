"""Line-ending-stable hashing for reviewed runtime Python sources."""

from __future__ import annotations

import hashlib
from pathlib import Path


class CanonicalSourceHashError(ValueError):
    """The runtime source could not be hashed under the reviewed contract."""


def canonical_source_bytes(path: Path) -> bytes:
    """Return strict UTF-8 source bytes with only newline spelling normalized."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CanonicalSourceHashError("reviewed source file is unavailable") from exc
    if b"\x00" in raw:
        raise CanonicalSourceHashError("reviewed source file contains a NUL byte")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CanonicalSourceHashError("reviewed source file is not valid UTF-8") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_source_sha256(path: Path) -> str:
    """Compute SHA-256 over the canonical runtime source representation."""
    return hashlib.sha256(canonical_source_bytes(path)).hexdigest()
