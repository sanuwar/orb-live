"""Research tooling for the fine-RVOL validation study (Issue TBD, opened by
this change).

The prior gap x RVOL sensitivity study (Issue #300, merged) found RVOL to be
the dominant frequency lever and reported its highest-tested frequency at
gap=0.50%/rvol=1.50x (44.44% candidate-day rate, 90 sessions) with the
strongest trade-quality figures in that matrix at the same point. That
single coarse grid point does not by itself distinguish a real threshold
effect from a 90-session sample artifact. This module answers exactly that:
does the elevated candidate-day rate and trade quality near RVOL=1.50x
persist under a finer RVOL grid (2.00x/1.75x/1.50x/1.25x, holding the same
three gap levels) and across independent chronological halves of the same
sample?

Reuse, not reinterpretation: every combination in this finer 3x4=12 matrix is
evaluated through the exact same unmodified pipeline Issue #300 already
validated -- `src.orb_universe_expansion_research.resolve_session_signal`
(fed by `precompute_session_signal_inputs`), `build_study_report`, and the
merged M15 shadow-execution simulator for trade quality
(`src.shadow_execution_simulator.simulate_shadow_execution` via
`src.shadow_trading_decision.build_shadow_trading_decision`). This module
imports the shared building blocks directly from
`src.orb_gap_rvol_sensitivity_research` (config override, per-combo
evaluation, matrix-row aggregation, concentration, incremental-effect
accounting, trade-quality aggregation) and adds only: (1) a finer RVOL
threshold tuple, (2) chronological-split-half evaluation and the two
robustness assessments this study specifically asks for (goal items F and
G), and (3) a recommendation dict that reports the real production baseline
(gap=1.00%/rvol=3.0x) as external context since it is not itself a point in
this finer grid.

Safety: identical discipline to `orb_gap_rvol_sensitivity_research` --
`sensitivity_config` (imported unmodified) always deep-copies the loaded
config before overriding the two thresholds under study; `config/
strategy_v0.yaml` is never read back or written by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.orb_gap_rvol_sensitivity_research import (
    ComboEvaluation,
    FREQUENCY_TARGETS,
    InputsTable,
    NORMALIZATION_VERSION,
    ParameterCombo,
    REPOSITORY_ROOT,
    RESEARCH_UNIVERSE_LABEL,
    TRADE_QUALITY_NOT_YET_EVALUATED,
    aggregate_trade_quality,
    baseline_combo as production_baseline_combo,
    build_candidate_shadow_decision,
    evaluate_combo,
    frequency_target_hits,
    incremental_effect,
    matrix_row,
    precompute_inputs_table,
    sensitivity_config,
    simulate_combo_trade_quality,
    symbol_concentration,
)

#: Same three gap levels Issue #300 tested -- this study does not assume
#: 0.50% is the better gap setting just because it had the highest frequency
#: in the coarse-grid sample; the full 3x4 matrix re-tests all three.
GAP_THRESHOLDS: tuple[Decimal, ...] = (Decimal("1.00"), Decimal("0.75"), Decimal("0.50"))
#: The finer RVOL grid this study adds. Issue #300's coarsest step below its
#: own baseline was 3.0x -> 2.5x -> 2.0x -> 1.5x; this study starts at 2.00x
#: (one notch above Issue #300's most-relaxed tested value) and steps down
#: in 0.25x increments through 1.25x, bracketing the reported 1.50x
#: "sweet spot" from both sides.
RVOL_THRESHOLDS: tuple[Decimal, ...] = (Decimal("2.00"), Decimal("1.75"), Decimal("1.50"), Decimal("1.25"))


class FineRvolValidationError(RuntimeError):
    """Refused: fine-RVOL validation tooling could not safely proceed."""


def parameter_matrix() -> tuple[ParameterCombo, ...]:
    """The full, deterministic 12-combination fine gap x RVOL matrix."""
    return tuple(
        ParameterCombo(gap_percent=gap, rvol_multiple=rvol)
        for gap in GAP_THRESHOLDS
        for rvol in RVOL_THRESHOLDS
    )


def gap_relaxation_steps() -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple(zip(GAP_THRESHOLDS, GAP_THRESHOLDS[1:]))


def rvol_relaxation_steps() -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple(zip(RVOL_THRESHOLDS, RVOL_THRESHOLDS[1:]))


def relaxation_distance(combo: ParameterCombo) -> int:
    """How many single-threshold relaxation steps separate `combo` from this
    grid's own most-restrictive corner (gap=1.00%, rvol=2.00x) -- used only
    to break ranking ties toward the smaller relaxation, matching
    `orb_gap_rvol_sensitivity_research.relaxation_distance`'s convention."""
    gap_index = GAP_THRESHOLDS.index(combo.gap_percent)
    rvol_index = RVOL_THRESHOLDS.index(combo.rvol_multiple)
    return gap_index + rvol_index


def all_incremental_effects(evaluations_by_combo: Mapping[ParameterCombo, ComboEvaluation]) -> list[dict[str, Any]]:
    """Every adjacent RVOL step at each fixed gap, and every adjacent gap
    step at each fixed RVOL, across this study's finer grid -- goal item
    "key comparisons" plus the general incremental-effects requirement."""
    effects: list[dict[str, Any]] = []
    for gap in GAP_THRESHOLDS:
        for from_rvol, to_rvol in rvol_relaxation_steps():
            from_eval = evaluations_by_combo[ParameterCombo(gap, from_rvol)]
            to_eval = evaluations_by_combo[ParameterCombo(gap, to_rvol)]
            effects.append(incremental_effect(from_eval, to_eval, axis="rvol", held_fixed=f"gap={gap}%").as_dict())
    for rvol in RVOL_THRESHOLDS:
        for from_gap, to_gap in gap_relaxation_steps():
            from_eval = evaluations_by_combo[ParameterCombo(from_gap, rvol)]
            to_eval = evaluations_by_combo[ParameterCombo(to_gap, rvol)]
            effects.append(incremental_effect(from_eval, to_eval, axis="gap", held_fixed=f"rvol={rvol}x").as_dict())
    return effects


# ---------------------------------------------------------------------------
# Chronological split-half robustness (goal item "Robustness").
# ---------------------------------------------------------------------------


def split_sessions_chronologically(session_dates: Sequence[date]) -> tuple[tuple[date, ...], tuple[date, ...]]:
    """Splits a sorted session-date sequence into two chronological halves
    of as-equal-as-possible size (the first half absorbs the odd session on
    an odd-length sample). Deterministic and order-independent: `session_dates`
    is sorted before splitting regardless of input order."""
    ordered = tuple(sorted(session_dates))
    midpoint = (len(ordered) + 1) // 2
    return ordered[:midpoint], ordered[midpoint:]


def assess_rvol_1_50_robustness(
    *,
    first_half_rows: Mapping[ParameterCombo, Mapping[str, Any]],
    second_half_rows: Mapping[ParameterCombo, Mapping[str, Any]],
    gap: Decimal = Decimal("0.50"),
) -> dict[str, Any]:
    """Goal item F: is the elevated candidate-day rate at RVOL=1.50x (at the
    given gap, versus this grid's strictest RVOL=2.00x) present
    independently in *both* chronological halves of the sample, not only in
    the full-sample aggregate? A step that only shows up when the whole
    sample is pooled is exactly the "90-session artifact" pattern this study
    exists to rule out."""
    strict = ParameterCombo(gap_percent=gap, rvol_multiple=Decimal("2.00"))
    target = ParameterCombo(gap_percent=gap, rvol_multiple=Decimal("1.50"))
    first_delta = first_half_rows[target]["candidate_day_rate"] - first_half_rows[strict]["candidate_day_rate"]
    second_delta = second_half_rows[target]["candidate_day_rate"] - second_half_rows[strict]["candidate_day_rate"]
    robust = first_delta > 0 and second_delta > 0
    return {
        "gap": str(gap),
        "comparison": "rvol_1.50x_vs_rvol_2.00x_candidate_day_rate_delta",
        "first_half_delta": first_delta,
        "second_half_delta": second_delta,
        "verdict": "ROBUST_ACROSS_BOTH_HALVES" if robust else "NOT_ROBUST_SAMPLE_ARTIFACT_SUSPECTED",
    }


def assess_rvol_1_25_signal(
    *,
    rows_by_combo: Mapping[ParameterCombo, Mapping[str, Any]],
    trade_quality_by_combo: Mapping[ParameterCombo, Mapping[str, Any]],
    gap: Decimal = Decimal("0.50"),
) -> dict[str, Any]:
    """Goal item G: relaxing RVOL one more step below 1.50x to 1.25x -- does
    it meaningfully add frequency while preserving quality, or does it
    mostly admit weaker trades and concentrate risk? `MOSTLY_NOISE` requires
    *both* a worse expectancy/trade and worse top-3 concentration than the
    1.50x row at the same gap; a single softer metric alone is not enough to
    call the extra step noise."""
    combo_150 = ParameterCombo(gap_percent=gap, rvol_multiple=Decimal("1.50"))
    combo_125 = ParameterCombo(gap_percent=gap, rvol_multiple=Decimal("1.25"))
    days_delta = rows_by_combo[combo_125]["sessions_with_candidate"] - rows_by_combo[combo_150]["sessions_with_candidate"]
    occurrences_delta = rows_by_combo[combo_125]["total_candidates"] - rows_by_combo[combo_150]["total_candidates"]

    tq_150 = trade_quality_by_combo.get(combo_150, {})
    tq_125 = trade_quality_by_combo.get(combo_125, {})
    expectancy_150 = tq_150.get("expectancy_per_trade")
    expectancy_125 = tq_125.get("expectancy_per_trade")
    expectancy_150_f = float(expectancy_150) if expectancy_150 is not None else None
    expectancy_125_f = float(expectancy_125) if expectancy_125 is not None else None
    expectancy_degrades = (
        expectancy_150_f is not None and expectancy_125_f is not None and expectancy_125_f < expectancy_150_f
    )

    concentration_150 = rows_by_combo[combo_150]["concentration"]["top_3_share"]
    concentration_125 = rows_by_combo[combo_125]["concentration"]["top_3_share"]
    concentration_worsens = concentration_125 > concentration_150

    if days_delta <= 0:
        verdict = "NO_ADDITIONAL_FREQUENCY"
    elif expectancy_degrades and concentration_worsens:
        verdict = "MOSTLY_NOISE"
    else:
        verdict = "USEFUL_SIGNAL"

    return {
        "gap": str(gap),
        "additional_candidate_days_vs_1.50x": days_delta,
        "additional_candidate_occurrences_vs_1.50x": occurrences_delta,
        "expectancy_per_trade_1.50x": expectancy_150,
        "expectancy_per_trade_1.25x": expectancy_125,
        "expectancy_degrades": expectancy_degrades,
        "top_3_concentration_1.50x": concentration_150,
        "top_3_concentration_1.25x": concentration_125,
        "concentration_worsens": concentration_worsens,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Ranking / recommendation (mirrors
# `orb_gap_rvol_sensitivity_research.build_recommendation`'s documented
# heuristic, extended with the production-baseline-as-context row this
# grid's own most-restrictive corner is not equal to, plus the two
# robustness assessments above).
# ---------------------------------------------------------------------------


def build_recommendation(
    rows_by_combo: Mapping[ParameterCombo, Mapping[str, Any]],
    *,
    production_baseline_row: Mapping[str, Any],
    robustness: Mapping[str, Any],
    rvol_1_25_signal: Mapping[str, Any],
    trade_quality_by_combo: Mapping[ParameterCombo, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    matrix = parameter_matrix()
    baseline = production_baseline_combo()

    highest_frequency = max(
        matrix, key=lambda combo: (rows_by_combo[combo]["candidate_day_rate"], -relaxation_distance(combo)),
    )
    first_50pct = next(
        (combo for combo in matrix if rows_by_combo[combo]["candidate_day_rate"] >= 0.50), None,
    )

    baseline_days = production_baseline_row["sessions_with_candidate"]
    smallest_relaxation = None
    for distance in range(1, len(GAP_THRESHOLDS) + len(RVOL_THRESHOLDS) - 1):
        candidates_at_distance = [c for c in matrix if relaxation_distance(c) == distance]
        improved = [
            c for c in candidates_at_distance
            if rows_by_combo[c]["sessions_with_candidate"] > baseline_days
            and rows_by_combo[c]["coverage_status"] == "VALID"
        ]
        if improved:
            smallest_relaxation = max(improved, key=lambda c: rows_by_combo[c]["sessions_with_candidate"])
            break

    valid_combos = [c for c in matrix if rows_by_combo[c]["coverage_status"] == "VALID"]
    pool = valid_combos or list(matrix)
    if trade_quality_by_combo:
        def score(combo: ParameterCombo) -> tuple[float, float, int]:
            row = rows_by_combo[combo]
            concentration = row["concentration"]["top_3_share"]
            expectancy_raw = trade_quality_by_combo.get(combo, {}).get("expectancy_per_trade")
            expectancy = float(expectancy_raw) if expectancy_raw is not None else 0.0
            return (row["candidate_day_rate"] * (1.0 - concentration), expectancy, -relaxation_distance(combo))
    else:
        def score(combo: ParameterCombo) -> tuple[float, float, int]:
            row = rows_by_combo[combo]
            concentration = row["concentration"]["top_3_share"]
            return (row["candidate_day_rate"] * (1.0 - concentration), 0.0, -relaxation_distance(combo))
    recommended = max(pool, key=score)

    def _combo_dict(combo: ParameterCombo | None) -> dict[str, str] | None:
        if combo is None:
            return None
        return {"combo": combo.label, "gap_percent": str(combo.gap_percent), "rvol_multiple": str(combo.rvol_multiple)}

    return {
        "production_baseline": {
            **_combo_dict(baseline),
            "candidate_day_rate": production_baseline_row["candidate_day_rate"],
            "sessions_with_candidate": production_baseline_row["sessions_with_candidate"],
        },
        "smallest_useful_relaxation": _combo_dict(smallest_relaxation),
        "first_exceeding_50pct_candidate_days": _combo_dict(first_50pct),
        "highest_frequency": _combo_dict(highest_frequency),
        "recommended_for_further_validation": _combo_dict(recommended),
        "rvol_1_50x_robustness": dict(robustness),
        "rvol_1_25x_signal_assessment": dict(rvol_1_25_signal),
        "rationale": (
            "recommended_for_further_validation maximizes candidate_day_rate * "
            "(1 - top_3_symbol_concentration) among VALID-coverage combinations "
            "in this finer 3x4 grid (falling back to all 12 if none reach VALID "
            "coverage), using expectancy_per_trade as a tiebreaker when "
            "trade-quality data exists and the smallest relaxation distance from "
            "this grid's own strictest corner (gap=1.00%, rvol=2.00x) as the "
            "final tiebreaker. production_baseline (gap=1.00%, rvol=3.0x) is "
            "reported as external context, not as a point in this grid. This is "
            "a research heuristic for choosing what to validate further, not an "
            "optimizer and not a production change."
        ),
    }


__all__ = [
    "FineRvolValidationError",
    "GAP_THRESHOLDS",
    "RVOL_THRESHOLDS",
    "FREQUENCY_TARGETS",
    "TRADE_QUALITY_NOT_YET_EVALUATED",
    "ParameterCombo",
    "parameter_matrix",
    "gap_relaxation_steps",
    "rvol_relaxation_steps",
    "relaxation_distance",
    "all_incremental_effects",
    "split_sessions_chronologically",
    "assess_rvol_1_50_robustness",
    "assess_rvol_1_25_signal",
    "build_recommendation",
    "production_baseline_combo",
    "sensitivity_config",
    "InputsTable",
    "precompute_inputs_table",
    "evaluate_combo",
    "ComboEvaluation",
    "matrix_row",
    "symbol_concentration",
    "incremental_effect",
    "frequency_target_hits",
    "build_candidate_shadow_decision",
    "simulate_combo_trade_quality",
    "aggregate_trade_quality",
    "RESEARCH_UNIVERSE_LABEL",
    "REPOSITORY_ROOT",
    "NORMALIZATION_VERSION",
]
