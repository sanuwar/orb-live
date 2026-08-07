"""Privacy-safe local application release identity projection."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE,checkpoint_implementation_digest,resolve_head_sha
@dataclass(frozen=True)
class OperatorReleaseInfo:
    schema_version:int; release_sha:str; implementation_digest:str; pinned_file_count:int; application_schema_version:int; read_model_schema_version:int; architecture_snapshot_status:str; build_identity_status:str
    def to_dict(self): return {"schema_version":self.schema_version,"release_sha":self.release_sha,"release_sha_short":self.release_sha[:12],"implementation_digest":self.implementation_digest,"implementation_digest_scope":"SOURCE_CHECKPOINT_CLOSURE","pinned_file_count":self.pinned_file_count,"pinned_file_count_scope":"SOURCE_CHECKPOINT_CLOSURE","application_identity_status":"NOT_ASSERTED_BY_CHECKPOINT_DIGEST","application_schema_version":self.application_schema_version,"read_model_schema_version":self.read_model_schema_version,"architecture_snapshot_status":self.architecture_snapshot_status,"demo_supported":True,"file_fixture_supported":True,"sqlite_supported":True,"build_identity_status":self.build_identity_status,"runtime_authority":"EXTERNAL","read_model_authority":"NON_AUTHORITATIVE","permits_automated_action":False,"live_ready":False,"unattended_ready":False,"blocker_reasons":[] if self.build_identity_status=="VERIFIED" else ["BUILD_IDENTITY_UNAVAILABLE"]}
def read_release_info(root:Path):
    # pinned_file_count was previously hard-coded to 20 here rather than
    # derived from PINNED_IMPLEMENTATION_CLOSURE, unlike release_sha and
    # implementation_digest which were already computed live -- a genuine
    # defect M13 Issue 5 uncovered when the closure grew past 20. Fixed to
    # the same live-derivation convention as its two siblings.
    pinned_file_count=len(PINNED_IMPLEMENTATION_CLOSURE)
    try: sha=resolve_head_sha(root); digest=checkpoint_implementation_digest(root)
    except Exception: return OperatorReleaseInfo(1,"unknown","unknown",pinned_file_count,1,1,"UNKNOWN_FAIL_CLOSED","UNKNOWN_FAIL_CLOSED")
    return OperatorReleaseInfo(1,sha,digest,pinned_file_count,1,1,"PRESENT","VERIFIED")
