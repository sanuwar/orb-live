"""Focused tests for the M46 universe-selection pure logic:
issuer-name normalization / share-class dedup, cross-fund overlap
resolution, median-based liquidity screening, and deterministic
sector-capped selection. No network I/O anywhere in this module."""

from __future__ import annotations

import pytest

from src.orb_small_midcap_momentum_research import (
    CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS,
    CONCENTRATION_STATUS_OK,
    ConcentrationResult,
    DailyBar,
    FeasibilityMetrics,
    HoldingCandidate,
    LiquidityProfile,
    MAX_PER_SECTOR,
    MAX_SYMBOL_SHARE_OF_POSITIVE_R,
    MIN_CLOSED_TRADES_FOR_EVIDENCE,
    MIN_CLOSED_TRADES_FOR_PROMISING,
    MIN_MATERIAL_POSITION_WEIGHT,
    MIN_MEDIAN_CLOSE_PRICE,
    MIN_MEDIAN_DOLLAR_VOLUME,
    MIN_MEDIAN_SHARE_VOLUME,
    MIN_PROFIT_FACTOR_R,
    MIN_SECTORS_WITH_CLOSED_TRADES,
    MIN_VALID_SESSION_COVERAGE_PCT,
    SelectionCandidate,
    TARGET_PER_BUCKET,
    UniverseSelectionError,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_MIXED,
    VERDICT_NOT_PROMISING,
    VERDICT_PROMISING,
    classify_feasibility,
    compute_liquidity_profile,
    dedupe_share_classes,
    filter_material_positions,
    normalize_issuer_name,
    positive_contribution_concentration,
    research_config_for_universe,
    resolve_cross_fund_overlap,
    select_sector_capped_basket,
)


# --------------------------------------------------------------------------
# normalize_issuer_name
# --------------------------------------------------------------------------

def test_normalize_issuer_name_strips_class_suffix():
    assert normalize_issuer_name("Foo Corp, Class A") == "FOO CORP"


def test_normalize_issuer_name_strips_cl_abbreviation():
    assert normalize_issuer_name("Bar Inc Cl B") == "BAR INC"


def test_normalize_issuer_name_strips_series_suffix():
    assert normalize_issuer_name("Baz Trust Series C") == "BAZ TRUST"


def test_normalize_issuer_name_preserves_corporate_suffix():
    assert normalize_issuer_name("Acme, Inc.") == "ACME, INC."


def test_normalize_issuer_name_no_suffix_is_unchanged_but_uppercased():
    assert normalize_issuer_name("Plain Co") == "PLAIN CO"


def test_normalize_issuer_name_rejects_non_string():
    with pytest.raises(UniverseSelectionError):
        normalize_issuer_name(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# filter_material_positions
# --------------------------------------------------------------------------

def test_filter_material_positions_excludes_negligible_weight_holding():
    real = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.01)
    residual = HoldingCandidate(source_fund="IWR", resolved_ticker="CVX", issuer_name="Chevron Corp.", pct_net_assets=0.0000011)
    material, negligible = filter_material_positions([real, residual])
    assert material == [real]
    assert negligible == [residual]


def test_filter_material_positions_boundary_is_inclusive():
    boundary = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=MIN_MATERIAL_POSITION_WEIGHT)
    material, negligible = filter_material_positions([boundary])
    assert material == [boundary]
    assert negligible == []


def test_filter_material_positions_respects_custom_threshold():
    c = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.0005)
    material, negligible = filter_material_positions([c], min_weight=0.001)
    assert material == []
    assert negligible == [c]


# --------------------------------------------------------------------------
# dedupe_share_classes
# --------------------------------------------------------------------------

def test_dedupe_keeps_larger_weight_row_for_same_ticker():
    a = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.01)
    b = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp Adr", pct_net_assets=0.02)
    kept, excluded = dedupe_share_classes([a, b])
    assert kept == [b]
    assert excluded == [a]


def test_dedupe_collapses_distinct_tickers_of_same_issuer_share_classes():
    a = HoldingCandidate(source_fund="IWM", resolved_ticker="FOOA", issuer_name="Foo Corp, Class A", pct_net_assets=0.03)
    b = HoldingCandidate(source_fund="IWM", resolved_ticker="FOOB", issuer_name="Foo Corp, Class B", pct_net_assets=0.01)
    kept, excluded = dedupe_share_classes([a, b])
    assert kept == [a]
    assert excluded == [b]


def test_dedupe_does_not_merge_across_different_funds():
    a = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.01)
    b = HoldingCandidate(source_fund="IWM", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.01)
    kept, excluded = dedupe_share_classes([a, b])
    assert set(kept) == {a, b}
    assert excluded == []


def test_dedupe_does_not_merge_distinct_issuers():
    a = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.01)
    b = HoldingCandidate(source_fund="IWR", resolved_ticker="BAR", issuer_name="Bar Corp", pct_net_assets=0.01)
    kept, excluded = dedupe_share_classes([a, b])
    assert set(kept) == {a, b}
    assert excluded == []


def test_holding_candidate_requires_source_fund_and_ticker():
    with pytest.raises(UniverseSelectionError):
        HoldingCandidate(source_fund="", resolved_ticker="FOO", issuer_name="Foo", pct_net_assets=0.01)
    with pytest.raises(UniverseSelectionError):
        HoldingCandidate(source_fund="IWR", resolved_ticker="", issuer_name="Foo", pct_net_assets=0.01)


# --------------------------------------------------------------------------
# resolve_cross_fund_overlap
# --------------------------------------------------------------------------

def test_cross_fund_overlap_assigns_to_larger_weight_fund():
    mid = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.05)
    small = HoldingCandidate(source_fund="IWM", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.02)
    winners, losers = resolve_cross_fund_overlap([mid, small])
    assert winners == [mid]
    assert losers == [small]


def test_cross_fund_overlap_no_overlap_all_win():
    a = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.05)
    b = HoldingCandidate(source_fund="IWM", resolved_ticker="BAR", issuer_name="Bar Corp", pct_net_assets=0.02)
    winners, losers = resolve_cross_fund_overlap([a, b])
    assert set(winners) == {a, b}
    assert losers == []


def test_cross_fund_overlap_tie_break_is_deterministic_by_fund_name():
    a = HoldingCandidate(source_fund="IWR", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.05)
    b = HoldingCandidate(source_fund="IWM", resolved_ticker="FOO", issuer_name="Foo Corp", pct_net_assets=0.05)
    winners, losers = resolve_cross_fund_overlap([a, b])
    # Equal weight -> alphabetically-first source_fund wins ("IWM" < "IWR").
    assert winners == [b]
    assert losers == [a]


# --------------------------------------------------------------------------
# compute_liquidity_profile
# --------------------------------------------------------------------------

def _bars(n, *, close=50.0, volume=2_000_000):
    return [DailyBar(close=close, volume=volume) for _ in range(n)]


def test_liquidity_profile_eligible_when_all_floors_cleared():
    bars = _bars(60, close=50.0, volume=2_000_000)  # $100M/day, well above every floor
    profile = compute_liquidity_profile("FOO", bars, window_session_count=60)
    assert profile.eligible is True
    assert profile.exclusion_reasons == ()
    assert profile.session_coverage_pct == 1.0


def test_liquidity_profile_rejects_below_dollar_volume_floor():
    # $10/share * 100k shares = $1M/day, well under the $75M floor.
    bars = _bars(60, close=10.0, volume=100_000)
    profile = compute_liquidity_profile("FOO", bars, window_session_count=60)
    assert profile.eligible is False
    assert "MEDIAN_DOLLAR_VOLUME_BELOW_FLOOR" in profile.exclusion_reasons


def test_liquidity_profile_rejects_below_share_volume_floor():
    # High price, low share count: dollar volume can still clear while
    # share volume does not -- floors are independent, both must clear.
    bars = _bars(60, close=1000.0, volume=100_000)  # $100M/day dollar vol, 100k shares
    profile = compute_liquidity_profile("FOO", bars, window_session_count=60)
    assert profile.eligible is False
    assert "MEDIAN_SHARE_VOLUME_BELOW_FLOOR" in profile.exclusion_reasons
    assert "MEDIAN_DOLLAR_VOLUME_BELOW_FLOOR" not in profile.exclusion_reasons


def test_liquidity_profile_rejects_below_price_floor():
    bars = _bars(60, close=5.0, volume=50_000_000)  # huge dollar/share vol, penny-adjacent price
    profile = compute_liquidity_profile("FOO", bars, window_session_count=60)
    assert profile.eligible is False
    assert "MEDIAN_CLOSE_PRICE_BELOW_FLOOR" in profile.exclusion_reasons


def test_liquidity_profile_rejects_below_coverage_floor():
    bars = _bars(50, close=50.0, volume=2_000_000)  # only 50/60 sessions present
    profile = compute_liquidity_profile("FOO", bars, window_session_count=60)
    assert profile.eligible is False
    assert "SESSION_COVERAGE_BELOW_FLOOR" in profile.exclusion_reasons


def test_liquidity_profile_no_bars_is_ineligible_with_reason():
    profile = compute_liquidity_profile("FOO", [], window_session_count=60)
    assert profile.eligible is False
    assert profile.exclusion_reasons == ("NO_BARS_IN_WINDOW",)
    assert profile.sessions_observed == 0


def test_liquidity_profile_uses_median_not_mean_so_one_outlier_day_does_not_flip_eligibility():
    # 59 illiquid days plus one enormous halo day: the mean would clear the
    # floor, the median (correctly) still does not.
    bars = _bars(59, close=10.0, volume=100_000) + [DailyBar(close=10.0, volume=100_000_000)]
    profile = compute_liquidity_profile("FOO", bars, window_session_count=60)
    assert profile.eligible is False
    assert "MEDIAN_DOLLAR_VOLUME_BELOW_FLOOR" in profile.exclusion_reasons


def test_liquidity_profile_rejects_non_positive_window():
    with pytest.raises(UniverseSelectionError):
        compute_liquidity_profile("FOO", [], window_session_count=0)


def test_liquidity_profile_cannot_be_constructed_eligible_with_reasons():
    with pytest.raises(UniverseSelectionError):
        LiquidityProfile(
            symbol="FOO", sessions_observed=60, window_session_count=60,
            session_coverage_pct=1.0, median_dollar_volume=1e9, median_share_volume=1e6,
            median_close_price=50.0, eligible=True, exclusion_reasons=("SOMETHING",),
        )


def test_liquidity_profile_cannot_be_constructed_ineligible_without_reasons():
    with pytest.raises(UniverseSelectionError):
        LiquidityProfile(
            symbol="FOO", sessions_observed=60, window_session_count=60,
            session_coverage_pct=1.0, median_dollar_volume=1e9, median_share_volume=1e6,
            median_close_price=50.0, eligible=False, exclusion_reasons=(),
        )


def test_frozen_liquidity_thresholds_match_milestone_spec():
    assert MIN_MEDIAN_DOLLAR_VOLUME == 75_000_000.0
    assert MIN_MEDIAN_SHARE_VOLUME == 750_000.0
    assert MIN_MEDIAN_CLOSE_PRICE == 10.00
    assert MIN_VALID_SESSION_COVERAGE_PCT == 0.95


# --------------------------------------------------------------------------
# select_sector_capped_basket
# --------------------------------------------------------------------------

def _candidate(ticker, sector, dollar_vol):
    return SelectionCandidate(ticker=ticker, sector=sector, median_dollar_volume=dollar_vol)


def test_selection_picks_highest_dollar_volume_first():
    pool = [_candidate("LOW", "TECH", 1), _candidate("HIGH", "TECH", 100)]
    result = select_sector_capped_basket(pool, target_per_bucket=1, max_per_sector=2)
    assert [c.ticker for c in result.selected] == ["HIGH"]


def test_selection_tie_breaks_by_ticker_ascending():
    pool = [_candidate("BBB", "TECH", 50), _candidate("AAA", "TECH", 50)]
    result = select_sector_capped_basket(pool, target_per_bucket=1, max_per_sector=2)
    assert [c.ticker for c in result.selected] == ["AAA"]


def test_selection_enforces_max_per_sector_cap():
    pool = [
        _candidate("T1", "TECH", 100), _candidate("T2", "TECH", 90), _candidate("T3", "TECH", 80),
        _candidate("H1", "HEALTH", 70),
    ]
    result = select_sector_capped_basket(pool, target_per_bucket=3, max_per_sector=2)
    selected_tickers = [c.ticker for c in result.selected]
    assert selected_tickers == ["T1", "T2", "H1"]
    assert [c.ticker for c in result.excluded_sector_cap] == ["T3"]


def test_selection_reports_shortfall_when_bucket_cannot_reach_target():
    pool = [_candidate("A", "TECH", 100), _candidate("B", "TECH", 90)]
    result = select_sector_capped_basket(pool, target_per_bucket=10, max_per_sector=2)
    assert len(result.selected) == 2
    assert result.shortfall == 8


def test_selection_shortfall_is_zero_when_target_met():
    pool = [_candidate(f"T{i}", "TECH", 100 - i) for i in range(5)]
    result = select_sector_capped_basket(pool, target_per_bucket=5, max_per_sector=5)
    assert result.shortfall == 0
    assert len(result.selected) == 5


def test_selection_excludes_beyond_target_even_when_sector_cap_not_hit():
    pool = [_candidate(f"T{i}", f"SECTOR{i}", 100 - i) for i in range(5)]
    result = select_sector_capped_basket(pool, target_per_bucket=2, max_per_sector=2)
    assert len(result.selected) == 2
    assert len(result.excluded_beyond_target) == 3


def test_default_thresholds_match_milestone_spec():
    assert TARGET_PER_BUCKET == 10
    assert MAX_PER_SECTOR == 2


def test_selection_is_deterministic_across_repeated_calls():
    pool = [_candidate(f"T{i}", "TECH" if i % 2 == 0 else "HEALTH", float(100 - i)) for i in range(20)]
    r1 = select_sector_capped_basket(pool, target_per_bucket=6, max_per_sector=2)
    r2 = select_sector_capped_basket(list(reversed(pool)), target_per_bucket=6, max_per_sector=2)
    assert [c.ticker for c in r1.selected] == [c.ticker for c in r2.selected]


# --------------------------------------------------------------------------
# research_config_for_universe
# --------------------------------------------------------------------------

def _controlled_base_config():
    """A minimal but shape-accurate stand-in for `src.config.load_config()`
    -- controlled, not the real tracked file, so these tests never touch
    config/strategy_v0.yaml and never depend on its contents changing."""
    return {
        "strategy": {"name": "orb_long_v0", "version": "0.1.0", "timezone": "America/New_York", "enabled": False},
        "universe": {"symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "AMD", "JPM", "XOM", "LLY", "COST", "WMT", "NFLX"]},
        "signal": {
            "minimum_gap_percent": 1.0, "opening_range_minutes": 5, "minimum_relative_volume": 3.0,
            "relative_volume_lookback_days": 20, "require_spy_above_vwap": True,
            "entry_start": "09:40", "entry_end": "11:00", "one_entry_per_symbol_per_day": True,
        },
        "exit": {
            "initial_stop": "opening_range_low", "move_stop_to_break_even_at_r": 1.0,
            "full_profit_exit_at_r": 2.0, "time_exit": "15:45", "overnight_positions_allowed": False,
        },
        "risk": {"risk_per_trade_usd": 15, "maximum_position_usd": 3000, "maximum_open_positions": 1},
    }


def test_research_config_replaces_only_universe_symbols():
    base = _controlled_base_config()
    research_universe = ("SNDK", "LITE", "WDC")
    overridden = research_config_for_universe(base, research_universe)
    assert overridden["universe"]["symbols"] == list(research_universe)


def test_research_config_deep_copies_and_never_mutates_base():
    base = _controlled_base_config()
    original_symbols = list(base["universe"]["symbols"])
    research_config_for_universe(base, ("SNDK", "LITE"))
    assert base["universe"]["symbols"] == original_symbols


def test_research_config_preserves_strategy_enabled_false():
    base = _controlled_base_config()
    overridden = research_config_for_universe(base, ("SNDK",))
    assert overridden["strategy"]["enabled"] is False


def test_research_config_preserves_frozen_signal_and_risk_settings():
    base = _controlled_base_config()
    overridden = research_config_for_universe(base, ("SNDK",))
    assert overridden["signal"] == base["signal"]
    assert overridden["exit"] == base["exit"]
    assert overridden["risk"] == base["risk"]


def test_research_config_membership_contract_allows_research_symbol():
    """The whole point of the override: a research-only symbol is allowed
    under the overridden copy (would otherwise be rejected by
    SignalEngine's universe-membership gate), while the original
    production-universe object is completely untouched."""
    base = _controlled_base_config()
    overridden = research_config_for_universe(base, ("SNDK",))
    assert "SNDK" in overridden["universe"]["symbols"]
    assert "SNDK" not in base["universe"]["symbols"]
    assert "AAPL" in base["universe"]["symbols"]


def test_research_config_rejects_empty_universe():
    base = _controlled_base_config()
    with pytest.raises(UniverseSelectionError):
        research_config_for_universe(base, ())


# --------------------------------------------------------------------------
# positive_contribution_concentration
# --------------------------------------------------------------------------

def test_concentration_positive_total_lane_computes_correct_share():
    # A winning lane: HOOD dominates, but positive_total is the sum of
    # ONLY the positive contributors (BE contributes nothing to the
    # denominator despite being in the mapping).
    result = positive_contribution_concentration({"HOOD": 3.0, "BE": 0.5, "WULF": -1.0})
    assert result.status == CONCENTRATION_STATUS_OK
    assert result.top_key == "HOOD"
    assert result.positive_total == pytest.approx(3.5)
    assert result.share == pytest.approx(3.0 / 3.5)


def test_concentration_negative_net_lane_with_positive_contributor_is_not_a_free_pass():
    # This is exactly Finding 1's bug case: net total is negative, but the
    # correct denominator is the positive-only sum, and the single positive
    # contributor is 100% of it -- must NOT report a small/negative share.
    contributions = {"HOOD": 1.0636420966180742, "BE": 0.0, "SNDK": 0.0, "CRDO": -0.1044,
                      "APLD": -0.2946, "HIMS": -1.0, "CDE": -1.0, "LITE": -1.0, "WULF": -2.0}
    result = positive_contribution_concentration(contributions)
    assert result.status == CONCENTRATION_STATUS_OK
    assert result.top_key == "HOOD"
    assert result.positive_total == pytest.approx(1.0636420966180742)
    assert result.share == pytest.approx(1.0)
    assert result.share > MAX_SYMBOL_SHARE_OF_POSITIVE_R  # correctly fails the 50% gate


def test_concentration_no_positive_contributors_is_explicit_not_a_pass():
    result = positive_contribution_concentration({"HOOD": -1.0, "BE": -0.5, "WULF": 0.0})
    assert result.status == CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS
    assert result.share is None
    assert result.top_key is None


def test_concentration_empty_mapping_is_no_positive_contributors():
    result = positive_contribution_concentration({})
    assert result.status == CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS


def test_concentration_tie_break_is_deterministic():
    result = positive_contribution_concentration({"BBB": 1.0, "AAA": 1.0})
    assert result.top_key == "AAA"


def test_concentration_result_ok_requires_share_and_top_key():
    with pytest.raises(UniverseSelectionError):
        ConcentrationResult(top_key=None, top_contribution=None, positive_total=1.0, share=None, status=CONCENTRATION_STATUS_OK)


def test_concentration_result_no_positive_forbids_top_key():
    with pytest.raises(UniverseSelectionError):
        ConcentrationResult(top_key="X", top_contribution=1.0, positive_total=0.0, share=None, status=CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS)


# --------------------------------------------------------------------------
# classify_feasibility
# --------------------------------------------------------------------------

def _promising_metrics(**overrides):
    defaults = dict(
        closed_trades=25, expectancy_r=0.3, profit_factor_r=1.5, trades_per_month=5.0,
        cumulative_r=6.0, expectancy_r_excluding_best_symbol=0.1, sectors_with_closed_trades=5,
        symbol_r_contributions={"A": 2.0, "B": 1.5, "C": 1.0, "D": 1.0, "E": 0.5},
        mfe_pct_reaching_050r=0.6, mfe_pct_reaching_100r=0.3,
    )
    defaults.update(overrides)
    return FeasibilityMetrics(**defaults)


def test_classify_insufficient_evidence_below_ten_closed():
    result = classify_feasibility(
        small_mid=_promising_metrics(closed_trades=9),
        large_cap_trades_per_month=2.0, large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["verdict"] == VERDICT_INSUFFICIENT_EVIDENCE
    assert result["criteria"] == {}


def test_classify_fully_passing_synthetic_case_is_promising():
    result = classify_feasibility(
        small_mid=_promising_metrics(),
        large_cap_trades_per_month=2.0, large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["verdict"] == VERDICT_PROMISING
    assert all(result["criteria"].values())
    assert result["reasons"] == []


def test_classify_mixed_when_positive_expectancy_pf_gt_1_but_secondary_criteria_fail():
    # PF_R > 1.0 but <= 1.10 (fails c3); expectancy still positive -> MIXED.
    metrics = _promising_metrics(profit_factor_r=1.05)
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["verdict"] == VERDICT_MIXED
    assert "c3_pf_r_gt_1_10" in result["reasons"]


def test_classify_not_promising_on_negative_expectancy():
    metrics = _promising_metrics(expectancy_r=-0.2, profit_factor_r=0.5, cumulative_r=-4.0)
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["verdict"] == VERDICT_NOT_PROMISING


def test_classify_not_promising_on_pf_r_at_or_below_one():
    metrics = _promising_metrics(profit_factor_r=1.0)
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["verdict"] == VERDICT_NOT_PROMISING


def test_classify_uses_corrected_concentration_for_positive_total_lane():
    metrics = _promising_metrics(symbol_r_contributions={"A": 2.0, "B": 2.0})  # 50/50, exactly at boundary
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["criteria"]["c8_symbol_share_of_positive_r_le_50pct"] is True
    assert result["concentration"].share == pytest.approx(0.5)


def test_classify_negative_net_r_lane_with_positive_contributor_fails_concentration_correctly():
    # Reproduces Finding 1 end-to-end through classify_feasibility: net
    # cumulative_r is negative, but the single positive symbol is 100% of
    # positive contribution -- c8 must FAIL, not silently pass.
    metrics = _promising_metrics(
        expectancy_r=-0.2, profit_factor_r=0.35, cumulative_r=-4.34,
        symbol_r_contributions={"HOOD": 1.06, "WULF": -2.0, "LITE": -1.0, "HIMS": -1.0, "CDE": -1.0},
    )
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["concentration"].status == CONCENTRATION_STATUS_OK
    assert result["concentration"].share == pytest.approx(1.0)
    assert result["criteria"]["c8_symbol_share_of_positive_r_le_50pct"] is False
    assert result["verdict"] == VERDICT_NOT_PROMISING


def test_classify_one_symbol_exceeding_50pct_of_positive_contribution_fails_c8():
    metrics = _promising_metrics(symbol_r_contributions={"A": 3.0, "B": 1.0})  # A = 75%
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["criteria"]["c8_symbol_share_of_positive_r_le_50pct"] is False
    assert result["verdict"] != VERDICT_PROMISING


def test_classify_no_positive_contributors_does_not_pass_c8():
    metrics = _promising_metrics(symbol_r_contributions={"A": -1.0, "B": -2.0})
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["concentration"].status == CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS
    assert result["criteria"]["c8_symbol_share_of_positive_r_le_50pct"] is False


def test_classify_mfe_edge_boundary_is_strict_greater_than():
    # Exactly equal to LARGE_CAP at both thresholds -> c9 fails (strict >).
    metrics = _promising_metrics(mfe_pct_reaching_050r=0.4, mfe_pct_reaching_100r=0.1)
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["criteria"]["c9a_mfe_050_edge"] is False
    assert result["criteria"]["c9b_mfe_100_edge"] is False
    assert result["criteria"]["c9_mfe_edge_050_or_100"] is False


def test_classify_mfe_edge_passes_on_either_threshold_alone():
    metrics = _promising_metrics(mfe_pct_reaching_050r=0.3, mfe_pct_reaching_100r=0.2)  # only 100R edge clears
    result = classify_feasibility(
        small_mid=metrics, large_cap_trades_per_month=2.0,
        large_cap_mfe_pct_reaching_050r=0.4, large_cap_mfe_pct_reaching_100r=0.1,
    )
    assert result["criteria"]["c9a_mfe_050_edge"] is False
    assert result["criteria"]["c9b_mfe_100_edge"] is True
    assert result["criteria"]["c9_mfe_edge_050_or_100"] is True


def test_classify_sector_concentration_is_not_a_gate():
    """Sector-level concentration is a reporting diagnostic only -- the
    frozen 9-criterion spec gates on symbol-level concentration alone.
    classify_feasibility has no sector-share input at all, which is itself
    the regression guard: it cannot fail on sector concentration."""
    import inspect
    params = inspect.signature(classify_feasibility).parameters
    assert not any("sector_share" in p or "sector_r" in p for p in params)
