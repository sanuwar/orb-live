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
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Protocol, Sequence

from src.config import load_config
from src.exchange_calendar import EASTERN, regular_session_bounds
from src.market_data import (
    MinuteBar,
    calculate_estimated_vwap,
    calculate_median_opening_volume,
    calculate_opening_range,
)
from src.market_data_canonical import session_open_for
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


@dataclass(frozen=True)
class SessionEvaluation:
    symbol: str
    session_date: date
    state: str
    gap_percent: Decimal | None
    relative_volume: Decimal | None
    approved_at: datetime | None

    def __post_init__(self) -> None:
        if self.state not in _TERMINAL_STATES:
            raise UniverseResearchError("invalid session evaluation state")
        if self.state == CANDIDATE and self.approved_at is None:
            raise UniverseResearchError("a CANDIDATE evaluation must record an approval time")


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
        median_volume = calculate_median_opening_volume(
            candidate_bars, before_date=session_date, lookback_days=lookback_days,
            opening_range_minutes=opening_minutes, session_open_for=session_open_for,
        )
        historical = [
            bar for bar in candidate_bars
            if bar.timestamp_et.date() < session_date and not bar.interpolated
        ]
        today_bars = sorted(
            (
                bar for bar in candidate_bars
                if bar.timestamp_et.date() == session_date and not bar.interpolated
            ),
            key=lambda bar: bar.begins_at,
        )
        spy_today = sorted(
            (
                bar for bar in spy_bars
                if bar.timestamp_et.date() == session_date and not bar.interpolated
            ),
            key=lambda bar: bar.begins_at,
        )
        if not historical or not today_bars or not spy_today:
            raise ValueError("required strategy inputs are absent")
        previous_close = max(historical, key=lambda bar: bar.begins_at).close_price
        today_open = today_bars[0].open_price
    except ValueError:
        return SessionEvaluation(symbol, session_date, DATA_INCOMPLETE, None, None, None)

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
        return SessionEvaluation(symbol, session_date, DATA_INCOMPLETE, None, None, None)

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
        return SessionEvaluation(symbol, session_date, DATA_INCOMPLETE, None, None, None)
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
    top_candidate_symbols: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "universe_size": len(self.universe),
            "universe": list(self.universe),
            "sessions": self.sessions,
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

    for session_date in session_dates:
        session_candidates = 0
        for symbol in universe:
            evaluation = evaluations.get((symbol, session_date))
            if evaluation is None or evaluation.state == DATA_INCOMPLETE:
                data_incomplete_count += 1
                continue
            evaluated_count += 1
            gap_survivors += 1
            if evaluation.state == GAP_ELIMINATED:
                continue
            rvol_survivors += 1
            if evaluation.state == RVOL_ELIMINATED:
                continue
            spy_survivors += 1
            if evaluation.state == SPY_VWAP_ELIMINATED:
                continue
            breakout_survivors += 1
            if evaluation.state == CANDIDATE:
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
        top_candidate_symbols=top_symbols,
    )


def build_study_report(
    *,
    session_dates: Sequence[date],
    evaluations: Mapping[tuple[str, date], SessionEvaluation],
    universe_sizes: Sequence[int] = UNIVERSE_SIZES,
    include_production: bool = True,
) -> dict[str, Any]:
    """Build the full structured report from a completed set of per
    symbol/session evaluations. Pure and deterministic -- no I/O, no
    randomness -- so it can be exercised in tests without network access."""
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
        rows.append({
            "label": universe_report["label"],
            "universe_size": universe_report["universe_size"],
            "sessions": universe_report["sessions"],
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
    "CANDIDATE",
    "GAP_ELIMINATED",
    "RVOL_ELIMINATED",
    "SPY_VWAP_ELIMINATED",
    "BREAKOUT_ELIMINATED",
    "DATA_INCOMPLETE",
    "SessionEvaluation",
    "evaluate_symbol_session",
    "UniverseSizeReport",
    "build_study_report",
    "csv_summary_rows",
    "cache_key",
    "CachingHttpClient",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_OUTPUT_DIR",
]
