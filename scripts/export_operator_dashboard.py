"""Export one static, read-only operator dashboard and exit."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.operator_dashboard_export import ConsoleError, ConsoleIdentityError, ConsoleSafetyError, exit_code, export_dashboard


def _as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("--as-of must be ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must be timezone-aware")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a static ORB-LIVE operator dashboard")
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--as-of", required=True, type=_as_of)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--include-json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result, _ = export_dashboard(runtime_root=args.runtime_root, as_of=args.as_of, output_root=args.output_root, include_json=args.include_json)
    except ConsoleSafetyError:
        print(json.dumps({"status": "FAIL_CLOSED_SAFETY_BOUNDARY"}, sort_keys=True))
        return 60
    except (ConsoleIdentityError, ConsoleError):
        print(json.dumps({"status": "FAIL_CLOSED_INCOMPLETE_EVIDENCE"}, sort_keys=True))
        return 60
    print(json.dumps({"status": "EXPORTED_STATIC_READ_ONLY_DASHBOARD", "recommended_operator_action": result.recommended_operator_action.value}, sort_keys=True))
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
