"""Create deterministic synthetic data for the local read-only operator app."""
from __future__ import annotations
import argparse, hashlib, json, shutil, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.operator_app_store import compute_operator_content_digest, import_projection_batch, initialize_store, verify_store_integrity
from src.operator_event_timeline import OperatorEventTimeline
from src.operator_incident_export import ADVERSE_EVENT_TYPES, OperatorIncident, _INCIDENT_TYPE
from src.operator_snapshot_history import SnapshotHistory
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
from src.supervised_operator_console import load_console_snapshot

TOOL_ID = "orb-live.operator-demo.v1"
MARKER = ".operator_demo_owned"

class _ImportSnapshot:
    def __init__(self, item): self.item=item
    def to_dict(self):
        data=self.item.to_dict(); raw=self.item.snapshot.to_dict(); snapshot_id=data["snapshot_id"]; data.update(raw); data["snapshot_id"]=snapshot_id; return data

def _as_of(value: str) -> datetime:
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: raise ValueError("as-of must be timezone-aware") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError("as-of must be timezone-aware")
    return parsed

def _safe_root(root: Path, replace: bool) -> Path:
    root = Path(root)
    if root.exists() and root.is_symlink(): raise ValueError("demo root symlink is unsafe")
    if root.exists() and not root.is_dir(): raise ValueError("demo root is not a directory")
    if root.exists() and any(root.iterdir()):
        marker = root / MARKER
        if not replace or not marker.is_file(): raise ValueError("existing demo root is not owned")
        try: data=json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError): raise ValueError("demo ownership marker is invalid") from None
        if data.get("tool_id") != TOOL_ID or set(data) != {"tool_id","schema_version","created_at","release_sha","implementation_digest","expected_content_digest"}:
            raise ValueError("demo ownership marker is invalid")
        unexpected=[p for p in root.iterdir() if p.name != MARKER]
        if any(p.is_symlink() for p in unexpected): raise ValueError("demo root contains an unsafe symlink")
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()

def _fixture_files(root: Path, sha: str, digest: str) -> None:
    (root / "config").mkdir(); (root / "evidence").mkdir(); (root / "history").mkdir()
    profile=json.loads((Path(__file__).resolve().parents[1]/"examples/read-only-deployment/valid-supervised-read-only-profile.json").read_text(encoding="utf-8")); profile.update(release_commit_sha=sha,implementation_digest=digest)
    lifecycle=json.loads((Path(__file__).resolve().parents[1]/"examples/read-only-deployment/host-process-lifecycle.json").read_text(encoding="utf-8")); lifecycle.update(release_commit_sha=sha,implementation_digest=digest)
    (root/"config/deployment-profile.json").write_text(json.dumps(profile,sort_keys=True),encoding="utf-8")
    (root/"config/lifecycle-contract.json").write_text(json.dumps(lifecycle,sort_keys=True),encoding="utf-8")

def create_demo(output_root: Path, as_of: datetime, *, replace_demo=False) -> dict:
    root=_safe_root(output_root,replace_demo); sha=resolve_head_sha(Path.cwd()); digest=checkpoint_implementation_digest(Path.cwd()); _fixture_files(root,sha,digest)
    base={"schema_version":1,"evidence_as_of":"","operating_mode":"SUPERVISED_READ_ONLY","scheduler_status":"IDLE","runner_status":"BOUNDED_IDLE","suspension_status":"CLEAR","heartbeat_status":"CURRENT","alert_status":"CONFIRMED","acknowledgment_status":"COMPLETE","recovery_status":"NOT_AVAILABLE","operational_status":"OFFLINE_HEALTHY","supervised_readiness_status":"REVIEW_ONLY","blocker_reasons":[],"strategy_enabled":False,"activation_approvals_present":False,"approved_protection_state_count":0,"protection_grounded":False,"activation_approved":False,"permits_automated_action":False,"live_ready":False,"unattended_ready":False,"evidence_completeness":"COMPLETE","evidence_freshness":"CURRENT","source_component_statuses":{k:"AVAILABLE" for k in ("scheduler","runner","suspension","heartbeat","alerts","acknowledgment","operational_status","readiness")},"release_commit_sha":sha,"implementation_digest":digest}
    states=[({},"COMPLETE"),( {"heartbeat_status":"STALE","blocker_reasons":["STALE_HEARTBEAT"]},"COMPLETE"),( {"suspension_status":"ACTIVE","operational_status":"SUSPENDED","blocker_reasons":["ACTIVE_SUSPENSION"]},"COMPLETE"),( {"acknowledgment_status":"REVIEW_REQUIRED","supervised_readiness_status":"REVIEW_REQUIRED","blocker_reasons":["OPERATOR_REVIEW_REQUIRED"]},"COMPLETE"),( {"recovery_status":"AMBIGUOUS","blocker_reasons":["AMBIGUOUS_RECOVERY"]},"COMPLETE"),( {"recovery_status":"OBSERVED","blocker_reasons":["OPERATOR_REVIEW_REQUIRED"]},"COMPLETE")]
    snapshots=[]
    for index,(changes,_) in enumerate(states):
        raw=dict(base); raw.update(changes); raw["evidence_as_of"]=(as_of-timedelta(minutes=(5-index)*10)).isoformat()
        (root/"evidence/operator-console-evidence.json").write_text(json.dumps(raw,sort_keys=True),encoding="utf-8")
        result=load_console_snapshot(runtime_root=root,as_of=as_of); content=result.serialize().encode(); ident=hashlib.sha256(content).hexdigest(); (root/"history"/f"snapshot-{ident}.json").write_bytes(content); snapshots.append(result)
    history=SnapshotHistory(runtime_root=root,repository_root=Path.cwd()); events=OperatorEventTimeline(history).build(as_of=as_of); incidents=[]
    for event in events:
        if event.event_type.value in ADVERSE_EVENT_TYPES:
            snap=history.get(snapshot_id=event.source_snapshot_id,as_of=as_of).snapshot; incidents.append(OperatorIncident(1,"incident-"+event.event_id,event.event_id,_INCIDENT_TYPE[event.event_type.value],event.severity.value,event.occurred_at,event.occurred_at,"ACTIVE",event.source_component,event.headline,event.detail_code,event.blocker_reasons,event.operator_action,(event.source_snapshot_id,),(event.event_id,),snap.operational_status,snap.supervised_readiness_status,snap.evidence_completeness,snap.evidence_freshness))
    db=root/"operator_app.sqlite"; initialize_store(db,release_commit_sha=sha,implementation_digest=digest); batch=import_projection_batch(db,snapshots=tuple(_ImportSnapshot(x) for x in history.load(as_of=as_of)),events=events,incidents=incidents,imported_at=as_of.isoformat(),source_release_commit_sha=sha,source_implementation_digest=digest)
    expected_content_digest=compute_operator_content_digest(db)
    if not verify_store_integrity(db,expected_content_digest): raise ValueError("demo store integrity failed")
    marker={"tool_id":TOOL_ID,"schema_version":1,"created_at":as_of.isoformat(),"release_sha":sha,"implementation_digest":digest,"expected_content_digest":expected_content_digest}; (root/MARKER).write_text(json.dumps(marker,sort_keys=True)+"\n",encoding="utf-8")
    return {"root":str(root),"database":str(db),"snapshot_count":len(snapshots),"event_count":len(events),"incident_count":len(incidents),"import_batch_id":batch,"expected_content_digest":expected_content_digest}

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--output-root",required=True,type=Path); p.add_argument("--as-of",required=True); p.add_argument("--replace-demo",action="store_true"); a=p.parse_args(argv)
    try: print(json.dumps(create_demo(a.output_root,_as_of(a.as_of),replace_demo=a.replace_demo),sort_keys=True))
    except (OSError,ValueError) as exc: p.error(str(exc))
    return 0
if __name__=="__main__": raise SystemExit(main())
