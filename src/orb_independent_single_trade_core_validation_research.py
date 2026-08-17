"""Research tooling for the independent single-trade-core validation
study (local milestone M40, GitHub issue not yet filed -- created during a
GitHub outage per operator instruction).

Issue #325 selected two CORE_CANDIDATE configurations
(`GAP_100_RVOL_250`, `GAP_050_RVOL_175`) from the frozen 18-configuration
set by evaluating all of them across P0-P3. Because P0-P3 were used to
*select* these two winners, P0-P3 are now selection data, not independent
evidence: a configuration can look robust across four periods purely
because it was chosen for looking robust across those same four periods.
This module evaluates the two winners -- and *only* the two winners, no
new threshold, no re-optimization -- against one completely
non-overlapping period immediately preceding P0 ("P_MINUS_1"), which
neither config's selection could have seen.

The two configuration labels are identified by the operator's own
boundary language (PRIMARY: gap>=1.00%, RVOL>=2.50x; CHALLENGER:
gap>=0.50%, RVOL>=1.75x) and resolved against the existing, unmodified
`orb_existing_configs_reevaluation_research.FROZEN_CONFIG_SET` -- this
module invents no new `ParameterCombo`.

P_MINUS_1 is defined as exactly 252 research-calendar trading sessions
strictly before P0's own start date (2023-03-31), derived
programmatically via `research_trading_sessions_before` -- never
hand-picked, never moved after the fact. As of this module's authorship,
the research-only historical calendar
(`src.orb_research_historical_calendar`) only covers 2023-01-01 onward,
and the existing bar cache's earliest cached bar is 2023-03-03 -- both
well inside the P_MINUS_1 window, which reaches back to roughly
2022-04-01. `p_minus_1_coverage_gap` detects this precisely (session
count actually available vs. required) instead of silently narrowing the
window, estimating a shortfall, or guessing at un-cached dates.

The universe is the #320 `ANCHORED_COHORT` (imported unchanged from
`orb_baseline_fourth_period_research`) -- the same fixed,
profitability-independent cohort already used to test P0 in Issue #321.
It is reused here unchanged, not re-derived and not chosen based on
P_MINUS_1's own P&L.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.orb_baseline_fourth_period_research import ANCHORED_COHORT, max_drawdown_and_streak_from_rows
from src.orb_baseline_stability_research import TradeDiagnosticRow, excluding_symbol
from src.orb_existing_configs_reevaluation_research import FROZEN_CONFIG_SET
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, symbol_summary, trade_quality_from_rows
from src.orb_research_historical_calendar import (
    RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE,
    ResearchCalendarError,
    research_regular_session_bounds,
    research_trading_sessions_before,
)

__all__ = [
    "IndependentValidationError",
    "PRIMARY_CONFIG_LABEL",
    "CHALLENGER_CONFIG_LABEL",
    "PRIMARY_CONFIG",
    "CHALLENGER_CONFIG",
    "VALIDATION_CONFIG_LABELS",
    "VALIDATION_UNIVERSE",
    "P0_START",
    "P_MINUS_1_SESSION_COUNT",
    "DataCoverageGap",
    "derive_p_minus_1_sessions",
    "p_minus_1_coverage_gap",
    "earliest_cached_bar_utc",
    "ValidationCriteria",
    "DEFAULT_VALIDATION_CRITERIA",
    "classify_validation",
    "recommend_next_milestone",
]


class IndependentValidationError(RuntimeError):
    """Refused: independent single-trade-core validation tooling could not
    safely proceed."""


# ---------------------------------------------------------------------------
# Frozen configuration identification. PRIMARY/CHALLENGER are resolved
# against the existing FROZEN_CONFIG_SET (Issue #325) -- no new
# ParameterCombo is constructed anywhere in this module.

PRIMARY_CONFIG_LABEL = "GAP_100_RVOL_250"
CHALLENGER_CONFIG_LABEL = "GAP_050_RVOL_175"

PRIMARY_CONFIG = FROZEN_CONFIG_SET[PRIMARY_CONFIG_LABEL]
CHALLENGER_CONFIG = FROZEN_CONFIG_SET[CHALLENGER_CONFIG_LABEL]

if PRIMARY_CONFIG.combo.gap_percent < Decimal("1.00") or PRIMARY_CONFIG.combo.rvol_multiple < Decimal("2.50"):
    raise IndependentValidationError(f"PRIMARY config {PRIMARY_CONFIG_LABEL} does not satisfy gap>=1.00%% and RVOL>=2.50x")
if CHALLENGER_CONFIG.combo.gap_percent < Decimal("0.50") or CHALLENGER_CONFIG.combo.rvol_multiple < Decimal("1.75"):
    raise IndependentValidationError(f"CHALLENGER config {CHALLENGER_CONFIG_LABEL} does not satisfy gap>=0.50%% and RVOL>=1.75x")

VALIDATION_CONFIG_LABELS: tuple[str, str] = (PRIMARY_CONFIG_LABEL, CHALLENGER_CONFIG_LABEL)


# ---------------------------------------------------------------------------
# Universe: the #320 fixed/common cohort, reused unchanged. Not re-derived
# for P_MINUS_1, not chosen based on P_MINUS_1's own profitability.

VALIDATION_UNIVERSE: tuple[str, ...] = ANCHORED_COHORT


# ---------------------------------------------------------------------------
# Period: P_MINUS_1 -- 252 sessions strictly before P0's own start date,
# i.e. ending no later than 2023-03-30. Programmatically derived, never
# hand-picked, never moved after reading results.

P0_START = date(2023, 3, 31)
P_MINUS_1_SESSION_COUNT = 252


@dataclass(frozen=True)
class DataCoverageGap:
    """Documents exactly what research-calendar/bar-cache coverage is
    missing to compute P_MINUS_1 in full -- produced instead of silently
    narrowing the window, estimating, or guessing at un-cached dates."""

    requested_session_count: int
    reference_day: date
    calendar_lower_bound: date
    sessions_available_within_calendar_coverage: int
    sessions_short: int
    earliest_cached_bar_utc: str | None
    detail: str


def derive_p_minus_1_sessions(
    *, reference_day: date = P0_START, count: int = P_MINUS_1_SESSION_COUNT,
) -> tuple[date, ...]:
    """The `count` most recent research-calendar-supported sessions
    strictly before `reference_day`. Raises `ResearchCalendarError`
    (propagated, not caught) if the research calendar's own coverage floor
    is reached before `count` sessions are found. Callers that want a
    structured report instead of an exception should call
    `p_minus_1_coverage_gap` first."""
    return research_trading_sessions_before(reference_day, count=count)


def _sessions_available_from_calendar_floor(*, reference_day: date) -> int:
    cursor = reference_day - timedelta(days=1)
    available = 0
    while cursor >= RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE:
        if research_regular_session_bounds(cursor) is not None:
            available += 1
        cursor -= timedelta(days=1)
    return available


def earliest_cached_bar_utc(cache_dir: Path) -> str | None:
    """The earliest bar timestamp found across every cached response in
    `cache_dir`, or `None` if the cache is empty/unreadable. Read-only,
    inspects existing cache files only -- never fetches anything."""
    import json

    earliest: str | None = None
    if not cache_dir.exists():
        return None
    for path in cache_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        bars = payload.get("bars") if isinstance(payload, dict) else None
        if not bars:
            continue
        first_ts = bars[0].get("t")
        if first_ts and (earliest is None or first_ts < earliest):
            earliest = first_ts
    return earliest


def p_minus_1_coverage_gap(
    *, reference_day: date = P0_START, count: int = P_MINUS_1_SESSION_COUNT, cache_dir: Path | None = None,
) -> DataCoverageGap | None:
    """Attempts to derive P_MINUS_1; returns `None` if fully computable
    from current research-calendar coverage, else a `DataCoverageGap`
    documenting exactly what is missing."""
    try:
        derive_p_minus_1_sessions(reference_day=reference_day, count=count)
        return None
    except ResearchCalendarError:
        pass

    available = _sessions_available_from_calendar_floor(reference_day=reference_day)
    short = count - available
    cached = earliest_cached_bar_utc(cache_dir) if cache_dir is not None else None

    return DataCoverageGap(
        requested_session_count=count,
        reference_day=reference_day,
        calendar_lower_bound=RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE,
        sessions_available_within_calendar_coverage=available,
        sessions_short=short,
        earliest_cached_bar_utc=cached,
        detail=(
            f"P_MINUS_1 requires {count} sessions strictly before {reference_day.isoformat()}; "
            f"the research calendar only covers {RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE.isoformat()} "
            f"onward, which provides {available} sessions in range ({short} short). Closing this gap "
            "requires (1) extending src/orb_research_historical_calendar.py with verified NYSE 2022 "
            "full-holiday/early-close tables (same pattern as the existing 2023/2024/2025 extensions), "
            "and (2) a new Alpaca network fetch of 5-minute bars for the validation universe and SPY "
            f"covering the additional {short} sessions -- not present in the existing bar cache"
            + (f" (earliest cached bar currently: {cached})" if cached else " (bar cache appears empty)")
            + "."
        ),
    )


# ---------------------------------------------------------------------------
# Predeclared classification -- frozen before P_MINUS_1 results are read.

@dataclass(frozen=True)
class ValidationCriteria:
    minimum_one_per_day_closed_trades: int = MIN_SAMPLE_CLOSED_TRADES
    all_candidates_not_materially_negative_threshold: float = -0.05
    maximum_acceptable_drawdown_r: float = 15.0


DEFAULT_VALIDATION_CRITERIA = ValidationCriteria()


def _expectancy_r(tq: Mapping[str, Any] | None) -> float | None:
    return (tq or {}).get("expectancy_r_per_trade")


def _pf_r(tq: Mapping[str, Any] | None) -> float | None:
    tq = tq or {}
    raw = tq.get("profit_factor_r")
    if raw is None and tq.get("profit_factor_r_infinite_no_losses"):
        return float("inf")
    return raw


def classify_validation(
    *,
    one_per_day: Mapping[str, Any] | None,
    all_candidates: Mapping[str, Any] | None,
    one_per_day_excluding_best_symbol: Mapping[str, Any] | None,
    drawdown_r: float | None,
    coverage_gap: DataCoverageGap | None,
    criteria: ValidationCriteria = DEFAULT_VALIDATION_CRITERIA,
) -> dict[str, Any]:
    """VALIDATED / MIXED / FAILED / INSUFFICIENT_SAMPLE, per this study's
    own predeclared rule -- fixed before any P_MINUS_1 result was read.
    R-normalized metrics only; price-per-share fields are never read by
    this function. A `coverage_gap` (missing calendar/bar-cache coverage)
    always yields INSUFFICIENT_SAMPLE -- data unavailability is not a
    verdict about the strategy."""
    if coverage_gap is not None:
        return {
            "verdict": "INSUFFICIENT_SAMPLE",
            "reason": f"data coverage gap, not a strategy result: {coverage_gap.detail}",
        }
    if one_per_day is None or all_candidates is None:
        return {"verdict": "INSUFFICIENT_SAMPLE", "reason": "no P_MINUS_1 results supplied"}

    closed = one_per_day.get("closed_trades") or 0
    if closed < criteria.minimum_one_per_day_closed_trades:
        return {
            "verdict": "INSUFFICIENT_SAMPLE",
            "reason": f"one-trade-per-day closed trades={closed} below minimum {criteria.minimum_one_per_day_closed_trades}",
        }

    exp_r = _expectancy_r(one_per_day)
    pf_r = _pf_r(one_per_day)
    all_cand_exp_r = _expectancy_r(all_candidates)
    excl_best_exp_r = _expectancy_r(one_per_day_excluding_best_symbol)

    positive_edge = (exp_r or 0) > 0 and (pf_r or 0) > 1

    checks = {
        "one_per_day_expectancy_r_positive": (exp_r or 0) > 0,
        "one_per_day_pf_r_above_1": (pf_r or 0) > 1,
        "adequate_sample": True,
        "all_candidates_not_materially_negative": (
            all_cand_exp_r is not None and all_cand_exp_r >= criteria.all_candidates_not_materially_negative_threshold
        ),
        "not_single_symbol_dependent": (excl_best_exp_r or 0) > 0,
        "acceptable_drawdown": drawdown_r is not None and drawdown_r <= criteria.maximum_acceptable_drawdown_r,
    }

    if all(checks.values()):
        verdict = "VALIDATED"
    elif positive_edge:
        verdict = "MIXED"
    else:
        verdict = "FAILED"

    return {
        "verdict": verdict, "checks": checks,
        "one_per_day_expectancy_r": exp_r, "one_per_day_profit_factor_r": pf_r,
        "all_candidates_expectancy_r": all_cand_exp_r,
        "one_per_day_excluding_best_symbol_expectancy_r": excl_best_exp_r,
        "drawdown_r": drawdown_r,
    }


def recommend_next_milestone(*, primary_verdict: str, challenger_verdict: str) -> dict[str, Any]:
    """Exactly one of PROFIT_PROTECTION_EXIT_EXPERIMENT /
    CANDIDATE_SELECTION_RESEARCH / PAUSE_SINGLE_TRADE_CORE, per this
    study's own fixed rule."""
    verdicts = {primary_verdict, challenger_verdict}
    if "VALIDATED" in verdicts:
        milestone = "PROFIT_PROTECTION_EXIT_EXPERIMENT"
        rationale = "At least one config independently validated out-of-sample on P_MINUS_1 -- profit-protection/exit refinement is the next defensible step for a validated core."
    elif "MIXED" in verdicts:
        milestone = "CANDIDATE_SELECTION_RESEARCH"
        rationale = "Neither config fully validated, but at least one shows a positive-edge-with-caveats (MIXED) result on P_MINUS_1 -- candidate-quality/selection research is more defensible than declaring the core validated or abandoning it outright."
    else:
        milestone = "PAUSE_SINGLE_TRADE_CORE"
        rationale = "Neither config could be independently validated on P_MINUS_1 (FAILED or INSUFFICIENT_SAMPLE) -- pausing core-dependent research (profit-protection, candidate-selection) until independent validation is achieved."
    return {
        "next_milestone": milestone, "rationale": rationale,
        "primary_verdict": primary_verdict, "challenger_verdict": challenger_verdict,
        "authorizes_production_change": False,
    }
