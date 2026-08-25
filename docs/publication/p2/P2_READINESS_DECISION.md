# P2 Readiness Decision

**Evidence cutoff:** main at `614473cf792d992256278dead0da508e4fffd4e2`. Statuses: `READY`, `READY_WITH_LIMITATIONS`, `BLOCKED`, `WAIT_FOR_M55`.

**Correction note:** this document was revised after an independent review identified material corrections to the original P2 package (family miscategorization, an internally-inconsistent "frozen" protocol label, unverified cache-availability claims, an incomplete Alpaca redistribution finding, an unsupported actor attribution, and overclaiming in the novelty statement). Items 3 and 6 below changed status as a direct result; see each item's text for what changed and why.

## 1. Is the 14/17/21 hypothesis ledger reconciled?

**READY.** `docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.json` encodes 21 arm-by-entry-policy evaluations with explicit `control_status`/`diagnostic_sleeve`/`decision_eligible`/`entry_policy` fields; filtering on those fields deterministically reproduces all three totals (14 novel decision-eligible variants, 17 displayed arm labels, 21 arm-by-entry-policy evaluations) with no invented experiments. `scripts/publication/generate_p2_hypothesis_ledger.py` asserts all three counts at generation time and produces `HYPOTHESIS_LEDGER_M43_M55.md` byte-identically on repeated runs (verified twice in this milestone's own verification step). **Corrected in this pass:** M46 (`MID`/`SMALL`/`SMALL_MID`) was originally miscategorized under the ETF family, which silently undercounted the hand-written protocol table to 13 while the JSON-derived total (mis-filed but still summing to 21 overall) read 14 through the wrong 3/5/6 family split. It is now correctly placed in the renamed "Intraday ORB entry/exit/frequency/universe extension" family (7/4/3 = 14), and a dedicated test (`test_every_novel_variant_appears_in_protocol_family_table_exactly_once`) checks this mechanically rather than relying on a hand count.

## 2. Which statistical inputs are available now?

**READY_WITH_LIMITATIONS.** Per the corrected `STATISTICAL_INPUT_INVENTORY.md`: every milestone's scalar/summary figures are `AVAILABLE_COMMITTED`. **Corrected in this pass:** granular trade-level and daily-return series for M43–M53 are now classified `SUMMARY_ONLY`, not `DERIVABLE_OFFLINE` — P2 never opened, parsed, or validated the gitignored local caches those series would come from, so asserting they were "derivable" overstated what P2 actually verified. A cache-dependent regeneration path is still described, but only as an explicitly unverified candidate pending a P3 cache-integrity preflight (see `STATISTICAL_INPUT_INVENTORY.md`'s dedicated preflight section), never as an availability classification. The Fama–French/UMD factor series is `NOT_AVAILABLE` anywhere in the record. M55's prospective series is `PROSPECTIVE_PENDING` by design.

## 3. Which P3 analyses are executable from committed evidence?

**BLOCKED — status changed in this correction pass (was READY_WITH_LIMITATIONS).** Descriptive re-tabulation, the hypothesis-count and inferential-evaluability reconciliation itself, and any analysis needing only the already-committed scalar figures remain executable today. But bootstrap/multiplicity/benchmark analyses in `STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md` §4–§13 and §15 are now correctly understood to require **two** separately unmet preconditions, not one: (a) the cache-integrity preflight from item 2 above, which was never run; and (b) resolution of the 9-item Unresolved Decisions Registry in `STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md` (bootstrap replication count, seed, block lengths, common-vs-family-specific selection, null hypotheses, comparators, test-statistic standardization, treatment of non-evaluable variants, and the White's Reality Check/SPA model-set membership question for M44/M46/M47) by a qualified external finance/econometrics reviewer. Neither precondition is satisfied. Additionally, even once both are satisfied, formal inference would apply only to the 11 of 14 variants classified `INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA` in `HYPOTHESIS_LEDGER_M43_M55.json` — the 2 `DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE` (M44, M46) and 1 `NOT_EVALUABLE_STRUCTURAL_NULL` (M47) variants are never assigned a p-value or resampled interval, regardless of preflight or review outcome.

## 4. Which analyses require external licensed data?

**BLOCKED.** The Fama–French five-factor plus UMD regression (§14) requires a new data acquisition (e.g., Kenneth French's public data library) not previously fetched by any milestone. This is a new-data-acquisition decision requiring separate authorization, not a P2 or automatically-in-scope-P3 action.

## 5. Which analyses must wait for M55?

**WAIT_FOR_M55.** Any prospective-vs-retrospective comparison, any inferential claim about M55's future performance, drawdown, hit rate, or robustness, and any decision to continue/stop/activate/retune the M52 candidate — all explicitly deferred until a minimum of 12 completed monthly M55 cycles exist, per `STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md` §16 and `PUBLICATION_GAP_PLAN.md`. As of this evidence cutoff, M55 has zero completed cycles (`INITIALIZED_WAITING_FOR_FIRST_SIGNAL`, zero transactions, empty base/stress positions); the first eligible signal is 2026-08-31, first possible execution 2026-09-01, first useful checkpoint 2026-09-02 morning.

## 6. Is the project ready to start P3 statistical reanalysis?

**BLOCKED — status changed in this correction pass (was READY_WITH_LIMITATIONS).** `STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md` (renamed from `FROZEN_STATISTICAL_ANALYSIS_PROTOCOL.md`) is now explicitly labeled a draft candidate, not a frozen, execution-ready protocol. P3 statistical reanalysis may begin only after all three of: (a) a qualified external finance/econometrics reviewer approves or corrects the candidate protocol, addressing the 9-item Unresolved Decisions Registry; (b) the final null hypotheses, comparators, test statistics, resampling design, replication count, seed rule, and treatment of non-evaluable variants are frozen in a subsequent, separately time-stamped commit (not this correction commit); and (c) the required granular inputs pass the cache-integrity/availability preflight in `STATISTICAL_INPUT_INVENTORY.md`. None of the three has happened. No reviewer was contacted during this correction pass, consistent with its explicit instruction not to.

## 7. Can methods-manuscript drafting begin in parallel?

**READY_WITH_LIMITATIONS — unchanged by this correction pass.** Per P1's `PUBLICATION_READINESS_DOSSIER.md` (unchanged, not rewritten by P2): the methods/research-software experience-paper route is "preparation-ready." This status does not depend on executing P3: drafting prose around the workflow, the correction record (including this very correction pass, itself a further demonstration of the audit trail the manuscript describes), and the reconciled hypothesis ledger can proceed regardless of item 6's `BLOCKED` status. Drafting must not state any P3 statistical result before P3 actually produces one, and must not describe the candidate protocol in §1–§19 of `STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md` as already executed or frozen.

## 8. What blocks public release?

**BLOCKED**, per `REPRODUCIBILITY_AND_ARCHIVE_BLUEPRINT.md` and `DATA_LICENSE_REDISTRIBUTION_MATRIX.md`:
- No OSI license has been chosen.
- No `CITATION.cff`, `CONTRIBUTING.md`, or `CODE_OF_CONDUCT.md` exists.
- No frozen environment/container, synthetic fixtures, or clean-machine reproduction log exists.
- **Corrected in this pass:** Alpaca raw market-data redistribution is no longer merely "unresolved" — it is **confirmed not permitted** per Alpaca's official redistribution page ("you cannot redistribute Alpaca API data"), absent separate written permission or an applicable governing agreement (the two named underlying agreements were still not read this session). This is now a stronger, confirmed blocker for raw-bar data, not an open question. The derived/aggregate question (whether a committed scalar like a mean-monthly-$ figure computed from Alpaca bars may be redistributed) genuinely remains unresolved and requires human/legal review — it must not be assumed permitted or prohibited.
- SEC EDGAR's exact current automated-access policy text (including a stated 10 req/sec fair-access limit) could not be independently refetched this session (sec.gov returned HTTP 403 to this environment's fetch tooling on every attempt across P1, the original P2 review, and this correction pass) and should be reconfirmed directly by a human before relying on it.
- OpenFIGI is the one source in the matrix with no material open question (MIT license + explicit FAQ redistribution statement, both confirmed live).

## 9. What blocks submission?

**BLOCKED** for a methods/software submission specifically on: external review (no reviewer has been engaged — `EXTERNAL_REVIEW_AND_JOURNAL_PRECHECK_PLAN.md` drafts questions and unsent inquiries only, now including explicit questions about the Unresolved Decisions Registry), the public-release blockers in item 8 (JORS does not strictly require public code the way JOSS does, but P1's dossier already recommends completing public-release prep first), and a full systematic literature/novelty review beyond the targeted map in `LITERATURE_AND_NOVELTY_MAP.md`.

**BLOCKED** for an empirical-finance submission additionally on: the entire statistical-inference gap (no confidence intervals, multiplicity correction, or factor regressions have actually been computed — the candidate protocol is not even reviewer-approved yet, let alone executed), no completed independent/untouched historical validation (M50 remains data-blocked), and M55's `WAIT_FOR_M55` status (item 5).

## 10. Recommended journal route

Unchanged from P1, reconfirmed: **JORS "Issues in Research Software"** as the provisional primary target, **MethodsX** as the provisional fallback, **SoftwareX/JOSS** conditional on completing public-release requirements, and any finance/data-science venue deferred until the empirical gap plan and M55's 12 completed cycles are both done. See `EXTERNAL_REVIEW_AND_JOURNAL_PRECHECK_PLAN.md` §5 for the full sequencing rationale.

## Summary status table

| Question | Status |
|---|---|
| 14/17/21 ledger reconciled | READY |
| Statistical inputs available now | READY_WITH_LIMITATIONS |
| P3 analyses executable from committed evidence | **BLOCKED** (changed from READY_WITH_LIMITATIONS in this correction pass) |
| Analyses requiring external licensed data | BLOCKED |
| Analyses that must wait for M55 | WAIT_FOR_M55 |
| Ready to start P3 statistical reanalysis | **BLOCKED** (changed from READY_WITH_LIMITATIONS in this correction pass) |
| Methods-manuscript drafting in parallel | READY_WITH_LIMITATIONS (unchanged) |
| Public release | BLOCKED (Alpaca raw-data prohibition now confirmed, not merely unresolved) |
| Submission (methods/software route) | BLOCKED (external review + public-release prep pending) |
| Submission (empirical-finance route) | BLOCKED (statistical inference, independent validation, and M55 all pending) |

## P1 correction boundary

No material P1 factual defect was found during P2's original preparation or during this correction pass. Every P1 figure spot-checked while building the P2 package (M49 $184.75/$122.02/14.60%/18.54%/$59.00-vs-$108.82–$108.90; M52 $456.87/34.14%/-25.08%; M53 $328.24/27.25% and $456.72/34.29%; M54's literal 17-item listing under a declared "14" headline) matched the committed source reports exactly. P1's merged files were not modified by P2 or by this correction pass. All corrections in this pass were to P2's own original documents (the family miscategorization, the frozen-protocol mislabeling, the unverified cache claims, the incomplete Alpaca finding, the unsupported actor attribution, and the overclaiming novelty language), not to anything P1 committed.
