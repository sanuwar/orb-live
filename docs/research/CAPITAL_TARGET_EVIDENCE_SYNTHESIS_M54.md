# M54: $30K Capital-Target Evidence Synthesis and Forward-Path Decision Gate

**Issue:** [#360](https://github.com/sanuwar/orb-live/issues/360)
**Branch:** `agent/m54-capital-target-evidence-synthesis`
**Depends on:** the complete M43-M53 committed record. M54 is not a new strategy experiment; it introduces no new threshold, universe, lookback, stop, hedge, allocation, or risk overlay, and fetches no new market data.

> **This section is committed BEFORE the final evidence tables and conclusions are written**, matching the pre-results sequencing discipline established in M51-M53: the protocol, decision-label definitions, and capital-scaling formulas are frozen first; the reconciled evidence and the official M54 conclusion are added in a separate, later commit.

## Purpose

Consolidate the complete M43-M53 empirical record into one auditable answer to four questions:

1. What profit range is actually supported for a fixed $30,000 account?
2. Is either $500/month or $1,000/month supported under the established risk limits?
3. How much capital would the existing positive strategies approximately require to reach those dollar targets, without assuming improved percentage returns?
4. Should the next activity be prospective paper monitoring, a genuinely different strategy-family investigation, publication preparation, or termination of retrospective strategy searching?

## Anti-analysis-paralysis rule

- No new market data fetch. No new backtest. No strategy alteration or optimization.
- No new threshold, universe, lookback, stop, hedge, allocation, or risk overlay.
- No automatic M55.
- Only committed M43-M53 reports, result JSON/hashes, and merged Git history are used as source evidence.
- Where two historical documents conflict, reconciled against the final corrected committed artifact, with the discrepancy documented explicitly rather than silently resolved.
- Finished in one issue/branch/PR.

## Method

### Evidence extraction

Every M43-M53 milestone's final, currently-committed report document (and, where a milestone produced one, its machine-readable result JSON / committed hashes file) was read directly from the merged-`main` checkout. Where a milestone's report document itself records a correction (a later commit that changed a reported figure or a classification's stated reason), the CURRENT committed text -- already reflecting that correction -- is treated as authoritative; the original, superseded figure is reported alongside it only for audit-trail completeness, never as the number carried into a table total or a capital-scaling calculation.

Git commit history (`git log --oneline -- <path>`) was checked per document to confirm that no further, more recent correction exists beyond what the document's own text already describes.

### Capital-target mathematics (`src/capital_target_evidence_synthesis.py`)

Pure, tested, deterministic functions -- no strategy logic, no market data:

- `monthly_return_pct_for_target`: the monthly percent return a fixed dollar target implies on a given capital base (`target / capital`).
- `simple_annual_pct`: simple (non-compounding) annualization (`monthly_pct * 12`).
- `compounded_annual_pct`: compounded annualization (`(1 + monthly_pct)^12 - 1`).
- `required_capital_for_target`: the frozen linear-scaling formula, `observed_starting_capital * target_monthly_dollars / observed_mean_monthly_dollars` -- applied only to a strategy with a genuinely positive, actually-simulated observed mean monthly profit; refuses on a non-positive input rather than producing a nonsensical or infinite scaling result.
- `scaled_drawdown_dollars`: `required_capital * observed_max_drawdown_pct` -- the dollar drawdown implied at the scaled capital, holding the OBSERVED drawdown PERCENTAGE fixed. This does **not** assume drawdown improves with more capital; it is the most conservative default absent contrary evidence, and is always reported next to an explicit non-guarantee warning.

Every calculation in the results tables is reproducible from these functions and the committed source figures; the pinned unit tests reconcile representative values (including the exact $500/$1,000-on-$30,000 percentages Issue #360 specifies: 1.6667%/20% simple annual, and 3.3333%/40% simple annual) against hand-computed expectations.

### Frozen decision labels

Applied to the evidence exactly as defined by Issue #360, never weakened or redefined:

1. **`FORWARD_VALIDATE_RISK_CONTROLLED_CANDIDATE`** -- only if a prior candidate already satisfies ALL of: base mean monthly profit >= $500; stress-cost outcome positive; max drawdown <= 25%; worst month >= -10%; adequate calendar-segment consistency; no unresolved integrity failure.
2. **`PAPER_MONITOR_RESEARCH_ONLY_CANDIDATE`** -- a strategy that is consistently profitable and economically meaningful but violates one or more of the risk limits above. May be monitored prospectively with simulated capital; must never be activated.
3. **`SUPPORTED_BELOW_TARGET`** -- the highest defensible practical monthly-profit range supported by a design that satisfies its own applicable safety/risk evidence better than the high-return candidates.
4. **`NO_CREDIBLE_30K_TARGET_PATH`** -- neither $500/month nor $1,000/month is supported at the established risk limits by any completed milestone.
5. **`INSUFFICIENT_EVIDENCE`** -- reserved for a conclusion that genuinely cannot be made because required committed evidence is missing or irreconcilable (applied per-milestone where applicable, e.g. a milestone blocked by a data-coverage gate before any economic result was computed).

Multiple labels may coexist. A strategy is never promoted merely for being the best result observed among those examined.

## Required evidence inventory (scope)

Final corrected results for: M43 profit protection, M44 fallback recovery, M45 later-first-entry recovery, M46 small/mid-cap momentum lane, M47 sequential second trade, M48 three ETF strategy arms and combination plausibility, M49 shared-capital core-satellite portfolio, M50 untouched-validation attempt and its data limitation, M51 strategy-family selection, M52 cross-sectional momentum, M53 breadth and volatility risk controls.

For every relevant economic strategy/design: starting capital, evaluation period, mean monthly fixed-notional net profit, stress-cost monthly profit where available, CAGR/annualized return where available, maximum drawdown, worst month, positive calendar segments, classification, data/provenance limitation, and whether it is risk-controlled, research-only, parked, or unavailable for inference.

---

## 1. Executive answer

**Best supported safer monthly-profit range from $30K:** roughly **$150-$250/month**, anchored on M49's `PRACTICAL_CORE_SATELLITE_30K` result: **$184.75/month base-cost, $122.02/month stress-cost**, 14.60% max drawdown, `SAFE_BUT_BELOW_TARGET`. This is the only genuinely positive $30K design whose own applicable risk evidence (drawdown, worst month, segment consistency) is clean, not merely "less bad than the alternatives."

**Best observed high-return result:** M52's concentrated cross-sectional momentum baseline, **$456.87/month base-cost, $439.23/month stress-cost**, 15.15% CAGR, `POSITIVE_BUT_INSUFFICIENT`. M53's `MARKET_VOL_15` arm reproduces this almost exactly ($456.72/month) without materially reducing its risk.

**Why $500/month did not pass:** no design that reaches $500/month base-cost also satisfies the established risk limits at the same time. M52/M53's highest-return designs clear the $500 dollar bar only marginally if at all (M52 $456.87 and M53 Arm B $456.72 both fall just short of $500 even before considering risk; M53 Arm A clears risk better but only reaches $328.24) and every one of them blows through the 25% drawdown ceiling (27%-34%) and the -10% worst-month floor (-21% to -25%). The single design that *does* satisfy its own risk evidence cleanly (M49) reaches only $184.75/month, roughly 37% of the $500 target.

**Why $1,000/month is not credible:** no completed milestone's dollar-denominated result reaches even half of $1,000/month on a base-cost basis after satisfying its own risk evidence. The only support for $1,000/month anywhere in the record is M51's own explicit, unsimulated caveat that it "is not a credible baseline expectation" for the momentum family it went on to select -- a judgment this synthesis's actual M52/M53 results (peaking at $456.87/month) directly confirms rather than contradicts.

**Official M54 labels (full reasoning in Section 6):** `NO_CREDIBLE_30K_TARGET_PATH` (overall, both $500 and $1,000), with `PAPER_MONITOR_RESEARCH_ONLY_CANDIDATE` for the M52/M53 momentum family and `SUPPORTED_BELOW_TARGET` for M49's core-satellite design coexisting underneath it.

## 2. Milestone evidence table

One final row per M43-M53. "Metric type" flags whether a milestone produced dollar-denominated figures (usable in Sections 3-4) or R-multiple-only figures (not directly comparable, reported for completeness only).

| Milestone | Issue | Design(s) | Metric type | Final classification | Status |
|---|---|---|---|---|---|
| M43 | #336 | 4 exit arms x `WAITED_FIRST_VALID_0950`(primary)/`CURRENT_FIRST_VALID`(sensitivity) | R-multiple only | Primary: `SUPPORTED`; sensitivity: `MIXED` | Research-only, not activated |
| M44 | #340 | `FALLBACK_FIRST_VALID_0950` | R-multiple + non-simulated R x $300 arithmetic | `INSUFFICIENT_EVIDENCE` (n=2 closed fallback-only trades) | Parked -- data limitation (7 of 9 candidates `DATA_INVALID`) |
| M45 | #342 | `LATER_FIRST_VALID_1020` | R-multiple + non-simulated R x $300 arithmetic | `MIXED` (7 of 10 predeclared conditions pass) | Not carried forward; BASELINE remains the supported policy |
| M46 | #344 | Small/mid-cap momentum lane: MID, SMALL, SMALL_MID vs. LARGE_CAP reference | R-multiple only | `NOT_PROMISING` (3 of 9 criteria pass) | Parked explicitly -- no sweep or retune authorized |
| M47 | #346 | `SEQUENTIAL_SECOND_VALID_1020` | R-multiple only | `INSUFFICIENT_EVIDENCE` (n=0, structurally null) | Parked -- definitive null finding, not a data artifact |
| M48 | #348 | ARM_A `ETF_PULLBACK_RSI2`, ARM_B `SECTOR_MOMENTUM_12_1`, ARM_C `DIVERSIFIED_TREND_12M` | Dollar-denominated (base/stress, CAGR, $ drawdown) | All three arms: `POSITIVE_BUT_INSUFFICIENT`; combination verdict: `COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE` for $1,000/month | Research-only |
| M49 | #350 | `PRACTICAL_CORE_SATELLITE_30K` (ORB + ARM_A satellite + ARM_B core, shared $30K account) | Dollar-denominated | `SAFE_BUT_BELOW_TARGET` | Research-only -- safest genuinely-positive $30K result in the record |
| M50 | #352 | Untouched 2019-2020 out-of-sample validation of the frozen M49 portfolio | N/A -- blocked before any economic result | `INSUFFICIENT_EVIDENCE` (Alpaca IEX minute-bar coverage floor at 2020-07-27; corrected from an original, incorrect universe-sourcing-gate failure) | Blocked -- no economic outcome computed |
| M51 | #354 | 4-candidate strategy-family selection gate (momentum, PEAD, option premium, trend following) | N/A -- evidence/rubric review, no new backtest | `SELECTED_FOR_M52` (momentum, 9 of 10 rubric criteria) | Selection gate only |
| M52 | #356 | Concentrated point-in-time cross-sectional equity momentum, top-15, SPY trend filter | Dollar-denominated | `POSITIVE_BUT_INSUFFICIENT` | Research-only -- highest observed dollar result in the record |
| M53 | #358 | Arm A `BREADTH_30` (top-30); Arm B `MARKET_VOL_15` (top-15 + lagged vol-exposure cap) | Dollar-denominated | Both arms: `POSITIVE_BUT_INSUFFICIENT`; overall: `PARKED_NO_CREDIBLE_RISK_CONTROL` | Parked -- neither risk control reached `TARGET_FEASIBLE` |

## 3. Apples-to-apples strategy table (dollar-denominated designs only)

Every design below ran on the same $30,000 fixed-notional convention. M43-M47's R-multiple-only studies and M44/M45's explicitly-non-simulated R x $300 arithmetic are excluded from this table -- they are not chronological capital simulations and mixing them with M48-M53's actual portfolio simulations would misrepresent both. Figures are the final, currently-committed numbers (Round 4 for M48; the corrected-and-verified state for every other milestone).

| Strategy | Milestone | Capital | Evaluation period | Base mean $/mo | Stress mean $/mo | CAGR | Max drawdown | Worst month | Positive segments | Classification |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| ARM_A `ETF_PULLBACK_RSI2` | M48 | $30,000 | 2022-01-03 to 2026-08-13 | $78.53 | net-positive ($1,039.14 pooled; monthly mean not separately published) | 3.05% | 9.04% ($2,713) | -$1,435.78 | 4/5 | `POSITIVE_BUT_INSUFFICIENT` |
| ARM_B `SECTOR_MOMENTUM_12_1` | M48 | $30,000 | 2022-01-03 to 2026-08-13 | $196.72 | net-positive ($10,804.29 pooled; monthly mean not separately published) | 7.25% | 23.56% ($7,519) | -$3,083.18 | 4/5 | `POSITIVE_BUT_INSUFFICIENT` |
| ARM_C `DIVERSIFIED_TREND_12M` | M48 | $30,000 | 2022-01-03 to 2026-08-13 | $177.26 | net-positive ($9,996.36 pooled; monthly mean not separately published) | 6.80% | 26.79% ($8,981) | -$2,301.42 | 4/5 | `POSITIVE_BUT_INSUFFICIENT` |
| `PRACTICAL_CORE_SATELLITE_30K` | M49 | $30,000 | 2022-01-03 to 2026-08-13 | **$184.75** | **$122.02** | 7.05% | 14.60% ($4,381.32) | -$2,398.37 (5th-pct month -4.60%) | 4/5 | **`SAFE_BUT_BELOW_TARGET`** |
| ORB standalone (uncontended, M49-derived) | M49 | $30,000 | 2022-01-03 to 2026-08-13 | $59.00 | not separately reported | not reported | not reported | not reported | not reported | reference only -- see Section 3a |
| Cross-sectional momentum, top-15 | M52 | $30,000 | 2021-01-04 to 2026-08-13 | **$456.87** | **$439.23** | 15.15% | 34.14% ($26,253.20) | -25.08% | 5/6 | `POSITIVE_BUT_INSUFFICIENT` |
| `BREADTH_30` (Arm A) | M53 | $30,000 | 2021-01-04 to 2026-08-13 | $328.24 | $320.47 | 11.22% | 27.25% | -20.79% | 4/6 | `POSITIVE_BUT_INSUFFICIENT` |
| `MARKET_VOL_15` (Arm B) | M53 | $30,000 | 2021-01-04 to 2026-08-13 | $456.72 | $451.42 | 15.38% | 34.29% | -25.13% | 5/6 | `POSITIVE_BUT_INSUFFICIENT` |

### 3a. A reconciled discrepancy: ORB's "reference economics" figure was wrong

M44, M45, M48, and M51 all cite ORB's `BE_AFTER_050R` result as approximately **$108.82-$108.90/month**, computed as a simple, explicitly-non-simulated `0.363 R/month x $300` multiplication -- M44's own doc states outright that this is "NOT a chronological capital simulation." M49, which actually ran ORB through a real position-sized, cost-modeled, chronological $30,000 simulation (as the standalone, uncontended lane inside its shared-account architecture), found the real figure to be **$59.00/month** -- roughly 46% lower. M49's own report explicitly flags this and states it is using the $59.00 figure "as the more rigorous figure precisely because it comes from an actual simulation of the frozen baseline," rather than silently overwriting the earlier informal figure.

Per this synthesis's reconciliation rule (final, currently-committed, most-rigorous artifact wins), **$59.00/month is the authoritative ORB standalone dollar figure**, not $108.82-$108.90/month. This does not change any milestone's own classification (none of M44/M45/M48/M51's classifications depended on the exact ORB dollar figure), but it matters for Section 4's capital-scaling table and for anyone reading M44/M45/M48/M51 in isolation and assuming their R x $300 arithmetic was a real simulated result.

## 4. Capital-scaling table

**Non-guarantee warning, stated once and applying to every row below:** `required_capital = 30000 x target_monthly_profit / observed_mean_monthly_profit` is a mechanical linear rescaling of an *already-observed historical mean*. It is not a forecast, not a guarantee, and it assumes the historical percent return holds unchanged at a larger capital base -- an assumption this synthesis makes explicit, never implicit. The scaled drawdown dollar figure holds the *observed drawdown percentage* fixed; it does **not** imply drawdown improves with more capital, and real execution frictions (market impact, liquidity, slippage) at multiples of the tested capital are not modeled at all. These are order-of-magnitude reference points, not investment plans.

Computed via `required_capital_for_target`/`scaled_drawdown_dollars` (`src/capital_target_evidence_synthesis.py`), pinned against these exact figures in `tests/test_capital_target_evidence_synthesis.py`.

$500/month on $30,000 = **1.6667% monthly**, **20.00% simple annual** (21.94% compounded annual). $1,000/month on $30,000 = **3.3333% monthly**, **40.00% simple annual** (48.21% compounded annual).

| Strategy | Observed base mean $/mo | Capital required for $500/mo | Multiple of $30K | Scaled drawdown at $500 target | Capital required for $1,000/mo | Multiple of $30K | Scaled drawdown at $1,000 target |
|---|---:|---:|---:|---:|---:|---:|---:|
| ARM_A `ETF_PULLBACK_RSI2` (M48) | $78.53 | $191,009.81 | 6.37x | $17,267.29 | $382,019.61 | 12.73x | $34,534.57 |
| ARM_B `SECTOR_MOMENTUM_12_1` (M48) | $196.72 | $76,250.51 | 2.54x | $17,964.62 | $152,501.02 | 5.08x | $35,929.24 |
| ARM_C `DIVERSIFIED_TREND_12M` (M48) | $177.26 | $84,621.46 | 2.82x | $22,670.09 | $169,242.92 | 5.64x | $45,340.18 |
| `PRACTICAL_CORE_SATELLITE_30K` (M49) | $184.75 | $81,190.80 | 2.71x | $11,853.86 | $162,381.60 | 5.41x | $23,707.71 |
| ORB standalone, reconciled (M49-derived) | $59.00 | $254,237.29 | 8.47x | not applicable (drawdown not separately reported) | $508,474.58 | 16.95x | not applicable |
| Cross-sectional momentum, top-15 (M52) | $456.87 | $32,832.10 | 1.09x | $11,208.88 | $65,664.19 | 2.19x | $22,417.76 |
| `BREADTH_30` (M53 Arm A) | $328.24 | $45,698.27 | 1.52x | $12,452.78 | $91,396.54 | 3.05x | $24,905.56 |
| `MARKET_VOL_15` (M53 Arm B) | $456.72 | $32,842.88 | 1.09x | $11,261.82 | $65,685.76 | 2.19x | $22,523.65 |

Reading this table honestly: M52/M53's headline designs need only ~9% more capital to hit the $500/month dollar figure mechanically -- but that scaling holds the SAME 27%-34% drawdown percentage, meaning a scaled account would still be expected to see a ~$11,000-12,000 (or, at the $1,000 target, ~$22,000-25,000) peak-to-trough decline. M49's design needs to grow to roughly 2.7x its size to reach $500/month, with a comparatively gentler ~$11,900 scaled drawdown at that size. ORB standalone would need to grow more than 8x just to reach $500/month, underscoring why it was never treated as a standalone $30K candidate.

## 5. Research-search accounting

**Distinct strategy/variant hypotheses actually backtested across M43-M53:** 14 -- M43's 4 exit arms (x2 entry-policy sensitivity), M44's fallback policy, M45's later-entry policy, M46's 3 lane variants (MID/SMALL/SMALL_MID), M47's sequential-second-trade policy, M48's 3 ETF arms, M49's shared-capital blend, M52's momentum baseline, and M53's 2 risk-control arms. Two additional strategy families (PEAD, option premium) were evaluated on paper in M51's rubric but never backtested (PEAD blocked on data-licensing cost; option premium blocked on Robinhood margin-account incompatibility) -- they do not count as tested hypotheses, only as screened-and-rejected candidates.

**Repeated-use evaluation windows:** the six pooled ORB periods (`P_MINUS_2` through `P3`, spanning 2021-03-31 through 2026-08-13) were reused, in full or in part, across M43, M44, M45, M46 (P3 only), and M47 -- five separate milestones testing different variants against the same underlying historical sample. Separately, M48 and M49 share the identical 2022-01-03 to 2026-08-13 window, and M52/M53 share the identical 2021-01-04 to 2026-08-13 window (M53 explicitly reuses M52's own cached data, by design, since it is testing risk controls on the same strategy, not a new draw). No milestone in this record tested a genuinely fresh, previously-unused historical sample except M50's attempted 2019-2020 window -- which was blocked before producing any result.

**Why further same-data retrospective searching increases risk, concretely:** 14 hypotheses is enough that, under standard multiple-comparisons reasoning, at least one "positive-looking" result among them is expected to arise from sampling noise alone even if no tested design has genuine forward edge -- and every one of those 14 hypotheses drew from one of only three overlapping historical windows (the ORB 2021-2026 pool, the ETF/core-satellite 2022-2026 window, and the momentum 2021-2026 window). Running an M55 that tests yet another variant against any of these same windows would add another comparison to an already-multiple-tested sample without adding new information about how any design behaves on data it has never influenced or been selected against. This is exactly the failure mode formal preregistration and out-of-sample holdout sets exist to prevent. The Git commits recording each milestone's frozen protocol (M51, M52, M53, and now M54) are **time-stamped, version-controlled frozen protocols** -- a genuine, auditable discipline that prevented result-driven tuning within each milestone -- but they are explicitly **not** a substitute for formal preregistration or true multiple-testing correction across the full 14-hypothesis search, and this report does not claim otherwise.

**The one genuine out-of-sample attempt in the record failed on data, not on strategy performance:** M50's attempt to validate the frozen M49 portfolio on an untouched 2019-2020 window was blocked by a real Alpaca minute-bar coverage floor (2020-07-27), not by a negative result. This means **no design in this entire record has yet been confirmed on data it did not influence** -- a materially important gap this synthesis surfaces rather than glosses over.

## 6. Forward-path decision

Reconciled against Sections 1-5, the four choices are weighed as follows. `FORWARD_VALIDATE_RISK_CONTROLLED_CANDIDATE` is unavailable (no design satisfies all its gates simultaneously -- see Section 1). Further same-data retrospective strategy-family research (a fifth ETF arm, a fourth M53-style risk control, another ORB variant) would add another comparison to an already 14-hypothesis, three-overlapping-window search without resolving the one real gap this record has: **no design has been confirmed on data it never touched**. Publication/methodology-paper preparation is valuable but is a documentation activity, not a way to close that gap, and Section 7 already begins that work regardless of which path is chosen. New independent data acquisition for true untouched validation is the only path that would fully resolve M50's blocker, but it requires a paid data subscription or vendor change -- a cost/scope decision for the operator, not something this bounded synthesis milestone can authorize or begin on its own.

**Decision: prospective simulated (paper) monitoring of the M52/M53 momentum family going forward, alongside stopping further same-data retrospective $500/$1,000 backtest searching under current constraints.**

This is the one path available today that produces genuinely new, never-fit, forward-only evidence without new data cost, without capital risk (`strategy.enabled` remains `false`; this is monitoring, never activation), and without adding another comparison to the already multiple-tested 2021-2026 historical sample. It directly targets M52 -- the record's highest-return, `PAPER_MONITOR_RESEARCH_ONLY_CANDIDATE` design -- since it is the design a forward confirmation would matter most for. New independent data acquisition (for a genuine M50-style untouched-history retry) is flagged as the necessary complementary path if and when the operator chooses to fund it, but is not started by this milestone. No M55 backtest is authorized by this decision.

## 7. Publication-readiness appendix

**Auditable workflow:** every milestone in this record (M43-M54) followed the same human-led, LLM-assisted pattern -- the operator specified the protocol, scope, safety boundaries, and (from M51 onward) explicit frozen decision rules; the assistant implemented the simulation/analysis code, wrote and ran the tests, and drafted the reports, with every implementation committed to version control *before* the corresponding empirical result was read. Genuine defects discovered during implementation (M46's concentration-formula bug, M47's universe-membership bug, M48's four rounds of corrections, M52's OpenFIGI/Alpaca/Stage-0/equity-marking fixes) were fixed with regression tests and honest reruns, documented in-place rather than silently absorbed into a "final" number.

**Negative/null/data-limited results preserved, not discarded:** M44 (`INSUFFICIENT_EVIDENCE`, n=2), M46 (`NOT_PROMISING`), M47 (`INSUFFICIENT_EVIDENCE`, n=0, a definitive structural null), M50 (`INSUFFICIENT_EVIDENCE`, blocked by a real data-coverage gap), and M53 (`PARKED_NO_CREDIBLE_RISK_CONTROL`) are all reported in Section 2 on equal footing with the positive results, not omitted.

**Remaining requirements before any of this record could support a scholarly paper:**
- **Formal multiple-testing correction** across the full 14-hypothesis, 3-overlapping-window search (Section 5) -- this synthesis documents the search honestly but does not apply a Bonferroni, false-discovery-rate, or similar statistical correction to any reported result.
- **Confidence intervals / statistical significance testing** on every reported mean-monthly-dollar and drawdown figure -- every number in Sections 3-4 is a single point estimate over one historical sample, with no resampling, bootstrap, or standard-error estimate attached.
- **Factor regressions** to determine whether M52/M53's momentum returns are genuinely idiosyncratic or substantially explained by known equity factors (market beta, size, value, momentum-factor exposure itself) -- none of M43-M53 ran a factor decomposition.
- **Independent out-of-sample validation** on data none of the designs influenced -- the one attempt (M50) was blocked, as documented in Section 5.
- **A reproducibility package** -- the code is version-controlled and the artifact hashes are committed, but a full external-reproduction package (frozen environment, one-command re-run instructions, published-not-just-cached raw data) does not yet exist.
- **Data licensing clarity** -- SEC EDGAR filings and Alpaca SIP/IEX market data each carry their own redistribution terms that have not been reviewed for publication compatibility.
- **AI-use disclosure** -- this record was produced with substantial LLM assistance (Claude Sonnet 5) under human direction at every milestone; any resulting publication would need to disclose this per applicable venue policy.

This report does not claim novel edge, statistically validated profitability, or publication-ready findings. It claims only what Sections 1-6 support: a reconciled, honest accounting of what has and has not been established, and a bounded, low-cost next step.
