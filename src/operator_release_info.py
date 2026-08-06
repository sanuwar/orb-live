"""Privacy-safe local application release identity projection."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from src.read_only_authorization_manifest import checkpoint_implementation_digest,resolve_head_sha
@dataclass(frozen=True)
class OperatorReleaseInfo:
    schema_version:int; release_sha:str; implementation_digest:str; pinned_file_count:int; application_schema_version:int; read_model_schema_version:int; architecture_snapshot_status:str; build_identity_status:str
    def to_dict(self): return {"schema_version":self.schema_version,"release_sha":self.release_sha,"release_sha_short":self.release_sha[:12],"implementation_digest":self.implementation_digest,"pinned_file_count":self.pinned_file_count,"application_schema_version":self.application_schema_version,"read_model_schema_version":self.read_model_schema_version,"architecture_snapshot_status":self.architecture_snapshot_status,"demo_supported":True,"file_fixture_supported":True,"sqlite_supported":True,"build_identity_status":self.build_identity_status,"runtime_authority":"EXTERNAL","read_model_authority":"NON_AUTHORITATIVE","permits_automated_action":False,"live_ready":False,"unattended_ready":False,"blocker_reasons":[] if self.build_identity_status=="VERIFIED" else ["BUILD_IDENTITY_UNAVAILABLE"]}
def read_release_info(root:Path):
    try: sha=resolve_head_sha(root); digest=checkpoint_implementation_digest(root)
    except Exception: return OperatorReleaseInfo(1,"unknown","unknown",20,1,1,"UNKNOWN_FAIL_CLOSED","UNKNOWN_FAIL_CLOSED")
    return OperatorReleaseInfo(1,sha,digest,20,1,1,"PRESENT","VERIFIED")
