"""Research tooling for the ORB universe-expansion study (Issue #298).

This module answers one question: does expanding the equity universe
materially increase the frequency of valid `orb_long_v0` setups, holding
every strategy rule fixed? It is research-only.

Reuse, not reinterpretation:
  * Gate order and thresholds come unmodified from `src.signal_engine.
    SignalEngine` (gap -> opening RVOL -> SPY/VWAP -> ORB breakout).
  * Opening-range, VWAP, and median-opening-volume math comes unmodified
    from `src.market_data`.
  * Historical bars come from the existing Alpaca market-data boundary
    (`src.market_data_provider.AlpacaMarketDataProvider`), the same one
    M15/M19 production code uses -- no parallel fetch path.

`evaluate_symbol_session` is the one addition this module makes on top of
that reuse: `src.live_shadow_session._evaluate` checks the strategy at a
single "now" instant (the live scanner's one snapshot per cycle). A
historical study instead needs, for each closed session, the first minute
(if any) within the configured entry window at which the unmodified
`SignalEngine.evaluate` would have approved -- so this function iterates
completed minutes in that window and stops at the first approval, exactly
mirroring what repeatedly invoking the live scanner every minute would
have found.

Safety: `strategy.enabled` is flipped to `True` only on an in-memory
`copy.deepcopy` of the loaded config, purely so `SignalEngine.evaluate`
does not short-circuit on the disabled flag -- the identical technique
`LiveScanner` (`src/live_scanner.py`) and `_evaluate`
(`src/live_shadow_session.py`) already use for read-only monitoring. The
tracked `config/strategy_v0.yaml` is never written by this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, replace as dataclass_replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable, Mapping, Protocol, Sequence

from src.config import load_config
from src.exchange_calendar import EASTERN, regular_session_bounds
from src.market_data import (
    MinuteBar,
    calculate_estimated_vwap,
    calculate_median_opening_volume,
    calculate_opening_range,
)
from src.market_data_canonical import (
    MarketBar, MarketDataFreshnessPolicy, MarketDataQuality, session_open_for,
    validate_market_bar, validate_market_data_batch,
)
from src.market_data_provider import (
    ALPACA_DEFAULT_BASE_URL, ALPACA_IEX_SOURCE, AlpacaCredentials, MarketDataProviderError,
    normalize_alpaca_bar,
)
from src.signal_engine import LiveData, OpeningData, SignalDecision, SignalEngine

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = REPOSITORY_ROOT / "artifacts" / "research-universe-expansion" / "bar-cache"
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "artifacts" / "research-universe-expansion" / "results"


class UniverseResearchError(RuntimeError):
    """Refused: research tooling could not safely proceed."""


# ---------------------------------------------------------------------------
# Universe construction.
#
# A static, reproducible, liquidity-tiered candidate pool. Methodology:
# mega/large-cap US common equities, each with a long trading history and
# (at the time this study was authored, 2026-08) average daily dollar volume
# comfortably in the multi-hundred-million to multi-billion dollar range --
# no penny stocks, no OTC names, no leveraged/inverse ETFs, no duplicate
# share classes (e.g. GOOG is omitted because GOOGL already represents that
# issuer). Ranked by descending approximate liquidity tier so that
# `UNIVERSE_POOL[:n]` is a deterministic, reproducible answer for every
# studied size `n`, and so each larger universe is a strict superset of
# every smaller one. Tier 1 is exactly `config/strategy_v0.yaml`'s current
# production universe.
# ---------------------------------------------------------------------------

UNIVERSE_POOL: tuple[str, ...] = (
    # Tier 1 (1-15): current production universe.
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "AMD",
    "JPM", "XOM", "LLY", "COST", "WMT", "NFLX",
    # Tier 2 (16-25): next highest-liquidity large caps.
    "V", "MA", "UNH", "HD", "PG", "JNJ", "ORCL", "CRM", "BAC", "KO",
    # Tier 3 (26-50).
    "PEP", "ABBV", "CVX", "MRK", "ADBE", "TMO", "CSCO", "ACN", "MCD", "ABT",
    "DIS", "WFC", "INTC", "IBM", "GE", "CAT", "PFE", "VZ", "TXN", "QCOM",
    "NKE", "PM", "UNP", "LOW", "AMGN",
    # Tier 4 (51-75).
    "HON", "COP", "UPS", "SBUX", "BA", "GS", "MS", "BLK", "SPGI", "PLD",
    "T", "RTX", "ELV", "MDT", "DE", "AXP", "GILD", "BKNG", "ADI", "LMT",
    "MDLZ", "SYK", "TJX", "CVS", "C",
    # Tier 5 (76-100).
    "SCHW", "REGN", "ZTS", "MO", "CB", "SO", "PGR", "DUK", "BSX", "VRTX",
    "EQIX", "ETN", "AMAT", "MMC", "PANW", "APD", "ITW", "SLB", "CL", "FI",
    "NOW", "BDX", "ADP", "CME", "ICE",
)

UNIVERSE_SIZES: tuple[int, ...] = (15, 25, 50, 75, 100)
SPY_SYMBOL = "SPY"


def production_universe() -> tuple[str, ...]:
    """The tracked, currently-configured universe (unmodified read)."""
    return tuple(load_config()["universe"]["symbols"])


def liquid_universe(size: int) -> tuple[str, ...]:
    """The first `size` symbols of the reproducible liquidity-tiered pool."""
    if not isinstance(size, int) or size < 1 or size > len(UNIVERSE_POOL):
        raise UniverseResearchError(
            f"universe size must be between 1 and {len(UNIVERSE_POOL)}"
        )
    return UNIVERSE_POOL[:size]


def incremental_symbols(*, smaller_size: int, larger_size: int) -> tuple[str, ...]:
    """Symbols present in `liquid_universe(larger_size)` but not the smaller one."""
    if smaller_size >= larger_size:
        raise UniverseResearchError("larger_size must exceed smaller_size")
    return liquid_universe(larger_size)[smaller_size:]


def all_pool_symbols_with_production() -> tuple[str, ...]:
    """Every symbol this study ever needs bars for: the full pool plus any
    production symbol not already in the pool (kept distinct on purpose --
    the production universe is operator-curated and may diverge from this
    static research pool)."""
    merged = list(UNIVERSE_POOL)
    for symbol in production_universe():
        if symbol not in merged:
            merged.append(symbol)
    return tuple(merged)


# ---------------------------------------------------------------------------
# Session calendar.
# ---------------------------------------------------------------------------


def trading_sessions_before(reference_day: date, *, count: int) -> tuple[date, ...]:
    """The `count` most recent supported trading sessions strictly before
    `reference_day`, oldest first."""
    if count < 1:
        raise UniverseResearchError("count must be positive")
    values: list[date] = []
    cursor = reference_day - timedelta(days=1)
    while len(values) < count:
        if regular_session_bounds(cursor) is not None:
            values.append(cursor)
        cursor -= timedelta(days=1)
        if reference_day - cursor > timedelta(days=3650):
            raise UniverseResearchError("exceeded supported calendar coverage while walking back")
    return tuple(reversed(values))


#: Research-only heuristic (see `historical_fetch_start`), not a change to
#: `calculate_median_opening_volume`'s own `lookback_days=20` requirement.
DEFAULT_RVOL_WARMUP_MULTIPLIER = 3


def historical_fetch_start(
    first_session_date: date, *, lookback_days: int,
    warmup_multiplier: int = DEFAULT_RVOL_WARMUP_MULTIPLIER,
) -> date:
    """The oldest date this study's historical fetch window should reach.

    `calculate_median_opening_volume` (`src/market_data.py`, unmodified)
    does not require the nearest `lookback_days` calendar sessions to each
    be complete -- it walks backward through every historical date it is
    GIVEN, skips any without a complete opening range, and stops once it
    has found `lookback_days` complete ones. Its accuracy is therefore
    bounded entirely by how far back the fetch window reaches, not by this
    module's own logic.

    Diagnosis against the operator's real IEX-fed cache found a persistent,
    well-under-100% per-session opening-range completion rate for many
    large-cap symbols (real feed sparsity, not a bug -- see
    `docs/research/ORB_UNIVERSE_EXPANSION_STUDY.md`), so fetching exactly
    `lookback_days` calendar sessions of buffer before the first study
    session does not give that walk-back enough candidates to reliably find
    `lookback_days` complete ones. Fetching `lookback_days *
    warmup_multiplier` sessions of buffer instead is a heuristic, not a
    guarantee: a symbol whose completion rate is low enough can still come
    up short, which is a genuine IEX-coverage limit no larger buffer alone
    can fix."""
    if lookback_days < 1:
        raise UniverseResearchError("lookback_days must be positive")
    if warmup_multiplier < 1:
        raise UniverseResearchError("warmup_multiplier must be positive")
    return trading_sessions_before(first_session_date, count=lookback_days * warmup_multiplier)[0]


# ---------------------------------------------------------------------------
# Canonical per-session-per-symbol evaluation.
# ---------------------------------------------------------------------------

_GAP_REASON = "Opening gap is below the required threshold."
_RVOL_REASON = "Opening relative volume is below the required threshold."
_SPY_VWAP_REASON = "SPY is not above its session VWAP."

CANDIDATE = "CANDIDATE"
GAP_ELIMINATED = "GAP_ELIMINATED"
RVOL_ELIMINATED = "RVOL_ELIMINATED"
SPY_VWAP_ELIMINATED = "SPY_VWAP_ELIMINATED"
BREAKOUT_ELIMINATED = "BREAKOUT_ELIMINATED"
DATA_INCOMPLETE = "DATA_INCOMPLETE"

_TERMINAL_STATES = frozenset({
    CANDIDATE, GAP_ELIMINATED, RVOL_ELIMINATED, SPY_VWAP_ELIMINATED,
    BREAKOUT_ELIMINATED, DATA_INCOMPLETE,
})

# Granular reasons for DATA_INCOMPLETE, in the order `evaluate_symbol_session`
# checks for them. Kept as an explicit, closed set (not a free-text string)
# so a caller can safely aggregate by reason without guessing at spelling.
MISSING_OPENING_RANGE_BARS = "MISSING_OPENING_RANGE_BARS"
INSUFFICIENT_RVOL_LOOKBACK_HISTORY = "INSUFFICIENT_RVOL_LOOKBACK_HISTORY"
MISSING_HISTORICAL_PRIOR_CLOSE = "MISSING_HISTORICAL_PRIOR_CLOSE"
MISSING_TODAY_BARS = "MISSING_TODAY_BARS"
MISSING_SPY_BARS_FOR_SESSION = "MISSING_SPY_BARS_FOR_SESSION"
MISSING_ENTRY_WINDOW_BARS = "MISSING_ENTRY_WINDOW_BARS"
NO_EVALUABLE_MINUTE_IN_WINDOW = "NO_EVALUABLE_MINUTE_IN_WINDOW"

DATA_INCOMPLETE_REASONS = frozenset({
    MISSING_OPENING_RANGE_BARS, INSUFFICIENT_RVOL_LOOKBACK_HISTORY,
    MISSING_HISTORICAL_PRIOR_CLOSE, MISSING_TODAY_BARS,
    MISSING_SPY_BARS_FOR_SESSION, MISSING_ENTRY_WINDOW_BARS,
    NO_EVALUABLE_MINUTE_IN_WINDOW,
})


@dataclass(frozen=True)
class SessionEvaluation:
    symbol: str
    session_date: date
    state: str
    gap_percent: Decimal | None
    relative_volume: Decimal | None
    approved_at: datetime | None
    incomplete_reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in _TERMINAL_STATES:
            raise UniverseResearchError("invalid session evaluation state")
        if self.state == CANDIDATE and self.approved_at is None:
            raise UniverseResearchError("a CANDIDATE evaluation must record an approval time")
        if self.state == DATA_INCOMPLETE:
            if self.incomplete_reason not in DATA_INCOMPLETE_REASONS:
                raise UniverseResearchError("a DATA_INCOMPLETE evaluation must record a known reason")
        elif self.incomplete_reason is not None:
            raise UniverseResearchError("incomplete_reason only applies to DATA_INCOMPLETE")


def evaluate_symbol_session(
    *,
    symbol: str,
    session_date: date,
    candidate_bars: Sequence[MinuteBar],
    spy_bars: Sequence[MinuteBar],
    config: Mapping[str, Any],
) -> SessionEvaluation:
    """Evaluate one symbol on one closed session using the unmodified
    canonical `orb_long_v0` gate order. `candidate_bars` must include the
    session's minute bars plus at least `relative_volume_lookback_days`
    prior real sessions (for the previous close and median opening volume);
    `spy_bars` need only cover the session itself."""
    opening_minutes = int(config["signal"]["opening_range_minutes"])
    lookback_days = int(config["signal"]["relative_volume_lookback_days"])
    session_open = session_open_for(session_date)

    try:
        opening_range = calculate_opening_range(
            candidate_bars, session_date=session_date,
            opening_range_minutes=opening_minutes, session_open=session_open,
        )
    except ValueError:
        return SessionEvaluation(
            symbol, session_date, DATA_INCOMPLETE, None, None, None,
            incomplete_reason=MISSING_OPENING_RANGE_BARS,
        )

    try:
        median_volume = calculate_median_opening_volume(
            candidate_bars, before_date=session_date, lookback_days=lookback_days,
            opening_range_minutes=opening_minutes, session_open_for=session_open_for,
        )
    except ValueError:
        return SessionEvaluation(
            symbol, session_date, DATA_INCOMPLETE, None, None, None,
            incomplete_reason=INSUFFICIENT_RVOL_LOOKBACK_HISTORY,
        )

    historical = [
        bar for bar in candidate_bars
        if bar.timestamp_et.date() < session_date and not bar.interpolated
    ]
    if not historical:
        return SessionEvaluation(
            symbol, session_date, DATA_INCOMPLETE, None, None, None,
            incomplete_reason=MISSING_HISTORICAL_PRIOR_CLOSE,
        )
    today_bars = sorted(
        (
            bar for bar in candidate_bars
            if bar.timestamp_et.date() == session_date and not bar.interpolated
        ),
        key=lambda bar: bar.begins_at,
    )
    if not today_bars:
        return SessionEvaluation(
            symbol, session_date, DATA_INCOMPLETE, None, None, None,
            incomplete_reason=MISSING_TODAY_BARS,
        )
    spy_today = sorted(
        (
            bar for bar in spy_bars
            if bar.timestamp_et.date() == session_date and not bar.interpolated
        ),
        key=lambda bar: bar.begins_at,
    )
    if not spy_today:
        return SessionEvaluation(
            symbol, session_date, DATA_INCOMPLETE, None, None, None,
            incomplete_reason=MISSING_SPY_BARS_FOR_SESSION,
        )
    previous_close = max(historical, key=lambda bar: bar.begins_at).close_price
    today_open = today_bars[0].open_price

    opening_data = OpeningData(
        symbol=symbol,
        previous_close=previous_close,
        today_open=today_open,
        opening_range_high=opening_range.high_price,
        opening_range_low=opening_range.low_price,
        opening_range_volume=opening_range.volume,
        median_opening_volume=median_volume,
    )

    evaluation_config = copy.deepcopy(dict(config))
    evaluation_config["strategy"]["enabled"] = True  # in-memory only; never persisted
    engine = SignalEngine(evaluation_config)

    entry_start = time.fromisoformat(config["signal"]["entry_start"])
    entry_end = time.fromisoformat(config["signal"]["entry_end"])
    # `SignalEngine.evaluate` checks the entry window against `live.timestamp`
    # (the instant the completed-bar decision is made, one minute after the
    # bar begins -- matching `_evaluate` in `src/live_shadow_session.py`),
    # not the bar's own start time. A bar beginning exactly at `entry_end`
    # completes one minute later and is therefore outside the window.
    window_bars = [
        bar for bar in today_bars
        if entry_start <= (bar.begins_at + timedelta(minutes=1)).astimezone(EASTERN).time() <= entry_end
    ]
    if not window_bars:
        return SessionEvaluation(
            symbol, session_date, DATA_INCOMPLETE, None, None, None,
            incomplete_reason=MISSING_ENTRY_WINDOW_BARS,
        )

    last_decision: SignalDecision | None = None
    for bar in window_bars:
        spy_up_to_now = [b for b in spy_today if b.begins_at <= bar.begins_at]
        if not spy_up_to_now:
            continue
        try:
            spy_vwap = calculate_estimated_vwap(spy_up_to_now)
        except ValueError:
            continue
        live_data = LiveData(
            timestamp=bar.begins_at + timedelta(minutes=1),
            completed_bar_close=bar.close_price,
            spy_price=spy_up_to_now[-1].close_price,
            spy_vwap=spy_vwap,
        )
        decision = engine.evaluate(opening_data, live_data)
        last_decision = decision
        if decision.approved:
            return SessionEvaluation(
                symbol, session_date, CANDIDATE, decision.gap_percent,
                decision.relative_volume, bar.begins_at,
            )
        if decision.reason == _GAP_REASON:
            return SessionEvaluation(
                symbol, session_date, GAP_ELIMINATED, decision.gap_percent, None, None,
            )
        if decision.reason == _RVOL_REASON:
            return SessionEvaluation(
                symbol, session_date, RVOL_ELIMINATED,
                decision.gap_percent, decision.relative_volume, None,
            )

    if last_decision is None:
        return SessionEvaluation(
            symbol, session_date, DATA_INCOMPLETE, None, None, None,
            incomplete_reason=NO_EVALUABLE_MINUTE_IN_WINDOW,
        )
    if last_decision.reason == _SPY_VWAP_REASON:
        return SessionEvaluation(
            symbol, session_date, SPY_VWAP_ELIMINATED,
            last_decision.gap_percent, last_decision.relative_volume, None,
        )
    return SessionEvaluation(
        symbol, session_date, BREAKOUT_ELIMINATED,
        last_decision.gap_percent, last_decision.relative_volume, None,
    )


# ---------------------------------------------------------------------------
# Report construction.
# ---------------------------------------------------------------------------


#: Minimum fraction of possible symbol-days (`universe_size * sessions`)
#: that must reach a determinate (non-DATA_INCOMPLETE) evaluation before a
#: universe size's candidate-day rate may be treated as decision-quality.
#: Chosen per the operator's explicit direction on this study, not derived.
MINIMUM_COVERAGE = 0.95
VALID_COVERAGE = "VALID"
INVALID_INSUFFICIENT_COVERAGE = "INVALID_INSUFFICIENT_COVERAGE"


@dataclass(frozen=True)
class UniverseSizeReport:
    label: str
    universe: tuple[str, ...]
    sessions: int
    sessions_with_candidate: int
    zero_candidate_sessions: int
    total_candidates: int
    mean_candidates_per_session: float
    median_candidates_on_candidate_days: float
    candidate_day_rate: float
    evaluated_count: int
    gap_survivors: int
    rvol_survivors: int
    spy_vwap_survivors: int
    breakout_survivors: int
    data_incomplete_count: int
    data_incomplete_by_reason: Mapping[str, int]
    data_incomplete_by_symbol: Mapping[str, int]
    data_incomplete_by_session: Mapping[date, int]
    top_candidate_symbols: tuple[tuple[str, int], ...]

    @property
    def possible_symbol_days(self) -> int:
        return len(self.universe) * self.sessions

    @property
    def completeness(self) -> float:
        possible = self.possible_symbol_days
        return (self.evaluated_count / possible) if possible else 0.0

    @property
    def coverage_status(self) -> str:
        return VALID_COVERAGE if self.completeness >= MINIMUM_COVERAGE else INVALID_INSUFFICIENT_COVERAGE

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "universe_size": len(self.universe),
            "universe": list(self.universe),
            "sessions": self.sessions,
            "coverage": {
                "possible_symbol_days": self.possible_symbol_days,
                "complete_symbol_days": self.evaluated_count,
                "data_incomplete_symbol_days": self.data_incomplete_count,
                "completeness": round(self.completeness, 4),
                "minimum_required": MINIMUM_COVERAGE,
                "status": self.coverage_status,
                "data_incomplete_by_reason": dict(sorted(self.data_incomplete_by_reason.items())),
                "data_incomplete_by_symbol": dict(
                    sorted(self.data_incomplete_by_symbol.items(), key=lambda item: (-item[1], item[0]))
                ),
                "data_incomplete_by_session": {
                    day.isoformat(): count
                    for day, count in sorted(self.data_incomplete_by_session.items())
                },
            },
            # Candidate-rate figures below are informational whenever
            # coverage_status is INVALID_INSUFFICIENT_COVERAGE -- they are
            # computed only from the symbol-days that WERE evaluable and do
            # not represent the full studied universe/session set.
            "sessions_with_candidate": self.sessions_with_candidate,
            "zero_candidate_sessions": self.zero_candidate_sessions,
            "candidate_day_rate": round(self.candidate_day_rate, 4),
            "total_candidates": self.total_candidates,
            "mean_candidates_per_session": round(self.mean_candidates_per_session, 4),
            "median_candidates_on_candidate_days": self.median_candidates_on_candidate_days,
            "funnel": {
                "symbols_evaluated": self.evaluated_count,
                "gap_survivors": self.gap_survivors,
                "opening_rvol_survivors": self.rvol_survivors,
                "spy_vwap_survivors": self.spy_vwap_survivors,
                "orb_breakout_survivors": self.breakout_survivors,
                "data_incomplete": self.data_incomplete_count,
            },
            "top_candidate_symbols": [
                {"symbol": symbol, "candidate_days": count}
                for symbol, count in self.top_candidate_symbols
            ],
        }


def _report_for_universe(
    *, label: str, universe: Sequence[str], session_dates: Sequence[date],
    evaluations: Mapping[tuple[str, date], SessionEvaluation],
) -> UniverseSizeReport:
    per_session_counts: list[int] = []
    total_candidates = 0
    gap_survivors = rvol_survivors = spy_survivors = breakout_survivors = 0
    evaluated_count = data_incomplete_count = 0
    candidate_days_by_symbol: dict[str, int] = {symbol: 0 for symbol in universe}
    incomplete_by_reason: dict[str, int] = {}
    incomplete_by_symbol: dict[str, int] = {}
    incomplete_by_session: dict[date, int] = {}

    for session_date in session_dates:
        session_candidates = 0
        for symbol in universe:
            evaluation = evaluations.get((symbol, session_date))
            if evaluation is None or evaluation.state == DATA_INCOMPLETE:
                data_incomplete_count += 1
                reason = evaluation.incomplete_reason if evaluation is not None else "MISSING_EVALUATION"
                incomplete_by_reason[reason] = incomplete_by_reason.get(reason, 0) + 1
                incomplete_by_symbol[symbol] = incomplete_by_symbol.get(symbol, 0) + 1
                incomplete_by_session[session_date] = incomplete_by_session.get(session_date, 0) + 1
                continue
            evaluated_count += 1
            # Each `*_survivors` counter must only advance for symbol-days
            # that actually cleared that specific gate -- i.e. the increment
            # happens strictly AFTER the corresponding elimination check, not
            # before it. (A prior version incremented every counter before
            # checking that gate's own elimination state, which meant e.g.
            # `gap_survivors` always equalled `symbols_evaluated` and
            # `breakout_survivors` counted BREAKOUT_ELIMINATED symbol-days as
            # if they had "survived" the breakout gate -- the exact bug this
            # ordering fixes. See tests/test_orb_universe_expansion_research.
            # py::test_breakout_survivors_never_exceeds_total_candidates.)
            if evaluation.state == GAP_ELIMINATED:
                continue
            gap_survivors += 1
            if evaluation.state == RVOL_ELIMINATED:
                continue
            rvol_survivors += 1
            if evaluation.state == SPY_VWAP_ELIMINATED:
                continue
            spy_survivors += 1
            if evaluation.state == BREAKOUT_ELIMINATED:
                continue
            breakout_survivors += 1
            # Only CANDIDATE can remain once a symbol-day has cleared every
            # gate including breakout.
            session_candidates += 1
            total_candidates += 1
            candidate_days_by_symbol[symbol] += 1
        per_session_counts.append(session_candidates)

    sessions = len(session_dates)
    sessions_with_candidate = sum(1 for count in per_session_counts if count > 0)
    candidate_day_counts = [count for count in per_session_counts if count > 0]
    top_symbols = tuple(
        sorted(
            ((symbol, count) for symbol, count in candidate_days_by_symbol.items() if count > 0),
            key=lambda item: (-item[1], item[0]),
        )[:10]
    )

    return UniverseSizeReport(
        label=label,
        universe=tuple(universe),
        sessions=sessions,
        sessions_with_candidate=sessions_with_candidate,
        zero_candidate_sessions=sessions - sessions_with_candidate,
        total_candidates=total_candidates,
        mean_candidates_per_session=mean(per_session_counts) if per_session_counts else 0.0,
        median_candidates_on_candidate_days=(
            float(median(candidate_day_counts)) if candidate_day_counts else 0.0
        ),
        candidate_day_rate=(sessions_with_candidate / sessions) if sessions else 0.0,
        evaluated_count=evaluated_count,
        gap_survivors=gap_survivors,
        rvol_survivors=rvol_survivors,
        spy_vwap_survivors=spy_survivors,
        breakout_survivors=breakout_survivors,
        data_incomplete_count=data_incomplete_count,
        data_incomplete_by_reason=incomplete_by_reason,
        data_incomplete_by_symbol=incomplete_by_symbol,
        data_incomplete_by_session=incomplete_by_session,
        top_candidate_symbols=top_symbols,
    )


def build_study_report(
    *,
    session_dates: Sequence[date],
    evaluations: Mapping[tuple[str, date], SessionEvaluation],
    universe_sizes: Sequence[int] = UNIVERSE_SIZES,
    include_production: bool = True,
    named_universes: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Build the full structured report from a completed set of per
    symbol/session evaluations. Pure and deterministic -- no I/O, no
    randomness -- so it can be exercised in tests without network access.

    `named_universes` lets a caller (e.g. the liquidity-qualified tiers)
    fold an arbitrary label -> symbol-list mapping into the same report
    structure -- e.g. `{"liquidity_top_25": (...)}` -- without hand-rolling
    a second `UniverseSizeReport` assembly path."""
    reports: list[UniverseSizeReport] = []
    if include_production:
        reports.append(
            _report_for_universe(
                label="current_production",
                universe=production_universe(),
                session_dates=session_dates,
                evaluations=evaluations,
            )
        )
    for size in universe_sizes:
        reports.append(
            _report_for_universe(
                label=f"top_{size}",
                universe=liquid_universe(size),
                session_dates=session_dates,
                evaluations=evaluations,
            )
        )
    for label, universe in (named_universes or {}).items():
        reports.append(
            _report_for_universe(
                label=label, universe=universe, session_dates=session_dates, evaluations=evaluations,
            )
        )

    return {
        "schema_version": "orb-universe-expansion-research-v1",
        "session_count": len(session_dates),
        "session_dates": [day.isoformat() for day in session_dates],
        "universe_reports": [report.as_dict() for report in reports],
    }


def csv_summary_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten `build_study_report`'s output into one row per universe size,
    suitable for `csv.DictWriter`."""
    rows: list[dict[str, Any]] = []
    for universe_report in report["universe_reports"]:
        funnel = universe_report["funnel"]
        coverage = universe_report["coverage"]
        rows.append({
            "label": universe_report["label"],
            "universe_size": universe_report["universe_size"],
            "sessions": universe_report["sessions"],
            "possible_symbol_days": coverage["possible_symbol_days"],
            "complete_symbol_days": coverage["complete_symbol_days"],
            "completeness": coverage["completeness"],
            "coverage_status": coverage["status"],
            "sessions_with_candidate": universe_report["sessions_with_candidate"],
            "zero_candidate_sessions": universe_report["zero_candidate_sessions"],
            "candidate_day_rate": universe_report["candidate_day_rate"],
            "total_candidates": universe_report["total_candidates"],
            "mean_candidates_per_session": universe_report["mean_candidates_per_session"],
            "median_candidates_on_candidate_days": universe_report["median_candidates_on_candidate_days"],
            "symbols_evaluated": funnel["symbols_evaluated"],
            "gap_survivors": funnel["gap_survivors"],
            "opening_rvol_survivors": funnel["opening_rvol_survivors"],
            "spy_vwap_survivors": funnel["spy_vwap_survivors"],
            "orb_breakout_survivors": funnel["orb_breakout_survivors"],
            "data_incomplete": funnel["data_incomplete"],
        })
    return rows


# ---------------------------------------------------------------------------
# Liquidity qualification.
#
# The naive fixed-size universes (15/25/50/75/100, a straight prefix of
# UNIVERSE_POOL) expand into increasingly less-liquid names with no
# data-quality floor -- exactly what produced the IEX-sparsity-dominated
# incompleteness diagnosed above. This section builds a research universe
# from objective, historical-data-derived liquidity/data-quality measures
# instead of the pool's reputation-based ordering, so a "tier" is a claim
# about *measured* opening-range reliability, not an assumption about which
# large-cap names are liquid enough.
# ---------------------------------------------------------------------------

BELOW_MINIMUM_PRICE = "BELOW_MINIMUM_PRICE"
BELOW_MINIMUM_DOLLAR_VOLUME = "BELOW_MINIMUM_DOLLAR_VOLUME"
BELOW_MINIMUM_SHARE_VOLUME = "BELOW_MINIMUM_SHARE_VOLUME"
INSUFFICIENT_OPENING_RANGE_COVERAGE = "INSUFFICIENT_OPENING_RANGE_COVERAGE"
INSUFFICIENT_RVOL_HISTORY_COVERAGE = "INSUFFICIENT_RVOL_HISTORY_COVERAGE"
NO_HISTORICAL_DATA = "NO_HISTORICAL_DATA"


@dataclass(frozen=True)
class LiquidityThresholds:
    """Explicit, evidence-based qualification floor. The two opening-window
    thresholds are weighted heaviest on purpose (a 5-minute ORB strategy
    lives or dies on 09:30-09:35 coverage) and are pinned to the same
    `MINIMUM_COVERAGE` (95%) the study-level fail-closed gate already uses,
    so a "qualified" symbol is, by construction, one whose own data quality
    could support that gate rather than an independently-chosen number. The
    price/dollar-volume/share-volume floors are conservative sanity guards
    against a penny-stock or illiquid name slipping into a research pool
    that is otherwise curated to mega/large caps -- they are not expected to
    bind for most `UNIVERSE_POOL` symbols and are not the primary
    discriminator (see `docs/research/ORB_UNIVERSE_EXPANSION_STUDY.md`)."""

    min_median_price: Decimal = Decimal("5")
    min_avg_daily_dollar_volume: Decimal = Decimal("1000000")
    min_avg_daily_share_volume: Decimal = Decimal("10000")
    min_session_opening_range_complete_pct: float = MINIMUM_COVERAGE
    min_rvol_history_satisfiable_pct: float = MINIMUM_COVERAGE

    def __post_init__(self) -> None:
        if self.min_median_price <= 0:
            raise UniverseResearchError("min_median_price must be positive")
        if self.min_avg_daily_dollar_volume <= 0:
            raise UniverseResearchError("min_avg_daily_dollar_volume must be positive")
        if self.min_avg_daily_share_volume <= 0:
            raise UniverseResearchError("min_avg_daily_share_volume must be positive")
        if not (0 < self.min_session_opening_range_complete_pct <= 1):
            raise UniverseResearchError("min_session_opening_range_complete_pct must be in (0, 1]")
        if not (0 < self.min_rvol_history_satisfiable_pct <= 1):
            raise UniverseResearchError("min_rvol_history_satisfiable_pct must be in (0, 1]")


DEFAULT_LIQUIDITY_THRESHOLDS = LiquidityThresholds()


@dataclass(frozen=True)
class SymbolLiquidityProfile:
    symbol: str
    trading_days_observed: int
    avg_daily_dollar_volume: Decimal
    avg_daily_share_volume: Decimal
    median_price: Decimal
    opening_window_minute_coverage_pct: float
    session_opening_range_complete_pct: float
    rvol_history_satisfiable_pct: float
    eligible: bool
    exclusion_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.eligible and self.exclusion_reasons:
            raise UniverseResearchError("an eligible profile cannot carry exclusion reasons")
        if not self.eligible and not self.exclusion_reasons:
            raise UniverseResearchError("an ineligible profile must record at least one reason")

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "trading_days_observed": self.trading_days_observed,
            "avg_daily_dollar_volume": str(self.avg_daily_dollar_volume.quantize(Decimal("1"))),
            "avg_daily_share_volume": str(self.avg_daily_share_volume.quantize(Decimal("1"))),
            "median_price": str(self.median_price),
            "opening_window_minute_coverage_pct": round(self.opening_window_minute_coverage_pct, 4),
            "session_opening_range_complete_pct": round(self.session_opening_range_complete_pct, 4),
            "rvol_history_satisfiable_pct": round(self.rvol_history_satisfiable_pct, 4),
            "eligible": self.eligible,
            "exclusion_reasons": list(self.exclusion_reasons),
        }


def compute_symbol_liquidity_profile(
    *,
    symbol: str,
    bars: Sequence[MinuteBar],
    session_dates: Sequence[date],
    lookback_days: int,
    opening_range_minutes: int = 5,
    thresholds: LiquidityThresholds = DEFAULT_LIQUIDITY_THRESHOLDS,
) -> SymbolLiquidityProfile:
    """Derive one symbol's objective liquidity/data-quality profile
    directly from its real historical bars. Pure and deterministic given
    its inputs -- no I/O, no network, no randomness -- so it is fully
    unit-testable against synthetic fixtures.

    Runs in O(bars + sessions), not O(sessions x bars): bars are bucketed by
    date once, and each date's opening-range completeness is determined by
    calling the unmodified `calculate_opening_range` on just that date's own
    bucket (a few hundred bars) rather than re-scanning the symbol's entire
    multi-month history on every one of the 90 study sessions. Running this
    per-symbol scan across a 100-symbol candidate pool the slow way (calling
    `evaluate_symbol_session`, and therefore rescanning the whole bar list,
    90 times per symbol just to RANK candidates before any study has even
    selected them) is what made the first version of this qualification
    pass impractically slow; only the symbols that make it into a final
    tier need the full `evaluate_symbol_session` pipeline.

    `rvol_history_satisfiable_pct` mirrors -- without recomputing the
    median volume itself -- exactly the precondition `calculate_
    median_opening_volume` (`src/market_data.py`, unmodified) already
    enforces: walk backward through dates with a complete opening range
    and stop once `lookback_days` have been found."""
    by_date: dict[date, list[MinuteBar]] = {}
    for bar in bars:
        if bar.interpolated:
            continue
        by_date.setdefault(bar.begins_at.astimezone(EASTERN).date(), []).append(bar)

    trading_days_observed = len(by_date)
    if trading_days_observed == 0:
        return SymbolLiquidityProfile(
            symbol=symbol, trading_days_observed=0,
            avg_daily_dollar_volume=Decimal("0"), avg_daily_share_volume=Decimal("0"),
            median_price=Decimal("0"), opening_window_minute_coverage_pct=0.0,
            session_opening_range_complete_pct=0.0, rvol_history_satisfiable_pct=0.0,
            eligible=False, exclusion_reasons=(NO_HISTORICAL_DATA,),
        )

    daily_dollar_volumes: list[Decimal] = []
    daily_share_volumes: list[Decimal] = []
    all_closes: list[Decimal] = []
    opening_minutes_present = 0
    opening_range_complete: dict[date, bool] = {}
    for day, day_bars in by_date.items():
        daily_dollar_volumes.append(sum((bar.volume * bar.close_price for bar in day_bars), Decimal("0")))
        daily_share_volumes.append(Decimal(sum(bar.volume for bar in day_bars)))
        all_closes.extend(bar.close_price for bar in day_bars)
        opening_minutes_present += sum(
            1 for bar in day_bars
            if time(9, 30) <= bar.begins_at.astimezone(EASTERN).time() < time(9, 35)
        )
        try:
            calculate_opening_range(
                day_bars, session_date=day, opening_range_minutes=opening_range_minutes,
                session_open=session_open_for(day),
            )
            opening_range_complete[day] = True
        except ValueError:
            opening_range_complete[day] = False

    avg_daily_dollar_volume = sum(daily_dollar_volumes) / len(daily_dollar_volumes)
    avg_daily_share_volume = sum(daily_share_volumes) / len(daily_share_volumes)
    median_price = Decimal(str(median(all_closes)))
    opening_window_minute_coverage_pct = opening_minutes_present / (trading_days_observed * 5)

    sorted_dates_desc = sorted(by_date, reverse=True)

    def _rvol_satisfiable(before: date) -> bool:
        found = 0
        for day in sorted_dates_desc:
            if day >= before:
                continue
            if opening_range_complete.get(day, False):
                found += 1
                if found >= lookback_days:
                    return True
        return False

    session_opening_range_ok = [day for day in session_dates if opening_range_complete.get(day, False)]
    session_opening_range_complete_pct = (
        len(session_opening_range_ok) / len(session_dates) if session_dates else 0.0
    )
    rvol_ok_count = sum(1 for day in session_opening_range_ok if _rvol_satisfiable(day))
    rvol_history_satisfiable_pct = (
        (rvol_ok_count / len(session_opening_range_ok)) if session_opening_range_ok else 0.0
    )

    exclusion_reasons: list[str] = []
    if median_price < thresholds.min_median_price:
        exclusion_reasons.append(BELOW_MINIMUM_PRICE)
    if avg_daily_dollar_volume < thresholds.min_avg_daily_dollar_volume:
        exclusion_reasons.append(BELOW_MINIMUM_DOLLAR_VOLUME)
    if avg_daily_share_volume < thresholds.min_avg_daily_share_volume:
        exclusion_reasons.append(BELOW_MINIMUM_SHARE_VOLUME)
    if session_opening_range_complete_pct < thresholds.min_session_opening_range_complete_pct:
        exclusion_reasons.append(INSUFFICIENT_OPENING_RANGE_COVERAGE)
    if rvol_history_satisfiable_pct < thresholds.min_rvol_history_satisfiable_pct:
        exclusion_reasons.append(INSUFFICIENT_RVOL_HISTORY_COVERAGE)

    return SymbolLiquidityProfile(
        symbol=symbol, trading_days_observed=trading_days_observed,
        avg_daily_dollar_volume=avg_daily_dollar_volume, avg_daily_share_volume=avg_daily_share_volume,
        median_price=median_price, opening_window_minute_coverage_pct=opening_window_minute_coverage_pct,
        session_opening_range_complete_pct=session_opening_range_complete_pct,
        rvol_history_satisfiable_pct=rvol_history_satisfiable_pct,
        eligible=not exclusion_reasons, exclusion_reasons=tuple(exclusion_reasons),
    )


def rank_liquidity_qualified_symbols(
    profiles: Mapping[str, SymbolLiquidityProfile],
) -> tuple[str, ...]:
    """Every eligible symbol, ranked descending by `avg_daily_dollar_volume`
    -- the standard objective liquidity ranking measure -- with ties broken
    alphabetically by symbol for a fully deterministic, reproducible order."""
    eligible = [profile for profile in profiles.values() if profile.eligible]
    ranked = sorted(eligible, key=lambda profile: (-profile.avg_daily_dollar_volume, profile.symbol))
    return tuple(profile.symbol for profile in ranked)


@dataclass(frozen=True)
class LiquidityTier:
    label: str
    requested_size: int
    symbols: tuple[str, ...]

    @property
    def achieved_size(self) -> int:
        return len(self.symbols)

    @property
    def size_shortfall(self) -> bool:
        """True when fewer symbols qualified than requested -- this tier was
        NOT padded to `requested_size` with a lower-quality symbol; report
        the shortfall rather than hide it."""
        return self.achieved_size < self.requested_size

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "requested_size": self.requested_size,
            "achieved_size": self.achieved_size,
            "size_shortfall": self.size_shortfall,
            "symbols": list(self.symbols),
        }


def build_liquidity_tiers(ranked_symbols: Sequence[str], sizes: Sequence[int]) -> tuple[LiquidityTier, ...]:
    """Nested tiers over the same ranked, qualified list -- `sizes` must be
    ascending, so `build_liquidity_tiers(..., (25, 40, 50))[1].symbols` is
    guaranteed to be a superset of `[0].symbols` (both are prefixes of the
    same ordering). Never pads a short tier: if fewer than `size` symbols
    qualified, the tier's `symbols` is the full qualified list and
    `size_shortfall` is `True`."""
    if list(sizes) != sorted(sizes):
        raise UniverseResearchError("sizes must be strictly ascending")
    if len(set(sizes)) != len(sizes):
        raise UniverseResearchError("sizes must not repeat")
    tiers = []
    for size in sizes:
        if size < 1:
            raise UniverseResearchError("tier size must be positive")
        tiers.append(LiquidityTier(
            label=f"liquidity_top_{size}", requested_size=size,
            symbols=tuple(ranked_symbols[:size]),
        ))
    return tuple(tiers)


def cache_key(symbol: str, start: datetime, end: datetime) -> str:
    material = f"{symbol}|{start.isoformat()}|{end.isoformat()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


class _HttpResponseLike(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _HttpClientLike(Protocol):
    async def get(
        self, url: str, *, headers: Mapping[str, str], params: Mapping[str, Any],
    ) -> _HttpResponseLike: ...


@dataclass(frozen=True)
class _CachedResponse:
    status_code: int
    _payload: Any

    def json(self) -> Any:
        return self._payload


class CachingHttpClient:
    """Wraps an injected HTTP client with a gitignored on-disk cache keyed
    by request URL + params. A closed trading session's historical bars
    never change, so a cache hit is reused indefinitely -- this is what
    keeps a repeat run of the study cheap and avoids re-requesting the same
    symbol/date range across universe sizes that overlap (every larger
    studied universe is a superset of the smaller ones, so overlap is the
    common case)."""

    def __init__(self, inner: _HttpClientLike, *, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
        self._inner = inner
        self._cache_dir = cache_dir

    def _cache_path(self, url: str, params: Mapping[str, Any]) -> Path:
        material = json.dumps({"url": url, "params": params}, sort_keys=True, default=str)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]
        return self._cache_dir / f"{digest}.json"

    async def get(
        self, url: str, *, headers: Mapping[str, str], params: Mapping[str, Any],
    ) -> _HttpResponseLike:
        path = self._cache_path(url, params)
        if path.exists():
            return _CachedResponse(200, json.loads(path.read_text(encoding="utf-8")))
        response = await self._inner.get(url, headers=headers, params=params)
        if response.status_code == 200:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(response.json()), encoding="utf-8")
        return response


# ---------------------------------------------------------------------------
# Historical retrieval: pagination + quality filtering.
#
# `AlpacaMarketDataProvider.fetch_minute_bars` (src/market_data_provider.py)
# deliberately returns only ONE page (Alpaca caps a page at 10,000 bars) and
# does not read `next_page_token` at all -- correct for its only current
# production caller, which requests at most a single trading day of history,
# far short of that cap. A 90+ session research window needs roughly
# 30,000-45,000 minute bars per symbol, 3-4x the page cap, so a single-page
# fetch there silently returns only the OLDEST ~26 trading days of the
# requested range and drops the rest. This was the confirmed root cause of
# the DATA_INCOMPLETE dominance in the first real run of this study: 99 of
# 101 cached Alpaca responses carried a non-null `next_page_token` (i.e.
# were truncated at exactly 10,000 bars), so most (symbol, session) pairs
# fell outside the ~26 trading days actually retrieved. This module follows
# that cursor to completion itself rather than changing the production
# provider's single-page contract.
# ---------------------------------------------------------------------------

#: Fail-closed guard against a runaway pagination loop (a malformed or
#: adversarial `next_page_token` cycle) -- 50 pages is roughly 500,000 bars,
#: far more than any symbol in this study's date range can produce.
MAX_PAGES_PER_SYMBOL = 50

#: Historical bars in this study can be many months old; the shared default
#: freshness policy (5 minutes, meant for a live snapshot) would mark all of
#: them STALE. 400 days comfortably covers any date range this study uses
#: while still catching a genuinely corrupt/out-of-range timestamp.
RESEARCH_FRESHNESS_POLICY = MarketDataFreshnessPolicy(
    version="orb-universe-expansion-research-v1", max_age_seconds=400 * 86400.0,
)


#: Feed values Alpaca's Market Data API v2 accepts for US equities. `iex`
#: (this module's and the production provider's default) is a single-venue
#: subset of consolidated volume; `sip` is the full consolidated tape and
#: requires a paid Alpaca market-data subscription entitlement. Exposed as a
#: RESEARCH-ONLY parameter (see `--feed` on `scripts/
#: research_orb_universe_expansion.py`) to let an operator compare
#: opening-range coverage between the two -- this never changes
#: `src/market_data_provider.py`'s production default, which stays `iex`.
SUPPORTED_FEEDS = frozenset({"iex", "sip"})
DEFAULT_FEED = "iex"


async def fetch_full_minute_history(
    http_client: _HttpClientLike, *, credentials: AlpacaCredentials, symbol: str,
    start: datetime, end: datetime, now: Callable[[], datetime],
    base_url: str = ALPACA_DEFAULT_BASE_URL, feed: str = DEFAULT_FEED,
    on_page: Callable[[int, int], None] | None = None,
) -> list[MarketBar]:
    """Fetch every minute bar in `[start, end)` for `symbol`, following
    Alpaca's `next_page_token` cursor until it is exhausted rather than
    stopping after the first page. `on_page`, if given, is called after each
    page as `on_page(page_number, bars_fetched_so_far)` for CLI progress
    output. Raises `UniverseResearchError` if the pagination guard is
    exceeded, and `MarketDataProviderError` for any transport/shape failure
    -- the same exception types the single-page production provider raises,
    so callers do not need a third error type to handle."""
    normalized_symbol = symbol.upper().strip()
    if not normalized_symbol:
        raise UniverseResearchError("a symbol is required")
    if start.tzinfo is None or end.tzinfo is None:
        raise UniverseResearchError("start and end must be timezone-aware")
    if start >= end:
        raise UniverseResearchError("start must be earlier than end")
    if feed not in SUPPORTED_FEEDS:
        raise UniverseResearchError(f"feed must be one of {sorted(SUPPORTED_FEEDS)}")

    all_bars: list[MarketBar] = []
    page_token: str | None = None
    receive_timestamp = now()
    for page_number in range(1, MAX_PAGES_PER_SYMBOL + 1):
        params: dict[str, Any] = {
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timeframe": "1Min",
            "feed": feed,
            "limit": 10000,
            "adjustment": "split",
        }
        if page_token is not None:
            params["page_token"] = page_token
        try:
            response = await http_client.get(
                f"{base_url}/stocks/{normalized_symbol}/bars",
                headers=credentials.headers(), params=params,
            )
        except Exception as exc:
            raise MarketDataProviderError("provider request failed") from exc
        if response.status_code != 200:
            raise MarketDataProviderError(
                f"provider returned a non-success status ({response.status_code})"
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise MarketDataProviderError("provider response was not valid JSON") from exc
        if not isinstance(payload, Mapping) or "bars" not in payload:
            raise MarketDataProviderError("provider response is missing 'bars'")
        raw_bars = payload["bars"] or []
        if not isinstance(raw_bars, list):
            raise MarketDataProviderError("provider 'bars' field is not a list")
        page_bars = (
            normalize_alpaca_bar(raw_bar, symbol=normalized_symbol, receive_timestamp=receive_timestamp)
            for raw_bar in raw_bars if raw_bar is not None
        )
        if feed != "iex":
            # `normalize_alpaca_bar` (src/market_data_provider.py, unmodified)
            # always tags a bar with the fixed `ALPACA_IEX_SOURCE` identity,
            # correct for its own iex-only production caller. Retagging here
            # keeps a SIP comparison's provenance honest without touching
            # that production function.
            source_identity = dataclass_replace(ALPACA_IEX_SOURCE, feed=feed)
            page_bars = (dataclass_replace(bar, source=source_identity) for bar in page_bars)
        all_bars.extend(page_bars)
        if on_page is not None:
            on_page(page_number, len(all_bars))
        page_token = payload.get("next_page_token")
        if not page_token:
            return all_bars
    raise UniverseResearchError(
        f"{normalized_symbol}: exceeded the {MAX_PAGES_PER_SYMBOL}-page pagination guard "
        "without the provider exhausting next_page_token"
    )


def eligible_minute_bars(bars: Sequence[MarketBar], *, symbol: str, now: datetime) -> list[MinuteBar]:
    """Quality-gate raw normalized bars before bridging to `MinuteBar`.
    `MarketBar.to_minute_bar()` (`src/market_data_canonical.py`) is
    documented to be called only on bars that already carry
    `MarketDataQuality.VALID` -- this applies the same canonical fail-closed
    gate (`validate_market_bar` / `validate_market_data_batch`) that
    `_live_eligible_bars` applies in `src/live_shadow_session.py`, dropping
    (never silently repairing) anything malformed, duplicate, out-of-order,
    or outside the regular session, so extended-hours or corrupt bars never
    contaminate an opening-range, RVOL, or breakout calculation."""
    batch = validate_market_data_batch(bars)
    eligible: list[MinuteBar] = []
    for index, bar in enumerate(bars):
        outcome = batch.get(
            index,
            validate_market_bar(
                bar, expected_symbol=symbol, expected_trading_date=bar.trading_date,
                expected_interval_seconds=60, freshness_policy=RESEARCH_FRESHNESS_POLICY, now=now,
            ),
        )
        if outcome is MarketDataQuality.VALID:
            eligible.append(bar.to_minute_bar())
    return eligible


def validate_cache_integrity(cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, list[str]]:
    """Scan the on-disk response cache and remove entries that cannot
    possibly be a usable Alpaca response: unparseable JSON, or a payload
    missing the `bars` key entirely (e.g. an interrupted write). Returns
    `{"removed": [...], "kept": [...]}` filenames.

    A cached response with a non-null `next_page_token` (a truncated first
    or middle page) is intentionally NOT evicted here -- it is a genuine,
    correctly normalized partial answer, not corruption. Every page's cache
    key is a hash of its exact request params, which now includes
    `page_token`; `fetch_full_minute_history` requesting the same first page
    again gets this cache hit, then simply requests the NEXT page (a
    different params dict -> a different, not-yet-cached key) to continue
    where the original single-page fetch stopped. No previously fetched
    page is wasted or re-downloaded."""
    if not cache_dir.exists():
        return {"removed": [], "kept": []}
    removed: list[str] = []
    kept: list[str] = []
    for path in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            removed.append(path.name)
            continue
        if not isinstance(payload, Mapping) or "bars" not in payload:
            path.unlink(missing_ok=True)
            removed.append(path.name)
            continue
        kept.append(path.name)
    return {"removed": removed, "kept": kept}


__all__ = [
    "UniverseResearchError",
    "UNIVERSE_POOL",
    "UNIVERSE_SIZES",
    "SPY_SYMBOL",
    "production_universe",
    "liquid_universe",
    "incremental_symbols",
    "all_pool_symbols_with_production",
    "trading_sessions_before",
    "DEFAULT_RVOL_WARMUP_MULTIPLIER",
    "historical_fetch_start",
    "CANDIDATE",
    "GAP_ELIMINATED",
    "RVOL_ELIMINATED",
    "SPY_VWAP_ELIMINATED",
    "BREAKOUT_ELIMINATED",
    "DATA_INCOMPLETE",
    "MISSING_OPENING_RANGE_BARS",
    "INSUFFICIENT_RVOL_LOOKBACK_HISTORY",
    "MISSING_HISTORICAL_PRIOR_CLOSE",
    "MISSING_TODAY_BARS",
    "MISSING_SPY_BARS_FOR_SESSION",
    "MISSING_ENTRY_WINDOW_BARS",
    "NO_EVALUABLE_MINUTE_IN_WINDOW",
    "DATA_INCOMPLETE_REASONS",
    "SessionEvaluation",
    "evaluate_symbol_session",
    "MINIMUM_COVERAGE",
    "VALID_COVERAGE",
    "INVALID_INSUFFICIENT_COVERAGE",
    "UniverseSizeReport",
    "build_study_report",
    "csv_summary_rows",
    "BELOW_MINIMUM_PRICE",
    "BELOW_MINIMUM_DOLLAR_VOLUME",
    "BELOW_MINIMUM_SHARE_VOLUME",
    "INSUFFICIENT_OPENING_RANGE_COVERAGE",
    "INSUFFICIENT_RVOL_HISTORY_COVERAGE",
    "NO_HISTORICAL_DATA",
    "LiquidityThresholds",
    "DEFAULT_LIQUIDITY_THRESHOLDS",
    "SymbolLiquidityProfile",
    "compute_symbol_liquidity_profile",
    "rank_liquidity_qualified_symbols",
    "LiquidityTier",
    "build_liquidity_tiers",
    "cache_key",
    "CachingHttpClient",
    "MAX_PAGES_PER_SYMBOL",
    "SUPPORTED_FEEDS",
    "DEFAULT_FEED",
    "RESEARCH_FRESHNESS_POLICY",
    "fetch_full_minute_history",
    "eligible_minute_bars",
    "validate_cache_integrity",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_OUTPUT_DIR",
]
