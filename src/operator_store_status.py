"""Privacy-safe, read-only status projection for the disposable operator store."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from src.operator_app_store import OperatorAppStoreError, inspect_store_content, inspect_store_identity, operator_content_digest, verify_store_integrity
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha

@dataclass(frozen=True)
class OperatorStoreStatus:
    status: str
    schema_version: int | None
    release_commit_sha_short: str | None
    implementation_digest_short: str | None
    import_batch_count: int
    snapshot_count: int
    event_count: int
    incident_count: int
    evidence_link_count: int
    newest_imported_at: str | None
    newest_snapshot_at: str | None
    newest_event_at: str | None
    newest_incident_at: str | None
    integrity_status: str
    identity_status: str
    database_mode: str = "READ_MODEL_ONLY"
    runtime_authority: str = "EXTERNAL"
    rebuildable: bool = True
    permits_automated_action: bool = False
    live_ready: bool = False
    unattended_ready: bool = False
    blocker_reasons: tuple[str, ...] = ()
    generated_as_of: str | None = None
    database_content_digest_short: str | None = None
    def to_dict(self):
        return {"status":self.status,"schema_version":self.schema_version,"release_commit_sha_short":self.release_commit_sha_short,"implementation_digest_short":self.implementation_digest_short,"database_content_digest_short":self.database_content_digest_short,"implementation_digest_scope":"SOURCE_CHECKPOINT_CLOSURE","database_content_digest_scope":"OPERATOR_READ_MODEL_CANONICAL_CONTENT","import_batch_count":self.import_batch_count,"snapshot_count":self.snapshot_count,"event_count":self.event_count,"incident_count":self.incident_count,"evidence_link_count":self.evidence_link_count,"newest_imported_at":self.newest_imported_at,"newest_snapshot_at":self.newest_snapshot_at,"newest_event_at":self.newest_event_at,"newest_incident_at":self.newest_incident_at,"integrity_status":self.integrity_status,"identity_status":self.identity_status,"database_mode":self.database_mode,"runtime_authority":self.runtime_authority,"rebuildable":self.rebuildable,"permits_automated_action":self.permits_automated_action,"live_ready":self.live_ready,"unattended_ready":self.unattended_ready,"blocker_reasons":list(self.blocker_reasons),"generated_as_of":self.generated_as_of}

def read_store_status(database: Path, *, generated_as_of: str | None = None) -> OperatorStoreStatus:
    try:
        identity=inspect_store_identity(database); expected_sha=resolve_head_sha(Path.cwd()); expected_digest=checkpoint_implementation_digest(Path.cwd())
        if identity.get("schema_version") != 1: raise OperatorAppStoreError("schema")
        identity_ok=identity.get("release_commit_sha")==expected_sha and identity.get("implementation_digest")==expected_digest
        integrity_ok=verify_store_integrity(database); counts=inspect_store_content(database); content_digest=operator_content_digest(database) if integrity_ok else None
        blockers=[]
        if not identity_ok: blockers.append("IDENTITY_MISMATCH")
        if not integrity_ok: blockers.append("INTEGRITY_FAILURE")
        if blockers: state="INVALID"
        elif counts["import_batch_count"]==0 and counts["snapshot_count"]==0 and counts["event_count"]==0 and counts["incident_count"]==0: state="EMPTY"
        elif not all(counts[k] > 0 for k in ("snapshot_count","event_count","incident_count")): state="DEGRADED"; blockers.append("INCOMPLETE_PROJECTION")
        else: state="HEALTHY"
        return OperatorStoreStatus(state,1,expected_sha[:12],expected_digest[:12],integrity_status="PASS" if integrity_ok else "FAIL",identity_status="MATCH" if identity_ok else "MISMATCH",blocker_reasons=tuple(blockers),generated_as_of=generated_as_of,database_content_digest_short=content_digest[:12] if content_digest else None,**counts)
    except Exception:
        return OperatorStoreStatus("UNKNOWN_FAIL_CLOSED",None,None,None,0,0,0,0,0,None,None,None,None,"UNKNOWN","UNKNOWN",blocker_reasons=("STATUS_UNAVAILABLE",),generated_as_of=generated_as_of)
