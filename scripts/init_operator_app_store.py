from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.operator_app_store import initialize_store
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
p=argparse.ArgumentParser(); p.add_argument('--database',required=True); a=p.parse_args()
initialize_store(Path(a.database), release_commit_sha=resolve_head_sha(Path.cwd()), implementation_digest=checkpoint_implementation_digest(Path.cwd()))
print('initialized operator read model')
