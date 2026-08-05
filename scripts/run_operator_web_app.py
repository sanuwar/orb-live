"""Launch the local read-only operator web application."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig, WebAppConfigError


def _as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("--as-of must be ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must be timezone-aware")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local read-only ORB-LIVE operator app")
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--as-of", required=True, type=_as_of)
    parser.add_argument("--refresh-interval", default=0, type=int)
    args = parser.parse_args(argv)
    try:
        app = OperatorWebApp(OperatorWebAppConfig(runtime_root=args.runtime_root, default_as_of=args.as_of, host=args.host, port=args.port, refresh_interval=args.refresh_interval))
    except WebAppConfigError as exc:
        parser.error(str(exc))
    print(f"ORB-LIVE read-only operator app listening on http://{args.host}:{args.port}", flush=True)
    try:
        with make_server(args.host, args.port, app) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
