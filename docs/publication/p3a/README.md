# Publication P3A — Formal Hypothesis Specification and Econometric Review Package

**Evidence cutoff:** main at `e20ef011282fa08c3703b5af1811143e74b2c780` (immediately after Publication P2, PR #367, merged).
**Issue:** #368. **Scope:** `docs/publication/p3a/` only.

## Purpose

P3A converts P2's hypothesis ledger and statistical-protocol candidate into a **scientifically reviewable hypothesis package**: precise candidate research questions, null and alternative hypotheses, comparators, estimands, analysis units, and the statistical decision points a qualified external reviewer must resolve.

## No statistical analysis was executed

**P3A ran no statistical analysis of any kind.** No bootstrap, no resampling, no regression, no significance test, no confidence interval, no multiplicity correction, no Reality Check or SPA computation. No market data, factor data, benchmark data, or external dataset was fetched. No strategy simulation, backtest, or parameter experiment was run. No cached trade or return series was opened, parsed, or reconstructed. No M55 checkpoint was run and no M55 ledger was altered. `strategy.enabled` remains `false`.

Every number in this package is copied from a committed M43–M55 artifact, cited by repository path, commit SHA, and Git blob hash. P3A produces **specifications**, not results.

## The distinction this package exists to preserve

P2 established that 11 of 14 decision-eligible variants are `INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA`. **Those 11 are not 11 independent scientific hypotheses.** P3A separates five levels that earlier documents risked collapsing:

| Level | Count | Meaning |
|---|---:|---|
| Conceptual research hypotheses | **7** | Distinct economic questions the research program actually asks |
| Family-level hypotheses | **3** | Multiplicity families, matching P2's corrected `strategy_family` split |
| Variant-level statistical test instances (candidate) | **11** | Individual test instances that *could* be executed, pending granular-data verification and reviewer sign-off |
| Descriptive-only evaluations | **2** | M44 `FALLBACK_FIRST_VALID_0950` (n=2), M46 `SMALL_MID` (20 trades, single 90-session period) |
| Structural-null / non-evaluable | **1** | M47 `SEQUENTIAL_SECOND_VALID_1020` (n=0 qualifying observations) |

11 + 2 + 1 = 14 decision-eligible variants, reconciling exactly with P2's ledger. The 17 displayed arm labels and 21 arm-by-entry-policy evaluations are carried forward unchanged for search accounting.

One conceptual hypothesis (CH-3, ORB universe extension) has **zero** executable test instances. This is a genuine finding about the limits of the committed record, not a gap in this catalog.

## Deliverables

| File | Purpose |
|---|---|
| `README.md` | This file. |
| `MANUSCRIPT_RESEARCH_QUESTIONS.md` | Primary methods-paper questions and secondary empirical questions; which are answerable now. |
| `FORMAL_HYPOTHESIS_CATALOG_CANDIDATE.md` | Conceptual, family-level, and variant-level catalog with nulls, alternatives, and evidence citations. |
| `COMPARATOR_ESTIMAND_AND_OUTCOME_MAP.md` | Comparator, estimand, observation unit, outcomes, and required data per test instance. |
| `MULTIPLE_TESTING_AND_MODEL_SET_OPTIONS.md` | Candidate family structure, search-universe accounting, FWER/FDR and Reality Check/SPA options. |
| `STATISTICAL_DECISION_REGISTER.md` | All unresolved decisions with alternatives, recommendations, consequences, reviewer questions, blocking status. |
| `EXTERNAL_ECONOMETRICS_REVIEW_PACKET.md` | Concise reviewer-facing package. |
| `P3A_READINESS_DECISION.md` | Readiness for external review, P3B preflight, protocol freeze, and statistical execution. |

## Status of the protocol

The statistical-analysis protocol remains a **time-stamped, version-controlled protocol candidate**. It is *not* frozen, *not* preregistered, and *not* execution-authorized. A later commit may freeze it only after the methodological decisions in `STATISTICAL_DECISION_REGISTER.md` are resolved by a qualified external finance/econometrics reviewer. No reviewer has been contacted; this package is prepared *for* review, and its existence is not evidence that review has occurred.

## Boundaries observed

Committed M43–M55 and P1–P2 evidence only. No external data acquisition, no analysis execution, no reviewer contact, no changes to production, broker, strategy, configuration, or M55 checkpoint code or data, no repository-visibility change, and no trading milestone started.
