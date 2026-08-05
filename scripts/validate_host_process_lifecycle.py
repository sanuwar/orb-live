"""Offline CLI for validating the supervised read-only host lifecycle contract."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.host_process_lifecycle import (  # noqa: E402
    LifecycleCompatibilityError,
    LifecycleError,
    LifecycleIdentityError,
    LifecycleSafetyError,
    HostProcessLifecycleContract,
    validate_lifecycle_contract,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        raw = json.loads(args.contract.read_text(encoding="utf-8"))
        contract = HostProcessLifecycleContract.from_dict(raw)
        validate_lifecycle_contract(contract, repository_root=ROOT)
        print("VALID_FOR_OFFLINE_LIFECYCLE_REVIEW")
        return 0
    except LifecycleIdentityError:
        return 20
    except LifecycleSafetyError:
        return 30
    except LifecycleCompatibilityError:
        return 40
    except (SystemExit, LifecycleError, OSError, ValueError, json.JSONDecodeError):
        return 10
    except Exception:
        return 50


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
