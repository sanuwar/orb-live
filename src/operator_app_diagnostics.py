"""Privacy-safe diagnostics for the local, read-only operator application."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from src.operator_app_store import OperatorAppStoreError, inspect_store_identity, verify_store_integrity
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha

@dataclass(frozen=True)
class OperatorAppDiagnostics:
    schema_version:int; generated_as_of:str; application_status:str; application_mode:str; backend_mode:str; schema_status:str; identity_status:str; integrity_status:str
    current_projection_available:bool; history_available:bool; timeline_available:bool; incidents_available:bool; imports_available:bool; search_available:bool; data_status_available:bool; demo_mode:bool; loopback_only:bool; debug_enabled:bool; external_resources_enabled:bool
    runtime_authority:str; read_model_authority:str; read_model_rebuildable:bool; permits_automated_action:bool; live_ready:bool; unattended_ready:bool; blocker_reasons:tuple[str,...]
    def to_dict(self):
        return {"schema_version":self.schema_version,"generated_as_of":self.generated_as_of,"application_status":self.application_status,"application_mode":self.application_mode,"backend_mode":self.backend_mode,"schema_status":self.schema_status,"identity_status":self.identity_status,"integrity_status":self.integrity_status,"current_projection_available":self.current_projection_available,"history_available":self.history_available,"timeline_available":self.timeline_available,"incidents_available":self.incidents_available,"imports_available":self.imports_available,"search_available":self.search_available,"data_status_available":self.data_status_available,"demo_mode":self.demo_mode,"loopback_only":self.loopback_only,"debug_enabled":False,"external_resources_enabled":False,"runtime_authority":"EXTERNAL","read_model_authority":"NON_AUTHORITATIVE","read_model_rebuildable":True,"permits_automated_action":False,"live_ready":False,"unattended_ready":False,"blocker_reasons":list(self.blocker_reasons)}

def read_app_diagnostics(config, generated_as_of:str) -> OperatorAppDiagnostics:
    blockers=[]; sqlite=config.backend_mode=="SQLITE_READ_MODEL"; schema=identity=integrity="NOT_APPLICABLE"
    if sqlite:
        try:
            identity_data=inspect_store_identity(config.database_path); expected_sha=resolve_head_sha(Path(__file__).resolve().parents[1]); expected_digest=checkpoint_implementation_digest(Path(__file__).resolve().parents[1])
            schema="VALID" if identity_data.get("schema_version")==1 else "INVALID"; identity="VALID" if identity_data.get("release_commit_sha")==expected_sha and identity_data.get("implementation_digest")==expected_digest else "INVALID"; integrity="PASS" if verify_store_integrity(config.database_path) else "FAIL"
        except (OperatorAppStoreError,OSError,KeyError): schema=identity=integrity="UNKNOWN_FAIL_CLOSED"
        if schema!="VALID": blockers.append("SCHEMA_UNAVAILABLE")
        if identity!="VALID": blockers.append("IDENTITY_UNAVAILABLE")
        if integrity!="PASS": blockers.append("INTEGRITY_UNAVAILABLE")
    status="HEALTHY_LOCAL_APP" if not blockers else "DEGRADED_LOCAL_APP"
    if sqlite and blockers: status="INVALID_CONFIGURATION"
    return OperatorAppDiagnostics(1,generated_as_of,status,"LOCAL_READ_ONLY",config.backend_mode,schema,identity,integrity,True,True,True,True,sqlite,sqlite,True,Path(config.runtime_root, ".operator_demo_owned").is_file(),config.host=="127.0.0.1",False,False,"EXTERNAL","NON_AUTHORITATIVE",True,False,False,False,tuple(blockers))
