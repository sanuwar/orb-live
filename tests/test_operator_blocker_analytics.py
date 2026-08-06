from datetime import datetime,timezone
from pathlib import Path
from src.operator_app_store import initialize_store
from src.operator_blocker_analytics import summarize_blockers
from src.read_only_authorization_manifest import checkpoint_implementation_digest,resolve_head_sha
def test_empty_blocker_analytics_is_closed(tmp_path):
 db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd())); out=summarize_blockers(db,datetime(2026,8,5,16,tzinfo=timezone.utc).isoformat()).to_dict(); assert out["unique_blocker_count"]==0 and out["live_ready"] is False
