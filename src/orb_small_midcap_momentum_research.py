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

import re
import statistics
from dataclasses import dataclass, field
from typing import Mapping, Sequence


class UniverseSelectionError(RuntimeError):
    """Any refusal in the M46 universe-selection pipeline."""


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


__all__ = [
    "UniverseSelectionError",
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
]
