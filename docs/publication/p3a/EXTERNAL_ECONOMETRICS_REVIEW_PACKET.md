# External Econometrics Review Packet

**No reviewer has been contacted. No review has occurred.** This document is prepared *for* a review that has not happened. Its existence is not evidence of review, and no statement in this package may be cited as reviewed, endorsed, or validated.

## What we are asking for

An independent methodological review of a **candidate** statistical analysis plan for a retrospective algorithmic-trading research record. We are **not** asking you to validate a trading strategy, and we make no claim that any strategy here is profitable or deployable. We are asking whether the proposed inference design is sound enough to execute, and if not, what must change.

## Repository context in one page

ORB-LIVE is a private research repository containing a sequential program of 13 numbered milestones (M43-M55) investigating whether a $30,000 account could support a target monthly withdrawal. **The program's own synthesis concluded `NO_CREDIBLE_30K_TARGET_PATH`.** No strategy was activated; `strategy.enabled` has been `false` throughout; no broker order was ever placed from research code.

What exists today:

- 14 decision-eligible strategy variants across 3 families, plus controls and diagnostic sleeves totalling 21 displayed evaluations.
- Committed per-milestone reports with frozen protocol sections, deterministic classification rules, and artifact hash manifests.
- Point estimates only. **No confidence interval, significance test, multiplicity correction, or factor regression has ever been computed anywhere in this record.**
- One attempted untouched-holdout validation (M50) that was blocked by a vendor data-coverage floor before producing any economic result.
- One prospective paper-monitoring protocol (M55) initialized with zero completed cycles.

## The five things we most need your judgment on

1. **Comparators (Decision 6).** Five of eleven test instances measure absolute dollar returns on a fixed $30,000 account with no design-determined comparator. This single choice can flip the sign and significance of nearly half the results. What should the primary comparator be?

2. **Executable model set versus search accounting (Decision 8).** 11 instances can supply a defined test statistic; 14 variants were decision-eligible; 21 evaluations were displayed; and an unenumerable exploratory search preceded all of it. The formal procedure can only run over instances that supply observations, so the wider counts cannot simply be used as a larger denominator. Is running the correction over the 11 executable instances, with the 14/17/21 search disclosed narratively, the right split — and if a heuristic sensitivity at larger hypothetical denominators is also reported, is labelling it informal sufficient?

3. **Within-family versus union-level control (Decision 7).** One family measures R-multiples, two measure dollars. Studentized statistics would make a union-level procedure technically feasible despite that, so the question is not whether mixed units forbid it. Is union-level FWER control worth the extra cross-family resampling assumptions over heavily overlapping windows, or is per-family control with an explicit no-union-control disclosure sounder?

4. **Path-dependent risk estimands (Decision 13).** Maximum drawdown and worst month are functionals of the whole equity path, not per-month observations, so they cannot enter a paired-mean estimand or a per-period resampling scheme the way returns can. How should their uncertainty and multiplicity treatment work alongside return endpoints — and is it acceptable for CH-7, whose motivating question is explicitly about the risk-return trade-off, to have its risk dimension answered only descriptively?

5. **Non-evaluable variants (Decisions 8, 9).** Three variants (n=2, 20 trades in one period, n=0) cannot supply a defined loss differential. We propose excluding them from any executable model set while retaining them in the search narrative. Is that defensible, or does the omission invalidate the data-snooping procedures?

## Tables you will need

| Table | Location |
|---|---|
| Hypothesis levels and count reconciliation (7 / 3 / 11 / 14 / 17 / 21) | `FORMAL_HYPOTHESIS_CATALOG_CANDIDATE.md` |
| Per-instance comparator, estimand, unit, outcomes, required series | `COMPARATOR_ESTIMAND_AND_OUTCOME_MAP.md` |
| Family structure, dependence, FWER/FDR and SPA options, window overlap | `MULTIPLE_TESTING_AND_MODEL_SET_OPTIONS.md` |
| All 13 unresolved decisions with alternatives and blocking status | `STATISTICAL_DECISION_REGISTER.md` |
| Input availability and the cache-integrity preflight requirement | `docs/publication/p2/STATISTICAL_INPUT_INVENTORY.md` |

## Constraints you should know before advising

- **No granular series is currently verified.** Every test instance depends on daily or per-trade data presently classified `SUMMARY_ONLY`, pending a cache-integrity preflight that has not run. That preflight is split into a read-only inventory phase and a deterministic-regeneration phase; the second is a historical simulation rerun and requires separate operator authorization. If either fails, the executable count drops below 11.
- **Factor data does not exist in the record.** Any Fama-French or UMD attribution requires a new, license-reviewed acquisition.
- **Market data cannot be redistributed.** The vendor's published policy prohibits redistribution of raw API data, which constrains what a reproducibility package can contain.
- **Windows overlap heavily.** Three families share calendar time from 2021 to 2026; the ETF window is nested inside the momentum window.
- **The search was sequential and results-conditioned.** Each family was reached after earlier families underperformed. Frozen within-milestone protocols limit within-milestone tuning but cannot correct across-milestone selection.
- **There is no clean holdout.** M50 was data-blocked and M55 has zero completed cycles, so no design has been confirmed on data it never influenced.

## What we are explicitly not claiming

That the protocol is preregistered; that Git commits substitute for a registry; that any strategy is production-ready, deployable, or reliably profitable; that LLM assistance improved research quality; or that any result in the record is statistically significant. None of those claims appears anywhere in this package, and the claim-discipline rules in P1 and P2 prohibit them.
