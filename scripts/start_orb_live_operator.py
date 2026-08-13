"""Start the fixed local ORB-LIVE operator console and open Operations."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.local_operator_launcher import LauncherError, launch_operator_console


def main(argv: list[str] | None = None) -> int:
    if argv:
        print("OPERATOR_LAUNCH_FAILED: this launcher accepts no arguments")
        return 2
    try:
        result = launch_operator_console()
    except LauncherError as exc:
        print(f"OPERATOR_LAUNCH_FAILED: {exc}")
        return 1
    print("ORB-LIVE OPERATOR CONSOLE")
    print(f"Local control service: {result.control_service} (127.0.0.1:8766)")
    print(f"Frontend: {result.frontend} (127.0.0.1:5173)")
    print(f"Operations: {result.browser_url}")
    print("LOCAL CONTROL ONLY")
    print("ORB_LIVE_OPERATOR_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
