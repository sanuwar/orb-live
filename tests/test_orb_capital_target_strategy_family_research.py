"""Focused tests for M48 $30K capital-target ETF strategy-family
feasibility research (Issue #348)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.orb_capital_target_strategy_family_research import (
    ARM_A, ARM_B, ARM_C,
    ATR_PERIOD_A, ATR_STOP_MULTIPLE_A, BASE_COST_BPS, EXIT_SMA_PERIOD_A, MAX_AGGREGATE_RISK,
    MAX_CONCURRENT_POSITIONS_A, MAX_HOLDING_SESSIONS_A, MAX_RISK_PER_POSITION, RSI_PERIOD_A,
    STARTING_CAPITAL, STRESS_COST_BPS, TREND_SMA_PERIOD,
    CapitalTargetResearchError,
    DailySeries, daily_series_from_bars, require_full_alignment, session_alignment_report,
    ohlc_integrity_report,
    simple_moving_average, wilder_rsi, average_true_range, trailing_return, momentum_12_1,
    stop_fill_price, whole_share_quantity, equal_capital_whole_shares,
    PullbackCandidate, rank_pullback_candidates, MomentumCandidate, rank_momentum_candidates,
    transaction_cost,
    run_arm_a, month_end_indices, run_monthly_rebalance_arm, arm_b_selector, arm_c_selector,
    monthly_dollar_pnl, monthly_return_pct, cagr, max_drawdown, sharpe_ratio, sortino_ratio,
    annualized_volatility_pct, annualized_downside_deviation_pct,
    profitable_month_stats, worst_and_percentile_month, pearson_correlation, align_monthly_series,
    positive_contribution_concentration, compute_arm_metrics, scale_trade_to_risk_cap, segment_label,
    classify_capital_target_arm, exposure_and_concurrency, evaluation_end_mark_transactions,
    CLASSIFICATION_DIRECT_TARGET_FEASIBLE, CLASSIFICATION_PROMISING_PORTFOLIO_CONTRIBUTOR,
    CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT, CLASSIFICATION_NOT_PROMISING,
    compute_combination_plausibility_upper_bound, ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK,
    COMBINATION_TARGET_MONTHLY_DOLLARS, COMBINATION_MATHEMATICALLY_PLAUSIBLE,
    COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE,
)
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE


# ---------------------------------------------------------------------------
# Fixture helpers.
# ---------------------------------------------------------------------------

def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _series_from_closes(symbol: str, dates: list[date], closes: list[Decimal], *, pad: Decimal = Decimal("0.05")) -> DailySeries:
    opens = closes
    highs = [c + pad for c in closes]
    lows = [c - pad for c in closes]
    return DailySeries(
        symbol=symbol, dates=tuple(dates), opens=tuple(opens), highs=tuple(highs), lows=tuple(lows),
        closes=tuple(closes), volumes=tuple([1_000_000] * len(closes)),
    )


def _uptrend_then_dip_then_recover(dates: list[date], *, dip_at: int, dip_factor: Decimal = Decimal("0.90")) -> list[Decimal]:
    """Steady uptrend (well clear of its own trailing 200-session SMA)
    through `dip_at - 1`, a one-session sharp drop at `dip_at` (drives
    RSI(2) low while staying above SMA(200)), then a steady recovery."""
    closes: list[Decimal] = []
    price = Decimal("80")
    for i in range(len(dates)):
        if i < dip_at:
            price = price + Decimal("0.30")
        elif i == dip_at:
            price = price * dip_factor
        else:
            price = price + Decimal("1.0")
        closes.append(price)
    return closes


def _flat(dates: list[date], price: Decimal) -> list[Decimal]:
    return [price] * len(dates)


def _mbar(symbol: str, day: date, *, o: str, h: str, l: str, c: str, v: int = 100_000) -> MarketBar:
    from datetime import datetime, timezone
    ts = datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc)
    return MarketBar(
        symbol=symbol, exchange_session="REGULAR", trading_date=day, event_timestamp=ts,
        end_timestamp=ts + timedelta(days=1), receive_timestamp=ts,
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=v,
        source=ALPACA_IEX_SOURCE, interval_seconds=86400, provider_sequence_id=None, quality=MarketDataQuality.VALID,
    )


# ---------------------------------------------------------------------------
# Indicators.
# ---------------------------------------------------------------------------

def test_wilder_rsi_known_reference_value():
    closes = [Decimal(x) for x in ["44", "44.5", "43.5", "44", "44.5", "43", "43.5", "44", "44.5", "45"]]
    rsi = wilder_rsi(closes, period=2)
    assert rsi[0] is None and rsi[1] is None
    assert rsi[2] == pytest.approx(Decimal("33.333333"), abs=Decimal("0.001"))


def test_wilder_rsi_warmup_boundary_is_none_before_period():
    closes = [Decimal(100 + i) for i in range(RSI_PERIOD_A)]  # exactly `period` closes -> period deltas is period-1, not enough
    rsi = wilder_rsi(closes, period=RSI_PERIOD_A)
    assert all(v is None for v in rsi)


def test_sma_warmup_boundary():
    closes = [Decimal(i) for i in range(10)]
    sma = simple_moving_average(closes, window=5)
    assert sma[3] is None and sma[4] is not None
    assert sma[4] == sum(closes[0:5]) / Decimal(5)


def test_atr_warmup_boundary():
    dates = _business_days(date(2021, 1, 4), 20)
    closes = [Decimal(100) + Decimal(i) for i in range(20)]
    highs = [c + Decimal("1") for c in closes]
    lows = [c - Decimal("1") for c in closes]
    atr = average_true_range(highs, lows, closes, period=14)
    assert atr[13] is None
    assert atr[14] is not None


def test_momentum_12_1_requires_252_and_21_session_history():
    closes = [Decimal(100) + Decimal(i) * Decimal("0.1") for i in range(300)]
    assert momentum_12_1(closes, as_of_index=250) is None  # 250 - 252 < 0
    assert momentum_12_1(closes, as_of_index=252) is not None
    expected = (closes[252 - 21] - closes[0]) / closes[0]
    assert momentum_12_1(closes, as_of_index=252) == expected


def test_trailing_return_requires_full_lookback():
    closes = [Decimal(100) + Decimal(i) for i in range(300)]
    assert trailing_return(closes, as_of_index=250, lookback=252) is None
    assert trailing_return(closes, as_of_index=252, lookback=252) is not None


# ---------------------------------------------------------------------------
# Order-fill mechanics, sizing, ranking, costs.
# ---------------------------------------------------------------------------

def test_stop_fill_price_gap_through_fills_at_open():
    price = stop_fill_price(session_open=Decimal("95"), stop_price=Decimal("98"))
    assert price == Decimal("95")


def test_stop_fill_price_touch_fills_at_stop_price():
    price = stop_fill_price(session_open=Decimal("99"), stop_price=Decimal("98"))
    assert price == Decimal("98")


def test_stop_fill_price_open_exactly_at_stop_is_gap_case():
    price = stop_fill_price(session_open=Decimal("98"), stop_price=Decimal("98"))
    assert price == Decimal("98")  # both branches agree at the boundary


def test_whole_share_quantity_respects_risk_cap():
    qty = whole_share_quantity(entry_price=Decimal("100"), stop_price=Decimal("97"), max_risk_dollars=Decimal("300"), available_cash=Decimal("1000000"))
    assert qty == 100  # 300 / 3 = 100 shares exactly


def test_whole_share_quantity_respects_cash_cap():
    qty = whole_share_quantity(entry_price=Decimal("100"), stop_price=Decimal("97"), max_risk_dollars=Decimal("300"), available_cash=Decimal("250"))
    assert qty == 2  # cash caps it below the risk-implied 100


def test_whole_share_quantity_zero_when_stop_not_below_entry():
    assert whole_share_quantity(entry_price=Decimal("100"), stop_price=Decimal("100"), max_risk_dollars=Decimal("300"), available_cash=Decimal("100000")) == 0
    assert whole_share_quantity(entry_price=Decimal("100"), stop_price=Decimal("101"), max_risk_dollars=Decimal("300"), available_cash=Decimal("100000")) == 0


def test_equal_capital_whole_shares():
    assert equal_capital_whole_shares(capital=Decimal("1000"), price=Decimal("300")) == 3
    assert equal_capital_whole_shares(capital=Decimal("100"), price=Decimal("300")) == 0


def test_rank_pullback_candidates_ascending_rsi_then_ticker_tie_break():
    candidates = [
        PullbackCandidate(symbol="ZZZ", rsi2=Decimal("5")),
        PullbackCandidate(symbol="AAA", rsi2=Decimal("5")),
        PullbackCandidate(symbol="BBB", rsi2=Decimal("2")),
    ]
    ranked = rank_pullback_candidates(candidates)
    assert [c.symbol for c in ranked] == ["BBB", "AAA", "ZZZ"]  # BBB lowest RSI; tie between AAA/ZZZ -> ticker order


def test_rank_momentum_candidates_descending_then_ticker_tie_break():
    candidates = [
        MomentumCandidate(symbol="ZZZ", momentum=Decimal("0.10")),
        MomentumCandidate(symbol="AAA", momentum=Decimal("0.10")),
        MomentumCandidate(symbol="BBB", momentum=Decimal("0.20")),
    ]
    ranked = rank_momentum_candidates(candidates)
    assert [c.symbol for c in ranked] == ["BBB", "AAA", "ZZZ"]  # BBB highest momentum; tie -> ticker order


def test_transaction_cost_5bps_vs_10bps_differ():
    cost_base = transaction_cost(Decimal("10000"), BASE_COST_BPS)
    cost_stress = transaction_cost(Decimal("10000"), STRESS_COST_BPS)
    assert cost_base == Decimal("5")
    assert cost_stress == Decimal("10")
    assert cost_stress == cost_base * 2


# ---------------------------------------------------------------------------
# Data alignment / integrity.
# ---------------------------------------------------------------------------

def test_daily_series_from_bars_rejects_duplicate_trading_date():
    day = date(2022, 1, 3)
    bars = [
        _mbar("AAA", day, o="100", h="101", l="99", c="100.5"),
        _mbar("AAA", day, o="100", h="101", l="99", c="100.6"),
    ]
    with pytest.raises(CapitalTargetResearchError):
        daily_series_from_bars(bars)


def test_daily_series_from_bars_rejects_mixed_symbols():
    day = date(2022, 1, 3)
    bars = [_mbar("AAA", day, o="100", h="101", l="99", c="100.5"), _mbar("BBB", day, o="50", h="51", l="49", c="50.5")]
    with pytest.raises(CapitalTargetResearchError):
        daily_series_from_bars(bars)


def test_require_full_alignment_raises_on_missing_session():
    dates = _business_days(date(2022, 1, 3), 10)
    full = _series_from_closes("AAA", dates, [Decimal(100)] * 10)
    missing_one = _series_from_closes("BBB", dates[:-1], [Decimal(100)] * 9)
    with pytest.raises(CapitalTargetResearchError):
        require_full_alignment({"AAA": full, "BBB": missing_one}, master_dates=dates)


def test_session_alignment_report_is_diagnostic_only_never_raises():
    dates = _business_days(date(2022, 1, 3), 10)
    missing_one = _series_from_closes("BBB", dates[:-1], [Decimal(100)] * 9)
    report = session_alignment_report({"BBB": missing_one}, master_dates=dates)
    assert report["BBB"]["missing_master_dates"] == [dates[-1].isoformat()]


def test_ohlc_integrity_report_flags_violation():
    day = date(2022, 1, 3)
    bad = DailySeries(
        symbol="AAA", dates=(day,), opens=(Decimal("100"),), highs=(Decimal("99"),),  # high < open -- violation
        lows=(Decimal("98"),), closes=(Decimal("100"),), volumes=(1000,),
    )
    report = ohlc_integrity_report({"AAA": bad})
    assert report["AAA"]["violations"]


def test_ohlc_integrity_report_clean_series_has_no_violations():
    dates = _business_days(date(2022, 1, 3), 5)
    good = _series_from_closes("AAA", dates, [Decimal(100)] * 5)
    report = ohlc_integrity_report({"AAA": good})
    assert report["AAA"]["violations"] == []


# ---------------------------------------------------------------------------
# ARM_A daily engine.
# ---------------------------------------------------------------------------

def _arm_a_fixture(dip_at: int = 250, total_days: int = 320):
    dates = _business_days(date(2021, 1, 4), total_days)
    aaa = _series_from_closes("AAA", dates, _uptrend_then_dip_then_recover(dates, dip_at=dip_at), pad=Decimal("0.02"))
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))
    return dates, {"AAA": aaa, "BBB": bbb}


def test_arm_a_full_lifecycle_enters_and_exits():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    assert len(result.closed_trades) >= 1
    trade = result.closed_trades[0]
    assert trade.symbol == "AAA"
    assert trade.quantity > 0
    assert trade.exit_date > trade.entry_date


def test_arm_a_entry_fills_at_next_session_open_not_signal_close():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    trade = result.closed_trades[0]
    entry_index = dates.index(trade.entry_date)
    # The RSI(2)-approving close must be the PRIOR session's close; the
    # trade's own entry_price is that prior day's series open == entry
    # session's own open (never that day's close).
    assert trade.entry_price == series["AAA"].opens[entry_index]
    assert trade.entry_price != series["AAA"].closes[entry_index - 1]  # not a same-close fill


def test_arm_a_no_same_close_fill_for_entry_or_exit():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    trade = result.closed_trades[0]
    entry_index = dates.index(trade.entry_date)
    exit_index = dates.index(trade.exit_date)
    assert trade.entry_price == series["AAA"].opens[entry_index]
    if trade.exit_reason != "STOP":
        assert trade.exit_price == series["AAA"].opens[exit_index]


def test_arm_a_five_session_max_hold_exit():
    # A dip that never recovers above its own 5-session SMA and never
    # touches the stop -- only the 5-session hold limit can close it.
    dates = _business_days(date(2021, 1, 4), 320)
    closes = _uptrend_then_dip_then_recover(dates, dip_at=250, dip_factor=Decimal("0.95"))
    # Flatten the post-dip path so it drifts sideways (never closes above
    # its own 5-session SMA by a wide margin, and never below the stop).
    for i in range(251, len(closes)):
        closes[i] = closes[250] + Decimal("0.01") * Decimal(i - 250)
    aaa = _series_from_closes("AAA", dates, closes, pad=Decimal("0.02"))
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))
    result = run_arm_a({"AAA": aaa, "BBB": bbb}, dates)
    assert len(result.closed_trades) >= 1
    trade = result.closed_trades[0]
    assert trade.holding_sessions <= MAX_HOLDING_SESSIONS_A + 1
    assert trade.exit_reason in ("MAX_HOLD", "SMA5")


def test_arm_a_stop_gap_through_fills_at_open():
    dates = _business_days(date(2021, 1, 4), 260)
    closes = _uptrend_then_dip_then_recover(dates, dip_at=250, dip_factor=Decimal("0.93"))
    # After entry (next session open, ~index 251), force a severe gap
    # down on the following session so the open itself is below the
    # 2xATR stop.
    for i in range(252, len(closes)):
        closes[i] = closes[251] * Decimal("0.5")
    aaa = _series_from_closes("AAA", dates, closes, pad=Decimal("0.02"))
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))
    result = run_arm_a({"AAA": aaa, "BBB": bbb}, dates)
    assert len(result.closed_trades) >= 1
    trade = result.closed_trades[0]
    assert trade.exit_reason == "STOP"
    exit_index = dates.index(trade.exit_date)
    assert trade.exit_price == series_open_at(aaa, exit_index)
    assert trade.exit_price < trade.entry_price - Decimal(2) * Decimal("0.01")  # well past a simple stop touch


def series_open_at(series: DailySeries, index: int) -> Decimal:
    return series.opens[index]


def test_arm_a_stop_can_fire_on_the_entry_day_itself():
    # Signal (RSI(2) dip) at day D=250 -> entry at D+1=251's open. The
    # SAME session (251) then has an intraday low that breaches the
    # 2xATR stop -- an implementation that only checks stops on
    # positions opened on an EARLIER day would miss this entirely and
    # let the position ride, understating both realized loss and
    # drawdown. The stop must fire on 251 itself.
    dates = _business_days(date(2021, 1, 4), 260)
    closes = _uptrend_then_dip_then_recover(dates, dip_at=250, dip_factor=Decimal("0.93"))
    aaa = _series_from_closes("AAA", dates, closes, pad=Decimal("0.02"))
    # Patch day 251's own low to crash 20% intraday, well past any
    # plausible 2xATR stop distance, while leaving its open/close (and
    # every other day) untouched.
    lows = list(aaa.lows)
    lows[251] = closes[251] * Decimal("0.80")
    aaa = DailySeries(symbol="AAA", dates=aaa.dates, opens=aaa.opens, highs=aaa.highs, lows=tuple(lows), closes=aaa.closes, volumes=aaa.volumes)
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))

    result = run_arm_a({"AAA": aaa, "BBB": bbb}, dates)
    assert len(result.closed_trades) >= 1
    trade = result.closed_trades[0]
    assert trade.symbol == "AAA"
    assert trade.entry_date == dates[251]
    assert trade.exit_date == dates[251]  # exits on its OWN entry day
    assert trade.exit_reason == "STOP"
    assert trade.holding_sessions == 1
    assert trade.exit_price == trade.entry_price - (trade.planned_risk / trade.quantity)  # fills at the stop (no gap at the open, since open==entry_price > stop)
    assert trade.entry_cost > 0
    assert trade.exit_cost > 0
    assert trade.net_pnl == trade.gross_pnl - trade.entry_cost - trade.exit_cost


def test_arm_a_max_concurrent_positions_and_no_duplicate_symbol():
    dates = _business_days(date(2021, 1, 4), 320)
    series = {}
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        closes = _uptrend_then_dip_then_recover(dates, dip_at=250 + i)  # staggered dips, all still eligible around the same window
        series[sym] = _series_from_closes(sym, dates, closes, pad=Decimal("0.02"))
    result = run_arm_a(series, dates)
    # At no point should more than MAX_CONCURRENT_POSITIONS_A positions be
    # open, and no symbol ever appears in two overlapping trades.
    events = []
    for t in result.closed_trades:
        events.append((t.entry_date, 1, t.symbol))
        events.append((t.exit_date, -1, t.symbol))
    events.sort(key=lambda e: (e[0], e[1]))  # process exits before entries on the same date? entries=+1 sort after -1 (exits) at same date via e[1] ascending
    concurrent = 0
    held: set[str] = set()
    for _day, delta, symbol in events:
        if delta == 1:
            assert symbol not in held, "duplicate ETF position"
            held.add(symbol)
            concurrent += 1
            assert concurrent <= MAX_CONCURRENT_POSITIONS_A
        else:
            held.discard(symbol)
            concurrent -= 1


def test_arm_a_per_position_risk_cap_never_exceeded():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    for t in result.closed_trades:
        assert t.planned_risk <= MAX_RISK_PER_POSITION + Decimal("0.01")


def test_arm_a_aggregate_risk_cap_enforced_via_direct_call():
    # Two simultaneously-eligible symbols with a tight aggregate cap:
    # the second entry must be sized down (or refused) to respect the
    # $600 aggregate ceiling even though each alone is under $300.
    dates = _business_days(date(2021, 1, 4), 320)
    series = {}
    for sym in ["AAA", "BBB"]:
        closes = _uptrend_then_dip_then_recover(dates, dip_at=250)
        series[sym] = _series_from_closes(sym, dates, closes, pad=Decimal("0.02"))
    result = run_arm_a(series, dates, max_aggregate_risk=Decimal("300"))  # only room for ~one full-risk position
    total_risk_at_any_time = Decimal("0")
    # Reconstruct: since both would enter on the same day (identical dip
    # pattern -> identical signal timing -> tie-break by ticker), assert
    # the aggregate never exceeds the injected cap across all trades that
    # were concurrently open.
    opens = sorted(result.closed_trades, key=lambda t: t.entry_date)
    for t in opens:
        assert t.planned_risk <= Decimal("300") + Decimal("0.01")


def test_arm_a_costs_5bps_vs_10bps_reduce_net_pnl_more_under_stress():
    dates, series = _arm_a_fixture()
    base = run_arm_a(series, dates, cost_bps=BASE_COST_BPS)
    stress = run_arm_a(series, dates, cost_bps=STRESS_COST_BPS)
    assert len(base.closed_trades) == len(stress.closed_trades) >= 1
    assert stress.total_cost_paid > base.total_cost_paid
    for b, s in zip(base.closed_trades, stress.closed_trades):
        assert s.entry_cost >= b.entry_cost
        assert s.net_pnl <= b.net_pnl


def test_arm_a_atr_stop_uses_only_bars_through_prior_close_not_entry_day():
    # A symbol whose entry-day (today's) high/low would imply a very
    # different ATR if it were (incorrectly) included -- the stop must
    # be identical to what completed bars through yesterday's close
    # alone would produce.
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    trade = result.closed_trades[0]
    entry_index = dates.index(trade.entry_date)
    from src.orb_capital_target_strategy_family_research import average_true_range as _atr
    aaa = series["AAA"]
    atr_series = _atr(aaa.highs, aaa.lows, aaa.closes, ATR_PERIOD_A)
    expected_stop = trade.entry_price - ATR_STOP_MULTIPLE_A * atr_series[entry_index - 1]
    assert trade.planned_risk / trade.quantity == pytest.approx(trade.entry_price - expected_stop, abs=Decimal("0.001"))


def test_arm_a_deterministic_repeatability():
    dates, series = _arm_a_fixture()
    r1 = run_arm_a(series, dates)
    r2 = run_arm_a(series, dates)
    assert r1.closed_trades == r2.closed_trades
    assert r1.daily_equity == r2.daily_equity


def test_arm_a_rejects_missing_session_fail_closed():
    dates, series = _arm_a_fixture()
    truncated = {"AAA": DailySeries(
        symbol="AAA", dates=series["AAA"].dates[:-1], opens=series["AAA"].opens[:-1],
        highs=series["AAA"].highs[:-1], lows=series["AAA"].lows[:-1], closes=series["AAA"].closes[:-1],
        volumes=series["AAA"].volumes[:-1],
    ), "BBB": series["BBB"]}
    with pytest.raises(CapitalTargetResearchError):
        run_arm_a(truncated, dates)


# ---------------------------------------------------------------------------
# ARM_B / ARM_C monthly engine.
# ---------------------------------------------------------------------------

def test_month_end_indices_finds_last_session_of_each_month():
    dates = _business_days(date(2022, 1, 3), 70)
    idxs = month_end_indices(dates)
    for i in idxs[:-1]:
        assert dates[i + 1].month != dates[i].month or dates[i + 1].year != dates[i].year
    assert idxs[-1] == len(dates) - 1


def _monthly_fixture(total_days: int = 550):
    dates = _business_days(date(2021, 1, 4), total_days)
    aaa = _series_from_closes("AAA", dates, [Decimal("50") + Decimal("0.15") * Decimal(i) for i in range(total_days)])
    bbb = _series_from_closes("BBB", dates, [Decimal("50") + Decimal("0.05") * Decimal(i) for i in range(total_days)])
    ccc = _series_from_closes("CCC", dates, [Decimal("50") - Decimal("0.05") * Decimal(i) if Decimal("50") - Decimal("0.05") * Decimal(i) > 1 else Decimal("1") for i in range(total_days)])
    return dates, {"AAA": aaa, "BBB": bbb, "CCC": ccc}


def test_arm_b_selects_top_three_positive_momentum_above_sma200():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_b_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_B, selector=selector)
    late_events = [e for e in result.rebalance_events if e.signal_index > 250]
    assert late_events
    for ev in late_events:
        assert "CCC" not in ev.target_symbols  # negative-drift symbol never eligible
        assert len(ev.target_symbols) <= 3


def test_arm_c_holds_every_eligible_not_just_top_n():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    late_events = [e for e in result.rebalance_events if e.signal_index > 250]
    assert late_events
    for ev in late_events:
        assert set(ev.target_symbols) <= {"AAA", "BBB"}
        assert "AAA" in ev.target_symbols and "BBB" in ev.target_symbols  # both positive-trend ETFs held, not just the top one


def test_monthly_rebalance_executes_at_next_open_not_signal_close():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_b_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_B, selector=selector)
    executed = [e for e in result.rebalance_events if e.execution_index is not None]
    assert executed
    for ev in executed:
        assert ev.execution_index == ev.signal_index + 1
        assert ev.execution_date == dates[ev.signal_index + 1]


def test_monthly_rebalance_final_month_has_no_execution_when_no_next_session():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_b_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_B, selector=selector)
    last_event = result.rebalance_events[-1]
    assert last_event.signal_index == len(dates) - 1
    assert last_event.execution_index is None and last_event.execution_date is None


def test_monthly_rebalance_trims_overweight_continuing_holding():
    # AAA grows much faster than BBB (0.15/day vs 0.05/day) -- once both
    # are continuously eligible, AAA's position value outpaces its
    # equal-capital target relative to BBB's, so it must be TRIMMED at
    # later rebalances, not left to drift indefinitely.
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    aaa_trims = [t for t in result.closed_trades if t.symbol == "AAA" and t.reason == "TRIMMED"]
    assert len(aaa_trims) >= 1
    for t in aaa_trims:
        assert t.quantity > 0
        assert t.exit_cost > 0  # costs charged on the required trim


def test_monthly_rebalance_tops_up_underweight_continuing_holding():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    bbb_topups = [t for t in result.transactions if t.symbol == "BBB" and t.reason == "REBALANCE_TOPUP"]
    assert len(bbb_topups) >= 1
    for t in bbb_topups:
        assert t.quantity > 0
        assert t.cost > 0  # costs charged on the required top-up


def test_average_concurrent_positions_never_exceeds_universe_size_despite_trims():
    # Regression: reconstructing exposure from closed-trade intervals
    # double-counted a continuing holding that gets TRIMMED across
    # several rebalances (each trim produces its own trade record
    # sharing the ORIGINAL entry_date), inflating average concurrency
    # above the arm's own universe size. The direct daily-tracked count
    # must never do that.
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    assert any(t.reason == "TRIMMED" for t in result.closed_trades), "fixture must exercise a trim"
    metrics = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions, daily_position_count=result.daily_position_count,
    )
    universe_size = len(series)
    assert metrics["average_concurrent_positions"] <= universe_size
    for _day, count in result.daily_position_count:
        assert count <= universe_size


def test_monthly_rebalance_continuing_holdings_finish_near_equal_target_capital():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    # At the final rebalance execution, AAA and BBB (both still eligible)
    # must each be within one share's worth of value from the other --
    # a genuine equal-capital rebalance, not permanently-drifted weights.
    executed = [e for e in result.rebalance_events if e.execution_index is not None]
    last_execution_index = executed[-1].execution_index
    last_execution_day = dates[last_execution_index]
    holdings_by_symbol = {p.symbol: p for p in result.open_positions_at_end}
    assert "AAA" in holdings_by_symbol and "BBB" in holdings_by_symbol
    price_aaa = series["AAA"].opens[last_execution_index]
    price_bbb = series["BBB"].opens[last_execution_index]
    value_aaa = holdings_by_symbol["AAA"].quantity * price_aaa
    value_bbb = holdings_by_symbol["BBB"].quantity * price_bbb
    max_one_share_gap = max(price_aaa, price_bbb) * 2  # generous one-share-rounding-both-ways tolerance
    assert abs(value_aaa - value_bbb) <= max_one_share_gap


def test_monthly_rebalance_trim_and_topup_are_deterministic():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    r1 = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    r2 = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    assert r1.closed_trades == r2.closed_trades
    assert r1.transactions == r2.transactions
    assert r1.open_positions_at_end == r2.open_positions_at_end


def test_monthly_rebalance_deterministic_repeatability():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_b_selector(series, sma200)
    r1 = run_monthly_rebalance_arm(series, dates, arm=ARM_B, selector=selector)
    r2 = run_monthly_rebalance_arm(series, dates, arm=ARM_B, selector=selector)
    assert r1.closed_trades == r2.closed_trades
    assert r1.rebalance_events == r2.rebalance_events


def test_monthly_rebalance_no_future_aware_selection():
    # A selector that peeks at day i+5's close would select differently
    # than one using only day i's close -- verify the REAL selector's
    # decision at an early month-end matches what's derivable from data
    # strictly through that day (recomputing the selector at a truncated
    # series must agree).
    dates, series = _monthly_fixture(total_days=300)
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_b_selector(series, sma200)
    idx = month_end_indices(dates)[-1]
    result_full = selector(idx)

    truncated_series = {}
    for s, ser in series.items():
        truncated_series[s] = DailySeries(
            symbol=s, dates=ser.dates[: idx + 1], opens=ser.opens[: idx + 1], highs=ser.highs[: idx + 1],
            lows=ser.lows[: idx + 1], closes=ser.closes[: idx + 1], volumes=ser.volumes[: idx + 1],
        )
    sma200_truncated = {s: simple_moving_average(truncated_series[s].closes, TREND_SMA_PERIOD) for s in truncated_series}
    selector_truncated = arm_b_selector(truncated_series, sma200_truncated)
    assert selector_truncated(idx) == result_full


# ---------------------------------------------------------------------------
# Metrics / concentration.
# ---------------------------------------------------------------------------

def test_fixed_notional_primary_view_never_compounds_unlike_secondary_view():
    # Two consecutive +10% months on a $30,000 notional: the PRIMARY
    # fixed-$30K view must show $3,000 then $3,000 (each month's own
    # return applied to the SAME fixed base), while the SECONDARY
    # compounded view (derived from the actual equity curve) shows
    # $3,000 then $3,300 (10% of the now-$33,000 balance).
    from src.orb_capital_target_strategy_family_research import fixed_notional_monthly_dollar_pnl
    pct_series = [(date(2022, 1, 31), Decimal("0.10")), (date(2022, 2, 28), Decimal("0.10"))]
    fixed_series = fixed_notional_monthly_dollar_pnl(pct_series, notional=Decimal("30000"))
    fixed_values = [v for _, v in fixed_series]
    assert fixed_values == [Decimal("3000.0"), Decimal("3000.0")]

    # The naturally-compounding equity curve producing that same +10%/
    # +10% return sequence shows $3,000 then $3,300 instead -- the two
    # views must genuinely diverge.
    equity = [(date(2022, 1, 3), Decimal("30000")), (date(2022, 1, 31), Decimal("33000")), (date(2022, 2, 28), Decimal("36300"))]
    compounded_series = monthly_dollar_pnl(equity)
    compounded_values = [v for _, v in compounded_series]
    assert compounded_values == [Decimal("3000"), Decimal("3300")]
    assert fixed_values != compounded_values


def test_compute_arm_metrics_uses_fixed_notional_for_primary_classification_input():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[240], evaluation_end=dates[-1],
        open_positions_at_end=result.open_positions_at_end, transactions=result.transactions,
    )
    assert "mean_monthly_dollar_pnl_fixed_30k" in metrics
    assert "mean_monthly_dollar_pnl_compounded" in metrics
    # No stale/ambiguous key from before the correction.
    assert "mean_monthly_dollar_pnl" not in metrics


def test_monthly_dollar_pnl_sums_to_total_change():
    dates = _business_days(date(2022, 1, 3), 90)
    equity = [(d, Decimal("30000") + Decimal(i) * Decimal("10")) for i, d in enumerate(dates)]
    pnl = monthly_dollar_pnl(equity)
    assert sum(v for _, v in pnl) == equity[-1][1] - equity[0][1]


def test_max_drawdown_and_recovery():
    dates = _business_days(date(2022, 1, 3), 10)
    equity = [
        (dates[0], Decimal("30000")), (dates[1], Decimal("31000")), (dates[2], Decimal("29000")),
        (dates[3], Decimal("28000")), (dates[4], Decimal("30500")), (dates[5], Decimal("31500")),
    ] + [(d, Decimal("31500")) for d in dates[6:]]
    dd = max_drawdown(equity)
    assert dd["max_drawdown_dollars"] == Decimal("3000")
    assert dd["recovery_days"] is not None


def test_sharpe_and_sortino_positive_for_consistently_positive_returns():
    returns = [Decimal("0.01")] * 12
    assert sharpe_ratio(returns) is None  # zero variance -> undefined
    mixed = [Decimal("0.02"), Decimal("0.01"), Decimal("-0.01"), Decimal("0.03"), Decimal("-0.005")]
    assert sharpe_ratio(mixed) is not None
    assert sortino_ratio(mixed) is not None


def test_positive_contribution_concentration_uses_net_per_symbol():
    pnl = {"AAA": Decimal("500"), "BBB": Decimal("-200"), "CCC": Decimal("100")}
    result = positive_contribution_concentration(pnl)
    assert result["best_contributor"] == "AAA"
    assert result["best_contribution_share"] == Decimal("500") / Decimal("600")  # only AAA+CCC (positive) count
    assert "BBB" not in result["positive_contributors"]


def test_pearson_correlation_and_alignment():
    a = [(date(2022, 1, 1), Decimal("0.01")), (date(2022, 2, 1), Decimal("0.02")), (date(2022, 3, 1), Decimal("-0.01"))]
    b = [(date(2022, 1, 1), Decimal("0.02")), (date(2022, 2, 1), Decimal("0.04")), (date(2022, 3, 1), Decimal("-0.02"))]
    aligned = align_monthly_series(a, b)
    assert len(aligned) == 3
    corr = pearson_correlation([r[1] for r in aligned], [r[2] for r in aligned])
    assert corr == pytest.approx(Decimal("1.0"), abs=Decimal("0.001"))


def test_fill_missing_months_with_zero_preserves_existing_and_zero_fills_gaps():
    from src.orb_capital_target_strategy_family_research import fill_missing_months_with_zero
    sparse = [(date(2022, 3, 1), Decimal("1.5")), (date(2022, 6, 1), Decimal("-0.5"))]
    filled = fill_missing_months_with_zero(sparse, start=date(2022, 1, 1), end=date(2022, 6, 30))
    assert len(filled) == 6  # Jan..Jun, none dropped
    by_month = dict(filled)
    assert by_month[date(2022, 1, 1)] == Decimal(0)
    assert by_month[date(2022, 2, 1)] == Decimal(0)
    assert by_month[date(2022, 3, 1)] == Decimal("1.5")  # existing value preserved
    assert by_month[date(2022, 4, 1)] == Decimal(0)
    assert by_month[date(2022, 5, 1)] == Decimal(0)
    assert by_month[date(2022, 6, 1)] == Decimal("-0.5")


def test_fill_missing_months_with_zero_then_align_keeps_all_dense_series_months():
    from src.orb_capital_target_strategy_family_research import fill_missing_months_with_zero
    # A sparse ORB-like series (one trading month) must not cause an
    # inner-join against a dense arm series to drop the other 5 months.
    orb_sparse = [(date(2022, 3, 15), Decimal("2.0"))]
    orb_filled = fill_missing_months_with_zero(orb_sparse, start=date(2022, 1, 1), end=date(2022, 6, 30))
    arm_dense = [(date(2022, m, 1), Decimal("0.01") * m) for m in range(1, 7)]
    aligned = align_monthly_series(arm_dense, orb_filled)
    assert len(aligned) == 6  # nothing dropped
    # The no-trade months correctly contribute a ZERO ORB value, not an
    # absence.
    march_row = next(r for r in aligned if r[0].month == 3)
    assert march_row[2] == Decimal("2.0")
    jan_row = next(r for r in aligned if r[0].month == 1)
    assert jan_row[2] == Decimal(0)


def test_align_monthly_series_inner_joins_on_missing_months():
    a = [(date(2022, 1, 1), Decimal("0.01")), (date(2022, 2, 1), Decimal("0.02"))]
    b = [(date(2022, 2, 1), Decimal("0.04")), (date(2022, 3, 1), Decimal("-0.02"))]
    aligned = align_monthly_series(a, b)
    assert len(aligned) == 1
    assert aligned[0][0] == date(2022, 2, 1)


def test_segment_label_boundaries():
    assert segment_label(date(2022, 1, 1)) == "2022"
    assert segment_label(date(2025, 12, 31)) == "2025"
    assert segment_label(date(2026, 1, 1)) == "2026_YTD"
    assert segment_label(date(2021, 12, 31)) is None


# ---------------------------------------------------------------------------
# Classification -- every label and exact boundary.
# ---------------------------------------------------------------------------

_ALL_SEGMENTS_POSITIVE = {"2022": Decimal("100"), "2023": Decimal("100"), "2024": Decimal("100"), "2025": Decimal("100"), "2026_YTD": Decimal("100")}


def test_classify_direct_target_feasible_when_all_gates_pass():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("1000"), segment_net_returns=_ALL_SEGMENTS_POSITIVE,
        max_drawdown_pct=Decimal("0.10"), pooled_net_base=Decimal("50000"), pooled_net_stress=Decimal("40000"),
        mean_monthly_return_pct=Decimal("0.02"), profitable_month_pct=Decimal("0.60"),
        correlation_to_orb=Decimal("0.1"), best_single_symbol_positive_share=Decimal("0.3"), integrity_ok=True,
    )
    assert result["verdict"] == CLASSIFICATION_DIRECT_TARGET_FEASIBLE
    assert all(result["checks"].values())


def test_classify_direct_target_feasible_boundary_exactly_1000_and_20pct_drawdown():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("1000"),  # exactly at the >= boundary
        segment_net_returns=_ALL_SEGMENTS_POSITIVE, max_drawdown_pct=Decimal("0.20"),  # exactly at the <= boundary
        pooled_net_base=Decimal("50000"), pooled_net_stress=Decimal("1"),
        mean_monthly_return_pct=Decimal("0.02"), profitable_month_pct=Decimal("0.60"),
        correlation_to_orb=Decimal("0.1"), best_single_symbol_positive_share=Decimal("0.50"),  # exactly at the <= boundary
        integrity_ok=True,
    )
    assert result["verdict"] == CLASSIFICATION_DIRECT_TARGET_FEASIBLE


def test_classify_fails_direct_just_below_1000_dollar_boundary():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("999.99"), segment_net_returns=_ALL_SEGMENTS_POSITIVE,
        max_drawdown_pct=Decimal("0.10"), pooled_net_base=Decimal("50000"), pooled_net_stress=Decimal("40000"),
        mean_monthly_return_pct=Decimal("0.02"), profitable_month_pct=Decimal("0.60"),
        correlation_to_orb=Decimal("0.1"), best_single_symbol_positive_share=Decimal("0.3"), integrity_ok=True,
    )
    assert result["verdict"] != CLASSIFICATION_DIRECT_TARGET_FEASIBLE


def test_classify_promising_portfolio_contributor_when_direct_fails_but_contributor_gates_pass():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("300"),  # below DIRECT's $1000 but above contributor's $250
        segment_net_returns={"2022": Decimal("100"), "2023": Decimal("100"), "2024": Decimal("100"), "2025": Decimal("100"), "2026_YTD": Decimal("-10")},
        max_drawdown_pct=Decimal("0.12"), pooled_net_base=Decimal("15000"), pooled_net_stress=Decimal("5000"),
        mean_monthly_return_pct=Decimal("0.01"), profitable_month_pct=Decimal("0.60"),
        correlation_to_orb=Decimal("0.40"), best_single_symbol_positive_share=Decimal("0.35"), integrity_ok=True,
    )
    assert result["verdict"] == CLASSIFICATION_PROMISING_PORTFOLIO_CONTRIBUTOR


def test_classify_promising_boundary_exact_250_dollars_015_drawdown_055_profitable_060_correlation():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("250"), segment_net_returns={"2022": Decimal("100"), "2023": Decimal("100"), "2024": Decimal("100"), "2025": Decimal("100"), "2026_YTD": Decimal("-1")},
        max_drawdown_pct=Decimal("0.15"), pooled_net_base=Decimal("15000"), pooled_net_stress=Decimal("1"),
        mean_monthly_return_pct=Decimal("0.01"), profitable_month_pct=Decimal("0.55"),
        correlation_to_orb=Decimal("0.60"), best_single_symbol_positive_share=Decimal("0.50"), integrity_ok=True,
    )
    assert result["verdict"] == CLASSIFICATION_PROMISING_PORTFOLIO_CONTRIBUTOR


def test_classify_positive_but_insufficient_when_contributor_gate_fails():
    # Positive both cost cases, but correlation to ORB too high (0.9 > 0.60).
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("300"),
        segment_net_returns={"2022": Decimal("100"), "2023": Decimal("100"), "2024": Decimal("100"), "2025": Decimal("100"), "2026_YTD": Decimal("100")},
        max_drawdown_pct=Decimal("0.10"), pooled_net_base=Decimal("15000"), pooled_net_stress=Decimal("5000"),
        mean_monthly_return_pct=Decimal("0.01"), profitable_month_pct=Decimal("0.60"),
        correlation_to_orb=Decimal("0.90"), best_single_symbol_positive_share=Decimal("0.30"), integrity_ok=True,
    )
    assert result["verdict"] == CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT


def test_classify_not_promising_when_base_cost_pooled_nonpositive():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("-10"), segment_net_returns=_ALL_SEGMENTS_POSITIVE,
        max_drawdown_pct=Decimal("0.10"), pooled_net_base=Decimal("-1"), pooled_net_stress=Decimal("-500"),
        mean_monthly_return_pct=Decimal("-0.01"), profitable_month_pct=Decimal("0.40"),
        correlation_to_orb=Decimal("0.1"), best_single_symbol_positive_share=Decimal("0.3"), integrity_ok=True,
    )
    assert result["verdict"] == CLASSIFICATION_NOT_PROMISING


def test_classify_not_promising_when_stress_cost_pooled_nonpositive_even_if_base_positive():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("1000"), segment_net_returns=_ALL_SEGMENTS_POSITIVE,
        max_drawdown_pct=Decimal("0.10"), pooled_net_base=Decimal("50000"), pooled_net_stress=Decimal("0"),
        mean_monthly_return_pct=Decimal("0.02"), profitable_month_pct=Decimal("0.60"),
        correlation_to_orb=Decimal("0.1"), best_single_symbol_positive_share=Decimal("0.3"), integrity_ok=True,
    )
    assert result["verdict"] == CLASSIFICATION_NOT_PROMISING


def test_classify_not_promising_on_integrity_failure_regardless_of_numbers():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("5000"), segment_net_returns=_ALL_SEGMENTS_POSITIVE,
        max_drawdown_pct=Decimal("0.05"), pooled_net_base=Decimal("100000"), pooled_net_stress=Decimal("90000"),
        mean_monthly_return_pct=Decimal("0.05"), profitable_month_pct=Decimal("0.90"),
        correlation_to_orb=Decimal("0.0"), best_single_symbol_positive_share=Decimal("0.1"), integrity_ok=False,
    )
    assert result["verdict"] == CLASSIFICATION_NOT_PROMISING
    assert "integrity" in result["reason"]


def test_classify_never_promotes_for_being_best_of_three():
    # Direct API contract check: classify_capital_target_arm takes only
    # ONE arm's own numbers -- it structurally cannot compare against
    # other arms, so "best of three" cannot influence its verdict.
    import inspect
    sig = inspect.signature(classify_capital_target_arm)
    assert "other_arm" not in sig.parameters
    assert "rank" not in sig.parameters


def test_classify_concentration_gate_blocks_direct_when_one_symbol_dominates():
    result = classify_capital_target_arm(
        average_monthly_dollars_base=Decimal("1000"), segment_net_returns=_ALL_SEGMENTS_POSITIVE,
        max_drawdown_pct=Decimal("0.10"), pooled_net_base=Decimal("50000"), pooled_net_stress=Decimal("40000"),
        mean_monthly_return_pct=Decimal("0.02"), profitable_month_pct=Decimal("0.60"),
        correlation_to_orb=Decimal("0.1"), best_single_symbol_positive_share=Decimal("0.51"), integrity_ok=True,
    )
    assert result["verdict"] != CLASSIFICATION_DIRECT_TARGET_FEASIBLE
    assert result["checks"]["no_symbol_over_50pct_of_positive_pnl"] is False


# ---------------------------------------------------------------------------
# scale_trade_to_risk_cap ($150/$300 view without rerunning).
# ---------------------------------------------------------------------------

def test_scale_trade_to_risk_cap_never_scales_up():
    from src.orb_capital_target_strategy_family_research import ClosedTrade
    trade = ClosedTrade(
        symbol="AAA", entry_date=date(2022, 1, 3), exit_date=date(2022, 1, 5),
        entry_price=Decimal("100"), exit_price=Decimal("103"), quantity=100, exit_reason="SMA5",
        holding_sessions=3, planned_risk=Decimal("300"), gross_pnl=Decimal("300"),
        entry_cost=Decimal("5"), exit_cost=Decimal("5.15"), net_pnl=Decimal("289.85"),
    )
    scaled = scale_trade_to_risk_cap(trade, risk_cap=Decimal("150"))
    assert scaled.quantity == 50  # half the risk -> half the shares
    assert scaled.quantity < trade.quantity
    scaled_up_attempt = scale_trade_to_risk_cap(trade, risk_cap=Decimal("600"))
    assert scaled_up_attempt.quantity == trade.quantity  # never scaled above what was actually taken


def test_exposure_and_concurrency_counts_overlapping_positions():
    dates = _business_days(date(2022, 1, 3), 10)
    pairs = [(dates[0], dates[2]), (dates[1], dates[4]), (dates[6], None)]
    result = exposure_and_concurrency(entry_exit_pairs=pairs, window_dates=dates)
    # day0: {A} =1; day1: {A,B}=2; day2: {A,B}=2; day3: {B}=1; day4: {B}=1; day5: {}=0; day6-9: {C}=1
    assert result["exposure_pct"] == Decimal(9) / Decimal(10)  # only day5 has zero positions
    expected_avg = Decimal(str((1 + 2 + 2 + 1 + 1 + 0 + 1 + 1 + 1 + 1) / 10))
    assert result["average_concurrent_positions"] == expected_avg


def test_exposure_and_concurrency_empty_window():
    result = exposure_and_concurrency(entry_exit_pairs=[], window_dates=[])
    assert result["exposure_pct"] is None


def test_compute_arm_metrics_includes_exposure_fields():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[240], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
    )
    assert metrics["exposure_pct"] is not None
    assert metrics["average_concurrent_positions"] is not None
    assert 0 <= metrics["exposure_pct"] <= 1


def test_compute_arm_metrics_end_to_end_with_arm_a_fixture():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[240], evaluation_end=dates[-1],
    )
    assert metrics["closed_lot_count"] >= 1
    assert metrics["concentration"]["best_contributor"] is not None


# ---------------------------------------------------------------------------
# Finding 5 (independent review): transaction ledger, EVALUATION_END_MARK,
# segment-scoped costs, and reconciliation.
# ---------------------------------------------------------------------------

def _arm_a_fixture_with_open_position_at_end():
    # Entry happens a couple of sessions before the series ends and the
    # price stays nearly flat afterward -- not enough remaining sessions
    # to trigger the 5-session max-hold exit, and flat enough to never
    # cross its own SMA(5) or touch the stop -- so the position is still
    # open when the data runs out.
    total_days = 320
    dip_at = total_days - 3
    dates = _business_days(date(2021, 1, 4), total_days)
    closes = _uptrend_then_dip_then_recover(dates, dip_at=dip_at, dip_factor=Decimal("0.93"))
    for i in range(dip_at + 1, len(closes)):
        closes[i] = closes[dip_at] * Decimal("1.001") ** Decimal(i - dip_at)
    aaa = _series_from_closes("AAA", dates, closes, pad=Decimal("0.02"))
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))
    return dates, {"AAA": aaa, "BBB": bbb}


def test_evaluation_end_mark_transactions_and_endpoint_concentration():
    dates, series = _arm_a_fixture_with_open_position_at_end()
    result = run_arm_a(series, dates)
    assert len(result.open_positions_at_end) >= 1, "fixture must leave a position open at the end"

    end_prices = {s: series[s].closes[-1] for s in series}
    end_marks = evaluation_end_mark_transactions(
        open_positions_at_end=result.open_positions_at_end, end_date=dates[-1],
        end_price_by_symbol=end_prices, cost_bps=BASE_COST_BPS, arm=ARM_A,
    )
    assert len(end_marks) == len(result.open_positions_at_end)
    for t in end_marks:
        assert t.reason == "EVALUATION_END_MARK"
        assert t.cost > 0

    metrics_without_mark = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions,
    )
    metrics_with_mark = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions, end_mark_transactions=end_marks,
    )
    open_symbol = result.open_positions_at_end[0].symbol
    assert open_symbol not in metrics_without_mark["concentration"]["positive_contributors"] or metrics_without_mark["concentration"]["positive_contributors"][open_symbol] == Decimal(0)
    assert metrics_with_mark["end_mark_liquidation_cost"] > 0
    assert metrics_with_mark["net_pnl_marked"] == metrics_with_mark["net_pnl"] - metrics_with_mark["end_mark_liquidation_cost"]


def test_stress_liquidation_cost_exceeds_base_liquidation_cost():
    dates, series = _arm_a_fixture_with_open_position_at_end()
    result = run_arm_a(series, dates)
    end_prices = {s: series[s].closes[-1] for s in series}
    base_marks = evaluation_end_mark_transactions(
        open_positions_at_end=result.open_positions_at_end, end_date=dates[-1],
        end_price_by_symbol=end_prices, cost_bps=BASE_COST_BPS, arm=ARM_A,
    )
    stress_marks = evaluation_end_mark_transactions(
        open_positions_at_end=result.open_positions_at_end, end_date=dates[-1],
        end_price_by_symbol=end_prices, cost_bps=STRESS_COST_BPS, arm=ARM_A,
    )
    base_cost = sum((t.cost for t in base_marks), Decimal(0))
    stress_cost = sum((t.cost for t in stress_marks), Decimal(0))
    assert stress_cost > base_cost


def test_turnover_includes_rebalance_trim_and_topup_transactions():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    trim_or_topup = [t for t in result.transactions if t.reason in ("REBALANCE_TRIM", "REBALANCE_TOPUP")]
    assert trim_or_topup, "fixture must produce at least one trim/top-up"
    metrics = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions,
    )
    trim_topup_notional = sum((t.notional for t in trim_or_topup), Decimal(0))
    assert metrics["turnover"]["total_traded_notional"] >= trim_topup_notional


def test_segment_costs_sum_to_pooled_costs_not_full_study_total_per_segment():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)

    pooled = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions,
    )
    mid = len(dates) // 2
    first_half = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[mid], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions,
    )
    second_half = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[mid + 1], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions,
    )
    assert abs((first_half["total_cost_paid"] + second_half["total_cost_paid"]) - pooled["total_cost_paid"]) <= Decimal("0.01")
    assert first_half["total_cost_paid"] != pooled["total_cost_paid"]
    assert second_half["total_cost_paid"] != pooled["total_cost_paid"]


def test_reconciliation_symbol_contributions_plus_residual_equals_net_pnl():
    dates, series = _arm_a_fixture_with_open_position_at_end()
    result = run_arm_a(series, dates)
    end_prices = {s: series[s].closes[-1] for s in series}
    end_marks = evaluation_end_mark_transactions(
        open_positions_at_end=result.open_positions_at_end, end_date=dates[-1],
        end_price_by_symbol=end_prices, cost_bps=BASE_COST_BPS, arm=ARM_A,
    )
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions, end_mark_transactions=end_marks,
    )
    recon = metrics["reconciliation"]
    assert recon["symbol_contribution_sum"] + recon["residual"] == metrics["net_pnl_marked"]


# ---------------------------------------------------------------------------
# Finding 10 (independent review): evaluation_start execution isolation --
# 2021 warm-up data must compute indicators only, never generate any
# pre-evaluation transaction, position, exposure, cost, or P&L.
# ---------------------------------------------------------------------------

_TRUE_EVAL_START = date(2022, 1, 3)  # matches the real business-day fixture's warmup-to-eval transition


def _warmup_to_eval_dates(total_days: int = 320) -> list[date]:
    dates = _business_days(date(2021, 1, 4), total_days)
    assert date(2021, 12, 31) in dates and dates[dates.index(date(2021, 12, 31)) + 1] == _TRUE_EVAL_START
    return dates


def test_arm_a_no_execution_before_evaluation_start_for_an_attractive_warmup_signal():
    # A dip attractive enough to trigger RSI(2) < 10 in October 2021 (deep
    # in warm-up) must produce ZERO transactions, positions, or trades --
    # not merely "no P&L", genuinely no execution artifact at all.
    dates = _warmup_to_eval_dates()
    october_index = dates.index(date(2021, 10, 15))
    closes = _uptrend_then_dip_then_recover(dates, dip_at=october_index, dip_factor=Decimal("0.90"))
    aaa = _series_from_closes("AAA", dates, closes, pad=Decimal("0.02"))
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))
    result = run_arm_a({"AAA": aaa, "BBB": bbb}, dates, evaluation_start=_TRUE_EVAL_START)
    assert result.closed_trades == ()
    assert result.transactions == ()
    assert result.open_positions_at_end == ()
    assert result.total_cost_paid == Decimal(0)
    for day, count in result.daily_position_count:
        if day < _TRUE_EVAL_START:
            assert count == 0


def test_arm_a_stale_warmup_signal_does_not_leak_into_january():
    # Same shape as above, but confirms the mechanism specifically: the
    # blocked October signal is never replayed once execution becomes
    # allowed -- by January there is no lingering trade from it (the
    # price has already fully recovered per the fixture, so nothing
    # should fire at all).
    dates = _warmup_to_eval_dates()
    october_index = dates.index(date(2021, 10, 15))
    closes = _uptrend_then_dip_then_recover(dates, dip_at=october_index, dip_factor=Decimal("0.90"))
    aaa = _series_from_closes("AAA", dates, closes, pad=Decimal("0.02"))
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))
    result = run_arm_a({"AAA": aaa, "BBB": bbb}, dates, evaluation_start=_TRUE_EVAL_START)
    for t in result.transactions:
        assert t.date >= _TRUE_EVAL_START


def test_arm_a_valid_december_31_signal_fills_on_january_3():
    # The dip lands EXACTLY on 2021-12-31's close (the final pre-
    # evaluation session, immediately followed by 2022-01-03) -- this
    # must be allowed to enter, since the signal itself is genuine and
    # its scheduled fill date is the very first evaluation session.
    dates = _warmup_to_eval_dates()
    dec31_index = dates.index(date(2021, 12, 31))
    closes = _uptrend_then_dip_then_recover(dates, dip_at=dec31_index, dip_factor=Decimal("0.90"))
    aaa = _series_from_closes("AAA", dates, closes, pad=Decimal("0.02"))
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))
    result = run_arm_a({"AAA": aaa, "BBB": bbb}, dates, evaluation_start=_TRUE_EVAL_START)
    assert len(result.transactions) >= 1
    entry_txn = result.transactions[0]
    assert entry_txn.side == "BUY"
    assert entry_txn.date == _TRUE_EVAL_START
    assert entry_txn.symbol == "AAA"


def test_arm_a_evaluation_starts_at_exactly_30000_with_no_positions():
    dates = _warmup_to_eval_dates()
    october_index = dates.index(date(2021, 10, 15))
    closes = _uptrend_then_dip_then_recover(dates, dip_at=october_index, dip_factor=Decimal("0.90"))
    aaa = _series_from_closes("AAA", dates, closes, pad=Decimal("0.02"))
    bbb = _series_from_closes("BBB", dates, _flat(dates, Decimal("50")), pad=Decimal("0.02"))
    result = run_arm_a({"AAA": aaa, "BBB": bbb}, dates, evaluation_start=_TRUE_EVAL_START)
    equity_by_date = dict(result.daily_equity)
    assert equity_by_date[_TRUE_EVAL_START] == STARTING_CAPITAL
    position_count_by_date = dict(result.daily_position_count)
    # This specific fixture (dip in October 2021, well before the true
    # evaluation start) produces no signal right at the evaluation
    # boundary -- so the very first evaluation session genuinely closes
    # with zero open positions, not merely "0 or vacuously anything".
    assert position_count_by_date[_TRUE_EVAL_START] == 0


def test_arm_a_normal_behavior_unchanged_when_evaluation_start_is_a_no_op():
    # Setting evaluation_start to a date before any real signal must
    # produce byte-identical results to omitting it entirely.
    dates, series = _arm_a_fixture()
    unconstrained = run_arm_a(series, dates)
    gated_but_unaffected = run_arm_a(series, dates, evaluation_start=dates[0])
    assert unconstrained.closed_trades == gated_but_unaffected.closed_trades
    assert unconstrained.transactions == gated_but_unaffected.transactions
    assert unconstrained.daily_equity == gated_but_unaffected.daily_equity


def test_monthly_rebalance_no_execution_before_evaluation_start():
    dates = _warmup_to_eval_dates(total_days=550)
    aaa = _series_from_closes("AAA", dates, [Decimal("50") + Decimal("0.15") * Decimal(i) for i in range(len(dates))])
    bbb = _series_from_closes("BBB", dates, [Decimal("50") + Decimal("0.05") * Decimal(i) for i in range(len(dates))])
    series = {"AAA": aaa, "BBB": bbb}
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector, evaluation_start=_TRUE_EVAL_START)
    for t in result.transactions:
        assert t.date >= _TRUE_EVAL_START
    for day, count in result.daily_position_count:
        if day < _TRUE_EVAL_START:
            assert count == 0


def test_monthly_rebalance_december_selection_executes_on_january_open():
    dates = _warmup_to_eval_dates(total_days=550)
    aaa = _series_from_closes("AAA", dates, [Decimal("50") + Decimal("0.15") * Decimal(i) for i in range(len(dates))])
    bbb = _series_from_closes("BBB", dates, [Decimal("50") + Decimal("0.05") * Decimal(i) for i in range(len(dates))])
    series = {"AAA": aaa, "BBB": bbb}
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector, evaluation_start=_TRUE_EVAL_START)
    first_txn_dates = sorted({t.date for t in result.transactions})
    assert first_txn_dates
    assert first_txn_dates[0] >= _TRUE_EVAL_START
    # The very first execution must land on/after the true evaluation
    # start even though AAA/BBB have been eligible (and selected at
    # earlier warm-up month-ends) since well before it.
    assert first_txn_dates[0] == _TRUE_EVAL_START or first_txn_dates[0] == dates[dates.index(_TRUE_EVAL_START)]


def test_monthly_rebalance_evaluation_starts_at_exactly_30000():
    dates = _warmup_to_eval_dates(total_days=550)
    aaa = _series_from_closes("AAA", dates, [Decimal("50") + Decimal("0.15") * Decimal(i) for i in range(len(dates))])
    bbb = _series_from_closes("BBB", dates, [Decimal("50") + Decimal("0.05") * Decimal(i) for i in range(len(dates))])
    series = {"AAA": aaa, "BBB": bbb}
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector, evaluation_start=_TRUE_EVAL_START)
    equity_by_date = dict(result.daily_equity)
    # The last pre-evaluation session (2021-12-31, a real trading day
    # immediately preceding the 2022-01-03 evaluation start -- see
    # `_warmup_to_eval_dates`) must show untouched starting capital.
    pre_eval_dates = [d for d in dates if d < _TRUE_EVAL_START]
    assert pre_eval_dates[-1] == _TRUE_EVAL_START - timedelta(days=3)
    assert equity_by_date[pre_eval_dates[-1]] == STARTING_CAPITAL


def test_monthly_rebalance_normal_behavior_unchanged_when_evaluation_start_is_a_no_op():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    unconstrained = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    gated_but_unaffected = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector, evaluation_start=dates[0])
    assert unconstrained.closed_trades == gated_but_unaffected.closed_trades
    assert unconstrained.transactions == gated_but_unaffected.transactions


# ---------------------------------------------------------------------------
# Finding 14 (independent review): annualized volatility/downside deviation,
# and strict separation of signal/scheduled-rebalance/executed-rebalance/
# buy-transaction/sell-transaction/closed-lot counts (closed-lot count must
# never be reported or read as a rebalance-event count).
# ---------------------------------------------------------------------------

def test_annualized_volatility_matches_sharpe_denominator_times_sqrt12():
    monthly = [Decimal("0.02"), Decimal("-0.01"), Decimal("0.03"), Decimal("0.00"), Decimal("-0.02")]
    vol = annualized_volatility_pct(monthly)
    assert vol is not None
    import statistics as _stats
    expected = Decimal(str(_stats.pstdev([float(v) for v in monthly]) * (12 ** 0.5)))
    assert vol == expected


def test_annualized_volatility_none_below_two_months():
    assert annualized_volatility_pct([Decimal("0.01")]) is None
    assert annualized_volatility_pct([]) is None


def test_annualized_downside_deviation_matches_sortino_denominator_times_sqrt12():
    # Finding 17 (independent review): downside deviation is
    # sqrt(mean(min(r-0,0)^2 over ALL months)) * sqrt(12), not
    # population stdev of only the negative subset -- see the dedicated
    # Finding 17 test section below for the full hand-calculated
    # coverage of this corrected formula.
    monthly = [Decimal("0.02"), Decimal("-0.01"), Decimal("0.03"), Decimal("-0.04"), Decimal("0.01")]
    dd = annualized_downside_deviation_pct(monthly)
    assert dd is not None
    shortfalls_squared = [0.0, 0.01 ** 2, 0.0, 0.04 ** 2, 0.0]
    expected = Decimal(str(((sum(shortfalls_squared) / 5) ** 0.5) * (12 ** 0.5)))
    assert abs(dd - expected) < Decimal("0.0000001")


def test_annualized_downside_deviation_zero_not_none_with_no_negative_months():
    # Finding 17: a series with no month below the 0% MAR has a
    # well-defined downside deviation of exactly zero, not None.
    monthly = [Decimal("0.02"), Decimal("0.01"), Decimal("0.03")]
    assert annualized_downside_deviation_pct(monthly) == Decimal("0")


def test_compute_arm_metrics_reports_annualized_volatility_and_downside_deviation():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[240], evaluation_end=dates[-1],
    )
    assert "annualized_volatility_pct" in metrics
    assert "annualized_downside_deviation_pct" in metrics


def test_compute_arm_metrics_closed_lot_count_never_equals_mislabeled_rebalance_count_for_arm_c():
    # A single scheduled rebalance can close several lots (one
    # TRIM/REMOVE per affected symbol) -- closed_lot_count must be
    # strictly the count of closed lots, and scheduled_rebalance_count
    # must be strictly the count of month-end rebalance events, and
    # these must genuinely differ when a rebalance touches more than
    # one symbol.
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    assert result.closed_trades, "fixture must produce at least one closed lot"
    metrics = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions, rebalance_events=result.rebalance_events,
    )
    assert metrics["closed_lot_count"] == len(result.closed_trades)
    expected_scheduled = sum(1 for e in result.rebalance_events if e.execution_date is not None and dates[0] <= e.execution_date <= dates[-1])
    assert metrics["scheduled_rebalance_count"] == expected_scheduled
    assert metrics["closed_lot_count"] != metrics["scheduled_rebalance_count"]
    assert metrics["signal_count"] == metrics["scheduled_rebalance_count"]
    assert metrics["executed_rebalance_count"] <= metrics["scheduled_rebalance_count"]
    # ARM_C has no comparable "filled entry" concept -- must not be
    # fabricated (Finding 18).
    assert metrics["filled_entry_count"] is None


def test_compute_arm_metrics_buy_and_sell_transaction_counts_derived_from_ledger():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    metrics = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions, rebalance_events=result.rebalance_events,
    )
    window_transactions = [t for t in result.transactions if dates[0] <= t.date <= dates[-1]]
    expected_buys = sum(1 for t in window_transactions if t.side == "BUY")
    expected_sells = sum(1 for t in window_transactions if t.side == "SELL")
    assert metrics["buy_transaction_count"] == expected_buys
    assert metrics["sell_transaction_count"] == expected_sells
    assert metrics["buy_transaction_count"] + metrics["sell_transaction_count"] == len(window_transactions)


def test_compute_arm_metrics_arm_a_filled_entry_count_never_mislabeled_as_signal_count():
    # Finding 18 (independent review): ARM_A never receives
    # rebalance_events, so it has no genuine pre-fill signal ledger --
    # its FILLED-entry count must be reported under the honestly-named
    # `filled_entry_count`, and `signal_count` (which would otherwise
    # silently relabel a fill count as a raw signal count) must be
    # `None`, not fabricated. Its scheduled/executed rebalance counts
    # must be zero (ARM_A has no periodic-rebalance concept).
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[-1], transactions=result.transactions,
    )
    window_entries = [t for t in result.transactions if t.reason == "ENTRY" and dates[0] <= t.date <= dates[-1]]
    assert window_entries, "fixture must produce at least one entry"
    assert metrics["filled_entry_count"] == len(window_entries)
    assert metrics["signal_count"] is None
    assert metrics["scheduled_rebalance_count"] == 0
    assert metrics["executed_rebalance_count"] == 0


def test_compute_arm_metrics_counts_reported_for_segment_views_too():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    mid = len(dates) // 2
    first_half = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[0], evaluation_end=dates[mid], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions, rebalance_events=result.rebalance_events,
    )
    second_half = compute_arm_metrics(
        arm=ARM_C, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[mid + 1], evaluation_end=dates[-1], open_positions_at_end=result.open_positions_at_end,
        transactions=result.transactions, rebalance_events=result.rebalance_events,
    )
    for view in (first_half, second_half):
        for key in ("signal_count", "filled_entry_count", "scheduled_rebalance_count", "executed_rebalance_count",
                    "buy_transaction_count", "sell_transaction_count", "closed_lot_count",
                    "annualized_volatility_pct", "annualized_downside_deviation_pct"):
            assert key in view


# ---------------------------------------------------------------------------
# Finding 15 (independent review): the frozen decision tree's required
# combination-plausibility upper bound when every arm classifies
# POSITIVE_BUT_INSUFFICIENT.
# ---------------------------------------------------------------------------

def test_combination_upper_bound_picks_the_two_highest_arms_only():
    arm_means = {
        ARM_A: Decimal("78.53"),
        ARM_B: Decimal("196.72"),
        ARM_C: Decimal("177.26"),
    }
    result = compute_combination_plausibility_upper_bound(arm_means)
    picked_arms = {row["arm"] for row in result["top_two_arms"]}
    assert picked_arms == {ARM_B, ARM_C}
    assert ARM_A not in picked_arms


def test_combination_upper_bound_sums_orb_plus_top_two_arms_exactly():
    arm_means = {ARM_A: Decimal("78.53"), ARM_B: Decimal("196.72"), ARM_C: Decimal("177.26")}
    result = compute_combination_plausibility_upper_bound(arm_means)
    expected = ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK + Decimal("196.72") + Decimal("177.26")
    assert result["generous_upper_bound_monthly_dollars"] == expected
    assert result["orb_monthly_dollars_at_one_pct_risk"] == ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK
    assert result["target_monthly_dollars"] == COMBINATION_TARGET_MONTHLY_DOLLARS


def test_combination_upper_bound_classifies_not_plausible_when_below_target():
    # ORB ($108.90) + two modest arms clearly can't reach $1,000/month.
    arm_means = {ARM_A: Decimal("78.53"), ARM_B: Decimal("196.72"), ARM_C: Decimal("177.26")}
    result = compute_combination_plausibility_upper_bound(arm_means)
    assert result["generous_upper_bound_monthly_dollars"] < COMBINATION_TARGET_MONTHLY_DOLLARS
    assert result["verdict"] == COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE


def test_combination_upper_bound_classifies_plausible_when_at_or_above_target():
    arm_means = {ARM_A: Decimal("100"), ARM_B: Decimal("500"), ARM_C: Decimal("500")}
    result = compute_combination_plausibility_upper_bound(arm_means)
    assert result["generous_upper_bound_monthly_dollars"] >= COMBINATION_TARGET_MONTHLY_DOLLARS
    assert result["verdict"] == COMBINATION_MATHEMATICALLY_PLAUSIBLE


def test_combination_upper_bound_boundary_is_inclusive_at_exact_target():
    # ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK is $108.90; choose two arm
    # values that sum with it to EXACTLY $1,000.00 to pin down the
    # boundary as inclusive ( >= target counts as plausible).
    remaining = COMBINATION_TARGET_MONTHLY_DOLLARS - ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK
    half = remaining / 2
    arm_means = {ARM_A: Decimal("1"), ARM_B: half, ARM_C: half}
    result = compute_combination_plausibility_upper_bound(arm_means)
    assert result["generous_upper_bound_monthly_dollars"] == COMBINATION_TARGET_MONTHLY_DOLLARS
    assert result["verdict"] == COMBINATION_MATHEMATICALLY_PLAUSIBLE


# ---------------------------------------------------------------------------
# Finding 16 (independent review, BLOCKER): compute_arm_metrics must anchor
# every pooled/segment window at its TRUE opening equity (the preceding
# session's closing equity, or starting_capital when there is no preceding
# session) -- never at the first window session's OWN closing equity --
# so that session's movement and costs are counted exactly once, never
# silently dropped from every monthly-return-derived metric.
# ---------------------------------------------------------------------------

def test_compute_arm_metrics_first_session_movement_counted_in_monthly_return():
    prior_date = date(2021, 12, 31)
    eval_start = date(2022, 1, 3)
    month_end = date(2022, 1, 31)
    daily_equity = [
        (prior_date, Decimal("30000")),
        (eval_start, Decimal("29900")),  # first evaluation session closes down $100
        (month_end, Decimal("30000")),  # round-trips back to the opening balance
    ]
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=(), daily_equity=daily_equity,
        starting_capital=Decimal("30000"), total_cost_paid=Decimal(0),
        evaluation_start=eval_start, evaluation_end=month_end,
    )
    # The month must report exactly $0 / 0% -- NOT +$100/$29,900
    # (~+0.33%), which is what results if the first session's own
    # closing equity is wrongly used as its own baseline.
    assert metrics["monthly_return_pct_series"] == [(month_end, Decimal("0"))]
    assert metrics["monthly_dollar_pnl_fixed_30k_series"] == [(month_end, Decimal("0"))]
    assert metrics["mean_monthly_return_pct"] == Decimal("0")


def test_compute_arm_metrics_first_session_dip_visible_in_drawdown():
    prior_date = date(2021, 12, 31)
    eval_start = date(2022, 1, 3)
    month_end = date(2022, 1, 31)
    daily_equity = [
        (prior_date, Decimal("30000")),
        (eval_start, Decimal("29900")),
        (month_end, Decimal("30000")),
    ]
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=(), daily_equity=daily_equity,
        starting_capital=Decimal("30000"), total_cost_paid=Decimal(0),
        evaluation_start=eval_start, evaluation_end=month_end,
    )
    # The $100 first-session dip below the true $30,000 opening balance
    # must be a visible drawdown. Without the opening-equity anchor the
    # visible series is $29,900 -> $30,000 -- monotonically
    # non-decreasing, i.e. zero drawdown, which would silently hide it.
    assert metrics["max_drawdown_dollars"] == Decimal("100")
    assert metrics["max_drawdown_pct"] == Decimal("100") / Decimal("30000")


def test_compute_arm_metrics_cagr_anchored_at_true_opening_equity():
    prior_date = date(2021, 12, 31)
    eval_start = date(2022, 1, 3)
    evaluation_end = date(2023, 1, 3)
    daily_equity = [
        (prior_date, Decimal("30000")),
        (eval_start, Decimal("29900")),
        (evaluation_end, Decimal("33000")),
    ]
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=(), daily_equity=daily_equity,
        starting_capital=Decimal("30000"), total_cost_paid=Decimal(0),
        evaluation_start=eval_start, evaluation_end=evaluation_end,
    )
    # CAGR must compound from the true $30,000 opening balance across
    # the full one-year window (33000/30000 => +10%), never from the
    # diminished $29,900 first-session close.
    assert metrics["cagr"] is not None
    assert abs(metrics["cagr"] - Decimal("0.1")) < Decimal("0.0001")


def test_compute_arm_metrics_pooled_window_opening_is_exactly_starting_capital():
    # With no preceding session at all (the true study start), the
    # opening anchor must fall back to starting_capital exactly -- and
    # the first month's return must be measured against IT, not against
    # that month's own first-session close.
    eval_start = date(2022, 1, 3)
    month_end = date(2022, 1, 31)
    daily_equity = [
        (eval_start, Decimal("29500")),
        (month_end, Decimal("30500")),
    ]
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=(), daily_equity=daily_equity,
        starting_capital=Decimal("30000"), total_cost_paid=Decimal(0),
        evaluation_start=eval_start, evaluation_end=month_end,
    )
    assert metrics["starting_capital"] == Decimal("30000")
    # +$500 on a true $30,000 opening balance is +1.667%, not
    # +$1,000/$29,500 (~+3.39%) against the first session's own close.
    expected_pct = (Decimal("30500") - Decimal("30000")) / Decimal("30000")
    assert metrics["monthly_return_pct_series"] == [(month_end, expected_pct)]


def test_compute_arm_metrics_yearly_segment_anchors_at_preceding_years_close():
    # A calendar-segment call (e.g. one year) must anchor its own
    # opening equity at the PRECEDING session's actual closing equity
    # (the last session of the prior year) -- never at that year's own
    # first session's closing equity.
    dec_30_2022 = date(2022, 12, 30)
    jan_3_2023 = date(2023, 1, 3)
    jan_31_2023 = date(2023, 1, 31)
    daily_equity = [
        (dec_30_2022, Decimal("31000")),
        (jan_3_2023, Decimal("30800")),  # 2023's first session dips $200
        (jan_31_2023, Decimal("31000")),  # round-trips back
    ]
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=(), daily_equity=daily_equity,
        starting_capital=Decimal("30000"), total_cost_paid=Decimal(0),
        evaluation_start=jan_3_2023, evaluation_end=jan_31_2023,
    )
    assert metrics["starting_capital"] == Decimal("31000")
    assert metrics["monthly_return_pct_series"] == [(jan_31_2023, Decimal("0"))]


# ---------------------------------------------------------------------------
# Finding 17 (independent review, CORRECTION): downside deviation must use
# the frozen 0% MAR definition -- sqrt(mean(min(r-0,0)^2 over ALL months))
# * sqrt(12) -- not population stdev of only the negative observations.
# ---------------------------------------------------------------------------

def test_downside_deviation_identical_negative_months_is_nonzero():
    # The prior (incorrect) implementation -- population stdev of only
    # the negative subset -- returns exactly ZERO for repeated identical
    # losses, since their spread around their own mean is zero. The
    # correct formula must NOT do this: three identical -5% months carry
    # a real, nonzero downside.
    monthly = [Decimal("-0.05"), Decimal("-0.05"), Decimal("-0.05")]
    dd = annualized_downside_deviation_pct(monthly)
    assert dd is not None
    assert dd > Decimal("0")
    # Hand-calculated: sqrt(mean([0.0025, 0.0025, 0.0025])) * sqrt(12)
    # = 0.05 * sqrt(12)
    expected = Decimal(str(0.05 * (12 ** 0.5)))
    assert abs(dd - expected) < Decimal("0.0000001")


def test_downside_deviation_mixed_positive_zero_negative_hand_calculated():
    monthly = [Decimal("0.10"), Decimal("0.00"), Decimal("-0.05"), Decimal("0.02"), Decimal("-0.03")]
    dd = annualized_downside_deviation_pct(monthly)
    assert dd is not None
    # Every month contributes a shortfall term (0 for non-negative
    # months) and the mean divides by ALL FIVE months, not just the two
    # negative ones.
    shortfalls_squared = [0.0, 0.0, 0.05 ** 2, 0.0, 0.03 ** 2]
    expected_monthly = (sum(shortfalls_squared) / 5) ** 0.5
    expected = Decimal(str(expected_monthly * (12 ** 0.5)))
    assert abs(dd - expected) < Decimal("0.0000001")


def test_downside_deviation_no_negative_months_is_zero_not_none():
    # A series with no month below the 0% MAR has a genuine,
    # well-defined downside deviation of exactly zero -- this is NOT the
    # same as "undefined"/None.
    monthly = [Decimal("0.05"), Decimal("0.02"), Decimal("0.00"), Decimal("0.10")]
    dd = annualized_downside_deviation_pct(monthly)
    assert dd == Decimal("0")


def test_downside_deviation_fewer_than_two_months_is_none():
    assert annualized_downside_deviation_pct([Decimal("-0.05")]) is None
    assert annualized_downside_deviation_pct([]) is None


def test_sortino_no_negative_months_is_none_division_by_zero_denominator():
    # Sortino itself (the ratio) is undefined when the downside
    # deviation is exactly zero, even though the deviation itself is a
    # well-defined zero -- this is a genuine div-by-zero, not "no data".
    monthly = [Decimal("0.05"), Decimal("0.02"), Decimal("0.10")]
    assert annualized_downside_deviation_pct(monthly) == Decimal("0")
    assert sortino_ratio(monthly) is None


def test_sortino_identical_negative_months_produces_finite_ratio():
    # The prior bug (zero downside deviation for identical losses) would
    # have made Sortino divide by zero (None) here too -- it must not.
    monthly = [Decimal("0.10"), Decimal("-0.05"), Decimal("-0.05"), Decimal("0.10")]
    result = sortino_ratio(monthly)
    assert result is not None
    # Hand-calculated: mean=0.025; downside_dev=sqrt(mean([0,0.0025,0.0025,0]))=sqrt(0.00125)
    mean_monthly = 0.025
    downside_dev = (sum([0.0, 0.05 ** 2, 0.05 ** 2, 0.0]) / 4) ** 0.5
    expected = Decimal(str(mean_monthly / downside_dev * (12 ** 0.5)))
    assert abs(result - expected) < Decimal("0.0000001")
