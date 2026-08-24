# Hypothesis and Evaluation-Window Audit

## Executive finding

The research record contains **nine empirical decision stages** (M43–M49, M52, M53), one blocked untouched-validation stage (M50), one literature/rubric selection gate (M51), one synthesis (M54), and one initialized-but-resultless prospective monitor (M55).

M54 reports **14 distinct backtested strategy/variant hypotheses**, but its prose names 17 arm labels if every control and diagnostic sleeve is counted literally:

- M43: 4 exit arms
- M44: 1 fallback policy
- M45: 1 later-entry policy
- M46: 3 displayed lanes
- M47: 1 sequential policy
- M48: 3 ETF arms
- M49: 1 blend
- M52: 1 momentum baseline
- M53: 2 risk-control arms

That literal sum is 17, before treating M43's two entry-policy views as separate evaluations. This is a documentation/counting ambiguity in the authoritative M54 narrative, not evidence of three hidden experiments.

## Reconciled counting rule

For publication, count a “hypothesis” as a **novel, predeclared, decision-eligible strategy variant**, excluding unchanged controls, sensitivity-only replays, and component-sleeve diagnostics that were not separately eligible for promotion.

| Milestone | Novel decision-eligible variants | Count rationale |
|---|---|---:|
| M43 | BE_AFTER_050R, PROFIT_LADDER, STRUCTURE_TRAIL_AFTER_1R | 3; BASELINE is the unchanged control and CURRENT_FIRST_VALID is sensitivity |
| M44 | FALLBACK_FIRST_VALID_0950 | 1 |
| M45 | LATER_FIRST_VALID_1020 | 1 |
| M46 | SMALL_MID combined lane | 1; MID and SMALL are decomposition sleeves/reference diagnostics within the combined-lane decision |
| M47 | SEQUENTIAL_SECOND_VALID_1020 | 1 |
| M48 | ETF_PULLBACK_RSI2, SECTOR_MOMENTUM_12_1, DIVERSIFIED_TREND_12M | 3 |
| M49 | PRACTICAL_CORE_SATELLITE_30K | 1 |
| M52 | top-15 cross-sectional momentum | 1 |
| M53 | BREADTH_30, MARKET_VOL_15 | 2 |
| **Total** |  | **14** |

Alternative transparent counts are also valid if labeled:

- **17 displayed arm labels**: includes M43 BASELINE and M46 MID/SMALL diagnostic sleeves.
- **21 arm-by-entry-policy/diagnostic evaluations**: 17 plus the additional CURRENT_FIRST_VALID results for all four M43 arms.
- Neither number changes the substantive conclusion: multiplicity is material and the effective number of independent tests is smaller than the raw count because outcomes share data and controls.

The manuscript must state its counting rule and should not repeat “14” without this definition.

## Strategy families

| Family | Milestones | Status |
|---|---|---|
| Large-cap intraday ORB entry/exit/frequency | M43–M47 | One supported exit rule; fallback/later/second-trade and small/mid extensions were insufficient, mixed, null, or not promising |
| ETF pullback / sector momentum / diversified trend | M48–M50 | Positive but insufficient standalone arms; M49 safer below-target blend; M50 untouched validation blocked |
| Cross-sectional equity momentum | M51–M55 | Selected from literature; M52 positive but insufficient; M53 risk controls parked; M55 prospectively initialized with no performance |
| Screened but not backtested | M51 PEAD and defined-risk option premium | Not counted as tested hypotheses |
| Screened tactical trend family | M51 | Related to M48 evidence; no new M51 backtest |

## Window map

| Window family | Exact window | Milestones using it | Reuse/overlap finding |
|---|---|---|---|
| ORB pooled | P_MINUS_2 2021-03-31–2022-03-29; P_MINUS_1 2022-03-30–2023-03-30; P0 2023-03-31–2024-04-02; P1 2024-04-03–2025-04-02; P2 2025-04-03–2026-04-06; P3 2026-04-07–2026-08-13 | M43, M44, M45, M47; M46 uses P3 only | Same 1,350 sessions repeatedly interrogated for exit and frequency variants; P3 additionally used for the small/mid lane |
| ETF/shared account | 2022-01-03–2026-08-13 | M48 and M49 | Identical dates; M49 reuses M48 arms inside a shared allocator |
| Cross-sectional momentum | 2021-01-04–2026-08-13 | M52 and M53 | Identical dates and caches; M53 intentionally modifies risk controls on the M52 strategy |
| Untouched attempted holdout | 2019-01-02–2020-12-31 | M50 | Non-overlapping and not previously used, but no economic result because required IEX minute data were unavailable |
| Prospective | Strictly after 2026-08-23 freeze; first eligible signal 2026-08-31 | M55 | No result yet; remains the only forward-only performance path in the record |

The three completed-result window families overlap heavily in calendar time: the ETF window is largely nested within both the ORB and momentum windows, and the ORB and momentum windows overlap from 2021 through 2026. Different instruments and frequencies do not make repeated market regimes statistically independent.

## Evidence classification

| Milestones | Classification |
|---|---|
| M43–M49, M52–M53 | Retrospective historical simulations with within-milestone frozen protocols; not independent confirmation after the broader research sequence selected families and questions |
| M50 | Retrospective untouched-window validation attempt; protocol valid, outcome blocked before economics |
| M51 | Retrospective literature/feasibility screen; no new market simulation |
| M54 | Retrospective synthesis |
| M55 checkpoint 0 | Prospective protocol and initialization evidence only; no performance observation |
| Exploratory antecedents | Pre-M43 ORB discovery/selection work referenced by M43–M47; outside the formal registry but relevant to total selection-bias history |

## Multiple-testing and selection-bias exposure

1. Frozen protocols reduce within-milestone result-driven changes but do not correct selection across milestones.
2. Reusing the same sessions for several variants makes conventional independent-test corrections inaccurate; dependence must be modeled.
3. The surviving candidates were chosen after earlier results, so their point estimates are selection-conditioned.
4. Multiple outcome gates—mean return, profit factor, drawdown, worst month, concentration, segment counts—create further researcher degrees of freedom even when thresholds are frozen.
5. Corrections were necessary and often material. Correct reruns improve truthfulness but do not restore a clean untouched holdout.
6. M50's data failure means there is no completed historical validation sample that none of the designs influenced.
7. M55 can add forward evidence only if the candidate and reporting rules remain frozen and no interim checkpoint drives retuning.

## Required statistical treatment

- Register the P1 counting rule and one primary endpoint per strategy family before statistical analysis.
- Use a joint stationary/block-bootstrap resampling scheme over time, preserving cross-strategy contemporaneous dependence.
- Report family-wise error control (for example, Romano–Wolf stepdown) and an FDR view as sensitivity; do not use naive 0.05 tests for each arm.
- Apply data-snooping-aware tests such as White's Reality Check or Hansen's SPA where technically appropriate.
- Separate confirmatory inference from descriptive engineering comparisons and from M55's future prospective evidence.
