from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.operator_route_contracts import construct_operator_application, discover_application_routes, verify_contracts
if __name__=="__main__":
    application=construct_operator_application()
    rows=verify_contracts(application); routes=discover_application_routes(application)
    print("operator-route-contracts: "+str(len(routes))+" framework-derived GET routes verified")
