"""M51 (Issue #354): $30K higher-return strategy-family selection gate.

M43-M49 built and honestly evaluated the frozen large-cap ORB lane plus a
$30K ETF strategy family sharing capital with it (M49's own
`PRACTICAL_CORE_SATELLITE_30K`: ~$184.75/month base, `SAFE_BUT_BELOW_
TARGET`). M50 could not independently validate that family on 2019-2020
data (`INSUFFICIENT_EVIDENCE`, Alpaca IEX minute coverage floor). That
family is not reopened, retuned, or further squeezed here -- it is
parked unless new independent evidence arises.

M51 asks a different, bounded question: is there at most one genuinely
different, practical strategy family -- among exactly four predeclared
candidates -- that deserves a separately frozen empirical M52 experiment
for a $30,000 account, targeting $500/month (practical) to $1,000/month
(stretch) after realistic costs?

This module encodes the frozen selection protocol as auditable,
testable data and logic, committed *before* any evidence is scored or
any conclusion is recorded (Issue #354's own required sequencing): the
four candidate families, the ten-criterion rubric, the four gating
dimensions the selection rule actually turns on, and the deterministic
selection function itself. `src.strategy_family_selection_gate_results`
(a separate module, committed later in a results-only commit) supplies
the actual `CriterionScore` evidence this module's functions consume --
never fabricated here, and this module performs no research of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "PRACTICAL_TARGET_MONTHLY_DOLLARS",
    "STRETCH_TARGET_MONTHLY_DOLLARS",
    "STARTING_CAPITAL",
    "M49_REFERENCE_RANGE",
    "CANDIDATE_FAMILIES",
    "FAMILY_MOMENTUM",
    "FAMILY_PEAD",
    "FAMILY_OPTION_PREMIUM",
    "FAMILY_TREND_FOLLOWING",
    "RubricCriterion",
    "SELECTION_RUBRIC",
    "GATING_CRITERIA_IDS",
    "VERDICT_PASS",
    "VERDICT_FAIL",
    "EvidenceSource",
    "CriterionScore",
    "FamilyGateResult",
    "evaluate_family_gates",
    "CLASSIFICATION_SELECTED_FOR_M52",
    "CLASSIFICATION_NO_CREDIBLE_CANDIDATE",
    "SelectionResult",
    "select_family",
]

# ---------------------------------------------------------------------------
# Frozen targets and account constraints (Issue #354).
# ---------------------------------------------------------------------------

STARTING_CAPITAL = 30_000
PRACTICAL_TARGET_MONTHLY_DOLLARS = 500
STRETCH_TARGET_MONTHLY_DOLLARS = 1_000

#: M49's own frozen 2022-2026 result, carried forward only as a reference
#: point for "explicitly compare the selected candidate's plausible
#: reward and drawdown with the existing M49 range" -- never reopened,
#: retuned, or combined with M51's own evaluation.
M49_REFERENCE_RANGE: dict[str, object] = {
    "base_mean_monthly_dollars": "184.75",
    "stress_mean_monthly_dollars": "122.02",
    "base_max_drawdown_pct": "0.1460",
    "stress_max_drawdown_pct": "0.1854",
    "classification": "SAFE_BUT_BELOW_TARGET",
    "note": "Frozen reference only. M51 does not reopen, retune, or further squeeze this family.",
}

# ---------------------------------------------------------------------------
# The exactly-four candidate families (Issue #354) -- no expanding the list.
# ---------------------------------------------------------------------------

FAMILY_MOMENTUM = "CONCENTRATED_CROSS_SECTIONAL_MOMENTUM"
FAMILY_PEAD = "POST_EARNINGS_ANNOUNCEMENT_DRIFT"
FAMILY_OPTION_PREMIUM = "DEFINED_RISK_INDEX_OPTION_PREMIUM"
FAMILY_TREND_FOLLOWING = "TACTICAL_ETF_TREND_FOLLOWING"

CANDIDATE_FAMILIES: tuple[str, ...] = (
    FAMILY_MOMENTUM,
    FAMILY_PEAD,
    FAMILY_OPTION_PREMIUM,
    FAMILY_TREND_FOLLOWING,
)

# ---------------------------------------------------------------------------
# The ten-criterion rubric (Issue #354), frozen before any family is scored.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RubricCriterion:
    id: str
    description: str


SELECTION_RUBRIC: tuple[RubricCriterion, ...] = (
    RubricCriterion("evidence_strength", "Strength and independence of published evidence"),
    RubricCriterion("data_availability_cost", "Point-in-time data availability and cost"),
    RubricCriterion("survivorship_lookahead_risk", "Survivorship/lookahead risk"),
    RubricCriterion("account_size_feasibility", "Implementability with $30,000 and whole-contract/share constraints"),
    RubricCriterion("realistic_costs", "Realistic transaction costs, spreads, assignment, liquidity, and tax/operational complexity"),
    RubricCriterion("monthly_dollar_range", "Plausible net monthly-dollar range"),
    RubricCriterion("drawdown_tail_risk", "Expected drawdown and tail-loss characteristics"),
    RubricCriterion("repo_robinhood_compatibility", "Compatibility with the existing repository and eventual Robinhood execution"),
    RubricCriterion("engineering_effort", "Engineering effort required for a trustworthy backtest"),
    RubricCriterion("target_plausibility", "Whether $500/month and $1,000/month are mathematically plausible without hiding unacceptable risk"),
)

#: The selection rule (Issue #354) names exactly four gating dimensions:
#: "Select it only if authoritative evidence, accessible point-in-time
#: data, account-size feasibility, and realistic after-cost economics all
#: pass." The first three map to one rubric criterion each
#: (`evidence_strength`, `data_availability_cost`,
#: `account_size_feasibility`); "realistic after-cost economics" is a
#: composite of the three criteria that are actually about economics
#: (`realistic_costs`, `monthly_dollar_range`, `target_plausibility`) --
#: a family fails this gate if ANY of the three fails, matching the
#: plain-language "realistic after-cost economics" as a single
#: pass/fail dimension. `survivorship_lookahead_risk`,
#: `drawdown_tail_risk`, `repo_robinhood_compatibility`, and
#: `engineering_effort` are scored and reported for every family but do
#: not by themselves gate selection -- they inform the qualitative
#: writeup and the deterministic tiebreak below, not a hard veto.
GATING_CRITERIA_IDS: tuple[str, ...] = ("evidence_strength", "data_availability_cost", "account_size_feasibility")
ECONOMICS_GATE_CRITERIA_IDS: tuple[str, ...] = ("realistic_costs", "monthly_dollar_range", "target_plausibility")

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"


@dataclass(frozen=True)
class EvidenceSource:
    family: str
    citation: str
    source_type: str
    url: str
    key_finding: str


@dataclass(frozen=True)
class CriterionScore:
    family: str
    criterion_id: str
    verdict: str
    reasoning: str

    def __post_init__(self) -> None:
        if self.verdict not in (VERDICT_PASS, VERDICT_FAIL):
            raise ValueError(f"verdict must be PASS or FAIL, got {self.verdict!r}")
        if self.criterion_id not in {c.id for c in SELECTION_RUBRIC}:
            raise ValueError(f"unknown criterion_id {self.criterion_id!r}")
        if self.family not in CANDIDATE_FAMILIES:
            raise ValueError(f"unknown family {self.family!r}")


@dataclass(frozen=True)
class FamilyGateResult:
    family: str
    evidence_gate_pass: bool
    data_gate_pass: bool
    account_size_gate_pass: bool
    economics_gate_pass: bool
    all_gates_pass: bool
    total_pass_count: int
    total_criteria_scored: int


def evaluate_family_gates(scores: tuple[CriterionScore, ...], *, family: str) -> FamilyGateResult:
    """Deterministic, pure evaluation of one family's gating dimensions
    from its already-recorded `CriterionScore`s. Never guesses a missing
    score as PASS: a criterion with no recorded score for this family
    counts as FAIL for gating purposes (fail closed)."""
    by_criterion = {s.criterion_id: s.verdict for s in scores if s.family == family}

    def _passes(criterion_id: str) -> bool:
        return by_criterion.get(criterion_id) == VERDICT_PASS

    evidence_gate = _passes("evidence_strength")
    data_gate = _passes("data_availability_cost")
    account_size_gate = _passes("account_size_feasibility")
    economics_gate = all(_passes(cid) for cid in ECONOMICS_GATE_CRITERIA_IDS)
    all_gates = evidence_gate and data_gate and account_size_gate and economics_gate
    total_pass = sum(1 for c in SELECTION_RUBRIC if by_criterion.get(c.id) == VERDICT_PASS)
    return FamilyGateResult(
        family=family,
        evidence_gate_pass=evidence_gate,
        data_gate_pass=data_gate,
        account_size_gate_pass=account_size_gate,
        economics_gate_pass=economics_gate,
        all_gates_pass=all_gates,
        total_pass_count=total_pass,
        total_criteria_scored=len(by_criterion),
    )


CLASSIFICATION_SELECTED_FOR_M52 = "SELECTED_FOR_M52"
CLASSIFICATION_NO_CREDIBLE_CANDIDATE = "NO_CREDIBLE_CANDIDATE"


@dataclass(frozen=True)
class SelectionResult:
    classification: str
    selected_family: str | None
    gate_results_by_family: dict[str, FamilyGateResult] = field(default_factory=dict)
    eligible_families: tuple[str, ...] = field(default_factory=tuple)
    tiebreak_used: bool = False


def select_family(scores: tuple[CriterionScore, ...], *, families: tuple[str, ...] = CANDIDATE_FAMILIES) -> SelectionResult:
    """The frozen selection rule (Issue #354), applied once, deterministically:

    - a family is *eligible* only if all four gating dimensions pass;
    - zero eligible families -> `NO_CREDIBLE_CANDIDATE`, no family selected;
    - exactly one eligible family -> `SELECTED_FOR_M52`, that family;
    - more than one eligible family -> `SELECTED_FOR_M52`, broken by the
      highest total rubric pass-count (all 10 criteria), then by
      `CANDIDATE_FAMILIES`'s own frozen declaration order -- deterministic,
      never a subjective "least bad" judgment call made after the fact.
    """
    gate_results = {f: evaluate_family_gates(scores, family=f) for f in families}
    eligible = tuple(f for f in families if gate_results[f].all_gates_pass)

    if not eligible:
        return SelectionResult(
            classification=CLASSIFICATION_NO_CREDIBLE_CANDIDATE, selected_family=None,
            gate_results_by_family=gate_results, eligible_families=eligible, tiebreak_used=False,
        )

    if len(eligible) == 1:
        return SelectionResult(
            classification=CLASSIFICATION_SELECTED_FOR_M52, selected_family=eligible[0],
            gate_results_by_family=gate_results, eligible_families=eligible, tiebreak_used=False,
        )

    ranked = sorted(eligible, key=lambda f: (-gate_results[f].total_pass_count, families.index(f)))
    return SelectionResult(
        classification=CLASSIFICATION_SELECTED_FOR_M52, selected_family=ranked[0],
        gate_results_by_family=gate_results, eligible_families=eligible, tiebreak_used=True,
    )
