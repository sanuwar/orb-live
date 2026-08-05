"""Offline CLI for validating a supervised read-only deployment profile."""

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

from src.read_only_deployment_profile import (
    ProfileError,
    ProfileIdentityError,
    ProfilePathError,
    ProfileSafetyError,
    ReadOnlyDeploymentProfile,
    validate_profile,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        raw = json.loads(args.profile.read_text(encoding="utf-8"))
        profile = ReadOnlyDeploymentProfile.from_dict(raw)
        validate_profile(profile, repository_root=Path(__file__).resolve().parents[1])
        print("VALID_FOR_OFFLINE_CONFIGURATION_REVIEW")
        return 0
    except ProfileIdentityError:
        return 20
    except ProfileSafetyError:
        return 30
    except ProfilePathError:
        return 40
    except (SystemExit, ProfileError, OSError, ValueError, json.JSONDecodeError):
        return 10
    except Exception:
        return 50


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
