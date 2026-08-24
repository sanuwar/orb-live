# Publication P2 — Evidence Hardening, Statistical Protocol, and Reproducibility Blueprint

**Evidence cutoff:** main at `614473cf792d992256278dead0da508e4fffd4e2` (immediately after Publication P1, PR #365, merged).
**Issue:** #366. **Scope:** `docs/publication/p2/`, plus the narrowly scoped generator script `scripts/publication/generate_p2_hypothesis_ledger.py` and its test `tests/test_publication_p2_artifacts.py`.

## Purpose

P2 converts P1's identified publication gaps into a precise, auditable, machine-checkable evidence and reproducibility plan **before** any new statistical analysis or full manuscript draft is written. It:

1. Freezes the hypothesis-counting and statistical-analysis rules a later P3 analysis will follow.
2. Determines exactly which required statistical inputs already exist in committed artifacts.
3. Identifies inputs that are derivable offline, summary-only, missing, license-restricted, or prospective-pending.
4. Formalizes actor provenance and defect/correction classification.
5. Defines the reproducibility, licensing, archival, external-review, and journal-precheck path.
6. Determines whether the project is ready to begin P3.

**P2 contains no new trading result.** It does not fetch market data, access a broker or market-data account, run a strategy simulation, recalculate strategy parameters, run a parameter experiment, run the M55 checkpoint, or alter any M55 ledger. `strategy.enabled` remains `false` throughout.

## Deliverable manifest

| File | Kind | Purpose |
|---|---|---|
| `README.md` | hand-authored | This file. |
| `HYPOTHESIS_LEDGER_M43_M55.json` | hand-authored, canonical | Single source of truth for every displayed empirical arm across M43-M55. |
| `HYPOTHESIS_LEDGER_M43_M55.md` | **generated** | Deterministic rendering of the JSON ledger. Do not hand-edit. |
| `STATISTICAL_INPUT_INVENTORY.md` | hand-authored | Per-planned-analysis input availability classification. |
| `FROZEN_STATISTICAL_ANALYSIS_PROTOCOL.md` | hand-authored | The frozen P3 analysis protocol (not executed in P2). |
| `LITERATURE_AND_NOVELTY_MAP.md` | hand-authored | Primary-source literature map and narrow novelty statement. |
| `DATA_LICENSE_REDISTRIBUTION_MATRIX.md` | hand-authored | Per-source redistribution/licensing review. |
| `ACTOR_PROVENANCE_AND_CORRECTION_TAXONOMY.md` | hand-authored | Evidence-grounded actor/activity/defect taxonomy. |
| `REPRODUCIBILITY_AND_ARCHIVE_BLUEPRINT.md` | hand-authored | Public-release, archival, and reproduction blueprint (not executed). |
| `EXTERNAL_REVIEW_AND_JOURNAL_PRECHECK_PLAN.md` | hand-authored | Reviewer rubric and draft (unsent) presubmission inquiries. |
| `P2_READINESS_DECISION.md` | hand-authored | Final evidence-backed P3/publication readiness decision. |

## Canonical-source hierarchy

1. **Committed M43-M55 research reports and hash manifests** under `docs/research/` are authoritative for every empirical figure. P2 never recomputes them.
2. **`HYPOTHESIS_LEDGER_M43_M55.json`** is authoritative for arm-level classification (control/diagnostic/decision-eligible status, evidence class, endpoint assignment) within P2. `HYPOTHESIS_LEDGER_M43_M55.md` is a derived view of it, not an independent source.
3. **P1's `docs/publication/*.md`** is the authoritative publication-readiness audit baseline. P2 reads from it and may reference it, but does not rewrite it (see the P1 correction boundary in `P2_READINESS_DECISION.md`).
4. **Git history** (commit SHAs, blob hashes) is authoritative for provenance and is cited, not paraphrased, wherever a figure is quoted.
5. **Public official policy pages and primary scholarly sources**, cited with URL/DOI and access date, are authoritative for external claims (journal policy, data-source terms, statistical methodology). Secondary summaries are used only when a primary source could not be reached, and this is stated explicitly at the point of use.

## Generated-versus-hand-authored file distinction

Exactly one file in this package is generated: `HYPOTHESIS_LEDGER_M43_M55.md`, produced by `scripts/publication/generate_p2_hypothesis_ledger.py` from `HYPOTHESIS_LEDGER_M43_M55.json`. It is idempotent and network-free. Every other file in `docs/publication/p2/` is hand-authored prose reviewed against committed evidence; none of them are templated or auto-generated.

## Commands for deterministic validation

```
# Regenerate the Markdown ledger from the JSON ledger (must be idempotent)
python -m scripts.publication.generate_p2_hypothesis_ledger

# Prove byte-identical regeneration
python -m scripts.publication.generate_p2_hypothesis_ledger
git diff --stat docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.md   # expect no diff

# Run the P2 artifact tests
python -m pytest tests/test_publication_p2_artifacts.py -v
```

## No new trading result

Every figure in this package is copied, not recomputed, from a committed M43-M55 artifact, cited by exact repository path, commit SHA, and (where available) Git blob hash. P2 adds no new backtest, no new simulation, no new parameter, and no new M55 observation. M55's status in this package is exactly what it was at the P2 evidence cutoff: `INITIALIZED_WAITING_FOR_FIRST_SIGNAL`, zero transactions, empty positions in both base and stress ledgers.
