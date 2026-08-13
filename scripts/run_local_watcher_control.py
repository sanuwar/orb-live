"""Run the loopback-only, allowlisted morning watcher control service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.local_watcher_control import (
    CONTROL_HOST,
    CONTROL_PORT,
    LocalControlConfig,
    LocalControlError,
    LocalWatcherControlApp,
    MorningWatcherController,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=CONTROL_HOST)
    parser.add_argument("--port", default=CONTROL_PORT, type=int)
    args = parser.parse_args(argv)
    try:
        config = LocalControlConfig(host=args.host, port=args.port).validate()
    except LocalControlError as exc:
        parser.error(str(exc))
    controller = MorningWatcherController()
    app = LocalWatcherControlApp(controller, config)
    print(f"ORB-LIVE local watcher control listening on http://{config.host}:{config.port}", flush=True)
    try:
        with make_server(config.host, config.port, app) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
