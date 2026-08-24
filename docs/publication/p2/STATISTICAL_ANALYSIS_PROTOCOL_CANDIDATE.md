# Statistical Analysis Protocol — Candidate for P3

**STATUS: DRAFT CANDIDATE FOR EXTERNAL FINANCE/ECONOMETRICS REVIEW — NOT AUTHORIZED FOR EXECUTION.**

This document was previously named `FROZEN_STATISTICAL_ANALYSIS_PROTOCOL.md` and labeled "frozen." That label was withdrawn in this correction pass: several material econometric decisions below (bootstrap replication count, random seed, exact block lengths, common-vs-family-specific block selection, null hypotheses, comparators, test-statistic standardization, and the treatment of non-evaluable variants) were left unpinned in the original version while the document still called itself frozen. Presenting an internally inconsistent document as frozen is a worse error than presenting an honestly incomplete one, so this version does neither: it is a **draft candidate**, not yet approved, and not authorized for execution by any future milestone until the review gate in `P2_READINESS_DECISION.md` (external finance/econometrics reviewer sign-off, plus a separate time-stamped commit that actually pins every item in the Unresolved Decisions Registry below) is satisfied. No inferential result — no p-value, confidence interval, corrected significance level, factor loading, or bootstrap output — is computed, estimated, or previewed anywhere in this document or in P2. This document remains a **time-stamped, version-controlled draft statistical-analysis protocol candidate for future P3 analysis**, not formal preregistration, and it does not become one by virtue of being committed to Git.

## Unresolved Decisions Registry

None of the items below may be filled in by this correction pass, by convenience, or by picking a "conventional" default just to look complete. Each requires either explicit reviewer input or a separately authorized, time-stamped freezing commit before P3 may execute anything that depends on it.

| # | Decision | Status | Why it cannot be assumed |
|---|---|---|---|
| 1 | Bootstrap replication count | **UNRESOLVED** | The previous draft suggested "5,000–10,000" as a default; picking a number now, without reviewer input, would itself be the kind of arbitrary econometric decision this correction pass was told not to make. |
| 2 | Random seed | **UNRESOLVED** | A seed fixed today, before reviewer sign-off on the resampling design itself, could not be justified as anything other than arbitrary. |
| 3 | Exact block lengths | **UNRESOLVED** | §6 below describes a data-driven selection *procedure* (candidate), but the procedure itself, and its output on real data, requires reviewer approval before being treated as authoritative. |
| 4 | Common vs. family-specific block selection | **UNRESOLVED** | Whether one block length applies across all three families or each family gets its own is an open methodological question the reviewer rubric in `EXTERNAL_REVIEW_AND_JOURNAL_PRECHECK_PLAN.md` explicitly asks about; this document does not prejudge the answer. |
| 5 | Null hypotheses (exact functional form per family) | **UNRESOLVED** | §1 names a primary endpoint per family (e.g., "pooled R-multiple expectancy per trade"), but the precise null (e.g., zero mean vs. a specific benchmark-relative null) is not fixed here. |
| 6 | Comparators | **UNRESOLVED** | §15 names candidate benchmark instruments (SPY, IWB, equal-weight, matched-risk, fixed-allocation), but which comparator is the primary confirmatory comparator per family, versus secondary/descriptive, is not decided. |
| 7 | Test-statistic standardization | **UNRESOLVED** | Whether endpoints are compared in R-multiples, dollars, or standardized (e.g., Sharpe-like) units across families with different native units (R-multiples for ORB, dollars for ETF/momentum) is not decided; naively pooling unlike units into one stepdown procedure would be a methodological error a reviewer must catch or approve. |
| 8 | Treatment of non-evaluable variants | **UNRESOLVED as a final rule; a candidate default is stated in §1a below** | See §1a — a candidate exclusion rule is proposed, but confirming it is correct (rather than merely convenient) is exactly what external review is for. |

## 1. Hypothesis families and candidate primary endpoint per family

Using `HYPOTHESIS_LEDGER_M43_M55.json`'s 14 novel decision-eligible variants, grouped by the corrected `strategy_family` field:

| Family | Members (arm_id) | Count | Candidate primary endpoint |
|---|---|---:|---|
| Intraday ORB entry/exit/frequency/universe extension | BE_AFTER_050R, PROFIT_LADDER, STRUCTURE_TRAIL_AFTER_1R, FALLBACK_FIRST_VALID_0950, LATER_FIRST_VALID_1020, SMALL_MID, SEQUENTIAL_SECOND_VALID_1020 | 7 | Pooled R-multiple expectancy per trade (candidate; see Unresolved Decisions Registry #5 for the exact null) |
| ETF pullback / sector momentum / diversified trend / core-satellite | ETF_PULLBACK_RSI2, SECTOR_MOMENTUM_12_1, DIVERSIFIED_TREND_12M, PRACTICAL_CORE_SATELLITE_30K | 4 | Mean monthly $ (base cost, fixed-notional) |
| Cross-sectional equity momentum | TOP15_MOMENTUM_BASELINE, BREADTH_30, MARKET_VOL_15 | 3 | Mean monthly $ (base cost, fixed-notional) |

**7 + 4 + 3 = 14.** M46 (`SMALL_MID`, plus its diagnostic siblings `MID`/`SMALL`) was previously miscategorized under the ETF family in an earlier draft of this document and the underlying JSON ledger; both are corrected here. M46 tests the same ORB signal/exit engine applied to a small/mid-cap universe over a single period (P3 only), not an ETF pullback/sector-momentum/trend-following strategy, so it belongs in the intraday-ORB family. Every one of the 14 novel decision-eligible variants appears in exactly one family row above; `tests/test_publication_p2_artifacts.py::test_every_novel_variant_appears_in_protocol_family_table_exactly_once` verifies this by cross-checking this table against the JSON ledger, not by hand-counting.

Every other reported figure (CAGR, Sharpe/Sortino, concentration, turnover, worst month, segment counts) is a **secondary descriptive endpoint**. Secondary endpoints are reported with the same rigor but do not carry independent confirmatory claims; they do not receive their own multiplicity-adjusted significance test.

### 1a. Candidate treatment of non-evaluable variants (Unresolved Decisions Registry #8)

A tested-variant count is not automatically an inferential-test count. `HYPOTHESIS_LEDGER_M43_M55.json` now carries an `inferential_evaluability` field on every entry, with four values: `INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA`, `DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE`, `NOT_EVALUABLE_STRUCTURAL_NULL`, `CONTROL_OR_DIAGNOSTIC_ONLY`. Applied to the 14 novel decision-eligible variants:

| Inferential evaluability | Count | Variants | Reason |
|---|---:|---|---|
| `INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA` | 11 | BE_AFTER_050R, PROFIT_LADDER, STRUCTURE_TRAIL_AFTER_1R, LATER_FIRST_VALID_1020, ETF_PULLBACK_RSI2, SECTOR_MOMENTUM_12_1, DIVERSIFIED_TREND_12M, PRACTICAL_CORE_SATELLITE_30K, TOP15_MOMENTUM_BASELINE, BREADTH_30, MARKET_VOL_15 | A real, non-trivial committed sample exists; formal inference is blocked only on the granular-data availability question in `STATISTICAL_INPUT_INVENTORY.md`, not on the sample itself. |
| `DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE` | 2 | FALLBACK_FIRST_VALID_0950 (n=2 genuine closed trades), SMALL_MID (20 trades, single 90-session period) | Sample too thin for a credible resampling or multiplicity-adjusted test regardless of data granularity. No bootstrap, stepdown, or Reality-Check/SPA procedure will be run against these two; they are reported descriptively only, exactly as their own source milestones already do. |
| `NOT_EVALUABLE_STRUCTURAL_NULL` | 1 | SEQUENTIAL_SECOND_VALID_1020 (n=0 second trades) | Zero observations; no p-value or resampled interval can be computed on zero observations. This is a structural finding, not something P3 statistics can act on. |
| `CONTROL_OR_DIAGNOSTIC_ONLY` | 0 (within the 14; 7 more exist among the other 7 ledger entries outside the 14-count) | — | Not part of the 14 novel decision-eligible variants by definition; listed here for completeness only. |

**Candidate rule (unresolved, pending review):** §8's Romano–Wolf stepdown and §9's Benjamini–Hochberg sensitivity view, if approved, would be restricted to the 11 `INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA` variants once they separately pass the granular-data availability/integrity preflight required by `STATISTICAL_INPUT_INVENTORY.md`. The 2 `DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE` and 1 `NOT_EVALUABLE_STRUCTURAL_NULL` variants would be reported in the same manuscript table with their true classification and sample size, never assigned a fabricated p-value or excluded silently. This rule is a candidate for reviewer approval, not a decision this document is authorized to make final.

**No observation was invented and no p-value was assigned to M44 or M47 anywhere in this repository.** `docs/research/ORB_FALLBACK_CANDIDATE_RECOVERY_EXPERIMENT.md` and `docs/research/ORB_SEQUENTIAL_SECOND_TRADE_FEASIBILITY.md` remain the authoritative descriptions of their respective n=2 and n=0 findings.

## 2. Analysis population and windows

Exactly the windows in `HYPOTHESIS_AND_WINDOW_AUDIT.md`'s window map, reused unmodified:

- ORB pooled: P_MINUS_2 (2021-03-31–2022-03-29) through P3 (2026-04-07–2026-08-13), 1,350 sessions.
- M46 small/mid-cap universe extension: P3 only, 2026-04-07–2026-08-13, 90 sessions (not pooled across the six-period ORB window; see §1a on why this makes SMALL_MID descriptive-only).
- ETF/shared account: 2022-01-03–2026-08-13.
- Cross-sectional momentum: 2021-01-04–2026-08-13.
- Untouched holdout: 2019-01-02–2020-12-31 (M50; blocked, no economic result — excluded from confirmatory analysis unless and until a completed economic result exists).
- Prospective: strictly after 2026-08-23 (M55; analyzed separately, never pooled with retrospective evidence — see §16).

## 3. Treatment of overlapping and reused windows

The ETF window is nested within both the ORB and momentum windows in calendar time, and the ORB and momentum windows overlap from 2021 through 2026. Any future P3 execution must not treat family-level test statistics as independent. All resampling (§4–§5) would be performed **jointly across families on shared calendar dates**, preserving whatever contemporaneous market dependence existed historically, rather than resampling each family separately and combining p-values as if independent. This joint-resampling *requirement* is not itself in dispute; only its tuning parameters (block length, replication count, seed — Unresolved Decisions Registry #1–4) are unresolved.

## 4. Joint time-block resampling requirements (candidate design)

- Resample blocks of consecutive trading days (ORB, ETF, momentum) or consecutive trading days translated to monthly observation dates (M48/M49/M52/M53), never individual trade/day rows in isolation.
- On any date shared by two or more families, all families' observations for that date would be drawn together in the same resample — this is what "joint" means here; it would not be satisfied by resampling each family with the same block length independently.
- ORB trade-level outcomes are session-clustered: resample by session/month (per `PUBLICATION_GAP_PLAN.md`), preserving no-trade days as valid zero-trade observations, not as missing data to be dropped.
- Cross-sectional momentum resamples time blocks jointly across constituents within each rebalance date, preserving the cross-sectional dependence structure between simultaneously held names (§13).
- Restricted, per §1a's candidate rule, to the 11 `INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA` variants pending the preflight in `STATISTICAL_INPUT_INVENTORY.md`.

## 5. Stationary and moving-block bootstrap specifications (candidate design; replication count and seed UNRESOLVED)

- Candidate primary method: stationary bootstrap (Politis & Romano, 1994) with expected block length drawn from a geometric distribution, applied to the return/expectancy series of each primary endpoint. This choice of *method family* is a candidate for reviewer approval, not yet adopted.
- Candidate sensitivity method: moving-block bootstrap (fixed block length) at multiple block lengths spanning roughly one month, one quarter, and half of one strategy's evaluation window — the exact lengths are Unresolved Decisions Registry #3.
- **Number of bootstrap replications: UNRESOLVED (Unresolved Decisions Registry #1).** No default is adopted here.
- **Random seed: UNRESOLVED (Unresolved Decisions Registry #2).** No seed is adopted here.

## 6. Block-length selection and sensitivity analysis (candidate procedure; exact output UNRESOLVED)

A candidate procedure — a data-driven rule such as the Politis–White automatic block-length selector, computed before looking at any bootstrap output and then held fixed — is proposed. Whether this procedure is applied once across all three families (Unresolved Decisions Registry #4, "common") or separately per family ("family-specific") is not decided. Its output on real data has not been computed anywhere in P2.

## 7. Percentile and BCa confidence intervals (candidate design)

- Candidate design: report both the percentile bootstrap interval and the bias-corrected-and-accelerated (BCa) interval for every evaluable primary endpoint.
- Candidate confidence level: 95%, subject to reviewer confirmation.
- Where the percentile and BCa intervals disagree materially, both would be reported and the disagreement flagged rather than picking the more favorable one.

## 8. Romano–Wolf stepdown family-wise error control (candidate procedure; scope restricted per §1a)

Candidate: applied across the 11 `INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA` variants (not all 14 — see §1a) as the primary multiplicity-correction procedure (Romano & Wolf, 2005), using the joint resampling scheme from §4–§5 to preserve cross-family dependence in the stepdown null distribution. Naive per-arm 0.05 tests remain prohibited as a stand-alone confirmatory claim anywhere in a future manuscript, independent of whether this specific procedure is the one a reviewer ultimately approves.

## 9. Benjamini–Hochberg FDR sensitivity view (candidate procedure)

Candidate: reported as a secondary, less conservative view alongside the Romano–Wolf result (Benjamini & Hochberg, 1995), never substituted for it as the headline confirmatory claim.

## 10. White's Reality Check and Hansen's SPA applicability criteria

- Applicable only to the comparison of a single "best" strategy against a well-defined benchmark model set, drawn from the full sequence of arms actually tested (including non-decision-eligible controls and diagnostic sleeves, and the `DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE`/`NOT_EVALUABLE_STRUCTURAL_NULL` variants, since those still consumed a look at the data) — not only the 11-count inferentially-evaluable subset, to avoid understating the true search.
- White's Reality Check (White, 2000) and Hansen's SPA (Hansen, 2005) would be reported side by side; Hansen's SPA is documented as designed to correct White's Reality Check's known low power from including uninformative/poor benchmarks in the comparison set.
- Not applicable to M50 (no completed economic result to compare) or M55 (no completed cycle yet).

## 11. Drawdown and worst-month path treatment (candidate design)

Drawdown, worst month, and time-to-recovery would be analyzed via the empirical distribution of paths generated by the same joint block-bootstrap resamples used in §4–§5. Naive normal-theory confidence intervals remain explicitly prohibited for these path-dependent quantities, per `PUBLICATION_GAP_PLAN.md`, independent of the unresolved tuning parameters above.

## 12. Session/month resampling for clustered ORB outcomes

ORB trade-level outcomes would be resampled by session or month (never by individual trade in isolation), because trades within a session/month share entry-signal and market-regime dependence; treating them as i.i.d. would understate the true variance of the pooled expectancy estimate. This structural requirement is not in dispute; only the tuning parameters above are.

## 13. Cross-sectional dependence preservation for momentum

For M52/M53, resample time blocks jointly across all simultaneously held constituents and the portfolio-level return on each rebalance date — never resample individual holdings' returns independently of each other, since doing so would destroy the real cross-sectional correlation structure that the strategy is actually exposed to.

## 14. Fama–French five-factor plus UMD regression design (candidate design; factor series NOT_AVAILABLE)

For M49 and M52/M53 only, once a daily or monthly investable return series exists (per `STATISTICAL_INPUT_INVENTORY.md`, the factor series itself is `NOT_AVAILABLE` anywhere in the committed record today — this section describes a candidate design that cannot be executed until that gap is separately resolved):

- Candidate: regress excess strategy returns on the Fama–French five factors (market, size, value, profitability, investment; Fama & French, 2015) plus the Carhart momentum factor UMD (Carhart, 1997), without altering any selection rule that produced the return series.
- Candidate: report beta on each factor, the regression intercept, adjusted R-squared, residual diagnostics (autocorrelation, heteroskedasticity), and subperiod stability (split-sample or rolling-window betas).
- Candidate: use Newey–West/HAC standard errors (Newey & West, 1987) with a lag length chosen by a documented rule, not tuned after seeing results.
- For M52/M53 specifically, any future execution must separate the loading on the known momentum factor (UMD) from any implementation-specific residual return, since the strategy is a momentum strategy by construction and a nonzero UMD loading is expected, not evidence of skill.
- **The regression intercept is never called "alpha" in any manuscript language unless it survives (a) a reviewer-approved multiplicity treatment applied jointly with the return-level tests, and (b) HAC-robust inference at a disclosed significance level. This prohibition is absolute and applies regardless of the intercept's sign or magnitude, and regardless of whether the rest of this document's tuning parameters are ever resolved.**

## 15. Candidate benchmarks and transaction-cost consistency

Candidate comparators (Unresolved Decisions Registry #6: which is primary vs. secondary per family is not decided): SPY, IWB, an equal-weight benchmark over the same eligible universe, a matched-risk benchmark (volatility-scaled to the strategy's own realized volatility), and — for M49 specifically — a simple fixed-allocation benchmark built from the same underlying sleeves. Any future execution must use the same base/stress transaction-cost assumptions already frozen in the source milestones for both strategy and benchmark legs; a cost assumption must never be relaxed for the benchmark leg alone.

## 16. Separation of retrospective evidence from M55 prospective evidence

M55 observations must **never be pooled** into the retrospective multiplicity correction in §8–§9, into the retrospective bootstrap resampling in §4–§7, or into the retrospective factor regression in §14. Once M55 accumulates its minimum 12 completed monthly cycles (per its own frozen protocol, which is a separate, already-genuinely-frozen protocol distinct from this candidate document), it would receive its own, separately reported descriptive and (if the cycle count and design permit) inferential treatment, explicitly labeled prospective, and compared against — never merged with — the retrospective M52/M53 point estimates. An interim M55 checkpoint before 12 completed cycles must never be used for any inferential claim, promotion, retuning, or activation decision, per `PUBLICATION_GAP_PLAN.md`. This rule is not unresolved; it carries forward unchanged from the prior draft.

## 17. Missing-data and summary-only handling

Where `STATISTICAL_INPUT_INVENTORY.md` classifies a required input as `SUMMARY_ONLY`, `NOT_AVAILABLE`, or "potentially derivable offline pending preflight," any future P3 execution must do exactly one of: (a) run and pass the granular-data availability/integrity preflight required before treating a locally cached series as usable evidence, (b) explicitly scope that analysis out and say so, or (c) request explicit authorization for a new, license-reviewed data acquisition. P3 must never approximate a missing granular series from a summary statistic (e.g., simulating fake daily returns that merely reproduce a committed mean and drawdown), and must never treat an unverified local cache as available evidence without first running that preflight.

## 18. Robustness and sensitivity reporting (candidate design)

Any future confirmatory result would be reported alongside: the block-length sensitivity in §6 (once resolved), the cost-scenario sensitivity (base vs. stress, already present in every M48–M53 report), and — where applicable — a leave-one-segment-out check against the six-period ORB pool and the per-year segments used in M48/M49/M52/M53.

## 19. Rules preventing protocol changes after results are observed (applies once this document is actually frozen)

- This document, once actually frozen by a subsequent, separately time-stamped commit (not this one), may be corrected only via the same defect-and-correction discipline used throughout M43–M55: a documented defect, a regression test where applicable, and a superseding commit that links back to the frozen version — never a silent edit.
- No primary endpoint (§1), resampling scheme (§4–§5), multiplicity procedure (§8–§9), or benchmark set (§15) may be changed after any P3 bootstrap, regression, or hypothesis test has been executed on real data, once this document is frozen. A change after that point would require a new, separately authorized milestone, explicitly labeled as such, with the original frozen protocol and its results remaining in the record rather than being overwritten.
- M55's own rule (no interim-checkpoint-driven retuning) is the model this section generalizes to the full P3 statistical protocol, once it exists as an actually-frozen document rather than this candidate.
- **This document itself is not yet frozen and therefore this section's own protections do not yet apply to it** — it may still be revised by ordinary correction commits (like this one) until the external-review gate in `P2_READINESS_DECISION.md` is satisfied and a genuinely frozen successor is committed.

## References (verify DOIs against publisher records before manuscript submission)

- Politis, D. N., & Romano, J. P. (1994). The Stationary Bootstrap. *Journal of the American Statistical Association*, 89(428), 1303–1313. https://doi.org/10.1080/01621459.1994.10476870
- Romano, J. P., & Wolf, M. (2005). Stepwise Multiple Testing as Formalized Data Snooping. *Econometrica*, 73(4), 1237–1282. https://doi.org/10.1111/j.1468-0262.2005.00615.x
- Benjamini, Y., & Hochberg, Y. (1995). Controlling the False Discovery Rate. *Journal of the Royal Statistical Society, Series B*, 57(1), 289–300. https://doi.org/10.1111/j.2517-6161.1995.tb02031.x
- White, H. (2000). A Reality Check for Data Snooping. *Econometrica*, 68(5), 1097–1126. https://doi.org/10.1111/1468-0262.00152
- Hansen, P. R. (2005). A Test for Superior Predictive Ability. *Journal of Business & Economic Statistics*, 23(4), 365–380. https://doi.org/10.1198/073500105000000063
- Fama, E. F., & French, K. R. (2015). A Five-Factor Asset Pricing Model. *Journal of Financial Economics*, 116(1), 1–22. https://doi.org/10.1016/j.jfineco.2014.10.010
- Carhart, M. M. (1997). On Persistence in Mutual Fund Performance. *Journal of Finance*, 52(1), 57–82. https://doi.org/10.1111/j.1540-6261.1997.tb03808.x
- Newey, W. K., & West, K. D. (1987). A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix. *Econometrica*, 55(3), 703–708. https://doi.org/10.2307/1913610
