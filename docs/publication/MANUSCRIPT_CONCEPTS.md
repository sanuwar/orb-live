# Manuscript Concepts

## Candidate titles

1. **Frozen Before Results: An Auditable Human–LLM Workflow for Sequential Algorithmic-Trading Research**
2. **Preserving Null Results and Corrections in LLM-Assisted Computational Finance**
3. **ORB-LIVE: Version-Controlled Protocols, Evidence Lineage, and Safety Gates for Human-Led Trading Research**

## Concept A — methods/experience paper (recommended)

### Structured abstract concept

**Background:** LLM-assisted software development can accelerate computational research while increasing risks of silent configuration, data, accounting, and reporting errors. Algorithmic-trading research adds severe selection-bias, reproducibility, and safety concerns.

**Method:** We present a human-led workflow using time-stamped version-controlled frozen protocols, bounded experiments, deterministic classification rules, artifact hashes, parity and Stage-0 gates, explicit correction chains, and disabled execution paths. A sequential M43–M55 case record is audited at claim level.

**Results:** The workflow preserved supported, mixed, not-promising, structurally null, and data-blocked outcomes. Material implementation defects were detected by parity checks, fail-closed gates, manual cross-checks, and review, then corrected with regression tests and reruns. The audit also found unresolved multiple-testing, actor-provenance, data-licensing, and external-reproduction gaps. No production-ready or reliable-income strategy is claimed.

**Conclusion:** Version control can support an auditable frozen-protocol discipline, but it is not formal preregistration and does not replace statistical correction, independent validation, or human accountability. The case supports a governance methodology, not an efficacy claim for LLMs or a trading recommendation.

## Concept B — research-software paper

### Structured abstract concept

**Motivation:** Computational trading studies often lose provenance across changing protocols, data caches, simulations, and corrections.

**Software:** ORB-LIVE provides research modules for deterministic signal evaluation, chronological portfolio simulation, point-in-time universe handling, cost scenarios, artifact hashing, parity gates, and append-only prospective checkpoints, with broker execution disabled.

**Audit case:** Thirteen milestones demonstrate how the software records protocol freeze points, correction chains, null/data-limited results, and safety invariants.

**Availability:** This route requires a public OSI-licensed release, installation path, versioned documentation, synthetic reproducibility fixtures, archived release, and a lawful data-access recipe.

**Limit:** The software paper must not present backtest results as validated performance or claim full reproduction while raw vendor data remain non-redistributable.

## Concept C — empirical-finance paper (future only)

### Structured abstract concept

**Question:** Does the frozen point-in-time cross-sectional momentum candidate retain positive risk-adjusted performance after data-snooping adjustment, factor attribution, costs, and independent/prospective validation?

**Design required:** Pre-specified primary endpoint; joint block bootstrap and multiple-testing correction; factor regressions; benchmark comparisons; independent licensed historical data and completed M55 prospective cycles.

**Current evidence:** Historical means near $457/month coexist with roughly 34% drawdown and -25% worst-month outcomes. Risk-control variants did not achieve the frozen feasibility gates.

**Status:** Concept only. It must not be drafted as a results paper until the gap plan is completed.

## Recommended outline

1. Introduction: reproducibility and LLM risk in computational finance
2. Related work: preregistration, multiverse/multiple testing, research software, AI-assisted coding governance, and reproducible finance
3. ORB-LIVE context and non-deployment safety boundary
4. The human-led frozen-protocol workflow
5. Evidence objects: issues, commits, artifacts, hashes, ledgers, tests, and correction links
6. Sequential case-study design and M43–M55 registry
7. Results of the workflow audit
   - outcome preservation
   - defect/correction taxonomy
   - claim-evidence reconciliation
   - window reuse and multiplicity
8. Human–LLM responsibility and actor-provenance limits
9. Reproducibility, licensing, and archival design
10. Threats to validity
11. Discussion: what version control can and cannot establish
12. Conclusion
13. AI-use disclosure, data/code availability, competing interests, and author contributions

## Route recommendation

| Route | Use now? | Required transformation |
|---|---|---|
| JORS “Issues in Research Software” experience report | **Yes, first choice after gap closure** | Emphasize best practices, correction governance, reusable workflow, and limitations in 3,000–4,000 words |
| MethodsX methods/protocol article | **Yes, alternative** | Convert workflow into a reproducible stepwise method with validation examples |
| SoftwareX/JOSS software paper | **Conditional** | Open-source, package, document, release, archive, and establish public development history/significance |
| Finance/data-science empirical article | **Later** | Complete statistics, factor models, benchmarks, independent validation, licensing, and prospective evidence |
