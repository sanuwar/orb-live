# Multiple Testing and Model-Set Options

**CANDIDATE, NON-EXECUTABLE SPECIFICATIONS.** No multiplicity procedure was run, no p-value computed, no model set assembled.

## 1. Search-universe accounting

The denominator of any multiplicity claim must reflect what was actually tried, not what survived. Three counts carry forward unchanged from P2, plus one new count introduced by P3A.

| Count | Value | What it includes | Role in multiplicity |
|---|---:|---|---|
| Conceptual research hypotheses | 7 | Distinct economic questions (CH-1 through CH-7) | Narrative framing; not a test count |
| Decision-eligible variants | 14 | Novel, predeclared, decision-eligible strategy variants | **The honest denominator for selection-bias narrative** |
| Displayed arm labels | 17 | 14 plus M43 `BASELINE` control and M46 `MID`/`SMALL` diagnostic sleeves | Broader look-count |
| Arm-by-entry-policy evaluations | 21 | 17 plus 4 M43 `CURRENT_FIRST_VALID` sensitivity replays | Widest look-count |
| **Candidate executable test instances** | **11** | Variants with a credible defined estimand, pending preflight | **The only candidates for an executable model set**, subject to reviewer confirmation |

**The gap between 11 and 21 is the central honesty problem of this package, but it is not a single choice of "denominator."** Two distinct things must both be reported, and conflating them is the error this section exists to prevent:

- **The executable model set** is bounded by evaluability, not by preference. Romano-Wolf, White Reality Check, and Hansen SPA each require a defined test statistic or an observation-level loss differential for every member. A variant with n=0 (SN-01) or n=2 (DO-01) supplies neither and **cannot be numerically inserted into a resampling procedure at all**. There is no arithmetic that substitutes for observations that do not exist.
- **The search-history accounting** (14 decision-eligible, 17 displayed, 21 arm-by-entry, plus unenumerable pre-M43 antecedents) is required for transparent selection-bias disclosure regardless of what the executable set contains. Dropping it because those looks are not testable would understate the search.

A further correction to an earlier draft: adjusted p-values from a stepdown procedure **do not generally scale linearly with the number of hypotheses**. They depend on the joint null distribution of the test statistics, so changing model-set membership changes the resampled maxima rather than multiplying a scalar. Any report that shows adjusted results at a hypothetical larger denominator must be labelled a **heuristic selection-sensitivity analysis**, not a formally valid Romano-Wolf or SPA correction. This is Decision 8 combined with Decision 9, and P3A does not resolve it.

Additionally, the record documents **pre-M43 exploratory antecedents** (ORB discovery/selection work referenced by M43-M47 and listed in P1's `HYPOTHESIS_AND_WINDOW_AUDIT.md` as outside the formal registry). Their raw candidate count cannot be reconstructed. Any multiplicity claim must state qualitatively that the true search was larger than 21, even though only 21 are enumerable.

## 2. Candidate family-wise organization

Three families, matching P2's corrected `strategy_family` split:

| Family | Test instances | Unit | Internal dependence |
|---|---|---|---|
| FH-1 | TI-01, TI-02, TI-03, TI-04 | R-multiples | Shared inputs: TI-01 to TI-03 are paired contrasts on the identical 122 occurrences against a shared baseline; TI-04 shares the same sessions. Substantial positive dependence is plausible; magnitude unverified |
| FH-2 | TI-05, TI-06, TI-07, TI-08 | Dollars on $30,000 | Shared inputs: TI-08 reuses the TI-05 to TI-07 arms inside a shared allocator on identical dates. Substantial positive dependence is plausible; magnitude unverified |
| FH-3 | TI-09, TI-10, TI-11 | Dollars on $30,000 | Shared inputs: TI-10 and TI-11 are paired contrasts against TI-09 on the identical 69 signal dates and the identical cache. Substantial positive dependence is plausible; magnitude unverified |

**Substantial positive dependence within each family is plausible**, given shared observations, shared baselines, shared dates, and shared strategy components. An earlier draft asserted that every family is "internally near-degenerate" and predicted which procedures would and would not reject; both claims went beyond the evidence and are withdrawn.

The **magnitude and structure** of that dependence are unverified and will remain so until the granular series pass the P3B preflight and the joint dependence can actually be measured. Romano-Wolf **may** offer power advantages if the joint dependence structure can be validly resampled, because it accommodates dependence rather than assuming independence. Holm and Bonferroni remain transparent fallbacks that **may** be conservative under positive dependence. **No prediction is made here about which hypotheses any procedure will or will not reject** — that is the analysis itself, and stating an expectation in advance would prejudge it.

## 3. FWER options

| Option | Fit to this evidence | Concern |
|---|---|---|
| **Romano-Wolf stepdown, within-family** (preliminary recommendation) | Accommodates arbitrary dependence via joint resampling; observation units are consistent within each family | Requires the joint resampling design (Decisions 1-4) to be settled first, and its power advantage over Holm is conditional on the dependence structure being validly resampled |
| Romano-Wolf stepdown, across all 11 | Technically available using reviewer-approved studentized statistics, which are dimensionless and do not require converting R-multiples into dollars | Requires cross-family observation alignment (session-clustered vs. monthly) and preservation of contemporaneous dependence across overlapping windows; see Decision 7 |
| Bonferroni / Holm | Simple, defensible, transparent; no resampling design required | May be conservative if the positive dependence suggested by shared inputs is in fact substantial. Whether that conservatism is material here is unverified. Any non-rejection must be reported as non-rejection, never as evidence of absence |

## 4. FDR options

Benjamini-Hochberg is defensible **only as a secondary, clearly-labeled sensitivity view**, never as the headline. Two cautions specific to this evidence: standard BH assumes independence or positive regression dependence, and while the within-family dependence here is positive, it has not been formally verified to satisfy PRDS; and with only 3 to 4 tests per family, FDR control offers little practical gain over FWER while inviting misreading as a weaker significance bar.

## 5. White Reality Check and Hansen SPA

Carried forward from P2's corrected section 10, restated here at test-instance granularity.

- Both procedures require, for **every** member of the comparison set, a defined loss or return differential series against the benchmark, observation by observation.
- **DO-01 (M44, n=2), DO-02 (M46, 20 trades in one period), and SN-01 (M47, n=0) cannot supply such a series.** SN-01 cannot supply one even in principle. No fabricated series or placeholder statistic may stand in for them.
- Consequently the executable model set is at most the 11 test instances, while the honest search narrative is 14 to 21 looks plus unenumerable pre-M43 antecedents. **These two numbers must both appear in any Reality Check or SPA reporting; presenting only the executable count would misstate the severity of the correction.**
- Hansen SPA is preferred over White Reality Check on power grounds where both apply, since SPA is designed to mitigate the loss of power White Reality Check suffers when poor benchmarks are included. Neither may execute until the reviewer defines an implementable model set (Decision 9).

## 6. Overlapping and reused windows

| Window family | Exact window | Test instances |
|---|---|---|
| ORB pooled | 2021-03-31 to 2026-08-13, six periods, 1,350 sessions | TI-01 to TI-04 (plus DO-01, DO-02 on P3 only, SN-01) |
| ETF / shared account | 2022-01-03 to 2026-08-13 | TI-05 to TI-08 |
| Cross-sectional momentum | 2021-01-04 to 2026-08-13 | TI-09 to TI-11 |

The ETF window is nested inside the momentum window; the momentum and ORB windows overlap from 2021 through 2026. Different instruments and frequencies do not make repeated market regimes statistically independent: a 2022 drawdown or a 2026 reversal appears in all three families at once. Any resampling scheme must therefore draw shared calendar dates jointly across families (P2 candidate protocol section 4), which is exactly why Decision 4 (common vs. family-specific resampling) is blocking rather than cosmetic.

## 7. Post-selection and researcher-degrees-of-freedom limitations

1. Surviving candidates were chosen after earlier results were observed, so their point estimates are selection-conditioned. No procedure in this document corrects that; it can only be disclosed.
2. Multiple frozen outcome gates per milestone (mean return, profit factor, drawdown, worst month, concentration, segment counts) create additional researcher degrees of freedom even when each threshold was fixed in advance.
3. Corrections were material and repeated (M48's 19 findings across four review rounds; M52's superseded 97% drawdown artifact). Corrected reruns improve truthfulness but do not restore an untouched holdout.
4. M50's data failure means **no completed historical validation sample exists that none of the designs influenced**, and M55 has zero completed prospective cycles. Multiplicity correction is therefore a partial remedy at best; it cannot substitute for out-of-sample evidence that does not exist.
