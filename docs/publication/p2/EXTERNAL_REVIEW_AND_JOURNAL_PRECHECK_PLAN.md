# External Review and Journal Precheck Plan

**No editor or reviewer was contacted. No presubmission inquiry was sent.** The two inquiry drafts at the end of this document are drafts only.

## 1. Finance/econometrics reviewer

**Qualifications:** a PhD-level or equivalent-experience researcher or practitioner with published or applied work in at least two of: multiple-testing/data-snooping correction in trading-strategy evaluation, factor-model attribution, transaction-cost-aware backtesting, or quantitative portfolio risk management. No named reviewer is proposed anywhere in this document, per the P2 instructions.

**Review rubric:**
1. Does `STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md`'s hypothesis-family and primary-endpoint assignment (§1) match how a domain expert would group these strategies, including the corrected placement of M46 in the intraday-ORB family rather than the ETF family?
2. Is the joint time-block resampling design (§4–§7) statistically sound given the documented window overlaps in `HYPOTHESIS_LEDGER_M43_M55.md`?
3. Is Romano–Wolf stepdown (§8) an appropriate primary multiplicity procedure here, or would the reviewer recommend a different family-wise or FDR method?
4. Does the White's Reality Check / Hansen's SPA benchmark-set definition (§10) fairly represent "everything actually tried," including non-decision-eligible controls and diagnostic sleeves?
5. Is the factor-regression design (§14), including the alpha-language prohibition, sufficient to prevent an overclaimed intercept?
6. Given `STATISTICAL_INPUT_INVENTORY.md`'s current availability findings, which planned P3 analyses are the reviewer confident are actually executable, and which would the reviewer expect to fail for a reason this inventory did not anticipate?
7. For each of the 8 items in `STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md`'s Unresolved Decisions Registry (bootstrap replication count, random seed, exact block lengths, common-vs-family-specific block selection, null hypotheses, comparators, test-statistic standardization, treatment of non-evaluable variants), what value or rule does the reviewer recommend, and does the candidate default proposed in §1a for non-evaluable variants (restrict Romano–Wolf/BH to the 11 `INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA` variants; report the 2 `DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE` and 1 `NOT_EVALUABLE_STRUCTURAL_NULL` variants descriptively only) hold up?
8. Is restricting formal inference to variants with a real, non-trivial committed sample (per the `inferential_evaluability` field in `HYPOTHESIS_LEDGER_M43_M55.json`) the right line to draw, or should the reviewer recommend a different evaluability criterion?

## 2. Research-software/reproducibility reviewer

**Qualifications:** experience with at least two of: JOSS/JORS/SoftwareX-style review, research-software engineering practice audits, FAIR-data/artifact-evaluation committee service, or maintaining a public open-source research codebase through multiple releases.

**Review rubric:**
1. Does `REPRODUCIBILITY_AND_ARCHIVE_BLUEPRINT.md` correctly identify every blocker to a clean-machine reproduction, or are there gaps (e.g., undocumented OS-specific behavior) this P2 review missed?
2. Is `scripts/publication/generate_p2_hypothesis_ledger.py`'s determinism claim (verified by regenerate-twice-and-diff in P2's own verification step) sufficient evidence of reproducibility, or does the reviewer want a CI-enforced check?
3. Does the generated-vs-hand-authored file distinction in `docs/publication/p2/README.md` match actual practice elsewhere in research-software publication?
4. Is the raw/derived-shareable/non-shareable data separation in `REPRODUCIBILITY_AND_ARCHIVE_BLUEPRINT.md` item 8 adequate for a public release decision, or does the reviewer see additional categories needed?
5. Are the JOSS six-month public-history and public-issue/PR requirements (confirmed live against https://joss.theoj.org/about during P1) correctly understood as currently unmet, and is the recommended venue order in `JOURNAL_TARGET_MATRIX.md` still the reviewer's own recommendation?

## 3. Actor-provenance / AI-governance review questions

For either or a third reviewer with AI-governance/research-ethics background:
1. Does `ACTOR_PROVENANCE_AND_CORRECTION_TAXONOMY.md`'s heavy use of `UNKNOWN` for detection/correction actors correctly reflect current publication-ethics expectations (e.g., COPE's position that a human must be accountable, not that every internal step must be actor-labeled), or does the reviewer expect finer-grained attribution before submission?
2. Is `AI_USE_DISCLOSURE_DRAFT.md`'s framing — naming Claude Code/Claude Sonnet 5 and ChatGPT/Codex, with exact versions to be confirmed from retained records — sufficient for the venues in `JOURNAL_TARGET_MATRIX.md`, or does the reviewer recommend additional detail (e.g., prompt-level logging) before submission?
3. Does the novelty statement in `LITERATURE_AND_NOVELTY_MAP.md` avoid overclaiming LLM-assistance benefits, consistent with the explicit prohibition already in `CLAIM_EVIDENCE_MATRIX.md`?

## 4. Comment-resolution log structure

Each reviewer's numbered comments are logged in a `docs/publication/p2/REVIEW_LOG_<reviewer-initials>_<date>.md` file (not created yet — no reviewer has been engaged), with columns: comment number, reviewer's exact text, affected P2/P1 file and line/section, proposed resolution, resolution status (`OPEN`/`RESOLVED`/`DEFERRED_TO_P3`), and resolving commit SHA once addressed. This mirrors the defect/correction discipline already used throughout M43–M55.

## 5. Venue sequencing

Unchanged from P1's `JOURNAL_TARGET_MATRIX.md` and `MANUSCRIPT_CONCEPTS.md`, reconfirmed here rather than re-derived:

1. **JORS "Issues in Research Software"** — provisional primary target. Confirmed live during P1: 3,000–4,000-word experience-report format, £891 APC (with waiver option), requires explicit AI-role venue confirmation given the unusually extensive generative-AI involvement documented in `AI_USE_DISCLOSURE_DRAFT.md`.
2. **MethodsX** — provisional fallback if the contribution is reframed as a reusable stepwise method with validation examples rather than a project narrative; USD 1,660 APC per P1 (ScienceDirect's page returned HTTP 403 to automated fetch both in P1 and not retried in P2 — reconfirm directly before submission).
3. **SoftwareX** — conditional on completing the public-release requirements in `REPRODUCIBILITY_AND_ARCHIVE_BLUEPRINT.md` (all software must be open source under an appropriate license).
4. **JOSS** — conditional on the same public-release requirements plus the six-month public-development-history and public-issue/PR requirements, both confirmed live against https://joss.theoj.org/about during P1's independent review.
5. Any finance/data-science venue (Journal of Finance and Data Science, Journal of Financial Data Science, Quantitative Finance) — deferred until the empirical gap plan (`PUBLICATION_GAP_PLAN.md`) and M55's minimum 12 completed prospective cycles are both complete, per P1's `PUBLICATION_READINESS_DOSSIER.md` verdict, unchanged here.

## 6. Current venue-policy recheck requirement

Every fee, word-limit, AI-policy, and eligibility figure cited above and in P1's `JOURNAL_TARGET_MATRIX.md` must be rechecked against the live official page immediately before submission — policies and fees change, and P1's own matrix states this explicitly. P2 did not refetch any of these pages; it reconfirms what P1 already fetched and verified live on 2026-08-24.

## 7. Draft presubmission-scope inquiry text (DRAFTS ONLY — NOT SENT)

### Draft — JORS "Issues in Research Software"

> Subject: Presubmission scope inquiry — auditable frozen-protocol workflow for sequential algorithmic-trading research
>
> We are preparing an experience-report submission describing a version-controlled, human-led, LLM-assisted research workflow for sequential algorithmic-trading research: time-stamped frozen protocols, deterministic classification rules, an append-only hash-chained prospective-monitoring checkpoint design, and a machine-readable hypothesis ledger reconciling multiple valid "how many hypotheses were tested" counts for the same 13-milestone research program. The manuscript will explicitly disclaim any production-ready trading strategy or reliable-income claim; its contribution is the audit/correction/governance methodology. Given the unusually extensive generative-AI role in both the underlying research program and this manuscript's preparation (to be disclosed in full per your AI-use policy), we would appreciate confirmation that this scope and disclosure approach fits Issues in Research Software before a full submission.

### Draft — MethodsX

> Subject: Presubmission scope inquiry — reusable frozen-protocol and hypothesis-ledger method for sequential trading research
>
> We are considering a MethodsX submission describing, as a reusable stepwise method: (1) a protocol-freezing and deterministic-classification discipline for sequential, multi-milestone algorithmic-trading research, and (2) a machine-readable hypothesis-ledger schema that reconciles multiple valid hypothesis-count definitions for the same research program against one canonical source. The method would be illustrated, not validated as a trading strategy, using a 13-milestone case study; no production-ready or profitable strategy is claimed. We would welcome confirmation that a methods/protocol framing (rather than a project narrative) fits MethodsX's scope before a full submission.

Both drafts are unsent. Sending either requires separate, explicit operator authorization outside P2's scope.
