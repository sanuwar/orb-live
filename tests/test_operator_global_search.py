from datetime import datetime,timezone
from pathlib import Path
import pytest
from src.operator_app_store import initialize_store,OperatorAppStoreError
from src.operator_global_search import search_store
from src.read_only_authorization_manifest import resolve_head_sha,checkpoint_implementation_digest
def db(tmp_path):
 p=tmp_path/"operator.sqlite"; initialize_store(p,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd())); return p
def test_empty_search_is_bounded_and_read_only(tmp_path):
 p=db(tmp_path); before=p.read_bytes(); assert search_store(p,"status")== ((),0); assert p.read_bytes()==before
def test_search_validation_is_allowlisted(tmp_path):
 p=db(tmp_path)
 with pytest.raises(OperatorAppStoreError): search_store(p,"x")
 with pytest.raises(OperatorAppStoreError): search_store(p,"status",result_type="SQL")
