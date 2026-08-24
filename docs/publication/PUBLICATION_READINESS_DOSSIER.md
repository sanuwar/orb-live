# Publication P1 — Publication-Readiness Dossier

**Issue:** [#364](https://github.com/sanuwar/orb-live/issues/364)
**Evidence cutoff:** main at b789517d856b5cc546c21d7f5562e301429b252b
**Audit date:** 2026-08-24
**Scope:** committed M43–M55 evidence only; no market-data fetch, strategy run, parameter experiment, or M55 advancement.

## Recommendation

The most defensible first paper is a **research-methods / research-software experience paper**, not an empirical-finance performance paper.

### Central contribution

> An auditable, human-led, LLM-assisted, version-controlled methodology for sequential algorithmic-trading research that preserves time-stamped frozen protocols, positive findings, null results, data limitations, defect corrections, and safety boundaries.

The contribution is the workflow and its case-study audit trail. The contribution is **not** a production-ready strategy, a reliable income claim, an assertion that LLM assistance improves scientific validity, or a discovery of a $1,000/month trading system.

## Paper-type assessment

| Route | Readiness | Defensible contribution | Binding gaps |
|---|---|---|---|
| Methods / reproducibility experience paper | **Recommended; preparation-ready** | Protocol freezing, evidence lineage, correction preservation, null-result retention, safety gates, and explicit human accountability in an LLM-assisted workflow | Formalize the workflow, resolve the hypothesis-count ambiguity, provide a public reproducibility package, clarify data licensing, and obtain external methods review |
| Research-software paper | **Conditionally realistic** | ORB-LIVE as tested research infrastructure supporting deterministic protocols, ledgers, provenance, and fail-closed checks | Repository is private; venues require open source, installability, documentation, releases, archival DOI, and in JOSS's case at least six months of public development history |
| Empirical-finance paper | **Not submission-ready** | A transparent case study containing positive, mixed, negative, null, and data-blocked experiments | No formal multiple-testing adjustment, no confidence intervals, no factor regressions, no completed independent validation, no completed prospective performance evidence, and market-data redistribution constraints |
| Strategy-performance / investment paper | **Not defensible** | None at present | No deployable or reliably profitable strategy is supported; M49 is below target and M52/M53 have excessive drawdown and monthly tail risk |

## Claims currently supportable

1. M43–M55 form a traceable sequence of committed research reports with frozen protocol sections, explicit classifications, correction histories, and safety statements.
2. The record preserves unfavorable evidence: M44 and M47 insufficient-evidence/null results, M46 not-promising evidence, M50's data-blocked validation, and M53's failed risk-control promotion.
3. The record documents consequential defects and corrected reruns rather than silently replacing every erroneous intermediate figure.
4. M49 supports only a historical, research-only **$150–$250/month practical range** for its exact shared-$30,000 architecture; its observed base mean was $184.75/month and stress mean $122.02/month.
5. M49's chronological, cost-modeled ORB standalone result of about **$59/month** is authoritative over the earlier approximately $108.82–$108.90/month R-times-$300 reference arithmetic.
6. M52's $456.87/month and M53 MARKET_VOL_15's $456.72/month are research-only point estimates. Their approximately 34% maximum drawdowns and approximately -25% worst months prevent deployability or reliable-income claims.
7. M55 is a genuinely prospective protocol boundary and initialized audit ledger, but it contains **no prospective signal, transaction, or performance result yet**.

## Claims not currently supportable

- ORB-LIVE has discovered a production-ready, deployable, or reliably profitable strategy.
- A $500/month or $1,000/month outcome is supported from $30,000 under the tested safety limits.
- Git commits constitute formal preregistration.
- Any reported mean is statistically significant or survives multiple-testing correction.
- The LLM caused better research quality, detected more defects than a human workflow, or reduced bias.
- The full study is externally reproducible from public data and code today.
- Code volume, commit volume, or test count establishes scientific validity.
- M55 has validated M52 prospectively.

## Principal strengths

- Explicit pre-results protocol text and deterministic classification rules in later milestones.
- Exact artifact hashes, clean-generation SHAs, parity checks, append-only M55 checkpoint design, and preserved superseded figures.
- Chronological capital accounting in M48–M53 and an explicit separation between R-multiple reference arithmetic and simulated dollar economics.
- Strong safety discipline: strategy disabled, no broker writes, no account access, and no activation implied by research classifications.
- Scientifically valuable negative evidence and visible data limitations.
- A rich correction record suitable for an experience report on AI-assisted research governance.

## Principal weaknesses and publication risks

- Heavy reuse and overlap of a small number of historical windows creates selection-bias and multiple-testing exposure.
- M54 declares 14 tested hypotheses, but its literal arm list totals 17 unless controls and diagnostic sub-sleeves are excluded; the paper must define the counting unit before using “14.”
- No confidence intervals, block bootstrap, hypothesis tests, or family-wise/FDR correction are attached to the trading results.
- No factor model establishes whether returns exceed standard market, size, value, and momentum exposures.
- M50 did not produce an untouched-window economic result; M55 has not produced prospective performance.
- Vendor data are cached but not publication-ready; redistribution rights and a lawful external reproduction path remain unresolved.
- The reports record “independent review” but do not consistently identify whether each review/detection was human or another AI pass. Actor-level provenance needs tightening.
- The breadth of LLM involvement requires unusually precise disclosure of tools, versions, prompts/records retained, human verification, and responsibility.
- Novelty has not been established against the research-software, reproducible-finance, and AI-assisted-science literature.

## Submission prerequisites

Before a methods/software submission:

1. Define the method as a reusable protocol with inputs, gates, state transitions, correction rules, and accountability boundaries.
2. Resolve and document the 14-versus-17 hypothesis-count ambiguity.
3. Prepare a public, installable, licensed software release or clearly scoped public reproducibility companion.
4. Produce a frozen environment, one-command non-proprietary demonstration, synthetic fixtures, artifact manifest, and archival DOI.
5. Complete data-license review and state what can and cannot be redistributed.
6. Add a structured defect/correction taxonomy and actor provenance.
7. Obtain external review from one finance/econometrics expert and one research-software/reproducibility expert.
8. Use a venue-specific AI disclosure and verify exact Claude model/product identifiers from retained records.

Before an empirical-finance submission, additionally complete the full statistical, factor, benchmark, and independent-validation work in PUBLICATION_GAP_PLAN.md. M55 results must remain prospective and untouched by any retuning.

## Readiness verdict

**READY_TO_DEVELOP_METHODS_PAPER; NOT_READY_TO_SUBMIT.** The current evidence supports a careful methodology/research-software case study. It does not support an empirical performance paper or investment claim.
