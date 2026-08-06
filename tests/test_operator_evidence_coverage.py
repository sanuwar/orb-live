from datetime import datetime,timezone
from pathlib import Path
from src.operator_app_store import initialize_store
from src.operator_evidence_coverage import read_coverage
from src.read_only_authorization_manifest import checkpoint_implementation_digest,resolve_head_sha
def test_empty_coverage_keeps_missing_categories_visible(tmp_path):
 db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd())); out=read_coverage(db,datetime(2026,8,5,16,tzinfo=timezone.utc).isoformat()).to_dict(); assert out["coverage_status"]=="PARTIAL_EVIDENCE_COVERAGE" and out["missing_category_count"]>0 and out["live_ready"] is False
