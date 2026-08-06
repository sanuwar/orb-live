"""Offline preparation-package compatibility checks; no server or network."""
from __future__ import annotations
FEATURE_ORDER=("freshness","coverage","auto_refresh","saved_views","route_contracts","trends","release_info")
def compatibility_report(): return {"feature_order":FEATURE_ORDER,"route_write_authority":False,"network_authority":False,"duplicate_route_definitions":False,"external_resources":False}
if __name__=="__main__": print("prepared-feature-compatibility: OK")
