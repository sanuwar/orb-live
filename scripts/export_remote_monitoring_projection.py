"""Export the local sanitized runtime journal through the remote allowlist."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.remote_monitoring_projection import write_remote_projection
from src.remote_monitoring_source import collect_remote_projection
from src.runtime_journal_projection import DEFAULT_HISTORY_PATH, RuntimeProjectionError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Destination for the sanitized remote projection JSON")
    parser.add_argument("--history", default=DEFAULT_HISTORY_PATH, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        projected = collect_remote_projection(history_path=args.history)
        write_remote_projection(projected, args.output)
    except (OSError, RuntimeProjectionError, ValueError):
        print("REMOTE_MONITORING_PROJECTION_BLOCKED", file=sys.stderr)
        return 2
    print("REMOTE_MONITORING_PROJECTION_READY")
    print(f"source_status={projected['sourceStatus']}")
    print(f"sessions={len(projected['sessions'])}")
    print(f"records={len(projected['records'])}")
    print(f"events={len(projected['events'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
