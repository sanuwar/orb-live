"""Focused tests for the M46 universe-selection pure logic:
issuer-name normalization / share-class dedup, cross-fund overlap
resolution, median-based liquidity screening, and deterministic
sector-capped selection. No network I/O anywhere in this module."""

from __future__ import annotations

import pytest

from src.orb_small_midcap_momentum_research import (
    DailyBar,
    HoldingCandidate,
    LiquidityProfile,
    MAX_PER_SECTOR,
    MIN_MATERIAL_POSITION_WEIGHT,
    MIN_MEDIAN_CLOSE_PRICE,
    MIN_MEDIAN_DOLLAR_VOLUME,
    MIN_MEDIAN_SHARE_VOLUME,
    MIN_VALID_SESSION_COVERAGE_PCT,
    SelectionCandidate,
    TARGET_PER_BUCKET,
    UniverseSelectionError,
    compute_liquidity_profile,
    dedupe_share_classes,
    filter_material_positions,
    normalize_issuer_name,
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
