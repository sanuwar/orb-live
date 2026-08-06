from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.operator_route_contracts import verify_contracts
if __name__=="__main__":
    rows=verify_contracts(); print("operator-route-contracts: "+str(len(rows))+" GET routes verified")
