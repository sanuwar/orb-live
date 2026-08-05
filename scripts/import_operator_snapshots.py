from pathlib import Path
from datetime import datetime, timezone
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.operator_snapshot_history import SnapshotHistory
from src.operator_event_timeline import OperatorEventTimeline
from src.operator_incident_export import ADVERSE_EVENT_TYPES, _INCIDENT_TYPE, OperatorIncident
from src.operator_app_store import initialize_store, import_projection_batch
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
p=argparse.ArgumentParser(); p.add_argument('--snapshot-root',required=True); p.add_argument('--database',required=True); p.add_argument('--as-of',required=True); a=p.parse_args()
as_of=datetime.fromisoformat(a.as_of.replace('Z','+00:00'))
if as_of.tzinfo is None: raise SystemExit('as-of must be timezone-aware')
repo=Path.cwd(); history=SnapshotHistory(runtime_root=Path(a.snapshot_root),repository_root=repo); snapshots=history.load(as_of=as_of); events=OperatorEventTimeline(history).build(as_of=as_of)
incidents=[]
for e in events:
    if e.event_type.value not in ADVERSE_EVENT_TYPES: continue
    s=history.get(snapshot_id=e.source_snapshot_id,as_of=as_of).snapshot; typ=_INCIDENT_TYPE[e.event_type.value]
    incidents.append(OperatorIncident(1,'incident-'+e.event_id,e.event_id,typ,e.severity.value,e.occurred_at,e.occurred_at,'ACTIVE',e.source_component,e.headline,e.detail_code,e.blocker_reasons,e.operator_action,(e.source_snapshot_id,),(e.event_id,),s.operational_status,s.supervised_readiness_status,s.evidence_completeness,s.evidence_freshness))
sha=resolve_head_sha(repo); digest=checkpoint_implementation_digest(repo); initialize_store(Path(a.database),release_commit_sha=sha,implementation_digest=digest)
bid=import_projection_batch(Path(a.database),snapshots=snapshots,events=events,incidents=incidents,imported_at=datetime.now(timezone.utc).isoformat(),source_release_commit_sha=sha,source_implementation_digest=digest)
print(f'import_batch_id={bid} snapshots={len(snapshots)} events={len(events)} incidents={len(incidents)}')
