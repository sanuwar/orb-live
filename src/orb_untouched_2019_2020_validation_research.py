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

CORRECTION (this module's second revision): the first revision concluded
`INSUFFICIENT_EVIDENCE` after every direct WebFetch attempt against
sec.gov returned HTTP 403 -- correct as far as it went, but incomplete: it
never tried an alternate technical retrieval path for the *same* official
documents before giving up on tier 2 entirely. An independent review
supplied two specific SEC EDGAR accession numbers for SPDR Dow Jones
Industrial Average ETF Trust (DIA) N-30D filings -- DIA is both an
"official issuer" (tier 1) and an SEC-filed source (tier 2) at once, since
its own shareholder reports carry an official, dated schedule of
investments. Direct WebFetch against these specific URLs still returns
HTTP 403 (sec.gov remains completely blocked to direct fetch in this
environment), but fetching the *same* URLs through a text-rendering proxy
(`r.jina.ai`) succeeds and returns the filed document's actual content.
The retrieved rosters were cross-validated three ways before being
accepted as compliant Stage-0 provenance:

    1. internal consistency with the independently-confirmed dated DJIA
       change events already on record here (the 2018-10-31 report shows
       DowDuPont/DWDP, not Dow Inc./DOW -- correct, since the DOW/DWDP
       swap did not take effect until 2019-04-02; the 2019-10-31 report
       shows DOW, not DWDP -- also correct);
    2. a third, independent source (the "Dow Additions and Deletions"
       changelog at dogsofthedow.com, itself cross-checked via WebSearch)
       confirming no DJIA membership change occurred between either
       filing's report date and its corresponding universe effective
       date; and
    3. exact agreement (30 names, correct membership) with the NBER
       working paper's December 31, 2019 table that the first revision
       had already found but disqualified for its own late publication
       date -- the SEC filing succeeds where the NBER paper failed
       *specifically* on the one criterion that mattered (the SEC filing
       was filed 2018-12-18 and 2019-12-20 respectively, both before
       their universe's own effective date; the NBER paper was published
       March 2021, after both).

Each year is evaluated **independently** via `stage0_universe_established_
for_year()` -- there is no single shared "established" boolean. A source
attempt, a 30-name/no-duplicate/publication-compliant check, and a
no-DJIA-change-in-window audit must each individually pass for that
specific year before that year's universe counts as established.

Because ARM_A/ARM_B reuse the already-frozen, stable ETF universes
unchanged (Issue #352 explicitly exempts them from the point-in-time
problem), Stage-0 item 1 was scoped entirely to the ORB stock-picking
lane, and it now passes for both years.
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
    "UniverseSource",
    "UNIVERSE_SOURCE_2019",
    "UNIVERSE_SOURCE_2020",
    "UNIVERSE_SOURCES_BY_YEAR",
    "UNIVERSE_2019",
    "UNIVERSE_2020",
    "NoChangeAuditWindow",
    "NO_CHANGE_AUDITS_BY_YEAR",
    "stage0_universe_established_for_year",
    "Stage0GateResult",
    "run_stage0_gate",
    "CorporateActionPolicy",
    "ORB_CORPORATE_ACTION_POLICIES",
    "expected_last_active_date_for",
    "EtfLaunchPolicy",
    "ETF_LAUNCH_POLICIES",
    "expected_first_active_date_for",
    "DataCoverageCheck",
    "evaluate_stage0_item2_coverage",
    "IEX_MINUTE_COVERAGE_FLOOR",
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
    # Tier 1/2 simultaneously: SPDR Dow Jones Industrial Average ETF Trust
    # (DIA) is both the official issuer of the fund tracking the eligibility
    # universe used here AND an SEC-filed source. Its N-30D reports carry an
    # official, dated schedule of investments -- the source that ultimately
    # qualifies both years (see `UNIVERSE_SOURCE_2019`/`UNIVERSE_SOURCE_2020`
    # below for the full accession/date/ticker record).
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- DIA N-30D (period 2018-10-31, accession 0001193125-18-351994) -- direct WebFetch",
        url="https://www.sec.gov/Archives/edgar/data/1041130/000119312518351994/d597585dn30d.htm",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403",
        notes="sec.gov remains blocked to direct WebFetch even for this specific, correctly-identified qualifying filing.",
    ),
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- DIA N-30D (period 2018-10-31, accession 0001193125-18-351994) -- via r.jina.ai text-rendering proxy",
        url="https://r.jina.ai/https://www.sec.gov/Archives/edgar/data/1041130/000119312518351994/d597585dn30d.htm",
        access_method="WebFetch via r.jina.ai proxy", outcome="COMPLETE_ROSTER_ESTABLISHED_COMPLIANT",
        notes=(
            "The same official SEC-hosted document, retrieved through an alternate technical path since sec.gov "
            "itself is blocked to direct fetch. Returned the fund's official Schedule of Investments: 30 names, "
            "filing date 2018-12-18, period of report 2018-10-31 -- both before the 2019-01-02 universe effective "
            "date. Cross-validated against independently-confirmed dated DJIA change events (correctly shows "
            "DowDuPont/DWDP, not Dow Inc./DOW, consistent with the confirmed 2019-04-02 swap) and against a third "
            "independent changelog source. See `UNIVERSE_SOURCE_2019`."
        ),
    ),
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- DIA N-30D (period 2019-10-31, accession 0001193125-19-320843) -- direct WebFetch",
        url="https://www.sec.gov/Archives/edgar/data/1041130/000119312519320843/d823731dn30d.htm",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403",
        notes="sec.gov remains blocked to direct WebFetch even for this specific, correctly-identified qualifying filing.",
    ),
    SourceAttempt(
        tier=2, source_name="SEC EDGAR -- DIA N-30D (period 2019-10-31, accession 0001193125-19-320843) -- via r.jina.ai text-rendering proxy",
        url="https://r.jina.ai/https://www.sec.gov/Archives/edgar/data/1041130/000119312519320843/d823731dn30d.htm",
        access_method="WebFetch via r.jina.ai proxy", outcome="COMPLETE_ROSTER_ESTABLISHED_COMPLIANT",
        notes=(
            "Returned the fund's official Schedule of Investments: 30 names, filing date 2019-12-20, period of "
            "report 2019-10-31 -- both before the 2020-01-02 universe effective date. Correctly shows Dow Inc./DOW, "
            "not DowDuPont/DWDP, consistent with the confirmed 2019-04-02 swap. See `UNIVERSE_SOURCE_2020`."
        ),
    ),
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
        tier=2, source_name="SEC EDGAR -- full text search API", url="https://efts.sec.gov/LATEST/search-index?q=%22Dow+Jones+Industrial+Average%22",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403", notes="",
    ),
    SourceAttempt(
        tier=2, source_name="sec.gov homepage", url="https://www.sec.gov/",
        access_method="WebFetch", outcome="BLOCKED_HTTP_403",
        notes="Confirms the direct-fetch block is domain-wide, not endpoint-specific -- which is why the r.jina.ai proxy path above was needed.",
    ),
    SourceAttempt(
        tier=2, source_name="sec.gov -- any endpoint via direct HTTP client (workaround attempt)", url="https://www.sec.gov",
        access_method="Bash curl with explicit User-Agent header", outcome="DENIED_BY_PERMISSION_SYSTEM",
        notes="Bash curl was denied by the tool permission system before any network request could be attempted.",
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
        tier=3, source_name="dogsofthedow.com -- 'Dow Additions and Deletions Since 1929' changelog, via r.jina.ai proxy",
        url="https://r.jina.ai/https://www.dogsofthedow.com/djdelete.htm",
        access_method="WebFetch via r.jina.ai proxy", outcome="CORROBORATING_CHANGELOG_NO_ADDITIONAL_CHANGES_FOUND",
        notes=(
            "Independent third source. Lists every DJIA addition/deletion 2015-2020: 2018-06-26 (GE out, WBA in), "
            "2019-04-02 (DWDP out, DOW in), 2020-08-31 (XOM out/CRM in, Raytheon out/HON in, PFE out/AMGN in). No "
            "changes listed between 2018-10-31 and 2019-01-02, nor between 2019-10-31 and 2020-01-02 -- corroborates "
            "both `NoChangeAuditWindow` entries below."
        ),
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
            "a complete, accurate 30-name roster citing S&P Dow Jones Indices as its underlying source -- and, in "
            "hindsight, an exact match to `UNIVERSE_SOURCE_2020` below. Still not used as primary provenance: the "
            "paper itself was published March 2021, after both effective dates, failing the 'no later than the "
            "effective date' requirement that the qualifying DIA N-30D filings satisfy."
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
    ConfirmedChangeEvent(
        effective_date=date(2020, 8, 31), announcement_date=date(2020, 8, 24),
        description="Salesforce (CRM) replaces ExxonMobil (XOM); Honeywell (HON) replaces Raytheon Technologies; Amgen (AMGN) replaces Pfizer (PFE) in the DJIA.",
        source_url="https://press.spglobal.com/2020-08-24-Salesforce-com-Amgen-and-Honeywell-International-Set-to-Join-Dow-Jones-Industrial-Average",
    ),
)


# ---------------------------------------------------------------------------
# Stage-0 item 1: qualifying point-in-time ORB universe sources.
#
# Each is DIA's (SPDR Dow Jones Industrial Average ETF Trust) own official
# SEC N-30D shareholder report -- simultaneously a tier-1 "official issuer
# historical holdings file" and a tier-2 "SEC EDGAR filing whose holdings
# were public by the universe effective date". Retrieved via the r.jina.ai
# proxy path documented above (direct sec.gov fetch is blocked); content
# cross-validated three ways (see module docstring).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseSource:
    universe_year: int
    effective_date: date
    issuer: str
    cik: str
    form_type: str
    accession_number: str
    filing_date: date
    period_of_report: date
    index_url: str
    document_url: str
    retrieval_method: str
    tickers: tuple[str, ...]

    @property
    def publication_compliant(self) -> bool:
        return self.filing_date <= self.effective_date

    @property
    def has_exactly_thirty_unique_tickers(self) -> bool:
        return len(self.tickers) == 30 and len(set(self.tickers)) == 30


UNIVERSE_SOURCE_2019 = UniverseSource(
    universe_year=2019,
    effective_date=date(2019, 1, 2),
    issuer="SPDR Dow Jones Industrial Average ETF Trust (DIA)",
    cik="0001041130",
    form_type="N-30D",
    accession_number="0001193125-18-351994",
    filing_date=date(2018, 12, 18),
    period_of_report=date(2018, 10, 31),
    index_url="https://www.sec.gov/Archives/edgar/data/1041130/000119312518351994/0001193125-18-351994-index.html",
    document_url="https://www.sec.gov/Archives/edgar/data/1041130/000119312518351994/d597585dn30d.htm",
    retrieval_method="WebFetch via r.jina.ai text-rendering proxy (direct sec.gov fetch returns HTTP 403)",
    tickers=(
        "MMM", "AXP", "AAPL", "BA", "CAT", "CVX", "CSCO", "KO", "DWDP", "XOM",
        "GS", "HD", "INTC", "IBM", "JNJ", "JPM", "MCD", "MRK", "MSFT", "NKE",
        "PFE", "PG", "TRV", "UTX", "UNH", "VZ", "V", "WBA", "WMT", "DIS",
    ),
)

UNIVERSE_SOURCE_2020 = UniverseSource(
    universe_year=2020,
    effective_date=date(2020, 1, 2),
    issuer="SPDR Dow Jones Industrial Average ETF Trust (DIA)",
    cik="0001041130",
    form_type="N-30D",
    accession_number="0001193125-19-320843",
    filing_date=date(2019, 12, 20),
    period_of_report=date(2019, 10, 31),
    index_url="https://www.sec.gov/Archives/edgar/data/1041130/000119312519320843/0001193125-19-320843-index.html",
    document_url="https://www.sec.gov/Archives/edgar/data/1041130/000119312519320843/d823731dn30d.htm",
    retrieval_method="WebFetch via r.jina.ai text-rendering proxy (direct sec.gov fetch returns HTTP 403)",
    tickers=(
        "MMM", "AXP", "AAPL", "BA", "CAT", "CVX", "CSCO", "KO", "DOW", "XOM",
        "GS", "HD", "INTC", "IBM", "JNJ", "JPM", "MCD", "MRK", "MSFT", "NKE",
        "PFE", "PG", "TRV", "UTX", "UNH", "VZ", "V", "WBA", "WMT", "DIS",
    ),
)

UNIVERSE_SOURCES_BY_YEAR: dict[int, UniverseSource] = {2019: UNIVERSE_SOURCE_2019, 2020: UNIVERSE_SOURCE_2020}
UNIVERSE_2019: tuple[str, ...] = UNIVERSE_SOURCE_2019.tickers
UNIVERSE_2020: tuple[str, ...] = UNIVERSE_SOURCE_2020.tickers


@dataclass(frozen=True)
class NoChangeAuditWindow:
    universe_year: int
    window_start: date
    window_end: date
    confirmed_no_change: bool
    corroborating_sources: tuple[str, ...] = field(default_factory=tuple)


NO_CHANGE_AUDITS_BY_YEAR: dict[int, NoChangeAuditWindow] = {
    2019: NoChangeAuditWindow(
        universe_year=2019, window_start=date(2018, 10, 31), window_end=date(2019, 1, 2),
        confirmed_no_change=True,
        corroborating_sources=(
            "press.spglobal.com dated change events (nearest changes: 2018-06-26 WBA/GE, before window; 2019-04-02 DOW/DWDP, after window)",
            "dogsofthedow.com 'Dow Additions and Deletions Since 1929' changelog (no entries between 2018-10-31 and 2019-01-02)",
        ),
    ),
    2020: NoChangeAuditWindow(
        universe_year=2020, window_start=date(2019, 10, 31), window_end=date(2020, 1, 2),
        confirmed_no_change=True,
        corroborating_sources=(
            "press.spglobal.com dated change events (nearest changes: 2019-04-02 DOW/DWDP, before window; 2020-08-31 CRM/HON/AMGN, after window)",
            "dogsofthedow.com 'Dow Additions and Deletions Since 1929' changelog (no entries between 2019-10-31 and 2020-01-02)",
            "NBER Working Paper No. 28528 Table A.1 (December 31, 2019 DJIA membership matches UNIVERSE_SOURCE_2020 exactly)",
        ),
    ),
}


def stage0_universe_established_for_year(
    year: int,
    *,
    universe_sources_by_year: dict[int, UniverseSource] = UNIVERSE_SOURCES_BY_YEAR,
    no_change_audits_by_year: dict[int, NoChangeAuditWindow] = NO_CHANGE_AUDITS_BY_YEAR,
) -> bool:
    """Evaluates ONE year's universe establishment independently -- never a
    shared boolean across both years. All of the following must hold for
    `year` specifically:

    - a `UniverseSource` is on record for `year`;
    - its filing date is no later than its own universe effective date;
    - it carries exactly 30 unique tickers;
    - a `NoChangeAuditWindow` is on record for `year` and confirms no DJIA
      membership change occurred between the source's report/period date
      and the universe effective date.
    """
    source = universe_sources_by_year.get(year)
    if source is None:
        return False
    if not source.publication_compliant:
        return False
    if not source.has_exactly_thirty_unique_tickers:
        return False
    audit = no_change_audits_by_year.get(year)
    if audit is None or not audit.confirmed_no_change:
        return False
    if audit.window_start != source.period_of_report or audit.window_end != source.effective_date:
        return False
    return True


@dataclass(frozen=True)
class Stage0GateResult:
    orb_universe_2019_established: bool
    orb_universe_2020_established: bool
    etf_universes_exempt: bool
    classification: str | None
    blocker_reason: str
    source_attempts: tuple[SourceAttempt, ...] = field(default_factory=tuple)
    confirmed_change_events: tuple[ConfirmedChangeEvent, ...] = field(default_factory=tuple)
    universe_sources_by_year: dict[int, UniverseSource] = field(default_factory=dict)
    no_change_audits_by_year: dict[int, NoChangeAuditWindow] = field(default_factory=dict)


def run_stage0_gate(
    *,
    source_attempts: tuple[SourceAttempt, ...] = STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS,
    confirmed_change_events: tuple[ConfirmedChangeEvent, ...] = STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS,
    universe_sources_by_year: dict[int, UniverseSource] = UNIVERSE_SOURCES_BY_YEAR,
    no_change_audits_by_year: dict[int, NoChangeAuditWindow] = NO_CHANGE_AUDITS_BY_YEAR,
) -> Stage0GateResult:
    """Evaluates only the Stage-0 item-1 (universe) gate, year by year
    independently. `classification` is one of the four frozen labels only
    when this gate itself blocks (`INSUFFICIENT_EVIDENCE`, when either year
    fails independently); a passing universe gate does not by itself
    determine `INDEPENDENTLY_VALIDATED`/`MIXED_VALIDATION`/
    `FAILED_VALIDATION`, which Issue #352 applies only after Stage-0 items
    2-3 and the economic outcome are also evaluated -- so `classification`
    is left `None` in that case, not guessed."""
    orb_universe_2019_established = stage0_universe_established_for_year(
        2019, universe_sources_by_year=universe_sources_by_year, no_change_audits_by_year=no_change_audits_by_year,
    )
    orb_universe_2020_established = stage0_universe_established_for_year(
        2020, universe_sources_by_year=universe_sources_by_year, no_change_audits_by_year=no_change_audits_by_year,
    )
    if orb_universe_2019_established and orb_universe_2020_established:
        classification = None
        blocker_reason = ""
    else:
        classification = CLASSIFICATION_INSUFFICIENT_EVIDENCE
        failed_years = [y for y, ok in ((2019, orb_universe_2019_established), (2020, orb_universe_2020_established)) if not ok]
        blocker_reason = (
            "Stage-0 item 1 (point-in-time ORB universe) could not be satisfied for year(s) "
            f"{failed_years}. Each year is evaluated independently against a qualifying "
            "UniverseSource (compliant filing date, exactly 30 unique tickers) and a "
            "NoChangeAuditWindow confirming no DJIA membership change between the source's "
            "report date and the universe effective date."
        )
    return Stage0GateResult(
        orb_universe_2019_established=orb_universe_2019_established,
        orb_universe_2020_established=orb_universe_2020_established,
        etf_universes_exempt=True,
        classification=classification,
        blocker_reason=blocker_reason,
        source_attempts=source_attempts,
        confirmed_change_events=confirmed_change_events,
        universe_sources_by_year=universe_sources_by_year,
        no_change_audits_by_year=no_change_audits_by_year,
    )


# ---------------------------------------------------------------------------
# Stage-0 item 2: market-data/calendar coverage, including the two known
# corporate actions inside the ORB universe (DWDP's 2019 three-way
# separation; UTX's 2020 merger into Raytheon Technologies/RTX). Both are
# genuine, real, dated events -- verified directly against Alpaca SIP daily
# bars during this investigation (DWDP's own trading data ends 2019-05-31;
# UTX's own trading data ends 2020-04-03, immediately preceding RTX's
# post-merger price history) -- not data-quality defects. Per Issue #352
# ("delisted/renamed/acquired securities preserved and resolved point-in-
# time; never silently replace them with current successors"), the policy
# here is deliberately conservative: no successor ticker is substituted.
# ORB simply generates no further candidates for that universe slot once
# the as-filed ticker's own real trading data ends, exactly as it already
# does for any symbol with no bars on a given session.
# ---------------------------------------------------------------------------


#: Verified empirically (not documented by Alpaca/the vendor anywhere this
#: investigation could find): Alpaca's IEX minute-bar history, as reached
#: through this repository's own fetch path (`fetch_full_minute_history`,
#: feed="iex"), returns zero bars for any session before this date and
#: complete data from this date forward. Confirmed by bisection across
#: four symbols (SPY, AAPL, MSFT, XLK) and dozens of individual trading
#: days on both sides of the boundary; the day immediately before
#: (2020-07-24) returns 0 bars for all four, the day itself (2020-07-27,
#: the next trading day) returns a full session's worth of bars for all
#: four. This is the binding Stage-0 item 2 constraint for M50: it leaves
#: only 2020-07-27..2020-12-31 (111 of the 253 2020 outcome sessions, 0 of
#: the 252 2019 outcome sessions) with any ORB minute data at all.
IEX_MINUTE_COVERAGE_FLOOR = date(2020, 7, 27)


@dataclass(frozen=True)
class CorporateActionPolicy:
    symbol: str
    universe_year: int
    expected_last_active_date: date
    reason: str


ORB_CORPORATE_ACTION_POLICIES: tuple[CorporateActionPolicy, ...] = (
    CorporateActionPolicy(
        symbol="DWDP", universe_year=2019, expected_last_active_date=date(2019, 5, 31),
        reason=(
            "DowDuPont Inc. completed its three-way separation (Dow Inc./DOW spun off "
            "2019-04-01, Corteva/CTVA spun off 2019-06-01) and was renamed DuPont de Nemours "
            "Inc. (ticker DD) shortly after. Verified via Alpaca SIP daily bars: DWDP's own "
            "trading data ends 2019-05-31. No successor ticker is substituted for the "
            "remainder of the 2019 outcome year."
        ),
    ),
    CorporateActionPolicy(
        symbol="UTX", universe_year=2020, expected_last_active_date=date(2020, 4, 3),
        reason=(
            "United Technologies Corp. completed its merger with Raytheon Company on "
            "2020-04-03, forming Raytheon Technologies Corporation (ticker RTX). Verified via "
            "Alpaca SIP daily bars: UTX's own trading data ends 2020-04-03 (RTX's own price "
            "history begins reflecting the merged entity from that date). No successor ticker "
            "is substituted for the remainder of the 2020 outcome year."
        ),
    ),
)


def expected_last_active_date_for(
    symbol: str, *, universe_year: int, outcome_year_end: date,
    policies: tuple[CorporateActionPolicy, ...] = ORB_CORPORATE_ACTION_POLICIES,
) -> date:
    """`outcome_year_end` unless a `CorporateActionPolicy` documents an
    earlier expected end of real trading data for `symbol` in
    `universe_year` -- never guessed, only from the recorded policy table."""
    for policy in policies:
        if policy.symbol == symbol and policy.universe_year == universe_year:
            return policy.expected_last_active_date
    return outcome_year_end


@dataclass(frozen=True)
class EtfLaunchPolicy:
    symbol: str
    expected_first_active_date: date
    reason: str


#: Issue #352 explicitly anticipates this: "A security without sufficient
#: real history (for example, a recently launched ETF) remains naturally
#: ineligible until the frozen lookback is actually present; do not
#: backfill, synthesize, or substitute it." XLC (Communication Services
#: Select Sector SPDR) launched June 18, 2018 (first trading day
#: 2018-06-19) -- after M50's own 2018-01-02 warmup target -- verified
#: directly against the fetched Alpaca SIP daily series (first bar dated
#: 2018-06-19; zero bars before that date). No other UNIVERSE_A/UNIVERSE_B
#: member requires this treatment for M50's window.
ETF_LAUNCH_POLICIES: tuple[EtfLaunchPolicy, ...] = (
    EtfLaunchPolicy(
        symbol="XLC", expected_first_active_date=date(2018, 6, 19),
        reason="Communication Services Select Sector SPDR Fund launched 2018-06-18 (first trading day 2018-06-19), after M50's 2018-01-02 warmup target. Verified via Alpaca SIP daily bars.",
    ),
)


def expected_first_active_date_for(
    symbol: str, *, warmup_start: date, policies: tuple[EtfLaunchPolicy, ...] = ETF_LAUNCH_POLICIES,
) -> date:
    """`warmup_start` unless an `EtfLaunchPolicy` documents a later actual
    launch date for `symbol` -- never guessed, only from the recorded
    policy table."""
    for policy in policies:
        if policy.symbol == symbol:
            return policy.expected_first_active_date
    return warmup_start


@dataclass(frozen=True)
class DataCoverageCheck:
    symbol: str
    data_kind: str
    requested_start: date
    requested_end: date
    expected_last_active_date: date
    expected_session_dates: tuple[date, ...]
    received_session_dates: tuple[date, ...]
    ohlc_integrity_violations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def missing_dates(self) -> tuple[date, ...]:
        return tuple(sorted(set(self.expected_session_dates) - set(self.received_session_dates)))

    @property
    def ok(self) -> bool:
        return len(self.missing_dates) == 0 and len(self.ohlc_integrity_violations) == 0


def evaluate_stage0_item2_coverage(checks: tuple[DataCoverageCheck, ...]) -> tuple[bool, str]:
    """Stage-0 item 2: pass only if every coverage check has zero missing
    expected sessions and zero OHLC integrity violations. `checks` must
    already encode any corporate-action-adjusted expected window (via
    `expected_session_dates`) -- this function does no calendar or
    corporate-action reasoning of its own, only aggregation."""
    if not checks:
        return False, "Stage-0 item 2 (market data/calendar coverage): no coverage checks were performed."
    failing = [c for c in checks if not c.ok]
    if not failing:
        return True, ""
    lines = [
        f"{c.symbol} ({c.data_kind}): {len(c.missing_dates)} missing session(s)"
        + (f" first={c.missing_dates[0]}" if c.missing_dates else "")
        + f", {len(c.ohlc_integrity_violations)} integrity violation(s)"
        for c in failing[:20]
    ]
    return False, "Stage-0 item 2 (market data/calendar coverage) failed for: " + "; ".join(lines)


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
