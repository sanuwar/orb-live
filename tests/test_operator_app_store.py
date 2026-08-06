from pathlib import Path
import sqlite3
import pytest
from src.operator_app_store import OperatorAppStoreError, initialize_store, inspect_store_identity, verify_store_integrity

SHA="a"*40; DIGEST="b"*64
def test_initialize_and_identity(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST); initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    assert inspect_store_identity(db)["schema_version"]==1 and verify_store_integrity(db)
def test_identity_conflict_rejected(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    with pytest.raises(OperatorAppStoreError): initialize_store(db,release_commit_sha="c"*40,implementation_digest=DIGEST)
def test_memory_allowed_only_as_test_store():
    initialize_store(":memory:",release_commit_sha=SHA,implementation_digest=DIGEST)
def test_required_indexes_exist(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    c=sqlite3.connect(db); names={r[1] for r in c.execute("PRAGMA index_list(operator_events)")}; c.close(); assert {"events_occurred","events_type","events_severity","events_snapshot"} <= names
def test_repository_path_rejected(tmp_path):
    with pytest.raises(OperatorAppStoreError): initialize_store(Path(__file__).resolve().parents[1]/"bad.sqlite",release_commit_sha=SHA,implementation_digest=DIGEST)

def test_content_digest_is_deterministic_for_fresh_stores(tmp_path):
    left=tmp_path/"left.sqlite"; right=tmp_path/"right.sqlite"
    initialize_store(left,release_commit_sha=SHA,implementation_digest=DIGEST)
    initialize_store(right,release_commit_sha=SHA,implementation_digest=DIGEST)
    from src.operator_app_store import operator_content_digest
    assert operator_content_digest(left)==operator_content_digest(right)

def test_content_tampering_fails_closed(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    c=sqlite3.connect(db); c.execute("UPDATE schema_metadata SET release_commit_sha=?",("c"*40,)); c.commit(); c.close()
    assert verify_store_integrity(db) is False

def test_unsupported_schema_fails_closed(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    c=sqlite3.connect(db); c.execute("CREATE TABLE unexpected (id TEXT)"); c.commit(); c.close()
    assert verify_store_integrity(db) is False

def test_implementation_and_content_digests_are_distinct(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    identity=inspect_store_identity(db)
    assert identity["implementation_digest"]==DIGEST and identity["content_digest"] != DIGEST
