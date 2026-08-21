"""Focused tests for the M50 CLI (Issue #352): hash/provenance helpers and
the end-to-end structure of the written Stage-0 result document. The real
Stage-0 item-3 parity re-verification against the live M43/M48 caches is
exercised separately (it is slow -- multi-minute ORB replay -- and the
CLI's own generated artifact is verified once as part of the required
empirical-generation sequencing, not repeatedly here)."""
from __future__ import annotations

import asyncio
import hashlib
import json

import scripts.research_orb_untouched_2019_2020_validation as cli


def test_source_file_hashes_match_actual_files_on_disk():
    hashes = cli._source_file_hashes()
    assert set(hashes) == {
        "src/orb_untouched_2019_2020_validation_research.py",
        "scripts/research_orb_untouched_2019_2020_validation.py",
    }
    for rel_path, sha in hashes.items():
        assert sha == hashlib.sha256((cli.ROOT / rel_path).read_bytes()).hexdigest()


def test_generation_working_tree_clean_is_scoped_to_source_files():
    import subprocess
    rel_paths = [str(p.relative_to(cli.ROOT).as_posix()) for p in cli.SOURCE_FILES_FOR_HASHING]
    status = subprocess.run(["git", "status", "--porcelain", "--"] + rel_paths, cwd=cli.ROOT, capture_output=True, text=True, timeout=10, check=True)
    expected = status.stdout.strip() == ""
    assert cli._generation_working_tree_clean() == expected


def test_git_head_sha_is_a_40_char_hex_string():
    sha = cli._git_head_sha()
    assert sha is not None
    assert len(sha) == 40
    int(sha, 16)  # raises if not valid hex


def test_main_skip_parity_writes_stage0_result_with_insufficient_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(cli, "COMMITTED_HASHES_PATH", tmp_path / "hashes.json")

    exit_code = asyncio.run(cli.main(["--skip-parity"]))
    assert exit_code == 0

    result_path = tmp_path / "results" / "stage0_result.json"
    assert result_path.exists()
    doc = json.loads(result_path.read_text(encoding="utf-8"))

    assert doc["milestone"] == "M50"
    assert doc["issue"] == 352
    assert doc["final_classification"] == "INSUFFICIENT_EVIDENCE"
    assert doc["stage0_item1_universe_provenance"]["orb_universe_2019_established"] is False
    assert doc["stage0_item1_universe_provenance"]["orb_universe_2020_established"] is False
    assert doc["stage0_item1_universe_provenance"]["etf_universes_exempt"] is True
    assert sorted(doc["stage0_item1_universe_provenance"]["fixed_source_order_tiers_attempted"]) == [1, 2, 3]
    assert len(doc["stage0_item1_universe_provenance"]["source_attempts"]) >= 15
    assert len(doc["stage0_item1_universe_provenance"]["confirmed_change_events"]) >= 2
    assert doc["stage0_item2_market_data_coverage"]["evaluated"] is False
    assert doc["stage0_item3_parity_before_economics"] == {"skipped": True}
    assert doc["m50_economic_outcome"]["computed"] is False
    assert doc["m49_frozen_reference"]["classification"] == "SAFE_BUT_BELOW_TARGET"
    assert doc["generation_git_head_sha"] is not None

    committed_hashes_path = tmp_path / "hashes.json"
    assert committed_hashes_path.exists()
    hashes_doc = json.loads(committed_hashes_path.read_text(encoding="utf-8"))
    assert hashes_doc["final_classification"] == "INSUFFICIENT_EVIDENCE"
    assert hashes_doc["stage0_result_sha256"] == hashlib.sha256(result_path.read_bytes()).hexdigest()
