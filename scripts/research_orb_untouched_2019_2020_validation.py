"""CLI for M50 (Issue #352): runs the Stage-0 provenance gate for the
untouched 2019-2020 validation of the frozen M49 portfolio, and -- as
Stage-0 item 3 requires independently of the item-1 universe blocker --
re-verifies the frozen M49 allocator's parity against its own existing
2022-2026 artifacts (ORB standalone replay parity against the M43
checkpoints, and ARM_A/ARM_B standalone parity against M48's committed
ledger hashes).

Stage-0 item 1 (point-in-time ORB universe) does not pass: see
`src.orb_untouched_2019_2020_validation_research` for the full, recorded
investigation. Because item 1 blocks, item 2 (2018-2020 market-data/
calendar coverage) is not evaluated -- there is no confirmed ORB universe
roster whose coverage could be checked -- and no 2019-2020 economic
outcome is computed. This script's job is to produce the committed,
auditable record of that Stage-0 outcome, not to simulate a portfolio.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

import scripts.research_orb_practical_core_satellite_portfolio as m49_cli
import scripts.research_capital_target_strategy_family as m48_cli
from src.orb_capital_target_strategy_family_research import UNIVERSE_A, UNIVERSE_B
from src.orb_untouched_2019_2020_validation_research import (
    M49_FROZEN_REFERENCE,
    STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS,
    STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS,
    run_stage0_gate,
)

RESULTS_DIR = ROOT / "artifacts" / "research-untouched-2019-2020-validation" / "results"
COMMITTED_HASHES_PATH = ROOT / "docs" / "research" / "ORB_UNTOUCHED_2019_2020_VALIDATION_ARTIFACT_HASHES.json"

SOURCE_FILES_FOR_HASHING = (
    ROOT / "src" / "orb_untouched_2019_2020_validation_research.py",
    ROOT / "scripts" / "research_orb_untouched_2019_2020_validation.py",
)


class UntouchedValidationCLIError(RuntimeError):
    pass


def _source_file_hashes() -> dict[str, str]:
    return {str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE_FILES_FOR_HASHING}


def _generation_working_tree_clean() -> bool | None:
    """Scoped to exactly this milestone's own two hashed source files --
    same convention as M48/M49 (see M49's own docstring for why: this
    result JSON and the committed hashes document are always rewritten by
    the current run and always differ from HEAD, which is expected, not
    evidence the implementation itself is unclean)."""
    try:
        rel_paths = [str(p.relative_to(ROOT).as_posix()) for p in SOURCE_FILES_FOR_HASHING]
        out = subprocess.run(["git", "status", "--porcelain", "--"] + rel_paths, cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip() == ""
    except Exception:
        return None


def _git_head_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip()
    except Exception:
        return None


async def _run_m49_parity_reverification(*, fetch_missing: bool) -> dict[str, Any]:
    """Stage-0 item 3: reproduce the frozen M49 allocator on its existing
    2022-2026 artifacts exactly (ORB standalone replay parity against the
    M43 checkpoints; ARM_A/ARM_B standalone parity against M48's own
    committed ledger hashes). Independent of the Stage-0 item-1 universe
    blocker -- this uses only the already-existing, frozen M43/M48/M49
    caches and checkpoints, never any 2019-2020 data."""
    report: dict[str, Any] = {"orb": None, "arm_a": None, "arm_b": None, "error": None}
    try:
        orb_intents, orb_parity_reports = await m49_cli.build_orb_intents(fetch_missing=fetch_missing)
        report["orb"] = {
            "ok": True,
            "periods": list(orb_parity_reports.keys()),
            "total_intents": len(orb_intents),
        }
    except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed
        report["orb"] = {"ok": False}
        report["error"] = f"ORB parity re-verification failed: {exc}"
        return report

    cache_dir = m48_cli.cache_dir_for_feed("sip")
    if not cache_dir.exists() or not m49_cli.M48_COMMITTED_HASHES_PATH.exists():
        report["arm_a"] = {"ok": None, "skipped": "M48 SIP cache or committed hashes document not present"}
        report["arm_b"] = {"ok": None, "skipped": "M48 SIP cache or committed hashes document not present"}
        return report

    series_by_symbol = await m48_cli.fetch_all_symbols(cache_dir=cache_dir, fetch_missing=False, feed="sip")
    master_dates = m48_cli.build_master_calendar(series_by_symbol["SPY"])
    series_by_symbol_a = {s: series_by_symbol[s] for s in UNIVERSE_A}
    series_by_symbol_b = {s: series_by_symbol[s] for s in UNIVERSE_B}
    arm_a_parity = m49_cli.verify_arm_a_standalone_parity(series_by_symbol_a=series_by_symbol_a, master_dates=master_dates)
    arm_b_parity = m49_cli.verify_arm_b_standalone_parity(series_by_symbol_b=series_by_symbol_b, master_dates=master_dates)
    report["arm_a"] = {"ok": arm_a_parity["ok"], "ledger_hash_match": arm_a_parity["ledger_hash_match"]}
    report["arm_b"] = {"ok": arm_b_parity["ok"], "ledger_hash_match": arm_b_parity["ledger_hash_match"]}
    if not arm_a_parity["ok"] or not arm_b_parity["ok"]:
        report["error"] = "ARM_A/ARM_B standalone parity re-verification against M48 FAILED."
    return report


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-parity", action="store_true", help="Skip the Stage-0 item-3 M43/M48/M49 parity re-verification (for fast iteration only).")
    args = parser.parse_args(argv)

    stage0_result = run_stage0_gate()
    print(f"Stage-0 item 1 (point-in-time ORB universe): 2019={stage0_result.orb_universe_2019_established}, 2020={stage0_result.orb_universe_2020_established}")
    print(f"Stage-0 classification: {stage0_result.classification}")
    if stage0_result.blocker_reason:
        print(f"Blocker: {stage0_result.blocker_reason}")

    parity_report: dict[str, Any] | None = None
    if not args.skip_parity:
        print("Running Stage-0 item 3 (parity before economics) against existing M43/M48/M49 artifacts...")
        parity_report = await _run_m49_parity_reverification(fetch_missing=False)
        print(f"Parity re-verification result: {parity_report}")

    generated_at = datetime.now(timezone.utc)
    result_doc: dict[str, Any] = {
        "milestone": "M50",
        "issue": 352,
        "generated_at": generated_at.isoformat(),
        "stage0_item1_universe_provenance": {
            "orb_universe_2019_established": stage0_result.orb_universe_2019_established,
            "orb_universe_2020_established": stage0_result.orb_universe_2020_established,
            "etf_universes_exempt": stage0_result.etf_universes_exempt,
            "classification": stage0_result.classification,
            "blocker_reason": stage0_result.blocker_reason,
            "fixed_source_order_tiers_attempted": sorted({a.tier for a in stage0_result.source_attempts}),
            "source_attempts": [asdict(a) for a in stage0_result.source_attempts],
            "confirmed_change_events": [
                {**asdict(e), "publication_compliant": e.publication_compliant} for e in stage0_result.confirmed_change_events
            ],
        },
        "stage0_item2_market_data_coverage": {
            "evaluated": False,
            "reason": "Not reached: Stage-0 item 1 (universe) did not establish a roster whose 2018-2020 data coverage could be checked.",
        },
        "stage0_item3_parity_before_economics": parity_report if parity_report is not None else {"skipped": True},
        "m50_economic_outcome": {
            "computed": False,
            "reason": "Not computed: Stage-0 item 1 blocked before any empirical outcome could be read, per Issue #352's own sequencing.",
        },
        "final_classification": stage0_result.classification,
        "m49_frozen_reference": M49_FROZEN_REFERENCE,
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / "stage0_result.json"
    results_path.write_text(json.dumps(result_doc, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(f"Stage-0 result written to {results_path}")

    committed_hashes = {
        "milestone": "M50",
        "issue": 352,
        "generated_at": generated_at.isoformat(),
        "final_classification": stage0_result.classification,
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
        "stage0_result_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
    }
    COMMITTED_HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED_HASHES_PATH.write_text(json.dumps(committed_hashes, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Committed artifact-hashes document written to {COMMITTED_HASHES_PATH}")

    if parity_report is not None and parity_report.get("error"):
        print(f"BLOCKER: {parity_report['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
