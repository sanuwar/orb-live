"""Append one completed morning-watch artifact to the sanitized automatic history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime_journal_projection import DEFAULT_HISTORY_PATH, RuntimeProjectionError, append_watch_artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch-summary", required=True, type=Path)
    parser.add_argument("--history-path", type=Path, default=DEFAULT_HISTORY_PATH)
    args = parser.parse_args(argv)
    try:
        result = append_watch_artifact(args.watch_summary, history_path=args.history_path)
    except RuntimeProjectionError:
        print(json.dumps({"status": "PROJECTION_REJECTED"}, sort_keys=True))
        return 2
    print(json.dumps({"status": result["status"], "sessions": len(result["sessions"]), "records": len(result["records"]), "events": len(result["events"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
