import hashlib

import pytest

from src.canonical_source_hash import (
    CanonicalSourceHashError,
    canonical_source_bytes,
    canonical_source_sha256,
)


def write_bytes(tmp_path, name, content):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_lf_and_crlf_equivalents_have_same_hash(tmp_path):
    lf = write_bytes(tmp_path, "lf.py", b"first\nsecond\n")
    crlf = write_bytes(tmp_path, "crlf.py", b"first\r\nsecond\r\n")
    assert canonical_source_sha256(lf) == canonical_source_sha256(crlf)


def test_mixed_newlines_normalize_deterministically(tmp_path):
    mixed = write_bytes(tmp_path, "mixed.py", b"a\r\nb\rc\n")
    assert canonical_source_bytes(mixed) == b"a\nb\nc\n"
    assert canonical_source_sha256(mixed) == hashlib.sha256(b"a\nb\nc\n").hexdigest()


def test_semantic_source_change_changes_hash(tmp_path):
    before = write_bytes(tmp_path, "before.py", b"value = 1\n")
    after = write_bytes(tmp_path, "after.py", b"value = 2\n")
    assert canonical_source_sha256(before) != canonical_source_sha256(after)


def test_whitespace_change_changes_hash(tmp_path):
    before = write_bytes(tmp_path, "before.py", b"value = 1\n")
    after = write_bytes(tmp_path, "after.py", b"value  = 1\n")
    assert canonical_source_sha256(before) != canonical_source_sha256(after)


def test_final_newline_presence_is_significant(tmp_path):
    with_newline = write_bytes(tmp_path, "with.py", b"value = 1\n")
    without_newline = write_bytes(tmp_path, "without.py", b"value = 1")
    assert canonical_source_sha256(with_newline) != canonical_source_sha256(
        without_newline)


def test_utf8_bom_presence_is_significant(tmp_path):
    plain = write_bytes(tmp_path, "plain.py", "value = 1\n".encode("utf-8"))
    bom = write_bytes(
        tmp_path, "bom.py", b"\xef\xbb\xbf" + "value = 1\n".encode("utf-8"))
    assert canonical_source_sha256(plain) != canonical_source_sha256(bom)


def test_invalid_utf8_fails_closed(tmp_path):
    path = write_bytes(tmp_path, "invalid.py", b"\xff")
    with pytest.raises(CanonicalSourceHashError, match="valid UTF-8"):
        canonical_source_sha256(path)


def test_nul_byte_fails_closed(tmp_path):
    path = write_bytes(tmp_path, "embedded_zero.py", b"value\x00 = 1\n")
    with pytest.raises(CanonicalSourceHashError, match="NUL"):
        canonical_source_sha256(path)


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(CanonicalSourceHashError, match="unavailable"):
        canonical_source_sha256(tmp_path / "missing.py")
