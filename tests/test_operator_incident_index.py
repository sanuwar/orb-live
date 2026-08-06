from datetime import datetime, timezone
from pathlib import Path
from src.operator_app_store import initialize_store
from src.operator_incident_index import list_incident_index
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
def test_empty_incident_index_is_bounded(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd()))
    assert list_incident_index(db)==((),0)
