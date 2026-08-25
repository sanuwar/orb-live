"""Change-aware CI tier classifier (Issue #370).

Maps a set of changed repository paths to the cheapest verification tier
that is still safe for those paths. The classifier is deliberately an
allowlist: a path earns a lighter tier only by matching an explicit safe
pattern, and anything unrecognized, ambiguous, malformed, or empty falls
back to the full tier.

Tiers:

- TIER_1_PUBLICATION_DOCS: every changed path is publication prose or an
  approved publication data artifact. Runs documentation/evidence checks
  and safety-state checks; no pytest suite.
- TIER_2_PUBLICATION_TOOLING: every changed path is publication
  documentation, a publication generator, or a publication test. Runs the
  publication-focused tests plus deterministic regeneration and
  architecture bookkeeping.
- TIER_3_FULL: everything else, and every failure mode. Runs the
  complete suite.

The only safe failure direction is upward. No code path in this module
returns a tier lighter than TIER_3_FULL for a path it did not explicitly
recognize, and classify() never raises: callers get TIER_3_FULL with a
stated reason instead.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import sys
from dataclasses import dataclass, field

TIER_1 = "TIER_1_PUBLICATION_DOCS"
TIER_2 = "TIER_2_PUBLICATION_TOOLING"
TIER_3 = "TIER_3_FULL"

# Paths that always force the full tier even if another rule would match
# them first. Listed explicitly so that widening a publication pattern
# later cannot accidentally expose one of these.
PROTECTED_PREFIXES: tuple[str, ...] = (
    "src/",
    "config/",
    "data/",
    "docs/research/",
    ".github/",
    "scripts/monitor_",
    "scripts/research_",
    "scripts/verify_",
    "scripts/architecture_audit",
    "scripts/check_",
    "scripts/deployment_",
)

# Dependency, packaging, test-runner, and environment configuration.
PROTECTED_EXACT: frozenset = frozenset(
    {
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pytest.ini",
        "tox.ini",
        "Makefile",
        "Dockerfile",
        "conftest.py",
        "poetry.lock",
        "uv.lock",
        ".python-version",
    }
)

# Tier-1-eligible: publication prose and approved publication data
# artifacts. These do not affect runtime behaviour.
TIER_1_SUFFIXES: tuple[str, ...] = (".md", ".json")
TIER_1_PREFIX = "docs/publication/"

# Tier-2-eligible additions: publication generators and their tests.
TIER_2_PREFIX_SCRIPTS = "scripts/publication/"
TIER_2_PREFIX_TESTS = "tests/test_publication_"


@dataclass(frozen=True)
class Classification:
    """Result of a classification, including why the tier was chosen."""

    tier: str
    reasons: tuple = field(default_factory=tuple)
    paths: tuple = field(default_factory=tuple)

    @property
    def runs_full_suite(self) -> bool:
        return self.tier == TIER_3


def _normalize(path):
    """Return a clean repo-relative POSIX path, or None if suspicious.

    Rejects absolute paths, parent-directory traversal, and anything that
    does not normalize to a plain relative path. A rejected path forces
    the full tier rather than being silently dropped.
    """
    if not isinstance(path, str):
        return None
    candidate = path.strip().replace("\\", "/")
    if not candidate:
        return None
    if candidate.startswith("/"):
        return None
    if ":" in candidate.split("/")[0]:
        return None
    normalized = posixpath.normpath(candidate)
    if normalized in {".", ""}:
        return None
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def _is_protected(path: str) -> bool:
    if path in PROTECTED_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def _is_tier1_path(path: str) -> bool:
    if not path.startswith(TIER_1_PREFIX):
        return False
    return path.endswith(TIER_1_SUFFIXES)


def _is_tier2_path(path: str) -> bool:
    if path.startswith(TIER_2_PREFIX_SCRIPTS) and path.endswith(".py"):
        return True
    if path.startswith(TIER_2_PREFIX_TESTS) and path.endswith(".py"):
        return True
    return False


def classify(paths, force_full: bool = False) -> Classification:
    """Classify changed `paths` into the cheapest safe tier.

    Never raises. Any unexpected condition yields TIER_3_FULL.
    """
    try:
        if force_full:
            return Classification(TIER_3, ("full-suite override requested",), ())

        if paths is None:
            return Classification(TIER_3, ("no changed-path input provided",), ())

        try:
            raw = list(paths)
        except TypeError:
            return Classification(TIER_3, ("changed-path input is not iterable",), ())

        if not raw:
            return Classification(TIER_3, ("empty changed-path set",), ())

        cleaned = []
        for item in raw:
            normalized = _normalize(item)
            if normalized is None:
                reason = "unparseable or unsafe path: {0!r}".format(item)
                return Classification(TIER_3, (reason,), tuple(cleaned))
            cleaned.append(normalized)

        ordered = tuple(sorted(set(cleaned)))

        protected = [p for p in ordered if _is_protected(p)]
        if protected:
            reasons = tuple(
                "protected path requires full verification: " + p for p in protected[:5]
            )
            return Classification(TIER_3, reasons, ordered)

        unknown = [p for p in ordered if not (_is_tier1_path(p) or _is_tier2_path(p))]
        if unknown:
            reasons = tuple(
                "unrecognized path, defaulting to full: " + p for p in unknown[:5]
            )
            return Classification(TIER_3, reasons, ordered)

        if all(_is_tier1_path(p) for p in ordered):
            reason = "all changes are publication documentation or data artifacts"
            return Classification(TIER_1, (reason,), ordered)

        reason = "changes are limited to publication documentation and tooling"
        return Classification(TIER_2, (reason,), ordered)
    except Exception as exc:  # noqa: BLE001 - fail closed, never fail open
        return Classification(TIER_3, ("classifier error, defaulting to full: {0!r}".format(exc),), ())


def _read_paths(args):
    if args.paths:
        return list(args.paths)
    data = sys.stdin.read()
    if not data.strip():
        return []
    return [line for line in data.splitlines() if line.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Classify changed paths into a CI tier")
    parser.add_argument(
        "paths", nargs="*", help="Changed paths; omit to read newline-delimited stdin"
    )
    parser.add_argument("--force-full", action="store_true", help="Force the full tier")
    parser.add_argument("--format", choices=("json", "github"), default="json")
    args = parser.parse_args(argv)

    try:
        paths = _read_paths(args)
    except Exception as exc:  # noqa: BLE001
        reason = "could not read changed paths: {0!r}".format(exc)
        result = Classification(TIER_3, (reason,), ())
    else:
        result = classify(paths, force_full=args.force_full)

    if args.format == "github":
        print("tier=" + result.tier)
        print("run_full=" + ("true" if result.runs_full_suite else "false"))
    else:
        print(
            json.dumps(
                {
                    "tier": result.tier,
                    "run_full": result.runs_full_suite,
                    "reasons": list(result.reasons),
                    "paths": list(result.paths),
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
