"""Generate an ORB-LIVE remote-monitoring password hash offline."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.remote_monitoring_service import RemoteMonitoringError, hash_password


def main() -> int:
    first = getpass.getpass("Remote monitoring password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        print("PASSWORD_CONFIRMATION_MISMATCH", file=sys.stderr)
        return 2
    try:
        print(hash_password(first))
    except RemoteMonitoringError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
