"""Read-only, privacy-safe import provenance projection."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from src.operator_app_store import OperatorAppStoreError, get_import_batch, inspect_store_identity, list_import_batches, verify_store_integrity
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
CONSISTENCY_VALUES=frozenset({"CONSISTENT","COUNT_MISMATCH","IDENTITY_MISMATCH","RELATIONSHIP_MISMATCH","INTEGRITY_FAILURE","UNKNOWN_FAIL_CLOSED"})
@dataclass(frozen=True)
class OperatorImportBatch:
    schema_version:int; import_batch_id:str; imported_at:str; source_release_commit_sha_short:str; source_implementation_digest_short:str; source_count:int; accepted_count:int; rejected_count:int; snapshot_count:int; event_count:int; incident_count:int; evidence_link_count:int; batch_digest_short:str; consistency_status:str; blocker_reasons:tuple[str,...]
    def to_dict(self): return {"schema_version":self.schema_version,"import_batch_id":self.import_batch_id,"imported_at":self.imported_at,"source_release_commit_sha_short":self.source_release_commit_sha_short,"source_implementation_digest_short":self.source_implementation_digest_short,"source_count":self.source_count,"accepted_count":self.accepted_count,"rejected_count":self.rejected_count,"snapshot_count":self.snapshot_count,"event_count":self.event_count,"incident_count":self.incident_count,"evidence_link_count":self.evidence_link_count,"batch_digest_short":self.batch_digest_short,"consistency_status":self.consistency_status,"blocker_reasons":list(self.blocker_reasons),"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def _project(d):
    try:
        identity=inspect_store_identity(d["_database"]); sha=resolve_head_sha(Path.cwd()); digest=checkpoint_implementation_digest(Path.cwd()); blockers=[]
        if identity.get("release_commit_sha")!=sha or d["source_release_commit_sha"]!=sha: blockers.append("IDENTITY_MISMATCH")
        if identity.get("implementation_digest")!=digest or d["source_implementation_digest"]!=digest: blockers.append("IDENTITY_MISMATCH")
        if d["source_count"] != d["accepted_count"] + d["rejected_count"]: blockers.append("COUNT_MISMATCH")
        if d["snapshot_count"]!=d["accepted_count"] or (d["event_count"] and d["event_count"]<0) or (d["incident_count"] and d["incident_count"]<0): blockers.append("RELATIONSHIP_MISMATCH")
        if not verify_store_integrity(d["_database"]): blockers.append("INTEGRITY_FAILURE")
        state=blockers[0] if blockers else "CONSISTENT"
        return OperatorImportBatch(1,d["import_batch_id"],d["imported_at"],d["source_release_commit_sha"][:12],d["source_implementation_digest"][:12],d["source_count"],d["accepted_count"],d["rejected_count"],d["snapshot_count"],d["event_count"],d["incident_count"],d["evidence_link_count"],d["batch_digest"][:12],state,tuple(blockers))
    except Exception:return OperatorImportBatch(1,str(d.get("import_batch_id","unknown")),str(d.get("imported_at","unknown")),"unknown","unknown",0,0,0,0,0,0,0,"unknown","UNKNOWN_FAIL_CLOSED",("CONSISTENCY_UNAVAILABLE",))
def list_operator_imports(database, *, page=1, page_size=25):
    rows,total=list_import_batches(database,page=page,page_size=page_size); out=[]
    for row in rows: row["_database"]=database; detail=get_import_batch(database,row["import_batch_id"]); detail["_database"]=database; out.append(_project(detail))
    return tuple(out),total
def get_operator_import(database, batch_id):
    if not re.fullmatch(r"import-[0-9a-f]{64}",batch_id): raise OperatorAppStoreError("record not found")
    d=get_import_batch(database,batch_id); d["_database"]=database; return _project(d)
