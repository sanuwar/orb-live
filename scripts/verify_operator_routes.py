from src.operator_route_contracts import verify_contracts
if __name__=="__main__":
    rows=verify_contracts(); print("operator-route-contracts: "+str(len(rows))+" GET routes verified")
