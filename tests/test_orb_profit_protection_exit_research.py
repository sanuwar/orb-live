"""Focused tests for M43 profit-protection exit research (Issue #336)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.orb_profit_protection_exit_research import (
    ARM_BASELINE,
    ARM_BE_AFTER_050R,
    ARM_PROFIT_LADDER,
    ARM_STRUCTURE_TRAIL_AFTER_1R,
    ENTRY_CURRENT_FIRST_VALID,
    ENTRY_WAITED_FIRST_VALID_0950,
    EXIT_ARMS,
    PROTECTION_ARMS,
    IndexedMarketBars,
    ProfitProtectionExitError,
    _build_rows_for_current,
    _build_rows_for_waited,
    _session_bars,
    build_all_entry_and_exit_rows,
    full_stop_out_rate,
    index_market_bars_by_date,
    max_drawdown_r_and_streak_from_rows,
    protection_diagnostics,
    simulate_exit_arm,
)
from src.orb_candidate_selection_timing_research import (
    ARM_CURRENT_FIRST_VALID,
    ARM_WAITED_FIRST_VALID_0950,
    CONFIG_ENTRY,
    build_all_arms_rows,
    early_pool,
    enrich_with_decision_time,
)
from src.orb_gap_rvol_sensitivity_research import sensitivity_config
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.shadow_execution_simulator import ShadowExecutionState
from tests.test_orb_candidate_selection_timing_research import STRONG_BREAKOUT_HOLDS, _evaluate_single_day
from tests.test_shadow_execution_simulator import bar, candidate

# `candidate()`/`bar()` are reused unmodified from the pinned simulator's
# own test suite -- entry=100, stop=99 (1R = $1 exactly), target defaults
# to None so the real `ExitEngine` config resolves it to +2R = 102.


def test_no_protection_before_threshold_all_arms_match_baseline():
    """MFE never reaches +0.50R -- every protection arm must be byte-
    identical to BASELINE (the pinned, unmodified simulator)."""
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),   # entry fill
        bar(2, open="100", high="100.4", low="100.0", close="100.2"),  # mfe=0.4R
        bar(3, open="100.2", high="100.45", low="98.5", close="98.6"),  # stop touch at 99
    ]
    baseline = simulate_exit_arm(candidate(), bars, arm=ARM_BASELINE)
    assert baseline.state is ShadowExecutionState.STOPPED
    assert baseline.exit_price == Decimal("99") and baseline.realized_pnl == Decimal("-1")
    for arm in PROTECTION_ARMS:
        outcome = simulate_exit_arm(candidate(), bars, arm=arm)
        assert outcome.state == baseline.state
        assert outcome.exit_price == baseline.exit_price
        assert outcome.realized_pnl == baseline.realized_pnl
        assert outcome.final_stop_price == Decimal("99")
        assert outcome.protection_activated is False


def test_be_after_050r_exact_boundary_moves_stop_next_bar_only():
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),          # entry fill
        bar(2, open="100", high="100.5", low="99.9", close="100.3"),        # mfe reaches EXACTLY +0.50R
        bar(3, open="100.3", high="100.6", low="99.95", close="100.2"),     # would NOT touch old stop (99) but DOES touch new stop (100)
    ]
    baseline = simulate_exit_arm(candidate(), bars, arm=ARM_BASELINE)
    assert baseline.state is ShadowExecutionState.FILLED_OPEN  # never moves before +1R in production

    protected = simulate_exit_arm(candidate(), bars, arm=ARM_BE_AFTER_050R)
    assert protected.state is ShadowExecutionState.STOPPED
    assert protected.exit_price == Decimal("100")  # breakeven, not the original 99
    assert protected.realized_pnl == Decimal("0")
    assert protected.protection_activated is True
    # The move only took effect starting bar 3 -- bar 2's own low (99.9) never
    # triggered anything, proving "effective beginning with the next bar".


def test_same_bar_rule_old_stop_authoritative_when_threshold_and_stop_touch_together():
    """A single bar that both reaches +0.50R (high) and touches the OLD
    stop (low) must be resolved using the OLD stop -- never the just-
    crossed protection level."""
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),
        bar(2, open="100.3", high="100.5", low="98.9", close="99.0"),  # high=+0.50R AND low<=99 (old stop) in ONE bar
    ]
    for arm in (ARM_BE_AFTER_050R, ARM_PROFIT_LADDER, ARM_STRUCTURE_TRAIL_AFTER_1R):
        outcome = simulate_exit_arm(candidate(), bars, arm=arm)
        assert outcome.state is ShadowExecutionState.STOPPED
        assert outcome.exit_price == Decimal("99")  # the OLD stop, not entry/breakeven
        assert outcome.realized_pnl == Decimal("-1")
        assert outcome.protection_activated is False


def test_profit_ladder_three_tiers_and_stop_never_loosens():
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),          # entry
        bar(2, open="100", high="100.5", low="99.9", close="100.3"),        # mfe=0.50R -> next stop=100
        bar(3, open="100.3", high="100.75", low="100.05", close="100.5"),   # mfe=0.75R -> next stop=100.25
        bar(4, open="100.5", high="101.0", low="100.3", close="100.8"),     # mfe=1.00R -> next stop=100.50
        bar(5, open="100.8", high="100.9", low="100.35", close="100.6"),    # stop touched at 100.50
    ]
    outcome = simulate_exit_arm(candidate(), bars, arm=ARM_PROFIT_LADDER)
    assert outcome.state is ShadowExecutionState.STOPPED
    assert outcome.exit_price == Decimal("100.50")
    assert outcome.realized_pnl == Decimal("0.50")
    assert outcome.protection_activated is True

    # A pullback that never reaches the next tier must never loosen the stop.
    bars_pullback = bars[:3] + [bar(4, open="100.5", high="100.6", low="100.3", close="100.4")]
    held = simulate_exit_arm(candidate(), bars_pullback, arm=ARM_PROFIT_LADDER)
    assert held.state is ShadowExecutionState.FILLED_OPEN
    assert held.final_stop_price == Decimal("100.25")  # tier reached at bar 3, not loosened by bar 4's pullback


def test_structure_trail_holds_baseline_before_1r_then_ratchets_completed_bar_lows():
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),          # entry
        bar(2, open="100", high="100.8", low="99.9", close="100.5"),        # mfe=0.80R -- below +1R, no movement
    ]
    before_1r = simulate_exit_arm(candidate(), bars, arm=ARM_STRUCTURE_TRAIL_AFTER_1R)
    assert before_1r.state is ShadowExecutionState.FILLED_OPEN
    assert before_1r.final_stop_price == Decimal("99")  # untouched, identical to baseline pre-1R

    full_path = bars + [
        bar(3, open="100.5", high="101.0", low="100.2", close="100.9"),     # mfe=1.00R, completed low=100.2 -> next stop=max(99,100,100.2)=100.2
        bar(4, open="100.9", high="101.5", low="100.6", close="101.2"),     # new mfe=1.50R, completed low=100.6 -> next stop=max(100.2,100,100.6)=100.6
        bar(5, open="101.2", high="101.3", low="100.5", close="100.7"),     # stop touched at 100.6
    ]
    outcome = simulate_exit_arm(candidate(), full_path, arm=ARM_STRUCTURE_TRAIL_AFTER_1R)
    assert outcome.state is ShadowExecutionState.STOPPED
    assert outcome.exit_price == Decimal("100.6")
    assert outcome.realized_pnl == Decimal("0.6")
    assert outcome.protection_activated is True


def test_stop_only_rises_never_falls_across_all_protection_arms():
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),
        bar(2, open="100", high="101.0", low="99.9", close="100.8"),   # mfe=1.00R immediately
        bar(3, open="100.8", high="100.9", low="99.5", close="99.6"),  # sharp pullback -- should NOT drop stop
    ]
    for arm in PROTECTION_ARMS:
        outcome = simulate_exit_arm(candidate(), bars, arm=arm)
        # every arm's post-1R stop must be >= entry (99.5 pullback alone
        # cannot force a stop below entry once protection has activated)
        assert outcome.final_stop_price is not None
        assert outcome.final_stop_price >= Decimal("99")
        if outcome.state is ShadowExecutionState.STOPPED:
            assert outcome.exit_price >= Decimal("99")


def test_target_stop_ambiguous_ordering_preserved_for_protection_arms():
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),
        bar(2, open="100", high="100.5", low="99.9", close="100.3"),   # activates BE_AFTER_050R -> stop=100 next bar
        bar(3, open="100.3", high="103.0", low="99.0", close="101.0"),  # low<=100 AND high>=102 (target) in one bar
    ]
    outcome = simulate_exit_arm(candidate(), bars, arm=ARM_BE_AFTER_050R)
    assert outcome.state is ShadowExecutionState.AMBIGUOUS
    assert outcome.realized_pnl is None


def test_time_exit_unchanged_for_protection_arms():
    day = date(2026, 8, 11)
    signal = datetime(2026, 8, 11, 14, 40, tzinfo=timezone.utc)
    eod = datetime.combine(day, time(15, 45), tzinfo=EASTERN).astimezone(timezone.utc)
    entry_bar = bar(1, open="100", low="99.9", high="100.1", close="100")
    ramp_bar = bar(2, open="100", high="100.5", low="99.9", close="100.3")  # activates protection, stop!=99
    time_bar = replace(bar(2, open="100.3", high="100.6", low="100.1", close="100.4"), event_timestamp=eod, end_timestamp=eod + timedelta(minutes=1))
    bars = [entry_bar, ramp_bar, time_bar]
    for arm in (ARM_BASELINE,) + PROTECTION_ARMS:
        outcome = simulate_exit_arm(candidate(), bars, arm=arm)
        assert outcome.state is ShadowExecutionState.TIME_EXIT
        assert outcome.exit_price == Decimal("100.4")
        assert outcome.realized_pnl == Decimal("0.4")


def test_entry_policy_and_data_invalid_paths_are_identical_across_every_arm():
    invalid_bar = replace(bar(1), quality=__import__("src.market_data_canonical", fromlist=["MarketDataQuality"]).MarketDataQuality.STALE)
    for arm in EXIT_ARMS:
        outcome = simulate_exit_arm(candidate(), [invalid_bar], arm=arm)
        assert outcome.state is ShadowExecutionState.DATA_INVALID
        assert outcome.entry_price is None


def test_deterministic_output():
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),
        bar(2, open="100", high="100.8", low="99.9", close="100.5"),
        bar(3, open="100.5", high="101.0", low="100.2", close="100.9"),
        bar(4, open="100.9", high="101.5", low="100.6", close="101.2"),
    ]
    for arm in EXIT_ARMS:
        first = simulate_exit_arm(candidate(), bars, arm=arm)
        second = simulate_exit_arm(candidate(), bars, arm=arm)
        assert first == second


def test_no_lookahead_a_stop_touch_ends_the_walk_before_later_bars_are_seen():
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),   # entry
        bar(2, open="100", high="100.2", low="98.0", close="98.1"),  # immediate stop touch at 99
        bar(3, open="200", high="500", low="200", close="300"),      # would hit target if this were peeked at
    ]
    for arm in EXIT_ARMS:
        outcome = simulate_exit_arm(candidate(), bars, arm=arm)
        assert outcome.state is ShadowExecutionState.STOPPED
        assert outcome.exit_price == Decimal("99")
        assert outcome.bars_evaluated_after_entry == 1


def test_r_calculations_and_unknown_arm_rejected():
    bars = [
        bar(1, open="100", low="99.9", high="100.1", close="100"),
        bar(2, open="100.3", high="102.2", low="100.1", close="102.0"),
    ]
    outcome = simulate_exit_arm(candidate(), bars, arm=ARM_BASELINE)
    assert outcome.state is ShadowExecutionState.TARGET_HIT
    risk = outcome.entry_price - outcome.initial_stop_price
    assert risk == Decimal("1")
    assert (outcome.realized_pnl / risk) == Decimal("2")  # +2R target hit

    with pytest.raises(ProfitProtectionExitError):
        simulate_exit_arm(candidate(), bars, arm="NOT_A_REAL_ARM")


# ---------------------------------------------------------------------------
# Reporting-helper unit tests (no simulator involvement).
# ---------------------------------------------------------------------------

class _FakeRow:
    def __init__(self, *, session_date: str, symbol: str, closed: bool, r_multiple, is_stopped=False, is_full_stop_loss=False, protection_activated=False):
        self.decision = {"session_date": session_date, "symbol": symbol}
        self.outcome = {
            "closed": closed, "r_multiple": r_multiple, "is_stopped": is_stopped,
            "is_full_stop_loss": is_full_stop_loss, "protection_activated": protection_activated,
        }


def test_max_drawdown_r_and_streak_from_rows():
    rows = [
        _FakeRow(session_date="2026-01-01", symbol="AAPL", closed=True, r_multiple=1.0),
        _FakeRow(session_date="2026-01-02", symbol="AAPL", closed=True, r_multiple=-1.0),
        _FakeRow(session_date="2026-01-03", symbol="AAPL", closed=True, r_multiple=-1.0),
        _FakeRow(session_date="2026-01-04", symbol="AAPL", closed=True, r_multiple=0.5),
    ]
    result = max_drawdown_r_and_streak_from_rows(rows)
    assert result["maximum_drawdown_r"] == pytest.approx(2.0)
    assert result["longest_losing_streak"] == 2


def test_full_stop_out_rate():
    rows = [
        _FakeRow(session_date="d1", symbol="A", closed=True, r_multiple=-1.0, is_stopped=True, is_full_stop_loss=True),
        _FakeRow(session_date="d2", symbol="A", closed=True, r_multiple=0.0, is_stopped=True, is_full_stop_loss=False),
        _FakeRow(session_date="d3", symbol="A", closed=False, r_multiple=None),
    ]
    assert full_stop_out_rate(rows) == pytest.approx(0.5)
    assert full_stop_out_rate([]) is None


def test_protection_diagnostics_paired_conversion_buckets():
    baseline = [
        _FakeRow(session_date="d1", symbol="A", closed=True, r_multiple=-1.0, is_stopped=True, is_full_stop_loss=True),
        _FakeRow(session_date="d2", symbol="B", closed=True, r_multiple=-1.0, is_stopped=True, is_full_stop_loss=True),
        _FakeRow(session_date="d3", symbol="C", closed=True, r_multiple=-1.0, is_stopped=True, is_full_stop_loss=True),
        _FakeRow(session_date="d4", symbol="D", closed=True, r_multiple=2.0, is_stopped=False),
    ]
    arm = [
        _FakeRow(session_date="d1", symbol="A", closed=True, r_multiple=0.0, is_stopped=True, is_full_stop_loss=False, protection_activated=True),
        _FakeRow(session_date="d2", symbol="B", closed=True, r_multiple=0.5, is_stopped=True, is_full_stop_loss=False, protection_activated=True),
        _FakeRow(session_date="d3", symbol="C", closed=True, r_multiple=-0.5, is_stopped=True, is_full_stop_loss=False, protection_activated=True),
        _FakeRow(session_date="d4", symbol="D", closed=True, r_multiple=1.0, is_stopped=True, protection_activated=True),
    ]
    diag = protection_diagnostics(baseline_rows=baseline, arm_rows=arm)
    assert diag["paired_trades"] == 4
    assert diag["activation_count"] == 4
    assert diag["baseline_full_losses"] == 3
    assert diag["full_losses_converted_to_breakeven"] == 1
    assert diag["full_losses_converted_to_small_win"] == 1
    assert diag["full_losses_converted_to_reduced_loss"] == 1
    assert diag["winners_clipped"] == 1
    assert diag["r_lost_from_clipped_winners"] == pytest.approx(1.0)
    assert diag["trades_improved"] == 3
    assert diag["trades_worsened"] == 1


def test_protection_diagnostics_rejects_misaligned_rows():
    baseline = [_FakeRow(session_date="d1", symbol="A", closed=True, r_multiple=-1.0, is_stopped=True, is_full_stop_loss=True)]
    arm = [_FakeRow(session_date="d1", symbol="B", closed=True, r_multiple=0.0, is_stopped=True)]
    with pytest.raises(ProfitProtectionExitError):
        protection_diagnostics(baseline_rows=baseline, arm_rows=arm)


# ---------------------------------------------------------------------------
# Full-pipeline integration: entry policy is genuinely unchanged, and
# BASELINE is byte-identical to M41's own (unmodified) implementation, all
# the way through the real SignalEngine/simulator pipeline -- not just at
# the light `simulate_exit_arm` unit level above.
# ---------------------------------------------------------------------------

def _outcome_key(row) -> tuple:
    o = row.outcome
    return (o["state"], o["entry_price"], o["exit_price"], o["realized_pnl"], o["r_multiple"])


def test_entry_policy_candidate_identity_matches_m41_exactly():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    assert evaluation.combo == CONFIG_ENTRY.combo

    m41_rows = build_all_arms_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        minute_bars_by_symbol=minute_bars, base_config=__import__("src.config", fromlist=["load_config"]).load_config(),
        implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )
    m43_rows = build_all_entry_and_exit_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=__import__("src.config", fromlist=["load_config"]).load_config(),
        implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )

    m41_current_identity = [(r.decision["symbol"], r.decision["session_date"]) for r in m41_rows[ARM_CURRENT_FIRST_VALID]]
    m43_current_identity = [(r.decision["symbol"], r.decision["session_date"]) for r in m43_rows[ENTRY_CURRENT_FIRST_VALID][ARM_BASELINE]]
    assert m41_current_identity == m43_current_identity

    m41_waited_identity = [(r.decision["symbol"], r.decision["session_date"]) for r in m41_rows[ARM_WAITED_FIRST_VALID_0950]]
    m43_waited_identity = [(r.decision["symbol"], r.decision["session_date"]) for r in m43_rows[ENTRY_WAITED_FIRST_VALID_0950][ARM_BASELINE]]
    assert m41_waited_identity == m43_waited_identity
    assert len(m43_current_identity) == 1  # sanity: the fixture produces exactly one candidate


def test_baseline_arm_is_byte_identical_to_m41_outcome_through_the_full_pipeline():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    config = __import__("src.config", fromlist=["load_config"]).load_config()
    created_at = datetime.now(timezone.utc)

    m41_rows = build_all_arms_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        minute_bars_by_symbol=minute_bars, base_config=config, implementation_digest="test-digest", created_at=created_at,
    )
    m43_rows = build_all_entry_and_exit_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=config, implementation_digest="test-digest", created_at=created_at,
    )

    m41_current_outcomes = [_outcome_key(r) for r in m41_rows[ARM_CURRENT_FIRST_VALID]]
    m43_current_outcomes = [_outcome_key(r) for r in m43_rows[ENTRY_CURRENT_FIRST_VALID][ARM_BASELINE]]
    assert m41_current_outcomes == m43_current_outcomes
    assert m43_current_outcomes[0][0] == "FILLED_OPEN"  # sanity: the fixture entered a real position

    m41_waited_outcomes = [_outcome_key(r) for r in m41_rows[ARM_WAITED_FIRST_VALID_0950]]
    m43_waited_outcomes = [_outcome_key(r) for r in m43_rows[ENTRY_WAITED_FIRST_VALID_0950][ARM_BASELINE]]
    assert m41_waited_outcomes == m43_waited_outcomes


def test_protection_arms_do_not_alter_entry_and_remain_paired_with_baseline():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    config = __import__("src.config", fromlist=["load_config"]).load_config()
    m43_rows = build_all_entry_and_exit_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=config, implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )
    for entry_policy in (ENTRY_CURRENT_FIRST_VALID, ENTRY_WAITED_FIRST_VALID_0950):
        baseline_rows = m43_rows[entry_policy][ARM_BASELINE]
        for arm in PROTECTION_ARMS:
            arm_rows = m43_rows[entry_policy][arm]
            identity = [(r.decision["symbol"], r.decision["session_date"]) for r in arm_rows]
            baseline_identity = [(r.decision["symbol"], r.decision["session_date"]) for r in baseline_rows]
            assert identity == baseline_identity
            diag = protection_diagnostics(baseline_rows=baseline_rows, arm_rows=arm_rows)
            assert diag["paired_trades"] <= len(baseline_rows)


# ---------------------------------------------------------------------------
# M43.5 (Issue #338): date-indexed bar retrieval for `_session_bars`.
# `index_market_bars_by_date` and `_session_bars`'s optional `index`
# parameter are a faster retrieval path only -- these tests prove the
# indexed path returns exactly what the original full-history rescan+sort
# returns, on the same inputs.
# ---------------------------------------------------------------------------


def _market_bar(symbol, ts, *, open_, high, low, close, volume):
    return MarketBar(
        symbol=symbol, exchange_session="REGULAR", trading_date=ts.date(), event_timestamp=ts,
        end_timestamp=ts + timedelta(minutes=1), receive_timestamp=ts + timedelta(seconds=1),
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=volume,
        source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None, quality=MarketDataQuality.VALID,
    )


def test_index_market_bars_by_date_buckets_by_date_and_sorts_by_event_timestamp():
    d1 = date(2026, 8, 10)
    d2 = date(2026, 8, 11)
    start1 = datetime(2026, 8, 10, 9, 30, tzinfo=EASTERN)
    start2 = datetime(2026, 8, 11, 9, 30, tzinfo=EASTERN)
    # Deliberately out of chronological order in the input list -- the index
    # must sort each date's bucket by event_timestamp regardless of input order.
    b1_late = _market_bar("AAPL", start1 + timedelta(minutes=1), open_="100", high="100.5", low="99.5", close="100.2", volume=1000)
    b1_early = _market_bar("AAPL", start1, open_="100", high="100.5", low="99.5", close="100.1", volume=1000)
    b2 = _market_bar("AAPL", start2, open_="101", high="101.5", low="100.5", close="101.2", volume=1000)
    bars = [b1_late, b1_early, b2]

    index = index_market_bars_by_date(bars)

    assert index.by_date[d1] == (b1_early, b1_late)  # sorted, not input order
    assert index.by_date[d2] == (b2,)
    assert index.by_date.get(date(2026, 8, 12), ()) == ()


def test_session_bars_index_parity_with_unindexed():
    d1 = date(2026, 8, 10)
    d2 = date(2026, 8, 11)
    start1 = datetime(2026, 8, 10, 9, 30, tzinfo=EASTERN)
    start2 = datetime(2026, 8, 11, 9, 30, tzinfo=EASTERN)
    b1a = _market_bar("AAPL", start1 + timedelta(minutes=2), open_="100", high="100.5", low="99.5", close="100.2", volume=1000)
    b1b = _market_bar("AAPL", start1, open_="100", high="100.5", low="99.5", close="100.1", volume=1000)
    b2 = _market_bar("AAPL", start2, open_="101", high="101.5", low="100.5", close="101.2", volume=1000)
    bars = [b1a, b1b, b2]
    index = index_market_bars_by_date(bars)

    for session_date in (d1, d2, date(2026, 8, 12)):
        reference = _session_bars(bars, session_date=session_date)
        indexed = _session_bars(bars, session_date=session_date, index=index)
        assert indexed == reference


def test_session_bars_missing_date_returns_empty_both_paths():
    bars = [_market_bar("AAPL", datetime(2026, 8, 10, 9, 30, tzinfo=EASTERN), open_="100", high="100.5", low="99.5", close="100.1", volume=1000)]
    index = index_market_bars_by_date(bars)
    missing = date(2026, 8, 25)

    assert _session_bars(bars, session_date=missing) == []
    assert _session_bars(bars, session_date=missing, index=index) == []


def _reference_build_all_entry_and_exit_rows_unindexed(
    *, period, evaluation, inputs_table, market_bars_by_symbol, base_config, implementation_digest, created_at,
):
    """Test-only reference: identical to `build_all_entry_and_exit_rows`
    except it never builds or passes `market_bars_index_by_symbol`, so
    `_session_bars` always takes its original full-history rescan+sort path
    (the still-supported `index=None` default). Not used at runtime; exists
    solely to prove the indexed production implementation never diverges
    from it -- the "most important integration test" per Issue #338."""
    if evaluation.combo != CONFIG_ENTRY.combo:
        raise ProfitProtectionExitError("requires the frozen GAP_050_RVOL_175 combo")
    combo_config = sensitivity_config(base_config, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"
    full_signals = evaluation.candidate_signals
    early_signals = early_pool(full_signals)
    current_winners = select_first_candidate_per_day(full_signals)
    early_winners = select_first_candidate_per_day(early_signals)
    waited_enriched = tuple(enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_winners)
    current_rows = _build_rows_for_current(
        current_winners, period=period, market_bars_by_symbol=market_bars_by_symbol, combo_config=combo_config,
        strategy_version=strategy_version, implementation_digest=implementation_digest, created_at=created_at,
    )
    waited_rows = _build_rows_for_waited(
        waited_enriched, period=period, market_bars_by_symbol=market_bars_by_symbol, combo_config=combo_config,
        strategy_version=strategy_version, implementation_digest=implementation_digest, created_at=created_at,
    )
    return {ENTRY_CURRENT_FIRST_VALID: current_rows, ENTRY_WAITED_FIRST_VALID_0950: waited_rows}


def test_build_all_entry_and_exit_rows_index_parity_baseline_and_be_after_050r():
    """M43's own BASELINE and BE_AFTER_050R outcomes, through the real
    SignalEngine/simulator/exit-arm pipeline, must be byte-identical whether
    `_session_bars` is served from the pre-built index (production, always
    on since Issue #338) or from the original full-history rescan (this
    test's reference)."""
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    config = __import__("src.config", fromlist=["load_config"]).load_config()
    created_at = datetime.now(timezone.utc)

    indexed = build_all_entry_and_exit_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=config, implementation_digest="test-digest", created_at=created_at,
    )
    reference = _reference_build_all_entry_and_exit_rows_unindexed(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=config, implementation_digest="test-digest", created_at=created_at,
    )

    for entry_policy in (ENTRY_CURRENT_FIRST_VALID, ENTRY_WAITED_FIRST_VALID_0950):
        for arm in EXIT_ARMS:
            indexed_outcomes = [_outcome_key(r) for r in indexed[entry_policy][arm]]
            reference_outcomes = [_outcome_key(r) for r in reference[entry_policy][arm]]
            assert indexed_outcomes == reference_outcomes, f"parity mismatch: {entry_policy}/{arm}"
    # Sanity: BASELINE actually entered a real position in this fixture (not
    # a table of trivially-identical empty results).
    assert indexed[ENTRY_CURRENT_FIRST_VALID][ARM_BASELINE][0].outcome["state"] == "FILLED_OPEN"


def test_build_all_entry_and_exit_rows_index_parity_deterministic_repeated_execution():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    config = __import__("src.config", fromlist=["load_config"]).load_config()
    created_at = datetime.now(timezone.utc)

    first = build_all_entry_and_exit_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=config, implementation_digest="test-digest", created_at=created_at,
    )
    second = build_all_entry_and_exit_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=config, implementation_digest="test-digest", created_at=created_at,
    )

    for entry_policy in (ENTRY_CURRENT_FIRST_VALID, ENTRY_WAITED_FIRST_VALID_0950):
        for arm in EXIT_ARMS:
            assert [_outcome_key(r) for r in first[entry_policy][arm]] == [_outcome_key(r) for r in second[entry_policy][arm]]
