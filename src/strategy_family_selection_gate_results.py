"""M51 (Issue #354) results: the evidence table and ten-criterion scores
for all four frozen candidate families, and the resulting classification.

This module supplies the `EvidenceSource` and `CriterionScore` records
that `src.strategy_family_selection_gate_research.select_family()`
consumes. It is committed in a separate, later commit from the frozen
protocol module, after the one bounded evidence pass described in
`docs/research/STRATEGY_FAMILY_SELECTION_GATE_M51.md` -- never before.

Every score is a judgment call grounded in the cited primary/authoritative
sources, made once, and not revisited to "improve" the outcome
(anti-analysis-paralysis). Where the evidence supports a plausible-but-
risky conclusion, the score is `PASS` with an explicit risk caveat in
`reasoning` -- never silently rounded up.
"""
from __future__ import annotations

from src.strategy_family_selection_gate_research import (
    FAMILY_MOMENTUM,
    FAMILY_OPTION_PREMIUM,
    FAMILY_PEAD,
    FAMILY_TREND_FOLLOWING,
    VERDICT_FAIL,
    VERDICT_PASS,
    CriterionScore,
    EvidenceSource,
    select_family,
)

__all__ = ["EVIDENCE_SOURCES", "CRITERION_SCORES", "M51_SELECTION_RESULT"]

# ---------------------------------------------------------------------------
# Evidence sources (primary/authoritative only), by family.
# ---------------------------------------------------------------------------

EVIDENCE_SOURCES: tuple[EvidenceSource, ...] = (
    # --- Family 1: concentrated cross-sectional momentum ---
    EvidenceSource(
        family=FAMILY_MOMENTUM, citation="Jegadeesh, N. and Titman, S. (1993). 'Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency.' Journal of Finance, 48(1), 65-91.",
        source_type="peer_reviewed_paper", url="https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1993.tb04702.x",
        key_finding="3-12 month past-winner stocks continue to outperform past losers over the subsequent 3-12 months; the original long-short spread averaged ~1.5%/month across ranking/holding periods.",
    ),
    EvidenceSource(
        family=FAMILY_MOMENTUM, citation="Carhart, M. M. (1997). 'On Persistence in Mutual Fund Performance.' Journal of Finance, 52(1), 57-82.",
        source_type="peer_reviewed_paper", url="https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1997.tb03808.x",
        key_finding="Formalized momentum as the fourth systematic factor (UMD) in mutual-fund performance attribution; independent replication of the momentum premium's persistence.",
    ),
    EvidenceSource(
        family=FAMILY_MOMENTUM, citation="Daniel, K. and Moskowitz, T. J. (2016). 'Momentum Crashes.' Journal of Financial Economics, 122(2), 221-247.",
        source_type="peer_reviewed_paper", url="https://www.nber.org/papers/w20439",
        key_finding="Momentum (long-short WML) suffers infrequent, severe crashes concentrated in market-rebound 'panic' states following declines; the 2009 crash saw WML lose roughly 67% over March-April 2009 as prior losers rallied far harder than prior winners. Crashes are partly forecastable from market volatility/prior-decline state -- the basis for a market/trend filter.",
    ),
    EvidenceSource(
        family=FAMILY_MOMENTUM, citation="Kenneth R. French Data Library -- Fama/French Momentum Factor (daily and monthly).",
        source_type="reproducible_dataset", url="https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html",
        key_finding="Free, public, long-history (1927-present) momentum factor returns for independent out-of-sample benchmarking and validation -- no cost, no institutional license required.",
    ),
    # --- Family 2: PEAD ---
    EvidenceSource(
        family=FAMILY_PEAD, citation="Bernard, V. L. and Thomas, J. K. (1989). 'Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium?' Journal of Accounting Research, 27, 1-36.",
        source_type="peer_reviewed_paper", url="https://www.scirp.org/reference/referencespapers?referenceid=2381416",
        key_finding="Standardized-unexpected-earnings (SUE) top-minus-bottom-decile spread is positive in 41 of 48 quarters (1974-1985); not explained by systematic risk.",
    ),
    EvidenceSource(
        family=FAMILY_PEAD, citation="Bernard, V. L. and Thomas, J. K. (1990). 'Evidence That Stock Prices Do Not Fully Reflect the Implications of Current Earnings for Future Earnings.' Journal of Accounting and Economics, 13(4), 305-340.",
        source_type="peer_reviewed_paper", url="https://en.wikipedia.org/wiki/Post%E2%80%93earnings-announcement_drift",
        key_finding="~25-30% of the drift is concentrated in the 3-day windows around SUBSEQUENT quarterly earnings announcements (only ~5% of trading days) -- the drift is directly tied to future earnings-surprise timing, not diffuse.",
    ),
    EvidenceSource(
        family=FAMILY_PEAD, citation="LSEG (Refinitiv) I/B/E/S -- Institutional Brokers' Estimate System.",
        source_type="official_broker_provider_doc", url="https://ceibs.libguides.com/blogs/news/newresources/home/ibes-earnings-estimates-the-global-analyst-consensus",
        key_finding="The standard point-in-time consensus-analyst-estimate dataset the PEAD/SUE literature is built on. Licensed commercial data, reached by most academics only through a WRDS institutional subscription; no public retail price list found.",
    ),
    EvidenceSource(
        family=FAMILY_PEAD, citation="Alpaca Markets -- Corporate Actions API documentation.",
        source_type="official_broker_provider_doc", url="https://docs.alpaca.markets/v1.3/reference/corporateactions",
        key_finding="This repository's existing market-data vendor covers dividends, mergers, spinoffs, and splits only -- no earnings-date or analyst-estimate data of any kind, and corporate-actions coverage itself only reaches back to April 2020.",
    ),
    EvidenceSource(
        family=FAMILY_PEAD, citation="SEC EDGAR -- Form 8-K, Item 2.02 (Results of Operations and Financial Condition) full-text and bulk data.",
        source_type="exchange_regulatory_filing", url="https://www.sec.gov/newsroom/press-releases/2021-159",
        key_finding="Free, bulk-downloadable, point-in-time EARNINGS ANNOUNCEMENT DATES back to August 2004 -- but no analyst consensus estimate is filed alongside it, so it cannot by itself establish a standardized-earnings-surprise (SUE) ranking, only the announcement date.",
    ),
    # --- Family 3: defined-risk index option premium ---
    EvidenceSource(
        family=FAMILY_OPTION_PREMIUM, citation="Cboe Exchange -- Cboe S&P 500 PutWrite Index (PUT), official index since 1986.",
        source_type="exchange_regulatory_filing", url="https://en.wikipedia.org/wiki/CBOE_S%26P_500_PutWrite_Index",
        key_finding="Since June 1986 inception, cumulative return of 1835%, historically higher risk-adjusted return than the S&P 500 with materially lower volatility over the long run.",
    ),
    EvidenceSource(
        family=FAMILY_OPTION_PREMIUM, citation="Ennis Knupp + Associates (commissioned by Cboe). 'Evaluating the Performance Characteristics of the CBOE S&P 500 PutWrite Index.'",
        source_type="exchange_regulatory_filing", url="https://cdn.cboe.com/resources/education/research_publications/PUTIndexEnnisKnupp.pdf",
        key_finding="July 1986-October 2008: PUT annualized return 10.32% vs. S&P 500's 8.77%; PUT annualized standard deviation 9.91%, 36% lower than the S&P 500's 15.39%. Underperforms in sharply rising months, outperforms in flat/declining months.",
    ),
    EvidenceSource(
        family=FAMILY_OPTION_PREMIUM, citation="PortfoliosLab / Cboe index data -- CBOE S&P 500 PutWrite Index maximum drawdown, 2006-2018.",
        source_type="reproducible_dataset", url="https://portfolioslab.com/symbol/%5EPUT",
        key_finding="Max drawdown -32.7% over 2006-2018 (spans the 2008 GFC) vs. -50.9% for the S&P 500 over the same period -- a materially smaller drawdown than raw equity exposure, though not risk-free.",
    ),
    EvidenceSource(
        family=FAMILY_OPTION_PREMIUM, citation="Robinhood -- 'Advanced options strategies (Level 3)' and account-type support articles.",
        source_type="official_broker_provider_doc", url="https://robinhood.com/us/en/support/articles/advanced-options-strategies/",
        key_finding="Level 3 (required for vertical/defined-risk spreads) is available only in a MARGIN account; cash accounts and Robinhood Retirement do not support Level 3 at all, regardless of whether the spread itself is fully collateralized.",
    ),
    EvidenceSource(
        family=FAMILY_OPTION_PREMIUM, citation="Retail historical options-data vendors: ORATS, ThetaData, EOD Historical Data (pricing pages).",
        source_type="official_broker_provider_doc", url="https://www.alphanume.com/blog/best-options-data-providers-for-systematic-trading-research",
        key_finding="Retail-accessible historical options-chain/Greeks data starting around $20-160/month (ORATS ~$100/month base, ThetaData $40-160/month, EOD Historical Data ~$20/month) -- an order of magnitude cheaper and more transparently priced than PEAD's institutional-only IBES/Zacks access.",
    ),
    # --- Family 4: tactical ETF trend following ---
    EvidenceSource(
        family=FAMILY_TREND_FOLLOWING, citation="Hurst, B., Ooi, Y. H., and Pedersen, L. H. (2017). 'A Century of Evidence on Trend-Following Investing.' Journal of Portfolio Management, 44(1), 15-29. (AQR Capital Management)",
        source_type="peer_reviewed_paper", url="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026",
        key_finding="Time-series trend-following delivered positive average returns in every decade since 1880 across global markets, with low correlation to stocks/bonds, and performed well in 8 of the 10 largest 60/40-portfolio drawdowns over the century -- but the strongest results are for a DIVERSIFIED multi-asset-class (equity, bond, commodity, FX) implementation, not a single-asset-class (equity/sector-ETF-only) one.",
    ),
    EvidenceSource(
        family=FAMILY_TREND_FOLLOWING, citation="Direxion Shares ETF Trust, Form 485BPOS (SEC-filed prospectus) -- leveraged/inverse ETF compounding-risk disclosure.",
        source_type="exchange_regulatory_filing", url="https://www.sec.gov/Archives/edgar/data/1424958/000119312521278320/d233148d485bpos.htm",
        key_finding="Officially discloses that daily-reset leveraged ETF performance over periods longer than one day will differ from the stated multiple of the index's performance due to compounding, and that this divergence grows with volatility and holding period -- the documented basis for restricting any leveraged-ETF use to research comparison only, never activation.",
    ),
    EvidenceSource(
        family=FAMILY_TREND_FOLLOWING, citation="This repository's own M48/M49 empirical result: `SECTOR_MOMENTUM_12_1` (ARM_B), a closely related cross-sectional sector-ETF rotation strategy already tested on the same $30K account and the same Alpaca SIP daily-bar infrastructure.",
        source_type="reproducible_dataset", url="internal:docs/research/ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_FEASIBILITY.md",
        key_finding="M49's shared-portfolio result (ORB + ARM_A + capped ARM_B together) was ~$184.75/month base / 14.60% max drawdown -- `SAFE_BUT_BELOW_TARGET`. Not identical to family 4 (cross-sectional relative rotation vs. time-series absolute trend), but the closest available real, repo-native, point-in-time-tested reference for what a similar-spirit liquid-ETF strategy actually produces on this exact account before assuming a more optimistic academic figure.",
    ),
)

# ---------------------------------------------------------------------------
# Ten-criterion scores, PASS/FAIL, per family. Every FAIL and every PASS
# with a material caveat carries its reasoning inline.
# ---------------------------------------------------------------------------

CRITERION_SCORES: tuple[CriterionScore, ...] = (
    # ===== Family 1: concentrated cross-sectional momentum =====
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="evidence_strength", verdict=VERDICT_PASS,
        reasoning="One of the most replicated findings in empirical asset pricing: Jegadeesh & Titman (1993), Carhart (1997), and Daniel & Moskowitz (2016) span three decades of independent peer-reviewed confirmation across markets and periods."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="data_availability_cost", verdict=VERDICT_PASS,
        reasoning="Daily equity price bars are already available via this repository's Alpaca integration at no incremental cost; the Kenneth French library provides a free, independent benchmark series for validation."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="survivorship_lookahead_risk", verdict=VERDICT_PASS,
        reasoning="Manageable with disciplined point-in-time universe construction (M50's own SEC-EDGAR-sourcing lesson applies directly); unlike M50, a forward/recent evaluation window avoids the deep-historical minute-bar coverage problem entirely, since only daily bars are required."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="account_size_feasibility", verdict=VERDICT_PASS,
        reasoning="A 10-20 name, equal-weighted, whole-share long-only book fits comfortably within $30,000 for typical large/mid-cap prices, with no options/margin constraints to manage."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="realistic_costs", verdict=VERDICT_PASS,
        reasoning="Monthly rebalancing (not ORB's intraday frequency) implies low turnover; liquid large/mid-cap names carry tight spreads. Same cost-modeling convention (bps-based) already validated in M48/M49 applies directly."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="monthly_dollar_range", verdict=VERDICT_PASS,
        reasoning="Concentrated top-decile momentum portfolios have shown gross average annual returns materially above a market-cap-weighted benchmark in the strongest documented academic samples (Jegadeesh & Titman-style top-decile returns often cited in the mid-teens % gross annually). Net of realistic slippage/costs, a $500/month (~20%/year) practical target is credible in above-average years, not as a safe average-case guarantee -- real variance is high."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="drawdown_tail_risk", verdict=VERDICT_FAIL,
        reasoning="Daniel & Moskowitz (2016) document a ~67% two-month crash for the long-short factor in 2009; a long-only, trend-filtered, CONCENTRATED implementation is structurally less exposed (no short-squeeze leg, and a trend filter that should reduce exposure heading into the worst 'panic' states) but concentration itself adds real idiosyncratic drawdown risk beyond the diversified factor. Scored FAIL to keep this risk explicit rather than implied away by the mitigations."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="repo_robinhood_compatibility", verdict=VERDICT_PASS,
        reasoning="Plain long-only equities, whole shares, cash account, no options/margin -- directly compatible with this repository's existing equity-trading conventions and Robinhood's basic cash-account trading."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="engineering_effort", verdict=VERDICT_PASS,
        reasoning="The 12-1 month cross-sectional momentum ranking mechanism is already implemented and tested in this repository (`src/orb_capital_target_strategy_family_research.py`'s ARM_B, `SECTOR_MOMENTUM_12_1`), applied there to a small ETF universe -- extending it to a point-in-time equity universe with a trend filter is a moderate, well-scoped extension, not new-from-scratch engineering."),
    CriterionScore(family=FAMILY_MOMENTUM, criterion_id="target_plausibility", verdict=VERDICT_PASS,
        reasoning="$500/month is mathematically credible based on documented top-decile momentum returns, achieved in above-average regimes, not as a reliable floor. $1,000/month (~40%/year) is NOT a credible baseline expectation for this family and would require an exceptionally strong regime or excessive concentration -- reported honestly, not implied as achievable on average."),

    # ===== Family 2: PEAD =====
    CriterionScore(family=FAMILY_PEAD, criterion_id="evidence_strength", verdict=VERDICT_PASS,
        reasoning="Bernard & Thomas (1989, 1990) remain among the most replicated anomalies in accounting/finance research, persisting across 40+ years of subsequent study -- described in the literature as 'the anomaly that refuses to die.'"),
    CriterionScore(family=FAMILY_PEAD, criterion_id="data_availability_cost", verdict=VERDICT_FAIL,
        reasoning="The strategy requires point-in-time consensus analyst estimates to compute standardized unexpected earnings (SUE) -- available only via institutional feeds (I/B/E/S, Zacks) with no published retail pricing, typically reached only through a WRDS academic/institutional subscription. This repository's existing Alpaca vendor provides no earnings-estimate data of any kind. Free SEC EDGAR 8-K filings give announcement DATES only, not the surprise magnitude the evidence base is built on."),
    CriterionScore(family=FAMILY_PEAD, criterion_id="survivorship_lookahead_risk", verdict=VERDICT_FAIL,
        reasoning="Without a genuine point-in-time consensus-estimate feed, any DIY substitute (e.g. trailing-earnings-only or restated-data proxies) risks look-ahead bias baked directly into the ranking signal -- the same data gap that fails the criterion above."),
    CriterionScore(family=FAMILY_PEAD, criterion_id="account_size_feasibility", verdict=VERDICT_PASS,
        reasoning="In principle feasible with $30,000 using the same whole-share long-only sizing as family 1, independent of the data blocker above."),
    CriterionScore(family=FAMILY_PEAD, criterion_id="realistic_costs", verdict=VERDICT_PASS,
        reasoning="Liquid stock trading costs are comparable to family 1's; not the binding constraint for this family."),
    CriterionScore(family=FAMILY_PEAD, criterion_id="monthly_dollar_range", verdict=VERDICT_FAIL,
        reasoning="Published SUE-decile spreads are smaller in magnitude than the momentum premium and cannot be credibly estimated for this account without the blocked point-in-time estimate data; no honest $500/month estimate can be constructed."),
    CriterionScore(family=FAMILY_PEAD, criterion_id="drawdown_tail_risk", verdict=VERDICT_FAIL,
        reasoning="Individual-stock earnings-surprise positions carry material single-name gap risk around announcements; without verified point-in-time data the true tail behavior cannot be characterized."),
    CriterionScore(family=FAMILY_PEAD, criterion_id="repo_robinhood_compatibility", verdict=VERDICT_PASS,
        reasoning="Long-only equities, no options/margin -- compatible in principle, independent of the data blocker."),
    CriterionScore(family=FAMILY_PEAD, criterion_id="engineering_effort", verdict=VERDICT_FAIL,
        reasoning="Building a genuine point-in-time earnings-surprise database from scratch (without a licensed estimate feed) is a substantial, open-ended data-engineering project in its own right, before any strategy logic is written."),
    CriterionScore(family=FAMILY_PEAD, criterion_id="target_plausibility", verdict=VERDICT_FAIL,
        reasoning="Cannot be credibly assessed as mathematically plausible without the blocked point-in-time estimate data; asserting $500/month here would require unverifiable assumptions this repository's own conventions (M50's own precedent) do not allow."),

    # ===== Family 3: defined-risk index option premium =====
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="evidence_strength", verdict=VERDICT_PASS,
        reasoning="Cboe's own official PUT index (since 1986) is a primary, exchange-published, four-decade track record, corroborated by an independent institutional study (Ennis Knupp) and academic work (Bondarenko)."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="data_availability_cost", verdict=VERDICT_PASS,
        reasoning="Retail-accessible historical options-chain/Greeks data is available from multiple vendors (ORATS ~$100/month, ThetaData $40-160/month, EOD Historical Data ~$20/month) -- an order of magnitude cheaper and more transparently priced than family 2's institutional-only data."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="survivorship_lookahead_risk", verdict=VERDICT_PASS,
        reasoning="Liquid index/ETF options (SPY/SPX/QQQ) carry no survivorship-bias concern the way individual-stock universes do; vendor historical-chain data is inherently point-in-time (quoted prices as of the actual historical date)."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="account_size_feasibility", verdict=VERDICT_PASS,
        reasoning="$30,000 supports a modest number of concurrent fully collateralized spread positions (e.g. multiple $500-1,000-max-risk SPY vertical spreads across expirations) within whole-contract constraints, allowing real diversification across time and strikes."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="realistic_costs", verdict=VERDICT_PASS,
        reasoning="SPY/SPX options are among the most liquid, tightest-spread listed contracts; two-legged spreads roughly double per-trade transaction drag relative to a single option, a real but well-quantified and well-documented cost already reflected in the cited index studies' own return figures."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="monthly_dollar_range", verdict=VERDICT_PASS,
        reasoning="The PUT index's own ~10.3%/year historical average (Ennis Knupp) implies roughly $250/month on $30,000 at index-like (moderately out-of-the-money) strike selection -- below the $500 practical target. Reaching $500-1,000/month requires closer-to-the-money strikes with proportionally higher premium AND proportionally higher assignment/loss risk per trade, a real, quantifiable, reportable trade-off rather than a free improvement."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="drawdown_tail_risk", verdict=VERDICT_PASS,
        reasoning="The PUT index's own max drawdown was -32.7% over 2006-2018 (spanning the 2008 GFC) vs. -50.9% for the S&P 500 -- materially smaller than raw equity exposure. Because the strategy is explicitly DEFINED-risk (fully collateralized spreads, never naked), max loss per position is contractually bounded in advance, a structural advantage over families 1 and 4's open-ended equity drawdown exposure."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="repo_robinhood_compatibility", verdict=VERDICT_FAIL,
        reasoning="Robinhood's own support documentation states Level 3 (required for vertical/defined-risk spreads) is available ONLY in a margin account -- cash accounts and Robinhood Retirement do not support it at all, regardless of the spread's own capital-defined structure. This directly conflicts with this repository's and Issue #354's own standing 'no margin borrowing' rule and is a serious, documented practical obstacle to eventual activation."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="engineering_effort", verdict=VERDICT_FAIL,
        reasoning="Requires new options-pricing/Greeks-aware simulation logic, assignment/expiration handling, and a new retail options-data vendor integration -- materially more new engineering than families 1 or 4, which can substantially reuse this repository's existing equity/ETF price-history infrastructure."),
    CriterionScore(family=FAMILY_OPTION_PREMIUM, criterion_id="target_plausibility", verdict=VERDICT_PASS,
        reasoning="$500/month is mathematically credible with moderately aggressive (closer-to-the-money) strike selection; $1,000/month would require materially more aggressive strikes and correspondingly higher per-trade tail/assignment risk -- reported explicitly, not hidden behind an attractive index-level average."),

    # ===== Family 4: tactical ETF trend following =====
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="evidence_strength", verdict=VERDICT_PASS,
        reasoning="Hurst, Ooi & Pedersen (2017, AQR/Journal of Portfolio Management) document positive trend-following returns in every decade since 1880 and strong performance in 8 of the 10 largest 60/40 drawdowns -- a long, independently reviewed evidentiary base, though the strongest results are for a diversified multi-asset-class implementation, not the single-asset-class (equity/sector ETF) version specified here."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="data_availability_cost", verdict=VERDICT_PASS,
        reasoning="Directly reuses the same Alpaca SIP daily-bar infrastructure already fetched, cached, and validated for UNIVERSE_A/UNIVERSE_B in M48/M49/M50, at zero incremental data cost."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="survivorship_lookahead_risk", verdict=VERDICT_PASS,
        reasoning="Liquid, long-lived, well-known index/sector ETFs (and, for research comparison only, their 2x-leveraged counterparts) carry no meaningful survivorship-bias concern; a recent evaluation window avoids M50's deep-historical minute-bar-coverage problem entirely, since only daily bars are required."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="account_size_feasibility", verdict=VERDICT_PASS,
        reasoning="Whole-share ETF positions size comfortably within $30,000; no options/margin constraints."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="realistic_costs", verdict=VERDICT_PASS,
        reasoning="Extremely liquid, tight-spread ETFs; monthly-or-lower rebalance frequency implies low turnover, directly matching the same bps-based cost convention already validated in M48/M49."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="monthly_dollar_range", verdict=VERDICT_FAIL,
        reasoning="A single-asset-class (equity/sector-ETF-only) time-series trend system loses the cross-asset diversification that drives the AQR paper's strongest headline results. This repository's own closest empirical reference -- M49's `SECTOR_MOMENTUM_12_1` (ARM_B) sleeve, tested on this exact $30,000 account with this exact data infrastructure -- contributed to a shared-portfolio result of ~$184.75/month base, well below $500/month. No credible case is made here that a related, similarly-scoped, UNLEVERED liquid-ETF trend system would reliably clear $500/month."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="drawdown_tail_risk", verdict=VERDICT_PASS,
        reasoning="Trend-following's own evidentiary strength is specifically its crisis-period performance (8 of 10 largest 60/40 drawdowns per Hurst/Ooi/Pedersen); a market/trend-filtered, unlevered ETF implementation should exhibit materially milder drawdowns than a raw buy-and-hold benchmark, consistent with M49's own measured 14.60% base max drawdown for a related sleeve."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="repo_robinhood_compatibility", verdict=VERDICT_PASS,
        reasoning="Plain long ETF positions (unlevered case), whole shares, no options/margin -- directly compatible with existing equity/ETF trading conventions and a Robinhood cash account."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="engineering_effort", verdict=VERDICT_PASS,
        reasoning="Time-series trend signals (e.g. price vs. a moving average) and the underlying daily-bar fetch/cache/cost pipeline are straightforward extensions of machinery already built and tested for M48/M49."),
    CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id="target_plausibility", verdict=VERDICT_FAIL,
        reasoning="Reaching $500/month for this family, based on both the academic single-asset-class Sharpe-ratio math and this repository's own closely related M49 reference point, would in practice require leverage -- the exact lever Issue #354 explicitly disallows for activation ('no leveraged position may be proposed for activation'). The 2x-leveraged-ETF comparison is permitted for research only, so the credible, activation-honest target for this family sits below $500/month, not at or above it."),
)


# ---------------------------------------------------------------------------
# The frozen selection rule, applied once to the scores above.
# ---------------------------------------------------------------------------

M51_SELECTION_RESULT = select_family(CRITERION_SCORES)
