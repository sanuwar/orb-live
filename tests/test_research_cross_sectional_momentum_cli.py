"""Focused tests for the M52 CLI (Issue #356): name-normalization for
OpenFIGI queries, monthly-universe assembly from filing/snapshot data,
Stage-0 aggregation, and hash/provenance helpers. The real SEC EDGAR
N-PORT fetch, OpenFIGI resolution, and Alpaca daily-bar fetch are
exercised separately (all three are slow, real network calls, run once
as part of the required empirical-generation sequencing, not repeated
here)."""
from __future__ import annotations

import hashlib
from datetime import date

from decimal import Decimal

import scripts.research_cross_sectional_momentum as cli
from src.cross_sectional_momentum_research import (
    IWB_CIK,
    IWB_SERIES_ID,
    MomentumTransaction,
    NportFiling,
    PortfolioSimulationResult,
    RawHolding,
    ResolvedHolding,
    UniverseSnapshot,
)


def test_bare_name_variant_strips_corporation_suffix():
    assert cli._bare_name_variant("LOCKHEED MARTIN CORPORATION") == "LOCKHEED MARTIN"


def test_bare_name_variant_strips_inc_and_comma():
    assert cli._bare_name_variant("ULTA BEAUTY, INC.") == "ULTA BEAUTY"


def test_bare_name_variant_returns_none_when_nothing_to_strip():
    assert cli._bare_name_variant("ALPHABET") is None


def test_name_query_variants_puts_bare_name_first_when_available():
    variants = cli._name_query_variants("LOCKHEED MARTIN CORPORATION")
    assert variants == ("LOCKHEED MARTIN", "LOCKHEED MARTIN CORPORATION")


def test_name_query_variants_single_entry_when_no_suffix_to_strip():
    variants = cli._name_query_variants("ALPHABET")
    assert variants == ("ALPHABET",)


def test_cache_path_is_deterministic_and_content_addressed():
    p1 = cli._cache_path(cli.SEC_CACHE_DIR, "some-key")
    p2 = cli._cache_path(cli.SEC_CACHE_DIR, "some-key")
    p3 = cli._cache_path(cli.SEC_CACHE_DIR, "other-key")
    assert p1 == p2
    assert p1 != p3


def test_source_file_hashes_match_actual_files_on_disk():
    hashes = cli._source_file_hashes()
    assert set(hashes) == {
        "src/cross_sectional_momentum_research.py",
        "scripts/research_cross_sectional_momentum.py",
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
    int(sha, 16)


def _filing(period_ending: date, file_date: date, accession: str) -> NportFiling:
    return NportFiling(series_id=IWB_SERIES_ID, cik=IWB_CIK, accession=accession, period_ending=period_ending, file_date=file_date, document_url="https://example.invalid")


def _snapshot(filing: NportFiling, tickers: tuple[str, ...]) -> UniverseSnapshot:
    holdings = tuple(
        ResolvedHolding(
            raw=RawHolding(name=t, cusip=f"cusip-{t}", isin=None, asset_category="EC", issuer_category="CORP", invest_country="US"),
            ticker=t, resolution_status="RESOLVED",
        )
        for t in tickers
    )
    return UniverseSnapshot(filing=filing, holdings=holdings, raw_holdings_sha256="raw", normalized_holdings_sha256="norm")


def test_build_monthly_universes_selects_the_correct_point_in_time_snapshot():
    filing_q4 = _filing(date(2020, 12, 31), date(2021, 2, 25), "q4_2020")
    filing_q1 = _filing(date(2021, 3, 31), date(2021, 5, 27), "q1_2021")
    snapshots = (_snapshot(filing_q4, ("AAA", "BBB")), _snapshot(filing_q1, ("CCC", "DDD")))

    signal_dates = (date(2021, 3, 1), date(2021, 6, 1))
    monthly_universes, filing_for_signal = cli.build_monthly_universes(signal_dates, snapshots)

    assert monthly_universes[date(2021, 3, 1)] == ("AAA", "BBB")  # Q1 2021 filing not yet public
    assert filing_for_signal[date(2021, 3, 1)] == "q4_2020"
    assert monthly_universes[date(2021, 6, 1)] == ("CCC", "DDD")  # now public
    assert filing_for_signal[date(2021, 6, 1)] == "q1_2021"


def test_build_monthly_universes_empty_before_any_filing_is_public():
    filing_q4 = _filing(date(2020, 12, 31), date(2021, 2, 25), "q4_2020")
    snapshots = (_snapshot(filing_q4, ("AAA",)),)
    signal_dates = (date(2020, 6, 1),)
    monthly_universes, filing_for_signal = cli.build_monthly_universes(signal_dates, snapshots)
    assert monthly_universes[date(2020, 6, 1)] == ()
    assert filing_for_signal[date(2020, 6, 1)] is None


def _txn(d: date, symbol: str = "AAA") -> MomentumTransaction:
    return MomentumTransaction(
        date=d, symbol=symbol, side="BUY", quantity=10, price=Decimal("100"),
        notional=Decimal("1000"), cost=Decimal("0.50"), reason="signal",
    )


def _result(cost_label: str, transactions: tuple[MomentumTransaction, ...]) -> PortfolioSimulationResult:
    return PortfolioSimulationResult(
        cost_label=cost_label, transactions=transactions, blocked_intents=(), daily_snapshots=(),
        monthly_signals=(), open_positions_at_end={}, final_cash=Decimal("0"),
    )


def test_stage0_duplicate_check_ignores_expected_base_stress_overlap():
    # Base and stress are two independent simulations of the same
    # signals/prices, so identical (date, symbol, side, quantity, price,
    # reason) tuples routinely recur across the two runs -- that overlap
    # must not be flagged as a duplicate transaction.
    shared = _txn(date(2021, 1, 4))
    base_result = _result("base", (shared,))
    stress_result = _result("stress", (shared,))
    checks = cli.run_stage0_checks(
        snapshots=(), monthly_universes={}, signal_dates=(), series_by_symbol={}, spy_series=None,
        base_result=base_result, stress_result=stress_result,
    )
    assert checks["item9_no_duplicates_no_unclassified_blocked"]["duplicate_transaction_count"] == 0
    assert checks["item9_no_duplicates_no_unclassified_blocked"]["ok"] is True


def test_stage0_duplicate_check_still_catches_a_real_within_run_duplicate():
    shared = _txn(date(2021, 1, 4))
    base_result = _result("base", (shared, shared))
    stress_result = _result("stress", ())
    checks = cli.run_stage0_checks(
        snapshots=(), monthly_universes={}, signal_dates=(), series_by_symbol={}, spy_series=None,
        base_result=base_result, stress_result=stress_result,
    )
    assert checks["item9_no_duplicates_no_unclassified_blocked"]["duplicate_transaction_count"] == 1
    assert checks["item9_no_duplicates_no_unclassified_blocked"]["ok"] is False
