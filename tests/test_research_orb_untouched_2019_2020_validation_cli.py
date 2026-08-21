"""Focused tests for the M50 CLI (Issue #352): hash/provenance helpers,
the session-range helper, coverage-check builders, and the end-to-end
structure of the written Stage-0 result document. The real item-2 data
fetch and item-3 parity re-verification against the live Alpaca/M43/M48
caches are exercised separately (both are slow -- multi-minute -- and were
run once already as part of the required empirical-generation sequencing,
not repeated here)."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date

import scripts.research_orb_untouched_2019_2020_validation as cli
from src.orb_untouched_2019_2020_validation_research import CLASSIFICATION_INSUFFICIENT_EVIDENCE, DataCoverageCheck


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


def test_all_sessions_in_range_matches_known_2019_session_count():
    sessions = cli._all_sessions_in_range(date(2019, 1, 2), date(2019, 12, 31))
    assert len(sessions) == 252
    assert sessions[0] == date(2019, 1, 2)
    assert sessions[-1] == date(2019, 12, 31)
    assert sessions == tuple(sorted(sessions))


def test_all_sessions_in_range_matches_known_2020_session_count():
    sessions = cli._all_sessions_in_range(date(2020, 1, 2), date(2020, 12, 31))
    assert len(sessions) == 253


def test_orb_union_universe_has_31_distinct_tickers():
    assert len(cli.ORB_UNION_UNIVERSE) == 31
    assert "DWDP" in cli.ORB_UNION_UNIVERSE
    assert "DOW" in cli.ORB_UNION_UNIVERSE


def test_build_orb_coverage_checks_reports_full_gap_with_no_bars_at_all():
    sessions_2019 = cli._all_sessions_in_range(date(2019, 1, 2), date(2019, 3, 1))
    sessions_2020 = cli._all_sessions_in_range(date(2020, 1, 2), date(2020, 3, 1))
    checks = cli.build_orb_coverage_checks(market_bars_by_symbol={}, session_dates_2019=sessions_2019, session_dates_2020=sessions_2020)
    assert len(checks) == 60  # 30 tickers x 2 years
    assert all(not c.ok for c in checks)
    assert all(len(c.received_session_dates) == 0 for c in checks)


def test_build_orb_coverage_checks_dwdp_truncates_to_its_own_corporate_action_window():
    sessions_2019 = cli._all_sessions_in_range(date(2019, 1, 2), date(2019, 12, 31))
    sessions_2020 = cli._all_sessions_in_range(date(2020, 1, 2), date(2020, 12, 31))
    checks = cli.build_orb_coverage_checks(market_bars_by_symbol={}, session_dates_2019=sessions_2019, session_dates_2020=sessions_2020)
    dwdp = next(c for c in checks if c.symbol == "DWDP")
    assert dwdp.expected_last_active_date == date(2019, 5, 31)
    assert dwdp.expected_session_dates[-1] <= date(2019, 5, 31)
    assert "DWDP" not in [c.symbol for c in checks if c.expected_last_active_date == date(2019, 12, 31)]


def test_build_etf_coverage_checks_xlc_excludes_its_own_pre_launch_window():
    from src.orb_capital_target_strategy_family_research import DailySeries
    from decimal import Decimal

    master_dates = cli._all_sessions_in_range(date(2018, 1, 2), date(2018, 7, 1))
    # XLC series that only actually has bars from its real launch date onward.
    xlc_dates = tuple(d for d in master_dates if d >= date(2018, 6, 19))
    other_symbols = [s for s in (set(cli.UNIVERSE_A) | set(cli.UNIVERSE_B)) if s != "XLC"]
    series_by_symbol = {"XLC": DailySeries(symbol="XLC", dates=xlc_dates, opens=tuple(Decimal("50") for _ in xlc_dates), highs=tuple(Decimal("51") for _ in xlc_dates), lows=tuple(Decimal("49") for _ in xlc_dates), closes=tuple(Decimal("50") for _ in xlc_dates), volumes=tuple(1000 for _ in xlc_dates))}
    for s in other_symbols:
        series_by_symbol[s] = DailySeries(symbol=s, dates=master_dates, opens=tuple(Decimal("50") for _ in master_dates), highs=tuple(Decimal("51") for _ in master_dates), lows=tuple(Decimal("49") for _ in master_dates), closes=tuple(Decimal("50") for _ in master_dates), volumes=tuple(1000 for _ in master_dates))

    checks = cli.build_etf_coverage_checks(series_by_symbol=series_by_symbol, master_dates=master_dates)
    xlc_check = next(c for c in checks if c.symbol == "XLC")
    assert xlc_check.ok is True
    assert xlc_check.expected_session_dates[0] == date(2018, 6, 19)


def test_main_skip_item2_and_skip_parity_leaves_final_classification_none(tmp_path, monkeypatch):
    # With item 1 passing and both item 2 and item 3 skipped, nothing blocks
    # -- there is no basis to claim INSUFFICIENT_EVIDENCE, so classification
    # must be left None, not guessed.
    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(cli, "COMMITTED_HASHES_PATH", tmp_path / "hashes.json")

    exit_code = asyncio.run(cli.main(["--skip-item2", "--skip-parity"]))
    assert exit_code == 0

    result_path = tmp_path / "results" / "stage0_result.json"
    doc = json.loads(result_path.read_text(encoding="utf-8"))

    assert doc["milestone"] == "M50"
    assert doc["issue"] == 352
    assert doc["stage0_item1_universe_provenance"]["orb_universe_2019_established"] is True
    assert doc["stage0_item1_universe_provenance"]["orb_universe_2020_established"] is True
    assert doc["stage0_item2_market_data_coverage"]["evaluated"] is False
    assert doc["stage0_item3_parity_before_economics"] == {"skipped": True}
    assert doc["final_classification"] is None
    assert doc["m49_frozen_reference"]["classification"] == "SAFE_BUT_BELOW_TARGET"
    assert doc["generation_git_head_sha"] is not None

    hashes_doc = json.loads((tmp_path / "hashes.json").read_text(encoding="utf-8"))
    assert hashes_doc["final_classification"] is None
    assert hashes_doc["stage0_result_sha256"] == hashlib.sha256(result_path.read_bytes()).hexdigest()
