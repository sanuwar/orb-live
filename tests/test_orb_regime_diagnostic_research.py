from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from src.exchange_calendar import EASTERN
from src.orb_regime_diagnostic_research import (
    DECISION_TIME_FIELDS,
    MIN_SAMPLE_CLOSED_TRADES,
    OUTCOME_FIELDS,
    SpyDayContext,
    breakout_time_bucket,
    classify_regime_signal,
    gap_bucket,
    or_width_bucket,
    regime_summary,
    rvol_bucket,
    spy_daily_context,
    spy_gap_direction,
    spy_vwap_premium_bucket,
    symbol_summary,
    trailing_realized_volatility,
    volatility_bucket,
    TradeDiagnosticRow,
)


# ---------------------------------------------------------------------------
# Bucket boundary correctness. Exact, non-overlapping.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    (Decimal("0.50"), "0.50-0.74"),
    (Decimal("0.74"), "0.50-0.74"),
    (Decimal("0.75"), "0.75-0.99"),
    (Decimal("0.99"), "0.75-0.99"),
    (Decimal("1.00"), "1.00-1.49"),
    (Decimal("1.49"), "1.00-1.49"),
    (Decimal("1.50"), ">=1.50"),
    (Decimal("5.00"), ">=1.50"),
])
def test_gap_bucket_boundaries(value, expected):
    assert gap_bucket(value) == expected


@pytest.mark.parametrize("value, expected", [
    (Decimal("1.25"), "1.25-1.49"),
    (Decimal("1.49"), "1.25-1.49"),
    (Decimal("1.50"), "1.50-1.99"),
    (Decimal("2.00"), "2.00-2.99"),
    (Decimal("3.00"), ">=3.00"),
])
def test_rvol_bucket_boundaries(value, expected):
    assert rvol_bucket(value) == expected


@pytest.mark.parametrize("value, expected", [
    (time(9, 40), "09:40-09:59"),
    (time(9, 59), "09:40-09:59"),
    (time(10, 0), "10:00-10:29"),
    (time(10, 29), "10:00-10:29"),
    (time(10, 30), "10:30-11:00"),
    (time(11, 0), "10:30-11:00"),
])
def test_breakout_time_bucket_boundaries(value, expected):
    assert breakout_time_bucket(value) == expected


def test_breakout_time_bucket_out_of_window():
    assert breakout_time_bucket(time(9, 30)) == "OUT_OF_WINDOW"
    assert breakout_time_bucket(time(11, 5)) == "OUT_OF_WINDOW"


def test_spy_vwap_premium_bucket_boundaries():
    assert spy_vwap_premium_bucket(-0.01) == "BELOW_VWAP"
    assert spy_vwap_premium_bucket(0.0) == "0.00-0.05_ABOVE"
    assert spy_vwap_premium_bucket(0.049) == "0.00-0.05_ABOVE"
    assert spy_vwap_premium_bucket(0.05) == "0.05-0.15_ABOVE"
    assert spy_vwap_premium_bucket(0.15) == ">=0.15_ABOVE"


def test_spy_gap_direction_boundaries():
    assert spy_gap_direction(0.06) == "UP"
    assert spy_gap_direction(-0.06) == "DOWN"
    assert spy_gap_direction(0.0) == "FLAT"
    assert spy_gap_direction(0.04) == "FLAT"


def test_or_width_bucket_boundaries():
    assert or_width_bucket(0.49) == "<0.50"
    assert or_width_bucket(0.50) == "0.50-0.99"
    assert or_width_bucket(1.0) == "1.00-1.99"
    assert or_width_bucket(2.0) == ">=2.00"


def test_volatility_bucket_boundaries():
    assert volatility_bucket(None) == "UNKNOWN"
    assert volatility_bucket(0.49) == "LOW_<0.50"
    assert volatility_bucket(0.99) == "MODERATE_0.50-0.99"
    assert volatility_bucket(1.5) == "HIGH_>=1.00"


# ---------------------------------------------------------------------------
# No look-ahead.
# ---------------------------------------------------------------------------


def test_decision_time_and_outcome_field_sets_are_disjoint():
    assert set(DECISION_TIME_FIELDS).isdisjoint(set(OUTCOME_FIELDS))


def test_decision_time_fields_exclude_known_lookahead_terms():
    forbidden = ("realized_pnl", "exit_price", "exit_reason", "final", "daily_close", "state")
    for field in DECISION_TIME_FIELDS:
        for term in forbidden:
            assert term not in field, f"{field!r} looks outcome-derived, not decision-time"


def test_spy_daily_context_prior_close_never_reads_same_or_later_day():
    from datetime import timedelta

    from src.market_data_canonical import MarketBar, MarketDataQuality
    from src.market_data_provider import ALPACA_IEX_SOURCE

    def bar(day, hh, mm, close):
        ts = datetime(day.year, day.month, day.day, hh, mm, tzinfo=EASTERN).astimezone(timezone.utc)
        return MarketBar(
            symbol="SPY", exchange_session="REGULAR", trading_date=day, event_timestamp=ts,
            end_timestamp=ts + timedelta(minutes=1),
            receive_timestamp=ts, open=Decimal("500"), high=Decimal("501"), low=Decimal("499"),
            close=Decimal(str(close)), volume=1000, source=ALPACA_IEX_SOURCE, interval_seconds=60,
            provider_sequence_id=None, quality=MarketDataQuality.VALID,
        ).to_minute_bar()

    day1, day2, day3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
    bars = [bar(day1, 9, 30, 500), bar(day1, 15, 59, 502), bar(day2, 9, 30, 502), bar(day2, 15, 59, 505),
            bar(day3, 9, 30, 505), bar(day3, 15, 59, 490)]
    contexts = spy_daily_context(bars)
    assert contexts[day1].prior_close is None
    assert contexts[day2].prior_close == Decimal("502")
    assert contexts[day3].prior_close == Decimal("505")
    # day3's own (large) drop to 490 must never leak into day2's or day1's context
    assert contexts[day2].daily_close == Decimal("505")


def test_trailing_realized_volatility_excludes_target_day_and_requires_full_window():
    contexts = {
        date(2026, 1, d): SpyDayContext(date(2026, 1, d), None, None, Decimal(str(500 + d)))
        for d in range(1, 25)
    }
    # fewer than lookback_days+1 prior days -> None
    assert trailing_realized_volatility(contexts, before=date(2026, 1, 10), lookback_days=20) is None
    # with a day(25) present but target day itself excluded from computation
    contexts[date(2026, 1, 25)] = SpyDayContext(date(2026, 1, 25), None, None, Decimal("999999"))
    vol_before_25 = trailing_realized_volatility(contexts, before=date(2026, 1, 25), lookback_days=20)
    assert vol_before_25 is not None
    assert vol_before_25 < 5.0  # the extreme day-25 value must not have influenced this


# ---------------------------------------------------------------------------
# Minimum-sample classification and regime summary aggregation.
# ---------------------------------------------------------------------------


def _row(*, bucket_value, pnl, symbol="AAPL", closed=True):
    return TradeDiagnosticRow(
        decision={"gap_bucket": bucket_value, "symbol": symbol},
        outcome={
            "closed": closed, "is_winner": pnl > 0 if closed else None, "is_stopped": pnl <= 0 if closed else False,
            "realized_pnl": str(pnl) if closed else None, "r_multiple": float(pnl) if closed else None,
        },
    )


def test_regime_summary_reports_sample_size_and_meets_minimum_flag():
    rows = [_row(bucket_value="A", pnl=Decimal("1")) for _ in range(10)]
    summary = regime_summary(rows, key_field="gap_bucket")
    assert summary["A"]["closed_trades"] == 10
    assert summary["A"]["meets_minimum_sample"] is False  # below MIN_SAMPLE_CLOSED_TRADES


def test_regime_summary_meets_minimum_at_threshold():
    rows = [_row(bucket_value="A", pnl=Decimal("1")) for _ in range(MIN_SAMPLE_CLOSED_TRADES)]
    summary = regime_summary(rows, key_field="gap_bucket")
    assert summary["A"]["meets_minimum_sample"] is True


def test_regime_summary_win_rate_and_expectancy():
    rows = (
        [_row(bucket_value="A", pnl=Decimal("2")) for _ in range(5)]
        + [_row(bucket_value="A", pnl=Decimal("-1")) for _ in range(5)]
    )
    summary = regime_summary(rows, key_field="gap_bucket")["A"]
    assert summary["win_rate"] == 0.5
    assert summary["expectancy_per_trade"] == pytest.approx(0.5)  # (5*2 - 5*1)/10


def test_classify_regime_signal_insufficient_sample_below_minimum():
    small = regime_summary([_row(bucket_value="A", pnl=Decimal("1")) for _ in range(5)], key_field="gap_bucket")["A"]
    result = classify_regime_signal(oos_bucket_stats=small, discovery_bucket_stats=small, baseline_expectancy=0.0)
    assert result["verdict"] == "INSUFFICIENT_SAMPLE"


def test_classify_regime_signal_promising_when_both_periods_improve_materially():
    good_rows = [_row(bucket_value="A", pnl=Decimal("2")) for _ in range(MIN_SAMPLE_CLOSED_TRADES)]
    good = regime_summary(good_rows, key_field="gap_bucket")["A"]
    result = classify_regime_signal(
        oos_bucket_stats=good, discovery_bucket_stats=good, baseline_expectancy=0.0,
        symbol_summary_for_bucket={"profitable_without_best_symbol": True},
    )
    assert result["verdict"] == "PROMISING"


def test_classify_regime_signal_not_promising_when_single_symbol_dependent():
    good_rows = [_row(bucket_value="A", pnl=Decimal("2")) for _ in range(MIN_SAMPLE_CLOSED_TRADES)]
    good = regime_summary(good_rows, key_field="gap_bucket")["A"]
    result = classify_regime_signal(
        oos_bucket_stats=good, discovery_bucket_stats=good, baseline_expectancy=0.0,
        symbol_summary_for_bucket={"profitable_without_best_symbol": False},
    )
    assert result["verdict"] != "PROMISING"


def test_classify_regime_signal_no_evidence_when_neither_period_improves():
    bad_rows = [_row(bucket_value="A", pnl=Decimal("-2")) for _ in range(MIN_SAMPLE_CLOSED_TRADES)]
    bad = regime_summary(bad_rows, key_field="gap_bucket")["A"]
    result = classify_regime_signal(oos_bucket_stats=bad, discovery_bucket_stats=bad, baseline_expectancy=0.0)
    assert result["verdict"] == "NO_EVIDENCE"


def test_classify_regime_signal_mixed_when_only_one_period_improves():
    good_rows = [_row(bucket_value="A", pnl=Decimal("2")) for _ in range(MIN_SAMPLE_CLOSED_TRADES)]
    bad_rows = [_row(bucket_value="A", pnl=Decimal("-2")) for _ in range(MIN_SAMPLE_CLOSED_TRADES)]
    good = regime_summary(good_rows, key_field="gap_bucket")["A"]
    bad = regime_summary(bad_rows, key_field="gap_bucket")["A"]
    result = classify_regime_signal(oos_bucket_stats=good, discovery_bucket_stats=bad, baseline_expectancy=0.0)
    assert result["verdict"] == "MIXED"


def test_classify_regime_signal_missing_bucket_is_insufficient():
    good_rows = [_row(bucket_value="A", pnl=Decimal("2")) for _ in range(MIN_SAMPLE_CLOSED_TRADES)]
    good = regime_summary(good_rows, key_field="gap_bucket")["A"]
    assert classify_regime_signal(oos_bucket_stats=good, discovery_bucket_stats=None, baseline_expectancy=0.0)["verdict"] == "INSUFFICIENT_SAMPLE"


# ---------------------------------------------------------------------------
# Symbol summary / concentration.
# ---------------------------------------------------------------------------


def test_symbol_summary_flags_single_symbol_dependence():
    rows = (
        [_row(bucket_value="A", pnl=Decimal("10"), symbol="AAPL") for _ in range(3)]
        + [_row(bucket_value="A", pnl=Decimal("-1"), symbol="MSFT") for _ in range(3)]
    )
    summary = symbol_summary(rows)
    assert summary["profitable_without_best_symbol"] is False  # removing AAPL flips it unprofitable


def test_symbol_summary_profitable_without_best_symbol_when_broadly_positive():
    rows = (
        [_row(bucket_value="A", pnl=Decimal("10"), symbol="AAPL") for _ in range(3)]
        + [_row(bucket_value="A", pnl=Decimal("5"), symbol="MSFT") for _ in range(3)]
    )
    summary = symbol_summary(rows)
    assert summary["profitable_without_best_symbol"] is True


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_regime_summary_is_deterministic_across_calls():
    rows = [_row(bucket_value="A", pnl=Decimal("1")) for _ in range(20)] + [_row(bucket_value="B", pnl=Decimal("-1")) for _ in range(20)]
    first = regime_summary(rows, key_field="gap_bucket")
    second = regime_summary(rows, key_field="gap_bucket")
    assert first == second
    assert list(first.keys()) == sorted(first.keys())


# ---------------------------------------------------------------------------
# Production config / simulator untouched (sanity guard for this study).
# ---------------------------------------------------------------------------


def test_production_config_unchanged():
    from src.config import load_config
    config = load_config()
    assert config["strategy"]["enabled"] is False
    assert float(config["signal"]["minimum_gap_percent"]) == 1.0
    assert float(config["signal"]["minimum_relative_volume"]) == 3.0


def test_shadow_execution_simulator_module_not_modified_by_this_study():
    # This study only ever calls simulate_shadow_execution; it must not
    # import or reference anything from this new research module.
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "src" / "shadow_execution_simulator.py").read_text(encoding="utf-8")
    assert "orb_regime_diagnostic_research" not in source


# ---------------------------------------------------------------------------
# End-to-end integration: build_diagnostic_rows against a real
# ComboEvaluation and raw MarketBar dicts (the exact call shape the CLI
# script uses) -- a regression guard for type mismatches between MarketBar
# and MinuteBar at this module's own boundary.
# ---------------------------------------------------------------------------


def test_build_diagnostic_rows_end_to_end_with_market_bars():
    from datetime import timedelta

    from src.config import load_config
    from src.orb_gap_rvol_sensitivity_research import evaluate_combo, precompute_inputs_table
    from src.orb_regime_diagnostic_research import PRIMARY_CONFIG_A, build_diagnostic_rows
    from tests.test_orb_gap_rvol_sensitivity_research import (
        HISTORICAL_DATES,
        SESSION_DATE,
        synthetic_spy_bars,
        synthetic_symbol_bars,
    )

    config = load_config()
    lookback_days = int(config["signal"]["relative_volume_lookback_days"])
    session_dates = [SESSION_DATE]
    universe = ["AAPL"]

    candidate_bars = synthetic_symbol_bars(
        "AAPL", SESSION_DATE, gap_percent=Decimal("0.60"), rvol_multiple=Decimal("1.30"),
        lookback_days=lookback_days,
    )
    spy_bars = synthetic_spy_bars(HISTORICAL_DATES + [SESSION_DATE])
    market_bars_by_symbol = {"AAPL": candidate_bars, "SPY": spy_bars}
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}

    inputs_table = precompute_inputs_table(
        universe=universe, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=config,
    )
    evaluation = evaluate_combo(
        combo=PRIMARY_CONFIG_A, universe=universe, session_dates=session_dates,
        inputs_table=inputs_table, base_config=config,
    )
    assert len(evaluation.candidate_signals) == 1  # fixture is built to qualify at 0.50%/1.25x

    from datetime import timezone as _tz
    rows = build_diagnostic_rows(
        period="TEST", combo=PRIMARY_CONFIG_A, config_label="A_TEST", evaluation=evaluation,
        market_bars_by_symbol=market_bars_by_symbol, base_config=config,
        implementation_digest="d" * 64, created_at=datetime.now(_tz.utc),
    )
    assert len(rows) == 1
    row = rows[0].flat()
    assert row["symbol"] == "AAPL"
    assert row["gap_bucket"] == "0.50-0.74"
    assert set(DECISION_TIME_FIELDS) <= set(row)
    assert set(OUTCOME_FIELDS) <= set(row)


# ---------------------------------------------------------------------------
# Regression: the historical calendar provider must reach
# simulate_shadow_execution through build_diagnostic_rows, or every 2025
# outcome silently reverts to DATA_INVALID -- the exact defect Issue #313
# corrected in the #304/#307 study, found here one call site over.
#
# Self-contained fixtures (not imported from tests/test_orb_out_of_sample_
# correction.py, which lives on a separate, still-open PR) -- same proven
# two-bar breakout/stop shape used there and in
# tests/test_orb_gap_rvol_sensitivity_research.py's own synthetic fixtures.
# ---------------------------------------------------------------------------


def _regime_2025_fixture():
    from src.config import load_config
    from src.exchange_calendar import EASTERN
    from src.market_data_canonical import MarketBar, MarketDataQuality
    from src.market_data_provider import ALPACA_IEX_SOURCE
    from src.orb_gap_rvol_sensitivity_research import ParameterCombo, evaluate_combo, precompute_inputs_table
    from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before

    config = load_config()
    lookback_days = int(config["signal"]["relative_volume_lookback_days"])
    historical_session_date = date(2025, 6, 2)
    historical_lookback_dates = research_trading_sessions_before(historical_session_date, count=lookback_days)
    combo = ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.25"))

    def mbar(symbol, session_date, hh, mm, *, open_, high, low, close, volume):
        from datetime import timedelta
        ts = datetime(session_date.year, session_date.month, session_date.day, hh, mm, tzinfo=EASTERN).astimezone(timezone.utc)
        return MarketBar(
            symbol=symbol, exchange_session="REGULAR", trading_date=session_date, event_timestamp=ts,
            end_timestamp=ts + timedelta(minutes=1), receive_timestamp=ts + timedelta(seconds=1),
            open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=volume,
            source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None, quality=MarketDataQuality.VALID,
        )

    candidate_bars = []
    for prior_date in historical_lookback_dates:
        for minute in range(5):
            candidate_bars.append(mbar("AAPL", prior_date, 9, 30 + minute, open_="100", high="100.5", low="99.5", close="100", volume=100_000))
    today_open = Decimal("100.60")
    opening_bar_volume = int((Decimal("1.30") * Decimal("500000")) / 5)
    opening_high = today_open + Decimal("0.5")
    opening_low = today_open - Decimal("0.3")
    for minute in range(5):
        candidate_bars.append(mbar("AAPL", historical_session_date, 9, 30 + minute, open_=str(today_open), high=str(opening_high), low=str(opening_low), close=str(today_open), volume=opening_bar_volume))
    breakout_close = opening_high + Decimal("1")
    candidate_bars.append(mbar("AAPL", historical_session_date, 9, 40, open_=str(today_open), high=str(breakout_close + Decimal("0.5")), low=str(opening_low), close=str(breakout_close), volume=50_000))
    candidate_bars.append(mbar("AAPL", historical_session_date, 9, 41, open_=str(breakout_close), high=str(breakout_close + Decimal("0.2")), low=str(breakout_close - Decimal("0.1")), close=str(breakout_close), volume=10_000))
    candidate_bars.append(mbar("AAPL", historical_session_date, 9, 42, open_=str(breakout_close), high=str(breakout_close), low=str(opening_low - Decimal("1")), close=str(opening_low - Decimal("1")), volume=10_000))

    spy_bars = []
    for session_date in list(historical_lookback_dates) + [historical_session_date]:
        for minute in range(20):
            spy_bars.append(mbar("SPY", session_date, 9, 30 + minute, open_="500", high="501", low="499.5", close="500.5", volume=100_000))

    market_bars_by_symbol = {"AAPL": candidate_bars, "SPY": spy_bars}
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    resolver = lambda day: __import__("src.market_data_canonical", fromlist=["session_open_for"]).session_open_for(
        day, session_bounds_provider=research_regular_session_bounds,
    )
    inputs_table = precompute_inputs_table(
        universe=["AAPL"], session_dates=[historical_session_date], minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=config, session_open_resolver=resolver,
    )
    evaluation = evaluate_combo(
        combo=combo, universe=["AAPL"], session_dates=[historical_session_date],
        inputs_table=inputs_table, base_config=config,
    )
    assert len(evaluation.candidate_signals) == 1
    return combo, evaluation, market_bars_by_symbol, config


def test_build_diagnostic_rows_2025_candidate_data_invalid_without_provider():
    from src.orb_regime_diagnostic_research import build_diagnostic_rows

    combo, evaluation, market_bars_by_symbol, config = _regime_2025_fixture()
    rows = build_diagnostic_rows(
        period="OUT_OF_SAMPLE", combo=combo, config_label="A_TEST", evaluation=evaluation,
        market_bars_by_symbol=market_bars_by_symbol, base_config=config,
        implementation_digest="d" * 64, created_at=datetime.now(timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].outcome["closed"] is False
    assert rows[0].outcome["state"] == "DATA_INVALID"


def test_build_diagnostic_rows_2025_candidate_real_outcome_with_research_provider():
    from src.orb_regime_diagnostic_research import build_diagnostic_rows
    from src.orb_research_historical_calendar import research_regular_session_bounds

    combo, evaluation, market_bars_by_symbol, config = _regime_2025_fixture()
    rows = build_diagnostic_rows(
        period="OUT_OF_SAMPLE", combo=combo, config_label="A_TEST", evaluation=evaluation,
        market_bars_by_symbol=market_bars_by_symbol, base_config=config,
        implementation_digest="d" * 64, created_at=datetime.now(timezone.utc),
        session_bounds_provider=research_regular_session_bounds,
    )
    assert len(rows) == 1
    assert rows[0].outcome["closed"] is True
    assert rows[0].outcome["state"] == "STOPPED"
    # Decision-time features must still be present and correctly bucketed
    # regardless of the outcome-side fix -- proves the two concerns stay
    # independent.
    assert rows[0].decision["gap_bucket"] == "0.50-0.74"


def test_script_passes_research_provider_into_build_diagnostic_rows():
    from pathlib import Path

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "research_orb_regime_diagnostic.py"
    source = script_path.read_text(encoding="utf-8")
    assert "session_bounds_provider=session_bounds_provider" in source
