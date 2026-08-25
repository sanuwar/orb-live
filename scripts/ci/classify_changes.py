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

# Tier-1-eligible: publication prose only. Structured data is NOT
# Tier-1-eligible: an arbitrary .json/.csv/.yaml under docs/publication/
# may well be an input that a generator consumes, so it needs the focused
# verification that only Tier 2 (or the full tier) provides.
TIER_1_PREFIX = "docs/publication/"
TIER_1_SUFFIX = ".md"


class ToolingEntry:
    """One publication artifact together with the exact checks that cover it.

    The manifest is the whole Tier-2 allowlist. A publication generator,
    test, or structured-data artifact earns Tier 2 only by appearing here
    with a named test module and, where it is generated, a regeneration
    command. Anything not listed -- including a brand-new
    scripts/publication/*.py or tests/test_publication_*.py -- falls to
    the full tier until its focused verification is added here and wired
    into the workflow.
    """

    __slots__ = ("path", "tests", "regeneration")

    def __init__(self, path: str, tests: tuple, regeneration=None):
        self.path = path
        self.tests = tests
        self.regeneration = regeneration

    def __repr__(self) -> str:
        return "ToolingEntry({0!r})".format(self.path)


#: The P2 hypothesis ledger is the only publication tooling with focused
#: verification wired up today. Its generated Markdown is included
#: deliberately: hand-editing the generated file must trigger the
#: regeneration-determinism check rather than passing as Tier-1 prose.
_P2_TESTS = ("tests/test_publication_p2_artifacts.py",)
_P2_REGEN = "python -m scripts.publication.generate_p2_hypothesis_ledger"

PUBLICATION_TOOLING_MANIFEST: tuple = (
    ToolingEntry("scripts/publication/generate_p2_hypothesis_ledger.py", _P2_TESTS, _P2_REGEN),
    ToolingEntry("tests/test_publication_p2_artifacts.py", _P2_TESTS),
    ToolingEntry("docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.json", _P2_TESTS, _P2_REGEN),
    ToolingEntry("docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.md", _P2_TESTS, _P2_REGEN),
)

MANIFEST_PATHS: frozenset = frozenset(entry.path for entry in PUBLICATION_TOOLING_MANIFEST)

#: Prefixes that look like publication tooling but are only Tier-2
#: eligible when the exact file is in the manifest. Anything matching
#: these prefixes and absent from the manifest is explicitly rejected to
#: the full tier, so adding a new generator cannot silently inherit the
#: P2 checks.
TOOLING_LOOKALIKE_PREFIXES: tuple = (
    "scripts/publication/",
    "tests/test_publication_",
)


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
    """Publication prose only, and never a manifest-managed file.

    A generated Markdown artifact lives in the manifest so that editing
    it triggers the regeneration-determinism check instead of passing as
    ordinary prose.
    """
    if path in MANIFEST_PATHS:
        return False
    if not path.startswith(TIER_1_PREFIX):
        return False
    return path.endswith(TIER_1_SUFFIX)


def _is_tier2_path(path: str) -> bool:
    """Tier-2 eligibility is manifest membership. Nothing else."""
    return path in MANIFEST_PATHS


def _is_unlisted_tooling_lookalike(path: str) -> bool:
    """True for publication-tooling-shaped paths absent from the manifest."""
    if path in MANIFEST_PATHS:
        return False
    return any(path.startswith(prefix) for prefix in TOOLING_LOOKALIKE_PREFIXES)


def checks_for(paths) -> tuple:
    """Return the manifest entries covering `paths`, for workflow wiring."""
    return tuple(
        entry for entry in PUBLICATION_TOOLING_MANIFEST if entry.path in set(paths)
    )


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

        unlisted_tooling = [p for p in ordered if _is_unlisted_tooling_lookalike(p)]
        if unlisted_tooling:
            reasons = tuple(
                "publication tooling not in the verification manifest, "
                "defaulting to full until its focused checks are wired up: " + p
                for p in unlisted_tooling[:5]
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
