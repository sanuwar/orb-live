"""Create and launch the loopback-only local operator demo."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from wsgiref.simple_server import make_server
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.create_operator_demo import MARKER, TOOL_ID, _as_of, create_demo
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig, WebAppConfigError
def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--output-root",required=True,type=Path); p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",default=8765,type=int); p.add_argument("--as-of",required=True); a=p.parse_args(argv)
    try:
        if a.host!="127.0.0.1": raise ValueError("demo binds only to loopback")
        if (a.output_root/MARKER).is_file():
            marker=json.loads((a.output_root/MARKER).read_text(encoding="utf-8"))
            if marker.get("tool_id") != TOOL_ID or not (a.output_root/"operator_app.sqlite").is_file(): raise ValueError("demo root is invalid")
            data={"root":str(a.output_root.resolve()),"database":str((a.output_root/"operator_app.sqlite").resolve()),"expected_content_digest":marker.get("expected_content_digest")}
        else: data=create_demo(a.output_root,_as_of(a.as_of))
        app=OperatorWebApp(OperatorWebAppConfig(runtime_root=Path(data["root"]),database_path=Path(data["database"]),backend_mode="SQLITE_READ_MODEL",expected_content_digest=data.get("expected_content_digest"),default_as_of=_as_of(a.as_of),host=a.host,port=a.port))
    except (OSError,ValueError,WebAppConfigError) as exc: p.error(str(exc))
    print(f"ORB-LIVE demo: http://{a.host}:{a.port}/ (READ-ONLY)",flush=True)
    try:
        with make_server(a.host,a.port,app) as server: server.serve_forever()
    except KeyboardInterrupt: return 0
    return 0
if __name__=="__main__": raise SystemExit(main())
