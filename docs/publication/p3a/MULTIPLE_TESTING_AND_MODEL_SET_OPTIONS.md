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
| **Candidate executable test instances** | **11** | Variants with a credible defined estimand, pending preflight | **The correction denominator, if and only if a reviewer confirms it** |

**The gap between 11 and 21 is the central honesty problem of this package.** Correcting over 11 while 21 looks were taken understates the search. Correcting over 21 when 10 of those looks cannot produce a test statistic overstates the procedure's applicability. Neither is obviously right, and P3A does not resolve it; it is Decision 8 combined with Decision 9.

Additionally, the record documents **pre-M43 exploratory antecedents** (ORB discovery/selection work referenced by M43-M47 and listed in P1's `HYPOTHESIS_AND_WINDOW_AUDIT.md` as outside the formal registry). Their raw candidate count cannot be reconstructed. Any multiplicity claim must state qualitatively that the true search was larger than 21, even though only 21 are enumerable.

## 2. Candidate family-wise organization

Three families, matching P2's corrected `strategy_family` split:

| Family | Test instances | Unit | Internal dependence |
|---|---|---|---|
| FH-1 | TI-01, TI-02, TI-03, TI-04 | R-multiples | Very high. TI-01 to TI-03 are paired contrasts on the identical 122 occurrences against a shared baseline; TI-04 shares the same sessions |
| FH-2 | TI-05, TI-06, TI-07, TI-08 | Dollars on $30,000 | Very high. TI-08 reuses TI-05 to TI-07 arms inside a shared allocator on identical dates |
| FH-3 | TI-09, TI-10, TI-11 | Dollars on $30,000 | Very high. TI-10 and TI-11 are paired contrasts against TI-09 on the identical 69 signal dates and the identical cache |

**Every family is internally near-degenerate.** Within-family test statistics are strongly positively dependent by construction, not by coincidence. This is favorable for Romano-Wolf, which is designed to exploit dependence rather than assume independence, and unfavorable for naive Bonferroni, which would be extremely conservative here.

## 3. FWER options

| Option | Fit to this evidence | Concern |
|---|---|---|
| **Romano-Wolf stepdown, within-family** (preliminary recommendation) | Strong. Handles arbitrary dependence via joint resampling; unit-consistent within each family | Requires the joint resampling design (Decisions 1-4) to be settled first |
| Romano-Wolf stepdown, across all 11 | Possible only after Decision 7 resolves the R-multiple vs. dollars unit mismatch | Would require an arbitrary standardization the reviewer has not sanctioned |
| Bonferroni / Holm | Simple, defensible, transparent | Severely conservative under the near-degenerate dependence documented above; would likely reject nothing and mislead readers into treating that as evidence of absence |

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
