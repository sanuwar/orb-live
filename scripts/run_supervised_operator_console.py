"""Render one offline supervised operator snapshot and exit."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.supervised_operator_console import ConsoleError, ConsoleIdentityError, ConsoleSafetyError, load_console_snapshot, render_text


def _as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("--as-of must be ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must be timezone-aware")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one read-only ORB-LIVE operator snapshot")
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--as-of", required=True, type=_as_of)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        result = load_console_snapshot(runtime_root=args.runtime_root, as_of=args.as_of)
    except SystemExit:
        raise
    except ConsoleSafetyError:
        print(json.dumps({"status": "FAIL_CLOSED_SAFETY_BOUNDARY"}, sort_keys=True))
        return 60
    except (ConsoleIdentityError, ConsoleError):
        print(json.dumps({"status": "FAIL_CLOSED_INCOMPLETE_EVIDENCE"}, sort_keys=True))
        return 60
    print(result.serialize() if args.format == "json" else render_text(result), end="")
    action = result.recommended_operator_action.value
    return {"NO_ACTION_OFFLINE_STATE_HEALTHY": 0, "REVIEW_ACTIVE_SUSPENSION": 20, "REVIEW_AMBIGUOUS_RECOVERY": 50, "REVIEW_STALE_HEARTBEAT": 30, "CONFIRM_ALERT_DELIVERY": 40, "COMPLETE_OPERATOR_REVIEW": 10, "VERIFY_DEPLOYMENT_CONFIGURATION": 60, "FAIL_CLOSED_INCOMPLETE_EVIDENCE": 60}[action]


if __name__ == "__main__":
    sys.exit(main())
