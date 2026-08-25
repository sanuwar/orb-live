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

## The four things we most need your judgment on

1. **Comparators (Decision 6).** Five of eleven test instances measure absolute dollar returns on a fixed $30,000 account with no design-determined comparator. This single choice can flip the sign and significance of nearly half the results. What should the primary comparator be?

2. **Multiplicity denominator (Decision 8).** 11 instances are executable; 14 variants were decision-eligible; 21 evaluations were displayed; and an unenumerable exploratory search preceded all of it. Which denominator is honest?

3. **Unit incommensurability (Decision 7).** One family measures R-multiples, two measure dollars. Should we decline a union-level procedure and report three within-family corrections with an explicit no-union-control disclosure?

4. **Non-evaluable variants (Decisions 8, 9).** Three variants (n=2, 20 trades in one period, n=0) cannot supply a defined loss differential. We propose excluding them from any executable model set while retaining them in the search narrative. Is that defensible, or does the omission invalidate the data-snooping procedures?

## Tables you will need

| Table | Location |
|---|---|
| Hypothesis levels and count reconciliation (7 / 3 / 11 / 14 / 17 / 21) | `FORMAL_HYPOTHESIS_CATALOG_CANDIDATE.md` |
| Per-instance comparator, estimand, unit, outcomes, required series | `COMPARATOR_ESTIMAND_AND_OUTCOME_MAP.md` |
| Family structure, dependence, FWER/FDR and SPA options, window overlap | `MULTIPLE_TESTING_AND_MODEL_SET_OPTIONS.md` |
| All 12 unresolved decisions with alternatives and blocking status | `STATISTICAL_DECISION_REGISTER.md` |
| Input availability and the cache-integrity preflight requirement | `docs/publication/p2/STATISTICAL_INPUT_INVENTORY.md` |

## Constraints you should know before advising

- **No granular series is currently verified.** Every test instance depends on daily or per-trade data that is presently classified `SUMMARY_ONLY`, pending a cache-integrity preflight that has not run. If the preflight fails, the executable count drops below 11.
- **Factor data does not exist in the record.** Any Fama-French or UMD attribution requires a new, license-reviewed acquisition.
- **Market data cannot be redistributed.** The vendor's published policy prohibits redistribution of raw API data, which constrains what a reproducibility package can contain.
- **Windows overlap heavily.** Three families share calendar time from 2021 to 2026; the ETF window is nested inside the momentum window.
- **The search was sequential and results-conditioned.** Each family was reached after earlier families underperformed. Frozen within-milestone protocols limit within-milestone tuning but cannot correct across-milestone selection.
- **There is no clean holdout.** M50 was data-blocked and M55 has zero completed cycles, so no design has been confirmed on data it never influenced.

## What we are explicitly not claiming

That the protocol is preregistered; that Git commits substitute for a registry; that any strategy is production-ready, deployable, or reliably profitable; that LLM assistance improved research quality; or that any result in the record is statistically significant. None of those claims appears anywhere in this package, and the claim-discipline rules in P1 and P2 prohibit them.
