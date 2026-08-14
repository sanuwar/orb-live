"""Run the authenticated read-only monitoring service behind an HTTPS proxy."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.remote_monitoring_source import collect_remote_projection
from src.runtime_journal_projection import DEFAULT_HISTORY_PATH
from src.remote_monitoring_service import (
    REMOTE_HOST,
    REMOTE_PORT,
    PasswordVerifier,
    RemoteMonitoringApp,
    RemoteMonitoringConfig,
    RemoteMonitoringError,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", default=DEFAULT_HISTORY_PATH, type=Path, help="Local sanitized runtime journal")
    parser.add_argument("--static-root", default=ROOT / "site" / "dist-remote", type=Path)
    parser.add_argument("--host", default=REMOTE_HOST)
    parser.add_argument("--port", default=REMOTE_PORT, type=int)
    parser.add_argument("--dev-insecure-cookie", action="store_true", help="Allow an HTTP cookie only on this loopback development service")
    args = parser.parse_args(argv)
    password_hash = os.environ.get("ORB_REMOTE_PASSWORD_HASH", "")
    origin = os.environ.get("ORB_REMOTE_ORIGIN", f"http://127.0.0.1:{args.port}" if args.dev_insecure_cookie else "")
    try:
        config = RemoteMonitoringConfig(
            projection_path=args.history,
            static_root=args.static_root,
            expected_origin=origin,
            host=args.host,
            port=args.port,
            secure_cookie=not args.dev_insecure_cookie,
        ).validate()
        app = RemoteMonitoringApp(config, PasswordVerifier(password_hash), projection_provider=lambda: collect_remote_projection(history_path=args.history))
    except RemoteMonitoringError as exc:
        parser.error(str(exc))
    print(f"ORB-LIVE remote read-only monitoring listening behind proxy on http://{config.host}:{config.port}", flush=True)
    try:
        with make_server(config.host, config.port, app) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
