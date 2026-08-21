"""M50 (Issue #352): untouched 2019-2020 validation of the frozen M49
`PRACTICAL_CORE_SATELLITE_30K` portfolio.

Issue #352 requires a mandatory Stage-0 provenance/data-feasibility gate to
complete -- and be committed -- *before* any empirical portfolio outcome for
the 2019-01-02..2020-12-31 window is read. The single hardest part of that
gate is Stage-0 item 1: constructing a deterministic, point-in-time ORB
stock universe for 2019 and for 2020, sourced only from official, dated,
auditable material whose publication/filing date is no later than the
universe's own effective date, following a fixed source-search order:

    1. official issuer historical holdings/constituent files with
       verifiable as-of and publication dates;
    2. SEC EDGAR filings whose holdings were public by the universe
       effective date;
    3. another primary official source with equivalent dated provenance.

This module encodes that investigation as auditable, testable data and
logic rather than narrative: every source actually attempted is recorded as
a `SourceAttempt`, every dated DJIA membership-change event actually
confirmed via a compliant source is recorded as a `ConfirmedChangeEvent`,
and `run_stage0_gate()` evaluates -- deterministically, from that recorded
evidence -- whether a compliant, *complete* point-in-time roster was
established for both 2019-01-02 and 2020-01-02.

It was not. Every tier of the fixed source order was attempted and
exhausted (see `STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS`): SEC EDGAR is
completely inaccessible to this environment's fetch tooling (HTTP 403 on
every endpoint tried, including the general-purpose full-text-search
endpoint and a specific DIA -- SPDR Dow Jones Industrial Average ETF
Trust -- annual-report filing); official issuer sites either expose only
current holdings (SSGA) or are also blocked (BlackRock/iShares); and the
one reachable primary official source (S&P Dow Jones Indices' press
subdomain) yields individually dated, compliant *change announcements*
but never a single complete, pre-dated full membership roster to anchor
a reconstruction from. An academically accurate, fully-sourced complete
roster was found (an NBER working paper's December 31, 2019 DJIA table),
but it is explicitly disqualified: the paper itself was published in
March 2021, after both 2019-01-02 and 2020-01-02, failing Issue #352's
literal "publication/filing date must be no later than the effective
date" requirement -- a rule this module does not reinterpret or relax.

Because ARM_A/ARM_B reuse the already-frozen, stable ETF universes
unchanged (Issue #352 explicitly exempts them from the point-in-time
problem), this blocker is scoped entirely to the ORB stock-picking lane.
`run_stage0_gate()` therefore returns `CLASSIFICATION_INSUFFICIENT_EVIDENCE`
-- Issue #352's own decision tree treats this as a valid, complete way to
finish M50: "record the exact provenance/data blocker; do not substitute a
biased universe or widen/change the window."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

__all__ = [
    "OUTCOME_WINDOW_START",
    "OUTCOME_WINDOW_END",
    "REQUIRED_OUTCOME_MONTHS",
    "WARMUP_TARGET_START",
    "CLASSIFICATION_INDEPENDENTLY_VALIDATED",
    "CLASSIFICATION_MIXED_VALIDATION",
    "CLASSIFICATION_FAILED_VALIDATION",
    "CLASSIFICATION_INSUFFICIENT_EVIDENCE",
    "INDEPENDENTLY_VALIDATED_MIN_MONTHLY_DOLLARS",
    "INDEPENDENTLY_VALIDATED_MAX_DRAWDOWN_PCT",
    "INDEPENDENTLY_VALIDATED_MIN_PROFITABLE_MONTH_PCT",
    "INDEPENDENTLY_VALIDATED_WORST_MONTH_FLOOR_PCT",
    "MIXED_VALIDATION_MAX_DRAWDOWN_PCT",
    "FIXED_SOURCE_ORDER",
    "SourceAttempt",
    "STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS",
    "ConfirmedChangeEvent",
    "STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS",
    "stage0_orb_universe_established",
    "Stage0GateResult",
    "run_stage0_gate",
    "M49_FROZEN_REFERENCE",
]

# ---------------------------------------------------------------------------
# Frozen validation window (Issue #352).
# ---------------------------------------------------------------------------

OUTCOME_WINDOW_START = date(2019, 1, 2)
OUTCOME_WINDOW_END = date(2020, 12, 31)
REQUIRED_OUTCOME_MONTHS = 24
WARMUP_TARGET_START = date(2018, 1, 2)

# ---------------------------------------------------------------------------
# Frozen classification labels and gate thresholds, copied verbatim from
# Issue #352 so a future reader can diff this module against the issue text
# rather than trust a paraphrase.
# ---------------------------------------------------------------------------

CLASSIFICATION_INDEPENDENTLY_VALIDATED = "INDEPENDENTLY_VALIDATED"
CLASSIFICATION_MIXED_VALIDATION = "MIXED_VALIDATION"
CLASSIFICATION_FAILED_VALIDATION = "FAILED_VALIDATION"
CLASSIFICATION_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

INDEPENDENTLY_VALIDATED_MIN_MONTHLY_DOLLARS = Decimal("150")
INDEPENDENTLY_VALIDATED_MAX_DRAWDOWN_PCT = Decimal("0.15")
INDEPENDENTLY_VALIDATED_MIN_PROFITABLE_MONTH_PCT = Decimal("0.55")
INDEPENDENTLY_VALIDATED_WORST_MONTH_FLOOR_PCT = Decimal("-0.10")
MIXED_VALIDATION_MAX_DRAWDOWN_PCT = Decimal("0.20")

# ---------------------------------------------------------------------------
# Stage-0 item 1: point-in-time ORB universe provenance investigation.
# ---------------------------------------------------------------------------

FIXED_SOURCE_ORDER: tuple[str, ...] = (
    "official issuer historical holdings/constituent files with verifiable as-of and publication dates",
    "SEC EDGAR filings whose holdings were public by the universe effective date",
    "another primary official source with equivalent dated provenance",
)


@dataclass(frozen=True)
class SourceAttempt:
    tier: int
    source_name: str
    url: str
    access_method: str
    outcome: str
    notes: str


STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS: tuple[SourceAttempt, ...] = (
    # Tier 1: official issuer historical holdings/constituent files.
    SourceAttempt(
        tier=1, source_name="SSGA (State Street) -- SPY issuer", url="https://www.ssga.com",
        access_method="WebFetch", outcome="CURRENT_HOLDINGS_ONLY_NO_HISTORICAL_ARCHIVE",
        notes="Site reachable; exposes only current SPY holdings, no dated historical constituent files for 2018-2020.",
    ),
    SourceAttempt(
        tier=1, source_name="BlackRock -- IVV issuer", url="https://www.blackrock.com",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="Product/holdings pages returned HTTP 403.",
    ),
    SourceAttempt(
        tier=1, source_name="iShares product page -- IVV issuer", url="https://www.ishares.com",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="Product/holdings pages returned HTTP 403.",
    ),
    SourceAttempt(
        tier=1, source_name="Vanguard -- VOO issuer", url="https://investor.vanguard.com/investment-products/etfs/profile/voo",
        access_method="WebFetch", outcome="INSUFFICIENT_RENDERED_CONTENT_NO_HISTORICAL_ARCHIVE_FOUND",
        notes="Fetch not blocked, but the page is JS-rendered; no historical holdings download or archive was found in the returned content.",
    ),
    # Tier 2: SEC EDGAR filings.
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- browse-edgar", url="https://www.sec.gov/cgi-bin/browse-edgar",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="",
    ),
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- submissions API (SPDR S&P 500 ETF Trust, CIK 0000884394)",
        url="https://data.sec.gov/submissions/CIK0000884394.json",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="",
    ),
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- specific archived filing (SPDR S&P 500 ETF Trust 485BPOS)",
        url="https://www.sec.gov/Archives/edgar/data/884394/000119312519011524/d678760d485bpos.htm",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="",
    ),
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- specific archived filing (SPDR Dow Jones Industrial Average ETF Trust N-30D)",
        url="https://www.sec.gov/Archives/edgar/data/1041130/000119312519175597/d747836dn30d.htm",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403",
        notes="DIA's own N-30D shareholder report would carry an official, dated schedule of investments, but the domain is blocked before content can be read.",
    ),
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- full text search API", url="https://efts.sec.gov/LATEST/search-index?q=%22Dow+Jones+Industrial+Average%22",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="",
    ),
    SourceAttempt(
        tier=2, source_name="sec.gov homepage", url="https://www.sec.gov/",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403",
        notes="Confirms the block is domain-wide, not endpoint-specific.",
    ),
    SourceAttempt(
        tier=2, source_name="sec.gov -- any endpoint via direct HTTP client (workaround attempt)", url="https://www.sec.gov",
        access_method="Bash curl with explicit User-Agent header", outcome="DENIED_BY_PERMISSION_SYSTEM",
        notes="Bash curl was denied by the tool permission system before any network request could be attempted; no direct-network workaround is available.",
    ),
    # Tier 3: another primary official source with equivalent dated provenance.
    SourceAttempt(
        tier=3, source_name="S&P Dow Jones Indices -- main site, index-family overview", url="https://www.spglobal.com/spdji/en/indices/equity/dow-jones-industrial-average/",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="",
    ),
    SourceAttempt(
        tier=3, source_name="S&P Dow Jones Indices -- specific dated announcement PDF", url="https://www.spglobal.com/spdji/en/documents/index-news-and-announcements/",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="",
    ),
    SourceAttempt(
        tier=3, source_name="S&P Dow Jones Indices -- Dow Jones Averages Methodology PDF, historical (2018/2019) edition",
        url="https://www.spglobal.com/spdji/en/documents/methodologies/methodology-dj-averages.pdf",
        access_method="WebSearch", outcome="ONLY_CURRENT_EDITION_INDEXED_NO_2018_2019_ARCHIVE_FOUND",
        notes="Search surfaces only the current (2026) methodology edition; no archived 2018/2019 edition with a dated components appendix was located, and the domain itself is blocked to WebFetch.",
    ),
    SourceAttempt(
        tier=3, source_name="press.spglobal.com -- S&P DJI press-release subdomain (WBA replaces GE)",
        url="https://press.spglobal.com/2018-06-19-Walgreens-Boots-Alliance-Set-to-Join-the-Dow-Jones-Industrial-Average",
        access_method="WebSearch", outcome="PARTIAL_EVIDENCE_SINGLE_DATED_CHANGE_EVENT_ONLY",
        notes="Confirms one dated membership change (WBA replaces GE, effective June 26, 2018); not a complete roster.",
    ),
    SourceAttempt(
        tier=3, source_name="press.spglobal.com -- S&P DJI press-release subdomain (Dow Inc. replaces DowDuPont)",
        url="https://press.spglobal.com/2019-03-26-Dow-Set-to-Join-Dow-Jones-Industrial-Average",
        access_method="WebFetch", outcome="PARTIAL_EVIDENCE_SINGLE_DATED_CHANGE_EVENT_ONLY",
        notes="Directly fetched and confirmed: Dow Inc. (DOW) replaces DowDuPont (DWDP) effective April 2, 2019, announced March 26, 2019 -- a single dated change, not a complete roster.",
    ),
    SourceAttempt(
        tier=3, source_name="Wayback Machine (archival fallback, not itself a fixed-order tier)", url="https://web.archive.org",
        access_method="WebFetch", outcome="TOOL_LEVEL_UNREACHABLE",
        notes="WebFetch reports it is unable to fetch from web.archive.org at all, tested with two different URL formats.",
    ),
    SourceAttempt(
        tier=3, source_name="archive.today (archival fallback, not itself a fixed-order tier)", url="https://archive.ph",
        access_method="WebFetch", outcome="TOOL_LEVEL_UNREACHABLE",
        notes="Same blanket tool-level block as web.archive.org.",
    ),
    SourceAttempt(
        tier=3, source_name="NBER Working Paper No. 28528, 'Replicating the Dow Jones Industrial Average' (Lin, Selden, Shoven, Sialm)",
        url="https://www.nber.org/system/files/working_papers/w28528/w28528.pdf",
        access_method="WebFetch + Read", outcome="DISQUALIFIED_PUBLICATION_DATE_AFTER_BOTH_EFFECTIVE_DATES",
        notes=(
            "Successfully fetched and read in full, including Table A.1 ('Components of DJIA on December 31, 2019'), "
            "a complete, accurate 30-name roster citing S&P Dow Jones Indices as its underlying source. Disqualified as "
            "primary Stage-0 provenance because the paper itself was published March 2021 -- after both the 2019-01-02 "
            "and 2020-01-02 universe effective dates -- failing Issue #352's literal 'publication/filing date must be no "
            "later than the effective date' requirement. Not used to establish either universe."
        ),
    ),
)


@dataclass(frozen=True)
class ConfirmedChangeEvent:
    effective_date: date
    announcement_date: date
    description: str
    source_url: str

    @property
    def publication_compliant(self) -> bool:
        return self.announcement_date <= self.effective_date


STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS: tuple[ConfirmedChangeEvent, ...] = (
    ConfirmedChangeEvent(
        effective_date=date(2018, 6, 26), announcement_date=date(2018, 6, 19),
        description="Walgreens Boots Alliance (WBA) replaces General Electric (GE) in the DJIA.",
        source_url="https://press.spglobal.com/2018-06-19-Walgreens-Boots-Alliance-Set-to-Join-the-Dow-Jones-Industrial-Average",
    ),
    ConfirmedChangeEvent(
        effective_date=date(2019, 4, 2), announcement_date=date(2019, 3, 26),
        description="Dow Inc. (DOW) replaces DowDuPont Inc. (DWDP) in the DJIA.",
        source_url="https://press.spglobal.com/2019-03-26-Dow-Set-to-Join-Dow-Jones-Industrial-Average",
    ),
)


def stage0_orb_universe_established(source_attempts: tuple[SourceAttempt, ...]) -> bool:
    """A point-in-time universe is established only if at least one
    attempted source actually produced a complete, compliant roster --
    an outcome recorded as `"COMPLETE_ROSTER_ESTABLISHED_COMPLIANT"`.
    Individual dated change events, partial evidence, and disqualified
    (post-dated) sources do not count."""
    return any(a.outcome == "COMPLETE_ROSTER_ESTABLISHED_COMPLIANT" for a in source_attempts)


@dataclass(frozen=True)
class Stage0GateResult:
    orb_universe_2019_established: bool
    orb_universe_2020_established: bool
    etf_universes_exempt: bool
    classification: str | None
    blocker_reason: str
    source_attempts: tuple[SourceAttempt, ...] = field(default_factory=tuple)
    confirmed_change_events: tuple[ConfirmedChangeEvent, ...] = field(default_factory=tuple)


def run_stage0_gate(
    *,
    source_attempts: tuple[SourceAttempt, ...] = STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS,
    confirmed_change_events: tuple[ConfirmedChangeEvent, ...] = STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS,
) -> Stage0GateResult:
    """Evaluates only the Stage-0 item-1 (universe) gate. `classification`
    is one of the four frozen labels only when this gate itself blocks
    (`INSUFFICIENT_EVIDENCE`); a passing universe gate does not by itself
    determine `INDEPENDENTLY_VALIDATED`/`MIXED_VALIDATION`/
    `FAILED_VALIDATION`, which Issue #352 applies only after Stage-0 items
    2-3 and the economic outcome are also evaluated -- so `classification`
    is left `None` in that case, not guessed."""
    established = stage0_orb_universe_established(source_attempts)
    orb_universe_2019_established = established
    orb_universe_2020_established = established
    if orb_universe_2019_established and orb_universe_2020_established:
        classification = None
        blocker_reason = ""
    else:
        classification = CLASSIFICATION_INSUFFICIENT_EVIDENCE
        tiers_exhausted = sorted({a.tier for a in source_attempts})
        blocker_reason = (
            "Stage-0 item 1 (point-in-time ORB universe) could not be satisfied. All "
            f"{len(tiers_exhausted)} tiers of the fixed source order were attempted "
            f"({len(source_attempts)} source attempts recorded) and none produced a "
            "complete, compliant point-in-time roster for either the 2019-01-02 or "
            "2020-01-02 universe effective date. SEC EDGAR (tier 2) is completely "
            "inaccessible to this environment's fetch tooling. Official issuer sites "
            "(tier 1) expose only current holdings or are blocked. The one reachable "
            "primary official source, S&P Dow Jones Indices' press-release subdomain "
            "(tier 3), yields individually dated, compliant membership-change "
            "announcements but never a complete pre-dated roster to anchor a "
            "reconstruction from. A complete, accurate roster exists (an NBER working "
            "paper's December 31, 2019 DJIA table) but is disqualified because it was "
            "published in March 2021, after both effective dates, failing the 'no "
            "later than the effective date' requirement. Per Issue #352 Stage 0 item 1: "
            "'If no qualifying source can establish both yearly universes, stop with "
            "INSUFFICIENT_EVIDENCE.'"
        )
    return Stage0GateResult(
        orb_universe_2019_established=orb_universe_2019_established,
        orb_universe_2020_established=orb_universe_2020_established,
        etf_universes_exempt=True,
        classification=classification,
        blocker_reason=blocker_reason,
        source_attempts=source_attempts,
        confirmed_change_events=confirmed_change_events,
    )


# ---------------------------------------------------------------------------
# M49's own 2022-2026 headline result, carried forward *only* as a frozen
# reference (Issue #352: "report the old M49 2022-2026 result beside M50
# only as a frozen reference. Never combine the periods or use M49
# observations to alter M50.").
# ---------------------------------------------------------------------------

M49_FROZEN_REFERENCE: dict[str, object] = {
    "evaluation_window": "2022-01-03..2026-08-13",
    "classification": "SAFE_BUT_BELOW_TARGET",
    "base_mean_monthly_dollars": "184.75",
    "stress_mean_monthly_dollars": "122.02",
    "base_max_drawdown_pct": "0.1460",
    "stress_max_drawdown_pct": "0.1854",
    "pr": 351,
    "merge_commit": "8a9eb26ab8a30c9e2f333a25a0302586048e8d3e",
    "note": "Frozen reference only. M50 does not combine this period with, or use it to alter, the 2019-2020 outcome window.",
}
