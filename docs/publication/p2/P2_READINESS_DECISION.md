# P2 Readiness Decision

**Evidence cutoff:** main at `614473cf792d992256278dead0da508e4fffd4e2`. Statuses: `READY`, `READY_WITH_LIMITATIONS`, `BLOCKED`, `WAIT_FOR_M55`.

## 1. Is the 14/17/21 hypothesis ledger reconciled?

**READY.** `docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.json` encodes 21 arm-by-entry-policy evaluations with explicit `control_status`/`diagnostic_sleeve`/`decision_eligible`/`entry_policy` fields; filtering on those fields deterministically reproduces all three totals (14 novel decision-eligible variants, 17 displayed arm labels, 21 arm-by-entry-policy evaluations) with no invented experiments. `scripts/publication/generate_p2_hypothesis_ledger.py` asserts all three counts at generation time and produces `HYPOTHESIS_LEDGER_M43_M55.md` byte-identically on repeated runs (verified twice in this milestone's own verification step).

## 2. Which statistical inputs are available now?

**READY_WITH_LIMITATIONS.** Per `STATISTICAL_INPUT_INVENTORY.md`: every milestone's scalar/summary figures are `AVAILABLE_COMMITTED`. Granular trade-level and daily-return series for M43–M53 are `DERIVABLE_OFFLINE` (contingent on reusing existing gitignored local caches without a new fetch) or `SUMMARY_ONLY` where no stable local cache is confirmed. The Fama–French/UMD factor series is `NOT_AVAILABLE` anywhere in the record. M55's prospective series is `PROSPECTIVE_PENDING` by design.

## 3. Which P3 analyses are executable from committed evidence?

**READY_WITH_LIMITATIONS.** Descriptive re-tabulation, the hypothesis-count reconciliation itself, and any analysis needing only the already-committed scalar figures are executable today. Bootstrap/multiplicity/benchmark analyses in `FROZEN_STATISTICAL_ANALYSIS_PROTOCOL.md` §4–§13 and §15 are executable **only if** the required gitignored local caches still exist unmodified; this has not been verified in P2 (verifying it would mean opening those caches, which is a P3-scope action, not a P2 one).

## 4. Which analyses require external licensed data?

**BLOCKED.** The Fama–French five-factor plus UMD regression (§14) requires a new data acquisition (e.g., Kenneth French's public data library) not previously fetched by any milestone. This is a new-data-acquisition decision requiring separate authorization, not a P2 or automatically-in-scope-P3 action.

## 5. Which analyses must wait for M55?

**WAIT_FOR_M55.** Any prospective-vs-retrospective comparison, any inferential claim about M55's future performance, drawdown, hit rate, or robustness, and any decision to continue/stop/activate/retune the M52 candidate — all explicitly deferred until a minimum of 12 completed monthly M55 cycles exist, per `FROZEN_STATISTICAL_ANALYSIS_PROTOCOL.md` §16 and `PUBLICATION_GAP_PLAN.md`. As of this evidence cutoff, M55 has zero completed cycles (`INITIALIZED_WAITING_FOR_FIRST_SIGNAL`, zero transactions, empty base/stress positions); the first eligible signal is 2026-08-31, first possible execution 2026-09-01, first useful checkpoint 2026-09-02 morning.

## 6. Is the project ready to start P3 statistical reanalysis?

**READY_WITH_LIMITATIONS.** The protocol is frozen (`FROZEN_STATISTICAL_ANALYSIS_PROTOCOL.md`) and the input inventory is honest about what exists. P3 may begin the portions of its analysis that depend only on `AVAILABLE_COMMITTED` and confirmed-`DERIVABLE_OFFLINE` inputs. P3 must not claim it has executed the factor-regression component (§14) until the `NOT_AVAILABLE` factor-series gap (§4 above) is separately resolved, and must not touch M55 evidence before 12 completed cycles (§5 above).

## 7. Can methods-manuscript drafting begin in parallel?

**READY_WITH_LIMITATIONS.** Per P1's `PUBLICATION_READINESS_DOSSIER.md` (unchanged, not rewritten by P2): the methods/research-software experience-paper route is "preparation-ready." Drafting prose around the workflow, the correction record, and the now-reconciled hypothesis ledger can proceed in parallel with P3's statistical work, since the manuscript's central contribution (per `MANUSCRIPT_CONCEPTS.md` Concept A) does not depend on the statistical reanalysis outcome. Drafting must not state any P3 statistical result before P3 actually produces one.

## 8. What blocks public release?

**BLOCKED**, per `REPRODUCIBILITY_AND_ARCHIVE_BLUEPRINT.md` and `DATA_LICENSE_REDISTRIBUTION_MATRIX.md`:
- No OSI license has been chosen.
- No `CITATION.cff`, `CONTRIBUTING.md`, or `CODE_OF_CONDUCT.md` exists.
- No frozen environment/container, synthetic fixtures, or clean-machine reproduction log exists.
- **Alpaca market-data redistribution rights are unresolved** — the two governing agreements (NASDAQ OMX Global Subscriber Agreement, NYSE Market Data Display Services Agreement) were not read this session and must be reviewed by a human before any raw-bar data is included in a public release.
- SEC EDGAR's exact current automated-access policy text could not be independently refetched this session (sec.gov returned HTTP 403 to this environment's fetch tooling) and should be reconfirmed directly.

## 9. What blocks submission?

**BLOCKED** for a methods/software submission specifically on: external review (no reviewer has been engaged — `EXTERNAL_REVIEW_AND_JOURNAL_PRECHECK_PLAN.md` drafts questions and unsent inquiries only), the public-release blockers in item 8 (JORS does not strictly require public code the way JOSS does, but P1's dossier already recommends completing public-release prep first), and a full systematic literature/novelty review beyond the targeted map in `LITERATURE_AND_NOVELTY_MAP.md`.

**BLOCKED** for an empirical-finance submission additionally on: the entire statistical-inference gap (no confidence intervals, multiplicity correction, or factor regressions have actually been computed — only frozen for future computation), no completed independent/untouched historical validation (M50 remains data-blocked), and M55's `WAIT_FOR_M55` status (item 5).

## 10. Recommended journal route

Unchanged from P1, reconfirmed: **JORS "Issues in Research Software"** as the provisional primary target, **MethodsX** as the provisional fallback, **SoftwareX/JOSS** conditional on completing public-release requirements, and any finance/data-science venue deferred until the empirical gap plan and M55's 12 completed cycles are both done. See `EXTERNAL_REVIEW_AND_JOURNAL_PRECHECK_PLAN.md` §5 for the full sequencing rationale.

## Summary status table

| Question | Status |
|---|---|
| 14/17/21 ledger reconciled | READY |
| Statistical inputs available now | READY_WITH_LIMITATIONS |
| P3 analyses executable from committed evidence | READY_WITH_LIMITATIONS |
| Analyses requiring external licensed data | BLOCKED |
| Analyses that must wait for M55 | WAIT_FOR_M55 |
| Ready to start P3 statistical reanalysis | READY_WITH_LIMITATIONS |
| Methods-manuscript drafting in parallel | READY_WITH_LIMITATIONS |
| Public release | BLOCKED |
| Submission (methods/software route) | BLOCKED (external review + public-release prep pending) |
| Submission (empirical-finance route) | BLOCKED (statistical inference, independent validation, and M55 all pending) |

## P1 correction boundary

No material P1 factual defect was found during P2's preparation. Every P1 figure spot-checked while building this package (M49 $184.75/$122.02/14.60%/18.54%/$59.00-vs-$108.82–$108.90; M52 $456.87/34.14%/-25.08%; M53 $328.24/27.25% and $456.72/34.29%; M54's literal 17-item listing under a declared "14" headline) matched the committed source reports exactly. P1's merged files were not modified by P2.
