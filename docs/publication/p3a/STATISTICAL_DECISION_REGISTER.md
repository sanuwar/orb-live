# Statistical Decision Register

**All decisions below are UNRESOLVED.** P3A deliberately does not silently resolve any of them to make P3 executable. Each carries alternatives, a preliminary recommendation where one is defensible, consequences, an exact reviewer question, and a blocking classification.

**Blocking classification:**
- `BLOCKS_FREEZE` — the protocol cannot be frozen until this is settled.
- `BLOCKS_ALL` — no test instance can execute until this is settled.
- `BLOCKS_FAMILY(x)` — only the named family or instances are blocked.

Nine decisions carry forward from P2 (D1-D9). Four are added by P3A (D10-D13): three from separating hypothesis levels, and one (D13) from recognizing that path-dependent risk measures are not per-period observations.

---

## D1 — Bootstrap replication count

- **Alternatives:** 1,000 (fast, coarse tail resolution); 5,000; 10,000 (conventional for stepdown procedures); adaptive until Monte Carlo error falls below a stated threshold.
- **Preliminary recommendation:** 10,000, with Monte Carlo standard error reported alongside every interval so readers can see the simulation noise floor. Not adopted.
- **Statistical consequences:** too few replications makes stepdown p-value ordering unstable, which matters more here than usual because within-family test statistics are highly dependent and closely spaced.
- **Reproducibility consequences:** must be pinned before execution and reported; changing it after seeing results is a degree of freedom.
- **Reviewer question:** Given three families of 3-4 highly dependent test instances each, what replication count gives stable Romano-Wolf ordering, and should Monte Carlo error be reported per instance?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_ALL`.

## D2 — Random seed

- **Alternatives:** a single fixed seed committed in advance; a set of seeds with results reported across all of them; a seed derived deterministically from the committed data hash.
- **Preliminary recommendation:** single fixed seed committed before execution, plus a stability check at two additional seeds reported in an appendix. Not adopted.
- **Statistical consequences:** none for validity if fixed in advance; fatal for credibility if chosen after seeing results.
- **Reproducibility consequences:** the seed plus the data hash must fully determine every reported interval.
- **Reviewer question:** Is a single pre-committed seed with a two-seed stability appendix sufficient, or do you require full multi-seed reporting?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_ALL`.

## D3 — Block-length selection

- **Alternatives:** Politis-White automatic selector; a fixed grid of roughly one month, one quarter, and half-window reported side by side; a single length justified by the strategy rebalance frequency.
- **Preliminary recommendation:** Politis-White as primary with the fixed grid as a mandatory sensitivity, since a conclusion holding at only one block length must be visible as such. Not adopted.
- **Statistical consequences:** block length governs how much serial dependence is preserved; too short understates variance and overstates significance.
- **Reproducibility consequences:** if data-driven, the selector output must be committed as an artifact, since it is itself a function of the data.
- **Reviewer question:** Is Politis-White appropriate for monthly-rebalance strategy returns with 69 observations (FH-3) and for session-clustered R-multiples (FH-1), or does the small monthly sample argue for a fixed defensible length?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_ALL`.

## D4 — Common versus family-specific resampling rules

- **Alternatives:** one common block length and scheme across all families; family-specific lengths with joint drawing on shared calendar dates; fully separate per-family resampling with no cross-family joint draw.
- **Preliminary recommendation:** family-specific lengths, but **joint drawing on shared calendar dates**, because the windows genuinely overlap and independent per-family resampling would destroy the contemporaneous dependence that makes the overlap a problem in the first place. Not adopted.
- **Statistical consequences:** fully separate resampling would understate joint uncertainty; a single common length may fit no family well, given that FH-1 is session-clustered while FH-2 and FH-3 are monthly.
- **Reproducibility consequences:** the joint-draw index construction must be committed as code, not described in prose.
- **Reviewer question:** Can family-specific block lengths be combined with a joint cross-family draw without invalidating the stepdown null distribution, and if not, which property should be sacrificed?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_ALL`.

## D5 — Exact null hypotheses

- **Alternatives:** zero-mean nulls; benchmark-relative nulls; nulls stated on a risk-adjusted quantity rather than a raw mean; one-sided versus two-sided per family.
- **Preliminary recommendation:** two-sided throughout, with the null form following the comparator class: benchmark-relative nulls for the five contested-comparator instances (TI-05 to TI-09); two-sided paired-difference nulls for the five genuinely paired instances (TI-01, TI-02, TI-03, TI-10, TI-11); and a two-sided policy-contrast null for TI-04, whose comparator is design-determined but whose contrast is not cleanly paired, since M45 adds trades on sessions where the baseline had none. Six instances have design-determined comparators, but only five of those six are genuinely paired. Two-sided is recommended despite the directional research questions because the committed record contains genuine wrong-direction results: `STRUCTURE_TRAIL_AFTER_1R` fell below baseline, and `MARKET_VOL_15` moved drawdown 0.15pp the wrong way. Not adopted.
- **Statistical consequences:** one-sided tests would roughly halve p-values and are only defensible if the direction was fixed before observation, which cannot be established for arms reached after prior results were known.
- **Reviewer question:** Is two-sided the correct default given the sequential, results-conditioned path by which each family was reached?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_ALL`.

## D6 — Exact comparators

- **Alternatives for TI-05 through TI-09:** zero; SPY buy-and-hold; IWB buy-and-hold, the parent universe for TI-09; an equal-weight benchmark over the eligible universe; a volatility-matched benchmark; a risk-free or capital-cost hurdle; and for TI-08 specifically, a fixed-allocation benchmark of the same sleeves or the capital-weighted sum of standalone sleeves.
- **Preliminary recommendation:** report against both a zero null and a matched buy-and-hold benchmark, treating the benchmark-relative result as primary, since an absolute-dollar claim on a $30,000 account is not economically interpretable without an opportunity-cost baseline. Not adopted.
- **Statistical consequences:** **this decision can flip the sign and significance of five of the eleven results.** A momentum strategy returning $456.87 per month may be strongly positive against zero and indistinguishable from, or worse than, IWB buy-and-hold over a window containing a strong equity bull phase.
- **Reproducibility consequences:** the benchmark series is itself Alpaca-derived and therefore subject to the redistribution constraint documented in the P2 licensing matrix.
- **Reviewer question:** For each of TI-05 through TI-09, which single comparator should be primary, and should the same comparator apply across FH-2 and FH-3 or be family-specific?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_FAMILY(FH-2, FH-3)`. FH-1 is unaffected, since its comparators are design-determined.

## D7 — Test-statistic standardization

- **Alternatives:** studentized test statistics (effect divided by its own bootstrap standard error), which are dimensionless and permit a union-level procedure across mixed-unit families; raw estimand differences within family only, with no cross-family procedure; conversion of all estimands to return-on-capital; conversion of dollar results to an R-equivalent.
- **Preliminary recommendation:** none strong enough to state. A union-level procedure using studentized statistics is **technically available** — studentization is dimensionless and does not require converting R-multiples into dollars — so mixed raw units are not on their own a reason to decline it. An earlier draft recommended declining the union procedure on unit grounds; that reasoning was wrong and is withdrawn. The real trade-off is that union-level control requires the joint resampling design (D1-D4) and model-set definition (D8, D9) to support cross-family alignment over heavily overlapping windows, whereas three within-family procedures need fewer assumptions but provide no union-level error control. Which is preferable is a reviewer judgement. Not adopted.
- **Statistical consequences:** three within-family procedures control FWER within each family but not across the 11-instance union, and that limitation must be stated explicitly rather than papered over. A union-level studentized procedure would control FWER across all 11 but rests on the joint resampling scheme being valid across families with different natural observation units (session-clustered versus monthly) and overlapping windows. Raw point estimates remain non-comparable under either option and must never be ranked or averaged across families.
- **Reviewer question:** Given that studentized statistics make a union-level procedure technically feasible across mixed-unit families, is union-level FWER control worth the additional cross-family resampling assumptions, or is per-family control with an explicit no-union-control disclosure the sounder choice?
- **Blocking:** `BLOCKS_FREEZE`. Blocks only the union-level procedure, not within-family analysis.

## D8 — Treatment of non-evaluable variants

- **Alternatives:** exclude DO-01, DO-02, and SN-01 from the executable set but retain them in the search denominator; exclude entirely from both; include via a conservative worst-case bound; report a sensitivity in which the correction denominator is 11 versus 14 versus 21.
- **Preliminary recommendation:** exclude from the executable set, retain in the search denominator, and report the correction at both an 11-instance and a 21-look denominator as an explicit sensitivity, so the reader can see how much the conclusion depends on that choice. Not adopted.
- **Statistical consequences:** this is **not** a simple scaling of adjusted p-values, and an earlier draft of this register wrongly implied it was. Romano-Wolf, White Reality Check, and Hansen SPA all require a defined test statistic or an observation-level loss differential for **every member of the model set**; a variant with n=0 or n=2 supplies neither, so it cannot be inserted into the resampling procedure at all, and no denominator arithmetic can substitute for the missing observations. Adjusted p-values from a stepdown procedure also do not generally scale linearly with the number of hypotheses: they depend on the joint null distribution of the test statistics, so adding or removing members changes the resampled maxima rather than multiplying a scalar. The executable model set is therefore bounded by evaluability, while the 14/17/21 counts serve a different and equally necessary purpose: transparent search-history and selection-bias disclosure. A report that presents adjusted results at a hypothetical larger denominator must label that a **heuristic selection-sensitivity analysis**, not a formally valid Romano-Wolf or SPA correction.
- **Reviewer question:** Given that only evaluable instances can enter an executable model set, should the formal correction run over the 11 executable instances with the wider 14/17/21 search disclosed narratively, or is a heuristic sensitivity at larger hypothetical denominators also worth reporting, clearly labelled as informal?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_ALL`, since the model-set definition is an input to the joint null distribution of every adjusted p-value.

## D9 — White Reality Check and Hansen SPA model-comparison universe

- **Alternatives:** restrict the model set to the 11 executable instances; restrict to one family at a time; abandon Reality Check and SPA in favour of Romano-Wolf alone; include non-evaluable variants only in the narrative.
- **Preliminary recommendation:** run SPA within family only, on executable instances, with the full 21-look narrative reported adjacent to it, and drop White Reality Check as redundant where SPA applies. Not adopted.
- **Statistical consequences:** a model set that silently omits 10 of 21 looks understates data-snooping severity; one that includes instances with no defined loss differential is not computable.
- **Reviewer question:** Is a within-family SPA on executable instances, with explicit narrative disclosure of the wider search, an acceptable compromise, or does the omission invalidate the procedure?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_FAMILY(all)` for the Reality Check and SPA component only.

---

## Decisions added by P3A

D10 to D12 arise from separating conceptual hypotheses from variant-level test instances, a distinction P2 did not draw. D13 arises from a distinct correction: risk functionals had been specified as if they were per-period paired observations.

## D10 — Inference level: conceptual hypothesis versus test instance

- **Statement:** should adjusted inference be reported per test instance (11 results), per conceptual hypothesis (at most 6, since CH-3 has no instance), or both?
- **Alternatives:** per-instance only; per-conceptual-hypothesis via a combining rule such as a min-p or a pre-specified representative instance; both, with one designated primary.
- **Preliminary recommendation:** report per test instance as primary, with conceptual-hypothesis-level statements made only narratively. Combining three paired M43 arms into a single CH-1 verdict would require a combining rule that is itself a researcher degree of freedom. Not adopted.
- **Statistical consequences:** per-instance reporting increases the multiplicity burden; hypothesis-level combining reduces it but requires an arbitrary combining rule.
- **Reviewer question:** Is per-instance primary reporting with narrative hypothesis-level interpretation the right level, or should a formal combining rule be pre-specified?
- **Blocking:** `BLOCKS_FREEZE`.

## D11 — Whether paired within-milestone contrasts and absolute-level tests belong in one multiplicity family

- **Statement:** FH-3 mixes an absolute-level test (TI-09, momentum versus an undecided external comparator) with two paired contrasts against that same instance (TI-10, TI-11). FH-1 has the same structure. Should paired contrasts and absolute-level tests share a family?
- **Alternatives:** keep them together as the current family definition does; split each family into an absolute-level sub-family and a paired-contrast sub-family; treat paired contrasts as secondary and exempt from the primary correction.
- **Preliminary recommendation:** split into sub-families, because the paired contrasts ask a materially different question (does the overlay change the baseline) from the absolute test (does the baseline beat a benchmark). Paired designs typically yield lower-variance estimates than absolute-level ones by removing shared variation, though the magnitude of that reduction here is unverified until the granular series exist. Not adopted.
- **Statistical consequences:** mixing them means paired and absolute statistics with potentially quite different variances enter the same stepdown ordering. Whether that materially distorts the ordering depends on the realized variance ratio, which is unverified.
- **Reviewer question:** Does mixing paired and absolute test statistics in one Romano-Wolf family distort the stepdown, and if so is a sub-family split the correct remedy?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_FAMILY(FH-1, FH-3)`.

## D12 — Status of the pre-M43 exploratory antecedents in the search denominator

- **Statement:** P1 records exploratory ORB discovery and selection work preceding M43 whose raw candidate count cannot be reconstructed. How should an unenumerable prior search enter the accounting?
- **Alternatives:** qualitative disclosure only; a stated lower bound with explicit uncertainty; a sensitivity analysis showing how conclusions change at hypothetical larger denominators; exclusion with disclosure.
- **Preliminary recommendation:** qualitative disclosure, optionally accompanied by a **heuristic selection-sensitivity analysis** at inflated hypothetical denominators so a reader can gauge whether any conclusion looks fragile to a plausibly larger true search. Such an analysis is explicitly **not** a formally valid Romano-Wolf or SPA correction: the unenumerable antecedents supply no test statistics, and stepdown adjusted p-values do not scale linearly with hypothesis count. It must be labelled as informal wherever it appears. Not adopted.
- **Statistical consequences:** if the unenumerable prior search was large, every adjusted p-value reported here is optimistic, and no procedure can repair that from the committed record alone.
- **Reviewer question:** Is a clearly-labelled heuristic selection-sensitivity analysis at hypothetical denominators scientifically honest, or does it invite over-interpretation of an unknowable quantity that no formal procedure can bound?
- **Blocking:** `BLOCKS_FREEZE`.

## D13 — Path-dependent risk estimands, endpoint status, and multiplicity treatment

- **Statement:** maximum drawdown, worst month, downside deviation, and expected shortfall are functionals of the complete equity path, not per-month observations. They cannot enter a paired-mean estimand or a per-period block-resampling scheme the way returns can. Three sub-questions follow: how should their uncertainty be characterized, are they primary or secondary endpoints, and how do they enter the multiplicity treatment alongside return endpoints?
- **Alternatives:**
  - *Uncertainty:* read the functional off the empirical distribution of resampled equity paths; use a functional-specific procedure; or report the point estimate with no interval and say so.
  - *Endpoint status:* risk functionals primary (matching the CH-7 research question, which is explicitly about the risk-return trade-off); risk secondary and descriptive; or co-primary with a stated multivariate rule.
  - *Multiplicity:* correct return and risk endpoints in one family; correct them in separate families; or correct returns only and report risk descriptively.
- **Preliminary recommendation:** characterize risk-functional uncertainty from the distribution of resampled paths; treat return as primary and risk as co-reported but separately corrected; and **do not** construct a joint null combining mean monthly return and max drawdown unless the reviewer supplies an explicit multivariate decision rule. Not adopted.
- **Statistical consequences:** treating drawdown as a paired monthly mean would be a category error and would produce intervals with no valid interpretation. Correcting return and risk endpoints in one family without a multivariate rule risks an incoherent verdict, where an arm "wins" on one dimension and "loses" on the other with no stated resolution.
- **Reproducibility consequences:** the path-functional resampling must be committed as code, since a drawdown distribution depends on the exact resampling scheme used to generate the paths.
- **Reviewer question:** How should path-dependent risk functionals be given uncertainty and multiplicity treatment alongside per-period return endpoints, and for CH-7 specifically, is it acceptable for the milestone's motivating risk question to be answered only descriptively if risk is demoted to a secondary endpoint?
- **Blocking:** `BLOCKS_FREEZE`, `BLOCKS_FAMILY(FH-3)` for the risk-endpoint component. The return-only analysis for FH-3 is not blocked by D13 alone.

---

## Blocking summary

| Decision | Blocks freeze | Blocks all execution | Blocks specific family |
|---|:---:|:---:|---|
| D1 replication count | Yes | Yes | — |
| D2 random seed | Yes | Yes | — |
| D3 block length | Yes | Yes | — |
| D4 common vs family-specific resampling | Yes | Yes | — |
| D5 exact nulls | Yes | Yes | — |
| D6 exact comparators | Yes | No | FH-2, FH-3 |
| D7 standardization | Yes | No | Union-level procedure only |
| D8 non-evaluable treatment | Yes | Yes | — |
| D9 Reality Check / SPA universe | Yes | No | Reality Check and SPA component |
| D10 inference level | Yes | No | — |
| D11 paired vs absolute families | Yes | No | FH-1, FH-3 |
| D12 pre-M43 antecedents | Yes | No | — |
| D13 path-dependent risk estimands | Yes | No | FH-3 risk-endpoint component |

**All thirteen decisions block protocol freezing. Six of them (D1, D2, D3, D4, D5, D8) block all statistical execution.** No subset of the analysis can proceed until at least those six are resolved by a qualified external finance/econometrics reviewer, and none has been resolved here.
