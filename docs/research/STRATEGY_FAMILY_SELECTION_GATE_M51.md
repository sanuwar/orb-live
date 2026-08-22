# M51: $30K Higher-Return Strategy-Family Selection Gate

**Issue:** [#354](https://github.com/sanuwar/orb-live/issues/354)
**Branch:** `agent/m51-higher-return-strategy-selection`
**Depends on:** M43-M50 (frozen large-cap ORB lane + M48/M49 ETF family), parked, not reopened.

> **This section is committed BEFORE any evidence is scored or conclusion recorded**, exactly as Issue #354 requires. It records the frozen selection protocol only: the four candidate families, the ten-criterion rubric, and the deterministic selection rule (`src/strategy_family_selection_gate_research.py`). The evidence tables, scores, classification, and (if applicable) frozen M52 proposal are added in a later, separate results commit.

## Purpose

M43-M49 built and honestly evaluated the frozen large-cap ORB lane plus a $30K ETF strategy family sharing capital with it:

- Frozen large-cap ORB frequency: ~1.898 trades/month.
- M46 (small/mid-cap ORB momentum lane): `NOT_PROMISING`.
- M47 (sequential second trade): `INSUFFICIENT_EVIDENCE`.
- M48's three ETF arms were positive but insufficient; the generous ORB-plus-top-two upper bound was ~$482.87/month.
- M49's shared-capital portfolio: ~$184.75/month base, ~$122.02/month stress, 14.60% base max drawdown -- `SAFE_BUT_BELOW_TARGET`.
- M50 could not independently validate 2019-2020 because Alpaca's frozen IEX minute history begins 2020-07-27 -- `INSUFFICIENT_EVIDENCE`.

**M51 does not reopen, retune, or further squeeze the ORB/M48/M49 family.** That path is parked unless new independent evidence arises. M51 asks a different, bounded question:

> Identify at most one genuinely different, practical strategy family that deserves a separately frozen empirical M52 experiment for a $30,000 account.

Primary practical target: **$500/month** after realistic costs. Stretch target: **$1,000/month**. Neither target is guaranteed; the required risk and mathematical credibility of each target must be reported explicitly, not implied.

Research only. No activation, no broker writes, no production configuration change.

## Candidate families (exactly four, frozen -- no expanding the list)

1. **Concentrated long-only cross-sectional equity momentum** with a market/trend filter.
2. **Post-earnings-announcement drift (PEAD) / earnings-momentum trading** using point-in-time announcement data.
3. **Defined-risk liquid-index option premium strategies** (e.g. fully collateralized vertical spreads) -- no naked options, no unlimited-loss structures.
4. **Tactical trend following** using liquid index/sector ETFs, permitting at most 2x leveraged ETFs for research comparison only -- no margin borrowing, no leveraged position may be proposed for activation.

## Evidence standard

Primary or authoritative evidence only: peer-reviewed papers, exchange/OCC/SEC/FINRA materials, official broker/data-provider documentation, and reproducible datasets. Influencer claims, marketing yields, isolated backtests, and unsourced aggregators are excluded.

## Frozen rubric (ten criteria, `src/strategy_family_selection_gate_research.py::SELECTION_RUBRIC`)

1. `evidence_strength` -- strength and independence of published evidence
2. `data_availability_cost` -- point-in-time data availability and cost
3. `survivorship_lookahead_risk` -- survivorship/lookahead risk
4. `account_size_feasibility` -- implementability with $30,000 and whole-contract/share constraints
5. `realistic_costs` -- realistic transaction costs, spreads, assignment, liquidity, tax/operational complexity
6. `monthly_dollar_range` -- plausible net monthly-dollar range
7. `drawdown_tail_risk` -- expected drawdown and tail-loss characteristics
8. `repo_robinhood_compatibility` -- compatibility with the existing repository and eventual Robinhood execution
9. `engineering_effort` -- engineering effort required for a trustworthy backtest
10. `target_plausibility` -- whether $500/month and $1,000/month are mathematically plausible without hiding unacceptable risk

Each criterion is scored `PASS`/`FAIL` per family, with a documented reason and source citations.

## Selection rule (frozen, `select_family()`)

A family is **eligible** only if all four gating dimensions pass:

- the `evidence_strength` gate,
- the `data_availability_cost` gate,
- the `account_size_feasibility` gate,
- the composite **"realistic after-cost economics"** gate (`realistic_costs` AND `monthly_dollar_range` AND `target_plausibility` all `PASS`).

`survivorship_lookahead_risk`, `drawdown_tail_risk`, `repo_robinhood_compatibility`, and `engineering_effort` are scored and reported for every family but do not independently veto eligibility -- they inform the written comparison and (only if needed) the deterministic tiebreak.

- **Zero** eligible families -> `NO_CREDIBLE_CANDIDATE`. Stop.
- **One** eligible family -> `SELECTED_FOR_M52`, that family, one frozen M52 proposal.
- **More than one** eligible family -> `SELECTED_FOR_M52`, broken deterministically by the highest total rubric pass-count (all 10 criteria), then by the families' own frozen declaration order. Never a subjective "least bad" judgment call made after the fact.

No threshold is tuned, no family is combined with another, no fifth candidate is introduced, and a family is never promoted merely because it ranks first among failing candidates. One bounded evidence pass, one deterministic ranking, one conclusion.

## Old M49 result (frozen reference only, never combined with M51's own evaluation)

- Base: $184.75/month mean, 14.60% max drawdown. Stress: $122.02/month mean, 18.54% max drawdown.
- Classification: `SAFE_BUT_BELOW_TARGET`.

---

## Evidence, scoring, and conclusion

One bounded evidence pass using primary/authoritative sources, recorded in `src/strategy_family_selection_gate_results.py` (17 evidence sources, 40 criterion scores -- 4 families x 10 criteria). Every score below is a judgment call grounded in a cited source; risk is reported explicitly, never implied away.

### Evidence table (abridged; full citations and URLs in `EVIDENCE_SOURCES`)

| Family | Source | Type | Key finding |
| --- | --- | --- | --- |
| 1. Momentum | Jegadeesh & Titman (1993), *J. Finance* | Peer-reviewed | Past 3-12mo winners keep outperforming past losers over the next 3-12mo. |
| 1. Momentum | Carhart (1997), *J. Finance* | Peer-reviewed | Formalized momentum (UMD) as a persistent systematic factor. |
| 1. Momentum | Daniel & Moskowitz (2016), *JFE* | Peer-reviewed | Long-short momentum crashed ~67% over Mar-Apr 2009; crashes cluster in market-rebound "panic" states -- the basis for a trend filter. |
| 1. Momentum | Kenneth French Data Library | Reproducible dataset | Free, public, 1927-present momentum factor for independent validation. |
| 2. PEAD | Bernard & Thomas (1989, 1990), *JAR*/*JAE* | Peer-reviewed | SUE decile spread positive in 41/48 quarters (1974-1985); ~25-30% of drift concentrated in 3-day windows around subsequent earnings. |
| 2. PEAD | LSEG I/B/E/S | Official provider doc | The standard point-in-time consensus-estimate dataset; licensed, no public retail price found. |
| 2. PEAD | Alpaca Corporate Actions API docs | Official provider doc | No earnings-estimate data of any kind; corporate-actions coverage only from April 2020. |
| 2. PEAD | SEC EDGAR Form 8-K Item 2.02 | Regulatory filing | Free announcement DATES back to 2004 -- but no consensus estimate, so no SUE ranking. |
| 3. Option premium | Cboe S&P 500 PutWrite Index (PUT) | Exchange index | Official index since 1986; 1835% cumulative return since inception. |
| 3. Option premium | Ennis Knupp / Cboe study | Exchange-commissioned study | 1986-2008: PUT 10.32%/yr vs. S&P 500 8.77%/yr, 36% lower volatility. |
| 3. Option premium | Cboe/PortfoliosLab index data | Reproducible dataset | PUT max drawdown -32.7% (2006-2018, spans 2008 GFC) vs. -50.9% for the S&P 500. |
| 3. Option premium | Robinhood Level 3 support docs | Official provider doc | Vertical spreads require a MARGIN account; cash accounts do not support Level 3 at all. |
| 3. Option premium | ORATS / ThetaData / EOD Historical Data | Official provider docs | Retail historical options data from ~$20-160/month. |
| 4. Trend following | Hurst, Ooi & Pedersen (2017, AQR), *JPM* | Peer-reviewed | Positive returns every decade since 1880; strong in 8/10 largest 60/40 drawdowns -- for a diversified multi-asset implementation. |
| 4. Trend following | Direxion Form 485BPOS (SEC-filed prospectus) | Regulatory filing | Official compounding-risk disclosure for daily-reset leveraged ETFs. |
| 4. Trend following | This repo's own M49 result (`SECTOR_MOMENTUM_12_1`) | Reproducible, repo-native | Closely related sleeve on this exact $30K account: ~$184.75/month base (shared portfolio), `SAFE_BUT_BELOW_TARGET`. |

### Scoring table (PASS/FAIL per criterion; full reasoning in `CRITERION_SCORES`)

| Criterion | 1. Momentum | 2. PEAD | 3. Option premium | 4. Trend following |
| --- | :---: | :---: | :---: | :---: |
| `evidence_strength` | PASS | PASS | PASS | PASS |
| `data_availability_cost` | PASS | **FAIL** | PASS | PASS |
| `survivorship_lookahead_risk` | PASS | **FAIL** | PASS | PASS |
| `account_size_feasibility` | PASS | PASS | PASS | PASS |
| `realistic_costs` | PASS | PASS | PASS | PASS |
| `monthly_dollar_range` | PASS | **FAIL** | PASS | **FAIL** |
| `drawdown_tail_risk` | **FAIL** | **FAIL** | PASS | PASS |
| `repo_robinhood_compatibility` | PASS | PASS | **FAIL** | PASS |
| `engineering_effort` | PASS | **FAIL** | **FAIL** | PASS |
| `target_plausibility` | PASS | **FAIL** | PASS | **FAIL** |
| **Total PASS** | **9/10** | 2/10 | 8/10 | 8/10 |
| **Gates (evidence + data + account-size + economics)** | **ALL PASS** | evidence, account-size only | **ALL PASS** | evidence, data, account-size only |

("Economics" gate = `realistic_costs` AND `monthly_dollar_range` AND `target_plausibility` all PASS.)

### Realistic $30,000 monthly-dollar ranges

- **Momentum (family 1):** Concentrated top-decile momentum has shown gross average annual returns materially above a market-cap benchmark in the strongest documented samples. Net of realistic costs, **$500/month (~20%/year) is credible in above-average regimes** -- not a safe average-case guarantee. **$1,000/month (~40%/year) is not a credible baseline expectation** and would require an exceptionally strong regime or excessive concentration.
- **PEAD (family 2):** Cannot be honestly estimated without the blocked point-in-time consensus-estimate data. Published SUE-decile spreads are smaller in magnitude than momentum's; no defensible $500/month figure can be constructed.
- **Option premium (family 3):** The PUT index's own ~10.3%/year historical average implies **~$250/month at index-like strikes -- below the $500 target**. Reaching $500-1,000/month requires closer-to-the-money strikes, which proportionally raises assignment/loss risk per trade. **$500/month is credible with moderately aggressive strikes; $1,000/month requires materially more aggressive strikes and correspondingly higher tail risk.**
- **Trend following (family 4):** This repository's own closest empirical reference (M49's `SECTOR_MOMENTUM_12_1` sleeve, tested on this exact account) contributed to a ~$184.75/month shared-portfolio result. A single-asset-class, unlevered version of family 4 has no credible case for reliably clearing $500/month; doing so would in practice require leverage, which Issue #354 disallows for activation.

### Drawdown / tail-risk discussion

- **Momentum:** Real, well-documented crash risk (-67% for the long-short factor in a single two-month window, 2009). A long-only, trend-filtered, concentrated implementation is structurally less exposed than the raw factor (no short-squeeze leg; the filter should reduce exposure heading into "panic" states) but concentration itself adds idiosyncratic risk beyond the diversified academic factor. Scored `drawdown_tail_risk` FAIL to keep this explicit.
- **PEAD:** Individual-name earnings-gap risk; unverifiable without the blocked data.
- **Option premium:** Structurally the safest of the four on a per-position basis -- defined-risk spreads have a contractually bounded max loss, and the PUT index's own historical max drawdown (-32.7%, 2006-2018) is materially smaller than the S&P 500's (-50.9%) over the same span. Not risk-free: aggressive strike selection needed to hit $500-1,000/month raises assignment frequency and per-trade loss magnitude.
- **Trend following:** Trend-following's own core evidentiary strength is crisis-period performance; a market/trend-filtered ETF implementation should behave more mildly than raw buy-and-hold, consistent with M49's own measured 14.60% base max drawdown for a related sleeve.

### Data-source and research-cost requirements

| Family | Required data | Cost |
| --- | --- | --- |
| 1. Momentum | Daily equity price bars (Alpaca, already integrated); Kenneth French library for validation | $0 incremental |
| 2. PEAD | Point-in-time consensus analyst estimates (I/B/E/S or Zacks) | Institutional license, no public price found -- likely the single largest cost of any family here |
| 3. Option premium | Historical options chains/Greeks (ORATS, ThetaData, or EOD Historical Data) | ~$20-160/month, retail-accessible |
| 4. Trend following | Daily ETF price bars (Alpaca, already integrated) | $0 incremental |

### Robinhood/platform compatibility assessment

- **Momentum, PEAD, trend following:** Plain long-only equity/ETF positions, whole shares, cash account -- fully compatible with this repository's existing trading conventions and a Robinhood cash account.
- **Option premium:** **Materially incompatible as specified.** Robinhood's own documentation states Level 3 (required for any vertical/defined-risk spread) is available only in a margin account; cash accounts do not support it, regardless of the spread's own fully collateralized structure. This directly conflicts with this repository's and Issue #354's standing "no margin borrowing" rule. The mechanical selection rule (which does not gate on this criterion, per Issue #354's own four named gates) still lets this family reach eligibility, but this is flagged here as a serious, load-bearing practical obstacle for any future activation path -- not a technicality to route around.

### Final classification: `SELECTED_FOR_M52`

Two families (momentum, option premium) pass all four gating dimensions. Per the frozen tiebreak (highest total rubric pass-count, then declaration order): **momentum (9/10) beats option premium (8/10)**, which failed on `repo_robinhood_compatibility` and `engineering_effort`. No threshold was retuned and no family was combined to reach this result -- see `src/strategy_family_selection_gate_results.py::M51_SELECTION_RESULT`, computed once from the frozen `select_family()` function.

**Selected family: 1. Concentrated long-only cross-sectional equity momentum with a market/trend filter.**

### Comparison with the existing M49 range

M49's real, empirically tested, repo-native result on this exact $30,000 account was **~$184.75/month base / ~14.60% base max drawdown** (`SAFE_BUT_BELOW_TARGET`). The academic evidence base for concentrated momentum plausibly supports a **higher** monthly-dollar range in above-average regimes ($500+/month), but this is a **projection from published research, not yet a repo-validated, point-in-time-correct empirical result** -- exactly the gap M52 exists to close. M49's own number is the only one of the two that has actually survived this repository's cost model, event ordering, and integrity gates; the momentum projection has not. This distinction is deliberately preserved, not blurred, in the classification above.

---

## Frozen M52 proposal (no empirical results -- protocol only)

**Working title:** Concentrated point-in-time cross-sectional momentum with a market/trend filter, $30,000 account.

- **Universe:** A liquid large/mid-cap equity universe, constructed prospectively (as-of each rebalance date, using only information available at that date) rather than retrospectively -- avoiding M50's own deep-historical point-in-time-sourcing problem by evaluating on a recent/forward window (see below), where a prospective construction does not require reconstructing a years-old historical roster.
- **Signal:** 12-1 month cross-sectional price momentum ranking, reusing the ranking mechanism already implemented for `SECTOR_MOMENTUM_12_1` (M48's ARM_B), generalized from an ETF universe to a point-in-time equity universe.
- **Trend filter:** A single, predeclared market-regime filter (e.g. SPY above/below its own 200-day moving average) gating new entries -- a specific, common, well-documented rule (Faber-style tactical allocation), not tuned during M52.
- **Concentration:** Top ~10-20 names by momentum rank, equal-weighted, monthly rebalance.
- **Capital and constraints:** $30,000, one cash account, whole shares, no leverage, no margin, no shorting -- identical safety posture to every prior milestone.
- **Costs:** Reuse the same base 5bps / stress 10bps cost convention already validated in M48/M49.
- **Evaluation window:** A recent period with confirmed, complete Alpaca daily-bar coverage (e.g. matching or extending M48/M49's own already-validated 2021-2026 window) -- deliberately avoiding re-litigating M50's specific 2019-2020 minute-bar-coverage blocker, since this strategy needs only daily bars.
- **Required Stage-0 gate (M52's own, before any economic result is read):** point-in-time universe provenance (learning directly from M50's SEC-EDGAR-sourcing precedent), daily-bar coverage/integrity verification, and parity-before-economics for any reused primitive (the 12-1 ranking mechanism).
- **Out of scope for M52 without a new milestone:** any change to the trend-filter rule, the concentration count, the rebalance frequency, or the cost model once frozen; any leverage; any options/margin; any activation.

No M52 code is implemented and no M52 result is computed in M51.
