"""CLI entry point for the bounded M15 live-shadow session."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.live_shadow_session import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))