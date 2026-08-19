"""M46: liquid small/mid-cap ORB momentum lane feasibility research.

Pure, deterministic building blocks for the frozen 20-symbol (10 IWR
mid-cap + 10 IWM small-cap) universe-selection pipeline: issuer-name
normalization for share-class dedup, median-based daily-bar liquidity
screening against the milestone's frozen thresholds, and the
sector-capped deterministic selection walk. No network I/O and no
strategy semantics live here -- those belong to the one-off acquisition
scripts (`scripts/m46_*.py`) and the later feasibility-experiment module
respectively. Every function here is unit-testable against synthetic
fixtures and never guesses: anything that fails a check is reported with
an explicit reason, not silently dropped or picked.
"""

from __future__ import annotations

import copy
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


class UniverseSelectionError(RuntimeError):
    """Any refusal in the M46 universe-selection pipeline."""


# --------------------------------------------------------------------------
# In-memory-only research universe override for SignalEngine evaluation.
# --------------------------------------------------------------------------

def research_config_for_universe(base_config: Mapping[str, Any], universe: Sequence[str]) -> dict[str, Any]:
    """A deep-copied, in-memory-only version of `base_config` with
    `universe.symbols` replaced by `universe`. This function performs no
    I/O itself -- it neither loads nor writes any file; the caller loads
    the tracked config (e.g. `src.config.load_config()`) and passes it in,
    and nothing here ever persists the result back to
    `config/strategy_v0.yaml`. `base_config` is deep-copied before the
    override, so the caller's original object is never mutated -- every
    other key (`strategy.enabled`, signal thresholds, risk/exit rules) is
    carried through unchanged. Mirrors the same in-memory-override
    convention `orb_gap_rvol_sensitivity_research.sensitivity_config` and
    `orb_profit_protection_exit_research._waited_shadow_decision` already
    use for gap/RVOL thresholds and `strategy.enabled` respectively.

    Required because `SignalEngine.evaluate` rejects any symbol not in
    `config["universe"]["symbols"]` regardless of gap/RVOL qualification
    (`src/signal_engine.py`: "Symbol is not in the approved universe.") --
    without this override, a symbol outside the tracked production
    15-symbol universe is silently rejected before gap/RVOL is ever
    evaluated, which is exactly what happened on the first P3 run against
    the 20-symbol SMALL_MID basket (zero candidate occurrences, not just
    zero closed trades, across all 90 sessions)."""
    if not universe:
        raise UniverseSelectionError("research universe must not be empty")
    overridden = copy.deepcopy(dict(base_config))
    overridden["universe"]["symbols"] = list(universe)
    return overridden


# --------------------------------------------------------------------------
# Issuer-name normalization / share-class dedup
# --------------------------------------------------------------------------

_SHARE_CLASS_SUFFIX_RE = re.compile(
    r",?\s*(Class|Cl\.?|Series)\s*[A-Z0-9]+\.?\s*$", re.IGNORECASE
)


def normalize_issuer_name(name: str) -> str:
    """Strip a trailing share-class designator (', Class A', 'Cl B',
    'Series C', ...) so distinct classes of the same issuer collapse to one
    dedup key. Deliberately does NOT strip corporate-entity suffixes
    ('Inc.', 'Corp.', 'plc', ...), to avoid over-merging distinct issuers
    that happen to share a name prefix."""
    if not isinstance(name, str):
        raise UniverseSelectionError("issuer name must be a string")
    return _SHARE_CLASS_SUFFIX_RE.sub("", name).strip().upper()


@dataclass(frozen=True)
class HoldingCandidate:
    """One resolved NPORT-P holding: a specific fund's position in a
    specific issuer, already mapped to a tradable ticker."""

    source_fund: str
    resolved_ticker: str
    issuer_name: str
    pct_net_assets: float
    value_usd: float = 0.0

    def __post_init__(self) -> None:
        if not self.source_fund:
            raise UniverseSelectionError("source_fund is required")
        if not self.resolved_ticker:
            raise UniverseSelectionError("resolved_ticker is required")


#: Below this weight, a reported NPORT-P holding does not represent
#: genuine, intentional index-tracking membership -- in practice these are
#: residual/stale entries (e.g. a since-delisted or restructured issuer, or
#: securities-lending-collateral-adjacent bookkeeping) rather than an
#: active constituent the fund is choosing to hold. Left unfiltered, a
#: holding like this can still rank into the TOP of a liquidity-sorted
#: selection if the underlying name happens to be a hyper-liquid mega-cap
#: (observed directly: a ~0.0001%-weight Chevron position in IWR's
#: holdings, clearly not a real Russell MidCap constituent, out-ranked
#: genuine mid-caps purely on Chevron's unrelated liquidity). 0.01% is
#: chosen because it cleanly separates the handful of near-zero residual
#: rows actually observed in the source data from every genuine holding.
MIN_MATERIAL_POSITION_WEIGHT = 0.0001


def filter_material_positions(
    candidates: Sequence[HoldingCandidate], *, min_weight: float = MIN_MATERIAL_POSITION_WEIGHT,
) -> tuple[list[HoldingCandidate], list[HoldingCandidate]]:
    """Split candidates into (material, negligible) by `pct_net_assets`.
    A negligible-weight row does not represent genuine, intentional index
    membership and must not compete for a basket slot on the strength of
    an unrelated liquidity profile."""
    material = [c for c in candidates if c.pct_net_assets >= min_weight]
    negligible = [c for c in candidates if c.pct_net_assets < min_weight]
    return material, negligible


def dedupe_share_classes(
    candidates: Sequence[HoldingCandidate],
) -> tuple[list[HoldingCandidate], list[HoldingCandidate]]:
    """Within each fund, collapse (a) multiple rows that resolved to the
    same ticker and (b) multiple rows for different share classes of the
    same issuer, keeping the larger `pct_net_assets` row in each group.
    Returns (kept, excluded) -- both in stable input order among survivors."""
    best_by_ticker: dict[tuple[str, str], HoldingCandidate] = {}
    for c in candidates:
        key = (c.source_fund, c.resolved_ticker)
        current = best_by_ticker.get(key)
        if current is None or c.pct_net_assets > current.pct_net_assets:
            best_by_ticker[key] = c
    stage_one = list(best_by_ticker.values())

    best_by_issuer: dict[tuple[str, str], HoldingCandidate] = {}
    for c in stage_one:
        key = (c.source_fund, normalize_issuer_name(c.issuer_name))
        current = best_by_issuer.get(key)
        if current is None or c.pct_net_assets > current.pct_net_assets:
            best_by_issuer[key] = c

    kept = list(best_by_issuer.values())
    kept_ids = {id(k) for k in kept}
    excluded = [c for c in candidates if id(c) not in kept_ids]
    return kept, excluded


def resolve_cross_fund_overlap(
    candidates: Sequence[HoldingCandidate],
) -> tuple[list[HoldingCandidate], list[HoldingCandidate]]:
    """A ticker eligible under more than one fund is assigned, deterministically,
    to the fund where its `pct_net_assets` is larger (source_fund name as a
    final tie-break). Returns (winners, losers)."""
    by_ticker: dict[str, list[HoldingCandidate]] = {}
    for c in candidates:
        by_ticker.setdefault(c.resolved_ticker, []).append(c)

    winners: list[HoldingCandidate] = []
    losers: list[HoldingCandidate] = []
    for _, entries in by_ticker.items():
        if len(entries) == 1:
            winners.append(entries[0])
            continue
        ordered = sorted(entries, key=lambda e: (-e.pct_net_assets, e.source_fund))
        winners.append(ordered[0])
        losers.extend(ordered[1:])
    return winners, losers


# --------------------------------------------------------------------------
# Daily-bar liquidity screening (median-based, frozen M46 thresholds)
# --------------------------------------------------------------------------

MIN_MEDIAN_DOLLAR_VOLUME = 75_000_000.0
MIN_MEDIAN_SHARE_VOLUME = 750_000.0
MIN_MEDIAN_CLOSE_PRICE = 10.00
MIN_VALID_SESSION_COVERAGE_PCT = 0.95


@dataclass(frozen=True)
class DailyBar:
    close: float
    volume: int


@dataclass(frozen=True)
class LiquidityProfile:
    symbol: str
    sessions_observed: int
    window_session_count: int
    session_coverage_pct: float
    median_dollar_volume: float
    median_share_volume: float
    median_close_price: float
    eligible: bool
    exclusion_reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.eligible and self.exclusion_reasons:
            raise UniverseSelectionError("an eligible profile cannot carry exclusion reasons")
        if not self.eligible and not self.exclusion_reasons:
            raise UniverseSelectionError("an ineligible profile must record at least one reason")


def compute_liquidity_profile(
    symbol: str, bars: Sequence[DailyBar], *, window_session_count: int,
) -> LiquidityProfile:
    """Median-based liquidity/data-quality profile over a fixed lookback
    window. Coverage is `len(bars) / window_session_count` -- callers must
    only pass bars that fall strictly inside the intended window (the
    fetch stage enforces this via the request's own start/end bounds), so
    this function never needs session-date bookkeeping of its own."""
    if window_session_count <= 0:
        raise UniverseSelectionError("window_session_count must be positive")

    coverage = len(bars) / window_session_count
    if not bars:
        return LiquidityProfile(
            symbol=symbol, sessions_observed=0, window_session_count=window_session_count,
            session_coverage_pct=0.0, median_dollar_volume=0.0, median_share_volume=0.0,
            median_close_price=0.0, eligible=False, exclusion_reasons=("NO_BARS_IN_WINDOW",),
        )

    dollar_vols = [b.close * b.volume for b in bars]
    share_vols = [float(b.volume) for b in bars]
    closes = [b.close for b in bars]

    med_dollar = statistics.median(dollar_vols)
    med_share = statistics.median(share_vols)
    med_close = statistics.median(closes)

    reasons = []
    if coverage < MIN_VALID_SESSION_COVERAGE_PCT:
        reasons.append("SESSION_COVERAGE_BELOW_FLOOR")
    if med_dollar < MIN_MEDIAN_DOLLAR_VOLUME:
        reasons.append("MEDIAN_DOLLAR_VOLUME_BELOW_FLOOR")
    if med_share < MIN_MEDIAN_SHARE_VOLUME:
        reasons.append("MEDIAN_SHARE_VOLUME_BELOW_FLOOR")
    if med_close < MIN_MEDIAN_CLOSE_PRICE:
        reasons.append("MEDIAN_CLOSE_PRICE_BELOW_FLOOR")

    return LiquidityProfile(
        symbol=symbol, sessions_observed=len(bars), window_session_count=window_session_count,
        session_coverage_pct=coverage, median_dollar_volume=med_dollar,
        median_share_volume=med_share, median_close_price=med_close,
        eligible=not reasons, exclusion_reasons=tuple(reasons),
    )


# --------------------------------------------------------------------------
# Deterministic sector-capped selection
# --------------------------------------------------------------------------

MAX_PER_SECTOR = 2
TARGET_PER_BUCKET = 10


@dataclass(frozen=True)
class SelectionCandidate:
    ticker: str
    sector: str
    median_dollar_volume: float


@dataclass(frozen=True)
class SelectionResult:
    selected: tuple[SelectionCandidate, ...]
    excluded_sector_cap: tuple[SelectionCandidate, ...]
    excluded_beyond_target: tuple[SelectionCandidate, ...]
    target_per_bucket: int = TARGET_PER_BUCKET

    @property
    def shortfall(self) -> int:
        return max(0, self.target_per_bucket - len(self.selected))


def select_sector_capped_basket(
    candidates: Sequence[SelectionCandidate],
    *, target_per_bucket: int = TARGET_PER_BUCKET, max_per_sector: int = MAX_PER_SECTOR,
) -> SelectionResult:
    """Sort by median dollar volume descending (ticker ascending
    tie-break), then walk the list taking the first `target_per_bucket`
    candidates that satisfy a `max_per_sector` cap. Purely mechanical --
    no discretionary picks. Does not raise on a shortfall; callers must
    check `.shortfall` and report rather than silently accepting fewer
    than the target or loosening the cap."""
    ordered = sorted(candidates, key=lambda c: (-c.median_dollar_volume, c.ticker))

    selected: list[SelectionCandidate] = []
    excluded_sector_cap: list[SelectionCandidate] = []
    excluded_beyond_target: list[SelectionCandidate] = []
    sector_counts: dict[str, int] = {}

    for c in ordered:
        if len(selected) >= target_per_bucket:
            excluded_beyond_target.append(c)
            continue
        if sector_counts.get(c.sector, 0) >= max_per_sector:
            excluded_sector_cap.append(c)
            continue
        sector_counts[c.sector] = sector_counts.get(c.sector, 0) + 1
        selected.append(c)

    return SelectionResult(
        selected=tuple(selected),
        excluded_sector_cap=tuple(excluded_sector_cap),
        excluded_beyond_target=tuple(excluded_beyond_target),
        target_per_bucket=target_per_bucket,
    )


# --------------------------------------------------------------------------
# Positive-contribution concentration (symbol- and sector-level).
#
# The predeclared M46 criterion is "no single symbol contributes more than
# 50% of positive cumulative R" -- a statement about how concentrated the
# WINNING side of the ledger is, not about net P&L. Dividing a top
# contributor by the NET total is a live bug for any losing lane: net R is
# negative, a positive numerator divided by a negative denominator produces
# a negative "share", which trivially clears any <=50% gate regardless of
# how concentrated the actual gains are. The denominator here is always the
# sum of POSITIVE contributions only; negative and zero contributors never
# enter it.
# --------------------------------------------------------------------------

CONCENTRATION_STATUS_OK = "OK"
CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS = "NO_POSITIVE_CONTRIBUTORS"


@dataclass(frozen=True)
class ConcentrationResult:
    """`share` is `top_contribution / positive_total`, computed only from
    entries with a strictly positive contribution. When no contributor is
    positive, `status` is `NO_POSITIVE_CONTRIBUTORS` and `share` is `None`
    -- callers must treat that as non-applicable, never as an automatic
    pass of a `<=` threshold check (an undefined ratio is not evidence the
    threshold was cleared)."""

    top_key: str | None
    top_contribution: float | None
    positive_total: float
    share: float | None
    status: str

    def __post_init__(self) -> None:
        if self.status == CONCENTRATION_STATUS_OK:
            if self.top_key is None or self.top_contribution is None or self.share is None:
                raise UniverseSelectionError("an OK concentration result requires top_key/top_contribution/share")
        elif self.status == CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS:
            if self.top_key is not None or self.share is not None:
                raise UniverseSelectionError("NO_POSITIVE_CONTRIBUTORS must not carry a top_key or share")
        else:
            raise UniverseSelectionError(f"unknown concentration status: {self.status!r}")


def positive_contribution_concentration(contributions: Mapping[str, float]) -> ConcentrationResult:
    """Reusable for both symbol-level and sector-level concentration --
    same math, different grouping key supplied by the caller. Contributions
    must already be R-multiples (e.g. summed `r_multiple` per symbol/sector
    over closed trades), never raw dollar P&L: the two units are not
    interchangeable (see `trade_quality_from_rows`'s own unit warning in
    `src/orb_regime_diagnostic_research.py`) and mixing them silently would
    reintroduce a unit bug alongside the denominator bug this function
    exists to fix."""
    positive = {k: v for k, v in contributions.items() if v > 0}
    positive_total = sum(positive.values())
    if not positive or positive_total <= 0:
        return ConcentrationResult(
            top_key=None, top_contribution=None, positive_total=0.0, share=None,
            status=CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS,
        )
    ordered = sorted(positive.items(), key=lambda kv: (-kv[1], kv[0]))
    top_key, top_contribution = ordered[0]
    return ConcentrationResult(
        top_key=top_key, top_contribution=top_contribution, positive_total=positive_total,
        share=top_contribution / positive_total, status=CONCENTRATION_STATUS_OK,
    )


# --------------------------------------------------------------------------
# Predeclared feasibility classification (frozen before any P3 result was
# computed). Exactly the 9 conditions from the M46 specification -- no more,
# no fewer. Sector-level concentration is reported for diagnostic purposes
# elsewhere but is NOT one of the 9 gates: only symbol-level concentration
# (#8) is part of the frozen classification.
# --------------------------------------------------------------------------

VERDICT_PROMISING = "PROMISING"
VERDICT_MIXED = "MIXED"
VERDICT_NOT_PROMISING = "NOT_PROMISING"
VERDICT_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

MIN_CLOSED_TRADES_FOR_EVIDENCE = 10
MIN_CLOSED_TRADES_FOR_PROMISING = 20
MIN_PROFIT_FACTOR_R = 1.10
MAX_SYMBOL_SHARE_OF_POSITIVE_R = 0.50
MIN_SECTORS_WITH_CLOSED_TRADES = 4


@dataclass(frozen=True)
class FeasibilityMetrics:
    """The minimal, plain-data input `classify_feasibility` needs for one
    lane (SMALL_MID). Deliberately independent of `TradeDiagnosticRow` and
    any network-oriented script so this stays trivially unit-testable
    against synthetic numbers."""

    closed_trades: int
    expectancy_r: float | None
    profit_factor_r: float | None
    trades_per_month: float
    cumulative_r: float | None
    expectancy_r_excluding_best_symbol: float | None
    sectors_with_closed_trades: int
    symbol_r_contributions: Mapping[str, float]
    mfe_pct_reaching_050r: float | None
    mfe_pct_reaching_100r: float | None


def classify_feasibility(
    *, small_mid: FeasibilityMetrics, large_cap_trades_per_month: float,
    large_cap_mfe_pct_reaching_050r: float | None, large_cap_mfe_pct_reaching_100r: float | None,
) -> dict:
    """PROMISING / MIXED / NOT_PROMISING / INSUFFICIENT_EVIDENCE against the
    9 criteria frozen before the P3 result was computed. Returns a dict with
    `verdict`, the individual `criteria` booleans (plus the raw
    `concentration` result for audit), and `reasons` (criteria that failed,
    only populated for MIXED/NOT_PROMISING)."""
    if small_mid.closed_trades < MIN_CLOSED_TRADES_FOR_EVIDENCE:
        return {
            "verdict": VERDICT_INSUFFICIENT_EVIDENCE,
            "reasons": [f"closed_trades={small_mid.closed_trades} < {MIN_CLOSED_TRADES_FOR_EVIDENCE}"],
            "criteria": {}, "concentration": None,
        }

    concentration = positive_contribution_concentration(small_mid.symbol_r_contributions)
    c8 = concentration.status == CONCENTRATION_STATUS_OK and concentration.share <= MAX_SYMBOL_SHARE_OF_POSITIVE_R

    c1 = small_mid.closed_trades >= MIN_CLOSED_TRADES_FOR_PROMISING
    c2 = (small_mid.expectancy_r or 0) > 0
    c3 = (small_mid.profit_factor_r or 0) > MIN_PROFIT_FACTOR_R
    c4 = small_mid.trades_per_month > large_cap_trades_per_month
    c5 = (small_mid.cumulative_r or 0) > 0
    c6 = (small_mid.expectancy_r_excluding_best_symbol or -1) > 0
    c7 = small_mid.sectors_with_closed_trades >= MIN_SECTORS_WITH_CLOSED_TRADES
    c9a = (small_mid.mfe_pct_reaching_050r or 0) > (large_cap_mfe_pct_reaching_050r or 0)
    c9b = (small_mid.mfe_pct_reaching_100r or 0) > (large_cap_mfe_pct_reaching_100r or 0)
    c9 = c9a or c9b

    criteria = {
        "c1_closed_ge_20": c1, "c2_expectancy_positive": c2, "c3_pf_r_gt_1_10": c3,
        "c4_trades_per_month_gt_large_cap": c4, "c5_cumulative_r_positive": c5,
        "c6_expectancy_excl_best_positive": c6, "c7_sectors_with_trades_ge_4": c7,
        "c8_symbol_share_of_positive_r_le_50pct": c8,
        "c9_mfe_edge_050_or_100": c9, "c9a_mfe_050_edge": c9a, "c9b_mfe_100_edge": c9b,
    }
    all_core_pass = c1 and c2 and c3 and c4 and c5 and c6 and c7 and c8 and c9

    if all_core_pass:
        verdict = VERDICT_PROMISING
        reasons: list[str] = []
    else:
        reasons = [k for k, v in criteria.items() if not v and not k.startswith(("c9a", "c9b"))]
        if c2 and (small_mid.profit_factor_r or 0) > 1.0:
            verdict = VERDICT_MIXED
        else:
            verdict = VERDICT_NOT_PROMISING

    return {"verdict": verdict, "criteria": criteria, "reasons": reasons, "concentration": concentration}


__all__ = [
    "UniverseSelectionError",
    "research_config_for_universe",
    "normalize_issuer_name",
    "HoldingCandidate",
    "MIN_MATERIAL_POSITION_WEIGHT",
    "filter_material_positions",
    "dedupe_share_classes",
    "resolve_cross_fund_overlap",
    "MIN_MEDIAN_DOLLAR_VOLUME",
    "MIN_MEDIAN_SHARE_VOLUME",
    "MIN_MEDIAN_CLOSE_PRICE",
    "MIN_VALID_SESSION_COVERAGE_PCT",
    "DailyBar",
    "LiquidityProfile",
    "compute_liquidity_profile",
    "MAX_PER_SECTOR",
    "TARGET_PER_BUCKET",
    "SelectionCandidate",
    "SelectionResult",
    "select_sector_capped_basket",
    "CONCENTRATION_STATUS_OK",
    "CONCENTRATION_STATUS_NO_POSITIVE_CONTRIBUTORS",
    "ConcentrationResult",
    "positive_contribution_concentration",
    "VERDICT_PROMISING",
    "VERDICT_MIXED",
    "VERDICT_NOT_PROMISING",
    "VERDICT_INSUFFICIENT_EVIDENCE",
    "MIN_CLOSED_TRADES_FOR_EVIDENCE",
    "MIN_CLOSED_TRADES_FOR_PROMISING",
    "MIN_PROFIT_FACTOR_R",
    "MAX_SYMBOL_SHARE_OF_POSITIVE_R",
    "MIN_SECTORS_WITH_CLOSED_TRADES",
    "FeasibilityMetrics",
    "classify_feasibility",
]
