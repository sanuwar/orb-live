from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.operator_route_contracts import discover_application_routes, verify_contracts
if __name__=="__main__":
    rows=verify_contracts(); print("operator-route-contracts: "+str(len(discover_application_routes()))+" GET routes verified")
