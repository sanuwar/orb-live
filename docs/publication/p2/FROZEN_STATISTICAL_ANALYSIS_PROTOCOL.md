# Frozen Statistical Analysis Protocol for P3

**Status: FROZEN, NOT EXECUTED.** This document specifies the exact statistical procedure a future P3 milestone must follow when it analyzes the evidence inventoried in `STATISTICAL_INPUT_INVENTORY.md`. No inferential result — no p-value, confidence interval, corrected significance level, factor loading, or bootstrap output — is computed, estimated, or previewed anywhere in P2. This document is a **time-stamped, version-controlled frozen statistical-analysis protocol for future P3 analysis**, not formal preregistration, and it does not become one by virtue of being committed to Git.

## 1. Hypothesis families and one primary endpoint per family

Using `HYPOTHESIS_LEDGER_M43_M55.json`'s 14 novel decision-eligible variants, grouped by `strategy_family`:

| Family | Members (arm_id) | Primary endpoint |
|---|---|---|
| Large-cap intraday ORB entry/exit/frequency | BE_AFTER_050R, PROFIT_LADDER, STRUCTURE_TRAIL_AFTER_1R, FALLBACK_FIRST_VALID_0950, LATER_FIRST_VALID_1020, SEQUENTIAL_SECOND_VALID_1020 | Pooled R-multiple expectancy per trade under WAITED_FIRST_VALID_0950 |
| ETF pullback / sector momentum / diversified trend / core-satellite | ETF_PULLBACK_RSI2, SECTOR_MOMENTUM_12_1, DIVERSIFIED_TREND_12M, PRACTICAL_CORE_SATELLITE_30K | Mean monthly $ (base cost, fixed-notional) |
| Cross-sectional equity momentum | TOP15_MOMENTUM_BASELINE, BREADTH_30, MARKET_VOL_15 | Mean monthly $ (base cost, fixed-notional) |

Every other reported figure (CAGR, Sharpe/Sortino, concentration, turnover, worst month, segment counts) is a **secondary descriptive endpoint**. Secondary endpoints are reported with the same rigor but do not carry independent confirmatory claims; they do not receive their own multiplicity-adjusted significance test.

## 2. Analysis population and windows

Exactly the windows in `HYPOTHESIS_AND_WINDOW_AUDIT.md`'s window map, reused unmodified:

- ORB pooled: P_MINUS_2 (2021-03-31–2022-03-29) through P3 (2026-04-07–2026-08-13), 1,350 sessions.
- ETF/shared account: 2022-01-03–2026-08-13.
- Cross-sectional momentum: 2021-01-04–2026-08-13.
- Untouched holdout: 2019-01-02–2020-12-31 (M50; blocked, no economic result — excluded from confirmatory analysis unless and until a completed economic result exists).
- Prospective: strictly after 2026-08-23 (M55; analyzed separately, never pooled with retrospective evidence — see §16).

## 3. Treatment of overlapping and reused windows

The ETF window is nested within both the ORB and momentum windows in calendar time, and the ORB and momentum windows overlap from 2021 through 2026. P3 must not treat family-level test statistics as independent. All resampling (§4–§5) is performed **jointly across families on shared calendar dates**, preserving whatever contemporaneous market dependence existed historically, rather than resampling each family separately and combining p-values as if independent.

## 4. Joint time-block resampling requirements

- Resample blocks of consecutive trading days (ORB, ETF, momentum) or consecutive trading days translated to monthly observation dates (M48/M49/M52/M53), never individual trade/day rows in isolation.
- On any date shared by two or more families, all families' observations for that date are drawn together in the same resample — this is what "joint" means here; it is not satisfied by resampling each family with the same block length independently.
- ORB trade-level outcomes are session-clustered: resample by session/month (per `PUBLICATION_GAP_PLAN.md`), preserving no-trade days as valid zero-trade observations, not as missing data to be dropped.
- Cross-sectional momentum resamples time blocks jointly across constituents within each rebalance date, preserving the cross-sectional dependence structure between simultaneously held names (§13).

## 5. Stationary and moving-block bootstrap specifications

- Primary method: stationary bootstrap (Politis & Romano, 1994) with expected block length drawn from a geometric distribution, applied to the return/expectancy series of each primary endpoint.
- Sensitivity method: moving-block bootstrap (fixed block length) at three block lengths spanning roughly one month, one quarter, and half of one strategy's evaluation window, to be fixed in the P3 commit before any resample is drawn.
- Number of bootstrap replications: fixed and disclosed in the P3 commit before execution (a conventional choice is 5,000–10,000; the exact value must be pinned, not tuned after seeing results).
- Random seed: fixed and committed before execution; reported in the P3 artifact so every reported interval is exactly reproducible from the committed seed and data.

## 6. Transparent block-length selection and sensitivity analysis

Block length is chosen using a data-driven rule (e.g., the Politis–White automatic block-length selector) computed **before** looking at any bootstrap output, and is then held fixed. P3 must report results at the selected length and at the two sensitivity lengths from §5 side by side; a conclusion that only holds at one block length is reported as such, not silently dropped.

## 7. Percentile and BCa confidence intervals

- Report both the percentile bootstrap interval and the bias-corrected-and-accelerated (BCa) interval for every primary endpoint.
- State the confidence level (95% is the default; any deviation must be justified in the same commit, not chosen after seeing results).
- Where the percentile and BCa intervals disagree materially, report both and flag the disagreement rather than picking the more favorable one.

## 8. Romano–Wolf stepdown family-wise error control

Applied across the 14 novel decision-eligible variants (§1) as the primary multiplicity-correction procedure (Romano & Wolf, 2005), using the joint resampling scheme from §4–§5 to preserve cross-family dependence in the stepdown null distribution. This is the primary confirmatory procedure; naive per-arm 0.05 tests are prohibited as a stand-alone confirmatory claim anywhere in a future manuscript.

## 9. Benjamini–Hochberg FDR sensitivity view

Reported as a secondary, less conservative view alongside the Romano–Wolf result (Benjamini & Hochberg, 1995), never substituted for it as the headline confirmatory claim.

## 10. White's Reality Check and Hansen's SPA applicability criteria

- Applicable only to the comparison of a single "best" strategy against a well-defined benchmark model set, drawn from the full sequence of arms actually tested (including non-decision-eligible controls and diagnostic sleeves, since those still consumed a look at the data) — not only the 14-count subset, to avoid understating the true search.
- White's Reality Check (White, 2000) and Hansen's SPA (Hansen, 2005) are reported side by side; Hansen's SPA is documented as designed to correct White's Reality Check's known low power from including uninformative/poor benchmarks in the comparison set.
- Not applicable to M50 (no completed economic result to compare) or M55 (no completed cycle yet).

## 11. Drawdown and worst-month path treatment

Drawdown, worst month, and time-to-recovery are analyzed via the empirical distribution of paths generated by the same joint block-bootstrap resamples used in §4–§5. Naive normal-theory confidence intervals are explicitly prohibited for these path-dependent quantities, per `PUBLICATION_GAP_PLAN.md`.

## 12. Session/month resampling for clustered ORB outcomes

ORB trade-level outcomes are resampled by session or month (never by individual trade in isolation), because trades within a session/month share entry-signal and market-regime dependence; treating them as i.i.d. would understate the true variance of the pooled expectancy estimate.

## 13. Cross-sectional dependence preservation for momentum

For M52/M53, resample time blocks jointly across all simultaneously held constituents and the portfolio-level return on each rebalance date — never resample individual holdings' returns independently of each other, since doing so would destroy the real cross-sectional correlation structure that the strategy is actually exposed to.

## 14. Fama–French five-factor plus UMD regression design

For M49 and M52/M53 only, once a daily or monthly investable return series exists (per `STATISTICAL_INPUT_INVENTORY.md`, currently `NOT_AVAILABLE` for the factor series itself):

- Regress excess strategy returns on the Fama–French five factors (market, size, value, profitability, investment; Fama & French, 2015) plus the Carhart momentum factor UMD (Carhart, 1997), without altering any selection rule that produced the return series.
- Report beta on each factor, the regression intercept, adjusted R-squared, residual diagnostics (autocorrelation, heteroskedasticity), and subperiod stability (split-sample or rolling-window betas).
- Use Newey–West/HAC standard errors (Newey & West, 1987) with a lag length chosen by a documented rule, not tuned after seeing results.
- For M52/M53 specifically, separate the loading on the known momentum factor (UMD) from any implementation-specific residual return, since the strategy is a momentum strategy by construction and a nonzero UMD loading is expected, not evidence of skill.
- **The regression intercept is never called "alpha" in any manuscript language unless it survives (a) the Romano–Wolf/BH multiplicity treatment in §8–§9 applied jointly with the return-level tests, and (b) HAC-robust inference at the disclosed significance level. This prohibition is absolute and applies regardless of the intercept's sign or magnitude.**

## 15. Benchmarks and transaction-cost consistency

Compare each family's primary endpoint against: SPY, IWB, an equal-weight benchmark over the same eligible universe, a matched-risk benchmark (volatility-scaled to the strategy's own realized volatility), and — for M49 specifically — a simple fixed-allocation benchmark built from the same underlying sleeves. All benchmark and strategy comparisons use the same base/stress transaction-cost assumptions already frozen in the source milestones; a cost assumption is never relaxed for the benchmark leg alone.

## 16. Separation of retrospective evidence from M55 prospective evidence

M55 observations are **never pooled** into the retrospective multiplicity correction in §8–§9, into the retrospective bootstrap resampling in §4–§7, or into the retrospective factor regression in §14. Once M55 accumulates its minimum 12 completed monthly cycles (per its own frozen protocol), it receives its own, separately reported descriptive and (if the cycle count and design permit) inferential treatment, explicitly labeled prospective, and is compared against — never merged with — the retrospective M52/M53 point estimates. An interim M55 checkpoint before 12 completed cycles is never used for any inferential claim, promotion, retuning, or activation decision, per `PUBLICATION_GAP_PLAN.md`.

## 17. Missing-data and summary-only handling

Where `STATISTICAL_INPUT_INVENTORY.md` classifies a required input as `SUMMARY_ONLY` or `NOT_AVAILABLE`, P3 must do exactly one of: (a) derive the missing granularity from a reused, unmodified local cache under the `DERIVABLE_OFFLINE` path already described, (b) explicitly scope that analysis out and say so, or (c) request explicit authorization for a new, license-reviewed data acquisition. P3 must never approximate a missing granular series from a summary statistic (e.g., simulating fake daily returns that merely reproduce a committed mean and drawdown).

## 18. Robustness and sensitivity reporting

Every confirmatory result is reported alongside: the block-length sensitivity in §6, the cost-scenario sensitivity (base vs. stress, already present in every M48–M53 report), and — where applicable — a leave-one-segment-out check against the six-period ORB pool and the per-year segments used in M48/M49/M52/M53.

## 19. Rules preventing P3 protocol changes after results are observed

- This document, once committed, may be corrected only via the same defect-and-correction discipline used throughout M43–M55: a documented defect, a regression test where applicable, and a superseding commit that links back to this one — never a silent edit.
- No primary endpoint (§1), resampling scheme (§4–§5), multiplicity procedure (§8–§9), or benchmark set (§15) may be changed after any P3 bootstrap, regression, or hypothesis test has been executed on real data. A change after that point requires a new, separately authorized milestone, explicitly labeled as such, and the original frozen protocol and its results remain in the record rather than being overwritten.
- M55's own rule (no interim-checkpoint-driven retuning) is the model this section generalizes to the full P3 statistical protocol.

## References (verify DOIs against publisher records before manuscript submission)

- Politis, D. N., & Romano, J. P. (1994). The Stationary Bootstrap. *Journal of the American Statistical Association*, 89(428), 1303–1313. https://doi.org/10.1080/01621459.1994.10476870
- Romano, J. P., & Wolf, M. (2005). Stepwise Multiple Testing as Formalized Data Snooping. *Econometrica*, 73(4), 1237–1282. https://doi.org/10.1111/j.1468-0262.2005.00615.x
- Benjamini, Y., & Hochberg, Y. (1995). Controlling the False Discovery Rate. *Journal of the Royal Statistical Society, Series B*, 57(1), 289–300. https://doi.org/10.1111/j.2517-6161.1995.tb02031.x
- White, H. (2000). A Reality Check for Data Snooping. *Econometrica*, 68(5), 1097–1126. https://doi.org/10.1111/1468-0262.00152
- Hansen, P. R. (2005). A Test for Superior Predictive Ability. *Journal of Business & Economic Statistics*, 23(4), 365–380. https://doi.org/10.1198/073500105000000063
- Fama, E. F., & French, K. R. (2015). A Five-Factor Asset Pricing Model. *Journal of Financial Economics*, 116(1), 1–22. https://doi.org/10.1016/j.jfineco.2014.10.010
- Carhart, M. M. (1997). On Persistence in Mutual Fund Performance. *Journal of Finance*, 52(1), 57–82. https://doi.org/10.1111/j.1540-6261.1997.tb03808.x
- Newey, W. K., & West, K. D. (1987). A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix. *Econometrica*, 55(3), 703–708. https://doi.org/10.2307/1913610
