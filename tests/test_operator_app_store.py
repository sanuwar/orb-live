from pathlib import Path
import sqlite3
import pytest
from scripts.create_operator_demo import create_demo
from src.operator_app_store import (CONTENT_MISMATCH, CONTENT_UNVERIFIED, CONTENT_VERIFIED, IDENTITY_MISMATCH, OperatorAppStoreError, _canonical_content_digest, _row_digest, compute_operator_content_digest, initialize_store, inspect_store_identity, read_model_integrity_status, verify_store_integrity)

SHA="a"*40; DIGEST="b"*64
def test_initialize_and_identity(tmp_path):
    db=tmp_path/"operator.sqlite"; expected=initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST); initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    assert inspect_store_identity(db)["schema_version"]==1 and verify_store_integrity(db,expected)
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
    assert verify_store_integrity(db,"b"*64) is False

def test_unsupported_schema_fails_closed(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    c=sqlite3.connect(db); c.execute("CREATE TABLE unexpected (id TEXT)"); c.commit(); c.close()
    assert verify_store_integrity(db,"b"*64) is False

def test_implementation_and_content_digests_are_distinct(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=SHA,implementation_digest=DIGEST)
    identity=inspect_store_identity(db)
    assert identity["implementation_digest"]==DIGEST and identity["content_digest"] != DIGEST

def _demo(tmp_path):
    from datetime import datetime, timezone
    return create_demo(tmp_path/"demo",datetime(2026,8,5,16,tzinfo=timezone.utc))

def _coordinated_tamper(database, table, key, column, value):
    c=sqlite3.connect(database); c.row_factory=sqlite3.Row
    c.execute(f"UPDATE {table} SET {column}=? WHERE rowid=(SELECT rowid FROM {table} LIMIT 1)",(value,))
    row=dict(c.execute(f"SELECT * FROM {table} WHERE rowid=(SELECT rowid FROM {table} LIMIT 1)").fetchone())
    if table in {"operator_snapshots","operator_events","operator_incidents"}:
        identity=row[key]; c.execute(f"UPDATE {table} SET canonical_digest=? WHERE {key}=?",(_row_digest(table,row),identity))
    elif table=="import_batches":
        identity=row[key]; c.execute(f"UPDATE {table} SET batch_digest=? WHERE {key}=?",(_row_digest(table,row),identity))
    c.commit(); c.close()
    c=sqlite3.connect(database); c.execute("UPDATE schema_metadata SET content_digest=?",(compute_operator_content_digest(database),)); c.commit(); c.close()

@pytest.mark.parametrize("table,key,column,value",[("operator_events","event_id","headline","coordinated event"),("operator_snapshots","snapshot_id","operational_status","COORDINATED"),("operator_incidents","incident_id","headline","coordinated incident"),("import_batches","import_batch_id","source_count",999)])
def test_coordinated_content_and_stored_digest_rewrite_fails(tmp_path,table,key,column,value):
    demo=_demo(tmp_path); database=Path(demo["database"]); _coordinated_tamper(database,table,key,column,value)
    assert read_model_integrity_status(database,expected_content_digest=demo["expected_content_digest"])==CONTENT_MISMATCH
    assert verify_store_integrity(database,demo["expected_content_digest"]) is False

def test_evidence_link_tampering_fails_with_rewritten_content_digest(tmp_path):
    demo=_demo(tmp_path); database=Path(demo["database"]); c=sqlite3.connect(database); c.execute("UPDATE incident_evidence_links SET evidence_id='tampered-evidence' WHERE rowid=(SELECT rowid FROM incident_evidence_links LIMIT 1)"); c.commit(); c.close(); c=sqlite3.connect(database); c.execute("UPDATE schema_metadata SET content_digest=?",(compute_operator_content_digest(database),)); c.commit(); c.close()
    assert verify_store_integrity(database,demo["expected_content_digest"]) is False

def test_missing_and_malformed_expected_digest_never_verify(tmp_path):
    demo=_demo(tmp_path); database=Path(demo["database"])
    assert read_model_integrity_status(database) == CONTENT_UNVERIFIED
    assert read_model_integrity_status(database,expected_content_digest="bad") == CONTENT_MISMATCH
    assert verify_store_integrity(database,"0"*64) is False

def test_release_binding_and_legacy_checkpoint_cannot_authorize_content(tmp_path):
    demo=_demo(tmp_path); database=Path(demo["database"])
    assert read_model_integrity_status(database,expected_content_digest=demo["expected_content_digest"],expected_release_commit_sha="wrong") == IDENTITY_MISMATCH
    assert verify_store_integrity(database,inspect_store_identity(database)["implementation_digest"]) is False

def test_equivalent_logical_stores_share_computed_digest(tmp_path):
    left=_demo(tmp_path/"left"); right=_demo(tmp_path/"right")
    assert compute_operator_content_digest(left["database"]) == compute_operator_content_digest(right["database"])

def test_integrity_validation_is_read_only(tmp_path):
    demo=_demo(tmp_path); database=Path(demo["database"]); before=database.read_bytes(); assert verify_store_integrity(database,demo["expected_content_digest"]); assert database.read_bytes()==before
