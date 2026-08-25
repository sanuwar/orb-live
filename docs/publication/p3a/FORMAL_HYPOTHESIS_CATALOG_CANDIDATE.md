# Formal Hypothesis Catalog — Candidate

**STATUS: CANDIDATE SPECIFICATION. NOT FROZEN, NOT PREREGISTERED, NOT EXECUTION-AUTHORIZED.**

Every hypothesis below is a *proposal* for a qualified external finance/econometrics reviewer to accept, amend, or reject. No hypothesis here has been tested. Nulls and alternatives are written in candidate form precisely because Decision 5 (exact null hypotheses) in `STATISTICAL_DECISION_REGISTER.md` is unresolved.

## Count reconciliation

| Level | Count |
|---|---:|
| Conceptual research hypotheses (CH) | **7** |
| Family-level hypotheses (FH) | **3** |
| Variant-level statistical test instances, candidate (TI) | **11** |
| Descriptive-only evaluations (DO) | **2** |
| Structural-null / non-evaluable (SN) | **1** |
| **Decision-eligible variants (TI + DO + SN)** | **14** |
| Displayed arm labels (P2, unchanged) | 17 |
| Arm-by-entry-policy evaluations (P2, unchanged) | 21 |

**Hypothesis counts and variant counts are different quantities and are never used interchangeably in this catalog.** Seven conceptual questions are addressed by fourteen decision-eligible variants, of which eleven are candidate test instances. CH-3 has zero test instances.

## Family-level hypotheses

Families match P2's corrected `strategy_family` field exactly (M46 in the intraday-ORB family, not the ETF family).

| ID | Family | Conceptual hypotheses | Decision-eligible variants | Candidate test instances |
|---|---|---|---:|---:|
| FH-1 | Intraday ORB entry/exit/frequency/universe extension | CH-1, CH-2, CH-3 | 7 | 4 |
| FH-2 | ETF pullback / sector momentum / diversified trend | CH-4, CH-5 | 4 | 4 |
| FH-3 | Cross-sectional equity momentum | CH-6, CH-7 | 3 | 3 |
| | **Total** | **7** | **14** | **11** |

**Candidate family-level null (FH-k):** no variant within family *k* has a population primary estimand better than its comparator, evaluated jointly across the family's test instances under a multiplicity procedure to be specified (Decision 8/9). The family is the natural multiplicity unit because members share a data window, an execution engine, and a selection history.

---

## CH-1 — Intraday stop-management (profit protection)

- **Family:** FH-1. **Variants covered:** M43 `BE_AFTER_050R`, `PROFIT_LADDER`, `STRUCTURE_TRAIL_AFTER_1R` (3 variants, mapping to TI-01, TI-02, TI-03).
- **Research question:** Does a predeclared stop-management policy protect realized favorable excursion without materially destroying R-normalized expectancy, relative to the unchanged baseline exit engine, on an already-selected entry rule?
- **Economic rationale (repository-supported):** M43's own report grounds this in prior committed work: earlier R-normalized MFE analysis found a substantial fraction of eventual losers first reach favorable excursion, including roughly the +0.50R region. The rationale is that some losing trades pass through profit before reversing, so a stop moved after a favorable-excursion threshold may convert a subset of full losses into breakeven or reduced losses. This is a mechanical, testable claim about the excursion distribution, not an imported market-efficiency argument.
- **Evaluation window:** six pooled ORB periods, P_MINUS_2 through P3, 2021-03-31 to 2026-08-13, 1,350 sessions, 122 candidate occurrences.
- **Unit of observation:** the trading session (or month), never the individual trade in isolation, since trades cluster within sessions and share signal/regime dependence.
- **Directionality:** one-sided is defensible for the protection mechanism (the hypothesis is that protection helps or is neutral), but the M43 report itself shows `STRUCTURE_TRAIL_AFTER_1R` performing worse than baseline (0.161 vs. 0.304 R/month), so two-sided is the safer default and the preliminary recommendation. Flagged for the reviewer under Decision 5.
- **Overlapping-window exposure:** total. All three variants use the identical 122 candidate occurrences and the identical baseline. They are **paired, not independent**: the same underlying trades with different exit rules applied.
- **Selection-bias exposure:** the entry rule (`WAITED_FIRST_VALID_0950`, `GAP_050_RVOL_175`) was selected by pre-M43 work outside the formal registry. CH-1 conditions on that selection and cannot correct it.
- **Non-evaluable conditions:** becomes non-evaluable if the per-trade R-multiple series cannot be verified by the P3B preflight.
- **Evidence:** `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md`, commit `264fdf3cf8ea264b14a974ce4ddaa48185b56599`, blob `9c8c4f9e5ee05621535832241fd74139adb9aa86`.
- **Could support:** a claim that a specific stop rule did or did not improve risk-normalized expectancy on this historical sample, with multiplicity-adjusted uncertainty.
- **Could not support:** that the rule generalizes forward; that the underlying entry edge exists; any dollar-income claim (this family is measured in R-multiples, not simulated dollars).

## CH-2 — ORB trade-frequency recovery

- **Family:** FH-1. **Variants covered:** M45 `LATER_FIRST_VALID_1020` (TI-04); M44 `FALLBACK_FIRST_VALID_0950` (DO-01, descriptive only); M47 `SEQUENTIAL_SECOND_VALID_1020` (SN-01, structural null).
- **Research question:** Can additional qualifying ORB trades be recovered, by relaxing entry timing on no-trade days, by falling back to another pre-09:50 candidate, or by permitting a second non-overlapping trade, without degrading trade quality?
- **Economic rationale (repository-supported):** M45's report states the question directly: on a day where the frozen `WAITED_FIRST_VALID_0950` primary produces no trade, does allowing the first genuinely valid ORB setup appearing strictly after 09:50 through 10:20 ET recover meaningful trade frequency while preserving positive trade quality? M47 asks the complementary question for a second trade after the first fully closes. The rationale is frequency: a low-frequency strategy with positive expectancy may be improved by more qualifying occurrences, provided per-trade quality holds.
- **This conceptual hypothesis is the clearest demonstration of why hypothesis count does not equal variant count.** Three variants address one question; only one is inferentially evaluable. Reporting "three tests" would overstate the evidence threefold; reporting "one hypothesis, one test" would hide two genuine looks at the data. The catalog records both.
- **Evaluation window:** six pooled ORB periods, 2021-03-31 to 2026-08-13.
- **Unit of observation:** session/month.
- **Directionality:** two-sided. M45's own result (later-only expectancy 0.021R, negative excluding NVDA) shows the effect can be negative.
- **Overlapping-window exposure:** total with CH-1, since it uses the same sessions, baseline, and six periods.
- **Selection-bias exposure:** the 09:51-10:20 window was frozen before results were read (M45 states no cutoff sweep was performed), which limits within-milestone tuning but not across-milestone selection.
- **Non-evaluable conditions:** M44 is permanently descriptive-only at n=2 regardless of preflight. M47 is permanently non-evaluable at n=0. Only TI-04 depends on the preflight.
- **Evidence:** M45 commit `051def4f9cce366ef128613941e634863b8efa07`, blob `91548473b15e0efdbd91c40881e323017595a541`; M44 commit `864416c829539c1567a23bf67b96f659598402de`, blob `258a8d929da1b8035c2bde6d4077bc4126805367`; M47 commit `2cfa08bf37924f0f7b01e0d6f31b2e6fb81a4169`, blob `460b35206e1b6ffd10cb833f3c84f78526338d22`.
- **Could support:** a statement about M45's combined-policy expectancy with adjusted uncertainty; a documented structural finding that the M47 second-trade opportunity does not occur under the frozen gate.
- **Could not support:** any inferential claim about M44 or M47; any claim that n=0 means the policy does not work rather than that it never triggered.

## CH-3 — ORB universe extension to small/mid-cap

- **Family:** FH-1. **Variants covered:** M46 `SMALL_MID` (DO-02, descriptive only). `MID` and `SMALL` are decomposition sleeves, not decision-eligible.
- **Research question:** Does the frozen large-cap ORB rule set transfer to a small/mid-cap universe with comparable economics?
- **Economic rationale (repository-supported):** M46 tests a frozen 20-name IWR/IWM basket under unchanged large-cap ORB rules, a transfer/robustness question about whether the signal is universe-specific. The rationale is grounded in the milestone's own framing as a feasibility screen.
- **Zero candidate test instances.** 20 trades over a single 90-session period (P3 only, not pooled) cannot support a credible block-bootstrap or multiplicity-adjusted test, and the milestone itself is labeled a feasibility screen. Additional limitation recorded in M46: HOOD was 100% of positive contribution.
- **Evaluation window:** P3 only, 2026-04-07 to 2026-08-13, 90 sessions.
- **Overlapping-window exposure:** P3 is a sub-window of the CH-1/CH-2 pooled window, so it is not independent of them in calendar time.
- **Evidence:** `docs/research/ORB_SMALL_MIDCAP_MOMENTUM_LANE_FEASIBILITY.md`, commit `2276ab9c9baba09f89aa1b3be8d9a1e66053e644`, blob `db336f7408e93d88063dbbad42886210b16100f7`.
- **Could support:** a descriptive statement that a bounded feasibility screen was run and classified `NOT_PROMISING`, retained in the search accounting.
- **Could not support:** any inferential claim; any claim the signal does not transfer, since the sample cannot distinguish that from noise.

## CH-4 — Standalone systematic ETF strategies at fixed capital

- **Family:** FH-2. **Variants covered:** M48 `ETF_PULLBACK_RSI2`, `SECTOR_MOMENTUM_12_1`, `DIVERSIFIED_TREND_12M` (3 variants, mapping to TI-05, TI-06, TI-07).
- **Research question:** Does each frozen ETF strategy family, simulated chronologically on its own $30,000 account with modeled transaction costs, produce a materially positive mean monthly dollar return?
- **Economic rationale (repository-supported):** three distinct, separately documented mechanisms in M48: short-horizon mean reversion (`ETF_PULLBACK_RSI2`), cross-sectional 12-1 relative strength across sector ETFs (`SECTOR_MOMENTUM_12_1`), and time-series trend-following across a diversified 8-symbol universe (`DIVERSIFIED_TREND_12M`). These are three different published effect families, not three parameterizations of one.
- **Evaluation window:** 2022-01-03 to 2026-08-13, chronological, base (5bps) and stress (10bps) cost scenarios.
- **Unit of observation:** month, matching the primary endpoint unit; daily returns used for resampling blocks.
- **Directionality:** one-sided against a zero/benchmark null is defensible because the research question is explicitly about reaching a positive capital target; two-sided is nevertheless recommended for symmetry with FH-1 unless the reviewer objects. Decision 5.
- **Overlapping-window exposure:** the ETF window (2022-01-03 to 2026-08-13) is nested inside the momentum window (2021-01-04 to 2026-08-13) and overlaps the ORB pooled window. Not independent.
- **Selection-bias exposure:** M48 arms were themselves selected after M43-M47 ORB results proved below target, a sequential, results-conditioned choice.
- **Non-evaluable conditions:** requires the daily portfolio return series, currently `SUMMARY_ONLY` pending preflight.
- **Evidence:** `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_FEASIBILITY.md`, commit `2d2a17e14415b6fc56c7c4cf45e9cc71c94164c0`, blob `16a263925ee91b14787bfbe80d347d58bae1ec2e`. Round-4 figures are authoritative; Round-1 and Round-2 are superseded.
- **Could support:** per-arm statements about historical mean monthly dollars with adjusted uncertainty, on this window, at these cost assumptions.
- **Could not support:** forward-looking income claims; any claim surviving without the multiplicity treatment; anything about behavior at other capital levels, since M54 linear scaling is explicitly not a forecast.

## CH-5 — Shared-capital multi-sleeve combination

- **Family:** FH-2. **Variant covered:** M49 `PRACTICAL_CORE_SATELLITE_30K` (TI-08).
- **Research question:** Does combining ORB, a tactical ETF sleeve, and a core ETF sleeve in one shared-cash $30,000 account, under fixed allocation and risk caps, produce a better risk-adjusted outcome than the sleeves considered separately?
- **Economic rationale (repository-supported):** M49 frames this as a real-account constraint question, one shared cash account with whole shares, where sleeves contend for the same capital, so combined performance is not the sum of standalone performances. This is a genuine portfolio-construction question, distinct from the CH-4 standalone arms.
- **Evaluation window:** 2022-01-03 to 2026-08-13, identical to CH-4.
- **Unit of observation:** month.
- **Comparator:** genuinely contested. See `COMPARATOR_ESTIMAND_AND_OUTCOME_MAP.md` and Decision 6. Candidates include a fixed-allocation benchmark of the same sleeves, or the capital-weighted sum of standalone sleeve results.
- **Overlapping-window exposure:** total with CH-4, since M49 reuses the M48 arms inside the shared allocator on identical dates. **TI-08 is not independent of TI-05, TI-06, or TI-07 in any meaningful sense**, which materially affects the multiplicity treatment.
- **Selection-bias exposure:** M49 allocation caps were fixed before results, with no sweep performed, but the sleeves themselves were chosen from already-observed M48 outcomes.
- **Additional documented limitation:** the sector-momentum sleeve contributed 63.9% of positive P&L, above the 50% single-sleeve concentration ceiling.
- **Evidence:** `docs/research/ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_FEASIBILITY.md`, commit `6de1794052be7ad439ce9b6b38ecd0e162dc8703`, blob `b1f9410ed234e12d124c02c48145074408f97c4e`.
- **Could support:** a statement about the shared-account historical mean monthly dollars and drawdown, with adjusted uncertainty, and the authoritative ~$59.00/month ORB standalone figure that supersedes the ~$108.82-$108.90 reference arithmetic.
- **Could not support:** that the combination is optimal, since no allocation sweep was run by design; any forward income claim; the superseded ~$108.9 figure presented as a simulated result.

## CH-6 — Concentrated cross-sectional equity momentum

- **Family:** FH-3. **Variant covered:** M52 `TOP15_MOMENTUM_BASELINE` (TI-09).
- **Research question:** Does a top-15, point-in-time IWB cross-sectional 12-1 momentum strategy with an SPY 200-session trend filter, long-only and whole-share on a fixed $30,000 account, produce a materially positive mean monthly dollar return?
- **Economic rationale (repository-supported):** selected by the frozen ten-criterion M51 literature/feasibility rubric (9/10 criteria, `SELECTED_FOR_M52`) as cross-sectional momentum with a market/trend filter on a $30,000 account. The rationale is the published cross-sectional momentum effect, screened against implementability constraints, not an ad-hoc construction.
- **Evaluation window:** 2021-01-04 to 2026-08-13, 69 monthly signals.
- **Unit of observation:** month, giving 69 rebalance observations; daily returns for resampling; cross-sectional dependence within each rebalance date must be preserved.
- **Overlapping-window exposure:** contains the entire CH-4/CH-5 window and overlaps the ORB pooled window from 2021 through 2026.
- **Selection-bias exposure:** M51 selected this family after M43-M50 had already been observed. M52 also has a documented corrected defect, a missing-bar zero-marking artifact that produced a superseded ~97% drawdown and `NOT_PROMISING` result; the authoritative result is the corrected rerun.
- **Additional documented limitation:** 20.85% of holdings were `UNRESOLVED` or `AMBIGUOUS` at ticker-validation time.
- **Non-evaluable conditions:** requires daily series (preflight) and, for any factor-attribution component, the Fama-French/UMD series, which is `NOT_AVAILABLE` in the record.
- **Evidence:** `docs/research/CROSS_SECTIONAL_MOMENTUM_FEASIBILITY_M52.md`, commit `f91827b490635244811fe13fcd59520439c90977`, blob `30ce10fd41eae26cbcc20943d089f80362625c3b`.
- **Could support:** a statement about historical mean monthly dollars, drawdown, and worst month with adjusted uncertainty, explicitly research-only.
- **Could not support:** that returns exceed known factor exposures, since no factor data exists; any deployability or income claim, given ~34% drawdown and ~-25% worst month; any prospective claim, which is the M55 role and it has zero cycles.

## CH-7 — Risk overlays on the momentum candidate

- **Family:** FH-3. **Variants covered:** M53 `BREADTH_30`, `MARKET_VOL_15` (2 variants, mapping to TI-10, TI-11).
- **Research question:** Does widening breadth from top-15 to top-30, or applying a lagged market-volatility exposure cap, improve the risk-return trade-off of the frozen M52 candidate?
- **Economic rationale (repository-supported):** M53 documents both mechanisms explicitly: breadth as diversification of idiosyncratic concentration, and a lagged market-risk throttle without changing stock selection for the volatility arm. Both are risk-control interventions on an unchanged selection rule, chosen because M52 drawdown, not its return, was the binding failure.
- **Evaluation window:** identical to CH-6, 2021-01-04 to 2026-08-13, using the immutable M52 caches, with verified bit-for-bit baseline parity.
- **Unit of observation:** month, paired to the same 69 M52 signal dates.
- **Comparator:** the M52 baseline itself. This comparator is **determined by design, not contested**: M53 was built to reproduce M52 exactly before varying one parameter, and parity was machine-verified across 58 scalar fields plus 12 segments. TI-10 and TI-11 are genuinely paired comparisons.
- **Directionality:** two-sided on return. The risk outcome, drawdown, is directional by construction since the overlay is intended to reduce it, but must still be reported two-sided given that `MARKET_VOL_15` moved drawdown slightly the wrong way, by +0.15pp.
- **Overlapping-window exposure:** total with CH-6. TI-09, TI-10, and TI-11 share one window, one cache, and one selection rule.
- **Selection-bias exposure:** exactly two arms were predeclared with no sweep, a genuine constraint, but the family was reached only after the M52 result was known.
- **Evidence:** `docs/research/MOMENTUM_RISK_CONTROL_FEASIBILITY_M53.md`, commit `f31889bf9023c1a653d50e0f368b205a3ac3f6c7`, blob `7b59fb2a65510d7dae8bc55683304c9536f3df09`.
- **Could support:** paired statements about the effect of each overlay on mean monthly dollars and drawdown versus the exact baseline, with adjusted uncertainty.
- **Could not support:** that no risk control could work, since only two were tested by design; any claim the overlays make the strategy deployable, given that the M53 decision was `PARKED_NO_CREDIBLE_RISK_CONTROL`.

---

## Candidate test-instance index

Nulls are candidate formulations pending Decision 5. Estimand detail is in `COMPARATOR_ESTIMAND_AND_OUTCOME_MAP.md`.

| TI | CH | FH | Variant | Comparator | Candidate null (H0) | Candidate alternative (H1) | Eligibility |
|---|---|---|---|---|---|---|---|
| TI-01 | CH-1 | FH-1 | M43 `BE_AFTER_050R` | M43 `BASELINE` (paired) | Population mean paired difference in R-expectancy is zero | Not zero | Candidate, pending preflight |
| TI-02 | CH-1 | FH-1 | M43 `PROFIT_LADDER` | M43 `BASELINE` (paired) | Same | Not zero | Candidate, pending preflight |
| TI-03 | CH-1 | FH-1 | M43 `STRUCTURE_TRAIL_AFTER_1R` | M43 `BASELINE` (paired) | Same | Not zero | Candidate, pending preflight |
| TI-04 | CH-2 | FH-1 | M45 `LATER_FIRST_VALID_1020` | Baseline-only policy | Combined-policy R/month equals baseline-only R/month | Not equal | Candidate, pending preflight |
| TI-05 | CH-4 | FH-2 | M48 `ETF_PULLBACK_RSI2` | **Undecided (Decision 6)** | Mean monthly dollars equals comparator | Not equal | Candidate, pending preflight and comparator |
| TI-06 | CH-4 | FH-2 | M48 `SECTOR_MOMENTUM_12_1` | **Undecided (Decision 6)** | Same | Not equal | Candidate, pending preflight and comparator |
| TI-07 | CH-4 | FH-2 | M48 `DIVERSIFIED_TREND_12M` | **Undecided (Decision 6)** | Same | Not equal | Candidate, pending preflight and comparator |
| TI-08 | CH-5 | FH-2 | M49 `PRACTICAL_CORE_SATELLITE_30K` | **Undecided (Decision 6)** | Shared-account mean monthly dollars equals comparator | Not equal | Candidate, pending preflight and comparator |
| TI-09 | CH-6 | FH-3 | M52 `TOP15_MOMENTUM_BASELINE` | **Undecided (Decision 6)** | Mean monthly dollars equals comparator | Not equal | Candidate, pending preflight and comparator |
| TI-10 | CH-7 | FH-3 | M53 `BREADTH_30` | M52 baseline (paired, parity-verified) | Paired difference in mean monthly dollars, and in max drawdown, is zero | Not zero | Candidate, pending preflight |
| TI-11 | CH-7 | FH-3 | M53 `MARKET_VOL_15` | M52 baseline (paired, parity-verified) | Same | Not zero | Candidate, pending preflight |

### Non-executable evaluations, retained in search accounting

| ID | CH | FH | Variant | Status | Why no test instance | Permanent |
|---|---|---|---|---|---|---|
| DO-01 | CH-2 | FH-1 | M44 `FALLBACK_FIRST_VALID_0950` | `DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE` | 2 genuine closed trades, with 7 of 9 nominal selections `DATA_INVALID`; no resampling or test can be credibly fit to n=2 | **Yes**, independent of preflight |
| DO-02 | CH-3 | FH-1 | M46 `SMALL_MID` | `DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE` | 20 trades, single 90-session period, self-described feasibility screen | **Yes**, independent of preflight |
| SN-01 | CH-2 | FH-1 | M47 `SEQUENTIAL_SECOND_VALID_1020` | `NOT_EVALUABLE_STRUCTURAL_NULL` | 0 qualifying second trades; only 15 of 122 first trades were even eligible. No estimand is defined on zero observations | **Yes**, independent of preflight |

**No observations, return series, test statistics, or p-values may be fabricated for DO-01, DO-02, or SN-01 under any circumstance.** They remain in the search-universe accounting in `MULTIPLE_TESTING_AND_MODEL_SET_OPTIONS.md` because they consumed genuine looks at the data, and omitting them would understate the search.
