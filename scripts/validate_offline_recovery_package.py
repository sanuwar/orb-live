"""Offline CLI for validating an isolated recovery package."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.offline_recovery_package import (  # noqa: E402
    RecoveryIdentityError,
    RecoveryIntegrityError,
    RecoveryPackageError,
    RecoveryPathError,
    RecoveryRestoreError,
    validate_recovery_package,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--restore-root", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        validate_recovery_package(package_path=args.package, restore_root=args.restore_root, repository_root=ROOT)
        print("VALID_FOR_OFFLINE_RECOVERY_REVIEW")
        return 0
    except RecoveryIdentityError:
        return 20
    except RecoveryIntegrityError:
        return 30
    except RecoveryRestoreError:
        return 40
    except RecoveryPathError:
        return 50
    except (SystemExit, RecoveryPackageError, OSError, ValueError):
        return 10
    except Exception:
        return 60


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
