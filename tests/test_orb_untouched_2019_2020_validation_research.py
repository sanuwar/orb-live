"""Tests for the M50 (Issue #352) Stage-0 provenance-gate module: the
frozen validation window, the frozen classification labels/thresholds
(copied verbatim from Issue #352), and the point-in-time ORB universe
sources/audits/evaluator built from the actually-recorded investigation.

Each universe year is pinned independently -- filing dates, report dates,
exactly-30-holdings, ticker resolution, and per-year pass/fail logic --
per the correction requiring no shared "established" boolean across years."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.orb_capital_target_strategy_family_research import EVALUATION_END as M48_EVALUATION_END
from src.orb_capital_target_strategy_family_research import EVALUATION_START as M48_EVALUATION_START
from src.orb_untouched_2019_2020_validation_research import (
    CorporateActionPolicy,
    DataCoverageCheck,
    ETF_LAUNCH_POLICIES,
    EtfLaunchPolicy,
    IEX_MINUTE_COVERAGE_FLOOR,
    ORB_CORPORATE_ACTION_POLICIES,
    evaluate_stage0_item2_coverage,
    expected_first_active_date_for,
    expected_last_active_date_for,
    CLASSIFICATION_FAILED_VALIDATION,
    CLASSIFICATION_INDEPENDENTLY_VALIDATED,
    CLASSIFICATION_INSUFFICIENT_EVIDENCE,
    CLASSIFICATION_MIXED_VALIDATION,
    FIXED_SOURCE_ORDER,
    INDEPENDENTLY_VALIDATED_MAX_DRAWDOWN_PCT,
    INDEPENDENTLY_VALIDATED_MIN_MONTHLY_DOLLARS,
    INDEPENDENTLY_VALIDATED_MIN_PROFITABLE_MONTH_PCT,
    INDEPENDENTLY_VALIDATED_WORST_MONTH_FLOOR_PCT,
    MIXED_VALIDATION_MAX_DRAWDOWN_PCT,
    M49_FROZEN_REFERENCE,
    NoChangeAuditWindow,
    NO_CHANGE_AUDITS_BY_YEAR,
    OUTCOME_WINDOW_END,
    OUTCOME_WINDOW_START,
    REQUIRED_OUTCOME_MONTHS,
    STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS,
    STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS,
    SourceAttempt,
    UniverseSource,
    UNIVERSE_2019,
    UNIVERSE_2020,
    UNIVERSE_SOURCE_2019,
    UNIVERSE_SOURCE_2020,
    UNIVERSE_SOURCES_BY_YEAR,
    stage0_universe_established_for_year,
    run_stage0_gate,
)


def test_frozen_window_matches_issue_352():
    assert OUTCOME_WINDOW_START == date(2019, 1, 2)
    assert OUTCOME_WINDOW_END == date(2020, 12, 31)
    assert REQUIRED_OUTCOME_MONTHS == 24


def test_outcome_window_does_not_overlap_m48_m49_evaluation_window():
    assert OUTCOME_WINDOW_END < M48_EVALUATION_START
    assert OUTCOME_WINDOW_START < OUTCOME_WINDOW_END < M48_EVALUATION_START < M48_EVALUATION_END


def test_classification_labels_match_issue_352_exactly():
    assert CLASSIFICATION_INDEPENDENTLY_VALIDATED == "INDEPENDENTLY_VALIDATED"
    assert CLASSIFICATION_MIXED_VALIDATION == "MIXED_VALIDATION"
    assert CLASSIFICATION_FAILED_VALIDATION == "FAILED_VALIDATION"
    assert CLASSIFICATION_INSUFFICIENT_EVIDENCE == "INSUFFICIENT_EVIDENCE"


def test_independently_validated_thresholds_match_issue_352_exactly():
    assert INDEPENDENTLY_VALIDATED_MIN_MONTHLY_DOLLARS == Decimal("150")
    assert INDEPENDENTLY_VALIDATED_MAX_DRAWDOWN_PCT == Decimal("0.15")
    assert INDEPENDENTLY_VALIDATED_MIN_PROFITABLE_MONTH_PCT == Decimal("0.55")
    assert INDEPENDENTLY_VALIDATED_WORST_MONTH_FLOOR_PCT == Decimal("-0.10")
    assert MIXED_VALIDATION_MAX_DRAWDOWN_PCT == Decimal("0.20")


def test_fixed_source_order_has_exactly_three_tiers():
    assert len(FIXED_SOURCE_ORDER) == 3
    assert "official issuer" in FIXED_SOURCE_ORDER[0]
    assert "SEC EDGAR" in FIXED_SOURCE_ORDER[1]
    assert "another primary official source" in FIXED_SOURCE_ORDER[2]


def test_all_three_tiers_were_actually_attempted():
    tiers_attempted = {a.tier for a in STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS}
    assert tiers_attempted == {1, 2, 3}


def test_qualifying_source_attempts_are_recorded_for_both_years():
    qualifying = [a for a in STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS if a.outcome == "COMPLETE_ROSTER_ESTABLISHED_COMPLIANT"]
    assert len(qualifying) == 2
    assert any("0001193125-18-351994" in a.source_name for a in qualifying)
    assert any("0001193125-19-320843" in a.source_name for a in qualifying)


# ---------------------------------------------------------------------------
# 2019 universe source: filing date, report date, holdings count, tickers.
# ---------------------------------------------------------------------------


def test_universe_source_2019_filing_and_report_dates_pinned():
    assert UNIVERSE_SOURCE_2019.universe_year == 2019
    assert UNIVERSE_SOURCE_2019.effective_date == date(2019, 1, 2)
    assert UNIVERSE_SOURCE_2019.accession_number == "0001193125-18-351994"
    assert UNIVERSE_SOURCE_2019.filing_date == date(2018, 12, 18)
    assert UNIVERSE_SOURCE_2019.period_of_report == date(2018, 10, 31)
    assert UNIVERSE_SOURCE_2019.form_type == "N-30D"
    assert UNIVERSE_SOURCE_2019.cik == "0001041130"


def test_universe_source_2019_filing_date_no_later_than_effective_date():
    assert UNIVERSE_SOURCE_2019.publication_compliant is True
    assert UNIVERSE_SOURCE_2019.filing_date <= UNIVERSE_SOURCE_2019.effective_date


def test_universe_source_2019_has_exactly_thirty_unique_tickers():
    assert len(UNIVERSE_SOURCE_2019.tickers) == 30
    assert len(set(UNIVERSE_SOURCE_2019.tickers)) == 30
    assert UNIVERSE_SOURCE_2019.has_exactly_thirty_unique_tickers is True


def test_universe_2019_contains_dwdp_not_dow():
    # Report date 2018-10-31 predates the 2019-04-02 DOW/DWDP swap.
    assert "DWDP" in UNIVERSE_2019
    assert "DOW" not in UNIVERSE_2019


def test_universe_2019_ticker_resolution_all_uppercase_alpha():
    for ticker in UNIVERSE_2019:
        assert ticker.isalpha() and ticker.isupper(), ticker


# ---------------------------------------------------------------------------
# 2020 universe source: filing date, report date, holdings count, tickers.
# ---------------------------------------------------------------------------


def test_universe_source_2020_filing_and_report_dates_pinned():
    assert UNIVERSE_SOURCE_2020.universe_year == 2020
    assert UNIVERSE_SOURCE_2020.effective_date == date(2020, 1, 2)
    assert UNIVERSE_SOURCE_2020.accession_number == "0001193125-19-320843"
    assert UNIVERSE_SOURCE_2020.filing_date == date(2019, 12, 20)
    assert UNIVERSE_SOURCE_2020.period_of_report == date(2019, 10, 31)
    assert UNIVERSE_SOURCE_2020.form_type == "N-30D"


def test_universe_source_2020_filing_date_no_later_than_effective_date():
    assert UNIVERSE_SOURCE_2020.publication_compliant is True
    assert UNIVERSE_SOURCE_2020.filing_date <= UNIVERSE_SOURCE_2020.effective_date


def test_universe_source_2020_has_exactly_thirty_unique_tickers():
    assert len(UNIVERSE_SOURCE_2020.tickers) == 30
    assert len(set(UNIVERSE_SOURCE_2020.tickers)) == 30
    assert UNIVERSE_SOURCE_2020.has_exactly_thirty_unique_tickers is True


def test_universe_2020_contains_dow_not_dwdp():
    # Report date 2019-10-31 postdates the 2019-04-02 DOW/DWDP swap.
    assert "DOW" in UNIVERSE_2020
    assert "DWDP" not in UNIVERSE_2020


def test_universe_2020_ticker_resolution_all_uppercase_alpha():
    for ticker in UNIVERSE_2020:
        assert ticker.isalpha() and ticker.isupper(), ticker


def test_2019_and_2020_rosters_differ_by_exactly_the_confirmed_dow_dwdp_swap():
    only_in_2019 = set(UNIVERSE_2019) - set(UNIVERSE_2020)
    only_in_2020 = set(UNIVERSE_2020) - set(UNIVERSE_2019)
    assert only_in_2019 == {"DWDP"}
    assert only_in_2020 == {"DOW"}
    assert len(set(UNIVERSE_2019) & set(UNIVERSE_2020)) == 29


# ---------------------------------------------------------------------------
# No-change audit windows: each year's report-date-to-effective-date gap
# must be independently confirmed change-free.
# ---------------------------------------------------------------------------


def test_no_change_audit_windows_align_with_each_source_and_confirm_no_change():
    for year, source in ((2019, UNIVERSE_SOURCE_2019), (2020, UNIVERSE_SOURCE_2020)):
        audit = NO_CHANGE_AUDITS_BY_YEAR[year]
        assert audit.universe_year == year
        assert audit.window_start == source.period_of_report
        assert audit.window_end == source.effective_date
        assert audit.confirmed_no_change is True
        assert len(audit.corroborating_sources) >= 2


def test_confirmed_djia_change_events_are_all_publication_compliant():
    assert len(STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS) >= 3
    for event in STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS:
        assert event.publication_compliant is True
        assert event.announcement_date <= event.effective_date


def test_confirmed_change_events_fall_outside_both_audit_windows():
    # The DOW/DWDP swap (2019-04-02) and the 2020-08-31 changes must NOT
    # fall inside either [report_date, effective_date) audit window -- if
    # they did, the "no change in window" audits above would be wrong.
    for event in STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS:
        for audit in NO_CHANGE_AUDITS_BY_YEAR.values():
            assert not (audit.window_start <= event.effective_date < audit.window_end), (
                f"{event.description} falls inside the {audit.universe_year} audit window"
            )


# ---------------------------------------------------------------------------
# Per-year independent evaluation -- never a single shared boolean.
# ---------------------------------------------------------------------------


def test_stage0_universe_established_for_year_is_true_for_both_years_given_real_evidence():
    assert stage0_universe_established_for_year(2019) is True
    assert stage0_universe_established_for_year(2020) is True


def test_stage0_universe_established_for_year_is_false_for_a_year_with_no_source():
    assert stage0_universe_established_for_year(2021) is False


def test_stage0_universe_established_for_year_fails_independently_when_only_one_year_has_a_source():
    only_2019 = {2019: UNIVERSE_SOURCE_2019}
    only_2019_audit = {2019: NO_CHANGE_AUDITS_BY_YEAR[2019]}
    assert stage0_universe_established_for_year(2019, universe_sources_by_year=only_2019, no_change_audits_by_year=only_2019_audit) is True
    assert stage0_universe_established_for_year(2020, universe_sources_by_year=only_2019, no_change_audits_by_year=only_2019_audit) is False


def test_stage0_universe_established_for_year_fails_on_non_compliant_filing_date():
    late_source = UniverseSource(
        universe_year=2019, effective_date=date(2019, 1, 2), issuer=UNIVERSE_SOURCE_2019.issuer,
        cik=UNIVERSE_SOURCE_2019.cik, form_type="N-30D", accession_number="synthetic",
        filing_date=date(2019, 1, 3),  # AFTER the effective date -- non-compliant
        period_of_report=date(2018, 10, 31), index_url="https://example.invalid", document_url="https://example.invalid",
        retrieval_method="synthetic", tickers=UNIVERSE_SOURCE_2019.tickers,
    )
    sources = {2019: late_source}
    audits = {2019: NO_CHANGE_AUDITS_BY_YEAR[2019]}
    assert stage0_universe_established_for_year(2019, universe_sources_by_year=sources, no_change_audits_by_year=audits) is False


def test_stage0_universe_established_for_year_fails_on_wrong_holdings_count():
    short_source = UniverseSource(
        universe_year=2019, effective_date=date(2019, 1, 2), issuer=UNIVERSE_SOURCE_2019.issuer,
        cik=UNIVERSE_SOURCE_2019.cik, form_type="N-30D", accession_number="synthetic",
        filing_date=date(2018, 12, 18), period_of_report=date(2018, 10, 31),
        index_url="https://example.invalid", document_url="https://example.invalid",
        retrieval_method="synthetic", tickers=UNIVERSE_SOURCE_2019.tickers[:29],  # only 29
    )
    sources = {2019: short_source}
    audits = {2019: NO_CHANGE_AUDITS_BY_YEAR[2019]}
    assert stage0_universe_established_for_year(2019, universe_sources_by_year=sources, no_change_audits_by_year=audits) is False


def test_stage0_universe_established_for_year_fails_when_no_change_audit_is_missing():
    sources = {2019: UNIVERSE_SOURCE_2019}
    assert stage0_universe_established_for_year(2019, universe_sources_by_year=sources, no_change_audits_by_year={}) is False


def test_stage0_universe_established_for_year_fails_when_audit_confirms_a_change():
    changed_audit = NoChangeAuditWindow(
        universe_year=2019, window_start=date(2018, 10, 31), window_end=date(2019, 1, 2),
        confirmed_no_change=False, corroborating_sources=("synthetic",),
    )
    sources = {2019: UNIVERSE_SOURCE_2019}
    audits = {2019: changed_audit}
    assert stage0_universe_established_for_year(2019, universe_sources_by_year=sources, no_change_audits_by_year=audits) is False


# ---------------------------------------------------------------------------
# run_stage0_gate(): the combined result.
# ---------------------------------------------------------------------------


def test_run_stage0_gate_passes_both_years_given_real_evidence():
    result = run_stage0_gate()
    assert result.orb_universe_2019_established is True
    assert result.orb_universe_2020_established is True
    assert result.etf_universes_exempt is True
    assert result.classification is None
    assert result.blocker_reason == ""
    assert result.universe_sources_by_year == UNIVERSE_SOURCES_BY_YEAR
    assert result.no_change_audits_by_year == NO_CHANGE_AUDITS_BY_YEAR


def test_run_stage0_gate_blocks_with_insufficient_evidence_when_only_one_year_passes():
    only_2019_sources = {2019: UNIVERSE_SOURCE_2019}
    only_2019_audits = {2019: NO_CHANGE_AUDITS_BY_YEAR[2019]}
    result = run_stage0_gate(universe_sources_by_year=only_2019_sources, no_change_audits_by_year=only_2019_audits)
    assert result.orb_universe_2019_established is True
    assert result.orb_universe_2020_established is False
    assert result.classification == CLASSIFICATION_INSUFFICIENT_EVIDENCE
    assert "[2020]" in result.blocker_reason
    assert "[2019" not in result.blocker_reason  # only the failed year is listed, not a mix


def test_run_stage0_gate_blocks_with_insufficient_evidence_when_neither_year_passes():
    result = run_stage0_gate(universe_sources_by_year={}, no_change_audits_by_year={})
    assert result.orb_universe_2019_established is False
    assert result.orb_universe_2020_established is False
    assert result.classification == CLASSIFICATION_INSUFFICIENT_EVIDENCE
    assert "2019" in result.blocker_reason and "2020" in result.blocker_reason


# ---------------------------------------------------------------------------
# Stage-0 item 2: corporate-action policy and coverage evaluator.
# ---------------------------------------------------------------------------


def test_dwdp_and_utx_corporate_action_policies_are_recorded():
    assert len(ORB_CORPORATE_ACTION_POLICIES) == 2
    dwdp = next(p for p in ORB_CORPORATE_ACTION_POLICIES if p.symbol == "DWDP")
    utx = next(p for p in ORB_CORPORATE_ACTION_POLICIES if p.symbol == "UTX")
    assert dwdp.universe_year == 2019
    assert dwdp.expected_last_active_date == date(2019, 5, 31)
    assert utx.universe_year == 2020
    assert utx.expected_last_active_date == date(2020, 4, 3)


def test_expected_last_active_date_for_returns_policy_date_for_dwdp_and_utx():
    assert expected_last_active_date_for("DWDP", universe_year=2019, outcome_year_end=date(2019, 12, 31)) == date(2019, 5, 31)
    assert expected_last_active_date_for("UTX", universe_year=2020, outcome_year_end=date(2020, 12, 31)) == date(2020, 4, 3)


def test_expected_last_active_date_for_returns_year_end_for_an_unaffected_symbol():
    assert expected_last_active_date_for("AAPL", universe_year=2019, outcome_year_end=date(2019, 12, 31)) == date(2019, 12, 31)
    # UTX is unaffected in 2019 (the merger policy is scoped to universe_year=2020 only)
    assert expected_last_active_date_for("UTX", universe_year=2019, outcome_year_end=date(2019, 12, 31)) == date(2019, 12, 31)


def test_iex_minute_coverage_floor_is_2020_07_27():
    assert IEX_MINUTE_COVERAGE_FLOOR == date(2020, 7, 27)


def test_xlc_etf_launch_policy_is_recorded():
    assert len(ETF_LAUNCH_POLICIES) == 1
    xlc = ETF_LAUNCH_POLICIES[0]
    assert xlc.symbol == "XLC"
    assert xlc.expected_first_active_date == date(2018, 6, 19)


def test_expected_first_active_date_for_returns_policy_date_for_xlc():
    assert expected_first_active_date_for("XLC", warmup_start=date(2018, 1, 2)) == date(2018, 6, 19)


def test_expected_first_active_date_for_returns_warmup_start_for_an_unaffected_symbol():
    assert expected_first_active_date_for("SPY", warmup_start=date(2018, 1, 2)) == date(2018, 1, 2)


def test_data_coverage_check_missing_dates_and_ok_with_full_coverage():
    expected = (date(2019, 1, 2), date(2019, 1, 3), date(2019, 1, 4))
    check = DataCoverageCheck(
        symbol="AAPL", data_kind="orb_minute_iex", requested_start=date(2019, 1, 2), requested_end=date(2019, 1, 4),
        expected_last_active_date=date(2019, 1, 4), expected_session_dates=expected, received_session_dates=expected,
    )
    assert check.missing_dates == ()
    assert check.ok is True


def test_data_coverage_check_detects_a_missing_session():
    expected = (date(2019, 1, 2), date(2019, 1, 3), date(2019, 1, 4))
    received = (date(2019, 1, 2), date(2019, 1, 4))
    check = DataCoverageCheck(
        symbol="AAPL", data_kind="orb_minute_iex", requested_start=date(2019, 1, 2), requested_end=date(2019, 1, 4),
        expected_last_active_date=date(2019, 1, 4), expected_session_dates=expected, received_session_dates=received,
    )
    assert check.missing_dates == (date(2019, 1, 3),)
    assert check.ok is False


def test_data_coverage_check_detects_integrity_violations_even_with_full_coverage():
    expected = (date(2019, 1, 2),)
    check = DataCoverageCheck(
        symbol="AAPL", data_kind="orb_minute_iex", requested_start=date(2019, 1, 2), requested_end=date(2019, 1, 2),
        expected_last_active_date=date(2019, 1, 2), expected_session_dates=expected, received_session_dates=expected,
        ohlc_integrity_violations=("high < low on 2019-01-02",),
    )
    assert check.missing_dates == ()
    assert check.ok is False


def test_evaluate_stage0_item2_coverage_passes_when_all_checks_ok():
    expected = (date(2019, 1, 2),)
    checks = (
        DataCoverageCheck(symbol="AAPL", data_kind="orb_minute_iex", requested_start=date(2019, 1, 2), requested_end=date(2019, 1, 2), expected_last_active_date=date(2019, 1, 2), expected_session_dates=expected, received_session_dates=expected),
        DataCoverageCheck(symbol="XLK", data_kind="etf_daily_sip", requested_start=date(2019, 1, 2), requested_end=date(2019, 1, 2), expected_last_active_date=date(2019, 1, 2), expected_session_dates=expected, received_session_dates=expected),
    )
    ok, reason = evaluate_stage0_item2_coverage(checks)
    assert ok is True
    assert reason == ""


def test_evaluate_stage0_item2_coverage_fails_and_names_the_symbol_when_a_check_fails():
    expected = (date(2019, 1, 2), date(2019, 1, 3))
    checks = (
        DataCoverageCheck(symbol="AAPL", data_kind="orb_minute_iex", requested_start=date(2019, 1, 2), requested_end=date(2019, 1, 3), expected_last_active_date=date(2019, 1, 3), expected_session_dates=expected, received_session_dates=(date(2019, 1, 2),)),
    )
    ok, reason = evaluate_stage0_item2_coverage(checks)
    assert ok is False
    assert "AAPL" in reason
    assert "1 missing session" in reason


def test_evaluate_stage0_item2_coverage_fails_closed_on_empty_checks():
    ok, reason = evaluate_stage0_item2_coverage(())
    assert ok is False
    assert reason != ""


def test_m49_frozen_reference_is_labeled_as_reference_only_and_not_recombined():
    assert M49_FROZEN_REFERENCE["classification"] == "SAFE_BUT_BELOW_TARGET"
    assert M49_FROZEN_REFERENCE["evaluation_window"] == "2022-01-03..2026-08-13"
    assert "not combine" in M49_FROZEN_REFERENCE["note"] or "does not combine" in M49_FROZEN_REFERENCE["note"]
