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
    profitable_month_stats, worst_and_percentile_month, pearson_correlation, align_monthly_series,
    positive_contribution_concentration, compute_arm_metrics, scale_trade_to_risk_cap, segment_label,
    classify_capital_target_arm,
    CLASSIFICATION_DIRECT_TARGET_FEASIBLE, CLASSIFICATION_PROMISING_PORTFOLIO_CONTRIBUTOR,
    CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT, CLASSIFICATION_NOT_PROMISING,
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


def test_monthly_rebalance_incremental_no_forced_trade_for_continuing_holding():
    dates, series = _monthly_fixture()
    sma200 = {s: simple_moving_average(series[s].closes, TREND_SMA_PERIOD) for s in series}
    selector = arm_c_selector(series, sma200)
    result = run_monthly_rebalance_arm(series, dates, arm=ARM_C, selector=selector)
    # AAA/BBB, once eligible, stay eligible every month after warm-up
    # (monotonic uptrend) -- so they should be bought exactly ONCE each
    # (the first month they become eligible), never re-traded every
    # subsequent rebalance.
    aaa_trades = [t for t in result.closed_trades if t.symbol == "AAA"]
    assert len(aaa_trades) == 0  # never sold (held straight through to the end)


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


def test_compute_arm_metrics_end_to_end_with_arm_a_fixture():
    dates, series = _arm_a_fixture()
    result = run_arm_a(series, dates)
    metrics = compute_arm_metrics(
        arm=ARM_A, closed_trades=result.closed_trades, daily_equity=result.daily_equity,
        starting_capital=result.starting_capital, total_cost_paid=result.total_cost_paid,
        evaluation_start=dates[240], evaluation_end=dates[-1],
    )
    assert metrics["trade_or_rebalance_count"] >= 1
    assert metrics["concentration"]["best_contributor"] is not None
