# P3A Readiness Decision

**Evidence cutoff:** main at `e20ef011282fa08c3703b5af1811143e74b2c780`. Statuses: `READY`, `READY_WITH_LIMITATIONS`, `BLOCKED`.

## Summary

| Gate | Status |
|---|---|
| a. External methodological review | **READY** |
| b. P3B Phase 1 — read-only cache inventory | **READY** |
| b2. P3B Phase 2 — deterministic regeneration verification | **READY_WITH_LIMITATIONS** — requires separate operator authorization |
| c. Protocol freezing | **BLOCKED** |
| d. Statistical execution | **BLOCKED** |

This is the expected split: the package is ready to be *reviewed* and the data can be *checked*, but nothing can be frozen or run until the review returns.

## a. External methodological review — READY

`EXTERNAL_ECONOMETRICS_REVIEW_PACKET.md` is self-contained: it states the repository context, the four highest-stakes questions, the tables a reviewer needs, and the constraints that bound any advice. All 13 decisions in `STATISTICAL_DECISION_REGISTER.md` carry alternatives, consequences, and an exact reviewer question. Hypothesis levels are separated so a reviewer is not asked to accept 11 test instances as 11 independent hypotheses.

**Limitation:** no reviewer has been identified or contacted, and no named reviewer is proposed anywhere in this package. Readiness here means the packet is complete enough to send, not that sending has been authorized.

## b. P3B cache and data preflight — split into two phases with different authorization requirements

An earlier draft of this document treated the preflight as one undifferentiated step that "may start immediately." That conflated two materially different activities, and the boundary is now drawn explicitly.

### P3B Phase 1 — read-only cache inventory — READY

Confirms that the required directories and files exist, and inspects committed manifests and recorded hashes. **Alters nothing. Runs no strategy code and no simulation.** It is a filesystem-and-manifest inspection: does `artifacts/research-*/results/` exist for each milestone, what does it contain, and what do the committed hash manifests record about it.

This is genuinely read-only, carries no execution risk, and can proceed in parallel with external methodological review, because it asks a factual question rather than a methodological one.

### P3B Phase 2 — deterministic regeneration verification — READY_WITH_LIMITATIONS, SEPARATE AUTHORIZATION REQUIRED

Reproduces historical strategy outputs from the existing caches in order to confirm that regeneration reproduces the pinned `study_results_json_sha256` recorded in each milestone hash manifest.

**This is a historical simulation rerun, even though every parameter remains frozen and no market data is fetched.** Executing committed strategy code over cached bars is strategy execution regardless of whether the output is expected to be identical to a committed hash. It therefore falls outside routine offline documentation work and **requires separate explicit operator authorization before it may begin.**

Phase 2 constraints, if authorized: fetch no market data; perform no parameter search; alter no cache; change no strategy, configuration, or M55 file; and commit the pass/fail result as its own artifact.

**Phase 1 may begin without further authorization. Phase 2 may not.** P3A does not authorize Phase 2 and did not perform any part of either phase.

**Limitation:** if either phase fails for a given milestone, the affected test instances drop out and the executable model set falls below 11, which changes the shape of the D8 and D9 discussion. Neither phase is a formality.

## c. Protocol freezing — BLOCKED

**All 13 decisions block freezing.** A protocol cannot be frozen while its replication count, seed, block length, resampling scheme, nulls, comparators, standardization, non-evaluable treatment, model-comparison universe, inference level, family structure, and search denominator are all open. P3A deliberately did not resolve any of them; resolving them by convenience to make the protocol look finished is precisely the defect the second P2 review corrected.

Freezing requires, in order: external review returns; decisions are resolved and recorded; a **separate, later, time-stamped commit** freezes the resulting protocol. That commit is not this one and not P3A's.

## d. Statistical execution — BLOCKED

Six decisions (D1, D2, D3, D4, D5, D8) block **all** execution; no subset of the analysis can proceed. Separately, every test instance depends on data classified `SUMMARY_ONLY` pending preflight, so even a fully resolved protocol could not run today. Factor-attribution components are additionally blocked by the `NOT_AVAILABLE` factor series.

## Standing constraints unchanged by P3A

- `strategy.enabled` remains `false`.
- M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL` with zero transactions and empty base and stress positions; its ledger blob is unchanged at `ce8f022d1fc9f734042de4c08feef35b9de00465`.
- M55 evidence must never be pooled into retrospective multiplicity accounting, and requires a minimum of 12 completed monthly cycles before any inferential use.
- No design has been confirmed on data it never influenced: M50 was data-blocked, M55 has zero cycles.
- Claim discipline from P1 and P2 carries forward unchanged, including the authoritative ~$59.00/month ORB standalone figure over the superseded ~$108.82-$108.90 reference arithmetic, and the research-only status of the ~$457/month momentum results given ~34% drawdown and ~-25% worst month.

## Recommended next steps, in order

1. Run **P3B Phase 1** (read-only cache inventory). Independent of review; may start without further authorization; alters nothing.
2. Obtain separate explicit operator authorization for **P3B Phase 2** (deterministic regeneration verification), which is a historical simulation rerun, then run it under the Phase 2 constraints.
3. Send the review packet once an operator authorizes contact and a reviewer is identified. This may proceed in parallel with steps 1 and 2.
4. Record the reviewer's resolutions against all 13 decisions.
5. Freeze the resulting protocol in a separate time-stamped commit.
6. Only then execute P3 statistical analysis, scoped to whatever test instances survived both preflight phases.

Steps 4, 5, and 6 must not be reordered or merged.
